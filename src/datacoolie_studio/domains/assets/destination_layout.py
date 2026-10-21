"""Bounded Assets-owned destination layout enrichment.

Monitoring aggregates captured facts first and uses one prepared context for
bounded latest-target and selected-page batches. The helper keeps
presentation values separate from endpoint identity: it never changes a
record's target, signature, or identity status and it never exposes endpoint
configure data.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from typing import Any

from sqlalchemy.orm import Session
from datacoolie_studio.domains.assets.format_policy import canonical_format

from datacoolie_studio.domains.assets.identity_catalog import (
    LAYOUT_PRESENTATION_FIELDS,
    IdentityCatalog,
    identity_revision,
    load_or_build_identity_catalog,
)


# The Monitoring API currently caps a page at 500 records.  Keeping the same
# bound here prevents an accidental caller from turning this into an
# unbounded metadata hydration path.
MAX_DESTINATION_LAYOUT_RECORDS = 500


class DestinationLayoutContext:
    """One revision-bound catalog for deterministic, bounded layout batches."""

    def __init__(self, target_index, metadata_revision: str):
        self.target_index = target_index
        self.metadata_revision = metadata_revision

    @classmethod
    def prepare(cls, session: Session, environment_id: int, *, metadata_revision: str,
                include_metadata: bool = True) -> DestinationLayoutContext:
        catalog = _load_catalog_for_revision(session, environment_id, metadata_revision) if include_metadata else None
        return cls(_target_index(catalog), metadata_revision)

    def enrich(self, records: Sequence[MutableMapping[str, Any]]) -> list[MutableMapping[str, Any]]:
        output = records if isinstance(records, list) else list(records)
        if len(output) > MAX_DESTINATION_LAYOUT_RECORDS:
            raise ValueError(f"destination layout enrichment is limited to {MAX_DESTINATION_LAYOUT_RECORDS} rows")
        for record in output:
            if isinstance(record, MutableMapping):
                record.pop("_layout_signature", None)
                _enrich_record(record, self.target_index, self.metadata_revision)
        return output


def enrich_destination_layouts(
    session: Session,
    environment_id: int,
    records: Sequence[MutableMapping[str, Any]],
    *,
    metadata_revision: str,
) -> list[MutableMapping[str, Any]]:
    """Attach a safe ``layout`` object to bounded destination aggregate rows.

    ``records`` is mutated in place when it is a list and is returned for
    convenient composition.  Captured values take precedence.  Missing
    captured values use only an exact canonical target index plus matching
    selected context from the row; unresolved or ambiguous identity rows do
    not receive a name-only metadata fallback.

    A catalog whose revision does not equal the request revision is discarded
    for this enrichment.  The caller's existing identity request fence then
    rejects the overall response if the revision changed while the page was
    being built.
    """

    output = records if isinstance(records, list) else list(records)
    if len(output) > MAX_DESTINATION_LAYOUT_RECORDS:
        raise ValueError(
            f"destination layout enrichment is limited to {MAX_DESTINATION_LAYOUT_RECORDS} rows"
        )
    # Fully captured resolved rows also compare against published metadata.
    # Catalog availability must not depend on other missing fields in a batch.
    context = DestinationLayoutContext.prepare(
        session, environment_id, metadata_revision=metadata_revision,
        include_metadata=any(record.get("identity_status") == "resolved" for record in output),
    )
    return context.enrich(output)


def _load_catalog_for_revision(
    session: Session,
    environment_id: int,
    metadata_revision: str | None,
) -> IdentityCatalog | None:
    requested = str(metadata_revision or "")
    catalog = load_or_build_identity_catalog(
        session,
        int(environment_id),
        revision=requested or None,
    )
    # A caller may have captured an older request revision while a metadata
    # write committed.  Do not use the newer catalog to fill this response.
    if requested and str(catalog.version) != requested:
        return None
    if requested and identity_revision(session, int(environment_id)) != requested:
        return None
    return catalog


def _target_index(catalog: IdentityCatalog | None) -> dict[str, tuple[dict[str, Any], ...]]:
    if catalog is None:
        return {}
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in catalog.rows:
        if not isinstance(row, Mapping) or row.get("role") != "destination":
            continue
        target = _text(row.get("asset_id"))
        if target:
            index[target].append(dict(row))
    return {key: tuple(value) for key, value in index.items()}


def _enrich_record(
    record: MutableMapping[str, Any],
    target_index: Mapping[str, Sequence[Mapping[str, Any]]],
    metadata_revision: str | None,
) -> None:
    captured = _captured_layout(record)
    metadata_candidates: list[dict[str, Any]] = []
    if record.get("identity_status") == "resolved":
        target = _text(record.get("target"))
        metadata_candidates = [
            dict(row)
            for row in target_index.get(target, ())
            if _context_matches(record, row)
        ]
    layout = _compose_layout(record, captured, metadata_candidates, metadata_revision)
    record["layout"] = layout


def _layout_input_is_missing(value: Any, *, scalar: bool) -> bool:
    if value is None:
        return True
    if scalar:
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            return False
        text = str(value).strip()
        return not text or text.casefold() in {"unknown", "null", "none"}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return True
        try:
            return json.loads(text) is None
        except (TypeError, ValueError):
            return False
    return False


def _layout_partition_spec(
    value: Any,
) -> tuple[list[str] | None, list[str] | None, bool, bool]:
    """Normalize captured or published partition column declarations."""

    if value is None:
        return None, None, True, False
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None, None, True, False
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            return None, None, False, True
    if not isinstance(value, (list, tuple)):
        return None, None, False, True
    columns: list[str] = []
    expressions: list[str] = []
    for item in value:
        if isinstance(item, str):
            column = item.strip()
            if not column:
                return None, None, False, True
            columns.append(column)
            continue
        if not isinstance(item, Mapping):
            return None, None, False, True
        column_value = item.get("column", item.get("name"))
        if not isinstance(column_value, str) or not column_value.strip():
            return None, None, False, True
        columns.append(column_value.strip())
        if "expression" not in item or item.get("expression") is None:
            continue
        expression = item.get("expression")
        if not isinstance(expression, str) or not expression.strip():
            return None, None, False, True
        expressions.append(expression.strip())
    return columns, expressions or None, False, False


def _captured_layout(record: Mapping[str, Any]) -> dict[str, Any]:
    raw_values = {
        "format": _value(record, "destination_format", "format"),
        "load_type": _value(record, "destination_load_type", "load_type"),
        "partition_columns": _value(
            record, "destination_partition_columns", "partition_columns"
        ),
        "partition_expressions": _value(
            record,
            "destination_partition_expressions",
            "partition_expressions",
        ),
    }
    values: dict[str, Any] = {}
    states: dict[str, str] = {}
    for field in ("format", "load_type"):
        raw = raw_values[field]
        value = (
            _normalize_layout_value(field, raw)
        )
        values[field] = value
        states[field] = (
            "missing"
            if _layout_input_is_missing(
                raw,
                scalar=field in {"format", "load_type"},
            )
            else "known"
            if value is not None
            else "invalid"
        )
    partition_value, derived_expressions, partition_missing, partition_invalid = (
        _layout_partition_spec(raw_values["partition_columns"])
    )
    values["partition_columns"] = partition_value
    states["partition_columns"] = (
        "missing"
        if partition_missing
        else "invalid"
        if partition_invalid
        else "known"
    )
    expression_raw = raw_values["partition_expressions"]
    if _layout_input_is_missing(expression_raw, scalar=False):
        values["partition_expressions"] = derived_expressions
        states["partition_expressions"] = (
            "known" if derived_expressions is not None else "missing"
        )
    else:
        expression_value = _layout_list(expression_raw)
        values["partition_expressions"] = expression_value
        states["partition_expressions"] = (
            "known" if expression_value is not None else "invalid"
        )
    values["_states"] = states
    return values


def _compose_layout(
    record: Mapping[str, Any],
    captured: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    metadata_revision: str | None,
) -> dict[str, Any]:
    candidates = _metadata_layout_candidates(rows)
    values: dict[str, Any] = {}
    origins: dict[str, str] = {}
    conflicts: list[dict[str, Any]] = []
    used_sources: list[dict[str, Any]] = []
    field_metadata_sources: dict[str, list[dict[str, Any]]] = {
        field: [] for field in LAYOUT_PRESENTATION_FIELDS
    }
    states = captured.get("_states") or {}
    field_candidates = {
        field: _candidates_for_field(field, candidates, captured, states)
        for field in LAYOUT_PRESENTATION_FIELDS
    }
    captured_invalid_fields = {
        field
        for field in LAYOUT_PRESENTATION_FIELDS
        if states.get(field) == "invalid"
    }
    metadata_invalid_fields: set[str] = set()
    invalid_fields = set(captured_invalid_fields)
    metadata_missing_fields = {
        field
        for field in LAYOUT_PRESENTATION_FIELDS
        if states.get(field) == "missing"
    }
    errors: list[dict[str, Any]] = [
        {"field": field, "code": "invalid_captured_value"}
        for field in sorted(invalid_fields)
    ]
    for field in metadata_missing_fields:
        for candidate in field_candidates[field]:
            if field in candidate.get("invalid_fields") or []:
                metadata_invalid_fields.add(field)
                invalid_fields.add(field)
            errors.extend(
                error
                for error in candidate.get("errors") or []
                if isinstance(error, Mapping) and error.get("field") == field
            )

    for field in LAYOUT_PRESENTATION_FIELDS:
        candidates_for_field = field_candidates[field]
        captured_value = captured.get(field)
        if captured_value is not None:
            values[field] = captured_value
            origins[field] = "captured"
            # Captured values win.  Record a metadata disagreement for audit,
            # but never let it replace the observed value.
            alternatives = _known_values(candidates_for_field, field)
            if alternatives and any(not _same_layout_value(captured_value, value) for value in alternatives):
                conflicts.append(_conflict(field, [captured_value, *alternatives], candidates_for_field))
            continue

        if states.get(field) == "invalid":
            # The source supplied a value but it could not be safely
            # normalized.  Keep it unknown and report why rather than treating
            # it as an omitted declaration eligible for metadata fallback.
            values[field] = None
            origins[field] = "unknown"
            continue

        if field in metadata_invalid_fields:
            # A malformed published candidate is evidence of an invalid
            # declaration, not an omitted field eligible for a different
            # candidate's value.
            values[field] = None
            origins[field] = "unknown"
            continue

        alternatives = _known_values(candidates_for_field, field)
        unique = _unique_layout_values(alternatives)
        if len(unique) == 1:
            values[field] = unique[0]
            origins[field] = "metadata"
            field_sources = _merge_sources(
                _sources_for_value(candidates_for_field, field, unique[0])
            )
            field_metadata_sources[field] = field_sources
            used_sources.extend(field_sources)
        elif len(unique) > 1:
            values[field] = None
            origins[field] = "unknown"
            conflicts.append(_conflict(field, unique, candidates_for_field))
            field_sources = _merge_sources(
                _sources_for_values(candidates_for_field, field, unique)
            )
            field_metadata_sources[field] = field_sources
            used_sources.extend(field_sources)
        else:
            values[field] = None
            origins[field] = "unknown"

    used_sources = _merge_sources(used_sources)
    layout: dict[str, Any] = {
        "format": values.get("format"),
        "load_type": values.get("load_type"),
        "partition_columns": values.get("partition_columns"),
        "partition_expressions": values.get("partition_expressions"),
        "observation_time": record.get("latest_time"),
        "dataflow_run_id": record.get("dataflow_run_id"),
        "origins": origins,
        "metadata_sources": used_sources,
        "field_metadata_sources": field_metadata_sources,
        "metadata_revision": str(metadata_revision) if used_sources and metadata_revision else None,
        "conflicts": conflicts,
        "invalid_fields": sorted(invalid_fields),
        "errors": _dedupe_errors(errors),
    }
    return layout


def _candidates_for_field(
    field: str,
    candidates: Sequence[Mapping[str, Any]],
    captured: Mapping[str, Any],
    states: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    """Keep expression fallback bound to the captured partition columns."""

    if field != "partition_expressions" or states.get(field) != "missing":
        return list(candidates)
    partition_state = states.get("partition_columns")
    if partition_state == "invalid":
        return []
    expected_columns = captured.get("partition_columns")
    if partition_state == "known":
        if not isinstance(expected_columns, list) or not expected_columns:
            return []
    elif partition_state == "missing":
        metadata_columns = _unique_layout_values(
            candidate.get("partition_columns")
            for candidate in candidates
            if candidate.get("partition_columns") is not None
        )
        if len(metadata_columns) != 1:
            return []
        expected_columns = metadata_columns[0]
        if not isinstance(expected_columns, list) or not expected_columns:
            return []
    else:
        return []
    return [
        candidate
        for candidate in candidates
        if _same_layout_value(candidate.get("partition_columns"), expected_columns)
    ]


def _metadata_layout_candidates(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        raw_candidates = row.get("layout_candidates")
        if not isinstance(raw_candidates, list):
            raw_candidates = []
        for raw in raw_candidates:
            if not isinstance(raw, Mapping):
                continue
            candidate = {
                field: _normalize_layout_value(field, raw.get(field))
                for field in LAYOUT_PRESENTATION_FIELDS
            }
            candidate["metadata_sources"] = _merge_sources(
                raw.get("metadata_sources") or row.get("metadata_sources") or [],
            )
            candidate["invalid_fields"] = sorted({
                str(field)
                for field in (raw.get("invalid_fields") or [])
                if field in LAYOUT_PRESENTATION_FIELDS
            })
            candidate["errors"] = _dedupe_errors(raw.get("errors") or [])
            candidates.append(candidate)
    return _dedupe_candidates(candidates)


def _context_matches(record: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    endpoint = row.get("endpoint") if isinstance(row.get("endpoint"), Mapping) else {}
    if not _context_id_or_name_matches(
        record.get("dataflow_id"), row.get("dataflow_id"),
        record.get("dataflow_name"), row.get("dataflow_name"),
    ):
        return False
    if not _context_id_or_name_matches(
        record.get("destination_id"),
        row.get("connection_id") or endpoint.get("connection_id"),
        record.get("destination_name"),
        row.get("connection_name") or endpoint.get("connection_name"),
    ):
        return False
    for incoming, candidate in (
        (
            record.get("workspace_id"),
            row.get("workspace_id") or endpoint.get("workspace_id"),
        ),
        (
            record.get("destination_connection_type"),
            endpoint.get("connection_type") or row.get("connection_type"),
        ),
    ):
        incoming_text = _text(incoming).casefold()
        candidate_text = _text(candidate).casefold()
        if incoming_text and candidate_text and incoming_text != candidate_text:
            return False
    return True


def _context_id_or_name_matches(
    incoming_id: Any,
    candidate_id: Any,
    incoming_name: Any,
    candidate_name: Any,
) -> bool:
    """Use an exact ID anchor before considering a display name.

    Published metadata can rename a dataflow or connection without changing
    its ID.  A name-only match remains exact and is never used when a supplied
    ID disagrees.
    """

    incoming_id_text = _text(incoming_id).casefold()
    candidate_id_text = _text(candidate_id).casefold()
    if incoming_id_text:
        return bool(candidate_id_text) and incoming_id_text == candidate_id_text
    incoming_name_text = _text(incoming_name).casefold()
    if incoming_name_text:
        candidate_name_text = _text(candidate_name).casefold()
        return bool(candidate_name_text) and incoming_name_text == candidate_name_text
    return True


def _known_values(candidates: Sequence[Mapping[str, Any]], field: str) -> list[Any]:
    return [candidate[field] for candidate in candidates if candidate.get(field) is not None]


def _unique_layout_values(values: Iterable[Any]) -> list[Any]:
    unique: dict[str, Any] = {}
    for value in values:
        key = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        unique.setdefault(key, value)
    return [unique[key] for key in sorted(unique)]


def _same_layout_value(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, separators=(",", ":"), default=str) == json.dumps(
        right, sort_keys=True, separators=(",", ":"), default=str
    )


def _conflict(
    field: str,
    values: Sequence[Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "field": field,
        "values": _unique_layout_values(values),
        "metadata_sources": _merge_sources(
            source
            for candidate in candidates
            for source in candidate.get("metadata_sources") or []
        ),
    }


def _dedupe_errors(errors: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    values: dict[tuple[str | None, str | None], dict[str, Any]] = {}
    for error in errors:
        if not isinstance(error, Mapping):
            continue
        field = str(error.get("field")) if error.get("field") else None
        code = str(error.get("code")) if error.get("code") else None
        values[(field, code)] = {"field": field, "code": code}
    return [
        values[key]
        for key in sorted(values, key=lambda item: (item[0] or "", item[1] or ""))
    ]


def _sources_for_value(
    candidates: Sequence[Mapping[str, Any]],
    field: str,
    value: Any,
) -> list[dict[str, Any]]:
    return [
        source
        for candidate in candidates
        if candidate.get(field) is not None and _same_layout_value(candidate.get(field), value)
        for source in candidate.get("metadata_sources") or []
    ]


def _sources_for_values(
    candidates: Sequence[Mapping[str, Any]],
    field: str,
    values: Sequence[Any],
) -> list[dict[str, Any]]:
    return [
        source
        for value in values
        for source in _sources_for_value(candidates, field, value)
    ]


def _dedupe_candidates(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        values = {
            field: candidate.get(field)
            for field in LAYOUT_PRESENTATION_FIELDS
        }
        key = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str)
        existing = grouped.setdefault(key, {**values, "metadata_sources": []})
        existing["metadata_sources"] = _merge_sources(
            existing.get("metadata_sources") or [],
            candidate.get("metadata_sources") or [],
        )
        existing["invalid_fields"] = sorted({
            str(field)
            for values in (
                existing.get("invalid_fields") or [],
                candidate.get("invalid_fields") or [],
            )
            for field in values
            if field in LAYOUT_PRESENTATION_FIELDS
        })
        existing["errors"] = _dedupe_errors(
            [*(existing.get("errors") or []), *(candidate.get("errors") or [])]
        )
    return [grouped[key] for key in sorted(grouped)]


def _normalize_layout_value(field: str, value: Any) -> Any:
    if field == "format":
        return canonical_format(_layout_scalar(value))
    if field in {"format", "load_type"}:
        return _layout_scalar(value)
    return _layout_list(value)


def _merge_sources(*groups: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[int, str | None], dict[str, Any]] = {}
    for group in groups:
        for item in group:
            if not isinstance(item, Mapping) or item.get("source_id") is None:
                continue
            try:
                source_id = int(item["source_id"])
            except (TypeError, ValueError):
                continue
            revision = str(item.get("revision")) if item.get("revision") else None
            merged[(source_id, revision)] = {
                "source_id": source_id,
                "revision": revision,
            }
    return [merged[key] for key in sorted(merged, key=lambda value: (value[0], value[1] or ""))]


def _value(record: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in record:
            return record.get(name)
    return None


def _layout_scalar(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        if not text or text.casefold() in {"unknown", "null", "none"}:
            return None
        return text.casefold()
    return None


def _layout_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, (list, tuple)):
        return None
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        text = item.strip()
        if not text:
            return None
        result.append(text)
    return result


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


__all__ = [
    "DestinationLayoutContext",
    "MAX_DESTINATION_LAYOUT_RECORDS",
    "enrich_destination_layouts",
]
