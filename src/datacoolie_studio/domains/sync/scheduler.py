from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from datacoolie_studio.db.models import EnvironmentSource, utc_now
from datacoolie_studio.db.session import create_session
from datacoolie_studio.domains.code_artifacts.service import refresh_code_artifact
from datacoolie_studio.domains.logs.cache import refresh_log_source_cache
from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.domains.metadata.service import ensure_metadata_snapshot

SCHEDULER_INTERVAL_SECONDS = 60


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        await asyncio.to_thread(run_due_schedules_once)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=SCHEDULER_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue


def run_due_schedules_once() -> int:
    session = create_session()
    refreshed = 0
    try:
        sources = list(
            session.scalars(
                select(EnvironmentSource).where(
                    EnvironmentSource.enabled.is_(True),
                    EnvironmentSource.sync_schedule_enabled.is_(True),
                )
            )
        )
        now = utc_now()
        for source in sources:
            if not _is_due(source, now):
                continue
            _refresh_source(session, source)
            source.last_scheduled_sync_at = now
            session.commit()
            refreshed += 1
        return refreshed
    finally:
        session.close()


def _is_due(source: EnvironmentSource, now: datetime) -> bool:
    interval = source.sync_interval_minutes or 60
    last = source.last_scheduled_sync_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now - last.astimezone(timezone.utc) >= timedelta(minutes=interval)


def _refresh_source(session, source: EnvironmentSource) -> None:
    if source.source_kind == "metadata":
        try:
            ensure_metadata_snapshot(session, source, force=True)
        except MetadataReadError:
            return
    elif source.source_kind == "logs":
        refresh_log_source_cache(session, source)
    elif source.source_kind == "code":
        refresh_code_artifact(session, source)
