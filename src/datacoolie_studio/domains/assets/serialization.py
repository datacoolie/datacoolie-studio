"""Stable output projections for Assets and references."""
from __future__ import annotations

from typing import Any

from datacoolie_studio.domains.assets.mapping_target import asset_mapping_target
from datacoolie_studio.domains.assets.normalization import (
    dependency_reference_id,
    first_string,
    resolution_dict,
    resolution_state,
    resolution_state_rank,
    string_or_none,
    to_int,
)


def asset_flow_row(
    item: dict[str, Any],
    counterpart_asset_id: str,
    asset_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "dataflow_id": str(item.get("dataflow_id") or ""),
        "name": str(item.get("name") or item.get("dataflow_id") or "dataflow"),
        "stage": string_or_none(item.get("stage")),
        "load_type": string_or_none(item.get("load_type")),
        "metadata_source_id": to_int(item.get("metadata_source_id")),
        "metadata_source_uri": string_or_none(item.get("metadata_source_uri")),
        "source_asset_id": string_or_none(item.get("source_asset_id")),
        "destination_asset_id": string_or_none(item.get("destination_asset_id")),
        "counterpart": asset_brief(counterpart_asset_id, asset_by_id),
    }

def asset_neighbor_row(
    neighbor_asset_id: str,
    asset_by_id: dict[str, dict[str, Any]],
    relation_flow_count: int,
    relation_dependency_count: int,
) -> dict[str, Any]:
    relation_kinds: list[str] = []
    if relation_flow_count > 0:
        relation_kinds.append("dataflow")
    if relation_dependency_count > 0:
        relation_kinds.append("dependency")
    return {
        "asset": asset_brief(neighbor_asset_id, asset_by_id),
        "relation_flow_count": relation_flow_count,
        "relation_dependency_count": relation_dependency_count,
        "relation_kinds": relation_kinds,
    }

def depends_on_row(
    item: dict[str, Any],
    resolved_asset_id: str | None,
    reference_id: str | None,
    asset_by_id: dict[str, dict[str, Any]],
    reference_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": str(item.get("id") or ""),
        "kind": str(item.get("kind") or "reads"),
        "provenance": str(item.get("provenance") or "sql"),
        "resolution": resolution_dict(item),
        "resolution_method": str(item.get("resolution_method") or ""),
        "reference_id": reference_id,
        "resolved_asset_id": resolved_asset_id,
        "resolved_asset": None,
        "source_reference": None,
    }
    if resolved_asset_id:
        row["resolved_asset"] = asset_brief(resolved_asset_id, asset_by_id)
    if reference_id:
        row["source_reference"] = reference_brief(reference_id, reference_by_id)
    return row

def used_by_row(
    item: dict[str, Any],
    target_asset_id: str,
    asset_by_id: dict[str, dict[str, Any]],
    reference_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    reference_id = dependency_reference_id(item)
    return {
        "id": str(item.get("id") or ""),
        "kind": str(item.get("kind") or "reads"),
        "provenance": str(item.get("provenance") or "sql"),
        "resolution": resolution_dict(item),
        "resolution_method": str(item.get("resolution_method") or ""),
        "target_asset": asset_brief(target_asset_id, asset_by_id),
        "reference": reference_brief(reference_id, reference_by_id) if reference_id else None,
    }

def reference_brief(reference_id: str, reference_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    reference = reference_by_id.get(reference_id)
    if reference is None:
        return {
            "id": reference_id,
            "display_name": reference_id or "reference",
            "reference_type": "unknown",
            "resolution": {"state": "unresolved", "reason": "no_match"},
            "raw_value": None,
            "provenance": None,
        }
    return {
        "id": reference_id,
        "display_name": str(reference.get("display_name") or reference_id),
        "reference_type": str(reference.get("reference_type") or "unknown"),
        "resolution": resolution_dict(reference),
        "raw_value": string_or_none(reference.get("raw_value")) or string_or_none(reference.get("normalized_value")),
        "provenance": string_or_none(reference.get("provenance")) or first_string(reference.get("provenances") or []),
    }

def asset_brief(asset_id: str, asset_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    asset = asset_by_id.get(asset_id)
    if asset is None:
        return {
            "id": asset_id,
            "display_name": asset_id,
            "friendly_name": asset_id,
            "full_identity": asset_id,
            "asset_type": "unresolved",
            "connection_name": None,
            "format": None,
            "attention_count": 0,
        }
    return {
        "id": asset_id,
        "display_name": str(asset.get("display_name") or asset_id),
        "friendly_name": str(asset.get("friendly_name") or asset.get("display_name") or asset_id),
        "full_identity": str(asset.get("full_identity") or asset.get("display_name") or asset_id),
        "asset_type": str(asset.get("asset_type") or "unresolved"),
        "connection_name": string_or_none(asset.get("connection_name")),
        "format": string_or_none(asset.get("format")),
        "attention_count": int(asset.get("attention_count") or 0),
    }

def asset_neighbor_sort_key(item: dict[str, Any]) -> tuple[int, int, str, str]:
    asset = item.get("asset") or {}
    return (
        -int(item.get("relation_flow_count") or 0),
        -int(asset.get("attention_count") or 0),
        str(asset.get("friendly_name") or "").lower(),
        str(asset.get("id") or ""),
    )

def asset_flow_sort_key(item: dict[str, Any]) -> tuple[str, str, str]:
    counterpart = item.get("counterpart") or {}
    return (
        str(item.get("name") or "").lower(),
        str(counterpart.get("friendly_name") or "").lower(),
        str(item.get("id") or ""),
    )

def depends_on_sort_key(item: dict[str, Any]) -> tuple[int, int, str, str]:
    resolved_asset = item.get("resolved_asset") or {}
    source_reference = item.get("source_reference") or {}
    display_name = (
        str(resolved_asset.get("friendly_name") or "")
        if item.get("resolved_asset_id")
        else str(source_reference.get("display_name") or "")
    ).lower()
    return (
        0 if item.get("resolved_asset_id") else 1,
        resolution_state_rank(resolution_state(item)),
        display_name,
        str(item.get("id") or ""),
    )

def used_by_sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
    target_asset = item.get("target_asset") or {}
    return (
        resolution_state_rank(resolution_state(item)),
        str(target_asset.get("friendly_name") or "").lower(),
        str(item.get("id") or ""),
    )

def compact_asset_row(asset: dict[str, Any]) -> dict[str, Any]:
    excluded = {"attention_items", "identifiers", "metadata_sources", "observations"}
    result = {key: value for key, value in asset.items() if key not in excluded}
    identifiers = list(asset.get("identifiers") or [])
    result.update({
        "identifier_count": len(identifiers),
        "observation_count": len(asset.get("observations") or []),
        "metadata_source_count": len(asset.get("metadata_source_ids") or []),
        "mapping_target": asset_mapping_target(identifiers, asset),
    })
    return result

def compact_reference_row(reference: dict[str, Any]) -> dict[str, Any]:
    excluded = {"attention_items", "observations", "occurrence_ids"}
    result = {key: value for key, value in reference.items() if key not in excluded}
    result["occurrence_count"] = len(reference.get("occurrence_ids") or [])
    return result
__all__ = [
    "asset_brief",
    "asset_flow_row",
    "asset_neighbor_row",
    "asset_neighbor_sort_key",
    "compact_asset_row",
    "compact_reference_row",
    "depends_on_row",
    "depends_on_sort_key",
    "reference_brief",
    "used_by_row",
    "used_by_sort_key",
    "asset_flow_sort_key",
]
