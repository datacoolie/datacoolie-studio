from __future__ import annotations

import json
import logging
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from datacoolie_studio.core.config import analytics_database_path
from datacoolie_studio.db.models import (
    AnalyticsUpgrade,
    EnvironmentSource,
    SourceObservation,
    SyncJob,
    utc_now,
)
from datacoolie_studio.db.session import create_session
from datacoolie_studio.domains.analytics import access, schema, store
from datacoolie_studio.domains.analytics.errors import AnalyticsRebuildRequired
from datacoolie_studio.domains.analytics.migrations import validate_registry
from datacoolie_studio.domains.logs.ingestion import refresh_log_source_cache
from datacoolie_studio.domains.source_observation.repository import (
    MAX_CONSECUTIVE_OBSERVATION_FAILURES,
    paused_source_ids,
)
from datacoolie_studio.domains.sources import service as source_validation


logger = logging.getLogger(__name__)
UPGRADE_ID = 1
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 3600
UPGRADE_POLL_SECONDS = 30
# Max Log sources materialized/parsed concurrently during a rebuild. DuckDB writes stay
# serialized by analytics_maintenance_lock; this only parallelizes network/parse work.
UPGRADE_BUILD_CONCURRENCY = 8

# Serializes overlapping upgrade RUNS without holding the DuckDB maintenance lock, so
# per-source publishes (which acquire analytics_maintenance_lock) can run from worker
# threads without deadlocking on the re-entrant lock.
_upgrade_run_lock = threading.Lock()

# Source-failure codes that must NOT park the source. They are transient
# (another sync holds the source) or environmental (an optional storage provider
# package is not installed in this deployment). Parking a source for a missing
# dependency would wrongly exclude a healthy cloud source that starts working again
# once the extra is installed. These abort and retry the whole upgrade instead.
_NON_PARKING_SOURCE_CODES = {
    "source_sync_busy",
    "provider_dependency_missing",
    "provider_not_enabled",
    "storage_provider_not_installed",
}


async def analytics_upgrade_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        await asyncio.to_thread(run_analytics_upgrade_once)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=UPGRADE_POLL_SECONDS)
        except asyncio.TimeoutError:
            continue


def run_analytics_upgrade_once(*, now: datetime | None = None) -> dict[str, Any]:
    with _upgrade_run_lock:
        return _run_analytics_upgrade_once(now=now)


def _build_sources_parallel(
    source_ids: list[int],
    candidate: Path,
) -> dict[int, dict[str, Any]]:
    """Replay each source into the candidate concurrently, one session per worker.

    Materialization (network) and parsing run in parallel; each source's publish_rows
    serializes on analytics_maintenance_lock, so DuckDB stays single-writer. Returns a
    per-source result dict. An unexpected worker exception aborts the whole upgrade.
    """
    if not source_ids:
        return {}
    results: dict[int, dict[str, Any]] = {}
    worker_errors: dict[int, BaseException] = {}

    def build_one(source_id: int) -> tuple[int, dict[str, Any]]:
        worker_session = create_session()
        try:
            source = worker_session.get(EnvironmentSource, source_id)
            if source is None:
                return source_id, {
                    "status": "error",
                    "error": {
                        "code": "source_replay_failed",
                        "message": "Log source no longer exists",
                    },
                }
            result = refresh_log_source_cache(
                worker_session,
                source,
                job_type="analytics_upgrade",
                database_path_override=candidate,
                force_analytics_replay=True,
            )
            return source_id, result
        finally:
            worker_session.close()

    max_workers = min(len(source_ids), UPGRADE_BUILD_CONCURRENCY)
    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="analytics-upgrade"
    ) as executor:
        futures = {
            executor.submit(build_one, source_id): source_id
            for source_id in source_ids
        }
        for future in as_completed(futures):
            source_id = futures[future]
            try:
                built_id, result = future.result()
                results[built_id] = result
            except Exception as exc:  # noqa: BLE001 - captured to abort deterministically
                worker_errors[source_id] = exc

    if worker_errors:
        raise worker_errors[sorted(worker_errors)[0]]
    return results


def _run_analytics_upgrade_once(*, now: datetime | None = None) -> dict[str, Any]:
    """Rebuild all eligible Log sources and atomically publish one generation."""

    validate_registry()
    checked_at = now or utc_now()
    session = create_session()
    candidate: Path | None = None
    try:
        sources = _eligible_sources(session)
        source_ids = [source.id for source in sources]
        live_path = analytics_database_path()
        if not source_ids:
            _mark_current_state_succeeded(session, [], checked_at)
            return {"state": "not_required", "source_ids": []}
        if _cache_is_current(live_path, source_ids):
            _mark_current_state_succeeded(session, source_ids, checked_at)
            return {"state": "current", "source_ids": source_ids}

        if _external_sync_in_progress(session, source_ids):
            # A user/scheduled sync is currently writing the cache. Defer the
            # upgrade to the next cycle so the two writers never collide.
            return current_upgrade_status(session) or {
                "state": "pending",
                "source_ids": source_ids,
            }

        upgrade = _get_or_create_upgrade(
            session,
            source_version=_live_schema_version(live_path),
            source_ids=source_ids,
        )
        if (
            upgrade.state == "failed"
            and upgrade.next_retry_at is not None
            and _as_utc(upgrade.next_retry_at) > checked_at
        ):
            return upgrade_status(upgrade, session)

        candidate = access.candidate_path(live_path)
        _begin_attempt(upgrade, source_ids, candidate, checked_at)
        session.commit()

        if _cache_is_current(live_path, source_ids):
            _mark_succeeded(upgrade, checked_at)
            session.commit()
            return upgrade_status(upgrade, session)

        # Resume a compatible partial candidate from a previous attempt instead of
        # rebuilding every already-completed source; only discard an incompatible one.
        already_built = _resumable_candidate_sources(candidate, source_ids)
        completed_ids: list[int] = sorted(already_built)
        for source_id in completed_ids:
            _mark_source_completed(upgrade, source_id)
        if completed_ids:
            session.commit()

        # Build the remaining sources concurrently (network materialization/parse run in
        # parallel; each source's DuckDB publish still serializes on the maintenance lock).
        pending = [source.id for source in sources if source.id not in already_built]
        build_results = _build_sources_parallel(pending, candidate)

        busy_error: AnalyticsUpgradeSourceError | None = None
        hard_error: AnalyticsUpgradeSourceError | None = None
        for source_id in sorted(build_results):
            result = build_results[source_id]
            status = result.get("status")
            if status == "ok":
                _mark_source_completed(upgrade, source_id)
                completed_ids.append(source_id)
                session.commit()
                continue
            error = result.get("error") or {}
            code = str(
                error.get("code")
                or (
                    "source_sync_busy"
                    if status == "running"
                    else "source_replay_failed"
                )
            )
            message = str(
                error.get("message")
                or (
                    "Another source sync is still running"
                    if status == "running"
                    else "Log source could not be replayed"
                )
            )
            if code in _NON_PARKING_SOURCE_CODES:
                source_error = AnalyticsUpgradeSourceError(
                    source_id, code=code, message=message
                )
                if code == "source_sync_busy":
                    busy_error = busy_error or source_error
                else:
                    hard_error = hard_error or source_error
                continue
            # The source cannot be materialized (missing storage/dependency). Park it
            # so it drops out of the upgrade scope and Monitoring coverage instead of
            # wedging every future upgrade, then keep building the remaining sources.
            _park_unbuildable_source(session, source_id, code, message, utc_now())

        # A real dependency/environment failure aborts (retry with backoff); a transient
        # sync collision defers softly. Both are raised after parking the rest.
        if hard_error is not None:
            raise hard_error
        if busy_error is not None:
            raise busy_error

        completed_ids = sorted(set(completed_ids))
        if not completed_ids:
            # Nothing could be rebuilt; leave the live cache untouched and settle.
            access.discard_candidate(candidate)
            upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID) or upgrade
            upgrade.source_ids_json = json.dumps([])
            upgrade.completed_source_ids_json = json.dumps([])
            _mark_succeeded(upgrade, utc_now())
            session.commit()
            return upgrade_status(upgrade, session)

        upgrade.state = "validating"
        upgrade.updated_at = utc_now()
        session.commit()
        store.validate_analytics_candidate(candidate, completed_ids)

        session.expire_all()
        current_source_ids = sorted(
            source.id for source in _eligible_sources(session)
        )
        if current_source_ids != completed_ids:
            raise AnalyticsUpgradeScopeChangedError()

        upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID) or upgrade
        upgrade.source_ids_json = json.dumps(completed_ids)
        upgrade.state = "publishing"
        upgrade.updated_at = utc_now()
        session.commit()
        access.swap_candidate(candidate, live_path)
        _mark_succeeded(upgrade, utc_now())
        session.commit()
        return upgrade_status(upgrade, session)
    except Exception as exc:
        session.rollback()
        transient_collision = (
            isinstance(exc, AnalyticsUpgradeSourceError)
            and exc.code == "source_sync_busy"
        )
        if candidate is not None and not transient_collision:
            # Keep a valid partial candidate after a transient collision so the next
            # attempt resumes it; discard it for real failures/scope changes.
            access.discard_candidate(candidate)
        upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID)
        if upgrade is not None:
            if transient_collision:
                # A user/scheduled sync started mid-build. This is not a failure —
                # defer and retry shortly instead of flashing a scary "failed" state.
                _defer_upgrade(upgrade, checked_at)
            else:
                _mark_failed(upgrade, exc, checked_at)
            session.commit()
            status = upgrade_status(upgrade, session)
        else:
            status = {
                "state": "failed",
                "error_code": getattr(exc, "code", "analytics_upgrade_failed"),
            }
        if transient_collision:
            logger.info("Analytics cache upgrade deferred: %s", exc)
        else:
            logger.warning("Analytics cache upgrade failed: %s", exc)
        return status
    finally:
        session.close()


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
        and status.get("state") in {"building", "validating", "publishing"}
    )


def _external_sync_in_progress(session: Session, source_ids: list[int]) -> bool:
    """Return whether a non-upgrade sync job is running for any of these sources."""
    if not source_ids:
        return False
    return (
        session.scalar(
            select(SyncJob.id)
            .where(
                SyncJob.source_id.in_(source_ids),
                SyncJob.status == "running",
                SyncJob.job_type != "analytics_upgrade",
            )
            .limit(1)
        )
        is not None
    )


def request_analytics_upgrade_retry(session: Session) -> dict[str, Any]:
    upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID)
    if upgrade is None:
        return {"state": "not_required"}
    if upgrade.state in {"building", "validating", "publishing"}:
        return upgrade_status(upgrade, session)
    upgrade.state = "pending"
    upgrade.error_code = None
    upgrade.error_message = None
    upgrade.next_retry_at = None
    upgrade.completed_at = None
    upgrade.updated_at = utc_now()
    session.commit()
    return upgrade_status(upgrade, session)


def upgrade_status(
    upgrade: AnalyticsUpgrade,
    session: Session | None = None,
) -> dict[str, Any]:
    now = utc_now()
    started_at = _as_utc(upgrade.started_at) if upgrade.started_at else None
    completed_at = _as_utc(upgrade.completed_at) if upgrade.completed_at else None
    source_progress = _source_progress(session, upgrade, now)
    completed_source_ids = set(_json_ids(upgrade.completed_source_ids_json))
    completed_source_ids.update(
        source["source_id"]
        for source in source_progress
        if source["status"] in {"completed", "succeeded"}
    )
    return {
        "state": upgrade.state,
        "source_schema_version": upgrade.source_schema_version,
        "target_schema_version": upgrade.target_schema_version,
        "source_ids": _json_ids(upgrade.source_ids_json),
        # Worker sessions commit SyncJobs as each source finishes, before the upgrade
        # coordinator has collected every future and persisted its own checkpoint.
        # Project those committed jobs so live progress never lags per-source details.
        "completed_source_ids": sorted(completed_source_ids),
        "attempt_count": upgrade.attempt_count,
        "error_code": upgrade.error_code,
        "error_message": upgrade.error_message,
        "candidate_path": upgrade.candidate_path,
        "next_retry_at": _iso_or_none(upgrade.next_retry_at),
        "started_at": _iso_or_none(upgrade.started_at),
        "completed_at": _iso_or_none(upgrade.completed_at),
        "updated_at": _iso_or_none(upgrade.updated_at),
        "duration_seconds": (
            max(0.0, ((completed_at or now) - started_at).total_seconds())
            if started_at is not None
            else None
        ),
        "source_progress": source_progress,
    }


def _source_progress(
    session: Session | None,
    upgrade: AnalyticsUpgrade,
    now: datetime,
) -> list[dict[str, Any]]:
    source_ids = _json_ids(upgrade.source_ids_json)
    if not source_ids:
        return []
    completed_ids = set(_json_ids(upgrade.completed_source_ids_json))
    if session is None:
        return [
            {"source_id": source_id, "status": "completed" if source_id in completed_ids else "pending"}
            for source_id in source_ids
        ]

    sources = {
        source.id: source
        for source in session.scalars(
            select(EnvironmentSource).where(EnvironmentSource.id.in_(source_ids))
        )
    }
    statement = select(SyncJob).where(
        SyncJob.source_id.in_(source_ids),
        SyncJob.job_type == "analytics_upgrade",
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
        job_started = _as_utc(job.started_at) if job else None
        job_completed = _as_utc(job.completed_at) if job and job.completed_at else None
        progress.append({
            "source_id": source_id,
            "label": (source.label or source.uri) if source else None,
            "status": job.status if job else ("completed" if source_id in completed_ids else "pending"),
            "message": job.message if job else None,
            "started_at": _iso_or_none(job.started_at) if job else None,
            "completed_at": _iso_or_none(job.completed_at) if job else None,
            "duration_seconds": (
                max(0.0, ((job_completed or now) - job_started).total_seconds())
                if job_started is not None
                else None
            ),
        })
    return progress


def _eligible_sources(session: Session) -> list[EnvironmentSource]:
    sources = list(
        session.scalars(
            select(EnvironmentSource)
            .where(
                EnvironmentSource.source_kind == "logs",
                EnvironmentSource.enabled.is_(True),
            )
            .order_by(EnvironmentSource.id)
        )
    )
    if not sources:
        return []
    # A parked (paused) source cannot be rebuilt from its storage, so it is out of
    # scope for the complete-candidate upgrade. Excluding it keeps this scope aligned
    # with Monitoring coverage (monitoring/context.source_ids) and lets the upgrade
    # publish the healthy sources instead of failing on one unreachable source.
    paused = paused_source_ids(session, [source.id for source in sources])
    return [
        source
        for source in sources
        if source.id not in paused
        and not source_validation.is_validated_empty_log_source(source)
    ]


def _park_unbuildable_source(
    session: Session,
    source_id: int,
    code: str,
    message: str,
    when: datetime,
) -> None:
    """Pause automatic observation for a source that cannot be materialized."""
    state = session.get(SourceObservation, source_id)
    if state is None:
        state = SourceObservation(source_id=source_id)
        session.add(state)
    state.last_outcome = "error"
    state.error_json = json.dumps(
        {"code": code, "message": message},
        sort_keys=True,
        separators=(",", ":"),
    )
    state.failure_streak = max(
        state.failure_streak or 0, MAX_CONSECUTIVE_OBSERVATION_FAILURES
    )
    state.automatic_observation_paused_at = when
    state.next_observation_at = None
    state.last_attempted_at = when
    state.lease_owner = None
    state.lease_expires_at = None
    session.commit()


def _resumable_candidate_sources(candidate: Path, source_ids: list[int]) -> set[int]:
    """Return in-scope sources already fully built into a compatible partial candidate.

    Lets a retried upgrade skip work it already finished. An incompatible or unreadable
    candidate is discarded so the build starts clean.
    """
    if not candidate.exists():
        return set()
    if not access.cache_is_ready(candidate):
        access.discard_candidate(candidate)
        return set()
    try:
        conn = access.connect(candidate, read_only=True)
    except duckdb.Error:
        access.discard_candidate(candidate)
        return set()
    try:
        return store.cache_source_ids(conn) & set(source_ids)
    except duckdb.Error:
        return set()
    finally:
        conn.close()


def _cache_is_current(path: Path, source_ids: list[int]) -> bool:
    if not path.exists():
        return False
    try:
        conn = access.connect(path, read_only=True)
    except duckdb.Error:
        return False
    try:
        access.validate_source_complete_candidate(conn, source_ids)
        return True
    except (AnalyticsRebuildRequired, duckdb.Error):
        return False
    finally:
        conn.close()


def _live_schema_version(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        conn = access.connect(path, read_only=True)
    except duckdb.Error:
        return None
    try:
        meta = store.analytics_meta(conn)
        return int(meta["schema_version"]) if meta is not None else None
    except duckdb.Error:
        return None
    finally:
        conn.close()


def _get_or_create_upgrade(
    session: Session,
    *,
    source_version: int | None,
    source_ids: list[int],
) -> AnalyticsUpgrade:
    upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID)
    if upgrade is None:
        upgrade = AnalyticsUpgrade(
            id=UPGRADE_ID,
            source_schema_version=source_version,
            target_schema_version=schema.ANALYTICS_SCHEMA_VERSION,
            state="pending",
            source_ids_json=json.dumps(source_ids),
        )
        session.add(upgrade)
        session.flush()
    elif upgrade.target_schema_version != schema.ANALYTICS_SCHEMA_VERSION:
        upgrade.source_schema_version = source_version
        upgrade.target_schema_version = schema.ANALYTICS_SCHEMA_VERSION
    return upgrade


def _begin_attempt(
    upgrade: AnalyticsUpgrade,
    source_ids: list[int],
    candidate: Path,
    started_at: datetime,
) -> None:
    upgrade.target_schema_version = schema.ANALYTICS_SCHEMA_VERSION
    upgrade.state = "building"
    upgrade.source_ids_json = json.dumps(source_ids)
    upgrade.completed_source_ids_json = "[]"
    upgrade.attempt_count += 1
    upgrade.error_code = None
    upgrade.error_message = None
    upgrade.candidate_path = str(candidate)
    upgrade.next_retry_at = None
    upgrade.started_at = started_at
    upgrade.completed_at = None
    upgrade.updated_at = started_at


def _mark_source_completed(upgrade: AnalyticsUpgrade, source_id: int) -> None:
    completed = _json_ids(upgrade.completed_source_ids_json)
    if source_id not in completed:
        completed.append(source_id)
    upgrade.completed_source_ids_json = json.dumps(sorted(completed))
    upgrade.updated_at = utc_now()


def _mark_succeeded(upgrade: AnalyticsUpgrade, completed_at: datetime) -> None:
    upgrade.state = "succeeded"
    upgrade.error_code = None
    upgrade.error_message = None
    upgrade.next_retry_at = None
    upgrade.candidate_path = None
    upgrade.completed_at = completed_at
    upgrade.updated_at = completed_at


def _mark_current_state_succeeded(
    session: Session,
    source_ids: list[int],
    completed_at: datetime,
) -> None:
    upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID)
    if upgrade is None:
        return
    upgrade.source_ids_json = json.dumps(source_ids)
    upgrade.completed_source_ids_json = json.dumps(source_ids)
    # Preserve the real last-publish time across no-op "still current" checks so the
    # UI can show when the cache was actually last upgraded (not the last check).
    already_current = upgrade.state == "succeeded" and upgrade.completed_at is not None
    _mark_succeeded(
        upgrade, upgrade.completed_at if already_current else completed_at
    )
    upgrade.updated_at = completed_at
    session.commit()


def _defer_upgrade(upgrade: AnalyticsUpgrade, checked_at: datetime) -> None:
    """Re-queue the upgrade after a transient sync collision (not a failure)."""
    upgrade.state = "pending"
    upgrade.error_code = None
    upgrade.error_message = None
    upgrade.candidate_path = None
    upgrade.completed_at = None
    upgrade.next_retry_at = checked_at + timedelta(seconds=RETRY_BASE_SECONDS)
    upgrade.updated_at = checked_at


def _mark_failed(
    upgrade: AnalyticsUpgrade,
    exc: Exception,
    failed_at: datetime,
) -> None:
    delay_seconds = min(
        RETRY_MAX_SECONDS,
        RETRY_BASE_SECONDS * (2 ** max(upgrade.attempt_count - 1, 0)),
    )
    upgrade.state = "failed"
    upgrade.error_code = str(getattr(exc, "code", "analytics_upgrade_failed"))
    upgrade.error_message = str(exc)[:500]
    upgrade.next_retry_at = failed_at + timedelta(seconds=delay_seconds)
    upgrade.completed_at = failed_at
    upgrade.updated_at = failed_at


def _json_ids(value: str | None) -> list[int]:
    try:
        loaded = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return sorted({int(item) for item in loaded})


def _iso_or_none(value: datetime | None) -> str | None:
    return _as_utc(value).isoformat() if value is not None else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class AnalyticsUpgradeSourceError(RuntimeError):
    def __init__(self, source_id: int, *, code: str, message: str) -> None:
        super().__init__(f"Log source {source_id}: {message}")
        self.source_id = source_id
        self.code = code


class AnalyticsUpgradeScopeChangedError(RuntimeError):
    code = "source_scope_changed"

    def __init__(self) -> None:
        super().__init__("Enabled Log source scope changed during analytics upgrade")
