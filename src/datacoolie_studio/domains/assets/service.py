from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlglot import errors as sqlglot_errors, parse

from datacoolie_studio.db.models import Environment, EnvironmentSource
from datacoolie_studio.domains.code_artifacts.indexer import ArtifactIndexError
from datacoolie_studio.domains.assets.manual_mapping import manual_mapping_from_observations
from datacoolie_studio.domains.assets.mapping_target import asset_mapping_target
from datacoolie_studio.domains.assets.project_reference_registry import build_project_reference_registry
from datacoolie_studio.domains.code_artifacts.service import (
    extract_python_function_source,
    read_code_artifact_function_source,
)
from datacoolie_studio.domains.assets.reference_source import build_reference_occurrence_source
from datacoolie_studio.domains.lineage.service import lineage_input_fingerprint, load_or_build_lineage_graph
from datacoolie_studio.domains.read_models.cache import (
    cached_read_model,
    empty_parameters_fingerprint,
    read_model_build_lock,
    read_model_generation,
    replace_read_model,
)
from datacoolie_studio.domains.read_models.keys import ASSETS_CATALOG
from datacoolie_studio.domains.workspace import service as workspace


ASSETS_PROJECTOR_VERSION = "assets-catalog-v3-reference-resolution"


@dataclass(frozen=True)
class AssetsCatalog:
    payload: dict[str, Any]
    input_fingerprint: str


@dataclass(frozen=True)
class AssetAttention:
    severity: str
    code: str
    message: str
    source_type: str
    subject_type: str = "asset"
    dataflow_id: str | None = None
    metadata_source_id: int | None = None
    reference_id: str | None = None
    reference_occurrence_id: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "source_type": self.source_type,
            "subject_type": self.subject_type,
            "dataflow_id": self.dataflow_id,
            "metadata_source_id": self.metadata_source_id,
            "reference_id": self.reference_id,
            "reference_occurrence_id": self.reference_occurrence_id,
            "details": self.details or {},
        }


def list_project_reference_registry(session: Session, project_id: int) -> dict[str, Any]:
    mappings = workspace.list_project_reference_mappings(session, project_id)
    environments = list(session.scalars(
        select(Environment)
        .where(Environment.project_id == project_id)
        .order_by(Environment.name, Environment.id)
    ))
    snapshots: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for environment in environments:
        try:
            catalog = load_or_build_assets_catalog(session, environment.id)
            assets = _sort_asset_rows(list(catalog.payload.get("assets") or []), sort_by="display_name", sort_dir="asc")
            references = _sort_reference_rows(
                list(catalog.payload.get("reference_groups") or []),
                sort_by="display_name",
                sort_dir="asc",
            )
            snapshots.append({
                "environment": {"id": environment.id, "name": environment.name},
                "assets": [_compact_asset_row(item) for item in assets],
                "reference_groups": [_compact_reference_row(item) for item in references],
                "catalog_version": catalog.input_fingerprint,
            })
        except Exception as exc:  # Keep one broken Environment from hiding the rest of the Project registry.
            failures.append({
                "environment_id": environment.id,
                "environment_name": environment.name,
                "message": str(exc) or "The environment asset registry could not be loaded.",
            })
    registry = build_project_reference_registry(snapshots, mappings)
    return {
        "project_id": project_id,
        "mappings": mappings,
        **registry,
        "failures": failures,
    }


def list_environment_assets(
    session: Session,
    environment_id: int,
    *,
    query: str | None = None,
    connection: str | None = None,
    format_name: str | None = None,
    asset_type: str | None = None,
    role: str | None = None,
    attention_state: str | None = None,
    scope: str | None = None,
    sort_by: str = "display_name",
    sort_dir: str = "asc",
    input_fingerprint: str | None = None,
) -> dict[str, Any]:
    catalog = load_or_build_assets_catalog(session, environment_id, input_fingerprint=input_fingerprint)
    rows = _filter_asset_rows(
        list(catalog.payload.get("assets") or []),
        query=query,
        connection=connection,
        format_name=format_name,
        asset_type=asset_type,
        role=role,
        attention_state=attention_state,
        scope=scope,
    )
    rows = _sort_asset_rows(rows, sort_by=sort_by, sort_dir=sort_dir)
    return {
        "summary": catalog.payload.get("summary") or {},
        "items": [_compact_asset_row(item) for item in rows],
        "filter_options": (catalog.payload.get("filter_options") or {}).get("assets") or {},
        "catalog_version": catalog.input_fingerprint,
    }


def list_environment_asset_references(
    session: Session,
    environment_id: int,
    *,
    query: str | None = None,
    reference_type: str | None = None,
    provenance: str | None = None,
    resolution_state: str | None = None,
    attention_state: str | None = None,
    sort_by: str = "display_name",
    sort_dir: str = "asc",
    input_fingerprint: str | None = None,
) -> dict[str, Any]:
    catalog = load_or_build_assets_catalog(session, environment_id, input_fingerprint=input_fingerprint)
    rows = _filter_reference_rows(
        list(catalog.payload.get("reference_groups") or []),
        query=query,
        reference_type=reference_type,
        provenance=provenance,
        resolution_state=resolution_state,
        attention_state=attention_state,
    )
    rows = _sort_reference_rows(rows, sort_by=sort_by, sort_dir=sort_dir)
    return {
        "items": [_compact_reference_row(item) for item in rows],
        "filter_options": (catalog.payload.get("filter_options") or {}).get("reference_groups") or {},
        "catalog_version": catalog.input_fingerprint,
    }


def get_environment_asset_reference(
    session: Session,
    environment_id: int,
    reference_id: str,
    *,
    input_fingerprint: str | None = None,
) -> dict[str, Any] | None:
    catalog = load_or_build_assets_catalog(session, environment_id, input_fingerprint=input_fingerprint)
    reference = next(
        (item for item in catalog.payload.get("reference_groups") or [] if str(item.get("id") or "") == reference_id),
        None,
    )
    if reference is None:
        return None
    return {
        "reference": reference,
        "occurrences": [
            item
            for item in catalog.payload.get("reference_occurrences") or []
            if str(item.get("reference_id") or "") == reference_id
        ],
        "catalog_version": catalog.input_fingerprint,
    }


def get_environment_asset(
    session: Session,
    environment_id: int,
    asset_id: str,
    *,
    input_fingerprint: str | None = None,
) -> dict[str, Any] | None:
    catalog = load_or_build_assets_catalog(session, environment_id, input_fingerprint=input_fingerprint)
    lineage = load_or_build_lineage_graph(session, environment_id, input_fingerprint=input_fingerprint)
    detail = build_asset_detail(asset_id, catalog.payload, lineage, include_definition=False)
    if detail is not None:
        detail["definition"] = _asset_definition_descriptor(detail["asset"])
    return detail


def get_environment_asset_source(session: Session, environment_id: int, asset_id: str) -> dict[str, Any] | None:
    catalog = load_or_build_assets_catalog(session, environment_id)
    asset = next(
        (item for item in catalog.payload.get("assets") or [] if str(item.get("id") or "") == asset_id),
        None,
    )
    if asset is None:
        return None
    definition = _asset_definition(asset, workspace.list_code_artifacts(session, environment_id))
    if definition is None:
        return None
    return {"definition": definition, "catalog_version": catalog.input_fingerprint}


def get_reference_occurrence_source(
    session: Session,
    environment_id: int,
    occurrence_id: str,
) -> dict[str, Any] | None:
    catalog = load_or_build_assets_catalog(session, environment_id)
    code_artifacts = workspace.list_code_artifacts(session, environment_id)
    occurrence = next(
        (item for item in catalog.payload.get("reference_occurrences") or [] if str(item.get("id") or "") == occurrence_id),
        None,
    )
    if occurrence is None:
        return None
    consumer_asset_id = str(occurrence.get("consumer_asset_id") or "")
    consumer_asset = next(
        (item for item in catalog.payload.get("assets") or [] if str(item.get("id") or "") == consumer_asset_id),
        None,
    )
    return build_reference_occurrence_source(occurrence, consumer_asset, code_artifacts)


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
        for resolved_asset_id in [_dependency_resolved_asset_id(item)]
        if resolved_asset_id
    )
    dependency_counts_by_reference = Counter(
        reference_id
        for item in dependencies
        for reference_id in [_dependency_reference_id(item)]
        if reference_id
    )
    dependency_counts_by_occurrence = Counter(
        occurrence_id
        for item in dependencies
        for occurrence_id in [_dependency_reference_occurrence_id(item)]
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
        source_id = _dependency_resolved_asset_id(dependency)
        target_id = str(dependency.get("target_asset_id") or "")
        if source_id and target_id:
            upstream_assets[target_id].add(source_id)
            downstream_assets[source_id].add(target_id)

    attention_by_asset: dict[str, list[AssetAttention]] = defaultdict(list)
    attention_keys_by_asset: dict[str, set[tuple[str, str, str | None, str | None, str | None]]] = defaultdict(set)
    for diagnostic in diagnostics:
        if _is_reference_resolution_diagnostic(diagnostic):
            continue
        attention = _attention_from_diagnostic(diagnostic)
        asset_id = _string_or_none(diagnostic.get("asset_id"))
        if asset_id:
            _append_attention(attention_by_asset, attention_keys_by_asset, asset_id, attention)
            continue
        dataflow_id = _string_or_none(diagnostic.get("dataflow_id"))
        if dataflow_id:
            for related_asset_id in dataflow_asset_links.get(dataflow_id, set()):
                if related_asset_id:
                    _append_attention(attention_by_asset, attention_keys_by_asset, related_asset_id, attention)

    for reference in reference_occurrences:
        resolution_state = _resolution_state(reference)
        if resolution_state != "unresolved":
            continue
        resolution_reason = _resolution_reason(reference)
        reference_occurrence_id = _string_or_none(reference.get("id"))
        reference_id = _string_or_none(reference.get("reference_id"))
        reference_message = str(reference.get("raw_value") or reference.get("display_name") or reference_occurrence_id or "reference")
        reference_attention = AssetAttention(
            severity="warning" if resolution_reason in {"multiple_matches", "conflicting_targets", "target_missing"} else "info",
            code=f"reference_{resolution_reason or 'unresolved'}",
            message=f"{(resolution_reason or 'unresolved').replace('_', ' ')} reference: {reference_message}",
            source_type=_reference_attention_source_type(reference),
            subject_type="reference",
            dataflow_id=_string_or_none(reference.get("dataflow_id")),
            reference_id=reference_id,
            reference_occurrence_id=reference_occurrence_id,
            details={"candidate_asset_ids": list(reference.get("candidate_asset_ids") or [])},
        )
        target_asset_id = _string_or_none(reference.get("target_asset_id"))
        if target_asset_id:
            _append_attention(attention_by_asset, attention_keys_by_asset, target_asset_id, reference_attention)
        for candidate_asset_id in reference.get("candidate_asset_ids") or []:
            candidate_id = _string_or_none(candidate_asset_id)
            if candidate_id:
                _append_attention(attention_by_asset, attention_keys_by_asset, candidate_id, reference_attention)

    asset_rows: list[dict[str, Any]] = []
    for asset in assets:
        asset_id = str(asset.get("id"))
        if not asset_id:
            continue
        metadata_source_ids = sorted({
            source_id
            for source_id in (_to_int(value) for value in (asset.get("metadata_source_ids") or []))
            if source_id is not None
        })
        metadata_sources = _metadata_sources_for_asset(asset, metadata_source_ids, metadata_source_uri_by_id)
        observations = list(asset.get("observations") or [])
        attention_items = [item.to_dict() for item in attention_by_asset.get(asset_id, [])]
        attention_items.sort(key=lambda item: (_severity_rank(item["severity"]), item["source_type"], item["code"], item["message"]))
        display_name = str(asset.get("display_name") or asset.get("label") or asset_id)
        connection_name = _string_or_none(asset.get("connection_name")) or _first_string(asset.get("connection_names") or [])
        asset_type = str(asset.get("asset_type") or "unresolved")
        format_value = _string_or_none(asset.get("format"))
        input_count = int(input_dataflow_counts.get(asset_id, 0))
        output_count = int(output_dataflow_counts.get(asset_id, 0))
        roles = _asset_roles(asset, input_count=input_count, output_count=output_count)
        full_identity = _full_identity(asset, display_name, connection_name)
        asset_rows.append({
            "id": asset_id,
            "display_name": display_name,
            "friendly_name": _friendly_name(asset, display_name),
            "full_identity": full_identity,
            "asset_type": asset_type,
            "format": format_value,
            "connection_name": connection_name,
            "connection_type": _string_or_none(asset.get("connection_type")),
            "catalog": _string_or_none(asset.get("catalog")),
            "database": _string_or_none(asset.get("database")),
            "schema_name": _string_or_none(asset.get("schema_name")),
            "table": _string_or_none(asset.get("table")),
            "path": _string_or_none(asset.get("path")),
            "query": _string_or_none(asset.get("query")),
            "python_function": _string_or_none(asset.get("python_function")),
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
        reference_id = _string_or_none(occurrence.get("reference_id"))
        consumer_asset_id = _string_or_none(occurrence.get("consumer_asset_id")) or _string_or_none(occurrence.get("target_asset_id"))
        consumer_asset = asset_by_id.get(consumer_asset_id or "")
        resolved_asset_id = _string_or_none(occurrence.get("resolved_asset_id"))
        candidate_asset_ids = [
            candidate_id
            for candidate_id in (_string_or_none(value) for value in (occurrence.get("candidate_asset_ids") or []))
            if candidate_id
        ]
        resolution = _resolution_dict(occurrence)
        observations = list(occurrence.get("observations") or [])
        reference_attention = _reference_attention(occurrence)
        attention_items = [reference_attention] if reference_attention is not None else []
        raw_value = _string_or_none(occurrence.get("raw_value")) or str(occurrence.get("display_name") or occurrence_id)
        display_name = str(occurrence.get("display_name") or raw_value)
        manual_mapping = manual_mapping_from_observations(observations)
        occurrence_rows.append({
            "id": occurrence_id,
            "reference_id": reference_id,
            "reference_type": str(occurrence.get("reference_type") or "unknown"),
            "raw_value": raw_value,
            "normalized_value": _string_or_none(occurrence.get("normalized_value")) or raw_value,
            "context_scope": _string_or_none(occurrence.get("context_scope")),
            "context_scope_source": _string_or_none(occurrence.get("context_scope_source")),
            "source_location": occurrence.get("source_location"),
            "display_name": display_name,
            "provenance": _string_or_none(occurrence.get("provenance")),
            "consumer_asset_id": consumer_asset_id,
            "consumer_asset": _asset_brief(consumer_asset_id, asset_by_id) if consumer_asset_id else None,
            "connection_name": consumer_asset.get("connection_name") if consumer_asset else None,
            "resolution": resolution,
            "resolution_method": _string_or_none(occurrence.get("resolution_method")),
            "resolved_asset_id": resolved_asset_id,
            "resolved_asset": _asset_brief(resolved_asset_id, asset_by_id) if resolved_asset_id else None,
            "candidate_asset_ids": candidate_asset_ids,
            "candidate_assets": [_asset_brief(candidate_id, asset_by_id) for candidate_id in candidate_asset_ids],
            "dependency_count": int(dependency_counts_by_occurrence.get(occurrence_id, 0)),
            "dataflow_ids": _reference_dataflow_ids(occurrence, dependencies, dataflows),
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
            for occurrence_id in (_string_or_none(value) for value in (reference.get("occurrence_ids") or []))
            if occurrence_id
        ]
        group_occurrences = [occurrence_by_id[occurrence_id] for occurrence_id in occurrence_ids if occurrence_id in occurrence_by_id]
        resolved_asset_ids = [
            asset_id
            for asset_id in (_string_or_none(value) for value in (reference.get("resolved_asset_ids") or []))
            if asset_id
        ]
        resolved_asset_id = _string_or_none(reference.get("resolved_asset_id"))
        candidate_asset_ids = [
            candidate_id
            for candidate_id in (_string_or_none(value) for value in (reference.get("candidate_asset_ids") or []))
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
            for asset_id in [_string_or_none(occurrence.get("consumer_asset_id"))]
            if asset_id
        } or {
            asset_id
            for asset_id in (_string_or_none(value) for value in (reference.get("consumer_asset_ids") or []))
            if asset_id
        })
        reference_group_rows.append({
            "id": reference_id,
            "reference_type": str(reference.get("reference_type") or "unknown"),
            "normalized_value": _string_or_none(reference.get("normalized_value")) or str(reference.get("display_name") or reference_id),
            "display_name": str(reference.get("display_name") or reference.get("normalized_value") or reference_id),
            "resolution": _resolution_dict(reference),
            "resolved_asset_id": resolved_asset_id,
            "resolved_asset_ids": resolved_asset_ids,
            "resolved_asset": _asset_brief(resolved_asset_id, asset_by_id) if resolved_asset_id else None,
            "candidate_asset_ids": candidate_asset_ids,
            "candidate_assets": [_asset_brief(candidate_id, asset_by_id) for candidate_id in candidate_asset_ids],
            "occurrence_ids": occurrence_ids,
            "consumer_asset_ids": consumer_asset_ids,
            "consumer_assets": [_asset_brief(asset_id, asset_by_id) for asset_id in consumer_asset_ids],
            "provenances": sorted({
                value
                for occurrence in group_occurrences
                for value in [_string_or_none(occurrence.get("provenance"))]
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

    asset_rows.sort(key=_asset_inventory_sort_key)
    occurrence_rows.sort(key=lambda item: (
        _resolution_state_rank(_resolution_state(item)),
        str(item.get("connection_name") or "").lower(),
        str(item.get("display_name") or "").lower(),
        str(item.get("id") or ""),
    ))
    reference_group_rows.sort(key=lambda item: (
        _resolution_state_rank(_resolution_state(item)),
        str(item.get("display_name") or "").lower(),
        str(item.get("id") or ""),
    ))

    mapped_reference_count = sum(
        1
        for item in occurrence_rows
        if item.get("manual_mapping")
    )
    filter_options = {
        "assets": _asset_filter_options(asset_rows),
        "reference_groups": _reference_group_filter_options(reference_group_rows),
        "reference_occurrences": _reference_occurrence_filter_options(occurrence_rows),
    }
    summary = {
        "assets": len(asset_rows),
        "references": len(reference_group_rows),
        "manual_mappings": mapped_reference_count,
        "visible": len(asset_rows) + len(reference_group_rows),
        "asset_attention": sum(1 for item in asset_rows if item["attention_count"] > 0),
        "with_attention": sum(1 for item in asset_rows if item["attention_count"] > 0),
        "automatic_references": sum(1 for item in reference_group_rows if _resolution_state(item) == "automatic"),
        "manual_references": sum(1 for item in reference_group_rows if _resolution_state(item) == "manual"),
        "unresolved_references": sum(1 for item in reference_group_rows if _resolution_state(item) == "unresolved"),
    }
    return {
        "summary": summary,
        "assets": asset_rows,
        "reference_groups": reference_group_rows,
        "reference_occurrences": occurrence_rows,
        "filter_options": filter_options,
        "diagnostics": diagnostics,
    }


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
        source_id = _string_or_none(item.get("source_asset_id"))
        destination_id = _string_or_none(item.get("destination_asset_id"))
        if not source_id or not destination_id:
            continue
        if destination_id == asset_id:
            upstream_assets.add(source_id)
            input_flow_counts[source_id] += 1
            input_flows.append(_asset_flow_row(item, source_id, asset_by_id))
        if source_id == asset_id:
            downstream_assets.add(destination_id)
            output_flow_counts[destination_id] += 1
            output_flows.append(_asset_flow_row(item, destination_id, asset_by_id))

    for item in dependencies:
        target_id = _string_or_none(item.get("target_asset_id"))
        resolved_asset_id = _dependency_resolved_asset_id(item)
        reference_id = _dependency_reference_id(item)
        if not target_id:
            continue
        if target_id == asset_id:
            depends_on_total += 1
            if resolved_asset_id:
                depends_on_mapped_total += 1
            else:
                depends_on_unmapped_total += 1
            depends_on.append(
                _depends_on_row(item, resolved_asset_id, reference_id, asset_by_id, reference_by_id)
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
            _used_by_row(item, target_id, asset_by_id, reference_by_id)
        )

    upstream_neighbors = [
        _asset_neighbor_row(
            neighbor_id,
            asset_by_id,
            relation_flow_count=int(input_flow_counts.get(neighbor_id, 0)),
            relation_dependency_count=int(depends_on_counts.get(neighbor_id, 0)),
        )
        for neighbor_id in upstream_assets
    ]
    downstream_neighbors = [
        _asset_neighbor_row(
            neighbor_id,
            asset_by_id,
            relation_flow_count=int(output_flow_counts.get(neighbor_id, 0)),
            relation_dependency_count=int(used_by_counts.get(neighbor_id, 0)),
        )
        for neighbor_id in downstream_assets
    ]
    upstream_neighbors.sort(key=_asset_neighbor_sort_key)
    downstream_neighbors.sort(key=_asset_neighbor_sort_key)
    input_flows.sort(key=_asset_flow_sort_key)
    output_flows.sort(key=_asset_flow_sort_key)
    depends_on.sort(key=_depends_on_sort_key)
    used_by.sort(key=_used_by_sort_key)

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
        "position": _asset_position(len(upstream_assets), len(downstream_assets)),
    }
    return {
        "asset": center,
        "definition": _asset_definition(center, code_artifacts or []) if include_definition else None,
        "attention_items": list(center.get("attention_items") or []),
        "direct_relationships": direct_relationships,
        "upstream_assets": upstream_neighbors,
        "downstream_assets": downstream_neighbors,
        "input_flows": input_flows,
        "output_flows": output_flows,
        "depends_on": depends_on,
        "used_by": used_by,
    }


def _asset_definition(asset: dict[str, Any], code_artifacts: list[EnvironmentSource]) -> dict[str, Any] | None:
    asset_type = str(asset.get("asset_type") or "")
    query = _string_or_none(asset.get("query"))
    python_function = _string_or_none(asset.get("python_function"))
    if asset_type == "sql_query" or query:
        return _sql_definition(query)
    if asset_type == "python_function" or python_function:
        return _python_definition(python_function, code_artifacts)
    return None


def _asset_definition_descriptor(asset: dict[str, Any]) -> dict[str, Any] | None:
    asset_type = str(asset.get("asset_type") or "")
    query = _string_or_none(asset.get("query"))
    python_function = _string_or_none(asset.get("python_function"))
    if asset_type == "sql_query" or query:
        return {
            "kind": "sql_query", "language": "sql", "status": "available" if query else "empty",
            "title": "SQL query", "line_count": len(query.splitlines()) if query else 0,
        }
    if asset_type == "python_function" or python_function:
        return {
            "kind": "python_function", "language": "python", "status": "available" if python_function else "empty",
            "title": "Python function", "function_path": python_function, "line_count": 0,
        }
    return None


def _sql_definition(query: str | None) -> dict[str, Any]:
    raw = (query or "").strip()
    if not raw:
        return {
            "kind": "sql_query",
            "language": "sql",
            "status": "empty",
            "title": "SQL query",
            "raw": "",
            "formatted": "",
            "line_count": 0,
            "diagnostics": [_definition_diagnostic("info", "sql_query_empty", "SQL query metadata is empty.")],
        }

    diagnostics: list[dict[str, Any]] = []
    formatted = raw
    try:
        expressions = [expression for expression in parse(raw) if expression is not None]
        if expressions:
            formatted = ";\n\n".join(expression.sql(pretty=True) for expression in expressions)
            if len(expressions) > 1:
                formatted = f"{formatted};"
    except (sqlglot_errors.ParseError, ValueError, TypeError) as exc:
        diagnostics.append(_definition_diagnostic("warning", "sql_format_failed", str(exc)))

    return {
        "kind": "sql_query",
        "language": "sql",
        "status": "available",
        "title": "SQL query",
        "raw": raw,
        "formatted": formatted,
        "line_count": _line_count(formatted),
        "diagnostics": diagnostics,
    }


def _python_definition(function_path: str | None, code_artifacts: list[EnvironmentSource]) -> dict[str, Any]:
    normalized_function_path = (function_path or "").strip()
    if not normalized_function_path:
        return {
            "kind": "python_function",
            "language": "python",
            "status": "empty",
            "title": "Python function",
            "function_path": None,
            "source": "",
            "line_count": 0,
            "diagnostics": [_definition_diagnostic("info", "python_function_empty", "Python function metadata is empty.")],
        }

    enabled_artifacts = [item for item in code_artifacts if bool(getattr(item, "enabled", True))]
    if not enabled_artifacts:
        return {
            "kind": "python_function",
            "language": "python",
            "status": "unavailable",
            "title": "Python function",
            "function_path": normalized_function_path,
            "source": "",
            "line_count": 0,
            "diagnostics": [_definition_diagnostic("info", "code_artifact_missing", "No enabled code artifact is linked to this environment.")],
        }

    diagnostics: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []
    for artifact in enabled_artifacts:
        try:
            content, module_name, relative_path = read_code_artifact_function_source(artifact, normalized_function_path)
            source, start_line, end_line = extract_python_function_source(content, normalized_function_path)
        except ArtifactIndexError as exc:
            diagnostics.append(_definition_diagnostic(
                "info",
                "python_source_not_found",
                str(exc),
                source_id=artifact.id,
                source_uri=artifact.uri,
            ))
            continue
        matches.append({
            "source_id": artifact.id,
            "source_uri": artifact.uri,
            "module_name": module_name,
            "relative_path": relative_path,
            "source": source,
            "start_line": start_line,
            "end_line": end_line,
        })

    if len(matches) == 1:
        match = matches[0]
        source = str(match["source"])
        return {
            "kind": "python_function",
            "language": "python",
            "status": "available",
            "title": "Python function",
            "function_path": normalized_function_path,
            "module_name": match["module_name"],
            "relative_path": match["relative_path"],
            "source_id": match["source_id"],
            "source_uri": match["source_uri"],
            "start_line": match["start_line"],
            "end_line": match["end_line"],
            "source": source,
            "line_count": _line_count(source),
            "diagnostics": diagnostics,
        }

    if len(matches) > 1:
        return {
            "kind": "python_function",
            "language": "python",
            "status": "ambiguous",
            "title": "Python function",
            "function_path": normalized_function_path,
            "source": "",
            "line_count": 0,
            "matches": [
                {
                    "source_id": match["source_id"],
                    "source_uri": match["source_uri"],
                    "module_name": match["module_name"],
                    "relative_path": match["relative_path"],
                    "start_line": match["start_line"],
                    "end_line": match["end_line"],
                }
                for match in matches
            ],
            "diagnostics": [
                _definition_diagnostic("warning", "python_source_ambiguous", "Python function resolves in multiple enabled code artifacts."),
                *diagnostics,
            ],
        }

    return {
        "kind": "python_function",
        "language": "python",
        "status": "unavailable",
        "title": "Python function",
        "function_path": normalized_function_path,
        "source": "",
        "line_count": 0,
        "diagnostics": diagnostics or [_definition_diagnostic("info", "python_source_unavailable", "Python source could not be resolved.")],
    }


def _definition_diagnostic(
    severity: str,
    code: str,
    message: str,
    **details: Any,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "details": {key: value for key, value in details.items() if value is not None},
    }


def _line_count(value: str) -> int:
    return len(value.splitlines()) if value else 0


def load_or_build_assets_catalog(
    session: Session,
    environment_id: int,
    *,
    input_fingerprint: str | None = None,
) -> AssetsCatalog:
    parameters_fingerprint = empty_parameters_fingerprint()
    build_key = f"{environment_id}:{ASSETS_CATALOG}:{parameters_fingerprint}"
    if input_fingerprint is not None:
        cached = cached_read_model(
            session,
            environment_id=environment_id,
            model_key=ASSETS_CATALOG,
            parameters_fingerprint=parameters_fingerprint,
            input_fingerprint=input_fingerprint,
            producer_version=ASSETS_PROJECTOR_VERSION,
        )
        if cached is not None:
            return AssetsCatalog(cached.payload, input_fingerprint)
    for _attempt in range(3):
        input_fingerprint = lineage_input_fingerprint(session, environment_id)
        cached = cached_read_model(
            session,
            environment_id=environment_id,
            model_key=ASSETS_CATALOG,
            parameters_fingerprint=parameters_fingerprint,
            input_fingerprint=input_fingerprint,
            producer_version=ASSETS_PROJECTOR_VERSION,
        )
        if cached is not None:
            return AssetsCatalog(cached.payload, input_fingerprint)
        with read_model_build_lock(build_key):
            input_fingerprint = lineage_input_fingerprint(session, environment_id)
            cached = cached_read_model(
                session,
                environment_id=environment_id,
                model_key=ASSETS_CATALOG,
                parameters_fingerprint=parameters_fingerprint,
                input_fingerprint=input_fingerprint,
                producer_version=ASSETS_PROJECTOR_VERSION,
            )
            if cached is not None:
                return AssetsCatalog(cached.payload, input_fingerprint)
            generation = read_model_generation(
                environment_id=environment_id,
                model_key=ASSETS_CATALOG,
                parameters_fingerprint=parameters_fingerprint,
                input_fingerprint=input_fingerprint,
                producer_version=ASSETS_PROJECTOR_VERSION,
            )
            lineage = load_or_build_lineage_graph(session, environment_id)
            payload = build_assets_inventory(lineage, _metadata_source_uri_by_id({}, lineage))
            current_fingerprint = lineage_input_fingerprint(session, environment_id)
            if current_fingerprint != input_fingerprint:
                continue
            replace_read_model(
                session,
                environment_id=environment_id,
                model_key=ASSETS_CATALOG,
                parameters_fingerprint=parameters_fingerprint,
                input_fingerprint=input_fingerprint,
                producer_version=ASSETS_PROJECTOR_VERSION,
                payload=payload,
                expected_generation=generation,
            )
            return AssetsCatalog(payload, input_fingerprint)
    raise RuntimeError("Unable to build a stable Assets catalog")


def _build_reference_detail(
    center: dict[str, Any],
    asset_by_id: dict[str, dict[str, Any]],
    lineage: dict[str, Any],
) -> dict[str, Any]:
    dependencies = list(lineage.get("dependencies") or [])
    references = list(lineage.get("references") or [])
    reference_by_id = {str(item.get("id")): item for item in references if item.get("id")}
    reference_id = str(center.get("id") or "")
    output_rows: list[dict[str, Any]] = []
    downstream_assets: set[str] = set()
    for item in dependencies:
        dependency_reference_id = _dependency_reference_id(item)
        if dependency_reference_id != reference_id:
            continue
        target_asset_id = _string_or_none(item.get("target_asset_id"))
        if not target_asset_id:
            continue
        downstream_assets.add(target_asset_id)
        output_rows.append(_used_by_row(item, target_asset_id, asset_by_id, reference_by_id))

    output_rows.sort(key=_used_by_sort_key)
    downstream_neighbors = [
        _asset_neighbor_row(
            neighbor_id,
            asset_by_id,
            relation_flow_count=0,
            relation_dependency_count=1,
        )
        for neighbor_id in sorted(downstream_assets)
    ]
    direct_relationships = {
        "upstream_assets": 0,
        "downstream_assets": len(downstream_assets),
        "input_flows": 0,
        "output_flows": 0,
        "depends_on_count": 0,
        "depends_on_total": 0,
        "depends_on_mapped_count": 0,
        "depends_on_unmapped_count": 0,
        "depends_on_asset_count": 0,
        "depends_on_reference_count": 0,
        "used_by_count": len(output_rows),
        "used_by_total": len(output_rows),
        "position": "isolated",
    }
    return {
        "asset": center,
        "attention_items": list(center.get("attention_items") or []),
        "direct_relationships": direct_relationships,
        "upstream_assets": [],
        "downstream_assets": downstream_neighbors,
        "input_flows": [],
        "output_flows": [],
        "depends_on": [],
        "used_by": output_rows,
    }


def _asset_flow_row(
    item: dict[str, Any],
    counterpart_asset_id: str,
    asset_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "dataflow_id": str(item.get("dataflow_id") or ""),
        "name": str(item.get("name") or item.get("dataflow_id") or "dataflow"),
        "stage": _string_or_none(item.get("stage")),
        "load_type": _string_or_none(item.get("load_type")),
        "metadata_source_id": _to_int(item.get("metadata_source_id")),
        "metadata_source_uri": _string_or_none(item.get("metadata_source_uri")),
        "source_asset_id": _string_or_none(item.get("source_asset_id")),
        "destination_asset_id": _string_or_none(item.get("destination_asset_id")),
        "counterpart": _asset_brief(counterpart_asset_id, asset_by_id),
    }


def _asset_neighbor_row(
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
        "asset": _asset_brief(neighbor_asset_id, asset_by_id),
        "relation_flow_count": relation_flow_count,
        "relation_dependency_count": relation_dependency_count,
        "relation_kinds": relation_kinds,
    }


def _depends_on_row(
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
        "resolution": _resolution_dict(item),
        "resolution_method": str(item.get("resolution_method") or ""),
        "reference_id": reference_id,
        "resolved_asset_id": resolved_asset_id,
        "resolved_asset": None,
        "source_reference": None,
    }
    if resolved_asset_id:
        row["resolved_asset"] = _asset_brief(resolved_asset_id, asset_by_id)
    if reference_id:
        row["source_reference"] = _reference_brief(reference_id, reference_by_id)
    return row


def _used_by_row(
    item: dict[str, Any],
    target_asset_id: str,
    asset_by_id: dict[str, dict[str, Any]],
    reference_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    reference_id = _dependency_reference_id(item)
    return {
        "id": str(item.get("id") or ""),
        "kind": str(item.get("kind") or "reads"),
        "provenance": str(item.get("provenance") or "sql"),
        "resolution": _resolution_dict(item),
        "resolution_method": str(item.get("resolution_method") or ""),
        "target_asset": _asset_brief(target_asset_id, asset_by_id),
        "reference": _reference_brief(reference_id, reference_by_id) if reference_id else None,
    }


def _reference_brief(reference_id: str, reference_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
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
        "resolution": _resolution_dict(reference),
        "raw_value": _string_or_none(reference.get("raw_value")) or _string_or_none(reference.get("normalized_value")),
        "provenance": _string_or_none(reference.get("provenance")) or _first_string(reference.get("provenances") or []),
    }


def _dependency_resolved_asset_id(item: dict[str, Any]) -> str | None:
    return _string_or_none(item.get("resolved_asset_id"))


def _dependency_reference_id(item: dict[str, Any]) -> str | None:
    return _string_or_none(item.get("reference_id"))


def _dependency_reference_occurrence_id(item: dict[str, Any]) -> str | None:
    return _string_or_none(item.get("reference_occurrence_id"))


def _reference_dataflow_ids(
    occurrence: dict[str, Any],
    dependencies: list[dict[str, Any]],
    dataflows: list[dict[str, Any]],
) -> list[str]:
    occurrence_id = _string_or_none(occurrence.get("id"))
    reference_id = _string_or_none(occurrence.get("reference_id"))
    if not occurrence_id and not reference_id:
        return []
    matching_dependencies = [
        item
        for item in dependencies
        if _dependency_reference_occurrence_id(item) == occurrence_id
        or _dependency_reference_id(item) == reference_id
    ]
    consumer_asset_ids = {
        asset_id
        for item in matching_dependencies
        for asset_id in [
            _string_or_none(item.get("consumer_asset_id")) or _string_or_none(item.get("target_asset_id")),
        ]
        if asset_id
    }
    consumer_asset_ids.update({
        asset_id
        for asset_id in [
            _string_or_none(occurrence.get("consumer_asset_id")) or _string_or_none(occurrence.get("target_asset_id")),
        ]
        if asset_id
    })
    values = {
        dataflow_id
        for item in matching_dependencies
        for observation in (item.get("observations") or [])
        for dataflow_id in [_string_or_none(observation.get("dataflow_id"))]
        if dataflow_id
    }
    values.update(
        _string_or_none(dataflow.get("dataflow_id"))
        for dataflow in dataflows
        if _string_or_none(dataflow.get("source_asset_id")) in consumer_asset_ids
        and _string_or_none(dataflow.get("dataflow_id"))
    )
    return sorted(values)


def _asset_brief(asset_id: str, asset_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
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
        "connection_name": _string_or_none(asset.get("connection_name")),
        "format": _string_or_none(asset.get("format")),
        "attention_count": int(asset.get("attention_count") or 0),
    }


def _asset_inventory_sort_key(item: dict[str, Any]) -> tuple[str, str, str, str, str, str, str]:
    asset_name = (
        _string_or_none(item.get("table"))
        or _string_or_none(item.get("path"))
        or _string_or_none(item.get("python_function"))
        or _string_or_none(item.get("query"))
        or _string_or_none(item.get("friendly_name"))
        or _string_or_none(item.get("display_name"))
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


def _asset_neighbor_sort_key(item: dict[str, Any]) -> tuple[int, int, str, str]:
    asset = item.get("asset") or {}
    return (
        -int(item.get("relation_flow_count") or 0),
        -int(asset.get("attention_count") or 0),
        str(asset.get("friendly_name") or "").lower(),
        str(asset.get("id") or ""),
    )


def _asset_flow_sort_key(item: dict[str, Any]) -> tuple[str, str, str]:
    counterpart = item.get("counterpart") or {}
    return (
        str(item.get("name") or "").lower(),
        str(counterpart.get("friendly_name") or "").lower(),
        str(item.get("id") or ""),
    )


def _depends_on_sort_key(item: dict[str, Any]) -> tuple[int, int, str, str]:
    resolved_asset = item.get("resolved_asset") or {}
    source_reference = item.get("source_reference") or {}
    display_name = (
        str(resolved_asset.get("friendly_name") or "")
        if item.get("resolved_asset_id")
        else str(source_reference.get("display_name") or "")
    ).lower()
    return (
        0 if item.get("resolved_asset_id") else 1,
        _resolution_state_rank(_resolution_state(item)),
        display_name,
        str(item.get("id") or ""),
    )


def _used_by_sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
    target_asset = item.get("target_asset") or {}
    return (
        _resolution_state_rank(_resolution_state(item)),
        str(target_asset.get("friendly_name") or "").lower(),
        str(item.get("id") or ""),
    )


def _resolution_state_rank(value: str) -> int:
    return {"unresolved": 0, "manual": 1, "automatic": 2}.get(value, 3)


def _resolution_dict(item: dict[str, Any]) -> dict[str, str | None]:
    resolution = item.get("resolution")
    if not isinstance(resolution, dict):
        return {"state": "unresolved", "reason": "no_match"}
    state = str(resolution.get("state") or "unresolved")
    if state not in {"automatic", "manual", "unresolved"}:
        state = "unresolved"
    reason = _string_or_none(resolution.get("reason")) if state == "unresolved" else None
    return {"state": state, "reason": reason}


def _resolution_state(item: dict[str, Any]) -> str:
    return str(_resolution_dict(item)["state"])


def _resolution_reason(item: dict[str, Any]) -> str | None:
    return _resolution_dict(item)["reason"]


def _asset_position(upstream_count: int, downstream_count: int) -> str:
    if upstream_count == 0 and downstream_count > 0:
        return "entry"
    if upstream_count > 0 and downstream_count > 0:
        return "transit"
    if upstream_count > 0 and downstream_count == 0:
        return "exit"
    return "isolated"


def _compact_asset_row(asset: dict[str, Any]) -> dict[str, Any]:
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


def _compact_reference_row(reference: dict[str, Any]) -> dict[str, Any]:
    excluded = {"attention_items", "observations", "occurrence_ids"}
    result = {key: value for key, value in reference.items() if key not in excluded}
    result["occurrence_count"] = len(reference.get("occurrence_ids") or [])
    return result


def _filter_asset_rows(
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
        if scope and _asset_position(upstream_count, downstream_count) != scope:
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


def _sort_asset_rows(rows: list[dict[str, Any]], *, sort_by: str, sort_dir: str) -> list[dict[str, Any]]:
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


def _filter_reference_rows(
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
        if resolution_state and _resolution_state(row) != resolution_state:
            continue
        has_attention = int(row.get("attention_count") or 0) > 0
        if attention_state == "with_attention" and not has_attention:
            continue
        if attention_state == "clean" and has_attention:
            continue
        values = [
            row.get("id"), row.get("display_name"), row.get("normalized_value"), row.get("reference_type"),
            _resolution_state(row), _resolution_reason(row), *(row.get("provenances") or []), *(row.get("resolution_methods") or []),
            (row.get("resolved_asset") or {}).get("full_identity"),
            (row.get("resolved_asset") or {}).get("display_name"),
            *(asset.get("full_identity") or asset.get("display_name") for asset in row.get("consumer_assets") or []),
            *(asset.get("full_identity") or asset.get("display_name") for asset in row.get("candidate_assets") or []),
        ]
        if needle and needle not in " ".join(str(value).lower() for value in values if value not in (None, "")):
            continue
        result.append(row)
    return result


def _sort_reference_rows(rows: list[dict[str, Any]], *, sort_by: str, sort_dir: str) -> list[dict[str, Any]]:
    sort_keys = {
        "display_name": lambda row: str(row.get("display_name") or "").lower(),
        "reference_type": lambda row: str(row.get("reference_type") or "").lower(),
        "resolution_state": lambda row: _resolution_state(row),
        "dependency_count": lambda row: int(row.get("dependency_count") or 0),
        "attention_count": lambda row: int(row.get("attention_count") or 0),
    }
    key = sort_keys.get(sort_by)
    if key is None:
        raise ValueError(f"Unsupported reference sort field: {sort_by}")
    ordered = sorted(rows, key=lambda row: str(row.get("id") or ""))
    return sorted(ordered, key=key, reverse=sort_dir == "desc")


def _asset_roles(asset: dict[str, Any], *, input_count: int, output_count: int) -> list[str]:
    roles = {str(role) for role in (asset.get("roles") or []) if str(role)}
    if output_count > 0:
        roles.add("source")
    if input_count > 0:
        roles.add("destination")
    return sorted(roles)


def _metadata_source_uri_by_id(metadata: dict[str, Any], lineage: dict[str, Any]) -> dict[int, str]:
    values: dict[int, str] = {}
    for source in metadata.get("sources") or []:
        source_id = _to_int(source.get("id"))
        source_uri = _string_or_none(source.get("uri"))
        if source_id is not None and source_uri:
            values[source_id] = source_uri
    for asset in lineage.get("assets") or []:
        for observation in asset.get("observations") or []:
            source_id = _to_int(observation.get("metadata_source_id"))
            source_uri = _string_or_none(observation.get("metadata_source_uri"))
            if source_id is not None and source_uri and source_id not in values:
                values[source_id] = source_uri
    return values


def _asset_filter_options(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    attention = {("with_attention" if int(item.get("attention_count") or 0) > 0 else "clean") for item in rows}
    return {
        "connections": sorted({item["connection_name"] for item in rows if item.get("connection_name")}),
        "formats": sorted({item["format"] for item in rows if item.get("format")}),
        "asset_types": sorted({item["asset_type"] for item in rows if item.get("asset_type")}),
        "roles": sorted({role for item in rows for role in (item.get("roles") or [])}),
        "attention_states": sorted(attention),
    }


def _reference_group_filter_options(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    attention = {("with_attention" if int(item.get("attention_count") or 0) > 0 else "clean") for item in rows}
    return {
        "reference_types": sorted({item["reference_type"] for item in rows if item.get("reference_type")}),
        "provenances": sorted({value for item in rows for value in (item.get("provenances") or []) if value}),
        "resolution_states": sorted({_resolution_state(item) for item in rows}),
        "attention_states": sorted(attention),
    }


def _reference_occurrence_filter_options(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    attention = {("with_attention" if int(item.get("attention_count") or 0) > 0 else "clean") for item in rows}
    return {
        "connections": sorted({item["connection_name"] for item in rows if item.get("connection_name")}),
        "reference_types": sorted({item["reference_type"] for item in rows if item.get("reference_type")}),
        "provenances": sorted({item["provenance"] for item in rows if item.get("provenance")}),
        "resolution_states": sorted({_resolution_state(item) for item in rows}),
        "resolution_methods": sorted({item["resolution_method"] for item in rows if item.get("resolution_method")}),
        "attention_states": sorted(attention),
    }


def _metadata_sources_for_asset(
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
        source_id = _to_int(observation.get("metadata_source_id"))
        source_uri = _string_or_none(observation.get("metadata_source_uri"))
        if source_id is not None and source_uri:
            values[source_id] = source_uri
    return [
        {"id": source_id, "uri": values[source_id]}
        for source_id in sorted(values)
    ]


def _append_attention(
    attention_by_asset: dict[str, list[AssetAttention]],
    attention_keys_by_asset: dict[str, set[tuple[str, str, str | None, str | None, str | None]]],
    asset_id: str,
    attention: AssetAttention,
) -> None:
    key = (attention.code, attention.message, attention.dataflow_id, attention.reference_id, attention.reference_occurrence_id)
    if key in attention_keys_by_asset[asset_id]:
        return
    attention_keys_by_asset[asset_id].add(key)
    attention_by_asset[asset_id].append(attention)


def _attention_from_diagnostic(diagnostic: dict[str, Any]) -> AssetAttention:
    details = diagnostic.get("details")
    subject_type = "asset" if diagnostic.get("asset_id") else "dataflow" if diagnostic.get("dataflow_id") else "graph"
    return AssetAttention(
        severity=_severity(str(diagnostic.get("severity") or "warning")),
        code=str(diagnostic.get("code") or "lineage_diagnostic"),
        message=str(diagnostic.get("message") or "Lineage diagnostic"),
        source_type="lineage_diagnostic",
        subject_type=subject_type,
        dataflow_id=_string_or_none(diagnostic.get("dataflow_id")),
        metadata_source_id=_to_int(diagnostic.get("metadata_source_id")),
        reference_id=_string_or_none((details or {}).get("reference_id") if isinstance(details, dict) else None),
        reference_occurrence_id=_string_or_none((details or {}).get("reference_occurrence_id") if isinstance(details, dict) else None),
        details=dict(details) if isinstance(details, dict) else {},
    )


def _is_reference_resolution_diagnostic(diagnostic: dict[str, Any]) -> bool:
    code = str(diagnostic.get("code") or "")
    details = diagnostic.get("details")
    has_reference_identity = isinstance(details, dict) and (
        bool(_string_or_none(details.get("reference_id")))
        or bool(_string_or_none(details.get("reference_occurrence_id")))
    )
    return has_reference_identity and code.startswith(("dependency_", "reference_"))


def _reference_attention(reference: dict[str, Any]) -> AssetAttention | None:
    resolution_state = _resolution_state(reference)
    if resolution_state != "unresolved":
        return None
    resolution_reason = _resolution_reason(reference)
    reference_occurrence_id = _string_or_none(reference.get("id"))
    reference_id = _string_or_none(reference.get("reference_id"))
    reference_message = str(reference.get("raw_value") or reference.get("display_name") or reference_occurrence_id or "reference")
    return AssetAttention(
        severity="warning" if resolution_reason in {"multiple_matches", "conflicting_targets", "target_missing"} else "info",
        code=f"reference_{resolution_reason or 'unresolved'}",
        message=f"{(resolution_reason or 'unresolved').replace('_', ' ')} reference: {reference_message}",
        source_type=_reference_attention_source_type(reference),
        subject_type="reference",
        reference_id=reference_id,
        reference_occurrence_id=reference_occurrence_id,
        details={"candidate_asset_ids": list(reference.get("candidate_asset_ids") or [])},
    )


def _reference_attention_source_type(reference: dict[str, Any]) -> str:
    provenance = str(reference.get("provenance") or "").strip().lower()
    if provenance == "python_sql":
        return "python_sql_reference"
    if provenance == "python":
        return "python_reference"
    if provenance == "sql":
        return "sql_reference"
    return "reference"


def _qualified_table(asset: dict[str, Any]) -> str | None:
    table = _string_or_none(asset.get("table"))
    if not table:
        return None
    parts = [
        _string_or_none(asset.get("catalog")),
        _string_or_none(asset.get("database")),
        _string_or_none(asset.get("schema_name")),
        table,
    ]
    values = [value for value in parts if value]
    return ".".join(values) if values else None


def _full_identity(asset: dict[str, Any], display_name: str, connection_name: str | None) -> str:
    locator = (
        _string_or_none(asset.get("query"))
        or _string_or_none(asset.get("python_function"))
        or _string_or_none(asset.get("path"))
        or _qualified_table(asset)
        or display_name
    )
    connection = connection_name or "unknown connection"
    return " · ".join(part for part in (connection, locator) if part)


def _friendly_name(asset: dict[str, Any], display_name: str) -> str:
    alias = _string_or_none(asset.get("table"))
    python_function = _string_or_none(asset.get("python_function"))
    if python_function:
        if alias:
            return alias
        return python_function.split(".")[-1] or python_function
    if _string_or_none(asset.get("query")):
        if alias:
            return alias
        return "SQL query"
    table = alias
    if table:
        return table
    path = _string_or_none(asset.get("path"))
    if path:
        normalized = path.replace("\\", "/").rstrip("/")
        parts = [part for part in normalized.split("/") if part]
        if len(parts) >= 3:
            return "/".join(parts[-3:])
        if parts:
            return "/".join(parts)
        return normalized or display_name
    return display_name


def _severity(value: str) -> str:
    return value if value in {"info", "warning", "error"} else "warning"


def _severity_rank(value: str) -> int:
    return {"error": 0, "warning": 1, "info": 2}.get(value, 3)


def _first_string(values: list[Any]) -> str | None:
    for value in values:
        text = _string_or_none(value)
        if text:
            return text
    return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _to_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
