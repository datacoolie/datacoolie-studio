"""Build the canonical Assets inventory read model."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from datacoolie_studio.domains.assets.attention import (
    append_asset_attention,
    attention_from_diagnostic,
    build_reference_attention,
    is_reference_resolution_diagnostic,
    reference_attention_source_type,
    severity_rank,
)
from datacoolie_studio.domains.assets.filtering import (
    asset_filter_options,
    reference_group_filter_options,
    reference_occurrence_filter_options,
)
from datacoolie_studio.domains.assets.inventory_helpers import (
    asset_inventory_sort_key,
    metadata_sources_for_asset,
    reference_dataflow_ids,
)
from datacoolie_studio.domains.assets.manual_mapping import manual_mapping_from_observations
from datacoolie_studio.domains.assets.models import AssetAttention
from datacoolie_studio.domains.assets.normalization import (
    asset_roles,
    dependency_reference_id,
    dependency_reference_occurrence_id,
    dependency_resolved_asset_id,
    first_string,
    full_identity,
    friendly_name,
    resolution_dict,
    resolution_reason,
    resolution_state,
    resolution_state_rank,
    string_or_none,
    to_int,
)
from datacoolie_studio.domains.assets.serialization import asset_brief


def build_assets_inventory(
    lineage: dict[str, Any],
    metadata_source_uri_by_id: dict[int, str],
) -> dict[str, Any]:
    assets = list(lineage.get("assets") or [])
    dataflows = list(lineage.get("dataflows") or [])
    dependencies = list(lineage.get("dependencies") or [])
    reference_groups = list(lineage.get("references") or [])
    reference_occurrences = list(lineage.get("reference_occurrences") or [])
    diagnostics = list(lineage.get("diagnostics") or [])

    input_dataflow_counts = Counter(str(item.get("destination_asset_id")) for item in dataflows)
    output_dataflow_counts = Counter(str(item.get("source_asset_id")) for item in dataflows)
    depends_on_counts = Counter(str(item.get("target_asset_id")) for item in dependencies)
    used_by_counts = Counter(
        resolved_asset_id
        for item in dependencies
        for resolved_asset_id in [dependency_resolved_asset_id(item)]
        if resolved_asset_id
    )
    dependency_counts_by_reference = Counter(
        reference_id
        for item in dependencies
        for reference_id in [dependency_reference_id(item)]
        if reference_id
    )
    dependency_counts_by_occurrence = Counter(
        occurrence_id
        for item in dependencies
        for occurrence_id in [dependency_reference_occurrence_id(item)]
        if occurrence_id
    )

    upstream_assets: dict[str, set[str]] = defaultdict(set)
    downstream_assets: dict[str, set[str]] = defaultdict(set)
    dataflow_asset_links: dict[str, set[str]] = defaultdict(set)
    for dataflow in dataflows:
        source_id = str(dataflow.get("source_asset_id"))
        destination_id = str(dataflow.get("destination_asset_id"))
        dataflow_id = str(dataflow.get("dataflow_id") or "")
        if source_id and destination_id:
            upstream_assets[destination_id].add(source_id)
            downstream_assets[source_id].add(destination_id)
        if dataflow_id:
            dataflow_asset_links[dataflow_id].update({source_id, destination_id})

    for dependency in dependencies:
        source_id = dependency_resolved_asset_id(dependency)
        target_id = str(dependency.get("target_asset_id") or "")
        if source_id and target_id:
            upstream_assets[target_id].add(source_id)
            downstream_assets[source_id].add(target_id)

    attention_by_asset: dict[str, list[AssetAttention]] = defaultdict(list)
    attention_keys_by_asset: dict[str, set[tuple[str, str, str | None, str | None, str | None]]] = defaultdict(set)
    for diagnostic in diagnostics:
        if is_reference_resolution_diagnostic(diagnostic):
            continue
        attention = attention_from_diagnostic(diagnostic)
        asset_id = string_or_none(diagnostic.get("asset_id"))
        if asset_id:
            append_asset_attention(attention_by_asset, attention_keys_by_asset, asset_id, attention)
            continue
        dataflow_id = string_or_none(diagnostic.get("dataflow_id"))
        if dataflow_id:
            for related_asset_id in dataflow_asset_links.get(dataflow_id, set()):
                if related_asset_id:
                    append_asset_attention(attention_by_asset, attention_keys_by_asset, related_asset_id, attention)

    for reference in reference_occurrences:
        reference_resolution_state = resolution_state(reference)
        if reference_resolution_state != "unresolved":
            continue
        reference_resolution_reason = resolution_reason(reference)
        reference_occurrence_id = string_or_none(reference.get("id"))
        reference_id = string_or_none(reference.get("reference_id"))
        reference_message = str(reference.get("raw_value") or reference.get("display_name") or reference_occurrence_id or "reference")
        reference_attention = AssetAttention(
            severity="warning" if reference_resolution_reason in {"multiple_matches", "conflicting_targets", "target_missing"} else "info",
            code=f"reference_{reference_resolution_reason or 'unresolved'}",
            message=f"{(reference_resolution_reason or 'unresolved').replace('_', ' ')} reference: {reference_message}",
            source_type=reference_attention_source_type(reference),
            subject_type="reference",
            dataflow_id=string_or_none(reference.get("dataflow_id")),
            reference_id=reference_id,
            reference_occurrence_id=reference_occurrence_id,
            details={"candidate_asset_ids": list(reference.get("candidate_asset_ids") or [])},
        )
        target_asset_id = string_or_none(reference.get("target_asset_id"))
        if target_asset_id:
            append_asset_attention(attention_by_asset, attention_keys_by_asset, target_asset_id, reference_attention)
        for candidate_asset_id in reference.get("candidate_asset_ids") or []:
            candidate_id = string_or_none(candidate_asset_id)
            if candidate_id:
                append_asset_attention(attention_by_asset, attention_keys_by_asset, candidate_id, reference_attention)

    asset_rows: list[dict[str, Any]] = []
    for asset in assets:
        asset_id = str(asset.get("id"))
        if not asset_id:
            continue
        metadata_source_ids = sorted({
            source_id
            for source_id in (to_int(value) for value in (asset.get("metadata_source_ids") or []))
            if source_id is not None
        })
        metadata_sources = metadata_sources_for_asset(asset, metadata_source_ids, metadata_source_uri_by_id)
        observations = list(asset.get("observations") or [])
        attention_items = [item.to_dict() for item in attention_by_asset.get(asset_id, [])]
        attention_items.sort(key=lambda item: (severity_rank(item["severity"]), item["source_type"], item["code"], item["message"]))
        display_name = str(asset.get("display_name") or asset.get("label") or asset_id)
        connection_name = string_or_none(asset.get("connection_name")) or first_string(asset.get("connection_names") or [])
        asset_type = str(asset.get("asset_type") or "unresolved")
        format_value = string_or_none(asset.get("format"))
        input_count = int(input_dataflow_counts.get(asset_id, 0))
        output_count = int(output_dataflow_counts.get(asset_id, 0))
        roles = asset_roles(asset, input_count=input_count, output_count=output_count)
        asset_full_identity = full_identity(asset, display_name, connection_name)
        asset_rows.append({
            "id": asset_id,
            "display_name": display_name,
            "friendly_name": friendly_name(asset, display_name),
            "full_identity": asset_full_identity,
            "asset_type": asset_type,
            "format": format_value,
            "connection_name": connection_name,
            "connection_type": string_or_none(asset.get("connection_type")),
            "database_type": string_or_none(asset.get("database_type")),
            "catalog": string_or_none(asset.get("catalog")),
            "database": string_or_none(asset.get("database")),
            "schema_name": string_or_none(asset.get("schema_name")),
            "table": string_or_none(asset.get("table")),
            "path": string_or_none(asset.get("path")),
            "query": string_or_none(asset.get("query")),
            "python_function": string_or_none(asset.get("python_function")),
            "roles": roles,
            "metadata_source_ids": metadata_source_ids,
            "metadata_sources": metadata_sources,
            "upstream_count": len(upstream_assets.get(asset_id, set())),
            "downstream_count": len(downstream_assets.get(asset_id, set())),
            "input_dataflow_count": input_count,
            "output_dataflow_count": output_count,
            "depends_on_count": int(depends_on_counts.get(asset_id, 0)),
            "used_by_count": int(used_by_counts.get(asset_id, 0)),
            "attention_count": len(attention_items),
            "attention_items": attention_items,
            "identifiers": list(asset.get("identifiers") or []),
            "observations": observations,
        })

    asset_by_id = {str(item["id"]): item for item in asset_rows}
    occurrence_rows: list[dict[str, Any]] = []
    for occurrence in reference_occurrences:
        occurrence_id = str(occurrence.get("id") or "")
        if not occurrence_id:
            continue
        reference_id = string_or_none(occurrence.get("reference_id"))
        consumer_asset_id = string_or_none(occurrence.get("consumer_asset_id")) or string_or_none(occurrence.get("target_asset_id"))
        consumer_asset = asset_by_id.get(consumer_asset_id or "")
        resolved_asset_id = string_or_none(occurrence.get("resolved_asset_id"))
        candidate_asset_ids = [
            candidate_id
            for candidate_id in (string_or_none(value) for value in (occurrence.get("candidate_asset_ids") or []))
            if candidate_id
        ]
        resolution = resolution_dict(occurrence)
        observations = list(occurrence.get("observations") or [])
        reference_attention = build_reference_attention(occurrence)
        attention_items = [reference_attention] if reference_attention is not None else []
        raw_value = string_or_none(occurrence.get("raw_value")) or str(occurrence.get("display_name") or occurrence_id)
        display_name = str(occurrence.get("display_name") or raw_value)
        manual_mapping = manual_mapping_from_observations(observations)
        occurrence_rows.append({
            "id": occurrence_id,
            "reference_id": reference_id,
            "reference_type": str(occurrence.get("reference_type") or "unknown"),
            "raw_value": raw_value,
            "normalized_value": string_or_none(occurrence.get("normalized_value")) or raw_value,
            "context_scope": string_or_none(occurrence.get("context_scope")),
            "context_scope_source": string_or_none(occurrence.get("context_scope_source")),
            "addressing_mode": string_or_none(occurrence.get("addressing_mode")),
            "qualification_level": string_or_none(occurrence.get("qualification_level")),
            "source_location": occurrence.get("source_location"),
            "display_name": display_name,
            "provenance": string_or_none(occurrence.get("provenance")),
            "consumer_asset_id": consumer_asset_id,
            "consumer_asset": asset_brief(consumer_asset_id, asset_by_id) if consumer_asset_id else None,
            "connection_name": consumer_asset.get("connection_name") if consumer_asset else None,
            "resolution": resolution,
            "resolution_method": string_or_none(occurrence.get("resolution_method")),
            "resolved_asset_id": resolved_asset_id,
            "resolved_asset": asset_brief(resolved_asset_id, asset_by_id) if resolved_asset_id else None,
            "candidate_asset_ids": candidate_asset_ids,
            "candidate_assets": [asset_brief(candidate_id, asset_by_id) for candidate_id in candidate_asset_ids],
            "dependency_count": int(dependency_counts_by_occurrence.get(occurrence_id, 0)),
            "dataflow_ids": reference_dataflow_ids(occurrence, dependencies, dataflows),
            "attention_count": len(attention_items),
            "attention_items": [item.to_dict() for item in attention_items],
            "observations": observations,
            "manual_mapping": manual_mapping,
        })

    occurrence_by_id = {str(item["id"]): item for item in occurrence_rows}
    reference_group_rows: list[dict[str, Any]] = []
    for reference in reference_groups:
        reference_id = str(reference.get("id") or "")
        if not reference_id:
            continue
        occurrence_ids = [
            occurrence_id
            for occurrence_id in (string_or_none(value) for value in (reference.get("occurrence_ids") or []))
            if occurrence_id
        ]
        group_occurrences = [occurrence_by_id[occurrence_id] for occurrence_id in occurrence_ids if occurrence_id in occurrence_by_id]
        resolved_asset_ids = [
            asset_id
            for asset_id in (string_or_none(value) for value in (reference.get("resolved_asset_ids") or []))
            if asset_id
        ]
        resolved_asset_id = string_or_none(reference.get("resolved_asset_id"))
        candidate_asset_ids = [
            candidate_id
            for candidate_id in (string_or_none(value) for value in (reference.get("candidate_asset_ids") or []))
            if candidate_id
        ]
        attention_items = [
            attention
            for occurrence in group_occurrences
            for attention in (occurrence.get("attention_items") or [])
        ]
        manual_mapping = next((occurrence.get("manual_mapping") for occurrence in group_occurrences if occurrence.get("manual_mapping")), None)
        consumer_asset_ids = sorted({
            asset_id
            for occurrence in group_occurrences
            for asset_id in [string_or_none(occurrence.get("consumer_asset_id"))]
            if asset_id
        } or {
            asset_id
            for asset_id in (string_or_none(value) for value in (reference.get("consumer_asset_ids") or []))
            if asset_id
        })
        reference_group_rows.append({
            "id": reference_id,
            "reference_type": str(reference.get("reference_type") or "unknown"),
            "normalized_value": string_or_none(reference.get("normalized_value")) or str(reference.get("display_name") or reference_id),
            "display_name": str(reference.get("display_name") or reference.get("normalized_value") or reference_id),
            "resolution": resolution_dict(reference),
            "resolved_asset_id": resolved_asset_id,
            "resolved_asset_ids": resolved_asset_ids,
            "resolved_asset": asset_brief(resolved_asset_id, asset_by_id) if resolved_asset_id else None,
            "candidate_asset_ids": candidate_asset_ids,
            "candidate_assets": [asset_brief(candidate_id, asset_by_id) for candidate_id in candidate_asset_ids],
            "occurrence_ids": occurrence_ids,
            "consumer_asset_ids": consumer_asset_ids,
            "consumer_assets": [asset_brief(asset_id, asset_by_id) for asset_id in consumer_asset_ids],
            "provenances": sorted({
                value
                for occurrence in group_occurrences
                for value in [string_or_none(occurrence.get("provenance"))]
                if value
            } or {str(value) for value in (reference.get("provenances") or []) if str(value)}),
            "resolution_methods": sorted({
                str(occurrence.get("resolution_method"))
                for occurrence in group_occurrences
                if occurrence.get("resolution_method")
            }),
            "dependency_count": int(dependency_counts_by_reference.get(reference_id, 0)),
            "dataflow_ids": sorted({
                dataflow_id
                for occurrence in group_occurrences
                for dataflow_id in (occurrence.get("dataflow_ids") or [])
            }),
            "attention_count": len(attention_items),
            "attention_items": attention_items,
            "observations": list(reference.get("observations") or []),
            "manual_mapping": manual_mapping,
        })

    asset_rows.sort(key=asset_inventory_sort_key)
    occurrence_rows.sort(key=lambda item: (
        resolution_state_rank(resolution_state(item)),
        str(item.get("connection_name") or "").lower(),
        str(item.get("display_name") or "").lower(),
        str(item.get("id") or ""),
    ))
    reference_group_rows.sort(key=lambda item: (
        resolution_state_rank(resolution_state(item)),
        str(item.get("display_name") or "").lower(),
        str(item.get("id") or ""),
    ))

    mapped_reference_count = sum(
        1
        for item in occurrence_rows
        if item.get("manual_mapping")
    )
    filter_options = {
        "assets": asset_filter_options(asset_rows),
        "reference_groups": reference_group_filter_options(reference_group_rows),
        "reference_occurrences": reference_occurrence_filter_options(occurrence_rows),
    }
    summary = {
        "assets": len(asset_rows),
        "references": len(reference_group_rows),
        "manual_mappings": mapped_reference_count,
        "visible": len(asset_rows) + len(reference_group_rows),
        "asset_attention": sum(1 for item in asset_rows if item["attention_count"] > 0),
        "with_attention": sum(1 for item in asset_rows if item["attention_count"] > 0),
        "automatic_references": sum(1 for item in reference_group_rows if resolution_state(item) == "automatic"),
        "manual_references": sum(1 for item in reference_group_rows if resolution_state(item) == "manual"),
        "unresolved_references": sum(1 for item in reference_group_rows if resolution_state(item) == "unresolved"),
    }
    return {
        "summary": summary,
        "assets": asset_rows,
        "reference_groups": reference_group_rows,
        "reference_occurrences": occurrence_rows,
        "filter_options": filter_options,
        "diagnostics": diagnostics,
    }

__all__ = ["build_assets_inventory"]
