from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource, SourceRevision, SyncJob, utc_now
from datacoolie_studio.domains.storage.uri import parse_storage_uri


_refresh_locks_guard = threading.Lock()
_refresh_locks: dict[int, threading.Lock] = {}


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


def refresh_source(session: Session, source: EnvironmentSource, job_type: str = "manual_refresh") -> dict[str, Any]:
    job = begin_sync_job(session, source, job_type)
    checked_at = utc_now()
    revision = stat_source(source)
    error = _revision_error(source, revision)
    status = "error" if error else "ok"
    message = error["message"] if error else _success_message(source)

    record_source_revision(
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
    session.commit()
    session.refresh(job)
    return job


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


def record_source_revision(
    session: Session,
    *,
    source: EnvironmentSource,
    status: str,
    revision: dict[str, Any] | None,
    error: dict[str, Any] | None,
    checked_at: datetime | None = None,
) -> None:
    _upsert_source_revision(
        session,
        source=source,
        status=status,
        revision=revision,
        error=error,
        checked_at=checked_at or utc_now(),
    )


def source_sync_status(session: Session, source: EnvironmentSource, latest_job: SyncJob | None = None) -> dict[str, Any]:
    revision = session.scalar(select(SourceRevision).where(SourceRevision.source_id == source.id))
    if latest_job is None:
        latest_job = session.scalars(
            select(SyncJob).where(SyncJob.source_id == source.id).order_by(SyncJob.started_at.desc(), SyncJob.id.desc())
        ).first()

    return {
        "source_id": source.id,
        "source_kind": source.source_kind,
        "status": revision.status if revision else "unknown",
        "message": _status_message(revision, latest_job),
        "revision": _json_or_none(revision.revision_json) if revision else None,
        "error": _json_or_none(revision.error_json) if revision else None,
        "checked_at": _as_utc(revision.checked_at) if revision else None,
        "latest_job": _job_to_dict(latest_job) if latest_job else None,
    }


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


def _upsert_source_revision(
    session: Session,
    *,
    source: EnvironmentSource,
    status: str,
    revision: dict[str, Any] | None,
    error: dict[str, Any] | None,
    checked_at: datetime,
) -> None:
    row = session.scalar(select(SourceRevision).where(SourceRevision.source_id == source.id))
    if row is None:
        row = SourceRevision(source_id=source.id, source_kind=source.source_kind, status=status, checked_at=checked_at)
        session.add(row)
    row.status = status
    row.source_kind = source.source_kind
    row.revision_json = json.dumps(revision, sort_keys=True) if revision else None
    row.error_json = json.dumps(error, sort_keys=True) if error else None
    row.checked_at = checked_at
    row.updated_at = checked_at


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


def _status_message(revision: SourceRevision | None, latest_job: SyncJob | None) -> str:
    if latest_job and latest_job.message:
        return latest_job.message
    if revision is None:
        return "Source has not been refreshed"
    if revision.status == "ok":
        return "Source revision recorded"
    error = _json_or_none(revision.error_json)
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
