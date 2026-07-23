from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import (
    CodeArtifactMaterialization,
    Environment,
    EnvironmentSource,
    LogFileManifest,
    MetadataMaterialization,
    ProjectReferenceMapping,
    SourceRevision,
)
from datacoolie_studio.db.session import create_session
from datacoolie_studio.domains.lineage.service import build_lineage_overview_summary
from datacoolie_studio.domains.logs.cache import AnalyticsRebuildRequired
from datacoolie_studio.domains.metadata.service import load_environment_metadata
from datacoolie_studio.domains.monitoring import service as monitoring
from datacoolie_studio.domains.studio_settings import service as studio_settings
from datacoolie_studio.domains.workspace import service as workspace
from datacoolie_studio.domains.read_models.cache import (
    cached_read_model,
    fingerprint,
    read_model_build_lock,
    read_model_generation,
    replace_read_model,
)
from datacoolie_studio.domains.read_models.keys import OVERVIEW


OVERVIEW_SUMMARY_KEY = OVERVIEW
OVERVIEW_CALCULATOR_VERSION = "environment-overview-v2-reference-resolution"
OVERVIEW_RANGE = "30d"


def load_environment_overview(session: Session, environment_id: int) -> dict[str, Any]:
    """Return the narrow Environment Overview read model.

    Cache reuse is based on persisted source revisions and manifests, never a
    guessed time-to-live. A caller receives the last successfully collected
    view until a source refresh, configuration mutation, mapping mutation, or
    daily failure-window boundary changes the fingerprint.
    """
    environment = session.get(Environment, environment_id)
    if environment is None:
        raise LookupError("Environment not found")
    timezone_context = studio_settings.studio_timezone_context(session)
    parameters = {
        "range": OVERVIEW_RANGE,
        "timezone": timezone_context["timezone"],
        "window_anchor": datetime.now(timezone_context["timezone_info"]).date().isoformat(),
    }
    parameters_fingerprint = fingerprint(parameters)

    input_fingerprint = overview_input_fingerprint(session, environment, timezone_context["timezone"])
    cached = cached_read_model(
        session,
        environment_id=environment_id,
        model_key=OVERVIEW_SUMMARY_KEY,
        parameters_fingerprint=parameters_fingerprint,
        input_fingerprint=input_fingerprint,
        producer_version=OVERVIEW_CALCULATOR_VERSION,
    )
    if cached is not None:
        return _cached_response(cached.payload, cached.computed_at)

    cache_key = ":".join((
        str(environment_id),
        OVERVIEW_SUMMARY_KEY,
        parameters_fingerprint,
        input_fingerprint,
        OVERVIEW_CALCULATOR_VERSION,
    ))
    with read_model_build_lock(cache_key):
        # A concurrent request may have completed while this request waited.
        timezone_context = studio_settings.studio_timezone_context(session)
        parameters = {
            "range": OVERVIEW_RANGE,
            "timezone": timezone_context["timezone"],
            "window_anchor": datetime.now(timezone_context["timezone_info"]).date().isoformat(),
        }
        parameters_fingerprint = fingerprint(parameters)
        input_fingerprint = overview_input_fingerprint(session, environment, timezone_context["timezone"])
        cached = cached_read_model(
            session,
            environment_id=environment_id,
            model_key=OVERVIEW_SUMMARY_KEY,
            parameters_fingerprint=parameters_fingerprint,
            input_fingerprint=input_fingerprint,
            producer_version=OVERVIEW_CALCULATOR_VERSION,
        )
        if cached is not None:
            return _cached_response(cached.payload, cached.computed_at)
        generation = read_model_generation(
            environment_id=environment_id,
            model_key=OVERVIEW_SUMMARY_KEY,
            parameters_fingerprint=parameters_fingerprint,
            input_fingerprint=input_fingerprint,
            producer_version=OVERVIEW_CALCULATOR_VERSION,
        )

        metadata_sources = workspace.list_metadata_sources(session, environment_id)
        code_artifacts = workspace.list_code_artifacts(session, environment_id)
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="overview-monitoring") as executor:
            monitoring_future = executor.submit(
                _load_monitoring_summary,
                environment_id,
                timezone_context,
            )
            metadata = load_environment_metadata(session, metadata_sources)
            reference_mappings = workspace.list_project_reference_mappings(session, environment.project_id)
            lineage = build_lineage_overview_summary(
                session,
                metadata,
                environment_id,
                code_artifacts,
                reference_mappings,
            )
            monitoring_summary = monitoring_future.result()
        log_sources = workspace.list_log_sources(session, environment_id)
        payload = {
            "schema_version": "environment-overview.v2",
            "sources": _source_summary(metadata_sources, log_sources),
            "metadata": _metadata_summary(metadata),
            "lineage": lineage,
            "monitoring": monitoring_summary,
        }

        # Metadata/code snapshot creation during a cache miss becomes part of
        # the post-compute fingerprint used by all following requests.
        input_fingerprint = overview_input_fingerprint(session, environment, timezone_context["timezone"])
        entry = replace_read_model(
            session,
            environment_id=environment_id,
            model_key=OVERVIEW_SUMMARY_KEY,
            parameters_fingerprint=parameters_fingerprint,
            input_fingerprint=input_fingerprint,
            producer_version=OVERVIEW_CALCULATOR_VERSION,
            payload=payload,
            expected_generation=generation,
        )
        return {**payload, "cache": {"state": "miss", "computed_at": entry.computed_at}}


def overview_input_fingerprint(session: Session, environment: Environment, timezone_name: str) -> str:
    """Fingerprint only persisted state already consumed by Overview reads."""
    sources = list(
        session.scalars(
            select(EnvironmentSource)
            .where(EnvironmentSource.environment_id == environment.id)
            .order_by(EnvironmentSource.id)
        )
    )
    source_ids = [source.id for source in sources]
    metadata_materializations = _materializations_by_source(
        session,
        MetadataMaterialization,
        source_ids,
    )
    code_materializations = _materializations_by_source(
        session,
        CodeArtifactMaterialization,
        source_ids,
    )
    revisions = {
        item.source_id: item
        for item in session.scalars(select(SourceRevision).where(SourceRevision.source_id.in_(source_ids)))
    } if source_ids else {}
    manifests = list(
        session.scalars(
            select(LogFileManifest)
            .where(LogFileManifest.source_id.in_(source_ids))
            .order_by(LogFileManifest.source_id, LogFileManifest.file_uri)
        )
    ) if source_ids else []
    mappings = list(
        session.scalars(
            select(ProjectReferenceMapping)
            .where(ProjectReferenceMapping.project_id == environment.project_id)
            .order_by(ProjectReferenceMapping.id)
        )
    )
    return fingerprint({
        "environment_id": environment.id,
        "timezone": timezone_name,
        "sources": [
            {
                "id": source.id,
                "kind": source.source_kind,
                "uri": source.uri,
                "enabled": source.enabled,
                "configuration": source.source_config_json,
                "validation_status": source.read_check_status,
                "validation": source.read_check_result_json,
                "updated_at": source.updated_at,
            }
            for source in sources
        ],
        "metadata_materializations": [
            {
                "source_id": source_id,
                "fingerprint": materialization.materialization_fingerprint,
                "materialized_at": materialization.materialized_at,
            }
            for source_id, materialization in sorted(metadata_materializations.items())
        ],
        "code_materializations": [
            {
                "source_id": source_id,
                "fingerprint": materialization.materialization_fingerprint,
                "materialized_at": materialization.materialized_at,
            }
            for source_id, materialization in sorted(code_materializations.items())
        ],
        "source_revisions": [
            {
                "source_id": source_id,
                "status": revision.status,
                "revision": revision.revision_json,
                "error": revision.error_json,
                "updated_at": revision.updated_at,
            }
            for source_id, revision in sorted(revisions.items())
        ],
        "log_manifests": [
            {
                "source_id": item.source_id,
                "file_uri": item.file_uri,
                "file_kind": item.file_kind,
                "revision": item.revision_json,
                "row_count": item.row_count,
                "status": item.status,
                "last_seen_at": item.last_seen_at,
            }
            for item in manifests
        ],
        "reference_mappings": [
            {
                "id": item.id,
                "reference_type": item.reference_type,
                "reference_normalized_value": item.reference_normalized_value,
                "target_identifier_kind": item.target_identifier_kind,
                "target_normalized_value": item.target_normalized_value,
                "updated_at": item.updated_at,
            }
            for item in mappings
        ],
    })


def _materializations_by_source(session: Session, model, source_ids: list[int]) -> dict[int, Any]:
    if not source_ids:
        return {}
    rows = session.scalars(select(model).where(model.source_id.in_(source_ids)))
    return {int(row.source_id): row for row in rows}


def _source_summary(metadata_sources: list[EnvironmentSource], log_sources: list[EnvironmentSource]) -> dict[str, Any]:
    validation = Counter(_validation_status(source) for source in [*metadata_sources, *log_sources])
    return {
        "metadata": {
            "configured": len(metadata_sources),
            "enabled": sum(1 for source in metadata_sources if source.enabled),
        },
        "logs": {
            "configured": len(log_sources),
            "enabled": sum(1 for source in log_sources if source.enabled),
        },
        "validation": {
            "errors": validation["error"],
            "warnings": validation["warning"],
        },
    }


def _metadata_summary(metadata: dict[str, Any]) -> dict[str, Any]:
    connections = metadata.get("connections", [])
    dataflows = metadata.get("dataflows", [])
    schema_hints = metadata.get("schema_hints", [])
    return {
        "connections": len(connections),
        "enabled_connections": _enabled_record_count(connections),
        "dataflows": len(dataflows),
        "enabled_dataflows": sum(1 for item in dataflows if item.get("is_active") is not False),
        "schema_hints": len(schema_hints),
        "enabled_schema_hints": _enabled_record_count(schema_hints),
        "stages": _named_counts(str(item.get("stage") or "unknown") for item in dataflows),
        "load_types": _named_counts(
            str(item.get("load_type") or (item.get("destination") or {}).get("load_type") or "unknown")
            for item in dataflows
        ),
        "errors": metadata.get("errors", []),
    }


def _monitoring_summary(
    session: Session,
    paths: list[EnvironmentSource],
    timezone_context: dict[str, Any],
) -> dict[str, Any]:
    timezone_info = timezone_context["timezone_info"]
    return monitoring.cached_environment_overview_summary(
        session,
        paths,
        timezone_info=timezone_info,
    )


def _load_monitoring_summary(environment_id: int, timezone_context: dict[str, Any]) -> dict[str, Any]:
    """Use an independent Session so the two Overview read models can overlap."""
    monitoring_session = create_session()
    try:
        paths = workspace.list_log_sources(monitoring_session, environment_id)
        try:
            return _monitoring_summary(monitoring_session, paths, timezone_context)
        except AnalyticsRebuildRequired as exc:
            return _empty_monitoring_summary(errors=[exc.detail()])
    finally:
        monitoring_session.close()


def _empty_monitoring_summary(*, errors: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "job_records": 0,
        "total_failures": 0,
        "dataflow_success_rate": 0.0,
        "failed_job_windows": {"last7": 0, "last30": 0, "last365": 0},
        "active_engines": 0,
        "latest_log_at": None,
        "date_range": {"min": None, "max": None},
        "errors": errors,
    }


def _validation_status(source: EnvironmentSource) -> str:
    if source.read_check_status:
        return source.read_check_status.lower()
    if not source.read_check_result_json:
        return "unknown"
    try:
        result = json.loads(source.read_check_result_json)
    except json.JSONDecodeError:
        return "unknown"
    return str(result.get("status") or "unknown").lower() if isinstance(result, dict) else "unknown"


def _enabled_record_count(records: list[dict[str, Any]]) -> int:
    return sum(1 for item in records if item.get("enabled") is not False and item.get("is_active") is not False)


def _named_counts(values) -> list[dict[str, Any]]:
    counts = Counter(values)
    return [
        {"name": name, "count": count}
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _cached_response(payload: dict[str, Any], computed_at) -> dict[str, Any]:
    return {
        **payload,
        "cache": {"state": "hit", "computed_at": computed_at},
    }
