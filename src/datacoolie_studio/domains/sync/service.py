from __future__ import annotations

import hashlib
import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
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
QUALIFYING_SYNC_JOB_TYPES = frozenset(
    {
        "initial_refresh",
        "manual_refresh",
        "force_refresh",
        "scheduled_refresh",
        "auto_refresh",
    }
)
SYNC_TRIGGER_BY_JOB_TYPE = {
    "initial_refresh": "initial",
    "manual_refresh": "manual",
    "force_refresh": "manual",
    "scheduled_refresh": "scheduled",
    "auto_refresh": "automatic",
}


class SyncJobOverlapError(RuntimeError):
    pass


def is_qualifying_sync_job_type(job_type: str | None) -> bool:
    return str(job_type or "") in QUALIFYING_SYNC_JOB_TYPES


def sync_trigger_for_job_type(job_type: str | None) -> str | None:
    return SYNC_TRIGGER_BY_JOB_TYPE.get(str(job_type or ""))


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


def reconcile_orphaned_sync_jobs(session: Session, *, now: datetime | None = None) -> int:
    """Fail sync jobs left 'running' by a previous process.

    A running job only exists while its in-process worker is alive, so any job still
    marked running at startup is orphaned (e.g. the server was killed mid-sync). Left
    alone it blocks that source's next sync via the unique running-job constraint.
    """
    completed_at = now or utc_now()
    result = session.execute(
        update(SyncJob)
        .where(SyncJob.status == "running")
        .values(
            status="failed",
            message="Interrupted by a Studio restart",
            completed_at=completed_at,
        )
    )
    session.commit()
    return int(result.rowcount or 0)


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
    previous_result = _json_or_none(job.result_json)
    job.status = status
    job.message = message
    job.result_json = json.dumps(result, sort_keys=True)
    job.completed_at = completed_at or utc_now()
    if status == "succeeded" and _is_qualifying_sync_job(job, previous_result):
        source = session.get(EnvironmentSource, job.source_id)
        current_success = _as_utc(source.last_successful_sync_at if source else None)
        completed_success = _as_utc(job.completed_at)
        if source is not None and completed_success is not None and (
            current_success is None or current_success < completed_success
        ):
            source.last_successful_sync_at = completed_success
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
    jobs = list(
        session.scalars(
            select(SyncJob)
            .where(SyncJob.source_id == source.id)
            .order_by(SyncJob.id)
        )
    )
    active_job = _latest_active_job(jobs)
    latest_job = latest_job or active_job or (jobs[-1] if jobs else None)
    latest_sync_job = _latest_qualifying_job(jobs)
    return _source_sync_status(source, observation, latest_job, latest_sync_job)


def source_sync_statuses(
    session: Session,
    sources: list[EnvironmentSource],
    *,
    observations: dict[int, SourceObservation] | None = None,
) -> list[dict[str, Any]]:
    source_ids = [source.id for source in sources]
    if not source_ids:
        return []
    if observations is None:
        observations = {
            item.source_id: item
            for item in session.scalars(
                select(SourceObservation).where(SourceObservation.source_id.in_(source_ids))
            )
        }
    jobs_by_source: dict[int, list[SyncJob]] = {}
    for job in session.scalars(
        select(SyncJob)
        .where(
            SyncJob.source_id.in_(source_ids),
        )
        .order_by(SyncJob.source_id, SyncJob.id)
    ):
        jobs_by_source.setdefault(job.source_id, []).append(job)
    return [
        _source_sync_status_for_jobs(source, observations.get(source.id), jobs_by_source.get(source.id, []))
        for source in sources
    ]


def _source_sync_status_for_jobs(
    source: EnvironmentSource,
    observation: SourceObservation | None,
    jobs: list[SyncJob],
) -> dict[str, Any]:
    return _source_sync_status(
        source,
        observation,
        _latest_active_job(jobs) or (jobs[-1] if jobs else None),
        _latest_qualifying_job(jobs),
    )


def _source_sync_status(
    source: EnvironmentSource,
    observation: SourceObservation | None,
    latest_job: SyncJob | None,
    latest_sync_job: SyncJob | None = None,
) -> dict[str, Any]:
    observed = observation_payload(observation)
    outcome = str(observed["status"])
    active_operation = _active_operation(latest_job)
    validation = _validation_status(source, active_operation)
    observation_status = _observation_status(observation, observed)
    sync_execution = _sync_execution_status(
        source,
        latest_sync_job,
        active_operation=active_operation,
    )
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
        "observation_state": observed["observation_state"],
        "observation_failure_count": observed["observation_failure_count"],
        "observation_paused_at": observed["observation_paused_at"],
        "active_operation": active_operation,
        "latest_job": _job_to_dict(latest_job) if latest_job else None,
        "validation": validation,
        "observation": observation_status,
        "sync_execution": sync_execution,
    }


def _latest_active_job(jobs: list[SyncJob]) -> SyncJob | None:
    active = [job for job in jobs if job.status in {"queued", "initializing", "running"}]
    return min(active, key=_active_job_priority) if active else None


def _latest_qualifying_job(jobs: list[SyncJob]) -> SyncJob | None:
    qualifying = [
        job for job in jobs
        if job.status in {"queued", "initializing", "running", "succeeded", "failed"}
        if _is_qualifying_sync_job(job, _json_or_none(job.result_json))
    ]
    return max(
        qualifying,
        key=lambda job: (_normalized_datetime(job.started_at), job.id),
        default=None,
    )


def _is_qualifying_sync_job(
    job: SyncJob,
    result: dict[str, Any] | None,
) -> bool:
    if not is_qualifying_sync_job_type(job.job_type):
        return False
    if job.job_type == "initial_refresh" and result:
        return result.get("active_operation") != "validate"
    return True


def _normalized_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _validation_status(
    source: EnvironmentSource,
    active_operation: str | None,
) -> dict[str, Any]:
    result = _json_or_none(source.read_check_result_json) or {}
    raw_status = str(source.read_check_status or result.get("status") or "")
    if active_operation == "validate":
        state = "validating"
    elif not raw_status:
        state = "not_validated"
    elif raw_status == "ok":
        state = "ready"
    elif raw_status == "warning":
        state = "warning"
    else:
        state = "invalid"
    errors = result.get("errors")
    error = (
        {"message": result.get("message"), "errors": errors}
        if state == "invalid"
        else None
    )
    return {
        "state": state,
        "completed_at": _as_utc(source.read_checked_at) if source.read_checked_at else None,
        "summary": result.get("message") or None,
        "error": error,
    }


def _observation_status(
    observation: SourceObservation | None,
    observed: dict[str, Any],
) -> dict[str, Any]:
    raw_status = str(observed["status"])
    if observation is None or raw_status == "never":
        state = "never"
    elif observation.lease_owner:
        state = "checking"
    elif observed["observation_paused_at"] is not None:
        state = "paused"
    elif raw_status == "error":
        state = "error"
    elif observed["pending_changes"] is True:
        # pending_changes is anchored to the last successful sync. Repeated
        # checks can therefore report an unchanged observation while the
        # source is still ahead of the cache.
        state = "changed"
    elif raw_status == "changed" and observed["pending_changes"] is None:
        # Keep the warning for legacy observations that predate the durable
        # pending_changes value.
        state = "changed"
    else:
        state = "unchanged"
    return {
        "state": state,
        "checked_at": (
            _as_utc(observed["last_observed_at"])
            if observed["last_observed_at"]
            else None
        ),
        "pending_changes": observed["pending_changes"],
        "next_check_at": (
            _as_utc(observed["next_check_at"])
            if observed["next_check_at"]
            else None
        ),
        "failure_count": observed["observation_failure_count"],
        "error": observed["error"],
    }


def _sync_execution_status(
    source: EnvironmentSource,
    latest_sync_job: SyncJob | None,
    *,
    active_operation: str | None,
) -> dict[str, Any]:
    active_sync = (
        latest_sync_job
        if latest_sync_job is not None
        and latest_sync_job.status in {"queued", "initializing", "running"}
        and active_operation != "validate"
        else None
    )
    job = active_sync or latest_sync_job
    result = _json_or_none(job.result_json) if job is not None else None
    if active_sync is not None:
        state = "running"
    elif job is None:
        state = "succeeded" if source.last_successful_sync_at else "never"
    elif job.status == "succeeded":
        state = "succeeded"
    elif job.status == "failed":
        state = "failed"
    else:
        state = "never"
    error = (
        (result or {}).get("error")
        if state == "failed"
        else None
    )
    if state == "failed" and not isinstance(error, dict):
        error = {"message": job.message} if job and job.message else None
    return {
        "state": state,
        "trigger": sync_trigger_for_job_type(job.job_type) if job else None,
        "started_at": _as_utc(job.started_at) if job else None,
        "completed_at": (
            _as_utc(job.completed_at)
            if job is not None
            else _as_utc(source.last_successful_sync_at)
            if state == "succeeded"
            else None
        ),
        "last_successful_at": (
            _as_utc(source.last_successful_sync_at)
            if source.last_successful_sync_at
            else None
        ),
        "summary": job.message if job else "Last successful sync is outside retained job history" if state == "succeeded" else None,
        "error": error,
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


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
