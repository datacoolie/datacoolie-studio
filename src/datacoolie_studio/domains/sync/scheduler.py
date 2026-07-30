from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import (
    EnvironmentSource,
    SourceObservation,
    utc_now,
)
from datacoolie_studio.db.session import create_session
from datacoolie_studio.domains.code_artifacts.service import (
    code_artifact_materialization,
    ensure_code_artifact_materialization_result,
)
from datacoolie_studio.domains.logs.ingestion import (
    log_source_has_pending_changes,
    refresh_log_source_cache,
)
from datacoolie_studio.domains.metadata.service import (
    ensure_metadata_materialization_result,
    metadata_materialization,
)
from datacoolie_studio.domains.source_observation.contracts import (
    ObservationResult,
)
from datacoolie_studio.domains.source_observation.repository import (
    claim_due_observation_ids,
    claim_local_observation,
    claim_paused_observation,
    complete_observation,
    release_observation,
    resume_observation,
)
from datacoolie_studio.domains.sources.initialization import (
    requeue_interrupted_source_initializations,
    run_queued_source_initializations_once,
)
from datacoolie_studio.domains.studio_settings.service import (
    source_check_interval_seconds,
    source_check_policy,
)
from datacoolie_studio.domains.sync import service as sync

SCHEDULER_INTERVAL_SECONDS = 60
logger = logging.getLogger(__name__)


class ObservationRetryUnavailableError(RuntimeError):
    pass


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    await asyncio.to_thread(requeue_interrupted_source_initializations)
    while not stop_event.is_set():
        try:
            interval_seconds = await asyncio.to_thread(configured_source_check_interval_seconds)
            await asyncio.to_thread(run_queued_source_initializations_once)
            await asyncio.to_thread(run_due_source_checks_once)
            await asyncio.to_thread(run_due_schedules_once)
        except Exception:
            logger.exception("Scheduled source refresh pass failed")
        try:
            timeout = min(SCHEDULER_INTERVAL_SECONDS, interval_seconds)
            await asyncio.wait_for(stop_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            continue


def configured_source_check_interval_seconds() -> int:
    session = create_session()
    try:
        return source_check_interval_seconds(session)
    finally:
        session.close()


def run_due_source_checks_once() -> int:
    session = create_session()
    refreshed = 0
    try:
        owner, source_ids = claim_due_observation_ids(session)
        for source_id in source_ids:
            source = session.get(EnvironmentSource, source_id)
            if source is None or not source.enabled:
                release_observation(session, source_id=source_id, lease_owner=owner)
                continue
            if _observe_source(session, source, owner):
                refreshed += 1
        return refreshed
    finally:
        session.close()


def observe_environment_local_sources(
    session: Session,
    environment_id: int,
) -> dict[str, object]:
    """Observe enabled backend-filesystem sources without cloud scheduling."""
    sources = list(
        session.scalars(
            select(EnvironmentSource)
            .where(
                EnvironmentSource.environment_id == environment_id,
                EnvironmentSource.enabled.is_(True),
                EnvironmentSource.storage_provider == "local",
                EnvironmentSource.source_kind.in_({"metadata", "code"}),
            )
            .order_by(EnvironmentSource.id)
        )
    )
    observed = 0
    changed = 0
    skipped = 0
    failed = 0
    results: list[ObservationResult] = []
    for source in sources:
        owner = f"local-{source.id}-{utc_now().timestamp()}"
        if not claim_local_observation(
            session,
            source_id=source.id,
            environment_id=environment_id,
            lease_owner=owner,
        ):
            skipped += 1
            continue
        result = _observe_source_outcome(session, source, owner)
        results.append(result)
        if result.outcome == "skipped":
            skipped += 1
        else:
            observed += 1
        if result.outcome == "changed":
            changed += 1
        elif result.outcome == "error":
            failed += 1
    statuses = {
        status["source_id"]: status
        for status in sync.source_sync_statuses(session, sources)
    }
    return {
        "environment_id": environment_id,
        "total": len(sources),
        "observed": observed,
        "changed": changed,
        "skipped": skipped,
        "failed": failed,
        "observed_at": utc_now(),
        "outcomes": [
            _observation_result_dict(result, statuses[result.source_id])
            for result in results
        ],
    }


def _observe_source(session, source: EnvironmentSource, owner: str) -> bool:
    return _observe_source_outcome(session, source, owner).changed


def _observe_source_outcome(
    session: Session,
    source: EnvironmentSource,
    owner: str,
    *,
    refresh_guard_acquired: bool = False,
) -> ObservationResult:
    started_at = utc_now()
    try:
        if refresh_guard_acquired:
            result = _observe_source_with_refresh_guard(
                session, source, owner, started_at
            )
        else:
            with sync.source_refresh_guard(source.id) as acquired:
                if not acquired or sync.has_running_sync_job(session, source.id):
                    release_observation(
                        session, source_id=source.id, lease_owner=owner
                    )
                    return _skipped_result(source, started_at)
                result = _observe_source_with_refresh_guard(
                    session, source, owner, started_at
                )
        complete_observation(
            session,
            result=result,
            lease_owner=owner,
            policy=source_check_policy(session),
            permanent_error=_is_permanent_observation_error(result.error),
        )
        return result
    except Exception as exc:
        session.rollback()
        logger.exception("Automatic source observation failed for source %s", source.id)
        result = ObservationResult(
            source_id=source.id,
            source_kind=source.source_kind,
            outcome="error",
            pending_changes=None,
            observed_revision=None,
            error={
                "code": str(getattr(exc, "code", "source_observation_error")),
                "message": str(exc) or "Source observation failed",
            },
            inventory_metrics=None,
            started_at=started_at,
            completed_at=utc_now(),
        )
        state = session.get(SourceObservation, source.id)
        if state is not None and state.lease_owner == owner:
            complete_observation(
                session,
                result=result,
                lease_owner=owner,
                policy=source_check_policy(session),
                permanent_error=_is_permanent_observation_error(result.error),
            )
        return result


def _observe_source_with_refresh_guard(
    session: Session,
    source: EnvironmentSource,
    owner: str,
    started_at: datetime,
) -> ObservationResult:
    if sync.has_running_sync_job(session, source.id):
        release_observation(
            session, source_id=source.id, lease_owner=owner
        )
        return _skipped_result(source, started_at)
    if source.source_kind == "logs":
        pending = log_source_has_pending_changes(
            session, source, ttl_seconds=0
        )
        state = session.get(SourceObservation, source.id)
        changed = state is None or pending != state.pending_changes
        return ObservationResult(
            source_id=source.id,
            source_kind=source.source_kind,
            outcome="changed" if changed else "unchanged",
            pending_changes=pending,
            observed_revision=None,
            error=None,
            inventory_metrics=None,
            started_at=started_at,
            completed_at=utc_now(),
        )
    return _refresh_automatic_source_result(
        session,
        source,
        started_at=started_at,
    )


def retry_source_observation(
    session: Session,
    source: EnvironmentSource,
) -> ObservationResult:
    """Run one explicit check after atomically restarting a paused lifecycle."""

    owner = f"retry-{source.id}-{uuid4().hex}"
    with sync.source_refresh_guard(source.id) as acquired:
        if not acquired or sync.has_running_sync_job(session, source.id):
            raise ObservationRetryUnavailableError(
                "A source operation is already running"
            )
        if not claim_paused_observation(
            session,
            source_id=source.id,
            lease_owner=owner,
        ):
            raise ObservationRetryUnavailableError(
                "Source checks are not paused or another retry is already running"
            )
        resume_observation(
            session,
            source.id,
            due_at=utc_now(),
            lease_owner=owner,
        )
        session.commit()
        return _observe_source_outcome(
            session,
            source,
            owner,
            refresh_guard_acquired=True,
        )


def run_due_schedules_once() -> int:
    session = create_session()
    refreshed = 0
    try:
        sources = list(
            session.scalars(
                select(EnvironmentSource)
                .outerjoin(
                    SourceObservation,
                    SourceObservation.source_id == EnvironmentSource.id,
                )
                .where(
                    EnvironmentSource.enabled.is_(True),
                    EnvironmentSource.source_kind == "logs",
                    EnvironmentSource.sync_schedule_enabled.is_(True),
                    SourceObservation.automatic_observation_paused_at.is_(None),
                )
            )
        )
        now = utc_now()
        for source in sources:
            if not _is_due(source, now):
                continue
            try:
                completed = _refresh_log_source(session, source)
                if completed:
                    source.last_scheduled_sync_at = now
                    session.commit()
                    refreshed += 1
            except Exception:
                session.rollback()
                logger.exception("Scheduled refresh failed for source %s", source.id)
        return refreshed
    finally:
        session.close()


def _is_due(source: EnvironmentSource, now: datetime) -> bool:
    interval = source.sync_interval_minutes or 1
    last = source.last_scheduled_sync_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now - last.astimezone(timezone.utc) >= timedelta(minutes=interval)


def _refresh_automatic_source_result(
    session: Session,
    source: EnvironmentSource,
    *,
    started_at: datetime,
) -> ObservationResult:
    if source.source_kind == "metadata":
        previous = metadata_materialization(session, source.id)
        previous_fingerprint = previous.materialization_fingerprint if previous else None
        current, error = ensure_metadata_materialization_result(session, source)
        return _materialization_result(
            source,
            started_at=started_at,
            changed=previous_fingerprint != current.materialization_fingerprint,
            revision_json=current.source_revision_json,
            error=error,
        )
    if source.source_kind == "code":
        previous = code_artifact_materialization(session, source.id)
        previous_fingerprint = previous.materialization_fingerprint if previous else None
        current, error = ensure_code_artifact_materialization_result(
            session,
            source,
        )
        return _materialization_result(
            source,
            started_at=started_at,
            changed=(
                current is not None
                and previous_fingerprint != current.materialization_fingerprint
            ),
            revision_json=current.source_revision_json if current else None,
            error=error,
        )
    raise ValueError(f"Unsupported observed source kind: {source.source_kind}")


def _materialization_result(
    source: EnvironmentSource,
    *,
    started_at: datetime,
    changed: bool,
    revision_json: str | None,
    error: dict[str, object] | None,
) -> ObservationResult:
    revision: dict[str, object] | None = None
    if revision_json:
        try:
            payload = json.loads(revision_json)
        except json.JSONDecodeError:
            payload = None
        revision = payload if isinstance(payload, dict) else None
    return ObservationResult(
        source_id=source.id,
        source_kind=source.source_kind,
        outcome=(
            "error"
            if error is not None
            else "changed"
            if changed
            else "unchanged"
        ),
        pending_changes=None if error is not None else False,
        observed_revision=None if error is not None else revision,
        error=error,
        inventory_metrics=None,
        started_at=started_at,
        completed_at=utc_now(),
    )


def _skipped_result(
    source: EnvironmentSource,
    started_at: datetime,
) -> ObservationResult:
    return ObservationResult(
        source_id=source.id,
        source_kind=source.source_kind,
        outcome="skipped",
        pending_changes=None,
        observed_revision=None,
        error=None,
        inventory_metrics=None,
        started_at=started_at,
        completed_at=utc_now(),
    )


def _observation_result_dict(
    result: ObservationResult,
    status: dict[str, object],
) -> dict[str, object]:
    return {
        "source_id": result.source_id,
        "source_kind": result.source_kind,
        "outcome": result.outcome,
        "pending_changes": result.pending_changes,
        "error": result.error,
        "started_at": result.started_at,
        "completed_at": result.completed_at,
        "status": status,
    }


def _is_permanent_observation_error(
    error: dict[str, object] | None,
) -> bool:
    code = str((error or {}).get("code") or "").lower()
    return code in {
        "invalid_type",
        "not_found",
        "provider_dependency_missing",
        "storage_configuration_error",
        "unsupported_type",
    }


def _refresh_log_source(session, source: EnvironmentSource) -> bool:
    with sync.source_refresh_guard(source.id) as acquired:
        if not acquired:
            logger.info("Skipping scheduled refresh for source %s because a refresh is already active", source.id)
            return False
        if sync.has_running_sync_job(session, source.id):
            logger.info(
                "Skipping scheduled refresh for source %s because a sync job is already running",
                source.id,
            )
            return False
        pending = log_source_has_pending_changes(
            session,
            source,
            ttl_seconds=0,
        )
        if not pending:
            from datacoolie_studio.domains.source_observation.repository import (
                reset_observation,
            )

            reset_observation(
                session,
                source.id,
                due_at=utc_now()
                + timedelta(seconds=source_check_interval_seconds(session)),
                pending_changes=False,
            )
            return True
        try:
            refresh_log_source_cache(session, source, job_type="scheduled_refresh")
        except sync.SyncJobOverlapError:
            logger.info(
                "Skipping scheduled refresh for source %s because another process started a sync job",
                source.id,
            )
            return False
    return True
