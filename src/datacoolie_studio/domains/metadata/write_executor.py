"""Prepare and publish durable environment metadata write operations."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import (
    EnvironmentSource,
    MetadataBackup,
    MetadataWriteItem,
    MetadataWriteOperation,
)
from datacoolie_studio.domains.credentials.store import CredentialSecretStore
from datacoolie_studio.domains.environment_caches import invalidate_environment_derived_caches
from datacoolie_studio.domains.metadata.artifact_store import MetadataArtifactStore
from datacoolie_studio.domains.metadata.backups import (
    cleanup_terminal_operation_artifacts,
    remove_committed_artifacts,
    metadata_backup,
    read_backup_bytes,
)
from datacoolie_studio.domains.metadata.document_lifecycle import document_sheets_as_rows, materialize_generated_ids
from datacoolie_studio.domains.metadata.document_serialization import serialize_editor_document_bytes
from datacoolie_studio.domains.metadata.document_rewrites import (
    synchronize_connection_name_references,
)
from datacoolie_studio.domains.metadata.drafts import delete_environment_editor_draft
from datacoolie_studio.domains.metadata.environment_documents import (
    environment_document_for_source,
    resolve_environment_document_sources,
    source_document_changed,
)
from datacoolie_studio.domains.metadata.errors import MetadataConflictError, MetadataValidationError
from datacoolie_studio.domains.metadata.events import record_save_event
from datacoolie_studio.domains.metadata.ordering import canonicalize_editor_document
from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.domains.metadata.reader import read_metadata_document_bytes
from datacoolie_studio.domains.metadata.revisions import environment_revision_sources
from datacoolie_studio.domains.metadata.service import (
    load_materialized_editor_document,
    merge_environment_editor_documents,
    store_metadata_materialization_from_bytes,
)
from datacoolie_studio.domains.metadata.source_policy import source_is_read_only
from datacoolie_studio.domains.metadata.source_policy import metadata_source_uses_strict_validation
from datacoolie_studio.domains.metadata.storage_io import (
    conditional_create_uri,
    conditional_replace_uri,
    read_source_bytes,
    read_storage_bytes,
    storage_for_source,
)
from datacoolie_studio.domains.metadata.write_operations import (
    WriteItemSpec,
    WriteOperationState,
    active_operation,
    create_operation,
    fingerprint,
    operation_expiry,
    operation_items,
    transition_item,
    transition_operation,
    sanitize_error,
    WriteItemState,
    guarded_publication,
)
from datacoolie_studio.domains.sources.persistence import create_source
from datacoolie_studio.domains.sources.storage_binding import binding_to_dict
from datacoolie_studio.domains.storage.errors import StorageConflictError, StorageNotFoundError
from datacoolie_studio.domains.storage.capabilities import metadata_write_safety
from datacoolie_studio.domains.sql.operations import normalize_pending_sql_files
from datacoolie_studio.domains.metadata.sql_writes import prepare_sql_file_writes


from datacoolie_studio.domains.metadata.publication_scope import (
    metadata_descriptor, source_resolution, validate_metadata_scope,
)

logger = logging.getLogger(__name__)


@guarded_publication
def prepare_environment_write(
    session: Session,
    environment_id: int,
    sources: list[EnvironmentSource],
    document: dict[str, Any],
    expected_revision: dict[str, Any],
    *,
    secret_store: CredentialSecretStore | None = None,
    now: datetime | None = None,
    artifact_store: MetadataArtifactStore | None = None,
) -> MetadataWriteOperation:
    """Build and durably persist a source-free write plan.

    This function may read source bytes to establish backups and intended
    payloads, but it never creates, replaces, or deletes an external object.
    """

    sources = [source for source in sources if source.enabled]
    document = canonicalize_editor_document(deepcopy(document))
    materialize_generated_ids(document_sheets_as_rows(document))
    pending_sql = normalize_pending_sql_files(document.get("sql_files"))
    document["sql_files"] = pending_sql
    source_revision_map = environment_revision_sources(expected_revision)
    base_documents: dict[int, dict[str, Any]] = {}
    for source in sources:
        expected = source_revision_map.get(source.id)
        if expected is None:
            raise MetadataConflictError(
                f"Metadata source revision missing for source id: {source.id}"
            )
        base_documents[source.id] = load_materialized_editor_document(
            session,
            source,
            expected,
        )

    resolved_sources = resolve_environment_document_sources(
        session,
        environment_id,
        list(sources),
        document,
        secret_store=secret_store,
        persist_new=False,
    )
    current_sources: dict[int, tuple[Any, bytes, dict[str, Any]]] = {}
    descriptors = {}
    for source in sources:
        with source_resolution(session, source):
            descriptors[source.id] = metadata_descriptor(session, source, secret_store)
            storage = storage_for_source(session, source, secret_store=secret_store)
            try:
                original, current = read_source_bytes(storage, source)
            except (FileNotFoundError, StorageNotFoundError) as exc:
                raise MetadataConflictError(f"Source file no longer exists: {source.uri}") from exc
            if not _same_revision(current, source_revision_map[source.id]):
                raise MetadataConflictError(f"Source file changed before save: {source.uri}")
            current_sources[source.id] = (storage, original, current)
    sql_plans = prepare_sql_file_writes(
        session, sources, pending_sql, secret_store=secret_store,
        reserved_target_uris={source.uri for source in resolved_sources},
    )
    baseline_document = merge_environment_editor_documents(
        sources,
        list(base_documents.values()),
    )
    document = synchronize_connection_name_references(document, baseline_document)
    for source in resolved_sources:
        if source.id not in descriptors:
            with source_resolution(session, source):
                descriptors[source.id] = metadata_descriptor(session, source, secret_store,
                    descriptor=_new_source_descriptor(source))
    artifact_store = artifact_store or MetadataArtifactStore()
    with artifact_store.lifecycle(session, staging=True):
        item_specs: list[WriteItemSpec] = []
        for source in resolved_sources:
            base_document = base_documents.get(source.id) or _empty_source_document(source)
            source_document = environment_document_for_source(document, base_document, source)
            is_new = source.id <= 0
            if not is_new and not source_document_changed(base_document, source_document):
                continue
            if source_is_read_only(source):
                raise MetadataValidationError(
                    "Metadata source is read-only; save the draft instead",
                    {
                        "status": "error",
                        "summary": {"errors": 1, "warnings": 0, "issues": 1},
                        "issues": [
                            {
                                "severity": "error",
                                "sheet": "source",
                                "row_index": 0,
                                "column": "uri",
                                "message": f"Metadata source is read-only: {source.uri}",
                            }
                        ],
                    },
                )

            storage = None
            original_bytes: bytes | None = None
            expected = None
            backup = None
            if not is_new:
                storage, original_bytes, current = current_sources[source.id]
                expected = source_revision_map.get(source.id)
                if expected is None or not _same_revision(current, expected):
                    raise MetadataConflictError(f"Source file changed before save: {source.uri}")
                backup = artifact_store.put(original_bytes)

            payload = serialize_editor_document_bytes(
                source_document,
                source.uri,
                original_bytes=original_bytes,
            )
            payload_artifact = artifact_store.put(payload)
            if not is_new and backup is None:
                raise RuntimeError("Changed metadata source has no backup artifact")
            item_specs.append(
                WriteItemSpec(
                    kind="metadata",
                    source_id=source.id if source.id > 0 else None,
                    source_name=source.label or uri_display_name(source.uri),
                    source_uri=source.uri,
                    target_uri=source.uri,
                    target_descriptor=descriptors[source.id],
                    binding_snapshot=binding_to_dict(source),
                    expected_revision=expected,
                    expected_content_hash=(
                        str(expected.get("content_hash"))
                        if isinstance(expected, dict) and expected.get("content_hash")
                        else None
                    ),
                    expected_size=(
                        int(expected["size"])
                        if isinstance(expected, dict) and expected.get("size") is not None
                        else None
                    ),
                    expected_provider_revision=(
                        str(expected.get("provider_revision"))
                        if isinstance(expected, dict) and expected.get("provider_revision") is not None
                        else None
                    ),
                    backup_ref=backup.ref if backup else None,
                    backup_hash=backup.sha256 if backup else None,
                    backup_size=backup.size if backup else None,
                    payload_ref=payload_artifact.ref,
                    payload_hash=payload_artifact.sha256,
                    payload_size=payload_artifact.size,
                )
            )

        for plan in sql_plans:
            if plan.get("already_satisfied"):
                continue
            content = bytes(plan["content"])
            payload_artifact = artifact_store.put(content)
            original = plan.get("original")
            backup = artifact_store.put(original) if isinstance(original, bytes) else None
            current = plan.get("current")
            source = plan["source"]
            item_specs.append(
                WriteItemSpec(
                    kind="sql",
                    source_id=source.id,
                    source_name=source.label or uri_display_name(source.uri),
                    source_uri=source.uri,
                    target_uri=str(plan["uri"]),
                    target_descriptor=plan["descriptor"],
                    binding_snapshot=plan["binding_snapshot"],
                    expected_revision=plan.get("expected") or current,
                    expected_content_hash=(
                        str(current.get("content_hash"))
                        if isinstance(current, dict) and current.get("content_hash")
                        else None
                    ),
                    expected_size=(
                        int(current["size"])
                        if isinstance(current, dict) and current.get("size") is not None
                        else None
                    ),
                    expected_provider_revision=(
                        str(current.get("provider_revision"))
                        if isinstance(current, dict) and current.get("provider_revision") is not None
                        else None
                    ),
                    backup_ref=backup.ref if backup else None,
                    backup_hash=backup.sha256 if backup else None,
                    backup_size=backup.size if backup else None,
                    payload_ref=payload_artifact.ref,
                    payload_hash=payload_artifact.sha256,
                    payload_size=payload_artifact.size,
                )
            )

        editor_fingerprint = fingerprint(document)
        plan_fingerprint = fingerprint(
            {
                "environment_id": environment_id,
                "expected_revision": expected_revision,
                "items": [
                    {
                        "kind": item.kind,
                        "target_uri": item.target_uri,
                        "payload_hash": item.payload_hash,
                        "expected_revision": item.expected_revision,
                        "target_descriptor": item.target_descriptor,
                        "binding_snapshot": item.binding_snapshot,
                    }
                    for item in item_specs
                ],
            }
        )
        existing = active_operation(session, environment_id)
        if existing is not None:
            if existing.plan_fingerprint == plan_fingerprint:
                return existing
            raise MetadataConflictError(
                "An active metadata write operation already exists; apply, retry, or abandon it first"
            )

        operation = create_operation(
            session,
            environment_id=environment_id,
            editor_fingerprint=editor_fingerprint,
            plan_fingerprint=plan_fingerprint,
            items=item_specs,
            expires_at=operation_expiry(now=now),
        )
        if not item_specs:
            operation.state = WriteOperationState.SUCCEEDED.value
            operation.completed_at = now or datetime.now(timezone.utc)
            operation.updated_at = operation.completed_at
        session.commit()
        return operation


@guarded_publication
def prepare_backup_restore(
    session: Session,
    backup_id: int,
    expected_revision: dict[str, Any],
    *,
    secret_store: CredentialSecretStore | None = None,
    now: datetime | None = None,
    artifact_store: MetadataArtifactStore | None = None,
    storage_override: Any | None = None,
    environment_id: int | None = None,
) -> MetadataWriteOperation:
    """Prepare a backup restore as the same durable, source-free write saga."""

    backup = metadata_backup(session, backup_id, environment_id=environment_id)
    source = session.get(EnvironmentSource, backup.source_id)
    if source is None or source.source_kind != "metadata":
        raise MetadataReadError("Metadata source not found")
    if source.uri != backup.source_uri:
        raise MetadataConflictError(
            "Metadata backup belongs to a previous source URI; select a backup for the current source"
        )
    if source_is_read_only(source):
        raise MetadataReadError("Metadata source is read-only; save the draft instead")
    if active_operation(session, source.environment_id) is not None:
        raise MetadataConflictError(
            "An active metadata write operation already exists; apply, retry, or abandon it first"
        )

    with source_resolution(session, source):
        descriptor = metadata_descriptor(session, source, secret_store)
        storage = storage_override or storage_for_source(
            session, source, secret_store=secret_store
        )
        current_bytes, current_revision = read_source_bytes(storage, source)
    if not _same_revision(current_revision, expected_revision):
        raise MetadataConflictError("Source file changed before restore")

    artifact_store = artifact_store or MetadataArtifactStore()
    with artifact_store.lifecycle(session):
        restored_bytes = read_backup_bytes(backup)
    read_metadata_document_bytes(
        source.uri,
        restored_bytes,
        strict=metadata_source_uses_strict_validation(source),
    )
    with artifact_store.lifecycle(session, staging=True):
        if current_bytes == restored_bytes:
            item_specs: list[WriteItemSpec] = []
        else:
            current_backup = artifact_store.put(current_bytes)
            restored_payload = artifact_store.put(restored_bytes)
            item_specs = [
                WriteItemSpec(
                    kind="metadata_restore",
                    source_id=source.id,
                    source_name=source.label or uri_display_name(source.uri),
                    source_uri=source.uri,
                    target_uri=source.uri,
                    target_descriptor=descriptor,
                    binding_snapshot=binding_to_dict(source),
                    expected_revision=current_revision,
                    expected_content_hash=str(current_revision.get("content_hash") or "") or None,
                    expected_size=int(current_revision["size"])
                    if current_revision.get("size") is not None
                    else None,
                    expected_provider_revision=(
                        str(current_revision.get("provider_revision"))
                        if current_revision.get("provider_revision") is not None
                        else None
                    ),
                    backup_ref=current_backup.ref,
                    backup_hash=current_backup.sha256,
                    backup_size=current_backup.size,
                    payload_ref=restored_payload.ref,
                    payload_hash=restored_payload.sha256,
                    payload_size=restored_payload.size,
                )
            ]

        operation = create_operation(
            session,
            environment_id=source.environment_id,
            editor_fingerprint=fingerprint(
                {"restore_backup_id": backup.id, "source_id": source.id}
            ),
            plan_fingerprint=fingerprint(
                {
                    "restore_backup_id": backup.id,
                    "source_id": source.id,
                    "expected_revision": expected_revision,
                    "payload_hash": item_specs[0].payload_hash if item_specs else None,
                }
            ),
            items=item_specs,
            expires_at=operation_expiry(now=now),
        )
        if not item_specs:
            # A verified no-op restore still finalizes its draft and persisted view.
            # Reuse the bytes just checked; the response must not refresh storage.
            store_metadata_materialization_from_bytes(
                session, source, current_bytes, current_revision, commit=False,
            )
            transition_operation(operation, WriteOperationState.APPLYING, now=now)
            _finalize_operation_rows(
                session, operation, secret_store=secret_store, artifact_store=artifact_store,
            )
        session.commit()
        return operation


def operation_response(
    session: Session,
    operation: MetadataWriteOperation,
) -> dict[str, Any]:
    items = operation_items(session, operation.id)
    return {
        "operation_key": operation.operation_key,
        "environment_id": operation.environment_id,
        "state": operation.state,
        "created_at": operation.created_at,
        "updated_at": operation.updated_at,
        "completed_at": operation.completed_at,
        "expires_at": operation.expires_at,
        "error": (
            {"code": operation.error_code, "message": operation.error_message}
            if operation.error_code or operation.error_message
            else None
        ),
        "summary": _operation_summary(items),
        "items": [_item_response(item) for item in items],
    }


@guarded_publication
def apply_environment_write(
    session: Session,
    operation_key: str,
    *,
    environment_id: int,
    confirm: bool,
    secret_store: CredentialSecretStore | None = None,
    now: datetime | None = None,
    artifact_store: MetadataArtifactStore | None = None,
    storage_overrides: dict[str, Any] | None = None,
) -> MetadataWriteOperation:
    """Apply or retry one confirmed operation sequentially and idempotently."""

    from datacoolie_studio.domains.metadata.write_operations import operation_for_key

    operation = operation_for_key(
        session,
        operation_key,
        environment_id=environment_id,
    )
    if operation is None:
        raise MetadataConflictError("Metadata write operation was not found")
    if operation.state == WriteOperationState.SUCCEEDED.value:
        return operation
    if operation.state in {
        WriteOperationState.ABANDONED.value,
        WriteOperationState.EXPIRED.value,
    }:
        raise MetadataConflictError(
            f"Metadata write operation is already {operation.state}"
        )
    current_time = now or datetime.now(timezone.utc)
    if (
        operation.state == WriteOperationState.PREPARED.value
        and _as_utc(operation.expires_at) <= current_time
    ):
        transition_operation(operation, WriteOperationState.EXPIRED, now=current_time)
        operation.error_code = "operation_expired"
        operation.error_message = "The unconfirmed metadata write plan expired"
        session.commit()
        _finish_operation_cleanup(session, operation, artifact_store)
        return operation
    if not confirm:
        raise MetadataConflictError("Applying metadata requires explicit confirmation")

    artifact_store = artifact_store or MetadataArtifactStore()
    if operation.state in {
        WriteOperationState.PREPARED.value,
        WriteOperationState.PARTIAL.value,
        WriteOperationState.FAILED.value,
    }:
        transition_operation(operation, WriteOperationState.APPLYING, now=current_time)
        if operation.confirmed_at is None:
            operation.confirmed_at = current_time
        session.commit()

    items = operation_items(session, operation.id)
    for item in items:
        if item.state in {
            WriteItemState.APPLIED.value,
            WriteItemState.ABANDONED.value,
            WriteItemState.EXPIRED.value,
        }:
            continue
        try:
            source = _source_for_item(session, operation, item)
            owner_id = _json_object(item.target_descriptor_json).get("storage_source_id")
            owner = session.get(EnvironmentSource, owner_id) if owner_id else source
            with source_resolution(session, owner or source):
                if item.kind == "sql":
                    from datacoolie_studio.domains.metadata.sql_writes import sql_storage_source_for_item

                    source = sql_storage_source_for_item(
                        session, item, operation.environment_id, secret_store=secret_store
                    )
                else:
                    validate_metadata_scope(session, source, item, secret_store)
                storage = (
                    storage_overrides.get(item.target_uri)
                    if storage_overrides is not None
                    else None
                ) or storage_for_source(
                    session,
                    source,
                    secret_store=secret_store,
                    writable=True,
                )
            payload = artifact_store.read(
                item.payload_ref or "",
                expected_sha256=item.payload_hash or "",
                expected_size=item.payload_size or 0,
            )
            needs_reconciliation = item.state in {
                WriteItemState.APPLYING.value,
                WriteItemState.FAILED.value,
                WriteItemState.CONFLICT.value,
            }
            if item.state != WriteItemState.APPLYING.value:
                transition_item(item, WriteItemState.APPLYING, now=current_time)
                session.commit()

            if needs_reconciliation:
                reconciled = _reconcile_applied_item(storage, item, payload)
            else:
                reconciled = None
            if reconciled is not None:
                item.applied_revision_json = json.dumps(
                    reconciled,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                transition_item(item, WriteItemState.APPLIED, now=current_time)
                session.commit()
                continue

            expected = _json_object(item.expected_revision_json)
            if expected:
                applied_receipt = conditional_replace_uri(
                    storage,
                    item.target_uri,
                    payload,
                    expected,
                    verified_revision=expected,
                )
            else:
                applied_receipt = conditional_create_uri(
                    storage,
                    item.target_uri,
                    payload,
                )
            applied_revision = applied_receipt.revision_dict()
            item.applied_revision_json = json.dumps(
                applied_revision,
                ensure_ascii=False,
                sort_keys=True,
            )
            transition_item(item, WriteItemState.APPLIED, now=current_time)
            session.commit()
        except (StorageConflictError, MetadataConflictError) as exc:
            session.rollback()
            item = _reload_item(session, item.id)
            if item.state == WriteItemState.APPLYING.value:
                transition_item(item, WriteItemState.CONFLICT, now=current_time)
            elif item.state in {
                WriteItemState.PREPARED.value,
                WriteItemState.FAILED.value,
            }:
                transition_item(item, WriteItemState.CONFLICT, now=current_time)
            item.error_code, item.error_message = sanitize_error(
                "write_conflict",
                str(exc) if isinstance(exc, MetadataConflictError)
                else "Target changed after the operation was prepared",
            )
            _mark_operation_partial_or_failed(session, operation_key, environment_id, item, str(exc))
            return operation_for_key(session, operation_key, environment_id=environment_id) or operation
        except Exception as exc:
            session.rollback()
            item = _reload_item(session, item.id)
            if item.state == WriteItemState.APPLYING.value:
                transition_item(item, WriteItemState.FAILED, now=current_time)
            elif item.state in {
                WriteItemState.PREPARED.value,
                WriteItemState.FAILED.value,
                WriteItemState.CONFLICT.value,
            }:
                if item.state != WriteItemState.FAILED.value:
                    transition_item(item, WriteItemState.FAILED, now=current_time)
            item.error_code = "write_failed"
            item.error_message = "Metadata write failed; retry or abandon the operation"
            _mark_operation_partial_or_failed(session, operation_key, environment_id, item, str(exc))
            return operation_for_key(session, operation_key, environment_id=environment_id) or operation

    operation = operation_for_key(session, operation_key, environment_id=environment_id) or operation
    try:
        _finalize_operation(
            session,
            operation,
            secret_store=secret_store,
            artifact_store=artifact_store,
        )
    except Exception:
        logger.exception(
            "Metadata write finalization deferred for operation %s",
            operation_key,
        )
        session.rollback()
        operation = operation_for_key(session, operation_key, environment_id=environment_id) or operation
        if operation.state == WriteOperationState.APPLYING.value:
            transition_operation(operation, WriteOperationState.PARTIAL, now=current_time)
        operation.error_code = "finalization_pending"
        operation.error_message = "External writes completed; finalization will retry"
        session.commit()
    return operation


@guarded_publication
def abandon_environment_write(
    session: Session,
    operation_key: str,
    *,
    environment_id: int,
    artifact_store: MetadataArtifactStore | None = None,
) -> MetadataWriteOperation:
    from datacoolie_studio.domains.metadata.write_operations import operation_for_key

    operation = operation_for_key(
        session,
        operation_key,
        environment_id=environment_id,
    )
    if operation is None:
        raise MetadataConflictError("Metadata write operation was not found")
    if operation.state in {
        WriteOperationState.SUCCEEDED.value,
        WriteOperationState.EXPIRED.value,
        WriteOperationState.ABANDONED.value,
    }:
        return operation
    transition_operation(operation, WriteOperationState.ABANDONED)
    for item in operation_items(session, operation.id):
        if item.state not in {
            WriteItemState.APPLIED.value,
            WriteItemState.ABANDONED.value,
        }:
            transition_item(item, WriteItemState.ABANDONED)
    operation.error_code = "operation_abandoned"
    operation.error_message = "Operation abandoned; reload the editor before preparing another write"
    session.commit()
    _finish_operation_cleanup(session, operation, artifact_store)
    return operation



def _finalize_operation(session, operation, *, secret_store, artifact_store):
    from datacoolie_studio.domains.credentials.store import credential_profile_guards
    from datacoolie_studio.db.session import ensure_workspace_transaction

    items = operation_items(session, operation.id)
    profiles = [_json_object(item.binding_snapshot_json).get("credential_profile_id") for item in items]
    with credential_profile_guards(session, profiles):
        for item in items:
            if item.kind in {"metadata", "metadata_restore"}:
                source = _source_for_item(session, operation, item)
                validate_metadata_scope(session, source, item, secret_store, finalizing=True)
        ensure_workspace_transaction(session)
        _finalize_operation_rows(session, operation, secret_store=secret_store, artifact_store=artifact_store)
    _finish_operation_cleanup(session, operation, artifact_store)

def _finalize_operation_rows(
    session: Session,
    operation: MetadataWriteOperation,
    *,
    secret_store: CredentialSecretStore | None,
    artifact_store: MetadataArtifactStore,
) -> None:
    items = operation_items(session, operation.id)
    for item in items:
        source = session.get(EnvironmentSource, item.source_id) if item.source_id else None
        if source is None and item.kind == "metadata":
            descriptor = _json_object(item.target_descriptor_json)
            binding = descriptor.get("binding") if isinstance(descriptor, dict) else None
            source = create_source(
                session,
                operation.environment_id,
                source_kind="metadata",
                uri=item.target_uri,
                label=item.source_name,
                enabled=True,
                source_config=descriptor.get("source_config") if isinstance(descriptor, dict) else None,
                storage=binding if isinstance(binding, dict) else None,
            )
            item.source_id = source.id
        if source is None:
            raise MetadataConflictError("Metadata write source no longer exists")
        applied_revision = _json_object(item.applied_revision_json)
        expected_revision = _json_object(item.expected_revision_json)
        if item.kind in {"metadata", "metadata_restore"}:
            payload = artifact_store.read(
                item.payload_ref or "",
                expected_sha256=item.payload_hash or "",
                expected_size=item.payload_size or 0,
            )
            is_restore = item.kind == "metadata_restore"
            event = record_save_event(
                session,
                source,
                "restored" if is_restore else "saved",
                "Metadata restored from backup" if is_restore else "Metadata source saved",
                expected_revision or None,
                applied_revision or None,
                None,
                commit=False,
            )
            if item.backup_ref:
                session.add(
                    MetadataBackup(
                        project_id=source.environment.project_id if source.environment else 0,
                        environment_id=operation.environment_id,
                        source_id=source.id,
                        source_uri=source.uri,
                        backup_path=item.backup_ref,
                        backup_ref=item.backup_ref,
                        backup_hash=item.backup_hash,
                        backup_size=item.backup_size,
                        source_revision_json=json.dumps(expected_revision, sort_keys=True)
                        if expected_revision
                        else None,
                        saved_revision_json=json.dumps(applied_revision, sort_keys=True)
                        if applied_revision
                        else None,
                        save_event_id=event.id,
                    )
                )
            store_metadata_materialization_from_bytes(
                session,
                source,
                payload,
                applied_revision,
                commit=False,
            )
        else:
            record_save_event(
                session,
                source,
                "sql_saved",
                "Staged SQL file saved",
                expected_revision or None,
                applied_revision or None,
                None,
                commit=False,
            )

    delete_environment_editor_draft(session, operation.environment_id, commit=False)
    invalidate_environment_derived_caches(operation.environment_id, structural=True)
    transition_operation(operation, WriteOperationState.SUCCEEDED)
    operation.error_code = None
    operation.error_message = None
    session.commit()


def _source_for_item(
    session: Session,
    operation: MetadataWriteOperation,
    item: MetadataWriteItem,
) -> EnvironmentSource:
    source = session.get(EnvironmentSource, item.source_id) if item.source_id else None
    if source is not None:
        session.refresh(source)
        return source
    if item.source_id is not None:
        raise MetadataConflictError("Metadata source was deleted; abandon and reprepare")
    descriptor = _json_object(item.target_descriptor_json)
    binding_data = descriptor.get("binding") if isinstance(descriptor, dict) else None
    source = EnvironmentSource(
        id=-item.id,
        environment_id=operation.environment_id,
        source_kind="metadata",
        uri=item.target_uri,
        label=item.source_name,
        enabled=True,
        source_config_json=json.dumps(
            descriptor.get("source_config") if isinstance(descriptor, dict) else {},
            sort_keys=True,
        ),
    )
    if isinstance(binding_data, dict):
        source.storage_provider = str(binding_data.get("provider") or "local")
        source.storage_auth_mode = str(binding_data.get("auth_mode") or "none")
        source.credential_profile_id = binding_data.get("credential_profile_id")
        source.storage_config_json = json.dumps(
            binding_data.get("options") or {},
            sort_keys=True,
        )
    else:
        source.storage_provider = "local"
        source.storage_auth_mode = "none"
    return source


def _reconcile_applied_item(storage: Any, item: MetadataWriteItem, payload: bytes) -> dict[str, Any] | None:
    try:
        current_bytes, current_revision = read_storage_bytes(storage, item.target_uri)
    except (FileNotFoundError, StorageNotFoundError, MetadataReadError):
        return None
    if current_bytes != payload:
        return None
    return current_revision


def _reload_item(session: Session, item_id: int) -> MetadataWriteItem:
    item = session.get(MetadataWriteItem, item_id)
    if item is None:
        raise MetadataConflictError("Metadata write item was not found")
    return item


def _mark_operation_partial_or_failed(
    session: Session,
    operation_key: str,
    environment_id: int,
    item: MetadataWriteItem,
    detail: str,
) -> None:
    from datacoolie_studio.domains.metadata.write_operations import operation_for_key

    operation = operation_for_key(session, operation_key, environment_id=environment_id)
    if operation is None:
        raise MetadataConflictError("Metadata write operation was not found")
    applied = any(
        row.state == WriteItemState.APPLIED.value
        for row in operation_items(session, operation.id)
    )
    if operation.state == WriteOperationState.APPLYING.value:
        transition_operation(
            operation,
            WriteOperationState.PARTIAL if applied else WriteOperationState.FAILED,
        )
    operation.error_code = item.error_code
    operation.error_message = item.error_message
    session.commit()



def _item_response(item: MetadataWriteItem) -> dict[str, Any]:
    binding = _json_object(item.binding_snapshot_json)
    provider = str(binding.get("provider") or "") or None
    is_restore = item.kind == "metadata_restore"
    is_sql = item.kind == "sql"
    return {
        "id": item.id,
        "order": item.item_order,
        "kind": item.kind,
        "state": item.state,
        "source_id": item.source_id,
        "source_name": item.source_name,
        "source_uri": item.source_uri,
        "target_uri": item.target_uri,
        "action": "restore"
        if is_restore
        else "create"
        if not item.expected_revision_json
        else "update",
        "label": item.source_name or uri_display_name(item.target_uri),
        "sheets": ["SQL file"] if is_sql else ["Metadata"],
        "provider": provider,
        "write_safety": metadata_write_safety(provider or "") if provider else None,
        "error": (
            {"code": item.error_code, "message": item.error_message}
            if item.error_code or item.error_message
            else None
        ),
    }


def _operation_summary(items: list[MetadataWriteItem]) -> dict[str, int]:
    return {
        "total": len(items),
        "metadata": sum(item.kind in {"metadata", "metadata_restore"} for item in items),
        "sql": sum(item.kind == "sql" for item in items),
        "pending": sum(item.state not in {WriteItemState.APPLIED.value} for item in items),
        "applied": sum(item.state == WriteItemState.APPLIED.value for item in items),
        "conflicted": sum(item.state == WriteItemState.CONFLICT.value for item in items),
    }


def _empty_source_document(source: EnvironmentSource) -> dict[str, Any]:
    return {
        "source": {
            "source_id": source.id,
            "environment_id": source.environment_id,
            "uri": source.uri,
            "name": source.label or uri_display_name(source.uri),
            "format": "json",
            "revision": {},
        },
        "sheets": {
            "connections": {"columns": [], "rows": []},
            "dataflows": {"columns": [], "rows": []},
            "schema_hints": {"columns": [], "rows": []},
        },
        "metadata_document": {},
        "issues": [],
    }


def _new_source_descriptor(source: EnvironmentSource) -> dict[str, Any]:
    return {
        "source_name": source.label or uri_display_name(source.uri),
        "source_uri": source.uri,
        "source_config": _json_object(source.source_config_json),
        "binding": binding_to_dict(source),
    }


def _same_revision(left: dict[str, Any], right: dict[str, Any]) -> bool:
    from datacoolie_studio.domains.storage.revision_contract import same_revision

    return same_revision(left, right)


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def uri_display_name(uri: str) -> str:
    return uri.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or uri


def _finish_operation_cleanup(session, operation, artifact_store):
    try:
        cleanup = cleanup_terminal_operation_artifacts(session, operation, artifact_store=artifact_store)
        session.commit()
    except Exception:
        session.rollback()
        logger.warning("Terminal metadata cleanup detach deferred", exc_info=True)
        return
    remove_committed_artifacts(session, cleanup, artifact_store=artifact_store)
