"""Persistence transitions and serialized state for analytics upgrades."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import AnalyticsUpgrade, utc_now
from datacoolie_studio.domains.analytics import schema
from datacoolie_studio.domains.analytics_upgrade.constants import (
    RETRY_BASE_SECONDS,
    RETRY_MAX_SECONDS,
    UPGRADE_ID,
)


def get_or_create_upgrade(
    session: Session,
    *,
    source_version: int | None,
    source_ids: list[int],
    source_context: dict[str, dict[str, Any]] | None,
    operation_kind: str,
    reason_code: str,
) -> AnalyticsUpgrade:
    source_context = source_context or {}
    upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID)
    if upgrade is None:
        upgrade = AnalyticsUpgrade(
            id=UPGRADE_ID,
            source_schema_version=source_version,
            target_schema_version=schema.ANALYTICS_SCHEMA_VERSION,
            state="pending",
            operation_kind=operation_kind,
            reason_code=reason_code,
            source_ids_json=json.dumps(source_ids),
            source_context_json=json.dumps(source_context, sort_keys=True),
        )
        session.add(upgrade)
        session.flush()
    else:
        merged_context = json_source_context(upgrade.source_context_json)
        merged_context.update(source_context)
        if merged_context != json_source_context(upgrade.source_context_json):
            upgrade.source_context_json = json.dumps(merged_context, sort_keys=True)
    if upgrade is not None and (
        upgrade.target_schema_version != schema.ANALYTICS_SCHEMA_VERSION
        or upgrade.operation_kind != operation_kind
        or upgrade.reason_code != reason_code
        or json_ids(upgrade.source_ids_json) != sorted(source_ids)
    ):
        upgrade.source_schema_version = source_version
        upgrade.target_schema_version = schema.ANALYTICS_SCHEMA_VERSION
        upgrade.operation_kind = operation_kind
        upgrade.reason_code = reason_code
        upgrade.source_ids_json = json.dumps(source_ids)
        upgrade.episode_attempt_count = 0
        upgrade.next_retry_at = None
    elif upgrade.state in {"succeeded", "current", "completed_with_warnings"}:
        # A later cache regression starts a fresh failure episode, while the terminal
        # status still retains the attempt count for the operation just completed.
        upgrade.episode_attempt_count = 0
        upgrade.next_retry_at = None
    return upgrade

def begin_attempt(
    upgrade: AnalyticsUpgrade,
    source_ids: list[int],
    candidate: Path | None,
    started_at: datetime,
    source_snapshot: dict[str, dict[str, Any]] | None = None,
    batch_checkpoints: dict[str, dict[str, Any]] | None = None,
) -> None:
    upgrade.target_schema_version = schema.ANALYTICS_SCHEMA_VERSION
    upgrade.state = (
        "repairing" if upgrade.operation_kind == "source_repair" else "staging"
    )
    upgrade.source_ids_json = json.dumps(source_ids)
    upgrade.source_snapshot_json = json.dumps(source_snapshot or {}, sort_keys=True)
    upgrade.batch_checkpoint_json = json.dumps(
        batch_checkpoints or {},
        sort_keys=True,
    )
    upgrade.completed_source_ids_json = "[]"
    upgrade.attempt_count += 1
    upgrade.error_code = None
    upgrade.error_message = None
    upgrade.candidate_path = str(candidate) if candidate is not None else None
    upgrade.next_retry_at = None
    upgrade.started_at = started_at
    upgrade.completed_at = None
    upgrade.episode_attempt_count += 1
    upgrade.updated_at = started_at

def mark_batch_completed(
    upgrade: AnalyticsUpgrade,
    source_id: int,
    checkpoint: dict[str, Any],
) -> None:
    from datacoolie_studio.domains.logs.batching import materialization_fingerprint
    checkpoints = json_checkpoints(upgrade.batch_checkpoint_json)
    snapshot = json_source_snapshot(upgrade.source_snapshot_json)
    source_checkpoint = checkpoints.get(str(source_id), {})
    batches = source_checkpoint.get("batches")
    if not isinstance(batches, dict):
        batches = {}
    batch_identity = str(checkpoint.get("batch_identity") or "").strip()
    if not batch_identity:
        return
    batches[batch_identity] = {
        **checkpoint,
        "status": "completed",
        "completed_at": utc_now().isoformat(),
    }
    source_checkpoint.update(
        {
            "status": "building",
            "materialization_fingerprint": materialization_fingerprint(),
            "config_fingerprint": snapshot.get(str(source_id), {}).get(
                "config_fingerprint"
            ),
            "batches": batches,
        }
    )
    checkpoints[str(source_id)] = source_checkpoint
    upgrade.batch_checkpoint_json = json.dumps(checkpoints, sort_keys=True)
    upgrade.updated_at = utc_now()

def mark_source_completed(
    upgrade: AnalyticsUpgrade,
    source_id: int,
    *,
    result: dict[str, Any] | None = None,
) -> None:
    from datacoolie_studio.domains.logs.batching import materialization_fingerprint
    completed = json_ids(upgrade.completed_source_ids_json)
    if source_id not in completed:
        completed.append(source_id)
    upgrade.completed_source_ids_json = json.dumps(sorted(completed))
    checkpoints = json_checkpoints(upgrade.batch_checkpoint_json)
    snapshot = json_source_snapshot(upgrade.source_snapshot_json)
    checkpoint = checkpoints.get(str(source_id), {})
    checkpoint.update(
        {
            "status": "completed",
            "materialization_fingerprint": materialization_fingerprint(),
            "config_fingerprint": snapshot.get(str(source_id), {}).get(
                "config_fingerprint"
            ),
        }
    )
    if result is not None:
        execution = result.get("sync_execution") or {}
        execution_result = execution.get("result") or {}
        revision = execution_result.get("revision")
        if revision is not None:
            checkpoint["revision"] = revision
        record_counts = execution_result.get("record_counts")
        if isinstance(record_counts, dict):
            checkpoint["record_counts"] = record_counts
    checkpoints[str(source_id)] = checkpoint
    upgrade.batch_checkpoint_json = json.dumps(checkpoints, sort_keys=True)
    upgrade.updated_at = utc_now()

def mark_succeeded(upgrade: AnalyticsUpgrade, completed_at: datetime) -> None:
    upgrade.state = "succeeded"
    upgrade.error_code = None
    upgrade.error_message = None
    upgrade.next_retry_at = None
    upgrade.candidate_path = None
    upgrade.completed_at = completed_at
    upgrade.updated_at = completed_at

def mark_completed_with_warnings(
    upgrade: AnalyticsUpgrade,
    *,
    warning_source_ids: list[int],
    completed_at: datetime,
) -> None:
    upgrade.state = "completed_with_warnings"
    upgrade.error_code = "source_replay_partial"
    upgrade.error_message = (
        f"{len(warning_source_ids)} Log source(s) could not be published; "
        "the previous cache was preserved for those sources"
    )
    upgrade.next_retry_at = None
    upgrade.completed_at = completed_at
    upgrade.updated_at = completed_at

def mark_current_state_succeeded(
    session: Session,
    source_ids: list[int],
    completed_at: datetime,
) -> None:
    upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID)
    if upgrade is None:
        return
    previous_source_ids = json_ids(upgrade.source_ids_json)
    if (
        (
            upgrade.state == "completed_with_warnings"
            or (
                upgrade.state == "failed"
                and upgrade.operation_kind == "source_repair"
            )
        )
        and previous_source_ids
        and previous_source_ids != sorted(source_ids)
    ):
        # A failed source may have been paused after repeated failures. The healthy
        # scope is current, but the last partial maintenance result remains visible.
        upgrade.state = "completed_with_warnings"
        upgrade.completed_at = upgrade.completed_at or completed_at
        upgrade.next_retry_at = None
        upgrade.updated_at = completed_at
        session.commit()
        return
    # Preserve the real last-publish time across no-op "still current" checks so the
    # UI can show when the cache was actually last upgraded (not the last check).
    already_current = upgrade.state == "succeeded" and upgrade.completed_at is not None
    if not already_current:
        upgrade.source_ids_json = json.dumps(source_ids)
        upgrade.completed_source_ids_json = json.dumps(source_ids)
    mark_succeeded(
        upgrade, upgrade.completed_at if already_current else completed_at
    )
    upgrade.updated_at = completed_at
    session.commit()

def defer_upgrade(upgrade: AnalyticsUpgrade, checked_at: datetime) -> None:
    """Re-queue the upgrade after a transient sync collision (not a failure)."""
    upgrade.state = "pending"
    upgrade.error_code = None
    upgrade.error_message = None
    upgrade.candidate_path = None
    upgrade.completed_at = None
    upgrade.next_retry_at = checked_at + timedelta(seconds=RETRY_BASE_SECONDS)
    upgrade.updated_at = checked_at

def mark_failed(
    upgrade: AnalyticsUpgrade,
    exc: Exception,
    failed_at: datetime,
) -> None:
    delay_seconds = min(
        RETRY_MAX_SECONDS,
        RETRY_BASE_SECONDS * (2 ** max(upgrade.episode_attempt_count - 1, 0)),
    )
    upgrade.state = "failed"
    upgrade.error_code = str(getattr(exc, "code", "analytics_upgrade_failed"))
    upgrade.error_message = str(exc)[:500]
    upgrade.next_retry_at = failed_at + timedelta(seconds=delay_seconds)
    upgrade.completed_at = failed_at
    upgrade.updated_at = failed_at

def json_ids(value: str | None) -> list[int]:
    try:
        loaded = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return sorted({int(item) for item in loaded})

def json_source_context(value: str | None) -> dict[str, dict[str, Any]]:
    try:
        loaded = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {
        str(source_id): context
        for source_id, context in loaded.items()
        if isinstance(context, dict)
    }

def json_source_snapshot(value: str | None) -> dict[str, dict[str, Any]]:
    return json_object_map(value)

def json_checkpoints(value: str | None) -> dict[str, dict[str, Any]]:
    return json_object_map(value)

def json_object_map(value: str | None) -> dict[str, dict[str, Any]]:
    try:
        loaded = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {
        str(key): item
        for key, item in loaded.items()
        if isinstance(item, dict)
    }

def iso_or_none(value: datetime | None) -> str | None:
    return as_utc(value).isoformat() if value is not None else None

def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

__all__ = [
    "as_utc",
    "begin_attempt",
    "defer_upgrade",
    "get_or_create_upgrade",
    "iso_or_none",
    "json_checkpoints",
    "json_ids",
    "json_object_map",
    "json_source_context",
    "json_source_snapshot",
    "mark_batch_completed",
    "mark_completed_with_warnings",
    "mark_current_state_succeeded",
    "mark_failed",
    "mark_source_completed",
    "mark_succeeded",
]
