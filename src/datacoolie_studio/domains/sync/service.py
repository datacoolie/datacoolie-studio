from __future__ import annotations

import hashlib
import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import (
    EnvironmentSource,
    SourceObservation,
    SyncJob,
    utc_now,
)
from datacoolie_studio.domains.source_observation.repository import (
    observation_payload,
    record_source_evidence,
)
from datacoolie_studio.domains.storage.uri import parse_storage_uri


_refresh_locks_guard = threading.Lock()
_refresh_locks: dict[int, threading.Lock] = {}
_retention_diagnostics_lock = threading.Lock()
_last_retention_diagnostics: dict[str, Any] | None = None
logger = logging.getLogger(__name__)

SYNC_JOB_RETENTION_DAYS = 30
SYNC_JOB_RETENTION_MINIMUM = 100


class SyncJobOverlapError(RuntimeError):
    pass


@contextmanager
def source_refresh_guard(source_id: int):
    """Prevent overlapping refreshes for a source within this Studio process."""
    with _refresh_locks_guard:
        lock = _refresh_locks.setdefault(source_id, threading.Lock())
    acquired = lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()


def has_running_sync_job(session: Session, source_id: int) -> bool:
    """Return whether persistent state already has an active job for the source."""
    return session.scalar(
        select(SyncJob.id)
        .where(
            SyncJob.source_id == source_id,
            SyncJob.status == "running",
        )
        .limit(1)
    ) is not None


def refresh_source(session: Session, source: EnvironmentSource, job_type: str = "manual_refresh") -> dict[str, Any]:
    job = begin_sync_job(session, source, job_type)
    checked_at = utc_now()
    revision = stat_source(source)
    error = _revision_error(source, revision)
    status = "error" if error else "ok"
    message = error["message"] if error else _success_message(source)

    record_source_observation(
        session,
        source=source,
        status=status,
        revision=revision if not error else None,
        error=error,
        checked_at=checked_at,
    )
    finish_sync_job(
        session,
        job,
        status="failed" if error else "succeeded",
        message=message,
        result={
            "status": status,
            "message": message,
            "revision": revision if not error else None,
            "error": error,
        },
        completed_at=checked_at,
    )
    return source_sync_status(session, source, job)


def begin_sync_job(session: Session, source: EnvironmentSource, job_type: str) -> SyncJob:
    job = SyncJob(
        environment_id=source.environment_id,
        source_id=source.id,
        source_kind=source.source_kind,
        job_type=job_type,
        status="running",
        started_at=utc_now(),
    )
    session.add(job)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if has_running_sync_job(session, source.id):
            raise SyncJobOverlapError(
                f"A sync job is already running for source {source.id}"
            ) from exc
        raise
    session.refresh(job)
    try:
        diagnostics = prune_terminal_sync_jobs(session, job.source_id)
    except Exception as exc:
        session.rollback()
        diagnostics = {
            "source_id": job.source_id,
            "status": "error",
            "message": str(exc),
            "checked_at": utc_now().isoformat(),
        }
        logger.exception("SyncJob retention failed for source %s", job.source_id)
    _record_retention_diagnostics(diagnostics)
    return job


def prune_terminal_sync_jobs(
    session: Session,
    source_id: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Retain the union of the latest 30 days and latest 100 terminal jobs."""
    checked_at = now or utc_now()
    cutoff = checked_at - timedelta(days=SYNC_JOB_RETENTION_DAYS)
    latest_ids = list(
        session.scalars(
            select(SyncJob.id)
            .where(
                SyncJob.source_id == source_id,
                SyncJob.status != "running",
                SyncJob.completed_at.is_not(None),
            )
            .order_by(
                SyncJob.completed_at.desc(),
                SyncJob.started_at.desc(),
                SyncJob.id.desc(),
            )
            .limit(SYNC_JOB_RETENTION_MINIMUM)
        )
    )
    delete_statement = delete(SyncJob).where(
        SyncJob.source_id == source_id,
        SyncJob.status != "running",
        SyncJob.completed_at.is_not(None),
        SyncJob.completed_at < cutoff,
    )
    if latest_ids:
        delete_statement = delete_statement.where(SyncJob.id.not_in(latest_ids))
    result = session.execute(delete_statement)
    malformed = int(
        session.scalar(
            select(func.count(SyncJob.id)).where(
                SyncJob.source_id == source_id,
                SyncJob.status != "running",
                SyncJob.completed_at.is_(None),
            )
        )
        or 0
    )
    session.commit()
    return {
        "source_id": source_id,
        "status": "ok",
        "deleted_jobs": int(result.rowcount or 0),
        "malformed_terminal_jobs": malformed,
        "cutoff": cutoff.isoformat(),
        "minimum_history": SYNC_JOB_RETENTION_MINIMUM,
        "checked_at": checked_at.isoformat(),
    }


def sync_job_retention_diagnostics() -> dict[str, Any] | None:
    with _retention_diagnostics_lock:
        return dict(_last_retention_diagnostics) if _last_retention_diagnostics else None


def _record_retention_diagnostics(diagnostics: dict[str, Any]) -> None:
    global _last_retention_diagnostics
    with _retention_diagnostics_lock:
        _last_retention_diagnostics = dict(diagnostics)


def finish_sync_job(
    session: Session,
    job: SyncJob,
    *,
    status: str,
    message: str,
    result: dict[str, Any],
    completed_at: datetime | None = None,
) -> SyncJob:
    job.status = status
    job.message = message
    job.result_json = json.dumps(result, sort_keys=True)
    job.completed_at = completed_at or utc_now()
    session.commit()
    session.refresh(job)
    return job


def record_source_observation(
    session: Session,
    *,
    source: EnvironmentSource,
    status: str,
    revision: dict[str, Any] | None,
    error: dict[str, Any] | None,
    checked_at: datetime | None = None,
) -> None:
    record_source_evidence(
        session,
        source,
        status=status,
        revision=revision,
        error=error,
        checked_at=checked_at or utc_now(),
    )


def source_sync_status(session: Session, source: EnvironmentSource, latest_job: SyncJob | None = None) -> dict[str, Any]:
    observation = session.get(SourceObservation, source.id)
    if latest_job is None:
        latest_job = session.scalars(
            select(SyncJob)
            .where(
                SyncJob.source_id == source.id,
                SyncJob.status.in_({"queued", "initializing", "running"}),
            )
            .order_by(
                case(
                    (SyncJob.status == "initializing", 0),
                    (SyncJob.status == "running", 1),
                    else_=2,
                ),
                SyncJob.id.desc(),
            )
        ).first()
    if latest_job is None:
        latest_job = session.scalars(
            select(SyncJob).where(SyncJob.source_id == source.id).order_by(SyncJob.started_at.desc(), SyncJob.id.desc())
        ).first()

    return _source_sync_status(source, observation, latest_job)


def source_sync_statuses(
    session: Session,
    sources: list[EnvironmentSource],
) -> list[dict[str, Any]]:
    source_ids = [source.id for source in sources]
    if not source_ids:
        return []
    observations = {
        item.source_id: item
        for item in session.scalars(
            select(SourceObservation).where(SourceObservation.source_id.in_(source_ids))
        )
    }
    latest_job_ids = (
        select(SyncJob.source_id, func.max(SyncJob.id).label("job_id"))
        .where(SyncJob.source_id.in_(source_ids))
        .group_by(SyncJob.source_id)
        .subquery()
    )
    latest_jobs = {
        job.source_id: job
        for job in session.scalars(
            select(SyncJob).join(latest_job_ids, SyncJob.id == latest_job_ids.c.job_id)
        )
    }
    active_jobs: dict[int, SyncJob] = {}
    for job in session.scalars(
        select(SyncJob)
        .where(
            SyncJob.source_id.in_(source_ids),
            SyncJob.status.in_({"queued", "initializing", "running"}),
        )
        .order_by(SyncJob.id.desc())
    ):
        current = active_jobs.get(job.source_id)
        if current is None or _active_job_priority(job) < _active_job_priority(current):
            active_jobs[job.source_id] = job
    latest_jobs.update(active_jobs)
    return [
        _source_sync_status(
            source,
            observations.get(source.id),
            latest_jobs.get(source.id),
        )
        for source in sources
    ]


def _source_sync_status(
    source: EnvironmentSource,
    observation: SourceObservation | None,
    latest_job: SyncJob | None,
) -> dict[str, Any]:
    observed = observation_payload(observation)
    outcome = str(observed["status"])
    active_operation = _active_operation(latest_job)
    return {
        "source_id": source.id,
        "source_kind": source.source_kind,
        "status": (
            "running"
            if active_operation
            else "error"
            if outcome == "error"
            else "ok"
            if outcome in {"changed", "unchanged"}
            else "unknown"
        ),
        "message": _status_message(observation, latest_job),
        "revision": observed["revision"],
        "error": observed["error"],
        "checked_at": observed["checked_at"],
        "last_observed_at": observed["last_observed_at"],
        "next_check_at": observed["next_check_at"],
        "pending_changes": observed["pending_changes"],
        "active_operation": active_operation,
        "latest_job": _job_to_dict(latest_job) if latest_job else None,
    }


def _active_operation(latest_job: SyncJob | None) -> str | None:
    if latest_job is None or latest_job.status not in {"queued", "initializing", "running"}:
        return None
    result = _json_or_none(latest_job.result_json)
    operation = result.get("active_operation") if result else None
    if operation in {"validate", "sync"}:
        return str(operation)
    return "sync" if latest_job.status == "running" else None


def _active_job_priority(job: SyncJob) -> tuple[int, int]:
    priority = {"initializing": 0, "running": 1, "queued": 2}.get(job.status, 3)
    return priority, -job.id


def stat_source(source: EnvironmentSource, *, include_content_hash: bool = True) -> dict[str, Any]:
    parsed = parse_storage_uri(source.uri)
    if not parsed.is_local or parsed.local_path is None:
        return {
            "provider": parsed.provider,
            "uri": source.uri,
            "path": source.uri,
            "exists": False,
            "source_kind": source.source_kind,
            "object_type": "provider_not_enabled",
            "scheme": parsed.scheme,
        }
    path = parsed.local_path
    base: dict[str, Any] = {
        "provider": "local",
        "uri": source.uri,
        "path": str(path),
        "exists": path.exists(),
        "source_kind": source.source_kind,
    }
    if not path.exists():
        return {**base, "object_type": "missing"}
    if path.is_file():
        stat = path.stat()
        revision = {
            **base,
            "object_type": "file",
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
        if include_content_hash:
            revision["content_hash"] = hashlib.sha256(path.read_bytes()).hexdigest()
        return revision
    if path.is_dir():
        file_count = 0
        total_size = 0
        max_mtime_ns = None
        for item in path.rglob("*"):
            if not item.is_file():
                continue
            file_count += 1
            stat = item.stat()
            total_size += stat.st_size
            max_mtime_ns = stat.st_mtime_ns if max_mtime_ns is None else max(max_mtime_ns, stat.st_mtime_ns)
        return {
            **base,
            "object_type": "directory",
            "file_count": file_count,
            "total_size": total_size,
            "max_mtime_ns": max_mtime_ns if max_mtime_ns is not None else path.stat().st_mtime_ns,
        }
    return {**base, "object_type": "unsupported"}


def _revision_error(source: EnvironmentSource, revision: dict[str, Any]) -> dict[str, Any] | None:
    if revision.get("object_type") == "provider_not_enabled":
        provider = str(revision.get("provider") or "storage")
        return {"message": f"{provider.upper()} storage URI is recognized but not enabled yet: {source.uri}", "code": "provider_not_enabled"}
    if not revision.get("exists"):
        return {"message": f"Source path not found: {source.uri}", "code": "not_found"}
    object_type = revision.get("object_type")
    if source.source_kind == "metadata" and object_type != "file":
        return {"message": f"Metadata source must be a file: {source.uri}", "code": "invalid_type"}
    if source.source_kind == "logs" and object_type != "directory":
        return {"message": f"Log source must be a directory: {source.uri}", "code": "invalid_type"}
    if object_type == "unsupported":
        return {"message": f"Unsupported source path type: {source.uri}", "code": "unsupported_type"}
    return None


def _success_message(source: EnvironmentSource) -> str:
    if source.source_kind == "metadata":
        return "Metadata source revision recorded"
    if source.source_kind == "logs":
        return "Log source revision recorded"
    return "Source revision recorded"


def _status_message(
    observation: SourceObservation | None,
    latest_job: SyncJob | None,
) -> str:
    if latest_job and latest_job.message:
        return latest_job.message
    if observation is None or observation.last_outcome == "never":
        return "Source has not been refreshed"
    if observation.last_outcome != "error":
        return "Source revision recorded"
    error = _json_or_none(observation.error_json)
    return str(error.get("message") if isinstance(error, dict) else "Source refresh failed")


def _job_to_dict(job: SyncJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "environment_id": job.environment_id,
        "source_id": job.source_id,
        "source_kind": job.source_kind,
        "job_type": job.job_type,
        "status": job.status,
        "message": job.message,
        "result": _json_or_none(job.result_json),
        "started_at": _as_utc(job.started_at),
        "completed_at": _as_utc(job.completed_at) if job.completed_at else None,
    }


def _json_or_none(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
