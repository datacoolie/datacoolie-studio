from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import (
    EnvironmentSource,
    SourceObservation,
    utc_now,
)
from datacoolie_studio.domains.source_observation.contracts import (
    ObservationResult,
)

OBSERVATION_LEASE_SECONDS = 15 * 60
OBSERVATION_BATCH_SIZE = 100
MAX_CONSECUTIVE_OBSERVATION_FAILURES = 3


def is_periodically_observed(source: EnvironmentSource) -> bool:
    return source.storage_provider != "local" or source.source_kind == "logs"


def _periodic_source_ids():
    return select(EnvironmentSource.id).where(
        EnvironmentSource.enabled.is_(True),
        or_(
            EnvironmentSource.storage_provider != "local",
            EnvironmentSource.source_kind == "logs",
        ),
    )


def ensure_periodic_observations(
    session: Session,
    now: datetime | None = None,
) -> None:
    observed_at = _as_utc(now or utc_now())
    eligible_ids = set(session.scalars(_periodic_source_ids()))
    if not eligible_ids:
        return
    existing_ids = set(
        session.scalars(
            select(SourceObservation.source_id).where(
                SourceObservation.source_id.in_(eligible_ids)
            )
        )
    )
    for source_id in sorted(eligible_ids - existing_ids):
        session.add(
            SourceObservation(
                source_id=source_id,
                next_observation_at=observed_at,
            )
        )
    normalized = session.execute(
        update(SourceObservation)
        .where(
            SourceObservation.source_id.in_(eligible_ids),
            SourceObservation.next_observation_at.is_(None),
            SourceObservation.automatic_observation_paused_at.is_(None),
        )
        .values(next_observation_at=observed_at)
    )
    if eligible_ids - existing_ids or normalized.rowcount:
        try:
            session.commit()
        except IntegrityError:
            session.rollback()


def claim_due_observation_ids(
    session: Session,
    *,
    now: datetime | None = None,
    limit: int = OBSERVATION_BATCH_SIZE,
    lease_owner: str | None = None,
) -> tuple[str, list[int]]:
    observed_at = _as_utc(now or utc_now())
    owner = lease_owner or uuid4().hex
    ensure_periodic_observations(session, observed_at)
    candidates = list(
        session.scalars(
            select(SourceObservation.source_id)
            .join(
                EnvironmentSource,
                EnvironmentSource.id == SourceObservation.source_id,
            )
            .where(
                EnvironmentSource.id.in_(_periodic_source_ids()),
                SourceObservation.automatic_observation_paused_at.is_(None),
                SourceObservation.next_observation_at <= observed_at,
                or_(
                    SourceObservation.lease_expires_at.is_(None),
                    SourceObservation.lease_expires_at <= observed_at,
                ),
            )
            .order_by(
                SourceObservation.next_observation_at,
                SourceObservation.source_id,
            )
            .limit(limit)
        )
    )
    eligible_source_ids = _periodic_source_ids()
    claimed: list[int] = []
    lease_expires_at = observed_at + timedelta(
        seconds=OBSERVATION_LEASE_SECONDS
    )
    for source_id in candidates:
        result = session.execute(
            update(SourceObservation)
            .where(
                SourceObservation.source_id == source_id,
                SourceObservation.source_id.in_(eligible_source_ids),
                SourceObservation.automatic_observation_paused_at.is_(None),
                SourceObservation.next_observation_at <= observed_at,
                or_(
                    SourceObservation.lease_expires_at.is_(None),
                    SourceObservation.lease_expires_at <= observed_at,
                ),
            )
            .values(
                lease_owner=owner,
                lease_expires_at=lease_expires_at,
            )
        )
        if result.rowcount == 1:
            claimed.append(source_id)
    if candidates:
        session.commit()
    return owner, claimed


def claim_local_observation(
    session: Session,
    *,
    source_id: int,
    environment_id: int,
    lease_owner: str,
    now: datetime | None = None,
) -> bool:
    observed_at = _as_utc(now or utc_now())
    eligible_source_ids = select(EnvironmentSource.id).where(
        EnvironmentSource.id == source_id,
        EnvironmentSource.environment_id == environment_id,
        EnvironmentSource.enabled.is_(True),
        EnvironmentSource.storage_provider == "local",
        EnvironmentSource.source_kind.in_({"metadata", "code"}),
    )
    if session.scalar(eligible_source_ids) is None:
        return False
    state = session.get(SourceObservation, source_id)
    if state is None:
        session.add(
            SourceObservation(
                source_id=source_id,
                next_observation_at=None,
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
    result = session.execute(
        update(SourceObservation)
        .where(
            SourceObservation.source_id == source_id,
            SourceObservation.source_id.in_(eligible_source_ids),
            SourceObservation.automatic_observation_paused_at.is_(None),
            or_(
                SourceObservation.lease_expires_at.is_(None),
                SourceObservation.lease_expires_at <= observed_at,
            ),
        )
        .values(
            lease_owner=lease_owner,
            lease_expires_at=observed_at
            + timedelta(seconds=OBSERVATION_LEASE_SECONDS),
        )
    )
    session.commit()
    return result.rowcount == 1


def claim_paused_observation(
    session: Session,
    *,
    source_id: int,
    lease_owner: str,
    now: datetime | None = None,
) -> bool:
    """Claim a paused source for one explicit retry attempt."""

    observed_at = _as_utc(now or utc_now())
    result = session.execute(
        update(SourceObservation)
        .where(
            SourceObservation.source_id == source_id,
            SourceObservation.automatic_observation_paused_at.is_not(None),
            or_(
                SourceObservation.lease_expires_at.is_(None),
                SourceObservation.lease_expires_at <= observed_at,
            ),
        )
        .values(
            lease_owner=lease_owner,
            lease_expires_at=observed_at
            + timedelta(seconds=OBSERVATION_LEASE_SECONDS),
        )
    )
    session.commit()
    return result.rowcount == 1


def complete_observation(
    session: Session,
    *,
    result: ObservationResult,
    lease_owner: str,
    policy: dict[str, int | str],
    permanent_error: bool = False,
) -> None:
    if result.outcome == "skipped":
        release_observation(
            session,
            source_id=result.source_id,
            lease_owner=lease_owner,
        )
        return
    state = session.get(SourceObservation, result.source_id)
    if state is None or state.lease_owner != lease_owner:
        return
    source = session.get(EnvironmentSource, result.source_id)
    if source is None:
        return
    if result.outcome == "error":
        state.failure_streak += 1
        state.unchanged_streak = 0
    else:
        state.failure_streak = 0
        state.automatic_observation_paused_at = None
        state.unchanged_streak = (
            0 if result.outcome == "changed" else state.unchanged_streak + 1
        )
        state.last_succeeded_at = result.completed_at
        state.error_json = None
    state.last_outcome = result.outcome
    state.last_attempted_at = result.completed_at
    state.last_duration_ms = result.duration_ms
    state.inventory_metrics_json = _dump(result.inventory_metrics)
    if result.observed_revision is not None:
        state.observed_revision_json = _dump(result.observed_revision)
    if result.error is not None:
        state.error_json = _dump(result.error)
    if result.pending_changes is not None:
        state.pending_changes = result.pending_changes
    if (
        result.outcome == "error"
        and state.failure_streak >= MAX_CONSECUTIVE_OBSERVATION_FAILURES
    ):
        state.automatic_observation_paused_at = result.completed_at
        state.next_observation_at = None
    else:
        state.next_observation_at = (
            result.completed_at
            + timedelta(
                seconds=observation_delay_seconds(
                    policy,
                    unchanged_streak=state.unchanged_streak,
                    failure_streak=state.failure_streak,
                    permanent_error=permanent_error,
                )
                + _jitter_seconds(
                    result.source_id,
                    observation_delay_seconds(
                        policy,
                        unchanged_streak=state.unchanged_streak,
                        failure_streak=state.failure_streak,
                        permanent_error=permanent_error,
                    ),
                )
            )
            if is_periodically_observed(source)
            else None
        )
    state.lease_owner = None
    state.lease_expires_at = None
    state.updated_at = result.completed_at
    session.commit()


def record_source_evidence(
    session: Session,
    source: EnvironmentSource,
    *,
    status: str,
    revision: dict[str, object] | None,
    error: dict[str, object] | None,
    checked_at: datetime | None = None,
) -> SourceObservation:
    observed_at = _as_utc(checked_at or utc_now())
    state = session.get(SourceObservation, source.id)
    if state is None:
        state = SourceObservation(
            source_id=source.id,
            next_observation_at=(
                observed_at if is_periodically_observed(source) else None
            ),
        )
        session.add(state)
    state.last_attempted_at = observed_at
    if status == "ok":
        state.last_outcome = "changed"
        state.last_succeeded_at = observed_at
        state.pending_changes = False
        state.error_json = None
        if revision is not None:
            state.observed_revision_json = _dump(revision)
    else:
        state.last_outcome = "error"
        state.error_json = _dump(error)
        if revision is not None:
            state.observed_revision_json = _dump(revision)
            state.pending_changes = True
    return state


def release_observation(
    session: Session,
    *,
    source_id: int,
    lease_owner: str,
) -> None:
    session.execute(
        update(SourceObservation)
        .where(
            SourceObservation.source_id == source_id,
            SourceObservation.lease_owner == lease_owner,
        )
        .values(lease_owner=None, lease_expires_at=None)
    )
    session.commit()


def reset_observation(
    session: Session,
    source_id: int,
    *,
    due_at: datetime | None = None,
    pending_changes: bool | None = None,
    clear_evidence: bool = False,
) -> SourceObservation:
    source = session.get(EnvironmentSource, source_id)
    if source is None:
        raise KeyError(source_id)
    state = session.get(SourceObservation, source_id)
    if state is None:
        state = SourceObservation(source_id=source_id)
        session.add(state)
    state.next_observation_at = (
        _as_utc(due_at or utc_now())
        if is_periodically_observed(source)
        else None
    )
    state.unchanged_streak = 0
    state.failure_streak = 0
    state.automatic_observation_paused_at = None
    state.lease_owner = None
    state.lease_expires_at = None
    if pending_changes is not None:
        state.pending_changes = pending_changes
    if clear_evidence:
        state.last_outcome = "never"
        state.pending_changes = pending_changes
        state.observed_revision_json = None
        state.error_json = None
        state.last_attempted_at = None
        state.last_succeeded_at = None
        state.last_duration_ms = None
        state.inventory_metrics_json = None
    return state


def resume_observation(
    session: Session,
    source_id: int,
    *,
    due_at: datetime | None = None,
    pending_changes: bool | None = None,
    lease_owner: str | None = None,
) -> SourceObservation:
    """Restart automatic observation while retaining successful source evidence."""

    state = reset_observation(
        session,
        source_id,
        due_at=due_at,
        pending_changes=pending_changes,
    )
    state.error_json = None
    state.last_outcome = "unchanged" if state.last_succeeded_at else "never"
    if lease_owner is not None:
        state.lease_owner = lease_owner
        state.lease_expires_at = _as_utc(utc_now()) + timedelta(
            seconds=OBSERVATION_LEASE_SECONDS
        )
    return state


def observation_delay_seconds(
    policy: dict[str, int | str],
    *,
    unchanged_streak: int,
    failure_streak: int,
    permanent_error: bool = False,
) -> int:
    base = int(policy["source_check_interval_seconds"])
    maximum = int(policy["source_check_max_interval_seconds"])
    mode = str(policy["source_check_mode"])
    if failure_streak:
        if permanent_error:
            return maximum
        return min(maximum, base * (2 ** min(failure_streak, 8)))
    if mode == "fixed" or unchanged_streak <= 0:
        return base
    if unchanged_streak == 1:
        return min(maximum, max(60, base * 2))
    return maximum


def observations_by_source_ids(
    session: Session,
    source_ids: list[int],
) -> dict[int, SourceObservation]:
    if not source_ids:
        return {}
    return {
        state.source_id: state
        for state in session.scalars(
            select(SourceObservation).where(
                SourceObservation.source_id.in_(source_ids)
            )
        )
    }


def observation_payload(state: SourceObservation | None) -> dict[str, object]:
    if state is None:
        return {
            "status": "never",
            "revision": None,
            "error": None,
            "checked_at": None,
            "last_observed_at": None,
            "next_check_at": None,
            "pending_changes": None,
            "observation_state": "active",
            "observation_failure_count": 0,
            "observation_paused_at": None,
        }
    observation_state = (
        "paused"
        if state.automatic_observation_paused_at is not None
        else "retrying"
        if state.failure_streak > 0
        else "active"
    )
    return {
        "status": state.last_outcome,
        "revision": _load(state.observed_revision_json),
        "error": _load(state.error_json),
        "checked_at": state.last_succeeded_at or state.last_attempted_at,
        "last_observed_at": state.last_attempted_at,
        "next_check_at": state.next_observation_at,
        "pending_changes": state.pending_changes,
        "observation_state": observation_state,
        "observation_failure_count": state.failure_streak,
        "observation_paused_at": state.automatic_observation_paused_at,
    }


def _jitter_seconds(source_id: int, interval: int) -> int:
    maximum = max(0, round(interval * 0.1))
    if maximum == 0:
        return 0
    digest = hashlib.sha256(f"{source_id}:{interval}".encode("ascii")).digest()
    return int.from_bytes(digest[:2], "big") % (maximum + 1)


def _dump(value: dict[str, object] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _load(value: str | None) -> dict[str, object] | None:
    try:
        payload = json.loads(value) if value else None
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
