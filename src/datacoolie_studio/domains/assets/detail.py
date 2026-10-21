"""Build detailed asset relationship projections."""
from __future__ import annotations

from collections import Counter
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.assets.definitions import read_asset_definition
from datacoolie_studio.domains.assets.normalization import (
    asset_position,
    dependency_reference_id,
    dependency_resolved_asset_id,
    string_or_none,
)
from datacoolie_studio.domains.assets.serialization import (
    asset_flow_row,
    asset_flow_sort_key,
    asset_neighbor_row,
    asset_neighbor_sort_key,
    depends_on_row,
    depends_on_sort_key,
    used_by_row,
    used_by_sort_key,
)


def build_asset_detail(
    asset_id: str,
    inventory: dict[str, Any],
    lineage: dict[str, Any],
    code_artifacts: list[EnvironmentSource] | None = None,
    *,
    include_definition: bool = True,
) -> dict[str, Any] | None:
    assets = list(inventory.get("assets") or [])
    asset_by_id = {str(item.get("id")): item for item in assets if item.get("id")}
    center = asset_by_id.get(asset_id)
    if center is None:
        return None

    dataflows = list(lineage.get("dataflows") or [])
    dependencies = list(lineage.get("dependencies") or [])
    reference_groups = list(inventory.get("reference_groups") or [])
    reference_by_id = {str(item.get("id")): item for item in reference_groups if item.get("id")}

    upstream_assets: set[str] = set()
    downstream_assets: set[str] = set()
    input_flows: list[dict[str, Any]] = []
    output_flows: list[dict[str, Any]] = []
    depends_on: list[dict[str, Any]] = []
    used_by: list[dict[str, Any]] = []
    input_flow_counts: Counter[str] = Counter()
    output_flow_counts: Counter[str] = Counter()
    depends_on_counts: Counter[str] = Counter()
    used_by_counts: Counter[str] = Counter()
    depends_on_total = 0
    depends_on_mapped_total = 0
    depends_on_unmapped_total = 0
    depends_on_asset_total = 0
    depends_on_reference_total = 0
    used_by_total = 0

    for item in dataflows:
        source_id = string_or_none(item.get("source_asset_id"))
        destination_id = string_or_none(item.get("destination_asset_id"))
        if not source_id or not destination_id:
            continue
        if destination_id == asset_id:
            upstream_assets.add(source_id)
            input_flow_counts[source_id] += 1
            input_flows.append(asset_flow_row(item, source_id, asset_by_id))
        if source_id == asset_id:
            downstream_assets.add(destination_id)
            output_flow_counts[destination_id] += 1
            output_flows.append(asset_flow_row(item, destination_id, asset_by_id))

    for item in dependencies:
        target_id = string_or_none(item.get("target_asset_id"))
        resolved_asset_id = dependency_resolved_asset_id(item)
        reference_id = dependency_reference_id(item)
        if not target_id:
            continue
        if target_id == asset_id:
            depends_on_total += 1
            if resolved_asset_id:
                depends_on_mapped_total += 1
            else:
                depends_on_unmapped_total += 1
            depends_on.append(
                depends_on_row(item, resolved_asset_id, reference_id, asset_by_id, reference_by_id)
            )
            if resolved_asset_id:
                depends_on_asset_total += 1
            else:
                depends_on_reference_total += 1
        if target_id == asset_id:
            if resolved_asset_id:
                upstream_assets.add(resolved_asset_id)
                depends_on_counts[resolved_asset_id] += 1
        if resolved_asset_id != asset_id:
            continue
        downstream_assets.add(target_id)
        used_by_counts[target_id] += 1
        used_by_total += 1
        used_by.append(
            used_by_row(item, target_id, asset_by_id, reference_by_id)
        )

    upstream_neighbors = [
        asset_neighbor_row(
            neighbor_id,
            asset_by_id,
            relation_flow_count=int(input_flow_counts.get(neighbor_id, 0)),
            relation_dependency_count=int(depends_on_counts.get(neighbor_id, 0)),
        )
        for neighbor_id in upstream_assets
    ]
    downstream_neighbors = [
        asset_neighbor_row(
            neighbor_id,
            asset_by_id,
            relation_flow_count=int(output_flow_counts.get(neighbor_id, 0)),
            relation_dependency_count=int(used_by_counts.get(neighbor_id, 0)),
        )
        for neighbor_id in downstream_assets
    ]
    upstream_neighbors.sort(key=asset_neighbor_sort_key)
    downstream_neighbors.sort(key=asset_neighbor_sort_key)
    input_flows.sort(key=asset_flow_sort_key)
    output_flows.sort(key=asset_flow_sort_key)
    depends_on.sort(key=depends_on_sort_key)
    used_by.sort(key=used_by_sort_key)

    direct_relationships = {
        "upstream_assets": len(upstream_assets),
        "downstream_assets": len(downstream_assets),
        "input_flows": len(input_flows),
        "output_flows": len(output_flows),
        "depends_on_count": int(depends_on_total),
        "depends_on_total": int(depends_on_total),
        "depends_on_mapped_count": int(depends_on_mapped_total),
        "depends_on_unmapped_count": int(depends_on_unmapped_total),
        "depends_on_asset_count": int(depends_on_asset_total),
        "depends_on_reference_count": int(depends_on_reference_total),
        "used_by_count": int(used_by_total),
        "used_by_total": int(used_by_total),
        "position": asset_position(len(upstream_assets), len(downstream_assets)),
    }
    return {
        "asset": center,
        "definition": read_asset_definition(center, code_artifacts or []) if include_definition else None,
        "attention_items": list(center.get("attention_items") or []),
        "direct_relationships": direct_relationships,
        "upstream_assets": upstream_neighbors,
        "downstream_assets": downstream_neighbors,
        "input_flows": input_flows,
        "output_flows": output_flows,
        "depends_on": depends_on,
        "used_by": used_by,
    }
__all__ = ["build_asset_detail"]
