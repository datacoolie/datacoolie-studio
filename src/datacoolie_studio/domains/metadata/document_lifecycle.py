"""Pure lifecycle normalization applied before metadata persistence."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from datacoolie_studio.core.identity import (
    identity_name,
    name_to_uuid,
    normalize_workspace_id,
)
from datacoolie_studio.domains.metadata.document_primitives import (
    is_empty,
    parse_cell_value,
    sheet_rows,
)
from datacoolie_studio.domains.metadata.document_schema import REQUIRED_SHEETS


def normalize_document_workspace(document: dict[str, Any]) -> dict[str, Any]:
    """Apply the selected workspace inheritance rules to raw metadata.

    A document with exactly one explicit scope supplies that scope to missing
    top-level connections and dataflows. A mixed-scope document must scope all
    top-level entities; missing ownership is rejected before projection. Inline
    connections inherit their flow scope only when they do not already declare
    one; a conflicting inline scope is rejected.
    """

    normalized = deepcopy(document) if isinstance(document, dict) else {}
    connections = normalized.get("connections")
    dataflows = normalized.get("dataflows")
    connections = connections if isinstance(connections, list) else []
    dataflows = dataflows if isinstance(dataflows, list) else []

    scopes: set[str | None] = set()
    for row in (*connections, *dataflows):
        if not isinstance(row, dict):
            continue
        scope = normalize_workspace_id(row.get("workspace_id"))
        inferred_scope = normalize_workspace_id(
            row.get("_identity_inferred_workspace_id")
        )
        if inferred_scope is not None and scope == inferred_scope:
            scopes.add(None)
        else:
            scopes.add(scope)
    explicit_scopes = scopes - {None}
    if len(explicit_scopes) > 1 and None in scopes:
        raise ValueError("Mixed-workspace metadata requires workspace_id on every entity")
    inherited_scope = next(iter(explicit_scopes)) if len(explicit_scopes) == 1 else None

    for row in connections:
        if not isinstance(row, dict):
            continue
        original_scope = normalize_workspace_id(row.get("workspace_id"))
        inferred_scope = normalize_workspace_id(
            row.get("_identity_inferred_workspace_id")
        )
        was_inferred = inferred_scope is not None and original_scope == inferred_scope
        scope = (None if was_inferred else original_scope) or inherited_scope
        if scope is not None or "workspace_id" in row:
            row["workspace_id"] = scope
        if scope is not None and (was_inferred or original_scope is None):
            row["_identity_inferred_workspace_id"] = scope
        elif not was_inferred or scope is None:
            row.pop("_identity_inferred_workspace_id", None)

    for row in dataflows:
        if not isinstance(row, dict):
            continue
        original_scope = normalize_workspace_id(row.get("workspace_id"))
        inferred_scope = normalize_workspace_id(
            row.get("_identity_inferred_workspace_id")
        )
        was_inferred = inferred_scope is not None and original_scope == inferred_scope
        flow_scope = (None if was_inferred else original_scope) or inherited_scope
        if flow_scope is not None or "workspace_id" in row:
            row["workspace_id"] = flow_scope
        if flow_scope is not None and (was_inferred or original_scope is None):
            row["_identity_inferred_workspace_id"] = flow_scope
        elif not was_inferred or flow_scope is None:
            row.pop("_identity_inferred_workspace_id", None)
        for role in ("source", "destination"):
            endpoint = row.get(role)
            if not isinstance(endpoint, dict):
                continue
            inline = endpoint.get("connection")
            if not isinstance(inline, dict):
                continue
            inline_scope = normalize_workspace_id(inline.get("workspace_id"))
            if inline_scope is not None and inline_scope != flow_scope:
                raise ValueError("inline connection workspace does not match its dataflow")
            if flow_scope is not None or "workspace_id" in inline:
                inline["workspace_id"] = flow_scope
            if flow_scope is not None and inline_scope is None:
                inline["_identity_inferred_workspace_id"] = flow_scope
    return normalized


def recompose_workspace_documents(documents: list[dict[str, Any]]) -> None:
    """Reapply workspace inheritance across an environment snapshot.

    Normalization happens once per source, while environment consumers see a
    combined snapshot.  This pass deliberately uses the internal inferred
    marker to distinguish authored scope from a per-source default.  A single
    authored scope can therefore flow across split shards; mixed authored
    scopes leave inferred rows unresolved instead of selecting one silently.
    """

    rows: list[tuple[dict[str, Any], str]] = []
    for document in documents:
        for kind in ("connections", "dataflows"):
            values = document.get(kind, [])
            if not isinstance(values, list):
                continue
            rows.extend(
                (row, kind)
                for row in values
                if isinstance(row, dict)
            )

    explicit_scopes: set[str] = set()
    missing_rows: list[tuple[dict[str, Any], str]] = []
    for row, kind in rows:
        try:
            current_scope = normalize_workspace_id(row.get("workspace_id"))
            inferred_scope = normalize_workspace_id(
                row.get("_identity_inferred_workspace_id")
            )
        except ValueError:
            # Preserve the authored invalid value and prevent a derived ID
            # from making it look valid in an environment projection.
            row["_identity_workspace_error"] = "workspace_id must be a string or null"
            continue
        if inferred_scope is not None and current_scope == inferred_scope:
            missing_rows.append((row, kind))
        elif current_scope is not None:
            explicit_scopes.add(current_scope)
        else:
            missing_rows.append((row, kind))

    scope = next(iter(explicit_scopes)) if len(explicit_scopes) == 1 else None
    mixed = len(explicit_scopes) > 1 and bool(missing_rows)
    for row, kind in missing_rows:
        if mixed:
            row.pop("workspace_id", None)
            row.pop("_identity_inferred_workspace_id", None)
            row["_identity_workspace_error"] = (
                "mixed workspace metadata requires an authored workspace_id"
            )
            continue
        if scope is None:
            row.pop("workspace_id", None)
            row.pop("_identity_inferred_workspace_id", None)
        else:
            row["workspace_id"] = scope
            row["_identity_inferred_workspace_id"] = scope
        row.pop("_identity_workspace_error", None)
        _refresh_generated_identity(row, kind, scope)

    # A consumer may recompose an already normalized document after an
    # authored scope edit. Refresh derived IDs for explicitly scoped rows too;
    # authored IDs have no generated marker and remain authoritative.
    for row, kind in rows:
        if row.get("_identity_workspace_error"):
            continue
        _refresh_generated_identity(row, kind, _safe_workspace(row.get("workspace_id")))

    # An inferred flow scope also supplies the inline connection projection.
    for row, kind in rows:
        if kind != "dataflows":
            continue
        flow_scope = _safe_workspace(row.get("workspace_id"))
        if row.get("_identity_workspace_error"):
            continue
        for role in ("source", "destination"):
            endpoints = [row.get(role)]
            authored = row.get("_identity_authored_endpoints")
            if isinstance(authored, dict):
                endpoints.append(authored.get(role))
            for endpoint in endpoints:
                if not isinstance(endpoint, dict):
                    continue
                inline = endpoint.get("connection")
                if not isinstance(inline, dict):
                    continue
                try:
                    inline_scope = normalize_workspace_id(inline.get("workspace_id"))
                    inferred_inline = normalize_workspace_id(
                        inline.get("_identity_inferred_workspace_id")
                    )
                except ValueError:
                    row["_identity_workspace_error"] = (
                        "workspace_id must be a string or null"
                    )
                    continue
                if inline_scope is None or (
                    inferred_inline is not None and inline_scope == inferred_inline
                ):
                    if flow_scope is None:
                        inline.pop("workspace_id", None)
                        inline.pop("_identity_inferred_workspace_id", None)
                    else:
                        inline["workspace_id"] = flow_scope
                        inline["_identity_inferred_workspace_id"] = flow_scope


def _refresh_generated_identity(
    row: dict[str, Any],
    kind: str,
    workspace_id: str | None,
) -> None:
    field = "connection_id" if kind == "connections" else "dataflow_id"
    marker_key = f"_identity_generated_{field}"
    name = identity_name(row.get("name"))
    marker = row.get(marker_key)
    if not name or not marker:
        return
    row[field] = name_to_uuid(name, workspace_id)


def _safe_workspace(value: Any) -> str | None:
    try:
        return normalize_workspace_id(value)
    except ValueError:
        return None


def normalize_editor_workspace(sheets: dict[str, list[dict[str, Any]]]) -> None:
    """Apply single-scope inheritance to an already materialized editor."""

    connections = sheets.get("connections", [])
    dataflows = sheets.get("dataflows", [])
    valid_rows: list[dict[str, Any]] = []
    inferred_row_ids: set[int] = set()
    for row in (*connections, *dataflows):
        if not isinstance(row, dict):
            continue
        try:
            current_scope = normalize_workspace_id(row.get("workspace_id"))
        except ValueError:
            continue
        valid_rows.append(row)
        try:
            inferred_scope = normalize_workspace_id(
                row.get("__studio_inferred_workspace_id")
            )
        except ValueError:
            inferred_scope = None
        # An unchanged inferred value is evidence of a previous projection,
        # not an authored scope.  Treating it as explicit makes a moved sole
        # connection pin the document to its old workspace forever.
        if inferred_scope is not None and current_scope == inferred_scope:
            inferred_row_ids.add(id(row))
            continue
        if "__studio_inferred_workspace_id" in row:
            row.pop("__studio_inferred_workspace_id", None)

    scopes = {
        scope
        for row in valid_rows
        if id(row) not in inferred_row_ids
        for scope in [normalize_workspace_id(row.get("workspace_id"))]
        if scope is not None
    }
    scope = next(iter(scopes)) if len(scopes) == 1 else None
    for row in valid_rows:
        current_scope = normalize_workspace_id(row.get("workspace_id"))
        if id(row) in inferred_row_ids or current_scope is None:
            if scope is None:
                # A mixed snapshot cannot assign an omitted row to an
                # arbitrary workspace.  Clear the old derived value so the
                # projection remains unresolved until the author scopes it.
                row.pop("workspace_id", None)
                row.pop("__studio_inferred_workspace_id", None)
            else:
                row["workspace_id"] = scope
                row["__studio_inferred_workspace_id"] = scope


def materialize_generated_ids(sheets: dict[str, list[dict[str, Any]]]) -> None:
    normalize_editor_workspace(sheets)
    for row in sheets.get("connections", []):
        name = identity_name(row.get("name"))
        try:
            workspace_id = normalize_workspace_id(row.get("workspace_id"))
        except ValueError:
            continue
        if name and _should_materialize_id(
            row,
            "connection_id",
            "connection",
            name,
            workspace_id,
        ):
            row.pop("__studio_authored_connection_id", None)
            row["connection_id"] = name_to_uuid(name, workspace_id)
            row["__studio_generated_connection_id"] = {
                "id": row["connection_id"],
                "name": name,
                "workspace_id": workspace_id,
            }
    for row in sheets.get("dataflows", []):
        name = identity_name(row.get("name"))
        try:
            workspace_id = normalize_workspace_id(row.get("workspace_id"))
        except ValueError:
            continue
        if name and _should_materialize_id(
            row,
            "dataflow_id",
            "dataflow",
            name,
            workspace_id,
        ):
            row.pop("__studio_authored_dataflow_id", None)
            row["dataflow_id"] = name_to_uuid(name, workspace_id)
            row["__studio_generated_dataflow_id"] = {
                "id": row["dataflow_id"],
                "name": name,
                "workspace_id": workspace_id,
            }


def _should_materialize_id(
    row: dict[str, Any],
    field: str,
    identity_kind: str,
    name: str,
    workspace_id: str | None,
) -> bool:
    """Return whether an ID is absent or is one of our stale generated IDs."""

    current = identity_name(row.get(field))
    if not current:
        return True
    marker = row.get(f"__studio_generated_{identity_kind}_id")
    if not isinstance(marker, dict):
        return False
    previous_id = identity_name(marker.get("id"))
    if previous_id != current:
        # The user replaced a generated value with an explicit ID.
        row.pop(f"__studio_generated_{identity_kind}_id", None)
        row.pop(f"__studio_previous_generated_{identity_kind}_id", None)
        return False
    expected = name_to_uuid(name, workspace_id)
    if current != expected:
        row[f"__studio_previous_generated_{identity_kind}_id"] = current
    return current != expected


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


__all__ = [
    "document_sheets_as_rows",
    "materialize_default_active_flags",
    "materialize_generated_ids",
    "normalize_document_workspace",
    "normalize_editor_workspace",
    "recompose_workspace_documents",
]
