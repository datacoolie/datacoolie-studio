from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import Environment, EnvironmentSource
from datacoolie_studio.domains.analytics import access as analytics_access
from datacoolie_studio.domains.analytics import cache_state
from datacoolie_studio.domains.analytics import maintenance as analytics_maintenance
from datacoolie_studio.domains.logs import pending as log_pending


logger = logging.getLogger(__name__)


def log_source_ids_for_environment(session: Session, environment_id: int) -> list[int]:
    rows = session.scalars(
        select(EnvironmentSource.id).where(
            EnvironmentSource.environment_id == environment_id,
            EnvironmentSource.source_kind == "logs",
        )
    ).all()
    return [int(source_id) for source_id in rows]


def log_source_ids_for_project(session: Session, project_id: int) -> list[int]:
    rows = session.scalars(
        select(EnvironmentSource.id)
        .join(Environment, EnvironmentSource.environment_id == Environment.id)
        .where(
            Environment.project_id == project_id,
            EnvironmentSource.source_kind == "logs",
        )
    ).all()
    return [int(source_id) for source_id in rows]


def purge_analytics_cache_by_source_ids(source_ids: Iterable[int]) -> dict[str, Any]:
    normalized_ids = _normalized_source_ids(source_ids)
    result: dict[str, Any] = {
        "status": "skipped" if not normalized_ids else "ok",
        "source_ids": list(normalized_ids),
        "errors": [],
        "dataflow_rows_deleted": 0,
        "job_rows_deleted": 0,
        "filter_values_deleted": 0,
    }
    if not normalized_ids:
        return result

    for source_id in normalized_ids:
        try:
            log_pending.invalidate_pending_changes(source_id)
        except Exception as exc:
            result["errors"].append(_error_type(exc))
            _log_cleanup_failure("pending-state-invalidation", normalized_ids, exc)

    try:
        purge_result = analytics_maintenance.purge_source_ids(list(normalized_ids))
    except Exception as exc:
        result["status"] = "failed"
        result["errors"].append(_error_type(exc))
        _log_cleanup_failure("analytics-cache-purge", normalized_ids, exc)
    else:
        result.update(purge_result)

    if result["errors"]:
        result["status"] = "failed"
    return result


def reconcile_orphaned_analytics_cache(session: Session) -> dict[str, Any]:
    """Remove cached log-source data whose authoritative source no longer exists."""

    try:
        cached_source_ids = _cached_analytics_source_ids()
    except Exception as exc:
        _log_cleanup_failure("analytics-cache-scan", (), exc)
        return {
            "status": "failed",
            "orphan_source_ids": [],
            "source_ids": [],
            "errors": [_error_type(exc)],
        }

    active_source_ids = set(
        session.scalars(
            select(EnvironmentSource.id).where(EnvironmentSource.source_kind == "logs")
        )
    )
    orphan_source_ids = sorted(cached_source_ids - {int(source_id) for source_id in active_source_ids})
    result = purge_analytics_cache_by_source_ids(orphan_source_ids)
    result["orphan_source_ids"] = orphan_source_ids
    if orphan_source_ids:
        logger.info(
            "Analytics orphan reconciliation completed status=%s source_count=%d",
            result["status"],
            len(orphan_source_ids),
        )
    return result


def _cached_analytics_source_ids() -> set[int]:
    path = analytics_maintenance.analytics_database_path()
    if not path.exists():
        return set()
    with analytics_access.analytics_maintenance_lock:
        connection = analytics_access.connect(path, read_only=True)
        try:
            return cache_state.cache_source_ids(connection)
        finally:
            connection.close()


def _normalized_source_ids(source_ids: Iterable[int]) -> tuple[int, ...]:
    return tuple(sorted({int(source_id) for source_id in source_ids if int(source_id) > 0}))


def _error_type(exc: Exception) -> str:
    return type(exc).__name__


def _log_cleanup_failure(
    operation: str,
    source_ids: Iterable[int],
    exc: Exception,
) -> None:
    logger.warning(
        "Analytics derived cleanup failed operation=%s source_ids=%s error_type=%s",
        operation,
        list(source_ids),
        _error_type(exc),
    )
