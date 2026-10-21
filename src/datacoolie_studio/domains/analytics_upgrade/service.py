"""Application facade for analytics-cache upgrade lifecycle operations."""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from datacoolie_studio.core.config import analytics_database_path
from datacoolie_studio.domains.analytics import publication
from datacoolie_studio.domains.analytics_upgrade.assessment import (
    resumable_candidate_sources,
)
from datacoolie_studio.domains.analytics_upgrade.constants import (
    NON_PARKING_SOURCE_CODES,
    RETRY_BASE_SECONDS,
    RETRY_MAX_SECONDS,
    UPGRADE_ID,
    UPGRADE_POLL_SECONDS,
)
from datacoolie_studio.domains.analytics_upgrade.errors import (
    AnalyticsUpgradeScopeChangedError,
    AnalyticsUpgradeSourceError,
)
from datacoolie_studio.domains.analytics_upgrade.models import CacheAssessment
from datacoolie_studio.domains.analytics_upgrade.orchestration import (
    run_upgrade_once,
)
from datacoolie_studio.domains.analytics_upgrade.source_replay import (
    UPGRADE_BUILD_CONCURRENCY,
    build_sources_parallel as _build_sources_parallel_impl,
)
from datacoolie_studio.domains.analytics_upgrade.status import (
    analytics_upgrade_is_building,
    current_upgrade_status,
    request_analytics_upgrade_retry,
    upgrade_status,
)
from datacoolie_studio.domains.logs.control_state import apply_deferred_log_control
from datacoolie_studio.domains.logs.ingestion import refresh_log_source_cache


_upgrade_run_lock = threading.Lock()

# These aliases keep the runtime test seam explicit: the facade supplies the
# mutable provider hooks to orchestration, while collaborators remain independent.
_resumable_candidate_sources = resumable_candidate_sources


def _build_sources_parallel(
    source_ids: list[int],
    candidate: Path,
    *,
    job_type: str = "analytics_upgrade",
    resume_checkpoints: dict[str, dict[str, Any]] | None = None,
) -> dict[int, dict[str, Any]]:
    return _build_sources_parallel_impl(
        source_ids,
        candidate,
        job_type=job_type,
        resume_checkpoints=resume_checkpoints,
        refresh_source=refresh_log_source_cache,
    )


async def analytics_upgrade_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        await asyncio.to_thread(run_analytics_upgrade_once)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=UPGRADE_POLL_SECONDS)
        except asyncio.TimeoutError:
            continue


def run_analytics_upgrade_once(*, now: datetime | None = None) -> dict[str, Any]:
    with _upgrade_run_lock:
        return run_upgrade_once(
            now=now,
            database_path_factory=analytics_database_path,
            build_sources_parallel_fn=_build_sources_parallel,
            publication_module=publication,
            apply_control=apply_deferred_log_control,
        )


__all__ = [
    "AnalyticsUpgradeScopeChangedError",
    "AnalyticsUpgradeSourceError",
    "CacheAssessment",
    "NON_PARKING_SOURCE_CODES",
    "RETRY_BASE_SECONDS",
    "RETRY_MAX_SECONDS",
    "UPGRADE_BUILD_CONCURRENCY",
    "UPGRADE_ID",
    "UPGRADE_POLL_SECONDS",
    "analytics_upgrade_is_building",
    "analytics_upgrade_loop",
    "current_upgrade_status",
    "request_analytics_upgrade_retry",
    "run_analytics_upgrade_once",
    "upgrade_status",
]
