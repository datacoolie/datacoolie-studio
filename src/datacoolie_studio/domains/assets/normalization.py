"""Shared normalization and identity rules for asset projections."""
from __future__ import annotations

from typing import Any


def dependency_resolved_asset_id(item: dict[str, Any]) -> str | None:
    return string_or_none(item.get("resolved_asset_id"))

def dependency_reference_id(item: dict[str, Any]) -> str | None:
    return string_or_none(item.get("reference_id"))

def dependency_reference_occurrence_id(item: dict[str, Any]) -> str | None:
    return string_or_none(item.get("reference_occurrence_id"))

def resolution_state_rank(value: str) -> int:
    return {"unresolved": 0, "manual": 1, "automatic": 2}.get(value, 3)

def resolution_dict(item: dict[str, Any]) -> dict[str, str | None]:
    resolution = item.get("resolution")
    if not isinstance(resolution, dict):
        return {"state": "unresolved", "reason": "no_match"}
    state = str(resolution.get("state") or "unresolved")
    if state not in {"automatic", "manual", "unresolved"}:
        state = "unresolved"
    reason = string_or_none(resolution.get("reason")) if state == "unresolved" else None
    return {"state": state, "reason": reason}

def resolution_state(item: dict[str, Any]) -> str:
    return str(resolution_dict(item)["state"])

def resolution_reason(item: dict[str, Any]) -> str | None:
    return resolution_dict(item)["reason"]

def asset_position(upstream_count: int, downstream_count: int) -> str:
    if upstream_count == 0 and downstream_count > 0:
        return "entry"
    if upstream_count > 0 and downstream_count > 0:
        return "transit"
    if upstream_count > 0 and downstream_count == 0:
        return "exit"
    return "isolated"

def asset_roles(asset: dict[str, Any], *, input_count: int, output_count: int) -> list[str]:
    roles = {str(role) for role in (asset.get("roles") or []) if str(role)}
    if output_count > 0:
        roles.add("source")
    if input_count > 0:
        roles.add("destination")
    return sorted(roles)

def qualified_table(asset: dict[str, Any]) -> str | None:
    table = string_or_none(asset.get("table"))
    if not table:
        return None
    parts = [
        string_or_none(asset.get("catalog")),
        string_or_none(asset.get("database")),
        string_or_none(asset.get("schema_name")),
        table,
    ]
    values = [value for value in parts if value]
    return ".".join(values) if values else None

def full_identity(asset: dict[str, Any], display_name: str, connection_name: str | None) -> str:
    locator = (
        string_or_none(asset.get("query"))
        or string_or_none(asset.get("python_function"))
        or string_or_none(asset.get("path"))
        or qualified_table(asset)
        or display_name
    )
    connection = connection_name or "unknown connection"
    return " · ".join(part for part in (connection, locator) if part)

def friendly_name(asset: dict[str, Any], display_name: str) -> str:
    alias = string_or_none(asset.get("table"))
    python_function = string_or_none(asset.get("python_function"))
    if python_function:
        if alias:
            return alias
        return python_function.split(".")[-1] or python_function
    if string_or_none(asset.get("query")):
        if alias:
            return alias
        return "SQL query"
    table = alias
    if table:
        return table
    path = string_or_none(asset.get("path"))
    if path:
        normalized = path.replace("\\", "/").rstrip("/")
        parts = [part for part in normalized.split("/") if part]
        if len(parts) >= 3:
            return "/".join(parts[-3:])
        if parts:
            return "/".join(parts)
        return normalized or display_name
    return display_name

def first_string(values: list[Any]) -> str | None:
    for value in values:
        text = string_or_none(value)
        if text:
            return text
    return None

def string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None

def to_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None

__all__ = [
    "asset_position",
    "asset_roles",
    "dependency_reference_id",
    "dependency_reference_occurrence_id",
    "dependency_resolved_asset_id",
    "first_string",
    "friendly_name",
    "full_identity",
    "qualified_table",
    "resolution_dict",
    "resolution_reason",
    "resolution_state",
    "resolution_state_rank",
    "string_or_none",
    "to_int",
]
