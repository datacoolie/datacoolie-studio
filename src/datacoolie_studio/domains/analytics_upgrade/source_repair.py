"""Source-scoped analytics cache repair."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import AnalyticsUpgrade, utc_now
from datacoolie_studio.domains.analytics_upgrade.errors import AnalyticsUpgradeSourceError
from datacoolie_studio.domains.analytics_upgrade.source_replay import build_sources_parallel
from datacoolie_studio.domains.analytics_upgrade.state import (
    begin_attempt,
    mark_source_completed,
    mark_succeeded,
)
from datacoolie_studio.domains.analytics_upgrade.status import upgrade_status


def run_source_repair_once(
    session: Session,
    upgrade: AnalyticsUpgrade,
    *,
    source_ids: list[int],
    live_path: Path,
    checked_at: datetime,
    build_sources_parallel_fn=build_sources_parallel,
) -> dict[str, Any]:
    """Refresh only sources missing from an otherwise compatible live cache."""

    begin_attempt(upgrade, source_ids, None, checked_at)
    session.commit()
    build_results = build_sources_parallel_fn(
        source_ids,
        live_path,
        job_type="analytics_repair",
    )
    completed_ids: list[int] = []
    for source_id in sorted(build_results):
        result = build_results[source_id]
        if result.get("status") == "ok":
            mark_source_completed(upgrade, source_id)
            completed_ids.append(source_id)
            session.commit()
            continue
        error = result.get("error") or {}
        code = str(
            error.get("code")
            or (
                "source_sync_busy"
                if result.get("status") == "running"
                else "source_repair_failed"
            )
        )
        message = str(
            error.get("message")
            or (
                "Another source sync is still running"
                if result.get("status") == "running"
                else "Log source could not be repaired"
            )
        )
        raise AnalyticsUpgradeSourceError(source_id, code=code, message=message)

    if sorted(completed_ids) != sorted(source_ids):
        raise AnalyticsUpgradeSourceError(
            next(source_id for source_id in source_ids if source_id not in completed_ids),
            code="source_repair_incomplete",
            message="Not every missing Log source was repaired",
        )
    mark_succeeded(upgrade, utc_now())
    session.commit()
    return upgrade_status(upgrade, session)

__all__ = ["run_source_repair_once"]
