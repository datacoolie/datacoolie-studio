"""Pure primitives shared by metadata document transformations."""

from __future__ import annotations

import json
from typing import Any


def sheet_rows(sheets: dict[str, Any], sheet_name: str) -> list[dict[str, Any]]:
    sheet = sheets.get(sheet_name) or {}
    rows = sheet.get("rows") if isinstance(sheet, dict) else []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def parse_cell_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.lower() == "true":
        return True
    if stripped.lower() == "false":
        return False
    if stripped.startswith(("{", "[")):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return value
    return value


def is_empty(value: Any) -> bool:
    return value is None or value == ""


def string_value(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def validation_issue(
    severity: str,
    sheet: str,
    row_index: int,
    column: str,
    message: str,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "sheet": sheet,
        "row_index": row_index,
        "column": column,
        "message": message,
    }
