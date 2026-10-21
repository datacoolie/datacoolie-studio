"""Status and progress projections for analytics-upgrade state."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from datacoolie_studio.db.models import AnalyticsUpgrade, Environment, EnvironmentSource, SyncJob, utc_now
from datacoolie_studio.db.session import create_session
from datacoolie_studio.domains.analytics_upgrade.assessment import source_context
from datacoolie_studio.domains.analytics_upgrade.constants import UPGRADE_ID
from datacoolie_studio.domains.analytics_upgrade.state import (
    as_utc,
    iso_or_none,
    json_ids,
    json_source_context,
)


def current_upgrade_status(session: Session | None = None) -> dict[str, Any] | None:
    owns_session = session is None
    active_session = session or create_session()
    try:
        try:
            upgrade = active_session.get(AnalyticsUpgrade, UPGRADE_ID)
        except SQLAlchemyError:
            active_session.rollback()
            return None
        return upgrade_status(upgrade, active_session) if upgrade is not None else None
    finally:
        if owns_session:
            active_session.close()

def analytics_upgrade_is_building(session: Session | None = None) -> bool:
    """Return whether an upgrade is actively rebuilding/swapping the live cache."""
    status = current_upgrade_status(session)
    return bool(
        status
        and status.get("state") in {
            "building",
            "staging",
            "finalizing",
            "validating",
            "publishing",
        }
    )

def request_analytics_upgrade_retry(session: Session) -> dict[str, Any]:
    upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID)
    if upgrade is None:
        return {"state": "not_required"}
    if upgrade.state in {
        "building",
        "staging",
        "repairing",
        "finalizing",
        "validating",
        "publishing",
    }:
        return upgrade_status(upgrade, session)
    upgrade.state = "pending"
    upgrade.error_code = None
    upgrade.error_message = None
    upgrade.next_retry_at = None
    upgrade.completed_at = None
    upgrade.episode_attempt_count = 0
    upgrade.updated_at = utc_now()
    session.commit()
    return upgrade_status(upgrade, session)

def upgrade_status(
    upgrade: AnalyticsUpgrade,
    session: Session | None = None,
) -> dict[str, Any]:
    now = utc_now()
    started_at = as_utc(upgrade.started_at) if upgrade.started_at else None
    completed_at = as_utc(upgrade.completed_at) if upgrade.completed_at else None
    progress_rows = source_progress(session, upgrade, now)
    completed_source_ids = set(json_ids(upgrade.completed_source_ids_json))
    completed_source_ids.update(
        source["source_id"]
        for source in progress_rows
        if source["status"] in {"completed", "succeeded"}
    )
    return {
        "state": upgrade.state,
        "operation_kind": upgrade.operation_kind,
        "reason_code": upgrade.reason_code,
        "source_schema_version": upgrade.source_schema_version,
        "target_schema_version": upgrade.target_schema_version,
        "source_ids": json_ids(upgrade.source_ids_json),
        # Worker sessions commit SyncJobs as each source finishes, before the upgrade
        # coordinator has collected every future and persisted its own checkpoint.
        # Project those committed jobs so live progress never lags per-source details.
        "completed_source_ids": sorted(completed_source_ids),
        "attempt_count": upgrade.attempt_count,
        "episode_attempt_count": upgrade.episode_attempt_count,
        "error_code": upgrade.error_code,
        "error_message": upgrade.error_message,
        "candidate_path": upgrade.candidate_path,
        "next_retry_at": iso_or_none(upgrade.next_retry_at),
        "started_at": iso_or_none(upgrade.started_at),
        "completed_at": iso_or_none(upgrade.completed_at),
        "updated_at": iso_or_none(upgrade.updated_at),
        "duration_seconds": (
            max(0.0, ((completed_at or now) - started_at).total_seconds())
            if started_at is not None
            else None
        ),
        "source_progress": progress_rows,
    }

def source_progress(
    session: Session | None,
    upgrade: AnalyticsUpgrade,
    now: datetime,
) -> list[dict[str, Any]]:
    source_ids = json_ids(upgrade.source_ids_json)
    if not source_ids:
        return []
    completed_ids = set(json_ids(upgrade.completed_source_ids_json))
    snapshot_context = json_source_context(upgrade.source_context_json)
    if session is None:
        return [
            {
                "source_id": source_id,
                **snapshot_context.get(str(source_id), {}),
                "is_registered": None,
                "status": "completed" if source_id in completed_ids else "pending",
                "message": None,
                "started_at": None,
                "completed_at": None,
                "duration_seconds": None,
            }
            for source_id in source_ids
        ]

    sources = {
        source.id: source
        for source in session.scalars(
            select(EnvironmentSource)
            .options(
                selectinload(EnvironmentSource.environment).selectinload(
                    Environment.project
                )
            )
            .where(EnvironmentSource.id.in_(source_ids))
        )
    }
    job_types = ["analytics_upgrade"]
    if upgrade.operation_kind == "source_repair":
        job_types.append("analytics_repair")
    statement = select(SyncJob).where(
        SyncJob.source_id.in_(source_ids),
        SyncJob.job_type.in_(job_types),
    )
    if upgrade.started_at is not None:
        statement = statement.where(SyncJob.started_at >= upgrade.started_at)
    jobs: dict[int, SyncJob] = {}
    for job in session.scalars(statement.order_by(SyncJob.started_at.desc(), SyncJob.id.desc())):
        jobs.setdefault(job.source_id, job)

    progress = []
    for source_id in source_ids:
        source = sources.get(source_id)
        job = jobs.get(source_id)
        context = source_context(source) if source is not None else snapshot_context.get(str(source_id), {})
        job_started = as_utc(job.started_at) if job else None
        job_completed = as_utc(job.completed_at) if job and job.completed_at else None
        progress.append({
            "source_id": source_id,
            **context,
            "is_registered": source is not None,
            "status": job.status if job else ("completed" if source_id in completed_ids else "pending"),
            "message": job.message if job else None,
            "started_at": iso_or_none(job.started_at) if job else None,
            "completed_at": iso_or_none(job.completed_at) if job else None,
            "duration_seconds": (
                max(0.0, ((job_completed or now) - job_started).total_seconds())
                if job_started is not None
                else None
            ),
        })
    return progress

__all__ = [
    "analytics_upgrade_is_building",
    "current_upgrade_status",
    "request_analytics_upgrade_retry",
    "source_progress",
    "upgrade_status",
]
