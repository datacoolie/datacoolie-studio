"""Persistence contract for metadata save events."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource, MetadataSaveEvent


def record_save_event(
    session: Session,
    source: EnvironmentSource,
    status: str,
    message: str,
    source_revision: dict | None,
    saved_revision: dict | None,
    backup_path: Path | None,
    *,
    commit: bool = True,
) -> MetadataSaveEvent:
    event = MetadataSaveEvent(
        source_id=source.id,
        status=status,
        message=message,
        source_revision_json=(
            json.dumps(source_revision, sort_keys=True)
            if source_revision
            else None
        ),
        saved_revision_json=(
            json.dumps(saved_revision, sort_keys=True)
            if saved_revision
            else None
        ),
        backup_path=str(backup_path) if backup_path else None,
    )
    session.add(event)
    if commit:
        session.commit()
    else:
        session.flush()
    return event
