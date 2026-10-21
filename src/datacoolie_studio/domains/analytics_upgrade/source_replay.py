"""Concurrent source replay and checkpoint persistence."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from datacoolie_studio.core.config import analytics_upgrade_build_concurrency
from datacoolie_studio.db.models import AnalyticsUpgrade, EnvironmentSource
from datacoolie_studio.db.session import create_session
from datacoolie_studio.domains.analytics_upgrade.constants import UPGRADE_ID
from datacoolie_studio.domains.analytics_upgrade.state import mark_batch_completed
from datacoolie_studio.domains.logs.ingestion import refresh_log_source_cache


UPGRADE_BUILD_CONCURRENCY = analytics_upgrade_build_concurrency()
_upgrade_checkpoint_lock = threading.Lock()


def persist_upgrade_batch_checkpoint(
    source_id: int,
    checkpoint: dict[str, Any],
) -> None:
    """Persist a committed candidate batch without sharing the coordinator session."""

    with _upgrade_checkpoint_lock:
        session = create_session()
        try:
            upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID)
            if upgrade is None or upgrade.state not in {
                "building",
                "staging",
            }:
                return
            mark_batch_completed(upgrade, source_id, checkpoint)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def build_sources_parallel(
    source_ids: list[int],
    candidate: Path,
    *,
    job_type: str = "analytics_upgrade",
    resume_checkpoints: dict[str, dict[str, Any]] | None = None,
    refresh_source=refresh_log_source_cache,
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
            result = refresh_source(
                worker_session,
                source,
                job_type=job_type,
                database_path_override=candidate,
                force_analytics_replay=True,
                defer_analytics_generation=job_type == "analytics_upgrade",
                defer_control_commit=job_type == "analytics_upgrade",
                resume_checkpoint=(resume_checkpoints or {}).get(str(source_id)),
                checkpoint_callback=(
                    persist_upgrade_batch_checkpoint
                    if job_type == "analytics_upgrade"
                    else None
                ),
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

__all__ = ["build_sources_parallel", "persist_upgrade_batch_checkpoint"]
