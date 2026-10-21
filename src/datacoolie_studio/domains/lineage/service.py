from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from datacoolie_studio.db.models import (
    Environment,
    EnvironmentSource,
    CodeArtifactMaterialization,
    MetadataMaterialization,
)
from datacoolie_studio.domains.analysis.models import AnalysisResult
from datacoolie_studio.domains.analysis.service import analyze_code_artifact_function
from datacoolie_studio.domains.analysis.sql import analyze_sql, sql_dialect_for_source
from datacoolie_studio.domains.assets.resolver import AssetResolver
from datacoolie_studio.domains.assets.registry import AssetRegistry
from datacoolie_studio.domains.assets.endpoint_identity import ASSETS_ENDPOINT_IDENTITY_VERSION
from datacoolie_studio.domains.assets.manual_mapping import manual_mapping_from_observations
from datacoolie_studio.domains.assets.mapping_target import asset_mapping_target
from datacoolie_studio.domains.code_artifacts.service import (
    ANALYZER_VERSION,
    ensure_code_artifact_materialization_result,
)
from datacoolie_studio.domains.lineage.graph import build_typed_graph, build_typed_graph_summary
from datacoolie_studio.domains.metadata.environment_documents import (
    environment_document_for_source,
)
from datacoolie_studio.domains.metadata.drafts import load_environment_editor_draft
from datacoolie_studio.domains.metadata.document_normalization import (
    editor_document_to_raw_metadata,
    metadata_payload,
)
from datacoolie_studio.domains.metadata.normalizer import (
    enrich_metadata_documents_with_connections,
    normalize_metadata_document,
)
from datacoolie_studio.domains.sql.reader import (
    SqlReferenceError,
    resolve_sql_file_target,
    resolve_sql_reference,
)
from datacoolie_studio.domains.read_models.cache import (
    cached_read_model,
    empty_parameters_fingerprint,
    fingerprint,
    read_model_build_lock,
    read_model_generation,
    replace_read_model,
)
from datacoolie_studio.domains.read_models.keys import LINEAGE_GRAPH
from datacoolie_studio.domains.reference_mappings import repository as reference_mapping_repository


LINEAGE_ANALYZER_VERSION = (
    f"lineage-v5:{ANALYZER_VERSION}:sql-reference-v1-context-aware:"
    f"{ASSETS_ENDPOINT_IDENTITY_VERSION}"
)
LINEAGE_RESPONSE_VERSION = "lineage-v4-compact"


@dataclass(frozen=True)
class _LineageInput:
    metadata_sources: list[EnvironmentSource]
    metadata_materializations: dict[int, object]
    code_artifacts: list[EnvironmentSource]
    reference_mappings: list[dict]
    fingerprint: str
    draft_document: dict | None = None


def lineage_graph_etag(session: Session, environment_id: int) -> str:
    input_fingerprint = _load_lineage_input(session, environment_id).fingerprint
    return f'"{input_fingerprint}:{LINEAGE_ANALYZER_VERSION}:{LINEAGE_RESPONSE_VERSION}"'


def draft_lineage_graph_etag(session: Session, environment_id: int) -> str:
    """Return an ETag for the editor's composed, draft-only lineage view."""

    input_fingerprint = _load_lineage_input(
        session,
        environment_id,
        include_draft=True,
    ).fingerprint
    return f'"{input_fingerprint}:{LINEAGE_ANALYZER_VERSION}:{LINEAGE_RESPONSE_VERSION}:draft"'


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
    include_draft: bool = False,
) -> dict:
    parameters_fingerprint = empty_parameters_fingerprint()
    model_key = f"{LINEAGE_GRAPH}:draft" if include_draft else LINEAGE_GRAPH
    build_key = f"{environment_id}:{model_key}:{parameters_fingerprint}"

    if input_fingerprint is not None:
        cached = cached_read_model(
            environment_id=environment_id,
            model_key=model_key,
            parameters_fingerprint=parameters_fingerprint,
            input_fingerprint=input_fingerprint,
            producer_version=LINEAGE_ANALYZER_VERSION,
        )
        if cached is not None:
            return cached.payload

    for attempt in range(2):
        lineage_input = _load_lineage_input(
            session,
            environment_id,
            include_draft=include_draft,
        )
        cached = cached_read_model(
            environment_id=environment_id,
            model_key=model_key,
            parameters_fingerprint=parameters_fingerprint,
            input_fingerprint=lineage_input.fingerprint,
            producer_version=LINEAGE_ANALYZER_VERSION,
        )
        if cached is not None:
            return cached.payload

        with read_model_build_lock(build_key):
            lineage_input = _load_lineage_input(
                session,
                environment_id,
                include_draft=include_draft,
            )
            cached = cached_read_model(
                environment_id=environment_id,
                model_key=model_key,
                parameters_fingerprint=parameters_fingerprint,
                input_fingerprint=lineage_input.fingerprint,
                producer_version=LINEAGE_ANALYZER_VERSION,
            )
            if cached is not None:
                return cached.payload
            generation = read_model_generation(
                environment_id=environment_id,
                model_key=model_key,
                parameters_fingerprint=parameters_fingerprint,
                input_fingerprint=lineage_input.fingerprint,
                producer_version=LINEAGE_ANALYZER_VERSION,
            )

            lineage_input = _load_lineage_input(
                session,
                environment_id,
                include_materialization_payload=True,
                include_draft=include_draft,
            )
            graph = build_lineage(
                _materialized_metadata(
                    session,
                    lineage_input.metadata_sources,
                    lineage_input.metadata_materializations,
                    draft_document=lineage_input.draft_document,
                ),
                environment_id,
                lineage_input.code_artifacts,
                reference_mappings=lineage_input.reference_mappings,
            )
            current_input = _load_lineage_input(
                session,
                environment_id,
                include_draft=include_draft,
            )
            if current_input.fingerprint != lineage_input.fingerprint:
                if attempt == 0:
                    continue
                # Do not publish a graph built from an obsolete source version.
                current_input = _load_lineage_input(
                    session,
                    environment_id,
                    include_materialization_payload=True,
                    include_draft=include_draft,
                )
                return build_lineage(
                    _materialized_metadata(
                        session,
                        current_input.metadata_sources,
                        current_input.metadata_materializations,
                        draft_document=current_input.draft_document,
                    ),
                    environment_id,
                    current_input.code_artifacts,
                    reference_mappings=current_input.reference_mappings,
                )
            replace_read_model(
                environment_id=environment_id,
                model_key=model_key,
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
    metadata_observations = [
        (node, int(document["source"]["id"]), str(document["source"]["uri"]))
        for document in documents
        for node in document.get("nodes", [])
        if isinstance(node, dict)
    ]
    registry.add_metadata_observations(metadata_observations)
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
            _materialization, warning = ensure_code_artifact_materialization_result(
                session, source
            )
            if warning:
                metadata.setdefault("errors", []).append({
                    "metadata_source_id": source.id,
                    "uri": source.uri,
                    **warning,
                    "cache_status": "stale",
                })

    registry = AssetRegistry(environment_id)
    documents = enrich_metadata_documents_with_connections(metadata.get("_documents", []))
    dataflows = [
        item
        for document in documents
        for item in document.get("dataflows", [])
    ] if documents else metadata.get("dataflows", [])
    metadata_observations = [
        (node, int(document["source"]["id"]), str(document["source"]["uri"]))
        for document in documents
        for node in document.get("nodes", [])
        if isinstance(node, dict)
    ]
    registry.add_metadata_observations(metadata_observations)

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
    query_error = source.get("query_resolution_error")
    if query_error:
        return AnalysisResult(diagnostics=[{
            "severity": "error",
            "code": "sql_file_unavailable",
            "message": str(query_error),
        }])
    query_content = source.get("query_content")
    query = query_content if isinstance(query_content, str) and query_content.strip() else source.get("query")
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
    include_draft: bool = False,
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
    draft_document = (
        load_environment_editor_draft(session, environment_id)
        if include_draft
        else None
    )
    reference_mappings = reference_mapping_repository.list_project_reference_mappings(
        session,
        environment.project_id,
    )
    sql_references = _load_sql_reference_fingerprints(
        session,
        metadata_sources,
        metadata_materializations,
    )
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
        "sql_references": sql_references,
        "reference_mappings": reference_mappings,
        "draft": draft_document if include_draft else None,
        "analyzer_version": LINEAGE_ANALYZER_VERSION,
    })
    return _LineageInput(
        metadata_sources,
        metadata_materializations,
        code_artifacts,
        reference_mappings,
        input_fingerprint,
        draft_document,
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
        columns = [
            model.id,
            model.source_id,
            model.source_revision_json,
            model.materialization_fingerprint,
            model.materialized_at,
        ]
        # The authored query references live SQL files, so the lightweight
        # lineage fingerprint still needs the editor rows.  It deliberately
        # does not load the normalized graph payload here.
        if model is MetadataMaterialization:
            columns.append(model.editor_document_json)
        statement = statement.options(load_only(*columns))
    return {
        int(materialization.source_id): materialization
        for materialization in session.scalars(statement)
    }


def _load_sql_reference_fingerprints(
    session: Session,
    sources: list[EnvironmentSource],
    materializations: dict[int, object],
) -> list[dict[str, object]]:
    """Capture live SQL dependencies without putting SQL text in a cache key.

    Metadata materialization revisions only change when the metadata source is
    synchronized.  A file-backed ``source_query`` can change independently, so
    its storage revision/content digest must be part of the lineage input key.
    Resolution failures are included as well; this prevents a stale successful
    graph from being served after a referenced SQL file disappears.
    """

    references: list[dict[str, object]] = []
    for source in sources:
        materialization = materializations.get(source.id)
        editor_json = getattr(materialization, "editor_document_json", None)
        if not isinstance(editor_json, str):
            continue
        try:
            editor = json.loads(editor_json)
        except (TypeError, json.JSONDecodeError):
            continue
        rows = (
            ((editor.get("sheets") or {}).get("dataflows") or {}).get("rows")
            if isinstance(editor, dict)
            else None
        )
        if not isinstance(rows, list):
            continue
        for row_index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            query = row.get("source_query")
            if not isinstance(query, str) or not query.strip():
                continue
            item: dict[str, object] = {
                "source_id": source.id,
                "row_index": row_index,
                "query": query,
            }
            try:
                resolved = resolve_sql_reference(session, source, query)
            except SqlReferenceError as exc:
                item.update({"status": "error", "error": str(exc)})
            else:
                kind = str(resolved.get("kind") or "inline")
                item["kind"] = kind
                if kind == "file":
                    item.update({
                        "file_uri": resolved.get("file_uri"),
                        "base_uri": resolved.get("base_uri"),
                        "base_kind": resolved.get("base_kind"),
                        "relative_path": resolved.get("relative_path"),
                        "revision": _sql_revision_fingerprint(
                            resolved.get("revision")
                        ),
                    })
                else:
                    content = resolved.get("content")
                    item["content_digest"] = hashlib.sha256(
                        str(content or "").encode("utf-8")
                    ).hexdigest()
            references.append(item)
    return references


def _sql_revision_fingerprint(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    # Keep the cache input stable and metadata-only.  The content hash is used
    # when available; provider revision/size/mtime cover adapters that do not
    # expose a hash in their first read response.
    return {
        key: value.get(key)
        for key in (
            "canonical_uri",
            "size",
            "mtime_ns",
            "last_modified",
            "provider_revision",
            "content_hash",
        )
        if value.get(key) is not None
    }


def _materialized_metadata(
    session: Session | None,
    sources: list[EnvironmentSource],
    materializations: dict[int, object],
    *,
    draft_document: dict | None = None,
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
            if draft_document is not None:
                # Keep the environment draft separate from source metadata.  A
                # source-scoped projection is rebuilt from the draft rows only
                # for preview; the persisted materialization remains untouched.
                base_editor_document = json.loads(
                    materialization.editor_document_json or "{}"
                )
                source_editor_document = environment_document_for_source(
                    draft_document,
                    base_editor_document,
                    source,
                )
                normalized = normalize_metadata_document(
                    source.id,
                    source.uri,
                    editor_document_to_raw_metadata(source_editor_document),
                )
            else:
                # Reproject published editor rows with the current Assets
                # policy. Old normalized materializations may have collapsed
                # duplicate connections before shared identity was introduced.
                normalized = normalize_metadata_document(
                    source.id, source.uri,
                    metadata_payload(editor_document_to_raw_metadata(
                        json.loads(materialization.editor_document_json or "{}")
                    )),
                )
        except (TypeError, json.JSONDecodeError) as exc:
            errors.append({"metadata_source_id": source.id, "uri": source.uri, "message": str(exc)})
            continue
        if normalized:
            _attach_sql_content(
                session,
                source,
                normalized,
                pending_sql_files=(draft_document or {}).get("sql_files")
                if draft_document is not None
                else None,
            )
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


def _attach_sql_content(
    session: Session | None,
    source: EnvironmentSource,
    document: dict,
    pending_sql_files: list[dict] | None = None,
) -> None:
    """Resolve file-backed SQL before lineage analysis while keeping authored
    ``query`` values unchanged for identity and round-tripping.
    """

    source_info = document.setdefault("source", {})
    try:
        config = json.loads(source.source_config_json or "{}")
    except (TypeError, json.JSONDecodeError):
        config = {}
    if isinstance(config, dict):
        for key in (
            "project_uri",
            "sql_root_uri",
            "sql_root_uris",
            "functions_root_uri",
            "functions_root_uris",
            "metadata_root_uri",
            "metadata_root_uris",
            "component_paths",
            "component_paths_all",
            "component_descriptors",
            "config_uri",
            "config_found",
            "config_revision",
            "manifest_uri",
            "manifest_found",
            "directive_kind",
            "directive_uri",
            "directive_revision",
            "component_path_provenance",
            "effective_component_path_provenance",
            "component_resolved_from",
            "component_path_diagnostics",
        ):
            if key in config:
                source_info[key] = config[key]
    if session is None:
        return
    pending_by_query, pending_by_uri = _pending_sql_overlays(
        session,
        source,
        pending_sql_files or [],
    ) if pending_sql_files else ({}, {})
    for dataflow in document.get("dataflows") or []:
        endpoint = dataflow.get("source") or {}
        query = endpoint.get("query")
        if not isinstance(query, str) or not query.strip():
            continue
        pending = pending_by_query.get(query.strip())
        target = None
        if pending is None:
            try:
                target = resolve_sql_file_target(
                    source,
                    query,
                    session=session,
                )
            except SqlReferenceError:
                target = None
            if target is not None:
                pending = pending_by_uri.get(str(target.get("file_uri")))
        if pending is not None:
            try:
                target = target or resolve_sql_file_target(
                    source,
                    query,
                    session=session,
                )
            except SqlReferenceError as exc:
                endpoint["query_resolution_error"] = str(exc)
                endpoint.pop("query_content", None)
                continue
            content = pending.get("content")
            if not isinstance(content, str) or not content.strip():
                endpoint["query_resolution_error"] = "Staged SQL content is empty"
                endpoint.pop("query_content", None)
                continue
            endpoint.pop("query_resolution_error", None)
            endpoint["query_content"] = content
            endpoint["query_file"] = {
                key: target.get(key)
                for key in (
                    "file_uri",
                    "base_uri",
                    "base_kind",
                    "relative_path",
                    "config_revision",
                )
            }
            endpoint["query_file"]["revision"] = (
                pending.get("published_revision")
                or pending.get("base_revision")
            )
            endpoint["query_file"]["draft_only"] = True
            endpoint["query_file"]["publish_status"] = pending.get(
                "publish_status",
                "pending",
            )
            continue
        try:
            resolved = resolve_sql_reference(session, source, query)
        except SqlReferenceError as exc:
            endpoint["query_resolution_error"] = str(exc)
            endpoint.pop("query_content", None)
            continue
        endpoint.pop("query_resolution_error", None)
        endpoint["query_content"] = resolved.get("content", "")
        if resolved.get("kind") == "file":
            endpoint["query_file"] = {
                key: resolved.get(key)
                for key in (
                    "file_uri",
                    "base_uri",
                    "base_kind",
                    "relative_path",
                    "revision",
                )
            }


def _pending_sql_overlays(
    session: Session,
    source: EnvironmentSource,
    operations: list[dict],
) -> tuple[dict[str, dict], dict[str, dict]]:
    by_query: dict[str, dict] = {}
    by_uri: dict[str, dict] = {}
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        try:
            if int(operation.get("source_id")) != source.id:
                continue
        except (TypeError, ValueError):
            continue
        query = operation.get("query")
        if not isinstance(query, str) or not query.strip():
            continue
        normalized_query = query.strip()
        by_query[normalized_query] = operation
        try:
            target = resolve_sql_file_target(
                source,
                normalized_query,
                session=session,
            )
        except SqlReferenceError:
            continue
        file_uri = target.get("file_uri")
        if isinstance(file_uri, str) and file_uri:
            by_uri[file_uri] = operation
    return by_query, by_uri
