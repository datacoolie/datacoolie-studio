from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from datacoolie_studio.db.models import (
    Environment,
    EnvironmentSource,
    CodeArtifactMaterialization,
    MetadataMaterialization,
    ProjectReferenceMapping,
)
from datacoolie_studio.domains.analysis.models import AnalysisResult
from datacoolie_studio.domains.analysis.service import analyze_code_artifact_function
from datacoolie_studio.domains.analysis.sql import analyze_sql, sql_dialect_for_source
from datacoolie_studio.domains.assets.resolver import AssetResolver
from datacoolie_studio.domains.assets.registry import AssetRegistry
from datacoolie_studio.domains.assets.manual_mapping import manual_mapping_from_observations
from datacoolie_studio.domains.assets.mapping_target import asset_mapping_target
from datacoolie_studio.domains.code_artifacts.service import (
    ANALYZER_VERSION,
    ensure_code_artifact_materialization,
)
from datacoolie_studio.domains.lineage.graph import build_typed_graph, build_typed_graph_summary
from datacoolie_studio.domains.metadata.normalizer import enrich_metadata_documents_with_connections
from datacoolie_studio.domains.read_models.cache import (
    cached_read_model,
    empty_parameters_fingerprint,
    fingerprint,
    read_model_build_lock,
    read_model_generation,
    replace_read_model,
)
from datacoolie_studio.domains.read_models.keys import LINEAGE_GRAPH


LINEAGE_ANALYZER_VERSION = f"lineage-v3:{ANALYZER_VERSION}:reference-resolution-v3-context-aware"
LINEAGE_RESPONSE_VERSION = "lineage-v4-compact"


@dataclass(frozen=True)
class _LineageInput:
    metadata_sources: list[EnvironmentSource]
    metadata_materializations: dict[int, object]
    code_artifacts: list[EnvironmentSource]
    reference_mappings: list[dict]
    fingerprint: str


def lineage_graph_etag(session: Session, environment_id: int) -> str:
    input_fingerprint = _load_lineage_input(session, environment_id).fingerprint
    return f'"{input_fingerprint}:{LINEAGE_ANALYZER_VERSION}:{LINEAGE_RESPONSE_VERSION}"'


def lineage_input_fingerprint(session: Session, environment_id: int) -> str:
    """Return the persisted structural version without loading materialization payloads."""
    return _load_lineage_input(session, environment_id).fingerprint


def project_lineage_graph(graph: dict) -> dict:
    return {
        "schema_version": "lineage.v4",
        "summary": graph.get("summary") or {},
        "assets": [_project_lineage_asset(item) for item in graph.get("assets") or []],
        "references": [_project_lineage_reference(item) for item in graph.get("references") or []],
        "dataflows": list(graph.get("dataflows") or []),
        "dependencies": [_project_lineage_dependency(item) for item in graph.get("dependencies") or []],
    }


def _project_lineage_asset(asset: dict) -> dict:
    excluded = {"query", "identifiers", "observations"}
    projected = {key: value for key, value in asset.items() if key not in excluded}
    projected["mapping_target"] = asset_mapping_target(list(asset.get("identifiers") or []), asset)
    return projected


def _project_lineage_reference(reference: dict) -> dict:
    excluded = {"occurrence_ids", "observations"}
    projected = {key: value for key, value in reference.items() if key not in excluded}
    projected["occurrence_count"] = len(reference.get("occurrence_ids") or [])
    projected["manual_mapping"] = manual_mapping_from_observations(reference.get("observations") or [])
    return projected


def _project_lineage_dependency(dependency: dict) -> dict:
    return {key: value for key, value in dependency.items() if key != "observations"}


def load_or_build_lineage_graph(
    session: Session,
    environment_id: int,
    *,
    input_fingerprint: str | None = None,
) -> dict:
    parameters_fingerprint = empty_parameters_fingerprint()
    build_key = f"{environment_id}:{LINEAGE_GRAPH}:{parameters_fingerprint}"

    if input_fingerprint is not None:
        cached = cached_read_model(
            session,
            environment_id=environment_id,
            model_key=LINEAGE_GRAPH,
            parameters_fingerprint=parameters_fingerprint,
            input_fingerprint=input_fingerprint,
            producer_version=LINEAGE_ANALYZER_VERSION,
        )
        if cached is not None:
            return cached.payload

    for attempt in range(2):
        lineage_input = _load_lineage_input(session, environment_id)
        cached = cached_read_model(
            session,
            environment_id=environment_id,
            model_key=LINEAGE_GRAPH,
            parameters_fingerprint=parameters_fingerprint,
            input_fingerprint=lineage_input.fingerprint,
            producer_version=LINEAGE_ANALYZER_VERSION,
        )
        if cached is not None:
            return cached.payload

        with read_model_build_lock(build_key):
            lineage_input = _load_lineage_input(session, environment_id)
            cached = cached_read_model(
                session,
                environment_id=environment_id,
                model_key=LINEAGE_GRAPH,
                parameters_fingerprint=parameters_fingerprint,
                input_fingerprint=lineage_input.fingerprint,
                producer_version=LINEAGE_ANALYZER_VERSION,
            )
            if cached is not None:
                return cached.payload
            generation = read_model_generation(
                environment_id=environment_id,
                model_key=LINEAGE_GRAPH,
                parameters_fingerprint=parameters_fingerprint,
                input_fingerprint=lineage_input.fingerprint,
                producer_version=LINEAGE_ANALYZER_VERSION,
            )

            lineage_input = _load_lineage_input(session, environment_id, include_materialization_payload=True)
            graph = build_lineage(
                _materialized_metadata(lineage_input.metadata_sources, lineage_input.metadata_materializations),
                environment_id,
                lineage_input.code_artifacts,
                reference_mappings=lineage_input.reference_mappings,
            )
            current_input = _load_lineage_input(session, environment_id)
            if current_input.fingerprint != lineage_input.fingerprint:
                if attempt == 0:
                    continue
                # Do not publish a graph built from an obsolete source version.
                current_input = _load_lineage_input(
                    session,
                    environment_id,
                    include_materialization_payload=True,
                )
                return build_lineage(
                    _materialized_metadata(current_input.metadata_sources, current_input.metadata_materializations),
                    environment_id,
                    current_input.code_artifacts,
                    reference_mappings=current_input.reference_mappings,
                )
            replace_read_model(
                session,
                environment_id=environment_id,
                model_key=LINEAGE_GRAPH,
                parameters_fingerprint=parameters_fingerprint,
                input_fingerprint=lineage_input.fingerprint,
                producer_version=LINEAGE_ANALYZER_VERSION,
                payload=graph,
                expected_generation=generation,
            )
            return graph

    raise RuntimeError("Unable to build a stable lineage read model")


def build_lineage(
    metadata: dict,
    environment_id: int = 0,
    code_artifacts: list[EnvironmentSource] | None = None,
    reference_mappings: list[dict] | None = None,
) -> dict:
    registry = AssetRegistry(environment_id)
    documents = enrich_metadata_documents_with_connections(metadata.get("_documents", []))
    dataflows = [
        item
        for document in documents
        for item in document.get("dataflows", [])
    ] if documents else metadata.get("dataflows", [])
    for document in documents:
        source_id = int(document["source"]["id"])
        source_uri = str(document["source"]["uri"])
        for node in document.get("nodes", []):
            registry.add_metadata_asset(node, source_id, source_uri)
    resolver = AssetResolver(registry, reference_mappings=reference_mappings)
    analyses: list[dict] = []
    for dataflow in dataflows:
        analysis = _analyze_dataflow_source(dataflow, code_artifacts or [])
        context = _source_context(dataflow.get("source") or {})
        resolutions = []
        for evidence in analysis.inputs:
            resolution = resolver.resolve(evidence, context)
            resolutions.append(resolution)
        analyses.append({
            "resolutions": resolutions,
            "diagnostics": analysis.diagnostics,
        })
    return build_typed_graph(
        registry,
        dataflows,
        analyses,
        metadata.get("errors", []),
    )


def build_lineage_overview_summary(
    session: Session,
    metadata: dict,
    environment_id: int,
    code_artifacts: list[EnvironmentSource],
    reference_mappings: list[dict] | None = None,
) -> dict:
    """Calculate only the lineage aggregates rendered by Environment Overview.

    This deliberately shares analysis and graph-validation rules with the full
    Lineage module, while avoiding full graph serialization and transport.
    """
    for source in code_artifacts:
        if source.enabled:
            ensure_code_artifact_materialization(session, source)

    registry = AssetRegistry(environment_id)
    documents = enrich_metadata_documents_with_connections(metadata.get("_documents", []))
    dataflows = [
        item
        for document in documents
        for item in document.get("dataflows", [])
    ] if documents else metadata.get("dataflows", [])
    for document in documents:
        source_id = int(document["source"]["id"])
        source_uri = str(document["source"]["uri"])
        for node in document.get("nodes", []):
            registry.add_metadata_asset(node, source_id, source_uri)

    resolver = AssetResolver(registry, reference_mappings=reference_mappings)
    analyses: list[dict] = []
    for dataflow in dataflows:
        analysis = _analyze_dataflow_source(dataflow, code_artifacts)
        context = _source_context(dataflow.get("source") or {})
        analyses.append({
            "resolutions": [resolver.resolve(evidence, context) for evidence in analysis.inputs],
            "diagnostics": analysis.diagnostics,
        })
    return build_typed_graph_summary(
        registry,
        dataflows,
        analyses,
        metadata.get("errors", []),
    )


def _analyze_dataflow_source(
    dataflow: dict,
    code_artifacts: list[EnvironmentSource],
) -> AnalysisResult:
    source = dataflow.get("source") or {}
    query = source.get("query")
    if isinstance(query, str) and query.strip():
        return analyze_sql(query, dialect=sql_dialect_for_source(source))
    function_path = source.get("python_function")
    if not isinstance(function_path, str) or not function_path.strip():
        return AnalysisResult()

    matches: list[AnalysisResult] = []
    unavailable = []
    context = {"source": _python_source_context(source)}
    for artifact in code_artifacts:
        if not artifact.enabled:
            continue
        result = analyze_code_artifact_function(artifact, function_path, context=context)
        if any(item["code"] == "artifact_function_unavailable" for item in result.diagnostics):
            unavailable.extend(result.diagnostics)
            continue
        matches.append(result)
    if not matches:
        result = AnalysisResult()
        result.diagnostics.extend(unavailable or [{
            "severity": "warning",
            "code": "code_artifact_not_configured",
            "message": f"No enabled code artifact can resolve {function_path}",
        }])
        return result
    result = matches[0]
    if len(matches) > 1:
        result.diagnostics.append({
            "severity": "warning",
            "code": "python_function_ambiguous",
            "message": f"Function exists in {len(matches)} code artifacts: {function_path}",
        })
    return result


def _source_context(source: dict) -> dict:
    return {
        key: source.get(key)
        for key in (
            "connection_name",
            "connection_instance",
            "connection_type",
            "format",
            "catalog",
            "database",
            "database_type",
            "host",
            "port",
            "workspace_id",
            "base_path",
            "base_url",
            "schema_name",
        )
    }


def _python_source_context(source: dict) -> dict:
    return {
        **source,
        "connection": {
            "catalog": source.get("catalog"),
            "database": source.get("database"),
            "configure": {
                "base_path": source.get("base_path"),
                "catalog": source.get("catalog"),
                "database": source.get("database"),
            },
        },
    }


def _load_lineage_input(
    session: Session,
    environment_id: int,
    *,
    include_materialization_payload: bool = False,
) -> _LineageInput:
    environment = session.get(Environment, environment_id)
    if environment is None:
        raise ValueError(f"Environment not found: {environment_id}")
    sources = list(
        session.scalars(
            select(EnvironmentSource)
            .where(EnvironmentSource.environment_id == environment_id, EnvironmentSource.enabled.is_(True))
            .order_by(EnvironmentSource.id)
        )
    )
    metadata_sources = [source for source in sources if source.source_kind == "metadata"]
    code_artifacts = [source for source in sources if source.source_kind == "code"]
    metadata_materializations = _materializations(
        session,
        MetadataMaterialization,
        [item.id for item in metadata_sources],
        include_payload=include_materialization_payload,
    )
    code_materializations = _materializations(
        session,
        CodeArtifactMaterialization,
        [item.id for item in code_artifacts],
        include_payload=include_materialization_payload,
    )
    reference_mappings = _load_project_reference_mappings(session, environment.project_id)
    input_fingerprint = fingerprint({
        "environment_id": environment_id,
        "sources": [
            {
                "id": source.id,
                "kind": source.source_kind,
                "uri": source.uri,
                "config": source.source_config_json,
                "materialization_fingerprint": _source_materialization(
                    source, metadata_materializations, code_materializations
                ).materialization_fingerprint
                if _source_materialization(source, metadata_materializations, code_materializations)
                else None,
            }
            for source in sources
        ],
        "reference_mappings": reference_mappings,
        "analyzer_version": LINEAGE_ANALYZER_VERSION,
    })
    return _LineageInput(
        metadata_sources,
        metadata_materializations,
        code_artifacts,
        reference_mappings,
        input_fingerprint,
    )


def _source_materialization(
    source: EnvironmentSource,
    metadata_materializations: dict,
    code_materializations: dict,
):
    return (
        metadata_materializations
        if source.source_kind == "metadata"
        else code_materializations
    ).get(source.id)


def _materializations(
    session: Session,
    model,
    source_ids: list[int],
    *,
    include_payload: bool,
) -> dict[int, object]:
    if not source_ids:
        return {}
    statement = select(model).where(model.source_id.in_(source_ids)).order_by(model.source_id)
    if not include_payload:
        statement = statement.options(
            load_only(
                model.id,
                model.source_id,
                model.source_revision_json,
                model.materialization_fingerprint,
                model.materialized_at,
            )
        )
    return {
        int(materialization.source_id): materialization
        for materialization in session.scalars(statement)
    }


def _materialized_metadata(
    sources: list[EnvironmentSource],
    materializations: dict[int, object],
) -> dict:
    documents = []
    errors = []
    for source in sources:
        materialization = materializations.get(source.id)
        if materialization is None:
            errors.append({
                "metadata_source_id": source.id,
                "uri": source.uri,
                "message": "Metadata source has not been synchronized yet",
            })
            continue
        try:
            normalized = json.loads(materialization.normalized_metadata_json or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            errors.append({"metadata_source_id": source.id, "uri": source.uri, "message": str(exc)})
            continue
        if normalized:
            documents.append(normalized)
    documents = enrich_metadata_documents_with_connections(documents)
    connections = [item for document in documents for item in document.get("connections", [])]
    dataflows = [item for document in documents for item in document.get("dataflows", [])]
    schema_hints = [item for document in documents for item in document.get("schema_hints", [])]
    return {
        "summary": {
            "sources": len(documents),
            "connections": len(connections),
            "dataflows": len(dataflows),
            "schema_hints": len(schema_hints),
            "errors": len(errors),
        },
        "sources": [document["source"] for document in documents],
        "connections": connections,
        "dataflows": dataflows,
        "schema_hints": schema_hints,
        "errors": errors,
        "_documents": documents,
    }


def _load_project_reference_mappings(session: Session, project_id: int) -> list[dict[str, str | int | None]]:
    statement = (
        select(ProjectReferenceMapping)
        .where(ProjectReferenceMapping.project_id == project_id)
        .order_by(ProjectReferenceMapping.updated_at.desc(), ProjectReferenceMapping.id.desc())
    )
    rows = []
    for item in session.scalars(statement):
        rows.append(
            {
                "id": item.id,
                "reference_type": item.reference_type,
                "reference_normalized_value": item.reference_normalized_value,
                "target_identifier_kind": item.target_identifier_kind,
                "target_normalized_value": item.target_normalized_value,
                "note": item.note,
                "updated_at": item.updated_at,
            }
        )
    return rows
