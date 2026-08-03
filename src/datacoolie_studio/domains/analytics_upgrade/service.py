from __future__ import annotations

import json
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from datacoolie_studio.core.config import analytics_database_path
from datacoolie_studio.db.models import AnalyticsUpgrade, EnvironmentSource, utc_now
from datacoolie_studio.db.session import create_session
from datacoolie_studio.domains.analytics import access, schema, store
from datacoolie_studio.domains.analytics.errors import AnalyticsRebuildRequired
from datacoolie_studio.domains.analytics.migrations import validate_registry
from datacoolie_studio.domains.logs.ingestion import refresh_log_source_cache
from datacoolie_studio.domains.sources import service as source_validation


logger = logging.getLogger(__name__)
UPGRADE_ID = 1
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 3600
UPGRADE_POLL_SECONDS = 30


async def analytics_upgrade_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        await asyncio.to_thread(run_analytics_upgrade_once)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=UPGRADE_POLL_SECONDS)
        except asyncio.TimeoutError:
            continue


def run_analytics_upgrade_once(*, now: datetime | None = None) -> dict[str, Any]:
    with access.analytics_maintenance_lock:
        return _run_analytics_upgrade_once(now=now)


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
            return upgrade_status(upgrade)

        candidate = access.candidate_path(live_path)
        _begin_attempt(upgrade, source_ids, candidate, checked_at)
        session.commit()

        if _cache_is_current(live_path, source_ids):
            _mark_succeeded(upgrade, checked_at)
            session.commit()
            return upgrade_status(upgrade)

        access.discard_candidate(candidate)
        for source in sources:
            result = refresh_log_source_cache(
                session,
                source,
                job_type="analytics_upgrade",
                database_path_override=candidate,
                force_analytics_replay=True,
            )
            if result.get("status") != "ok":
                error = result.get("error") or {}
                raise AnalyticsUpgradeSourceError(
                    source.id,
                    code=str(
                        error.get("code")
                        or (
                            "source_sync_busy"
                            if result.get("status") == "running"
                            else "source_replay_failed"
                        )
                    ),
                    message=str(
                        error.get("message")
                        or (
                            "Another source sync is still running"
                            if result.get("status") == "running"
                            else "Log source could not be replayed"
                        )
                    ),
                )
            _mark_source_completed(upgrade, source.id)
            session.commit()

        upgrade.state = "validating"
        upgrade.updated_at = utc_now()
        session.commit()
        store.validate_analytics_candidate(candidate, source_ids)

        session.expire_all()
        current_source_ids = [source.id for source in _eligible_sources(session)]
        if current_source_ids != source_ids:
            raise AnalyticsUpgradeScopeChangedError()

        upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID) or upgrade
        upgrade.state = "publishing"
        upgrade.updated_at = utc_now()
        session.commit()
        access.swap_candidate(candidate, live_path)
        _mark_succeeded(upgrade, utc_now())
        session.commit()
        return upgrade_status(upgrade)
    except Exception as exc:
        session.rollback()
        if candidate is not None:
            access.discard_candidate(candidate)
        upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID)
        if upgrade is not None:
            _mark_failed(upgrade, exc, checked_at)
            session.commit()
            status = upgrade_status(upgrade)
        else:
            status = {
                "state": "failed",
                "error_code": getattr(exc, "code", "analytics_upgrade_failed"),
            }
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
        return upgrade_status(upgrade) if upgrade is not None else None
    finally:
        if owns_session:
            active_session.close()


def request_analytics_upgrade_retry(session: Session) -> dict[str, Any]:
    upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID)
    if upgrade is None:
        return {"state": "not_required"}
    if upgrade.state in {"building", "validating", "publishing"}:
        return upgrade_status(upgrade)
    upgrade.state = "pending"
    upgrade.error_code = None
    upgrade.error_message = None
    upgrade.next_retry_at = None
    upgrade.completed_at = None
    upgrade.updated_at = utc_now()
    session.commit()
    return upgrade_status(upgrade)


def upgrade_status(upgrade: AnalyticsUpgrade) -> dict[str, Any]:
    return {
        "state": upgrade.state,
        "source_schema_version": upgrade.source_schema_version,
        "target_schema_version": upgrade.target_schema_version,
        "source_ids": _json_ids(upgrade.source_ids_json),
        "completed_source_ids": _json_ids(upgrade.completed_source_ids_json),
        "attempt_count": upgrade.attempt_count,
        "error_code": upgrade.error_code,
        "error_message": upgrade.error_message,
        "candidate_path": upgrade.candidate_path,
        "next_retry_at": _iso_or_none(upgrade.next_retry_at),
        "started_at": _iso_or_none(upgrade.started_at),
        "completed_at": _iso_or_none(upgrade.completed_at),
        "updated_at": _iso_or_none(upgrade.updated_at),
    }


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
    return [
        source
        for source in sources
        if not source_validation.is_validated_empty_log_source(source)
    ]


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
    _mark_succeeded(upgrade, completed_at)
    session.commit()


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
        self.code = code


class AnalyticsUpgradeScopeChangedError(RuntimeError):
    code = "source_scope_changed"

    def __init__(self) -> None:
        super().__init__("Enabled Log source scope changed during analytics upgrade")
