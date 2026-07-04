from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import (
    EnvironmentSource,
    LineageSnapshot,
    MetadataSourceSnapshot,
)
from datacoolie_studio.domains.analysis.models import AnalysisResult
from datacoolie_studio.domains.analysis.service import analyze_code_artifact_function
from datacoolie_studio.domains.analysis.sql import analyze_sql
from datacoolie_studio.domains.assets.resolver import AssetResolver
from datacoolie_studio.domains.assets.registry import AssetRegistry
from datacoolie_studio.domains.code_artifacts.service import (
    ANALYZER_VERSION,
    ensure_code_artifact_snapshot,
)
from datacoolie_studio.domains.lineage.graph import build_typed_graph
from datacoolie_studio.domains.metadata.normalizer import enrich_metadata_documents_with_connections


LINEAGE_ANALYZER_VERSION = f"lineage-v2:{ANALYZER_VERSION}:typed-graph-v5"


def load_or_build_lineage(
    session: Session,
    metadata: dict,
    environment_id: int,
    code_artifacts: list[EnvironmentSource],
) -> dict:
    code_snapshots = [
        snapshot
        for source in code_artifacts
        if source.enabled
        for snapshot in [ensure_code_artifact_snapshot(session, source)]
        if snapshot is not None
    ]
    fingerprint = _lineage_fingerprint(session, metadata, environment_id, code_snapshots)
    cached = session.scalars(
        select(LineageSnapshot)
        .where(
            LineageSnapshot.environment_id == environment_id,
            LineageSnapshot.input_fingerprint == fingerprint,
            LineageSnapshot.analyzer_version == LINEAGE_ANALYZER_VERSION,
        )
        .order_by(LineageSnapshot.created_at.desc(), LineageSnapshot.id.desc())
    ).first()
    if cached is not None:
        return json.loads(cached.graph_json)
    graph = build_lineage(metadata, environment_id, code_artifacts)
    session.add(LineageSnapshot(
        environment_id=environment_id,
        input_fingerprint=fingerprint,
        graph_json=json.dumps(graph, ensure_ascii=False),
        analyzer_version=LINEAGE_ANALYZER_VERSION,
    ))
    session.commit()
    return graph


def build_lineage(
    metadata: dict,
    environment_id: int = 0,
    code_artifacts: list[EnvironmentSource] | None = None,
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
    resolver = AssetResolver(registry)
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


def _analyze_dataflow_source(
    dataflow: dict,
    code_artifacts: list[EnvironmentSource],
) -> AnalysisResult:
    source = dataflow.get("source") or {}
    query = source.get("query")
    if isinstance(query, str) and query.strip():
        return analyze_sql(query)
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
            "connection_type",
            "format",
            "catalog",
            "database",
            "database_type",
            "host",
            "port",
            "workspace_id",
            "base_path",
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


def _lineage_fingerprint(
    session: Session,
    metadata: dict,
    environment_id: int,
    code_snapshots: list,
) -> str:
    metadata_source_ids = sorted(
        int(item["id"])
        for item in metadata.get("sources", [])
    )
    metadata_revisions = []
    for source_id in metadata_source_ids:
        snapshot = session.scalars(
            select(MetadataSourceSnapshot)
            .where(MetadataSourceSnapshot.source_id == source_id)
            .order_by(MetadataSourceSnapshot.created_at.desc(), MetadataSourceSnapshot.id.desc())
        ).first()
        metadata_revisions.append({
            "source_id": source_id,
            "snapshot_id": snapshot.id if snapshot else None,
            "revision": snapshot.source_revision_json if snapshot else None,
        })
    payload = {
        "environment_id": environment_id,
        "metadata": metadata_revisions,
        "code": [
            {"source_id": item.source_id, "revision": item.source_revision_json}
            for item in sorted(code_snapshots, key=lambda value: value.source_id)
        ],
        "analyzer_version": LINEAGE_ANALYZER_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
