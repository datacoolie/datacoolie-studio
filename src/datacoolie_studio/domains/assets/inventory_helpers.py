"""Context and ordering helpers for the Assets inventory projection."""
from __future__ import annotations

from typing import Any

from datacoolie_studio.domains.assets.normalization import (
    dependency_reference_id,
    dependency_reference_occurrence_id,
    string_or_none,
    to_int,
)


def asset_inventory_sort_key(item: dict[str, Any]) -> tuple[str, str, str, str, str, str, str]:
    asset_name = (
        string_or_none(item.get("table"))
        or string_or_none(item.get("path"))
        or string_or_none(item.get("python_function"))
        or string_or_none(item.get("query"))
        or string_or_none(item.get("friendly_name"))
        or string_or_none(item.get("display_name"))
        or ""
    )
    return (
        str(item.get("connection_name") or "").lower(),
        str(item.get("catalog") or "").lower(),
        str(item.get("database") or "").lower(),
        str(item.get("schema_name") or "").lower(),
        str(item.get("asset_type") or "").lower(),
        asset_name.lower(),
        str(item.get("id") or ""),
    )

def reference_dataflow_ids(
    occurrence: dict[str, Any],
    dependencies: list[dict[str, Any]],
    dataflows: list[dict[str, Any]],
) -> list[str]:
    occurrence_id = string_or_none(occurrence.get("id"))
    reference_id = string_or_none(occurrence.get("reference_id"))
    if not occurrence_id and not reference_id:
        return []
    matching_dependencies = [
        item
        for item in dependencies
        if dependency_reference_occurrence_id(item) == occurrence_id
        or dependency_reference_id(item) == reference_id
    ]
    consumer_asset_ids = {
        asset_id
        for item in matching_dependencies
        for asset_id in [
            string_or_none(item.get("consumer_asset_id")) or string_or_none(item.get("target_asset_id")),
        ]
        if asset_id
    }
    consumer_asset_ids.update({
        asset_id
        for asset_id in [
            string_or_none(occurrence.get("consumer_asset_id")) or string_or_none(occurrence.get("target_asset_id")),
        ]
        if asset_id
    })
    values = {
        dataflow_id
        for item in matching_dependencies
        for observation in (item.get("observations") or [])
        for dataflow_id in [string_or_none(observation.get("dataflow_id"))]
        if dataflow_id
    }
    values.update(
        string_or_none(dataflow.get("dataflow_id"))
        for dataflow in dataflows
        if string_or_none(dataflow.get("source_asset_id")) in consumer_asset_ids
        and string_or_none(dataflow.get("dataflow_id"))
    )
    return sorted(values)

def metadata_source_uri_by_id(metadata: dict[str, Any], lineage: dict[str, Any]) -> dict[int, str]:
    values: dict[int, str] = {}
    for source in metadata.get("sources") or []:
        source_id = to_int(source.get("id"))
        source_uri = string_or_none(source.get("uri"))
        if source_id is not None and source_uri:
            values[source_id] = source_uri
    for asset in lineage.get("assets") or []:
        for observation in asset.get("observations") or []:
            source_id = to_int(observation.get("metadata_source_id"))
            source_uri = string_or_none(observation.get("metadata_source_uri"))
            if source_id is not None and source_uri and source_id not in values:
                values[source_id] = source_uri
    return values

def metadata_sources_for_asset(
    asset: dict[str, Any],
    metadata_source_ids: list[int],
    metadata_source_uri_by_id: dict[int, str],
) -> list[dict[str, Any]]:
    values: dict[int, str] = {}
    for source_id in metadata_source_ids:
        uri = metadata_source_uri_by_id.get(source_id)
        if uri:
            values[source_id] = uri
    for observation in asset.get("observations") or []:
        source_id = to_int(observation.get("metadata_source_id"))
        source_uri = string_or_none(observation.get("metadata_source_uri"))
        if source_id is not None and source_uri:
            values[source_id] = source_uri
    return [
        {"id": source_id, "uri": values[source_id]}
        for source_id in sorted(values)
    ]

__all__ = [
    "asset_inventory_sort_key",
    "metadata_source_uri_by_id",
    "metadata_sources_for_asset",
    "reference_dataflow_ids",
]
