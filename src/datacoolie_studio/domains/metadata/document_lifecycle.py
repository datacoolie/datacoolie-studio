"""Pure lifecycle normalization applied before metadata persistence."""

from __future__ import annotations

from typing import Any

from datacoolie_studio.core.identity import name_to_uuid
from datacoolie_studio.domains.metadata.document_primitives import (
    is_empty,
    parse_cell_value,
    sheet_rows,
    string_value,
)
from datacoolie_studio.domains.metadata.document_schema import REQUIRED_SHEETS


def materialize_generated_ids(sheets: dict[str, list[dict[str, Any]]]) -> None:
    for row in sheets.get("connections", []):
        name = string_value(row.get("name"))
        if name and not string_value(row.get("connection_id")) and not row.get("__studio_authored_connection_id"):
            row.pop("__studio_authored_connection_id", None)
            row["connection_id"] = name_to_uuid(name)
    for row in sheets.get("dataflows", []):
        name = string_value(row.get("name"))
        if name and not string_value(row.get("dataflow_id")) and not row.get("__studio_authored_dataflow_id"):
            row.pop("__studio_authored_dataflow_id", None)
            row["dataflow_id"] = name_to_uuid(name)


def materialize_default_active_flags(
    sheets: dict[str, list[dict[str, Any]]],
) -> None:
    for sheet_name in REQUIRED_SHEETS:
        for row in sheets.get(sheet_name, []):
            if "is_active" not in row or is_empty(row.get("is_active")):
                row["is_active"] = True
            else:
                row["is_active"] = parse_cell_value(row["is_active"])


def document_sheets_as_rows(
    document: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    sheets = document.get("sheets") or {}
    return {name: sheet_rows(sheets, name) for name in REQUIRED_SHEETS}
