"""Pure transformations between metadata file and editor document shapes."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from datacoolie_studio.domains.metadata.document_primitives import (
    is_empty,
    parse_cell_value,
    sheet_rows,
    string_value,
)
from datacoolie_studio.domains.metadata.document_schema import (
    FILE_RUNTIME_FIELDS,
)
from datacoolie_studio.domains.metadata.reader import (
    MetadataDocumentRead,
    MetadataReadError,
)


def editor_document_to_raw_metadata(document: dict[str, Any]) -> dict[str, Any]:
    sheets = document.get("sheets") or {}
    connection_columns = sheet_column_keys(sheets, "connections")
    dataflow_columns = sheet_column_keys(sheets, "dataflows")
    schema_hint_columns = sheet_column_keys(sheets, "schema_hints")
    raw = {
        "connections": [
            clean_row(
                order_row(row, connection_columns),
                FILE_RUNTIME_FIELDS["connections"],
            )
            for row in sheet_rows(sheets, "connections")
        ],
        "dataflows": [
            unflatten_dataflow(order_row(row, dataflow_columns))
            for row in sheet_rows(sheets, "dataflows")
        ],
        "schema_hints": group_schema_hints(
            [
                order_row(row, schema_hint_columns)
                for row in sheet_rows(sheets, "schema_hints")
            ]
        ),
    }
    metadata_document = document.get("metadata_document")
    if isinstance(metadata_document, dict):
        for section, value in metadata_document.get("opaque_sections", {}).items():
            if raw.get(section):
                raise MetadataReadError(f"Cannot overwrite unprojectable metadata section: {section}")
            raw[section] = deepcopy(value)
        extras = metadata_document.get("extras")
        if isinstance(extras, dict):
            for key, value in extras.items():
                if (
                    isinstance(key, str)
                    and key not in raw
                    and key != "metadata"
                ):
                    raw[key] = deepcopy(value)
        if metadata_document.get("wrapper") == "metadata":
            return {**deepcopy(metadata_document.get("envelope_extras") or {}), "metadata": raw}
    return raw


def editor_document_to_source_metadata(document: dict[str, Any]) -> Any:
    """Return the serialized shape required by the original metadata source.

    The editor always works with the canonical three-section document.  A
    modular source may instead be a JSON/YAML list or a single record, so the
    storage serializer must restore that shape instead of silently changing
    the source contract on every save.
    """

    raw = editor_document_to_raw_metadata(document)
    metadata_document = document.get("metadata_document")
    if not isinstance(metadata_document, dict):
        return raw

    section = metadata_document.get("fragment_section")
    shape = metadata_document.get("fragment_shape")
    if not isinstance(section, str) or shape not in {"list", "record"}:
        return raw

    payload = raw.get(section)
    if not isinstance(payload, list):
        raise MetadataReadError(
            f"Cannot serialize metadata fragment '{section}': section is not a list"
        )
    if shape == "list":
        return payload
    if len(payload) != 1:
        raise MetadataReadError(
            "Cannot serialize a single-record metadata fragment after adding or "
            "removing records"
        )
    return payload[0]


def metadata_payload(raw_metadata: dict[str, Any]) -> dict[str, Any]:
    wrapped = raw_metadata.get("metadata")
    return wrapped if isinstance(wrapped, dict) else raw_metadata


def metadata_document_state(
    document_info: MetadataDocumentRead | None,
    raw_metadata: dict[str, Any],
) -> dict[str, Any]:
    if document_info is not None:
        state = {
            "wrapper": document_info.wrapper,
            "extras": deepcopy(document_info.extras),
            **({"envelope_extras": deepcopy(document_info.envelope_extras)} if document_info.envelope_extras else {}),
            **({"opaque_sections": opaque_metadata_sections(document_info.payload)}
               if opaque_metadata_sections(document_info.payload) else {}),
        }
        if document_info.fragment_section is not None:
            state.update(
                {
                    "fragment_section": document_info.fragment_section,
                    "fragment_shape": document_info.fragment_shape,
                }
            )
        return state
    payload = metadata_payload(raw_metadata)
    return {
        "wrapper": "metadata" if payload is not raw_metadata else None,
        **({"envelope_extras": {key: deepcopy(value) for key, value in raw_metadata.items() if key != "metadata"}} if payload is not raw_metadata else {}),
        **({"opaque_sections": opaque_metadata_sections(payload)} if opaque_metadata_sections(payload) else {}),
        "extras": {
            key: deepcopy(value)
            for key, value in payload.items()
            if key not in {"connections", "dataflows", "schema_hints"}
        },
    }


def sheet_column_keys(sheets: dict[str, Any], sheet_name: str) -> list[str]:
    sheet = sheets.get(sheet_name) or {}
    columns = (sheet.get("columns") or []) if isinstance(sheet, dict) else []
    return [
        str(column.get("key"))
        for column in columns
        if isinstance(column, dict) and column.get("key")
    ]


def order_row(row: dict[str, Any], column_keys: list[str]) -> dict[str, Any]:
    ordered = {key: row[key] for key in column_keys if key in row}
    ordered.update({key: value for key, value in row.items() if key not in ordered})
    return ordered


def clean_row(
    row: dict[str, Any],
    excluded_fields: set[str] | None = None,
) -> dict[str, Any]:
    excluded_fields = excluded_fields or set()
    result: dict[str, Any] = {}
    for key, value in row.items():
        preserve_empty = key in row.get("__studio_authored_empty_fields", [])
        original = parse_cell_value(row.get("__studio_original_values", {}))
        original = original if isinstance(original, dict) else {}
        unchanged = key in original and value == stringify_cell_value(original[key])
        if (
            key.startswith("__")
            or (
                key in excluded_fields
                and not authored_runtime_field(row, key)
            )
            or (is_empty(value) and not preserve_empty)
            or is_default_omitted_value(key, value)
        ):
            continue
        result[key] = deepcopy(original[key]) if unchanged else (value if preserve_empty else parse_cell_value(value))
    return result


def unflatten_dataflow(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    nested: dict[str, dict[str, Any]] = {
        "source": {},
        "transform": {},
        "destination": {},
    }
    for key, value in row.items():
        preserve_empty = key in row.get("__studio_authored_empty_fields", [])
        original = parse_cell_value(row.get("__studio_original_values", {}))
        original = original if isinstance(original, dict) else {}
        unchanged = key in original and value == stringify_cell_value(original[key])
        if (
            key.startswith("__")
            or (
                key in FILE_RUNTIME_FIELDS["dataflows"]
                and not authored_runtime_field(row, key)
            )
            or (is_empty(value) and not preserve_empty)
            or is_default_omitted_value(key, value)
        ):
            continue
        target = None
        field = key
        for prefix in nested:
            marker = f"{prefix}_"
            if key.startswith(marker):
                target = prefix
                field = key[len(marker):]
                break
        parsed = deepcopy(original[key]) if unchanged else (value if preserve_empty else parse_cell_value(value))
        if target:
            nested[target][field] = parsed
        else:
            result[key] = parsed
    for key, value in nested.items():
        if value:
            result[key] = value
    return result


def group_schema_hints(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    ungrouped: list[dict[str, Any]] = []
    for row in rows:
        clean = clean_row(row)
        if not clean:
            continue
        connection_name = string_value(clean.get("connection_name"))
        connection_id = string_value(clean.get("connection_id"))
        table_name = string_value(clean.get("table_name"))
        schema_name = string_value(clean.get("schema_name"))
        if not (connection_name or connection_id) or not table_name:
            ungrouped.append(clean)
            continue
        reference_key = f"id:{connection_id}" if connection_id else f"name:{connection_name}"
        key = (reference_key, table_name, schema_name)
        parent: dict[str, Any] = {
            "table_name": table_name,
            **({"connection_name": connection_name} if connection_name else {}),
            **({"connection_id": connection_id} if connection_id else {}),
            **({"schema_name": schema_name} if schema_name else {}),
            "hints": [],
        }
        parent = grouped.setdefault(key, parent)
        for field, value in (
            ("connection_name", connection_name),
            ("connection_id", connection_id),
            ("schema_name", schema_name),
        ):
            if value and field not in parent:
                parent[field] = value
        hint = {
            field: value
            for field, value in clean.items()
            if field not in {"connection_name", "connection_id", "table_name", "schema_name"}
        }
        parent["hints"].append(hint)
    return [*grouped.values(), *ungrouped]


def schema_hints_need_opaque(hints: list[dict[str, Any]]) -> bool:
    parent_keys = {"connection_name", "connection_id", "table_name", "schema_name"}
    seen: set[str] = set()
    for group in hints:
        if "hints" not in group:
            continue
        children = group["hints"]
        if not isinstance(children, list) or not children or any(not isinstance(hint, dict) for hint in children):
            return True
        if set(group) - parent_keys - {"hints"} or any(set(hint) & parent_keys for hint in children):
            return True
        identity = json.dumps({key: group.get(key) for key in parent_keys}, sort_keys=True)
        if identity in seen:
            return True
        seen.add(identity)
    return False


def opaque_metadata_sections(raw: dict[str, Any]) -> dict[str, Any]:
    opaque = {}
    for key in ("connections", "dataflows", "schema_hints"):
        if key not in raw:
            continue
        value = raw[key]
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            opaque[key] = deepcopy(value)
        elif key == "schema_hints" and schema_hints_need_opaque(value):
            opaque[key] = deepcopy(value)
        elif key == "dataflows" and any(
            any(endpoint in row and any(field.startswith(endpoint + "_") for field in row)
                for endpoint in ("source", "destination", "transform")) for row in value
        ):
            opaque[key] = deepcopy(value)
    return opaque


def raw_metadata_to_sheets(raw: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    opaque = opaque_metadata_sections(raw)
    raw = {key: value for key, value in raw.items() if key not in opaque}
    return {
        "connections": [
            mark_authored_runtime_fields(dict(row), {"connection_id"})
            for row in raw.get("connections", [])
            if isinstance(row, dict)
        ],
        "dataflows": [
            mark_authored_runtime_fields(flatten_dataflow(row), {"dataflow_id"})
            for row in raw.get("dataflows", [])
            if isinstance(row, dict)
        ],
        "schema_hints": [mark_authored_runtime_fields(row, set()) for row in flatten_schema_hints(raw.get("schema_hints", []))],
    }


def mark_authored_runtime_fields(
    row: dict[str, Any],
    fields: set[str],
) -> dict[str, Any]:
    row["__studio_original_values"] = deepcopy(row)
    empty_fields = [key for key, value in row.items() if value is None or value == ""]
    if empty_fields:
        row["__studio_authored_empty_fields"] = empty_fields
    for field in fields:
        if field in row:
            row[f"__studio_authored_{field}"] = True
    return row


def authored_runtime_field(row: dict[str, Any], field: str) -> bool:
    return row.get(f"__studio_authored_{field}") is True


def flatten_dataflow(row: dict[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, value in row.items():
        if key in {"source", "destination", "transform"} and isinstance(value, dict):
            if not value:
                flattened[key] = value
            else:
                flattened.update({f"{key}_{field}": field_value for field, field_value in value.items()})
        else:
            flattened[key] = value
    return flattened


def stringify_complex_values(
    sheets: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    return {
        sheet_name: [
            {key: value if key.startswith("__studio_") else stringify_cell_value(value) for key, value in row.items()}
            for row in rows
        ]
        for sheet_name, rows in sheets.items()
    }


def stringify_cell_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def flatten_schema_hints(raw_hints: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(raw_hints, list):
        return rows
    for item in raw_hints:
        if not isinstance(item, dict):
            continue
        hints = item.get("hints")
        if isinstance(hints, list):
            base = {key: value for key, value in item.items() if key != "hints"}
            for hint in hints:
                if isinstance(hint, dict):
                    rows.append({**base, **hint})
        else:
            rows.append(dict(item))
    return rows


def is_default_omitted_value(key: str, value: Any) -> bool:
    return key == "is_active" and parse_cell_value(value) is True
