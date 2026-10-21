"""Post-swap control-state recovery and replay result rules."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import AnalyticsUpgrade, EnvironmentSource, SyncJob
from datacoolie_studio.domains.analytics import access
from datacoolie_studio.domains.analytics_upgrade.state import (
    json_ids,
    mark_succeeded,
)
from datacoolie_studio.domains.analytics_upgrade.constants import RETRY_BASE_SECONDS
from datacoolie_studio.domains.analytics_upgrade.status import upgrade_status
from datacoolie_studio.domains.logs.control_state import apply_deferred_log_control


def analytics_swap_completed(
    live_path: Path,
    upgrade: AnalyticsUpgrade | None,
) -> bool:
    """Detect the post-swap/pre-control recovery window after a process crash."""

    if upgrade is None or upgrade.state != "publishing" or not upgrade.candidate_path:
        return False
    candidate_path = Path(upgrade.candidate_path)
    if candidate_path.exists() or not live_path.exists():
        return False
    try:
        return access.cache_is_ready(live_path)
    except Exception:  # noqa: BLE001 - recovery must never mask the original state
        return False


def is_sql_only_upgrade(upgrade: AnalyticsUpgrade | None) -> bool:
    """Return whether post-swap recovery must avoid Log control writes."""

    if upgrade is None:
        return False
    if upgrade.operation_kind in {
        "schema_migration",
        "additive_ddl",
        "stored_column_rewrite",
    }:
        return True
    return (
        upgrade.source_schema_version == 17
        and upgrade.target_schema_version >= 18
        and upgrade.reason_code in {
            "schema17_native_rewrite",
            "optional_fields_added",
            "mapping_unchanged",
        }
    )

def recover_deferred_log_control(
    session: Session,
    upgrade: AnalyticsUpgrade,
    *,
    checked_at: datetime,
    apply_control=apply_deferred_log_control,
) -> dict[str, Any] | None:
    """Finish operational Log control publication after a successful file swap."""

    if is_sql_only_upgrade(upgrade):
        # The database generation is already committed. SQL-only migration has
        # no file discovery, so there is no deferred control to recover and no
        # workspace watermark to advance. This branch also handles an absent or
        # stale historical replay payload after a process crash.
        mark_succeeded(upgrade, checked_at)
        session.commit()
        return upgrade_status(upgrade, session)

    source_ids = json_ids(upgrade.source_ids_json)
    if not source_ids:
        mark_succeeded(upgrade, checked_at)
        session.commit()
        return upgrade_status(upgrade, session)
    controls: list[tuple[EnvironmentSource, dict[str, Any]]] = []
    for source_id in source_ids:
        source = session.get(EnvironmentSource, source_id)
        if source is None:
            continue
        control = deferred_control_for_source(session, source_id, None)
        if control is None:
            return None
        controls.append((source, control))
    for source, control in controls:
        apply_control(
            session,
            source,
            control,
            checked_at=checked_at,
        )
    mark_succeeded(upgrade, checked_at)
    session.commit()
    return upgrade_status(upgrade, session)

def mark_control_recovery_pending(
    upgrade: AnalyticsUpgrade,
    exc: Exception,
    checked_at: datetime,
) -> None:
    """Keep a swapped generation recoverable instead of falsely marking it failed."""

    upgrade.state = "publishing"
    upgrade.error_code = "control_state_pending"
    upgrade.error_message = str(exc)[:500]
    upgrade.next_retry_at = checked_at + timedelta(seconds=RETRY_BASE_SECONDS)
    upgrade.completed_at = None
    upgrade.updated_at = checked_at

def source_replay_succeeded(result: dict[str, Any]) -> bool:
    execution = result.get("sync_execution") or {}
    if execution.get("state") != "succeeded":
        return False
    status = result.get("status")
    execution_result = execution.get("result") or {}
    execution_status = execution_result.get("status")
    if status in {"error", "failed"} or execution_status in {"error", "failed"}:
        return False
    if status == "ok":
        return True
    # Deferred source replays do not commit workspace observations before the
    # candidate swap, so their outer status is ``unknown``. The nested execution
    # result and deferred control are the authoritative success signals there.
    if status == "unknown":
        return (
            deferred_control_from_result(result) is not None
            and execution_status in {"ok", "warning"}
        )
    # Admission warnings still produce a deferred control payload. Keep that
    # payload eligible for the post-swap commit while preventing an error result
    # from being treated as successful merely because a stale control exists.
    return status == "warning" and deferred_control_from_result(result) is not None


def source_replay_has_warning(result: dict[str, Any]) -> bool:
    """Return whether a successful replay contains excluded log records."""

    if result.get("status") == "warning":
        return True
    execution = result.get("sync_execution") or {}
    execution_result = execution.get("result") or {}
    if _has_admission_exclusions(execution_result.get("log_admission")):
        return True
    summaries = execution_result.get("file_admission_summaries")
    if isinstance(summaries, dict) and any(
        _has_admission_exclusions(summary) for summary in summaries.values()
    ):
        return True
    control = deferred_control_from_result(result)
    if control is None:
        return False
    for key in ("changed_files", "changed_system_files"):
        states = control.get(key)
        if isinstance(states, list) and any(
            isinstance(state, dict)
            and _has_admission_exclusions(state.get("admission_summary"))
            for state in states
        ):
            return True
    return False


def _has_admission_exclusions(value: Any) -> bool:
    return isinstance(value, dict) and int(value.get("excluded") or 0) > 0

def deferred_control_from_result(
    result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    execution = result.get("sync_execution") or {}
    job_result = execution.get("result") or {}
    control = job_result.get("deferred_control")
    return control if isinstance(control, dict) else None

def deferred_control_for_source(
    session: Session,
    source_id: int,
    result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    control = deferred_control_from_result(result)
    if control is not None:
        return control
    # A retried attempt can skip a source whose candidate rows and checkpoints
    # are already complete. Its successful staging job still owns the control
    # payload that must be committed after this attempt's swap.
    for job in session.scalars(
        select(SyncJob)
        .where(
            SyncJob.source_id == source_id,
            SyncJob.job_type == "analytics_upgrade",
            SyncJob.status == "succeeded",
        )
        .order_by(SyncJob.started_at.desc(), SyncJob.id.desc())
    ):
        try:
            payload = json.loads(job.result_json or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = None
        if not isinstance(payload, dict):
            payload = None
        control = deferred_control_from_result({"sync_execution": {"result": payload}})
        if control is not None:
            return control
    return None

__all__ = [
    "analytics_swap_completed",
    "deferred_control_for_source",
    "deferred_control_from_result",
    "mark_control_recovery_pending",
    "recover_deferred_log_control",
    "is_sql_only_upgrade",
    "source_replay_has_warning",
    "source_replay_succeeded",
]
