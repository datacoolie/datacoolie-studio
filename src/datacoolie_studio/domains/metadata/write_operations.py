"""State machine and repository primitives for durable metadata write sagas."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4
from contextlib import contextmanager
from functools import wraps
from inspect import signature

from sqlalchemy import select, or_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import (
    MetadataWriteItem,
    MetadataWriteOperation,
    utc_now,
)
from datacoolie_studio.domains.metadata.errors import MetadataConflictError
from datacoolie_studio.core.process_lock import ProcessLockTimeoutError, reentrant_process_lock, process_lock_owned
from datacoolie_studio.db.session import ensure_workspace_transaction, workspace_lock_path, refresh_workspace_reads


class WriteOperationState(StrEnum):
    PREPARED = "prepared"
    APPLYING = "applying"
    PARTIAL = "partial"
    SUCCEEDED = "succeeded"
    ABANDONED = "abandoned"
    EXPIRED = "expired"
    FAILED = "failed"


class WriteItemState(StrEnum):
    PREPARED = "prepared"
    APPLYING = "applying"
    APPLIED = "applied"
    CONFLICT = "conflict"
    FAILED = "failed"
    ABANDONED = "abandoned"
    EXPIRED = "expired"


ACTIVE_OPERATION_STATES = frozenset(
    {
        WriteOperationState.PREPARED,
        WriteOperationState.APPLYING,
        WriteOperationState.PARTIAL,
        WriteOperationState.FAILED,
    }
)

_OPERATION_TRANSITIONS: dict[WriteOperationState, frozenset[WriteOperationState]] = {
    WriteOperationState.PREPARED: frozenset(
        {
            WriteOperationState.APPLYING,
            WriteOperationState.ABANDONED,
            WriteOperationState.EXPIRED,
        }
    ),
    WriteOperationState.APPLYING: frozenset(
        {
            WriteOperationState.PARTIAL,
            WriteOperationState.SUCCEEDED,
            WriteOperationState.FAILED,
        }
    ),
    WriteOperationState.PARTIAL: frozenset(
        {
            WriteOperationState.APPLYING,
            WriteOperationState.SUCCEEDED,
            WriteOperationState.ABANDONED,
        }
    ),
    WriteOperationState.FAILED: frozenset(
        {
            WriteOperationState.APPLYING,
            WriteOperationState.ABANDONED,
        }
    ),
    WriteOperationState.SUCCEEDED: frozenset(),
    WriteOperationState.ABANDONED: frozenset(),
    WriteOperationState.EXPIRED: frozenset(),
}

_ITEM_TRANSITIONS: dict[WriteItemState, frozenset[WriteItemState]] = {
    WriteItemState.PREPARED: frozenset(
        {
            WriteItemState.APPLYING,
            WriteItemState.CONFLICT,
            WriteItemState.FAILED,
            WriteItemState.ABANDONED,
            WriteItemState.EXPIRED,
        }
    ),
    WriteItemState.APPLYING: frozenset(
        {
            WriteItemState.APPLIED,
            WriteItemState.CONFLICT,
            WriteItemState.FAILED,
        }
    ),
    WriteItemState.FAILED: frozenset(
        {
            WriteItemState.APPLYING,
            WriteItemState.ABANDONED,
        }
    ),
    WriteItemState.CONFLICT: frozenset(
        {
            WriteItemState.APPLYING,
            WriteItemState.ABANDONED,
        }
    ),
    WriteItemState.APPLIED: frozenset(),
    WriteItemState.ABANDONED: frozenset(),
    WriteItemState.EXPIRED: frozenset(),
}


class WriteOperationTransitionError(MetadataConflictError):
    pass


class ActiveWriteOperationError(RuntimeError):
    """A source cannot be structurally changed while a write targets it."""

    def __init__(self, operation_key: str) -> None:
        self.operation_key = operation_key
        super().__init__(
            "Source is targeted by an active metadata write operation; "
            "apply, retry, or abandon it first"
        )


@contextmanager
def environment_publication_guard(session: Session, environment_id: int):
    """Serialize publication and source mutation across explicit DB checkpoints."""
    try:
        with reentrant_process_lock(
            workspace_lock_path(session, f"environment-{environment_id}"),
            owner_label="Environment publication",
        ):
            yield
    except ProcessLockTimeoutError as exc:
        raise MetadataConflictError("Environment publication is busy; retry after the current request") from exc


def guarded_publication(function):
    """Apply the Environment guard before a publication entrypoint loads state."""
    parameters = signature(function)

    @wraps(function)
    def guarded(*args, **kwargs):
        values = parameters.bind(*args, **kwargs).arguments
        session = values["session"]
        environment_id = values.get("environment_id")
        if environment_id is None:
            from datacoolie_studio.db.models import MetadataBackup

            environment_id = session.scalar(
                select(MetadataBackup.environment_id).where(MetadataBackup.id == values["backup_id"])
            )
        if environment_id is None:
            return function(*args, **kwargs)
        nested = process_lock_owned(workspace_lock_path(session, f"environment-{environment_id}"))
        with environment_publication_guard(session, environment_id):
            if not nested:
                try:
                    refresh_workspace_reads(session)
                except RuntimeError as exc:
                    raise MetadataConflictError(str(exc)) from exc
            return function(*args, **kwargs)

    return guarded


@dataclass(frozen=True)
class WriteItemSpec:
    kind: str
    source_id: int | None
    source_name: str | None
    source_uri: str
    target_uri: str
    target_descriptor: dict[str, Any] | None
    binding_snapshot: dict[str, Any] | None
    expected_revision: dict[str, Any] | None
    expected_content_hash: str | None
    expected_size: int | None
    expected_provider_revision: str | None
    backup_ref: str | None
    backup_hash: str | None
    backup_size: int | None
    payload_ref: str
    payload_hash: str
    payload_size: int


def new_operation_key() -> str:
    return f"mwo_{uuid4().hex}"


def fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def create_operation(
    session: Session,
    *,
    environment_id: int,
    editor_fingerprint: str,
    plan_fingerprint: str,
    items: list[WriteItemSpec],
    expires_at: datetime,
    operation_key: str | None = None,
) -> MetadataWriteOperation:
    ensure_workspace_transaction(session)
    try:
        with session.begin_nested():
            return _create_operation(
                session, environment_id=environment_id, editor_fingerprint=editor_fingerprint,
                plan_fingerprint=plan_fingerprint, items=items, expires_at=expires_at,
                operation_key=operation_key,
            )
    except IntegrityError as exc:
        if "metadata_write_operations.environment_id" not in str(exc.orig):
            raise
        raise WriteOperationTransitionError(
            "A recoverable metadata write already owns this environment"
        ) from exc


def _create_operation(
    session: Session,
    *,
    environment_id: int,
    editor_fingerprint: str,
    plan_fingerprint: str,
    items: list[WriteItemSpec],
    expires_at: datetime,
    operation_key: str | None,
) -> MetadataWriteOperation:
    active = session.scalar(
        select(MetadataWriteOperation.id).where(
            MetadataWriteOperation.environment_id == environment_id,
            MetadataWriteOperation.state.in_(
                [state.value for state in ACTIVE_OPERATION_STATES]
            ),
        )
    )
    if active is not None:
        raise WriteOperationTransitionError(
            "An active metadata write operation already exists for this environment"
        )
    if len({item.target_uri for item in items}) != len(items):
        raise WriteOperationTransitionError(
            "A metadata write operation cannot contain duplicate target URIs"
        )
    operation = MetadataWriteOperation(
        operation_key=operation_key or new_operation_key(),
        environment_id=environment_id,
        state=WriteOperationState.PREPARED.value,
        editor_fingerprint=editor_fingerprint,
        plan_fingerprint=plan_fingerprint,
        expires_at=expires_at,
    )
    session.add(operation)
    session.flush()
    for item_order, spec in enumerate(items):
        session.add(
            MetadataWriteItem(
                operation_id=operation.id,
                item_order=item_order,
                kind=spec.kind,
                state=WriteItemState.PREPARED.value,
                source_id=spec.source_id,
                source_name=spec.source_name,
                source_uri=spec.source_uri,
                target_uri=spec.target_uri,
                target_descriptor_json=_json_text(spec.target_descriptor),
                binding_snapshot_json=_json_text(spec.binding_snapshot),
                expected_revision_json=_json_text(spec.expected_revision),
                expected_content_hash=spec.expected_content_hash,
                expected_size=spec.expected_size,
                expected_provider_revision=spec.expected_provider_revision,
                backup_ref=spec.backup_ref,
                backup_hash=spec.backup_hash,
                backup_size=spec.backup_size,
                payload_ref=spec.payload_ref,
                payload_hash=spec.payload_hash,
                payload_size=spec.payload_size,
            )
        )
    session.flush()
    return operation


def operation_for_key(
    session: Session,
    operation_key: str,
    *,
    environment_id: int | None = None,
) -> MetadataWriteOperation | None:
    statement = select(MetadataWriteOperation).where(
        MetadataWriteOperation.operation_key == operation_key
    )
    if environment_id is not None:
        statement = statement.where(
            MetadataWriteOperation.environment_id == environment_id
        )
    return session.scalar(statement.execution_options(populate_existing=True))


def operation_items(
    session: Session,
    operation_id: int,
) -> list[MetadataWriteItem]:
    return list(
        session.scalars(
            select(MetadataWriteItem)
            .where(MetadataWriteItem.operation_id == operation_id)
            .order_by(MetadataWriteItem.item_order)
            .execution_options(populate_existing=True)
        ).all()
    )


def active_operation(
    session: Session,
    environment_id: int,
) -> MetadataWriteOperation | None:
    return session.scalar(
        select(MetadataWriteOperation)
        .where(
            MetadataWriteOperation.environment_id == environment_id,
            MetadataWriteOperation.state.in_(
                [state.value for state in ACTIVE_OPERATION_STATES]
            ),
        )
        .order_by(MetadataWriteOperation.created_at.desc())
    )


def ensure_source_not_in_active_write(
    session: Session,
    source_id: int,
    *,
    environment_id: int | None = None,
) -> None:
    """Reject source lifecycle mutations that would invalidate a prepared plan."""

    statement = (
        select(MetadataWriteOperation.operation_key)
        .join(
            MetadataWriteItem,
            MetadataWriteItem.operation_id == MetadataWriteOperation.id,
        )
        .where(
            or_(
                MetadataWriteItem.source_id == source_id,
                func.json_extract(MetadataWriteItem.target_descriptor_json, "$.storage_source_id") == source_id,
            ),
            MetadataWriteOperation.state.in_(
                [state.value for state in ACTIVE_OPERATION_STATES]
            ),
        )
        .order_by(MetadataWriteOperation.created_at.desc())
        .limit(1)
    )
    if environment_id is not None:
        statement = statement.where(
            MetadataWriteOperation.environment_id == environment_id
        )
    row = session.execute(statement).first()
    if row is not None:
        raise ActiveWriteOperationError(str(row[0]))


def transition_operation(
    operation: MetadataWriteOperation,
    target: WriteOperationState,
    *,
    now: datetime | None = None,
) -> None:
    current = WriteOperationState(operation.state)
    if target not in _OPERATION_TRANSITIONS[current]:
        raise WriteOperationTransitionError(
            f"Invalid metadata write operation transition: {current.value} -> {target.value}"
        )
    operation.state = target.value
    operation.updated_at = now or utc_now()
    if target in {
        WriteOperationState.SUCCEEDED,
        WriteOperationState.ABANDONED,
        WriteOperationState.EXPIRED,
    }:
        operation.completed_at = operation.updated_at


def transition_item(
    item: MetadataWriteItem,
    target: WriteItemState,
    *,
    now: datetime | None = None,
) -> None:
    current = WriteItemState(item.state)
    if target not in _ITEM_TRANSITIONS[current]:
        raise WriteOperationTransitionError(
            f"Invalid metadata write item transition: {current.value} -> {target.value}"
        )
    item.state = target.value
    item.updated_at = now or utc_now()


def mark_operation_error(
    operation: MetadataWriteOperation,
    *,
    code: str,
    message: str,
) -> None:
    operation.error_code, operation.error_message = sanitize_error(code, message)
    operation.updated_at = utc_now()


def mark_item_error(
    item: MetadataWriteItem,
    *,
    code: str,
    message: str,
) -> None:
    item.error_code, item.error_message = sanitize_error(code, message)
    item.updated_at = utc_now()


def sanitize_error(code: str, message: str) -> tuple[str, str]:
    normalized_code = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(code).strip())[:100]
    normalized_code = normalized_code or "metadata_write_error"
    normalized_message = " ".join(str(message).split())[:1000]
    return normalized_code, normalized_message


def operation_expiry(*, now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)) + timedelta(hours=24)


def _json_text(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
