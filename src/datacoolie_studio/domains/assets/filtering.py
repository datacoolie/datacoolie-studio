"""Filtering and sorting rules for Assets read models."""
from __future__ import annotations

from typing import Any

from datacoolie_studio.domains.assets.normalization import (
    asset_position,
    resolution_reason,
    resolution_state as get_resolution_state,
)


def filter_asset_rows(
    rows: list[dict[str, Any]],
    *,
    query: str | None,
    connection: str | None,
    format_name: str | None,
    asset_type: str | None,
    role: str | None,
    attention_state: str | None,
    scope: str | None,
) -> list[dict[str, Any]]:
    needle = (query or "").strip().lower()
    result = []
    for row in rows:
        if connection and row.get("connection_name") != connection:
            continue
        if format_name and row.get("format") != format_name:
            continue
        if asset_type and row.get("asset_type") != asset_type:
            continue
        if role and role not in (row.get("roles") or []):
            continue
        has_attention = int(row.get("attention_count") or 0) > 0
        if attention_state == "with_attention" and not has_attention:
            continue
        if attention_state == "clean" and has_attention:
            continue
        upstream_count = int(row.get("upstream_count") or 0)
        downstream_count = int(row.get("downstream_count") or 0)
        if scope and asset_position(upstream_count, downstream_count) != scope:
            continue
        if needle and needle not in _asset_search_text(row):
            continue
        result.append(row)
    return result

def _asset_search_text(row: dict[str, Any]) -> str:
    values = [
        row.get("id"), row.get("display_name"), row.get("friendly_name"), row.get("full_identity"),
        row.get("asset_type"), row.get("format"), row.get("connection_name"), row.get("catalog"),
        row.get("database"), row.get("schema_name"), row.get("table"), row.get("path"),
        row.get("query"), row.get("python_function"), *(row.get("roles") or []),
        *(source.get("uri") for source in row.get("metadata_sources") or []),
    ]
    values.extend(
        value
        for identifier in row.get("identifiers") or []
        for value in (identifier.get("display_value"), identifier.get("normalized_value"))
    )
    return " ".join(str(value).lower() for value in values if value not in (None, ""))

def sort_asset_rows(rows: list[dict[str, Any]], *, sort_by: str, sort_dir: str) -> list[dict[str, Any]]:
    sort_keys = {
        "display_name": lambda row: str(row.get("display_name") or "").lower(),
        "asset_type": lambda row: str(row.get("asset_type") or "").lower(),
        "connection_name": lambda row: str(row.get("connection_name") or "").lower(),
        "upstream_count": lambda row: int(row.get("upstream_count") or 0),
        "downstream_count": lambda row: int(row.get("downstream_count") or 0),
        "attention_count": lambda row: int(row.get("attention_count") or 0),
    }
    key = sort_keys.get(sort_by)
    if key is None:
        raise ValueError(f"Unsupported asset sort field: {sort_by}")
    ordered = sorted(rows, key=lambda row: str(row.get("id") or ""))
    return sorted(ordered, key=key, reverse=sort_dir == "desc")

def filter_reference_rows(
    rows: list[dict[str, Any]],
    *,
    query: str | None,
    reference_type: str | None,
    provenance: str | None,
    resolution_state: str | None,
    attention_state: str | None,
) -> list[dict[str, Any]]:
    needle = (query or "").strip().lower()
    result = []
    for row in rows:
        if reference_type and row.get("reference_type") != reference_type:
            continue
        if provenance and provenance not in (row.get("provenances") or []):
            continue
        if resolution_state and get_resolution_state(row) != resolution_state:
            continue
        has_attention = int(row.get("attention_count") or 0) > 0
        if attention_state == "with_attention" and not has_attention:
            continue
        if attention_state == "clean" and has_attention:
            continue
        values = [
            row.get("id"), row.get("display_name"), row.get("normalized_value"), row.get("reference_type"),
            get_resolution_state(row), resolution_reason(row), *(row.get("provenances") or []), *(row.get("resolution_methods") or []),
            (row.get("resolved_asset") or {}).get("full_identity"),
            (row.get("resolved_asset") or {}).get("display_name"),
            *(asset.get("full_identity") or asset.get("display_name") for asset in row.get("consumer_assets") or []),
            *(asset.get("full_identity") or asset.get("display_name") for asset in row.get("candidate_assets") or []),
        ]
        if needle and needle not in " ".join(str(value).lower() for value in values if value not in (None, "")):
            continue
        result.append(row)
    return result

def sort_reference_rows(rows: list[dict[str, Any]], *, sort_by: str, sort_dir: str) -> list[dict[str, Any]]:
    sort_keys = {
        "display_name": lambda row: str(row.get("display_name") or "").lower(),
        "reference_type": lambda row: str(row.get("reference_type") or "").lower(),
        "resolution_state": lambda row: get_resolution_state(row),
        "dependency_count": lambda row: int(row.get("dependency_count") or 0),
        "attention_count": lambda row: int(row.get("attention_count") or 0),
    }
    key = sort_keys.get(sort_by)
    if key is None:
        raise ValueError(f"Unsupported reference sort field: {sort_by}")
    ordered = sorted(rows, key=lambda row: str(row.get("id") or ""))
    return sorted(ordered, key=key, reverse=sort_dir == "desc")

def asset_filter_options(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    attention = {("with_attention" if int(item.get("attention_count") or 0) > 0 else "clean") for item in rows}
    return {
        "connections": sorted({item["connection_name"] for item in rows if item.get("connection_name")}),
        "formats": sorted({item["format"] for item in rows if item.get("format")}),
        "asset_types": sorted({item["asset_type"] for item in rows if item.get("asset_type")}),
        "roles": sorted({role for item in rows for role in (item.get("roles") or [])}),
        "attention_states": sorted(attention),
    }

def reference_group_filter_options(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    attention = {("with_attention" if int(item.get("attention_count") or 0) > 0 else "clean") for item in rows}
    return {
        "reference_types": sorted({item["reference_type"] for item in rows if item.get("reference_type")}),
        "provenances": sorted({value for item in rows for value in (item.get("provenances") or []) if value}),
        "resolution_states": sorted({get_resolution_state(item) for item in rows}),
        "attention_states": sorted(attention),
    }

def reference_occurrence_filter_options(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    attention = {("with_attention" if int(item.get("attention_count") or 0) > 0 else "clean") for item in rows}
    return {
        "connections": sorted({item["connection_name"] for item in rows if item.get("connection_name")}),
        "reference_types": sorted({item["reference_type"] for item in rows if item.get("reference_type")}),
        "provenances": sorted({item["provenance"] for item in rows if item.get("provenance")}),
        "resolution_states": sorted({get_resolution_state(item) for item in rows}),
        "resolution_methods": sorted({item["resolution_method"] for item in rows if item.get("resolution_method")}),
        "attention_states": sorted(attention),
    }
__all__ = [
    "asset_filter_options",
    "filter_asset_rows",
    "filter_reference_rows",
    "reference_group_filter_options",
    "reference_occurrence_filter_options",
    "sort_asset_rows",
    "sort_reference_rows",
]
