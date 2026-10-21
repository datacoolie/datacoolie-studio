"""Bounded execution-evidence reconstruction at the Monitoring boundary.

The analytics tables deliberately do not contain a serialized copy of an
admitted log record.  Monitoring read models may still expose the historical
``*_record_json`` response fields, so this module reconstructs those fields
only for the rows that are about to leave the API boundary.

There are two separate lifetimes here:

* durable result-cache payloads contain report values, identities and source
  provenance only; and
* an analytics reader fence owns the short-lived typed row used for
  reconstruction.

Keeping those lifetimes separate prevents a cache hit from becoming a second
whole-record store and makes a generation change reject a stale payload before
hydration.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from typing import Any, ContextManager, TypeAlias

from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.analytics.record_codec import (
    reconstruct_record,
    record_json,
    root_column_types,
)


AnalyticsContext: TypeAlias = tuple[Any, list[int], str]

# These names are part of the existing response contract.  They are response
# markers, not storage columns.  ``latest_raw_record_json`` is used by latest
# registry projections while ``attempt_record_json`` is used by recovered
# attempt evidence.
EVIDENCE_FIELDS = frozenset(
    {"raw_record_json", "latest_raw_record_json", "attempt_record_json"}
)

# Native nested root values are useful while a response row is being hydrated,
# but a complete nested record in SQLite is equivalent to a raw copy.  Report
# specific nested objects (for example ``freshness_evidence``) are intentionally
# not in this set and remain cacheable when they are bounded report output.
ROOT_RECORD_FIELDS = frozenset(
    {
        "metrics",
        "read_request",
        "attempts",
        "diagnostic_issues",
    }
)
_DATAFLOW_DETAIL_FIELDS = frozenset({"destination_operation_details", "transformers_applied"})

PROVENANCE_FIELDS = frozenset(
    {"_source_id", "_file_uri", "_record_line", "_file_kind"}
)

CACHE_GENERATION_FIELD = "_monitoring_analytics_generation"
CACHE_INPUT_FINGERPRINT_FIELD = "_monitoring_input_fingerprint"

# API routes currently cap pages at 500 rows and log listings at 10,000 rows.
# The helper accepts an explicit limit so a caller cannot accidentally turn a
# report aggregate into an unbounded evidence reconstruction operation.
DEFAULT_MAX_HYDRATION_ROWS = 500
MAX_HYDRATION_ROWS = 10_000
HYDRATION_BATCH_SIZE = 500

_NO_SOURCE_ROW = object()


class EvidenceHydrationError(RuntimeError):
    """Base error for a response that cannot safely reconstruct evidence."""


class EvidenceGenerationMismatch(EvidenceHydrationError):
    """The cache payload was produced for a different analytics generation."""


class EvidenceHydrationLimit(EvidenceHydrationError):
    """A caller attempted to hydrate more rows than its explicit bound."""


def cache_payload(
    payload: Mapping[str, Any],
    *,
    generation: str,
    input_fingerprint: str,
) -> dict[str, Any]:
    """Return the durable-cache representation of a Monitoring payload.

    Evidence strings and native root containers are replaced by ``None`` on
    execution rows.  The response field remains as a marker so a later cache
    hit knows which selected rows require hydration.  The generation and
    fingerprint are small cache metadata, removed by :func:`hydrate_payload`
    before the response is returned.
    """

    if not isinstance(payload, Mapping):
        raise TypeError("Monitoring cache payload must be a mapping")
    result = _cache_value(payload, execution_row=False)
    if not isinstance(result, dict):  # pragma: no cover - _cache_value contract
        raise TypeError("Monitoring cache payload must produce a mapping")
    result[CACHE_GENERATION_FIELD] = str(generation)
    result[CACHE_INPUT_FINGERPRINT_FIELD] = str(input_fingerprint)
    return result


def hydrate_payload(
    payload: Mapping[str, Any],
    analytics_context: AnalyticsContext,
    *,
    expected_generation: str,
    expected_input_fingerprint: str,
    max_rows: int = DEFAULT_MAX_HYDRATION_ROWS,
) -> dict[str, Any]:
    """Hydrate marked evidence in a cached payload under one reader fence.

    The cache metadata is checked before any row lookup or codec call.  A
    changed generation or input fingerprint therefore causes the caller to
    rebuild the cache entry rather than reconstructing from stale identities.
    """

    _validate_limit(max_rows)
    if not isinstance(payload, Mapping):
        raise TypeError("Monitoring cached payload must be a mapping")
    generation = payload.get(CACHE_GENERATION_FIELD)
    fingerprint = payload.get(CACHE_INPUT_FINGERPRINT_FIELD)
    if str(generation or "") != str(expected_generation):
        raise EvidenceGenerationMismatch(
            "Monitoring evidence cache generation no longer matches the analytics reader"
        )
    if str(fingerprint or "") != str(expected_input_fingerprint):
        raise EvidenceGenerationMismatch(
            "Monitoring evidence cache fingerprint no longer matches the request"
        )

    # Reapply the cache redaction on every hit as a defensive boundary.  This
    # also makes an entry written by an older producer safe before it reaches
    # the response, while the marker checks above still reject stale entries.
    body = _cache_value(
        {
            key: value
            for key, value in payload.items()
            if key not in {CACHE_GENERATION_FIELD, CACHE_INPUT_FINGERPRINT_FIELD}
        },
        execution_row=False,
    )
    marker_rows: list[dict[str, Any]] = []
    _collect_marker_rows(body, marker_rows)
    if len(marker_rows) > max_rows:
        raise EvidenceHydrationLimit(
            f"Monitoring evidence hydration is bounded at {max_rows} rows"
        )
    source_rows = _batch_source_rows(marker_rows, analytics_context, kind=None)
    for row in marker_rows:
        resolved_kind = _record_kind(row, None)
        source_key = _provenance_key(row, resolved_kind)
        _hydrate_row(
            row,
            analytics_context,
            kind=None,
            evidence_fields=tuple(EVIDENCE_FIELDS.intersection(row)),
            max_rows=max_rows,
            source_row=_source_rows_get(source_rows, source_key),
        )
    return body


def hydrate_rows(
    rows: Sequence[Mapping[str, Any]],
    analytics_context: AnalyticsContext,
    *,
    kind: str | None = None,
    evidence_fields: Sequence[str] = ("raw_record_json",),
    native_fields: Sequence[str] = (),
    max_rows: int = DEFAULT_MAX_HYDRATION_ROWS,
) -> list[dict[str, Any]]:
    """Hydrate evidence for a bounded list of selected API rows.

    ``analytics_context`` is the tuple returned by ``monitoring.context.reader``
    and must remain open for the duration of this call.  Rows that have no
    requested evidence marker are copied unchanged.  A row with provenance but
    without the native root columns is looked up by source/file/line in the
    typed base table; this is still an analytics-cache read, never a raw-file
    reread. Direct detail consumers can also request native evidence containers
    for semantic enrichment; they reuse that bounded lookup and remain excluded
    from durable result-cache payloads.
    """

    _validate_limit(max_rows)
    if len(rows) > max_rows:
        raise EvidenceHydrationLimit(
            f"Monitoring evidence hydration is bounded at {max_rows} rows"
        )
    requested = _validate_evidence_fields(evidence_fields)
    allowed_native = ROOT_RECORD_FIELDS | (frozenset(root_column_types(kind)) if kind else frozenset())
    if kind == "dataflow_run_log":
        allowed_native |= _DATAFLOW_DETAIL_FIELDS
    if set(native_fields) - allowed_native:
        raise ValueError("Unsupported native execution evidence fields")
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            result.append(dict(row))
            continue
        result.append(dict(row))
    source_rows = _batch_source_rows(
        [row for row in result if native_fields or any(row.get(field) is None for field in requested)],
        analytics_context,
        kind=kind,
        projection_fields=tuple(set(native_fields) & _DATAFLOW_DETAIL_FIELDS),
        native_fields=tuple(native_fields) if not requested else None,
    )
    for row in result:
        source_key = _provenance_key(row, _record_kind(row, kind))
        source_row = _source_rows_get(source_rows, source_key)
        if source_row is not None:
            for field in native_fields:
                if field in source_row:
                    row[field] = copy.deepcopy(source_row[field])
        _hydrate_row(
            row,
            analytics_context,
            kind=kind,
            evidence_fields=requested,
            max_rows=max_rows,
            source_row=source_row,
        )
    return result


def hydrate_row(
    row: Mapping[str, Any],
    analytics_context: AnalyticsContext,
    *,
    kind: str | None = None,
    evidence_fields: Sequence[str] = ("raw_record_json",),
) -> dict[str, Any]:
    """Hydrate one response row using the same bounded implementation."""

    return hydrate_rows(
        [row],
        analytics_context,
        kind=kind,
        evidence_fields=evidence_fields,
        max_rows=1,
    )[0]


def reader_context(
    analytics_context: AnalyticsContext | None,
    paths: Sequence[Any] | None = None,
) -> ContextManager[AnalyticsContext]:
    """Adapt an optional existing fence for callers that own the reader.

    This small adapter keeps query/page modules from opening a second reader
    when the route already supplies one.  It is intentionally kept here so
    hydration and SQL selection share one lifecycle contract.
    """

    if analytics_context is not None:
        return nullcontext(analytics_context)
    if paths is None:
        raise ValueError("paths are required when no analytics reader context is supplied")
    from datacoolie_studio.domains.monitoring.context import reader

    return reader(list(paths))


def _cache_value(value: Any, *, execution_row: bool) -> Any:
    if isinstance(value, Mapping):
        is_row = execution_row or _looks_like_execution_row(value)
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in EVIDENCE_FIELDS:
                # Keep the response key as a null marker; never persist its
                # reconstructed text.
                result[key_text] = None
            elif is_row and key_text in ROOT_RECORD_FIELDS:
                # Native record containers must be reloaded from DuckDB on a
                # cache hit.  Scalar report values remain durable cache data.
                continue
            else:
                result[key_text] = _cache_value(item, execution_row=is_row)
        return result
    if isinstance(value, (list, tuple)):
        return [_cache_value(item, execution_row=False) for item in value]
    return copy.deepcopy(value)


def _hydrate_row(
    row: dict[str, Any],
    analytics_context: AnalyticsContext,
    *,
    kind: str | None,
    evidence_fields: Sequence[str],
    max_rows: int,
    source_row: Mapping[str, Any] | None | object = _NO_SOURCE_ROW,
) -> dict[str, Any]:
    pending = [
        field
        for field in evidence_fields
        if field in EVIDENCE_FIELDS and row.get(field) is None
    ]
    if not pending:
        return row
    if max_rows < 1:
        raise EvidenceHydrationLimit("Monitoring evidence hydration requires a positive bound")
    resolved_kind = _record_kind(row, kind)
    if resolved_kind is None:
        return row
    conn = analytics_context[0]
    source_ids = analytics_context[1]
    if conn is None or not source_ids:
        return row
    if source_row is _NO_SOURCE_ROW:
        source_row = _source_row(row, conn, resolved_kind, analytics_context[1])
    if source_row is None:
        return row
    record = _reconstruct(source_row, resolved_kind)
    if not isinstance(record, Mapping):
        raise EvidenceHydrationError("Analytics record codec returned a non-object record")
    serialized = _serialize(record)
    for field in pending:
        if field in {"raw_record_json", "latest_raw_record_json"}:
            row[field] = serialized
        elif field == "attempt_record_json":
            attempt = _selected_attempt(record, row)
            if attempt is not None:
                row[field] = _serialize(attempt)
    return row


def _source_row(
    row: Mapping[str, Any],
    conn: Any,
    kind: str,
    source_ids: Sequence[Any] = (),
) -> dict[str, Any] | None:
    # Serving facts replace a few root names with report projections
    # (for example duration and time).  When provenance is available, always
    # fetch the matching base-table row so the codec never serializes a
    # report-altered value.  Durable cache payloads omit root containers and take
    # this same path after a hit.
    source_id, file_uri, record_line, file_kind = _provenance_values(row)
    if source_id is None or file_uri is None or record_line is None:
        return None
    if source_ids and str(source_id) not in {str(value) for value in source_ids}:
        return None
    table = (
        analytics_schema.DATAFLOW_TABLE
        if kind == "dataflow_run_log"
        else analytics_schema.JOB_TABLE
    )
    available = set(analytics_schema.table_columns(conn, table))
    if not available:
        raise EvidenceHydrationError("Typed analytics evidence table is unavailable")
    if "_file_kind" not in available:
        raise EvidenceHydrationError(
            "Typed analytics evidence table is missing _file_kind provenance"
        )
    predicates = ["_source_id = ?", "_file_uri IS NOT DISTINCT FROM ?", "_record_line IS NOT DISTINCT FROM ?"]
    params: list[Any] = [source_id, file_uri, record_line]
    if file_kind is not None and "_file_kind" in available:
        predicates.append("_file_kind IS NOT DISTINCT FROM ?")
        params.append(file_kind)
    try:
        result = conn.execute(
            f"SELECT * FROM {table} WHERE {' AND '.join(predicates)} LIMIT 1",
            params,
        )
    except Exception as exc:  # DuckDB wraps missing transitional columns in BinderException.
        raise EvidenceHydrationError("Could not read the typed analytics evidence row") from exc
    values = result.fetchone()
    if values is None:
        return None
    names = [description[0] for description in result.description]
    return dict(zip(names, values))


def _batch_source_rows(
    rows: Sequence[Mapping[str, Any]],
    analytics_context: AnalyticsContext,
    *,
    kind: str | None,
    projection_fields: Sequence[str] = (),
    native_fields: Sequence[str] | None = None,
) -> dict[tuple[str, str, str, str, str | None], dict[str, Any]]:
    """Read the selected typed rows in bounded provenance batches.

    A response can expose the same source row through several evidence
    markers (for example raw, latest, and an attempt projection).  Grouping
    the identities before the SQL read keeps that case one source lookup and
    caps the query parameter list for large latest pages.
    """

    conn, source_ids, _generation = analytics_context
    if conn is None or not source_ids:
        return {}
    allowed_source_ids = {str(value) for value in source_ids}
    grouped: dict[
        tuple[str, bool],
        dict[tuple[str, str, str, str, str | None], tuple[Any, ...]],
    ] = {}
    available_by_kind: dict[str, set[str]] = {}

    for row in rows:
        if not isinstance(row, Mapping):
            continue
        resolved_kind = _record_kind(row, kind)
        source_key = _provenance_key(row, resolved_kind)
        if source_key is None or source_key[1] not in allowed_source_ids:
            continue
        if resolved_kind not in available_by_kind:
            table = _table_for_kind(resolved_kind)
            try:
                available_by_kind[resolved_kind] = set(
                    analytics_schema.table_columns(conn, table)
                )
            except Exception as exc:
                raise EvidenceHydrationError(
                    "Could not inspect the typed analytics evidence table"
                ) from exc
        available = available_by_kind[resolved_kind]
        if "_file_kind" not in available:
            raise EvidenceHydrationError(
                "Typed analytics evidence table is missing _file_kind provenance"
            )
        include_file_kind = source_key[4] is not None
        group_key = (resolved_kind, include_file_kind)
        source_id, file_uri, record_line, file_kind = _provenance_values(row)
        source_values: tuple[Any, ...] = (
            source_id,
            file_uri,
            record_line,
        )
        if include_file_kind:
            source_values += (file_kind,)
        grouped.setdefault(group_key, {})[source_key] = source_values

    hydrated: dict[tuple[str, str, str, str, str | None], dict[str, Any]] = {}
    for (resolved_kind, include_file_kind), requested in grouped.items():
        table = _table_for_kind(resolved_kind)
        columns = ("_source_id", "_file_uri", "_record_line")
        if include_file_kind:
            columns += ("_file_kind",)
        requested_keys = tuple(requested)
        for offset in range(0, len(requested_keys), HYDRATION_BATCH_SIZE):
            chunk_keys = requested_keys[offset : offset + HYDRATION_BATCH_SIZE]
            values_sql = ", ".join(
                "(" + ", ".join("?" for _ in columns) + ")"
                for _ in chunk_keys
            )
            column_sql = ", ".join(columns)
            predicates = [
                "base._source_id = wanted._source_id",
                "base._file_uri IS NOT DISTINCT FROM wanted._file_uri",
                "base._record_line IS NOT DISTINCT FROM wanted._record_line",
            ]
            if include_file_kind:
                predicates.append(
                    "base._file_kind IS NOT DISTINCT FROM wanted._file_kind"
                )
            parameters = [
                value
                for source_key in chunk_keys
                for value in requested[source_key]
            ]
            from datacoolie_studio.domains.analytics.v6_projection import field_expression
            extra_projection = ''.join(
                f', {field_expression(field, "VARCHAR", raw="base")} AS "{field}"'
                for field in projection_fields if resolved_kind == "dataflow_run_log"
            )
            if native_fields is None:
                base_projection = "base.*"
            else:
                # Native-only enrichment needs provenance plus its explicit
                # fields; raw reconstruction retains the full root projection.
                selected_fields = tuple(dict.fromkeys((
                    "_source_id", "_file_uri", "_record_line", "_file_kind",
                    "dataflow_id" if resolved_kind == "dataflow_run_log" else "job_id",
                    *native_fields,
                )))
                base_projection = ', '.join(
                    f'base."{field}"' for field in selected_fields
                    if field in available_by_kind[resolved_kind]
                )
            sql = (
                f"WITH wanted ({column_sql}) AS (VALUES {values_sql}) "
                f"SELECT {base_projection}{extra_projection} FROM {table} AS base "
                f"JOIN wanted ON {' AND '.join(predicates)}"
            )
            try:
                query = conn.execute(sql, parameters)
                values = query.fetchall()
            except Exception as exc:
                raise EvidenceHydrationError(
                    "Could not read the typed analytics evidence rows"
                ) from exc
            descriptions = getattr(query, "description", None) or ()
            names = [description[0] for description in descriptions]
            for row_values in values:
                if row_values is None:
                    continue
                source_row = dict(zip(names, row_values))
                base_key = _provenance_key(source_row, resolved_kind)
                if base_key is None:
                    continue
                if include_file_kind:
                    hydrated[base_key] = source_row
                    continue
                # A row without a requested file kind is matched by its
                # three-part source identity, while exact file-kind requests
                # remain qualified in the batch above.
                base_key_without_kind = (*base_key[:4], None)
                hydrated[base_key_without_kind] = source_row
                for requested_key in chunk_keys:
                    if requested_key[:4] == base_key_without_kind[:4]:
                        hydrated[requested_key] = source_row
    return hydrated


def _source_rows_get(
    source_rows: Mapping[tuple[str, str, str, str, str | None], dict[str, Any]],
    source_key: tuple[str, str, str, str, str | None] | None,
) -> dict[str, Any] | None:
    if source_key is None:
        return None
    return source_rows.get(source_key)


def _provenance_key(
    row: Mapping[str, Any],
    kind: str | None,
) -> tuple[str, str, str, str, str | None] | None:
    if kind is None:
        return None
    source_id, file_uri, record_line, file_kind = _provenance_values(row)
    if source_id is None or file_uri is None or record_line is None:
        return None
    return (
        str(kind),
        str(source_id),
        str(file_uri),
        str(record_line),
        None if file_kind is None else str(file_kind),
    )


def _provenance_values(row: Mapping[str, Any]) -> tuple[Any, Any, Any, Any]:
    """Return technical source identity, including latest projection aliases."""

    return (
        _first(row, "_source_id", "latest_source_id", "source_id"),
        _first(row, "_file_uri", "latest_file_uri", "file_uri", "uri"),
        _first(row, "_record_line", "latest_record_line", "record_line", "line"),
        _first(row, "_file_kind", "latest_file_kind", "file_kind"),
    )


def _collect_marker_rows(value: Any, result: list[dict[str, Any]]) -> None:
    if isinstance(value, Mapping):
        marker_fields = EVIDENCE_FIELDS.intersection(value)
        if marker_fields and any(value.get(field) is None for field in marker_fields):
            result.append(value if isinstance(value, dict) else dict(value))
            return
        for item in value.values():
            _collect_marker_rows(item, result)
    elif isinstance(value, list):
        for item in value:
            _collect_marker_rows(item, result)


def _table_for_kind(kind: str) -> str:
    return (
        analytics_schema.DATAFLOW_TABLE
        if kind == "dataflow_run_log"
        else analytics_schema.JOB_TABLE
    )


def _selected_attempt(record: Mapping[str, Any], row: Mapping[str, Any]) -> Any:
    attempts = record.get("attempts")
    if not isinstance(attempts, Sequence) or isinstance(attempts, (str, bytes, bytearray)):
        return None
    attempt_values = list(attempts)
    index = _first(row, "attempt_index", "_attempt_index", "attempt_idx")
    if index is not None:
        try:
            index_value = int(index)
        except (TypeError, ValueError):
            return None
        return attempt_values[index_value] if 0 <= index_value < len(attempt_values) else None
    number = _first(row, "attempt_number", "_attempt_number")
    if number is not None:
        for attempt in attempt_values:
            if isinstance(attempt, Mapping) and _same_attempt_number(
                attempt.get("attempt_number"), number
            ):
                return attempt
        return None
    return attempt_values[0] if len(attempt_values) == 1 else None


def _same_attempt_number(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return str(left) == str(right)


def _record_kind(row: Mapping[str, Any], explicit: str | None) -> str | None:
    candidate = explicit or _first(row, "_type", "record_type", "record_kind")
    if candidate is not None:
        value = str(candidate).strip().lower()
        if value in {"dataflow", "dataflow_jsonl", "dataflow_parquet", "dataflow_run_log"}:
            return "dataflow_run_log"
        if value in {"job", "job_jsonl", "job_parquet", "job_run_log"}:
            return "job_run_log"
    if any(key in row for key in ("dataflow_run_id", "dataflow_id", "attempt_number", "attempt_index")):
        return "dataflow_run_log"
    if "job_id" in row:
        return "job_run_log"
    return None


def _reconstruct(row: Mapping[str, Any], kind: str) -> Mapping[str, Any]:
    try:
        return reconstruct_record(dict(row), kind)
    except EvidenceHydrationError:
        raise
    except Exception as exc:
        raise EvidenceHydrationError("The analytics record codec could not reconstruct evidence") from exc


def _serialize(value: Any) -> str:
    try:
        result = record_json(value)
    except Exception as exc:
        raise EvidenceHydrationError("The analytics record codec could not serialize evidence") from exc
    if not isinstance(result, str):
        raise EvidenceHydrationError("The analytics record codec serializer must return text")
    return result


def _looks_like_execution_row(value: Mapping[str, Any]) -> bool:
    # An API row may be missing provenance during a transitional projection;
    # the evidence marker still identifies it as an execution payload whose
    # native containers must not be copied into durable SQLite.
    if EVIDENCE_FIELDS.intersection(value):
        return True
    if ROOT_RECORD_FIELDS.intersection(value) and {
        "dataflow_run_id",
        "dataflow_id",
        "job_id",
        "log_session_id",
    }.intersection(value):
        return True
    return bool(
        PROVENANCE_FIELDS.intersection(value)
        and any(
            key in value
            for key in (
                "dataflow_run_id",
                "dataflow_id",
                "job_id",
                *EVIDENCE_FIELDS,
            )
        )
    )


def _first(value: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in value:
            return value[key]
    return None


def _validate_limit(max_rows: int) -> None:
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
        raise EvidenceHydrationLimit("Monitoring evidence hydration bound must be a positive integer")
    if max_rows > MAX_HYDRATION_ROWS:
        raise EvidenceHydrationLimit(
            f"Monitoring evidence hydration cannot exceed {MAX_HYDRATION_ROWS} rows"
        )


def _validate_evidence_fields(fields: Sequence[str]) -> tuple[str, ...]:
    requested = tuple(dict.fromkeys(str(field) for field in fields))
    invalid = set(requested) - EVIDENCE_FIELDS
    if invalid:
        raise ValueError(f"Unsupported Monitoring evidence fields: {', '.join(sorted(invalid))}")
    return requested
