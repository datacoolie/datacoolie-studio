from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource, SyncJob, utc_now
from datacoolie_studio.db.session import create_session
from datacoolie_studio.domains.code_artifacts.service import (
    refresh_code_artifact,
    validate_code_artifact,
)
from datacoolie_studio.domains.credentials.store import KeyringCredentialSecretStore
from datacoolie_studio.domains.logs.ingestion import refresh_log_source_cache
from datacoolie_studio.domains.metadata.service import (
    ensure_metadata_materialization_result,
)
from datacoolie_studio.domains.sources import service as source_validation
from datacoolie_studio.domains.sync import service as sync


INITIAL_REFRESH_JOB_TYPE = "initial_refresh"
logger = logging.getLogger(__name__)


def queue_source_initializations(
    session: Session,
    sources: Iterable[EnvironmentSource],
) -> list[int]:
    """Persist initial refresh requests before an API response is returned."""
    job_ids: list[int] = []
    for source in sources:
        if not source.enabled:
            continue
        operation = (
            "validate" if _initialization_requires_validation(source) else "sync"
        )
        existing = session.scalars(
            select(SyncJob)
            .where(
                SyncJob.source_id == source.id,
                SyncJob.job_type == INITIAL_REFRESH_JOB_TYPE,
                SyncJob.status.in_({"queued", "initializing"}),
            )
            .order_by(SyncJob.id.desc())
        ).first()
        if existing is not None:
            job_ids.append(existing.id)
            continue
        job = SyncJob(
            environment_id=source.environment_id,
            source_id=source.id,
            source_kind=source.source_kind,
            job_type=INITIAL_REFRESH_JOB_TYPE,
            status="queued",
            message=(
                "Waiting to validate source"
                if operation == "validate"
                else "Waiting to sync discovered source"
            ),
            result_json=json.dumps(
                {"active_operation": operation}, sort_keys=True
            ),
            started_at=utc_now(),
        )
        session.add(job)
        session.flush()
        job_ids.append(job.id)
    session.commit()
    return job_ids


def queue_source_initialization_ids(
    session: Session,
    source_ids: Iterable[int],
) -> list[int]:
    ids = list(dict.fromkeys(source_ids))
    if not ids:
        return []
    sources = list(
        session.scalars(
            select(EnvironmentSource)
            .where(EnvironmentSource.id.in_(ids))
            .order_by(EnvironmentSource.id)
        )
    )
    return queue_source_initializations(session, sources)


def run_source_initialization_jobs(job_ids: Iterable[int]) -> int:
    """Run queued jobs in an independent session after the request has returned."""
    session = create_session()
    completed = 0
    try:
        for job_id in dict.fromkeys(job_ids):
            if _run_source_initialization(session, job_id):
                completed += 1
        return completed
    finally:
        session.close()


def run_queued_source_initializations_once(limit: int = 16) -> int:
    """Recover initialization work left queued after a process restart."""
    session = create_session()
    try:
        job_ids = list(
            session.scalars(
                select(SyncJob.id)
                .where(
                    SyncJob.job_type == INITIAL_REFRESH_JOB_TYPE,
                    SyncJob.status == "queued",
                )
                .order_by(SyncJob.id)
                .limit(limit)
            )
        )
    finally:
        session.close()
    return run_source_initialization_jobs(job_ids)


def requeue_interrupted_source_initializations() -> int:
    """Recover jobs claimed by a Studio process that stopped mid-operation."""
    session = create_session()
    try:
        result = session.execute(
            update(SyncJob)
            .where(
                SyncJob.job_type == INITIAL_REFRESH_JOB_TYPE,
                SyncJob.status == "initializing",
            )
            .values(
                status="queued",
                message="Resuming interrupted source initialization",
            )
        )
        if result.rowcount:
            session.commit()
        else:
            session.rollback()
        return int(result.rowcount or 0)
    finally:
        session.close()


def _run_source_initialization(session: Session, job_id: int) -> bool:
    claimed = session.execute(
        update(SyncJob)
        .where(SyncJob.id == job_id, SyncJob.status == "queued")
        .values(status="initializing")
    )
    session.commit()
    if claimed.rowcount != 1:
        return False
    job = session.get(SyncJob, job_id)
    assert job is not None
    source = session.get(EnvironmentSource, job.source_id)
    if source is None or not source.enabled:
        _finish_initialization(
            session,
            job,
            status="failed",
            message="Source is unavailable or disabled",
        )
        return True

    with sync.source_refresh_guard(source.id) as acquired:
        if not acquired or sync.has_running_sync_job(session, source.id):
            job.status = "queued"
            session.commit()
            return False
        try:
            validated = _initialization_requires_validation(source)
            if validated:
                validation = _validate_source(session, source)
                if validation.get("status") == "error":
                    _finish_initialization(
                        session,
                        job,
                        status="failed",
                        message=str(
                            validation.get("message") or "Source validation failed"
                        ),
                        error={
                            "message": validation.get("message"),
                            "errors": validation.get("errors") or [],
                        },
                    )
                    return True

                _set_initialization_phase(
                    session,
                    job,
                    operation="sync",
                    message="Source is readable; syncing cache",
                )
            _sync_source(session, source)
            _finish_initialization(
                session,
                job,
                status="succeeded",
                message=(
                    "Source validation and initial sync completed"
                    if validated
                    else "Initial sync completed"
                ),
            )
        except Exception as exc:
            session.rollback()
            job = session.get(SyncJob, job_id)
            if job is not None and job.status == "initializing":
                _finish_initialization(
                    session,
                    job,
                    status="failed",
                    message=str(exc) or "Source initialization failed",
                )
            logger.exception("Initial refresh failed for source %s", source.id)
        return True


def _validate_source(session: Session, source: EnvironmentSource) -> dict[str, Any]:
    secret_store = KeyringCredentialSecretStore()
    if source.source_kind == "logs":
        return source_validation.validate_log_source(
            session, source, secret_store=secret_store
        )
    if source.source_kind == "code":
        return validate_code_artifact(
            session, source, secret_store=secret_store
        )
    return source_validation.record_source_validation(
        session,
        source,
        source_validation.source_validation_error(
            source, f"Unsupported source kind: {source.source_kind}"
        ),
    )


def _sync_source(session: Session, source: EnvironmentSource) -> None:
    secret_store = KeyringCredentialSecretStore()
    if source.source_kind == "metadata":
        _materialization, error = ensure_metadata_materialization_result(
            session,
            source,
            secret_store=secret_store,
        )
        if error:
            raise RuntimeError(
                str(error.get("message") or "Initial cache sync failed")
            )
        return
    elif source.source_kind == "code":
        status = refresh_code_artifact(
            session,
            source,
            job_type="auto_refresh",
            secret_store=secret_store,
        )
    elif source.source_kind == "logs":
        status = refresh_log_source_cache(
            session,
            source,
            job_type="auto_refresh",
            secret_store=secret_store,
        )
    else:
        raise RuntimeError(f"Unsupported source kind: {source.source_kind}")
    _raise_for_failed_sync(status)


def _initialization_requires_validation(source: EnvironmentSource) -> bool:
    if source.source_kind == "logs":
        return True
    if source.source_kind != "code":
        return False
    try:
        config = json.loads(source.source_config_json or "{}")
    except (json.JSONDecodeError, TypeError):
        config = {}
    artifact_type = str(config.get("artifact_type") or "").strip().lower()
    if artifact_type:
        return artifact_type == "directory"
    uri = source.uri.lower().rstrip("/")
    return not uri.endswith((".py", ".zip", ".whl"))


def _raise_for_failed_sync(status: dict[str, Any]) -> None:
    latest_job = status.get("latest_job") or {}
    if latest_job.get("status") == "failed" or status.get("status") == "error":
        raise RuntimeError(str(status.get("message") or "Initial cache sync failed"))


def _set_initialization_phase(
    session: Session,
    job: SyncJob,
    *,
    operation: str,
    message: str,
) -> None:
    job.message = message
    job.result_json = json.dumps({"active_operation": operation}, sort_keys=True)
    session.commit()
    session.refresh(job)


def _finish_initialization(
    session: Session,
    job: SyncJob,
    *,
    status: str,
    message: str,
    error: dict[str, Any] | None = None,
) -> None:
    sync.finish_sync_job(
        session,
        job,
        status=status,
        message=message,
        result={
            "status": "error" if status == "failed" else "ok",
            "message": message,
            "error": error,
        },
    )
