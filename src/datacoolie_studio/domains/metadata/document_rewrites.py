"""Pure metadata document rewrites used by editor workflows."""

from __future__ import annotations

from typing import Any

from datacoolie_studio.domains.metadata.document_primitives import (
    sheet_rows,
    string_value,
)


CONNECTION_REFERENCE_FIELDS = (
    "connection_name",
    "source_connection_name",
    "destination_connection_name",
)


def synchronize_connection_name_references(
    document: dict[str, Any],
    baseline_document: dict[str, Any],
) -> dict[str, Any]:
    """Apply connection renames to dataflow and schema-hint references."""

    baseline_connections = sheet_rows(
        baseline_document.get("sheets") or {}, "connections"
    )
    next_connections = sheet_rows(document.get("sheets") or {}, "connections")
    baseline_names = {
        _connection_row_identity(row, index): string_value(row.get("name"))
        for index, row in enumerate(baseline_connections)
        if string_value(row.get("name"))
    }
    renames: dict[str, str] = {}
    for index, row in enumerate(next_connections):
        previous_name = baseline_names.get(_connection_row_identity(row, index))
        next_name = string_value(row.get("name"))
        if previous_name and next_name and previous_name != next_name:
            renames[previous_name] = next_name
    if not renames:
        return document

    sheets = dict(document.get("sheets") or {})
    for sheet_name in ("dataflows", "schema_hints"):
        sheet = sheets.get(sheet_name) or {}
        rows = sheet_rows(sheets, sheet_name)
        sheets[sheet_name] = {
            **sheet,
            "rows": [_replace_connection_name_references(row, renames) for row in rows],
        }
    return {**document, "sheets": sheets}


def _connection_row_identity(row: dict[str, Any], index: int) -> str:
    connection_id = string_value(row.get("connection_id"))
    return f"id:{connection_id}" if connection_id else f"index:{index}"


def _replace_connection_name_references(
    row: dict[str, Any],
    renames: dict[str, str],
) -> dict[str, Any]:
    updated = dict(row)
    changed = False
    for field in CONNECTION_REFERENCE_FIELDS:
        current_name = string_value(row.get(field))
        replacement = renames.get(current_name)
        if replacement and replacement != current_name:
            updated[field] = replacement
            changed = True
    return updated if changed else row
