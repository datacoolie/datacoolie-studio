"""Metadata backup load and restore use cases."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource, MetadataWriteOperation
from datacoolie_studio.domains.credentials.store import CredentialSecretStore
from datacoolie_studio.domains.metadata.backups import metadata_backup, read_backup_bytes
from datacoolie_studio.domains.metadata.editor import (
    load_editor_document_from_path,
    load_editor_document_from_raw,
)
from datacoolie_studio.domains.metadata.errors import MetadataConflictError
from datacoolie_studio.domains.metadata.reader import (
    MetadataReadError,
    read_metadata_document_bytes,
)
from datacoolie_studio.domains.metadata.source_policy import metadata_source_uses_strict_validation
from datacoolie_studio.domains.metadata.write_executor import (
    apply_environment_write,
    prepare_backup_restore,
)
from datacoolie_studio.domains.metadata.write_operations import operation_items
from datacoolie_studio.domains.metadata.service import (
    load_materialized_editor_document, merge_environment_editor_documents,
    load_environment_editor_workspace,
)
from datacoolie_studio.domains.sources.repository import list_sources


def load_backup_editor_document(
    session: Session,
    backup_id: int,
    *,
    environment_id: int | None = None,
) -> dict[str, Any]:
    backup = metadata_backup(session, backup_id, environment_id=environment_id)
    source = session.get(EnvironmentSource, backup.source_id)
    if source is None:
        raise MetadataReadError("Metadata source not found")
    if backup.backup_ref:
        content = read_backup_bytes(backup)
        document_info = read_metadata_document_bytes(
            backup.source_uri,
            content,
            strict=metadata_source_uses_strict_validation(source),
        )
        return load_editor_document_from_raw(
            source,
            document_info.payload,
            json.loads(backup.source_revision_json or "{}"),
            document_info=document_info,
        )
    path = Path(backup.backup_path).expanduser()
    return load_editor_document_from_path(
        path,
        source_id=backup.source_id,
        environment_id=backup.environment_id,
        project_id=backup.project_id,
        source_uri=backup.source_uri,
        backup_id=backup.id,
        strict=metadata_source_uses_strict_validation(source),
    )


def _restore_backup_operation(
    session: Session,
    backup_id: int,
    expected_revision: dict[str, Any],
    confirm_restore: bool,
    *,
    secret_store: CredentialSecretStore | None = None,
    environment_id: int | None = None,
) -> tuple[EnvironmentSource, MetadataWriteOperation]:
    if not confirm_restore:
        raise MetadataReadError("Restoring metadata requires confirmation")
    backup = metadata_backup(session, backup_id, environment_id=environment_id)
    source = session.get(EnvironmentSource, backup.source_id)
    if source is None or source.source_kind != "metadata":
        raise MetadataReadError("Metadata source not found")
    operation = prepare_backup_restore(
        session,
        backup_id,
        expected_revision,
        secret_store=secret_store,
        environment_id=environment_id,
    )
    operation = apply_environment_write(
        session,
        operation.operation_key,
        environment_id=operation.environment_id,
        confirm=True,
        secret_store=secret_store,
    )
    if operation.state != "succeeded":
        raise MetadataConflictError(
            "Metadata restore is incomplete; retry or review the operation"
        )
    return source, operation


def restore_backup(
    session: Session,
    backup_id: int,
    expected_revision: dict[str, Any],
    confirm_restore: bool,
    *,
    secret_store: CredentialSecretStore | None = None,
    environment_id: int | None = None,
) -> dict[str, Any]:
    """Restore and preserve the document contract of existing direct callers."""
    source, operation = _restore_backup_operation(
        session, backup_id, expected_revision, confirm_restore,
        secret_store=secret_store, environment_id=environment_id,
    )

    backup = metadata_backup(session, backup_id, environment_id=environment_id)
    restored_bytes = read_backup_bytes(backup)
    restored_document = read_metadata_document_bytes(
        source.uri,
        restored_bytes,
        strict=metadata_source_uses_strict_validation(source),
    )
    items = operation_items(session, operation.id)
    restored_revision = (
        json.loads(items[0].applied_revision_json or "{}")
        if items and items[0].applied_revision_json
        else expected_revision
    )
    return load_editor_document_from_raw(
        source,
        restored_document.payload,
        restored_revision,
        document_info=restored_document,
    )


def restore_backup_workspace(
    session: Session,
    backup_id: int,
    expected_revision: dict[str, Any],
    confirm_restore: bool,
    *,
    environment_id: int,
    secret_store: CredentialSecretStore | None = None,
) -> dict[str, Any]:
    """Compose the response from durable state after the saga's finalization."""
    _restore_backup_operation(
        session, backup_id, expected_revision, confirm_restore,
        environment_id=environment_id, secret_store=secret_store,
    )
    sources = list_sources(session, environment_id, "metadata")
    documents = [load_materialized_editor_document(session, source)
                 for source in sources if source.enabled]
    document = merge_environment_editor_documents(sources, documents)
    return load_environment_editor_workspace(
        session, environment_id, sources, document=document,
    )
