from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.environment_caches import (
    invalidate_environment_derived_caches,
)
from datacoolie_studio.domains.logs import control as log_control
from datacoolie_studio.domains.logs import control_codec, pending
from datacoolie_studio.domains.source_observation.repository import reset_observation
from datacoolie_studio.domains.studio_settings.service import source_check_interval_seconds
from datacoolie_studio.domains.sync import service as sync


def upsert_manifest(
    session: Session,
    source_id: int,
    changed_files: list[dict[str, Any]],
    removed_files: list[str],
    file_row_counts: dict[str, int],
    checked_at: datetime,
) -> None:
    if removed_files:
        log_control.delete_manifest_rows(session, source_id, removed_files)
    log_control.upsert_manifest_rows(
        session,
        source_id,
        changed_files,
        file_row_counts,
        seen_at=checked_at,
    )


def apply_deferred_log_control(
    session: Session,
    source: EnvironmentSource,
    control: dict[str, Any],
    *,
    checked_at: datetime,
) -> None:
    """Commit log control state after an analytics candidate becomes live."""

    changed_files = [
        control_codec.deserialize_control_file_state(item)
        for item in control.get("changed_files", [])
        if isinstance(item, dict)
    ]
    changed_system_files = [
        control_codec.deserialize_control_file_state(item)
        for item in control.get("changed_system_files", [])
        if isinstance(item, dict)
    ]
    row_counts = control.get("file_row_counts")
    file_row_counts = (
        {str(uri): int(count or 0) for uri, count in row_counts.items()}
        if isinstance(row_counts, dict)
        else {}
    )
    removed_files = [
        str(file_uri)
        for file_uri in control.get("removed_files", [])
        if file_uri
    ]
    if control.get("replace_source_control"):
        log_control.reset_log_control_state(session, source.id)
    upsert_manifest(
        session,
        source.id,
        [*changed_files, *changed_system_files],
        removed_files,
        file_row_counts,
        checked_at,
    )
    stream_updates = [
        control_codec.deserialize_stream_state_update(item)
        for item in control.get("stream_states", [])
        if isinstance(item, dict)
    ]
    log_control.upsert_stream_states(
        session,
        source.id,
        stream_updates,
        updated_at=checked_at,
    )
    sync.record_source_observation(
        session,
        source=source,
        status="ok",
        revision=(
            control.get("revision")
            if isinstance(control.get("revision"), dict)
            else None
        ),
        error=None,
        checked_at=checked_at,
    )
    reset_observation(
        session,
        source.id,
        due_at=checked_at
        + timedelta(seconds=source_check_interval_seconds(session)),
        pending_changes=False,
    )
    if control.get("needs_publish"):
        invalidate_environment_derived_caches(
            source.environment_id,
            structural=False,
        )
    pending.invalidate_pending_changes(source.id)
