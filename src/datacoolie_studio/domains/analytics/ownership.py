"""Minimal persisted policy for analytics-cache lifecycle ownership."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import AnalyticsUpgrade
from datacoolie_studio.db.session import create_session


# Analytics owns this lookup contract; the upgrade coordinator owns the
# lifecycle transitions and richer progress projection.
ANALYTICS_UPGRADE_ID = 1
ACTIVE_BUILD_STATES = frozenset(
    {"building", "staging", "finalizing", "validating", "publishing"}
)


def cache_upgrade_status(session: Session | None = None) -> dict[str, Any] | None:
    """Read only the state required by cache readers and publishers."""
    owns_session = session is None
    active_session = session or create_session()
    try:
        try:
            upgrade = active_session.get(AnalyticsUpgrade, ANALYTICS_UPGRADE_ID)
        except SQLAlchemyError:
            active_session.rollback()
            return None
        if upgrade is None:
            return None
        return {
            "state": str(upgrade.state),
            "candidate_path": upgrade.candidate_path,
        }
    finally:
        if owns_session:
            active_session.close()


def cache_upgrade_is_building(session: Session | None = None) -> bool:
    status = cache_upgrade_status(session)
    return bool(status and status.get("state") in ACTIVE_BUILD_STATES)


def blocking_upgrade_status(
    path: Path,
    session: Session | None = None,
) -> dict[str, Any] | None:
    """Return the active owner unless this write targets its candidate."""
    status = cache_upgrade_status(session)
    if status is None or status.get("state") not in ACTIVE_BUILD_STATES:
        return None
    candidate = status.get("candidate_path")
    return None if candidate and Path(str(candidate)) == path else status
