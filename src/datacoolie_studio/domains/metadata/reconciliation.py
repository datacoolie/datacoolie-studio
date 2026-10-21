"""Startup recovery for confirmed metadata write operations."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import MetadataWriteOperation
from datacoolie_studio.domains.metadata.write_executor import apply_environment_write
from datacoolie_studio.domains.metadata.write_operations import (
    WriteOperationState,
    ACTIVE_OPERATION_STATES,
)


logger = logging.getLogger(__name__)


def reconcile_metadata_write_operations(
    session: Session,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Resume only confirmed operations and expire stale unconfirmed plans."""

    current_time = now or datetime.now(timezone.utc)
    operations = list(
        session.scalars(
            select(MetadataWriteOperation).where(
                MetadataWriteOperation.state.in_(
                    [state.value for state in ACTIVE_OPERATION_STATES]
                )
            )
        ).all()
    )
    summary = {"resumed": 0, "expired": 0, "deferred": 0, "errors": 0}
    for operation in operations:
        try:
            if operation.state == WriteOperationState.PREPARED.value:
                if _as_utc(operation.expires_at) <= current_time:
                    apply_environment_write(
                        session,
                        operation.operation_key,
                        environment_id=operation.environment_id,
                        confirm=False,
                        now=current_time,
                    )
                    summary["expired"] += 1
                else:
                    summary["deferred"] += 1
                continue
            if operation.confirmed_at is None:
                summary["deferred"] += 1
                continue
            apply_environment_write(
                session,
                operation.operation_key,
                environment_id=operation.environment_id,
                confirm=True,
                now=current_time,
            )
            summary["resumed"] += 1
        except Exception:
            session.rollback()
            summary["errors"] += 1
            logger.warning(
                "Metadata write operation recovery deferred: %s",
                operation.operation_key,
                exc_info=True,
            )
    return summary


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
