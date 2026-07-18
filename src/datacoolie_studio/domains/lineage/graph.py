from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

from datacoolie_studio.domains.assets.reference_identity import canonical_reference_id, normalize_reference_signature
from datacoolie_studio.domains.assets.registry import AssetRegistry
from datacoolie_studio.domains.assets.resolver import Resolution
from datacoolie_studio.domains.lineage.models import (
    LineageDataflow,
    LineageDependency,
    LineageDiagnostic,
    LineageReference,
    LineageReferenceOccurrence,
)


MAX_OBSERVATIONS = 20
MAX_CANDIDATES = 20


def build_typed_graph(
    registry: AssetRegistry,
    dataflows: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
    base_diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    assets, references, reference_occurrences, typed_dataflows, dependencies, diagnostics = _build_typed_graph_components(
        registry,
        dataflows,
        analyses,
        base_diagnostics,
    )
    serialized_diagnostics = [item.to_dict() for item in diagnostics]
    summary = _summary(assets, references, typed_dataflows, dependencies, serialized_diagnostics)
    return {
        "schema_version": "lineage.v2",
        "summary": summary,
        "assets": sorted(assets, key=lambda item: (str(item.get("display_name", "")).lower(), item["id"])),
        "references": [
            item.to_dict()
            for item in sorted(references.values(), key=lambda value: (value.display_name.lower(), value.id))
        ],
        "reference_occurrences": [
            item.to_dict()
            for item in sorted(reference_occurrences.values(), key=lambda value: (value.display_name.lower(), value.id))
        ],
        "dataflows": [
            item.to_dict()
            for item in sorted(typed_dataflows, key=lambda value: (value.name.lower(), value.id))
        ],
        "dependencies": [
            item.to_dict()
            for item in sorted(dependencies.values(), key=lambda value: value.id)
        ],
        "diagnostics": sorted(
            serialized_diagnostics,
            key=lambda item: (str(item.get("severity", "")), str(item.get("code", "")), str(item.get("message", ""))),
        ),
    }


def build_typed_graph_summary(
    registry: AssetRegistry,
    dataflows: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
    base_diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute the Overview projection without serializing a full lineage graph."""
    assets, references, _, typed_dataflows, dependencies, diagnostics = _build_typed_graph_components(
        registry,
        dataflows,
        analyses,
        base_diagnostics,
    )
    summary = _summary(assets, references, typed_dataflows, dependencies, diagnostics)
    return {
        **summary,
        "error_count": sum(1 for item in diagnostics if item.severity == "error"),
    }


def _build_typed_graph_components(
    registry: AssetRegistry,
    dataflows: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
    base_diagnostics: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    dict[str, LineageReference],
    dict[str, LineageReferenceOccurrence],
    list[LineageDataflow],
    dict[str, LineageDependency],
    list[LineageDiagnostic],
]:
    typed_dataflows = _build_dataflows(registry, dataflows)
    references: dict[str, LineageReference] = {}
    reference_occurrences: dict[str, LineageReferenceOccurrence] = {}
    dependencies: dict[str, LineageDependency] = {}
    diagnostics = [_normalize_diagnostic(item) for item in [*base_diagnostics, *registry.diagnostics]]
    diagnostics.extend(_dataflow_diagnostics(typed_dataflows))

    for index, dataflow in enumerate(typed_dataflows):
        if index >= len(analyses):
            continue
        analysis = analyses[index]
        target_asset_id = dataflow.source_asset_id
        for resolution in analysis.get("resolutions", []):
            _add_resolution(
                resolution,
                target_asset_id,
                dataflow,
                references,
                reference_occurrences,
                dependencies,
                diagnostics,
            )
        for diagnostic in analysis.get("diagnostics", []):
            normalized = _normalize_analysis_diagnostic(diagnostic, target_asset_id, dataflow)
            diagnostics.append(normalized)
            if _is_unresolved_input_diagnostic(diagnostic):
                _add_unresolved_diagnostic_reference(
                    diagnostic,
                    target_asset_id,
                    dataflow,
                    references,
                    reference_occurrences,
                    dependencies,
                )

    assets = [_present_asset(node) for node in registry.nodes()]
    diagnostics.extend(_validate_graph(assets, references, reference_occurrences, typed_dataflows, dependencies))
    invalid_codes = {"dangling_dataflow", "dangling_dependency", "invalid_dependency_target"}
    invalid_relation_ids = {
        str(item.details.get("relation_id"))
        for item in diagnostics
        if item.code in invalid_codes and item.details.get("relation_id")
    }
    typed_dataflows = [item for item in typed_dataflows if item.id not in invalid_relation_ids]
    dependencies = {key: item for key, item in dependencies.items() if key not in invalid_relation_ids}
    _finalize_references(references, dependencies)
    return assets, references, reference_occurrences, typed_dataflows, dependencies, diagnostics


def _build_dataflows(registry: AssetRegistry, dataflows: list[dict[str, Any]]) -> list[LineageDataflow]:
    records = []
    occurrences: Counter[str] = Counter()
    for item in dataflows:
        source_id = registry.resolve_candidate_id(str(item["source_asset_id"]))
        destination_id = registry.resolve_candidate_id(str(item["destination_asset_id"]))
        semantic_key = (
            f"{item['metadata_source_id']}:{item['dataflow_id']}:"
            f"{source_id}:{destination_id}"
        )
        occurrence_index = occurrences[semantic_key]
        occurrences[semantic_key] += 1
        occurrence_key = f"{semantic_key}:occurrence:{occurrence_index}"
        records.append(LineageDataflow(
            id=f"dataflow:{_digest(occurrence_key)}",
            dataflow_id=str(item["dataflow_id"]),
            name=str(item["name"]),
            source_asset_id=source_id,
            destination_asset_id=destination_id,
            stage=_optional_string(item.get("stage")),
            load_type=_optional_string(item.get("load_type")),
            metadata_source_id=int(item["metadata_source_id"]),
            metadata_source_uri=str(item["metadata_source_uri"]),
        ))
    return records


def _add_resolution(
    resolution: Resolution,
    target_asset_id: str,
    dataflow: LineageDataflow,
    references: dict[str, LineageReference],
    reference_occurrences: dict[str, LineageReferenceOccurrence],
    dependencies: dict[str, LineageDependency],
    diagnostics: list[LineageDiagnostic],
) -> None:
    evidence = resolution.evidence.to_dict()
    resolution_observations = list(resolution.observations or [])
    provenance = _provenance(resolution.evidence.provenance)
    reference = _reference_from_resolution(resolution)
    existing_reference = references.get(reference.id)
    if existing_reference is None:
        references[reference.id] = reference
        existing_reference = reference
    else:
        _merge_reference_identity(existing_reference, reference)
    occurrence = _reference_occurrence_from_resolution(resolution, existing_reference.id, target_asset_id, provenance)
    existing_occurrence = reference_occurrences.get(occurrence.id)
    if existing_occurrence is None:
        reference_occurrences[occurrence.id] = occurrence
        existing_occurrence = occurrence
    else:
        existing_occurrence.resolution_status = _merge_resolution_status(
            existing_occurrence.resolution_status,
            occurrence.resolution_status,
        )
        if existing_occurrence.resolved_asset_id is None and occurrence.resolved_asset_id is not None:
            existing_occurrence.resolved_asset_id = occurrence.resolved_asset_id
        for candidate_asset_id in reference.candidate_asset_ids:
            if not candidate_asset_id:
                continue
            if candidate_asset_id in existing_occurrence.candidate_asset_ids:
                continue
            if len(existing_occurrence.candidate_asset_ids) >= MAX_CANDIDATES:
                break
            existing_occurrence.candidate_asset_ids.append(candidate_asset_id)
    _add_unique(existing_reference.occurrence_ids, existing_occurrence.id)
    _add_unique(existing_reference.consumer_asset_ids, target_asset_id)
    _add_unique(existing_reference.provenances, provenance)
    if occurrence.resolved_asset_id:
        _add_unique(existing_reference.resolved_asset_ids, occurrence.resolved_asset_id)
    for candidate_asset_id in occurrence.candidate_asset_ids:
        _add_unique(existing_reference.candidate_asset_ids, candidate_asset_id, limit=MAX_CANDIDATES)
    _append_observation(existing_reference.observations, evidence)
    _append_observation(existing_occurrence.observations, evidence)
    for observation in resolution_observations:
        _append_observation(existing_reference.observations, observation)
        _append_observation(existing_occurrence.observations, observation)

    resolved_asset_id = resolution.asset_id or None
    if not resolved_asset_id:
        diagnostics.append(LineageDiagnostic(
            severity="warning" if resolution.status in {"ambiguous", "mapping_target_missing"} else "info",
            code=f"dependency_{resolution.status}",
            message=f"{resolution.status.replace('_', ' ')} input: {resolution.evidence.value}",
            asset_id=target_asset_id,
            dataflow_id=dataflow.dataflow_id,
            metadata_source_id=dataflow.metadata_source_id,
            details={
                "reference_id": existing_reference.id,
                "reference_occurrence_id": existing_occurrence.id,
                "resolution_method": resolution.method,
                "candidate_asset_ids": resolution.candidates[:MAX_CANDIDATES],
            },
        ))
    dependency_id = _dependency_id(existing_occurrence.id, target_asset_id, "reads")
    dependency = dependencies.get(dependency_id)
    if dependency is None:
        dependency = LineageDependency(
            id=dependency_id,
            target_asset_id=target_asset_id,
            consumer_asset_id=target_asset_id,
            kind="reads",
            provenance=provenance,
            resolution_status=resolution.status,
            resolution_method=resolution.method,
            reference_id=existing_reference.id,
            reference_occurrence_id=existing_occurrence.id,
            resolved_asset_id=resolved_asset_id,
        )
        dependencies[dependency_id] = dependency
    else:
        dependency.resolution_status = _merge_resolution_status(dependency.resolution_status, resolution.status)
        if resolved_asset_id:
            dependency.resolved_asset_id = resolved_asset_id
    _append_observation(dependency.observations, evidence)
    for observation in resolution_observations:
        _append_observation(dependency.observations, observation)


def _add_unresolved_diagnostic_reference(
    diagnostic: dict[str, Any],
    target_asset_id: str,
    dataflow: LineageDataflow,
    references: dict[str, LineageReference],
    reference_occurrences: dict[str, LineageReferenceOccurrence],
    dependencies: dict[str, LineageDependency],
) -> None:
    raw_value = str(diagnostic.get("details", {}).get("expression") or diagnostic.get("message") or diagnostic["code"])
    provenance = "python" if str(diagnostic.get("code", "")).startswith(("dynamic_", "python_")) else "sql"
    signature = normalize_reference_signature(reference_type="unknown", value=raw_value)
    reference_id = canonical_reference_id(signature)
    occurrence_key = f"{reference_id}:{target_asset_id}:{provenance}:{raw_value.strip().lower()}"
    occurrence_id = f"reference-occurrence:{_digest(occurrence_key)}"
    observation = {
        "reference_type": "unknown",
        "value": raw_value,
        "provenance": provenance,
        "confidence": "unresolved",
        "location": diagnostic.get("location"),
        "details": diagnostic.get("details", {}),
    }
    reference = references.setdefault(reference_id, LineageReference(
        id=reference_id,
        reference_type="unknown",
        display_name=_compact_reference_name(raw_value),
        normalized_value=signature.normalized_value,
        group_status="unresolved",
        resolved_asset_id=None,
    ))
    occurrence = reference_occurrences.setdefault(occurrence_id, LineageReferenceOccurrence(
        id=occurrence_id,
        reference_id=reference_id,
        reference_type="unknown",
        display_name=_compact_reference_name(raw_value),
        resolution_status="unresolved",
        raw_value=raw_value,
        normalized_value=signature.normalized_value,
        context_scope=None,
        context_scope_source=None,
        source_location=_source_location_dict(diagnostic.get("location")),
        provenance=provenance,
        target_asset_id=target_asset_id,
        consumer_asset_id=target_asset_id,
        resolved_asset_id=None,
        resolution_method=str(diagnostic.get("code") or "unresolved_reference"),
    ))
    _add_unique(reference.occurrence_ids, occurrence.id)
    _add_unique(reference.consumer_asset_ids, target_asset_id)
    _add_unique(reference.provenances, provenance)
    _append_observation(reference.observations, observation)
    _append_observation(occurrence.observations, observation)
    dependency_id = _dependency_id(occurrence_id, target_asset_id, "reads")
    dependency = dependencies.setdefault(dependency_id, LineageDependency(
        id=dependency_id,
        target_asset_id=target_asset_id,
        consumer_asset_id=target_asset_id,
        kind="reads",
        provenance=provenance,
        resolution_status="unresolved",
        resolution_method=str(diagnostic.get("code") or "unresolved_reference"),
        reference_id=reference_id,
        reference_occurrence_id=occurrence_id,
        resolved_asset_id=None,
    ))
    _append_observation(dependency.observations, observation)


def _reference_from_resolution(
    resolution: Resolution,
) -> LineageReference:
    evidence = resolution.evidence
    signature = resolution.reference_signature
    reference_id = canonical_reference_id(signature)
    return LineageReference(
        id=reference_id,
        reference_type=signature.reference_type,
        display_name=_compact_reference_name(evidence.value),
        normalized_value=signature.normalized_value,
        group_status=_initial_group_status(resolution.status),
        resolved_asset_id=resolution.asset_id,
        resolved_asset_ids=([resolution.asset_id] if resolution.asset_id else []),
        candidate_asset_ids=resolution.candidates[:MAX_CANDIDATES],
        occurrence_ids=[],
        consumer_asset_ids=[],
        provenances=[],
        dependency_count=0,
        observations=[],
    )


def _reference_occurrence_from_resolution(
    resolution: Resolution,
    reference_id: str,
    target_asset_id: str,
    provenance: str,
) -> LineageReferenceOccurrence:
    evidence = resolution.evidence
    signature = resolution.reference_signature
    key = ":".join([
        target_asset_id,
        reference_id,
        provenance,
        evidence.value.strip().lower(),
        _proven_context(evidence),
        _source_location_key(evidence.location),
        resolution.context_scope or "",
        resolution.context_scope_source or "",
    ])
    return LineageReferenceOccurrence(
        id=f"reference-occurrence:{_digest(key)}",
        reference_id=reference_id,
        reference_type=signature.reference_type,
        display_name=_compact_reference_name(evidence.value),
        resolution_status=_normalize_resolution_status(resolution.status),
        raw_value=evidence.value,
        normalized_value=signature.normalized_value,
        context_scope=resolution.context_scope,
        context_scope_source=resolution.context_scope_source,
        source_location=_source_location_dict(evidence.location),
        provenance=provenance,
        target_asset_id=target_asset_id,
        consumer_asset_id=target_asset_id,
        resolved_asset_id=resolution.asset_id,
        candidate_asset_ids=resolution.candidates[:MAX_CANDIDATES],
        resolution_method=resolution.method,
        observations=[],
    )


def _present_asset(node: dict[str, Any]) -> dict[str, Any]:
    endpoint_kind = str(node.get("endpoint_kind") or "")
    if node.get("query"):
        kind = "sql_query"
    elif node.get("python_function"):
        kind = "python_function"
    elif endpoint_kind == "api":
        kind = "api"
    elif endpoint_kind == "table" or node.get("table") and not node.get("path"):
        kind = "table"
    elif endpoint_kind == "file" or node.get("path"):
        kind = "path"
    else:
        kind = "unresolved"
    metadata_source_ids = list(node.get("metadata_source_ids") or [])
    observations = list(node.get("observations") or [])[:MAX_OBSERVATIONS]
    return {
        **node,
        "entity_type": "asset",
        "asset_type": kind,
        "display_name": node.get("display_label") or node.get("endpoint_locator") or node.get("label") or node["id"],
        "declaration_status": "declared" if metadata_source_ids else "discovered_only",
        "observations": observations,
    }


def _validate_graph(
    assets: list[dict[str, Any]],
    references: dict[str, LineageReference],
    reference_occurrences: dict[str, LineageReferenceOccurrence],
    dataflows: list[LineageDataflow],
    dependencies: dict[str, LineageDependency],
) -> list[LineageDiagnostic]:
    asset_by_id = {item["id"]: item for item in assets}
    diagnostics: list[LineageDiagnostic] = []
    for dataflow in dataflows:
        if dataflow.source_asset_id not in asset_by_id or dataflow.destination_asset_id not in asset_by_id:
            diagnostics.append(LineageDiagnostic(
                severity="error",
                code="dangling_dataflow",
                message=f"Dataflow has a missing endpoint: {dataflow.name}",
                dataflow_id=dataflow.dataflow_id,
                metadata_source_id=dataflow.metadata_source_id,
                details={"relation_id": dataflow.id},
            ))
    for dependency in dependencies.values():
        target = asset_by_id.get(dependency.target_asset_id)
        reference_exists = dependency.reference_id in references
        occurrence_exists = dependency.reference_occurrence_id in reference_occurrences
        resolved_asset_exists = (
            dependency.resolved_asset_id is None
            or dependency.resolved_asset_id in asset_by_id
        )
        if not reference_exists or not occurrence_exists or not resolved_asset_exists or target is None:
            diagnostics.append(LineageDiagnostic(
                severity="error",
                code="dangling_dependency",
                message="Dependency has a missing source or target",
                details={"relation_id": dependency.id},
            ))
        elif target.get("asset_type") not in {"sql_query", "python_function"}:
            diagnostics.append(LineageDiagnostic(
                severity="error",
                code="invalid_dependency_target",
                message="Dependency target must be a SQL query or Python function asset",
                asset_id=dependency.target_asset_id,
                details={"relation_id": dependency.id},
            ))
    return diagnostics


def _dataflow_diagnostics(dataflows: list[LineageDataflow]) -> list[LineageDiagnostic]:
    by_semantic_id: dict[str, list[LineageDataflow]] = {}
    for dataflow in dataflows:
        by_semantic_id.setdefault(dataflow.dataflow_id, []).append(dataflow)
    diagnostics = []
    for dataflow_id, occurrences in by_semantic_id.items():
        endpoints = {
            (item.source_asset_id, item.destination_asset_id)
            for item in occurrences
        }
        if len(endpoints) <= 1:
            continue
        diagnostics.append(LineageDiagnostic(
            severity="warning",
            code="dataflow_identity_conflict",
            message=f"Dataflow ID maps to multiple endpoint pairs: {dataflow_id}",
            dataflow_id=dataflow_id,
            details={
                "occurrence_ids": [item.id for item in occurrences],
                "endpoint_pairs": [
                    {"source_asset_id": source, "destination_asset_id": destination}
                    for source, destination in sorted(endpoints)
                ],
            },
        ))
    return diagnostics


def _summary(
    assets: list[dict[str, Any]],
    references: dict[str, LineageReference],
    dataflows: list[LineageDataflow],
    dependencies: dict[str, LineageDependency],
    diagnostics: list[Any],
) -> dict[str, int]:
    statuses = Counter(item.resolution_status for item in dependencies.values())
    return {
        "assets": len(assets),
        "references": len(references),
        "dataflows": len(dataflows),
        "dependencies": len(dependencies),
        "stitched_assets": sum(1 for item in assets if len(item.get("metadata_source_ids") or []) > 1),
        "declared_assets": sum(1 for item in assets if item["declaration_status"] == "declared"),
        "resolved_auto_dependencies": statuses["resolved_auto"],
        "resolved_dependencies": statuses["resolved_auto"] + statuses["resolved_manual"],
        "resolved_manual_dependencies": statuses["resolved_manual"],
        "ambiguous_dependencies": statuses["ambiguous"],
        "unresolved_dependencies": statuses["unresolved"],
        "mapping_target_missing_dependencies": statuses["mapping_target_missing"],
        "diagnostics": len(diagnostics),
    }


def _normalize_analysis_diagnostic(
    diagnostic: dict[str, Any],
    target_asset_id: str,
    dataflow: LineageDataflow,
) -> LineageDiagnostic:
    return LineageDiagnostic(
        severity=_severity(diagnostic.get("severity")),
        code=str(diagnostic.get("code") or "analysis_diagnostic"),
        message=str(diagnostic.get("message") or "Lineage analysis diagnostic"),
        asset_id=target_asset_id,
        dataflow_id=dataflow.dataflow_id,
        metadata_source_id=dataflow.metadata_source_id,
        details={
            key: value
            for key, value in diagnostic.items()
            if key not in {"severity", "code", "message"} and value is not None
        },
    )


def _normalize_diagnostic(item: dict[str, Any]) -> LineageDiagnostic:
    return LineageDiagnostic(
        severity=_severity(item.get("severity") or ("error" if item.get("type") else "warning")),
        code=str(item.get("code") or item.get("type") or "lineage_diagnostic"),
        message=str(item.get("message") or "Lineage diagnostic"),
        metadata_source_id=_optional_int(item.get("metadata_source_id")),
        details={
            key: value
            for key, value in item.items()
            if key not in {"severity", "code", "type", "message", "metadata_source_id"} and value is not None
        },
    )


def _append_observation(observations: list[dict[str, Any]], observation: dict[str, Any]) -> None:
    encoded = json.dumps(observation, sort_keys=True, default=str)
    if any(json.dumps(item, sort_keys=True, default=str) == encoded for item in observations):
        return
    if len(observations) < MAX_OBSERVATIONS:
        observations.append(observation)


def _merge_reference_identity(existing: LineageReference, candidate: LineageReference) -> None:
    if existing.resolved_asset_id is None and candidate.resolved_asset_id is not None:
        existing.resolved_asset_id = candidate.resolved_asset_id
    for asset_id in candidate.resolved_asset_ids:
        _add_unique(existing.resolved_asset_ids, asset_id)
    for candidate_asset_id in candidate.candidate_asset_ids:
        _add_unique(existing.candidate_asset_ids, candidate_asset_id, limit=MAX_CANDIDATES)


def _finalize_references(
    references: dict[str, LineageReference],
    dependencies: dict[str, LineageDependency],
) -> None:
    counts = Counter(item.reference_id for item in dependencies.values())
    statuses_by_reference: dict[str, list[str]] = {}
    for dependency in dependencies.values():
        statuses_by_reference.setdefault(dependency.reference_id, []).append(dependency.resolution_status)
    for reference in references.values():
        reference.dependency_count = int(counts.get(reference.id, 0))
        reference.resolved_asset_ids = sorted(set(reference.resolved_asset_ids))
        reference.candidate_asset_ids = sorted(set(reference.candidate_asset_ids))[:MAX_CANDIDATES]
        reference.occurrence_ids = sorted(set(reference.occurrence_ids))
        reference.consumer_asset_ids = sorted(set(reference.consumer_asset_ids))
        reference.provenances = sorted(set(reference.provenances))
        reference.group_status = _reference_group_status(statuses_by_reference.get(reference.id, []), reference.resolved_asset_ids)
        reference.resolved_asset_id = reference.resolved_asset_ids[0] if len(reference.resolved_asset_ids) == 1 else None


def _reference_group_status(statuses: list[str], resolved_asset_ids: list[str]) -> str:
    normalized = [_normalize_resolution_status(value) for value in statuses]
    if len(resolved_asset_ids) > 1:
        return "resolved_mixed"
    if resolved_asset_ids:
        return "resolved_single" if all(value in {"resolved_auto", "resolved_manual"} for value in normalized) else "partially_resolved"
    if "ambiguous" in normalized:
        return "ambiguous"
    if "mapping_target_missing" in normalized:
        return "mapping_target_missing"
    return "unresolved"


def _initial_group_status(status: str) -> str:
    normalized = _normalize_resolution_status(status)
    if normalized in {"resolved_auto", "resolved_manual"}:
        return "resolved_single"
    if normalized == "ambiguous":
        return "ambiguous"
    if normalized == "mapping_target_missing":
        return "mapping_target_missing"
    return "unresolved"


def _add_unique(values: list[str], value: str | None, *, limit: int | None = None) -> None:
    if not value or value in values:
        return
    if limit is not None and len(values) >= limit:
        return
    values.append(value)


def _dependency_id(reference_id: str, target_asset_id: str, kind: str) -> str:
    value = f"reference:{reference_id}:{target_asset_id}:{kind}"
    return f"dependency:{_digest(value)}"


def _merge_resolution_status(current: str, candidate: str) -> str:
    ranks = {
        "unresolved": 0,
        "mapping_target_missing": 1,
        "ambiguous": 2,
        "resolved_auto": 3,
        "resolved_manual": 4,
    }
    current_status = _normalize_resolution_status(current)
    candidate_status = _normalize_resolution_status(candidate)
    if ranks[candidate_status] >= ranks[current_status]:
        return candidate_status
    return current_status


def _normalize_resolution_status(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "resolved":
        return "resolved_auto"
    if normalized in {"resolved_auto", "resolved_manual", "ambiguous", "unresolved", "mapping_target_missing"}:
        return normalized
    return "unresolved"


def _provenance(value: str) -> str:
    if value == "sql":
        return "sql"
    if value == "python_sql":
        return "python_sql"
    return "python"


def _proven_context(evidence: Any) -> str:
    return "|".join(
        str(value or "").strip().lower()
        for value in (evidence.catalog, evidence.database, evidence.schema_name)
    )


def _source_location_dict(location: Any) -> dict[str, Any] | None:
    if location is None:
        return None
    if isinstance(location, dict):
        return {key: value for key, value in location.items() if value is not None}
    values = {
        key: getattr(location, key, None)
        for key in (
            "module",
            "path",
            "function_path",
            "line",
            "column",
            "end_line",
            "end_column",
            "coordinate_space",
        )
    }
    return {key: value for key, value in values.items() if value is not None} or None


def _source_location_key(location: Any) -> str:
    details = _source_location_dict(location)
    return json.dumps(details, sort_keys=True) if details else ""


def _is_unresolved_input_diagnostic(diagnostic: dict[str, Any]) -> bool:
    return str(diagnostic.get("code") or "") in {
        "dynamic_path",
        "dynamic_sql",
        "dynamic_temp_view",
        "dynamic_table",
    }


def _compact_reference_name(value: str) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= 80 else f"{compact[:77]}..."


def _severity(value: Any) -> str:
    normalized = str(value or "warning").lower()
    return normalized if normalized in {"info", "warning", "error"} else "warning"


def _optional_string(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:20]
