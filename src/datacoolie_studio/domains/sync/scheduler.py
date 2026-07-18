from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from datacoolie_studio.db.models import EnvironmentSource, utc_now
from datacoolie_studio.db.session import create_session
from datacoolie_studio.domains.code_artifacts.service import ensure_code_artifact_snapshot, latest_code_artifact_snapshot
from datacoolie_studio.domains.logs.cache import refresh_log_source_cache
from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.domains.metadata.service import ensure_metadata_snapshot, latest_metadata_snapshot
from datacoolie_studio.domains.studio_settings.service import source_check_interval_seconds
from datacoolie_studio.domains.sync import service as sync

SCHEDULER_INTERVAL_SECONDS = 60
logger = logging.getLogger(__name__)


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    next_source_check_at = 0.0
    while not stop_event.is_set():
        interval_seconds = SCHEDULER_INTERVAL_SECONDS
        try:
            interval_seconds = await asyncio.to_thread(configured_source_check_interval_seconds)
            if time.monotonic() >= next_source_check_at:
                next_source_check_at = time.monotonic() + interval_seconds
                await asyncio.to_thread(run_automatic_source_checks_once)
            await asyncio.to_thread(run_due_schedules_once)
        except Exception:
            logger.exception("Scheduled source refresh pass failed")
        try:
            remaining_source_check = max(0.0, next_source_check_at - time.monotonic())
            timeout = min(SCHEDULER_INTERVAL_SECONDS, remaining_source_check) if next_source_check_at else interval_seconds
            await asyncio.wait_for(stop_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            continue


def configured_source_check_interval_seconds() -> int:
    session = create_session()
    try:
        return source_check_interval_seconds(session)
    finally:
        session.close()


def run_automatic_source_checks_once() -> int:
    """Refresh enabled Metadata and Code only when their source revision changed."""
    session = create_session()
    refreshed = 0
    try:
        sources = list(
            session.scalars(
                select(EnvironmentSource).where(
                    EnvironmentSource.enabled.is_(True),
                    EnvironmentSource.source_kind.in_(("metadata", "code")),
                )
            )
        )
        for source in sources:
            with sync.source_refresh_guard(source.id) as acquired:
                if not acquired:
                    continue
                try:
                    if _refresh_automatic_source(session, source):
                        refreshed += 1
                except Exception:
                    session.rollback()
                    logger.exception("Automatic source refresh failed for source %s", source.id)
        return refreshed
    finally:
        session.close()


def run_due_schedules_once() -> int:
    session = create_session()
    refreshed = 0
    try:
        sources = list(
            session.scalars(
                select(EnvironmentSource).where(
                    EnvironmentSource.enabled.is_(True),
                    EnvironmentSource.source_kind == "logs",
                    EnvironmentSource.sync_schedule_enabled.is_(True),
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


def _refresh_automatic_source(session, source: EnvironmentSource) -> bool:
    if source.source_kind == "metadata":
        previous = latest_metadata_snapshot(session, source.id)
        try:
            current = ensure_metadata_snapshot(session, source)
        except MetadataReadError:
            return False
        return previous is None or previous.id != current.id
    if source.source_kind == "code":
        previous = latest_code_artifact_snapshot(session, source.id)
        current = ensure_code_artifact_snapshot(session, source)
        return current is not None and (previous is None or previous.id != current.id)
    return False


def _refresh_log_source(session, source: EnvironmentSource) -> bool:
    with sync.source_refresh_guard(source.id) as acquired:
        if not acquired:
            return False
        refresh_log_source_cache(session, source, job_type="scheduled_refresh")
    return True
