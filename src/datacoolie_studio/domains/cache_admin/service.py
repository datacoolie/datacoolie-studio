from __future__ import annotations

import shutil
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import Environment, EnvironmentSource
from datacoolie_studio.core.config import source_materialization_cache_dir
from datacoolie_studio.domains.analytics import maintenance as analytics_maintenance
from datacoolie_studio.domains.analytics_upgrade.service import current_upgrade_status
from datacoolie_studio.domains.logs import ingestion as log_ingestion
from datacoolie_studio.domains.read_models.keys import (
    ASSETS_CATALOG,
    LINEAGE_GRAPH,
    LINEAGE_LATEST_RUNS,
    OVERVIEW,
)
from datacoolie_studio.domains.read_models.sqlite_store import SqliteResultCacheStore
from datacoolie_studio.domains.sync.service import sync_job_retention_diagnostics


FEATURE_NAMESPACES = {
    "overview": {OVERVIEW},
    "assets": {ASSETS_CATALOG},
    "lineage": {LINEAGE_GRAPH, LINEAGE_LATEST_RUNS},
    "monitoring": {"monitoring.page.any", LINEAGE_LATEST_RUNS},
}
ANALYTICS_DEPENDENT_NAMESPACES = {"monitoring.page.any", LINEAGE_LATEST_RUNS, OVERVIEW}


def cache_status(session: Session) -> dict[str, Any]:
    analytics = analytics_maintenance.cache_stats()
    return {
        "result_cache": SqliteResultCacheStore().stats(),
        "analytics_cache": {
            "backend": "duckdb",
            "path": analytics["path"],
            "exists": analytics["exists"],
            "file_bytes": analytics.get("size_bytes") or 0,
            "schema_version": analytics.get("schema_version"),
            "generation": analytics.get("generation"),
            "build_state": analytics.get("build_state", "rebuild_required"),
            "published_at": analytics.get("published_at"),
            "dataflow_rows": analytics.get("dataflow_row_count", 0),
            "job_rows": analytics.get("job_row_count", 0),
            "filter_values": analytics.get("filter_value_count", 0),
            "upgrade": current_upgrade_status(session),
        },
        "sync_job_retention": sync_job_retention_diagnostics(),
    }


def clear_cache(
    session: Session,
    *,
    scope: str,
    environment_id: int | None = None,
    features: set[str] | None = None,
) -> dict[str, Any]:
    _validate_scope(scope)
    if environment_id is not None:
        _require_environment(session, environment_id)
    namespaces = _feature_namespaces(features)
    store = SqliteResultCacheStore()
    result: dict[str, Any] = {
        "scope": scope,
        "environment_id": environment_id,
        "features": sorted(features or []),
    }
    if scope in {"read_models", "all_disposable"}:
        result["read_models"] = store.clear(
            environment_id=environment_id,
            namespaces=namespaces,
        )
    if scope in {"analytics", "all_disposable"}:
        result["analytics"] = _clear_analytics(session, environment_id)
        dependent_namespaces = set(ANALYTICS_DEPENDENT_NAMESPACES)
        result["analytics_dependent_read_models"] = store.clear(
            environment_id=environment_id,
            namespaces=dependent_namespaces,
        )
    if scope == "all_disposable":
        result["source_materializations"] = _clear_source_materializations(
            session, environment_id
        )
    return result


def prune_cache() -> dict[str, Any]:
    return {"scope": "read_models", "read_models": SqliteResultCacheStore().prune()}


def compact_cache() -> dict[str, Any]:
    store = SqliteResultCacheStore()
    pruned = store.prune()
    compacted = store.compact()
    return {
        "scope": "read_models",
        "read_models": {
            **compacted,
            "prune": pruned,
        },
    }


def _clear_analytics(session: Session, environment_id: int | None) -> dict[str, int]:
    if environment_id is None:
        return analytics_maintenance.clear_cache()
    source_ids = list(
        session.scalars(
            select(EnvironmentSource.id).where(
                EnvironmentSource.environment_id == environment_id,
                EnvironmentSource.source_kind == "logs",
            )
        )
    )
    normalized_source_ids = [int(source_id) for source_id in source_ids]
    for source_id in normalized_source_ids:
        log_ingestion.invalidate_pending_changes(source_id)
    deleted = analytics_maintenance.purge_source_ids(normalized_source_ids)
    return {
        "deleted_files": 0,
        "deleted_file_bytes": 0,
        "deleted_rows": sum(int(value) for value in deleted.values()),
    }


def _clear_source_materializations(
    session: Session, environment_id: int | None
) -> dict[str, int]:
    root = source_materialization_cache_dir()
    if environment_id is None:
        existed = root.exists()
        shutil.rmtree(root, ignore_errors=True)
        return {"deleted_roots": int(existed)}
    source_ids = [
        int(value)
        for value in session.scalars(
            select(EnvironmentSource.id).where(
                EnvironmentSource.environment_id == environment_id
            )
        )
    ]
    deleted = 0
    for source_id in source_ids:
        for target in (root / f"source-{source_id}", root / "logs" / f"source-{source_id}"):
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
                deleted += 1
    return {"deleted_roots": deleted}


def _feature_namespaces(features: set[str] | None) -> set[str] | None:
    if not features:
        return None
    unknown = features - set(FEATURE_NAMESPACES)
    if unknown:
        raise ValueError(f"Unsupported cache features: {', '.join(sorted(unknown))}")
    return set().union(*(FEATURE_NAMESPACES[feature] for feature in features))


def _validate_scope(scope: str) -> None:
    if scope not in {"read_models", "analytics", "all_disposable"}:
        raise ValueError(f"Unsupported cache scope: {scope}")


def _require_environment(session: Session, environment_id: int) -> None:
    if session.get(Environment, environment_id) is None:
        raise LookupError(f"Environment not found: {environment_id}")
