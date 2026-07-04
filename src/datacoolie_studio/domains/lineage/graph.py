from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

from datacoolie_studio.domains.assets.registry import AssetRegistry
from datacoolie_studio.domains.assets.resolver import Resolution
from datacoolie_studio.domains.lineage.models import (
    LineageDataflow,
    LineageDependency,
    LineageDiagnostic,
    LineageReference,
)


MAX_OBSERVATIONS = 20
MAX_CANDIDATES = 20


def build_typed_graph(
    registry: AssetRegistry,
    dataflows: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
    base_diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    typed_dataflows = _build_dataflows(registry, dataflows)
    references: dict[str, LineageReference] = {}
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
                    dependencies,
                )

    assets = [_present_asset(node) for node in registry.nodes()]
    diagnostics.extend(_validate_graph(assets, references, typed_dataflows, dependencies))
    invalid_codes = {"dangling_dataflow", "dangling_dependency", "invalid_dependency_target"}
    invalid_relation_ids = {
        str(item.details.get("relation_id"))
        for item in diagnostics
        if item.code in invalid_codes and item.details.get("relation_id")
    }
    typed_dataflows = [item for item in typed_dataflows if item.id not in invalid_relation_ids]
    dependencies = {key: item for key, item in dependencies.items() if key not in invalid_relation_ids}
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
    dependencies: dict[str, LineageDependency],
    diagnostics: list[LineageDiagnostic],
) -> None:
    evidence = resolution.evidence.to_dict()
    provenance = _provenance(resolution.evidence.provenance)
    if resolution.asset_id and resolution.status in {"resolved", "discovered_only"}:
        source = {"entity_type": "asset", "id": resolution.asset_id}
    else:
        reference = _reference_from_resolution(resolution, target_asset_id, provenance)
        references.setdefault(reference.id, reference)
        _append_observation(references[reference.id].observations, evidence)
        source = {"entity_type": "reference", "id": reference.id}
        diagnostics.append(LineageDiagnostic(
            severity="warning" if resolution.status == "ambiguous" else "info",
            code=f"dependency_{resolution.status}",
            message=f"{resolution.status.replace('_', ' ')} input: {resolution.evidence.value}",
            asset_id=target_asset_id,
            dataflow_id=dataflow.dataflow_id,
            metadata_source_id=dataflow.metadata_source_id,
            details={
                "reference_id": reference.id,
                "reason_code": resolution.method,
                "candidate_asset_ids": resolution.candidates[:MAX_CANDIDATES],
            },
        ))
    dependency_id = _dependency_id(source, target_asset_id, "reads")
    dependency = dependencies.get(dependency_id)
    if dependency is None:
        dependency = LineageDependency(
            id=dependency_id,
            source=source,
            target_asset_id=target_asset_id,
            kind="reads",
            provenance=provenance,
            resolution_status=resolution.status,
            resolution_method=resolution.method,
        )
        dependencies[dependency_id] = dependency
    _append_observation(dependency.observations, evidence)


def _add_unresolved_diagnostic_reference(
    diagnostic: dict[str, Any],
    target_asset_id: str,
    dataflow: LineageDataflow,
    references: dict[str, LineageReference],
    dependencies: dict[str, LineageDependency],
) -> None:
    raw_value = str(diagnostic.get("details", {}).get("expression") or diagnostic.get("message") or diagnostic["code"])
    provenance = "python" if str(diagnostic.get("code", "")).startswith(("dynamic_", "python_")) else "sql"
    key = f"{target_asset_id}:dynamic_expression:{provenance}:{raw_value.strip().lower()}"
    reference_id = f"reference:{_digest(key)}"
    observation = {
        "kind": "unknown",
        "value": raw_value,
        "provenance": provenance,
        "confidence": "unresolved",
        "location": diagnostic.get("location"),
        "details": diagnostic.get("details", {}),
    }
    reference = references.setdefault(reference_id, LineageReference(
        id=reference_id,
        kind="dynamic_expression",
        display_name=_compact_reference_name(raw_value),
        resolution_status="unresolved",
        raw_value=raw_value,
        provenance=provenance,
        target_asset_id=target_asset_id,
        reason_code=str(diagnostic.get("code") or "dynamic_expression"),
    ))
    _append_observation(reference.observations, observation)
    source = {"entity_type": "reference", "id": reference_id}
    dependency_id = _dependency_id(source, target_asset_id, "reads")
    dependency = dependencies.setdefault(dependency_id, LineageDependency(
        id=dependency_id,
        source=source,
        target_asset_id=target_asset_id,
        kind="reads",
        provenance=provenance,
        resolution_status="unresolved",
        resolution_method=str(diagnostic.get("code") or "dynamic_expression"),
    ))
    _append_observation(dependency.observations, observation)


def _reference_from_resolution(
    resolution: Resolution,
    target_asset_id: str,
    provenance: str,
) -> LineageReference:
    evidence = resolution.evidence
    kind = {
        "table": "table_reference",
        "path": "path_reference",
    }.get(evidence.kind, "unknown")
    key = ":".join([
        target_asset_id,
        kind,
        provenance,
        evidence.value.strip().lower(),
        _proven_context(evidence),
    ])
    return LineageReference(
        id=f"reference:{_digest(key)}",
        kind=kind,
        display_name=_compact_reference_name(evidence.value),
        resolution_status="ambiguous" if resolution.status == "ambiguous" else "unresolved",
        raw_value=evidence.value,
        provenance=provenance,
        target_asset_id=target_asset_id,
        candidate_asset_ids=resolution.candidates[:MAX_CANDIDATES],
        reason_code=resolution.method,
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
        "kind": kind,
        "display_name": node.get("display_label") or node.get("endpoint_locator") or node.get("label") or node["id"],
        "declaration_status": "declared" if metadata_source_ids else "discovered_only",
        "observations": observations,
    }


def _validate_graph(
    assets: list[dict[str, Any]],
    references: dict[str, LineageReference],
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
        source_type = dependency.source["entity_type"]
        source_id = dependency.source["id"]
        source_exists = source_id in asset_by_id if source_type == "asset" else source_id in references
        if not source_exists or target is None:
            diagnostics.append(LineageDiagnostic(
                severity="error",
                code="dangling_dependency",
                message="Dependency has a missing source or target",
                details={"relation_id": dependency.id},
            ))
        elif target.get("kind") not in {"sql_query", "python_function"}:
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
    diagnostics: list[dict[str, Any]],
) -> dict[str, int]:
    statuses = Counter(item.resolution_status for item in dependencies.values())
    return {
        "assets": len(assets),
        "references": len(references),
        "dataflows": len(dataflows),
        "dependencies": len(dependencies),
        "stitched_assets": sum(1 for item in assets if len(item.get("metadata_source_ids") or []) > 1),
        "declared_assets": sum(1 for item in assets if item["declaration_status"] == "declared"),
        "discovered_only_assets": sum(1 for item in assets if item["declaration_status"] == "discovered_only"),
        "resolved_dependencies": statuses["resolved"],
        "discovered_only_dependencies": statuses["discovered_only"],
        "ambiguous_dependencies": statuses["ambiguous"],
        "unresolved_dependencies": statuses["unresolved"],
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


def _dependency_id(source: dict[str, str], target_asset_id: str, kind: str) -> str:
    value = f"{source['entity_type']}:{source['id']}:{target_asset_id}:{kind}"
    return f"dependency:{_digest(value)}"


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


def _is_unresolved_input_diagnostic(diagnostic: dict[str, Any]) -> bool:
    return str(diagnostic.get("code") or "") in {
        "dynamic_path",
        "dynamic_sql",
        "dynamic_temp_view",
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
