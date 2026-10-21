from __future__ import annotations

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import (
    CodeArtifactMaterialization,
    LogFileManifest,
    SourceObservation,
    SyncJob,
)
from datacoolie_studio.domains.analytics import maintenance as analytics_maintenance
from datacoolie_studio.domains.metadata.backups import ArtifactCleanup
from datacoolie_studio.domains.environment_caches import (
    invalidate_environment_derived_caches,
)
from datacoolie_studio.domains.read_models.keys import LINEAGE_GRAPH
from datacoolie_studio.domains.sources.mutation_support import (
    METADATA_SOURCE_DELETE_DEPENDENCIES,
    count_environment_read_models,
    count_environment_rows,
    count_source_rows,
    delete_metadata_source_dependencies,
    delete_source_and_orphan_registration,
    delete_source_log_cache,
    delete_source_sync_records,
    delete_impact_summary,
    log_delete_impact_summary,
    pluralize,
    source_delete_impact_summary,
    source_rows,
)
from datacoolie_studio.domains.sources.repository import get_source


def delete_code_artifact(
    session: Session,
    environment_id: int,
    source_id: int,
) -> bool:
    source = get_source(session, environment_id, source_id, "code")
    if source is None:
        return False
    delete_source_sync_records(session, source)
    for row in source_rows(session, CodeArtifactMaterialization, source.id):
        session.delete(row)
    invalidate_environment_derived_caches(source.environment_id, structural=True, identity=False)
    delete_source_and_orphan_registration(session, source)
    from datacoolie_studio.domains.code_artifacts.materializer import (
        clear_remote_artifact_snapshot,
    )

    clear_remote_artifact_snapshot(source_id)
    return True


def delete_metadata_source(
    session: Session,
    environment_id: int,
    source_id: int,
) -> ArtifactCleanup | bool:
    source = get_source(session, environment_id, source_id, "metadata")
    if source is None:
        return False
    cleanup = delete_metadata_source_dependencies(session, source)
    invalidate_environment_derived_caches(source.environment_id, structural=True)
    delete_source_and_orphan_registration(session, source)
    return cleanup


def delete_sql_source(
    session: Session,
    environment_id: int,
    source_id: int,
) -> bool:
    source = get_source(session, environment_id, source_id, "sql")
    if source is None:
        return False
    delete_source_sync_records(session, source)
    invalidate_environment_derived_caches(source.environment_id, structural=True, identity=False)
    delete_source_and_orphan_registration(session, source)
    return True


def delete_log_source(
    session: Session,
    environment_id: int,
    path_id: int,
) -> bool:
    path = get_source(session, environment_id, path_id, "logs")
    if path is None:
        return False
    delete_source_sync_records(session, path)
    delete_source_log_cache(session, path)
    invalidate_environment_derived_caches(path.environment_id, structural=False)
    delete_source_and_orphan_registration(session, path)
    return True


def code_artifact_delete_impact(
    session: Session,
    environment_id: int,
    source_id: int,
) -> dict | None:
    source = get_source(session, environment_id, source_id, "code")
    if source is None:
        return None
    impact_specs = [
        (
            "materialization",
            "code materialization",
            count_source_rows(session, CodeArtifactMaterialization, source.id),
            "warning",
        ),
        (
            "lineage_cache",
            "lineage graph cache entry",
            count_environment_read_models(source.environment_id, LINEAGE_GRAPH),
            "warning",
        ),
        (
            "source_observation",
            "source observation",
            count_source_rows(session, SourceObservation, source.id),
            "info",
        ),
        ("sync_job", "sync history entry", count_source_rows(session, SyncJob, source.id), "info"),
    ]
    impacts = [
        {"kind": kind, "label": pluralize(label, count), "count": count, "severity": severity}
        for kind, label, count, severity in impact_specs
        if count
    ]
    return {
        "source_id": source.id,
        "source_kind": source.source_kind,
        "source_uri": source.uri,
        "mode": "hard_delete",
        "metadata_file_deleted": False,
        "has_impact": bool(impacts),
        "impacts": impacts,
        "summary": source_delete_impact_summary(impacts, "code artifact"),
    }


def metadata_source_delete_impact(
    session: Session,
    environment_id: int,
    source_id: int,
) -> dict | None:
    source = get_source(session, environment_id, source_id, "metadata")
    if source is None:
        return None
    impacts = []
    for kind, label, model, severity in METADATA_SOURCE_DELETE_DEPENDENCIES:
        count = (
            count_environment_rows(session, model, source.environment_id)
            if kind == "environment_draft"
            else count_source_rows(session, model, source.id)
        )
        if count:
            impacts.append(
                {
                    "kind": kind,
                    "label": pluralize(label, count),
                    "count": count,
                    "severity": severity,
                }
            )
    return {
        "source_id": source.id,
        "source_kind": source.source_kind,
        "source_uri": source.uri,
        "mode": "hard_delete",
        "metadata_file_deleted": False,
        "has_impact": bool(impacts),
        "impacts": impacts,
        "summary": delete_impact_summary(impacts),
    }


def sql_source_delete_impact(
    session: Session,
    environment_id: int,
    source_id: int,
) -> dict | None:
    source = get_source(session, environment_id, source_id, "sql")
    if source is None:
        return None
    impact_specs = [
        (
            "source_observation",
            "source observation",
            count_source_rows(session, SourceObservation, source.id),
            "info",
        ),
        ("sync_job", "sync history entry", count_source_rows(session, SyncJob, source.id), "info"),
    ]
    impacts = [
        {"kind": kind, "label": pluralize(label, count), "count": count, "severity": severity}
        for kind, label, count, severity in impact_specs
        if count
    ]
    return {
        "source_id": source.id,
        "source_kind": source.source_kind,
        "source_uri": source.uri,
        "mode": "hard_delete",
        "metadata_file_deleted": False,
        "has_impact": bool(impacts),
        "impacts": impacts,
        "summary": source_delete_impact_summary(impacts, "SQL source"),
    }


def log_source_delete_impact(
    session: Session,
    environment_id: int,
    path_id: int,
) -> dict | None:
    path = get_source(session, environment_id, path_id, "logs")
    if path is None:
        return None
    cache_stats = analytics_maintenance.source_stats(path.id)
    impact_specs = [
        ("manifest", "indexed log file", count_source_rows(session, LogFileManifest, path.id), "warning"),
        ("dataflow_cache", "cached dataflow run", cache_stats["dataflow_row_count"], "warning"),
        ("job_cache", "cached job run", cache_stats["job_row_count"], "warning"),
        ("filter_cache", "cached monitoring filter value", cache_stats["filter_value_count"], "info"),
        ("source_observation", "source observation", count_source_rows(session, SourceObservation, path.id), "info"),
        ("sync_job", "refresh history entry", count_source_rows(session, SyncJob, path.id), "info"),
        ("schedule", "auto-refresh schedule", 1 if path.sync_schedule_enabled else 0, "info"),
    ]
    impacts = [
        {"kind": kind, "label": pluralize(label, count), "count": count, "severity": severity}
        for kind, label, count, severity in impact_specs
        if count
    ]
    return {
        "source_id": path.id,
        "source_kind": path.source_kind,
        "source_uri": path.uri,
        "mode": "hard_delete",
        "metadata_file_deleted": False,
        "has_impact": bool(impacts),
        "impacts": impacts,
        "summary": log_delete_impact_summary(impacts),
    }
