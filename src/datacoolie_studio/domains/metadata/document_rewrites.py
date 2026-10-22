"""Pure metadata document rewrites used by editor workflows."""

from __future__ import annotations

from typing import Any

from datacoolie_studio.core.identity import normalize_workspace_id
from datacoolie_studio.domains.metadata.document_primitives import (
    sheet_rows,
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
    baseline_connections_by_identity = {
        _connection_row_identity(row, index, prefer_previous=False): row
        for index, row in enumerate(baseline_connections)
        if _identity_value(row.get("name"))
    }
    renames: list[dict[str, str | None]] = []
    for index, row in enumerate(next_connections):
        previous = baseline_connections_by_identity.get(
            _connection_row_identity(row, index, prefer_previous=True)
        )
        previous_name = _identity_value(previous.get("name")) if previous else ""
        next_name = _identity_value(row.get("name"))
        if previous_name and next_name and previous_name != next_name:
            renames.append({
                "connection_id": _identity_value(row.get("connection_id"))
                    or _identity_value(previous.get("connection_id")),
                "old_connection_id": _identity_value(previous.get("connection_id")),
                "workspace_id": _workspace(row.get("workspace_id"))
                    or _workspace(previous.get("workspace_id")),
                "old_name": previous_name,
                "new_name": next_name,
            })
    if not renames:
        return document

    sheets = dict(document.get("sheets") or {})
    for sheet_name in ("dataflows", "schema_hints"):
        sheet = sheets.get(sheet_name) or {}
        rows = sheet_rows(sheets, sheet_name)
        sheets[sheet_name] = {
            **sheet,
            "rows": [
                _replace_connection_name_references(
                    row,
                    renames,
                    flow_workspace=_workspace(row.get("workspace_id")),
                )
                for row in rows
            ],
        }
    return {**document, "sheets": sheets}


def _connection_row_identity(
    row: dict[str, Any],
    index: int,
    *,
    prefer_previous: bool,
) -> str:
    previous_generated_id = _identity_value(
        row.get("__studio_previous_generated_connection_id")
    )
    if prefer_previous and previous_generated_id:
        return f"id:{previous_generated_id}"
    connection_id = _identity_value(row.get("connection_id"))
    return f"id:{connection_id}" if connection_id else f"index:{index}"


def _replace_connection_name_references(
    row: dict[str, Any],
    renames: list[dict[str, str | None]],
    *,
    flow_workspace: str | None,
) -> dict[str, Any]:
    updated = dict(row)
    changed = False
    for field in CONNECTION_REFERENCE_FIELDS:
        current_name = _identity_value(row.get(field))
        if not current_name:
            continue
        connection_id = _identity_value(
            row.get(field.replace("_name", "_id"))
        )
        replacement = _matching_rename(
            renames,
            current_name,
            workspace_id=flow_workspace,
            connection_id=connection_id,
        )
        if replacement and replacement != current_name:
            updated[field] = replacement
            changed = True
    if "connection_name" in row:
        current_name = _identity_value(row.get("connection_name"))
        connection_id = _identity_value(row.get("connection_id"))
        replacement = _matching_rename(
            renames,
            current_name,
            workspace_id=_workspace(row.get("workspace_id")),
            connection_id=connection_id,
        )
        if replacement and replacement != current_name:
            updated["connection_name"] = replacement
            changed = True
    return updated if changed else row


def _matching_rename(
    renames: list[dict[str, str | None]],
    current_name: str,
    *,
    workspace_id: str | None,
    connection_id: str | None,
) -> str | None:
    candidates = [
        rename for rename in renames
        if rename.get("old_name") == current_name
        and (
            connection_id
            and connection_id in {
                rename.get("connection_id"),
                rename.get("old_connection_id"),
            }
            or not connection_id
            and rename.get("workspace_id") == workspace_id
        )
    ]
    # A name-only reference with no scope cannot safely cross a workspace
    # boundary.  Preserve it until the editor can carry an explicit ID.
    if len(candidates) != 1:
        return None
    return candidates[0].get("new_name")


def _identity_value(value: Any) -> str:
    return value if isinstance(value, str) and value.strip() else ""


def _workspace(value: Any) -> str | None:
    try:
        return normalize_workspace_id(value)
    except ValueError:
        return None
