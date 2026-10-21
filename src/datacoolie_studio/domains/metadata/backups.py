"""Metadata backup file and record persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from dataclasses import dataclass, field
import logging

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import (
    MetadataBackup,
    MetadataWriteItem,
    MetadataWriteOperation,
)
from datacoolie_studio.domains.metadata.artifact_store import MetadataArtifactStore
from datacoolie_studio.domains.metadata.reader import MetadataReadError


logger = logging.getLogger(__name__)


@dataclass
class ArtifactCleanup:
    refs: set[str] = field(default_factory=set)
    legacy_paths: list[Path] = field(default_factory=list)


def remove_committed_artifacts(session, cleanup, *, artifact_store=None):
    store = artifact_store or MetadataArtifactStore()
    try:
        with store.lifecycle(session) as owned:
            if not owned:
                logger.warning("Metadata artifact cleanup deferred: root ownership is unproven")
                return
            with Session(bind=session.get_bind()) as committed:
                for ref in cleanup.refs:
                    _remove_artifact_if_unreferenced(committed, ref, artifact_store=store)
                for path in cleanup.legacy_paths:
                    path.unlink(missing_ok=True)
    except Exception:
        logger.warning("Metadata artifact cleanup deferred after DB commit", exc_info=True)


def cleanup_orphaned_metadata_artifacts(session, *, limit=256):
    store = MetadataArtifactStore()
    removed = 0
    with store.lifecycle(session) as owned:
        if not owned:
            logger.warning("Metadata artifact sweep deferred: root ownership is unproven")
            return 0
        with Session(bind=session.get_bind()) as committed:
            scanned = 0
            for path in (store.root / "objects").glob("*/*"):
                if scanned >= limit:
                    break
                scanned += 1
                if not path.is_file() or path.is_symlink():
                    continue
                ref = path.relative_to(store.root).as_posix()
                _remove_artifact_if_unreferenced(committed, ref, artifact_store=store)
                removed += int(not path.exists())
    return removed




def list_environment_backups(
    session: Session,
    environment_id: int,
) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(MetadataBackup)
        .where(MetadataBackup.environment_id == environment_id)
        .order_by(MetadataBackup.created_at.desc(), MetadataBackup.id.desc())
    ).all()
    return [backup_response(row) for row in rows]


def backup_response(row: MetadataBackup) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "environment_id": row.environment_id,
        "source_id": row.source_id,
        "source_uri": row.source_uri,
        # Never expose a local absolute path. New records use an opaque,
        # data-root-relative artifact reference; legacy records remain
        # previewable through the server but have no public path identity.
        "backup_path": None,
        "backup_ref": row.backup_ref,
        "backup_hash": row.backup_hash,
        "backup_size": row.backup_size,
        "source_revision": (
            json.loads(row.source_revision_json)
            if row.source_revision_json
            else None
        ),
        "saved_revision": (
            json.loads(row.saved_revision_json)
            if row.saved_revision_json
            else None
        ),
        "created_at": row.created_at,
    }


def delete_backup(
    session: Session,
    backup_id: int,
    *,
    environment_id: int | None = None,
    source_id: int | None = None,
) -> bool:
    backup = session.get(MetadataBackup, backup_id)
    if backup is None:
        return False
    if environment_id is not None and backup.environment_id != environment_id:
        return False
    if source_id is not None and backup.source_id != source_id:
        return False
    cleanup = delete_backup_rows(session, [backup])
    session.commit()
    remove_committed_artifacts(session, cleanup)
    return True




def delete_environment_backups(session: Session, environment_id: int) -> int:
    backups = list(
        session.scalars(
            select(MetadataBackup).where(
                MetadataBackup.environment_id == environment_id
            )
        ).all()
    )
    cleanup = delete_backup_rows(session, backups)
    session.commit()
    remove_committed_artifacts(session, cleanup)
    return len(backups)


def metadata_backup(
    session: Session,
    backup_id: int,
    *,
    environment_id: int | None = None,
    source_id: int | None = None,
) -> MetadataBackup:
    backup = session.get(MetadataBackup, backup_id)
    if backup is None:
        raise MetadataReadError("Metadata backup not found")
    if environment_id is not None and backup.environment_id != environment_id:
        raise MetadataReadError("Metadata backup not found")
    if source_id is not None and backup.source_id != source_id:
        raise MetadataReadError("Metadata backup not found")
    if backup.backup_ref:
        MetadataArtifactStore().read(
            backup.backup_ref,
            expected_sha256=backup.backup_hash or "",
            expected_size=backup.backup_size or 0,
        )
    else:
        path = Path(backup.backup_path).expanduser()
        if not path.exists():
            raise MetadataReadError(
                f"Metadata backup file not found: {backup.backup_path}"
            )
    return backup


def read_backup_bytes(backup: MetadataBackup) -> bytes:
    if backup.backup_ref:
        return MetadataArtifactStore().read(
            backup.backup_ref,
            expected_sha256=backup.backup_hash or "",
            expected_size=backup.backup_size or 0,
        )
    try:
        return Path(backup.backup_path).expanduser().read_bytes()
    except OSError as exc:
        raise MetadataReadError("Metadata backup file is unavailable") from exc


def delete_backup_rows(
    session: Session,
    backups: list[MetadataBackup],
) -> ArtifactCleanup:
    """Detach/delete DB rows; caller commits before removing collected objects."""

    legacy_paths: list[Path] = []
    backup_refs = {backup.backup_ref for backup in backups if backup.backup_ref}
    _detach_terminal_operation_references(session, backup_refs)
    for backup in backups:
        if not backup.backup_ref:
            path = Path(backup.backup_path).expanduser()
            if path.exists():
                legacy_paths.append(path)
        session.delete(backup)
    session.flush()
    return ArtifactCleanup(backup_refs, legacy_paths)


def cleanup_terminal_operation_artifacts(
    session: Session,
    operation: MetadataWriteOperation,
    *,
    artifact_store: MetadataArtifactStore | None = None,
) -> ArtifactCleanup:
    """Detach staged artifacts after a terminal operation; caller owns commit.

    Published metadata backups remain referenced by ``MetadataBackup`` history.
    SQL backups have no user-facing restore record, so they are released after a
    successful operation; all backup refs remain available for abandoned
    operations as recovery evidence. Content-addressed objects are removed only
    after their database references have been detached and no other operation
    or history row uses them.
    """

    if operation.state not in {"succeeded", "abandoned", "expired"}:
        return ArtifactCleanup()
    if artifact_store is None:
        artifact_store = MetadataArtifactStore()

    removable_refs: set[str] = set()
    items = session.scalars(
        select(MetadataWriteItem).where(
            MetadataWriteItem.operation_id == operation.id
        )
    ).all()
    for item in items:
        if item.payload_ref:
            removable_refs.add(item.payload_ref)
            item.payload_ref = None

        keep_backup = operation.state == "abandoned" or (
            operation.state == "succeeded"
            and item.kind in {"metadata", "metadata_restore"}
        )
        if item.backup_ref and not keep_backup:
            removable_refs.add(item.backup_ref)
            item.backup_ref = None

    session.flush()
    return ArtifactCleanup(removable_refs)


def _remove_artifact_if_unreferenced(
    session: Session,
    backup_ref: str,
    *,
    artifact_store: MetadataArtifactStore | None = None,
) -> None:
    referenced_by_backup = session.scalar(
        select(func.count(MetadataBackup.id)).where(
            MetadataBackup.backup_ref == backup_ref
        )
    )
    referenced_by_operation = session.scalar(
        select(func.count(MetadataWriteItem.id)).where(
            or_(
                MetadataWriteItem.backup_ref == backup_ref,
                MetadataWriteItem.payload_ref == backup_ref,
            )
        )
    )
    if not referenced_by_backup and not referenced_by_operation:
        (artifact_store or MetadataArtifactStore()).remove(backup_ref)


def _detach_terminal_operation_references(
    session: Session,
    backup_refs: set[str],
) -> None:
    if not backup_refs:
        return
    terminal_items = session.scalars(
        select(MetadataWriteItem)
        .join(
            MetadataWriteOperation,
            MetadataWriteOperation.id == MetadataWriteItem.operation_id,
        )
        .where(
            MetadataWriteOperation.state.in_(
                ["succeeded", "abandoned", "expired"]
            ),
            or_(
                MetadataWriteItem.backup_ref.in_(backup_refs),
                MetadataWriteItem.payload_ref.in_(backup_refs),
            ),
        )
    ).all()
    for item in terminal_items:
        if item.backup_ref in backup_refs:
            item.backup_ref = None
        if item.payload_ref in backup_refs:
            item.payload_ref = None
