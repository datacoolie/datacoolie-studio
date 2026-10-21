from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource, MetadataMaterialization, utc_now
from datacoolie_studio.domains.freshness.service import metadata_catalog_version
from datacoolie_studio.domains.metadata.editor import load_editor_document_from_raw
from datacoolie_studio.domains.metadata.drafts import load_environment_editor_draft
from datacoolie_studio.domains.metadata.source_policy import (
    metadata_source_name,
    metadata_source_uses_strict_validation,
    source_is_read_only,
)
from datacoolie_studio.domains.metadata.environment_documents import (
    merge_editor_sheet_documents,
)
from datacoolie_studio.domains.metadata.normalizer import (
    METADATA_NORMALIZER_VERSION,
    enrich_metadata_documents_with_connections,
    normalize_metadata_document,
)
from datacoolie_studio.domains.metadata.ordering import canonicalize_editor_document
from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.domains.metadata.reader import read_metadata_document_bytes
from datacoolie_studio.domains.metadata.storage_io import (
    current_revision as storage_current_revision,
    read_source_bytes,
    storage_revision_from_dict,
    storage_for_source,
)
from datacoolie_studio.domains.credentials.store import CredentialSecretStore
from datacoolie_studio.domains.environment_caches import invalidate_environment_derived_caches
from datacoolie_studio.domains.storage.inventory import storage_diagnostics
from datacoolie_studio.domains.storage.revision_contract import same_revision
from datacoolie_studio.domains.sources.service import stored_source_validation_error
from datacoolie_studio.domains.sync import service as sync


METADATA_MATERIALIZATION_SCHEMA_VERSION = "metadata-materialization.v1"


class MetadataSourcesUnavailableError(MetadataReadError):
    """All enabled metadata sources failed and no usable materialization exists."""

    code = "metadata_sources_unavailable"

    def __init__(self, errors: list[dict]) -> None:
        self.errors = errors
        self.source_ids = [
            int(error["metadata_source_id"])
            for error in errors
            if error.get("metadata_source_id") is not None
        ]
        messages = [
            f"{error.get('uri')}: {error.get('message')}"
            if error.get("uri")
            else str(error.get("message") or "Metadata source read failed")
            for error in errors
        ]
        message = messages[0] if len(messages) == 1 else (
            "Metadata sources could not be read:\n"
            + "\n".join(f"- {item}" for item in messages)
        )
        super().__init__(message)


def load_environment_metadata(
    session: Session,
    sources: list[EnvironmentSource],
    *,
    secret_store: CredentialSecretStore | None = None,
) -> dict:
    documents = []
    errors = []
    for source in sources:
        if not source.enabled:
            continue
        try:
            materialization, warning = ensure_metadata_materialization_result(
                session, source, secret_store=secret_store
            )
            normalized = json.loads(materialization.normalized_metadata_json or "{}")
            if normalized:
                documents.append(normalized)
            if warning:
                errors.append(
                    {
                        "metadata_source_id": source.id,
                        "uri": source.uri,
                        **warning,
                        "cache_status": "stale",
                    }
                )
        except MetadataReadError as exc:
            error = {
                "metadata_source_id": source.id,
                "uri": source.uri,
                "message": str(exc),
            }
            validation_error = stored_source_validation_error(source)
            if validation_error:
                error.update(validation_error)
                error["message"] = str(exc)
            errors.append(error)
    _raise_if_all_metadata_reads_failed(sources, documents, errors)
    documents = enrich_metadata_documents_with_connections(documents)
    connections = [item for doc in documents for item in doc["connections"]]
    dataflows = [item for doc in documents for item in doc["dataflows"]]
    schema_hints = [item for doc in documents for item in doc["schema_hints"]]
    return {
        "summary": {
            "sources": len(documents),
            "connections": len(connections),
            "dataflows": len(dataflows),
            "schema_hints": len(schema_hints),
            "errors": len(errors),
        },
        "sources": [doc["source"] for doc in documents],
        "connections": connections,
        "dataflows": dataflows,
        "schema_hints": schema_hints,
        "errors": errors,
        "_documents": documents,
    }


def load_cached_editor_document(
    session: Session,
    source: EnvironmentSource,
    *,
    secret_store: CredentialSecretStore | None = None,
) -> dict:
    materialization = ensure_metadata_materialization(
        session, source, secret_store=secret_store
    )
    return _refresh_editor_document_routing(source, json.loads(materialization.editor_document_json))


def load_materialized_editor_document(
    session: Session,
    source: EnvironmentSource,
    expected_revision: dict | None = None,
) -> dict:
    """Load the editor cache without checking remote storage."""
    materialization = metadata_materialization(session, source.id)
    if materialization is None:
        raise MetadataReadError(
            f"Metadata cache is unavailable for source: {source.uri}"
        )
    if expected_revision is not None and not _same_revision_json(
        expected_revision, materialization.source_revision_json
    ):
        raise MetadataReadError(
            f"Metadata cache does not match the editor revision: {source.uri}"
        )
    if (
        not _materialization_uses_current_normalizer(materialization)
        or not _materialization_has_editor_routing(materialization)
    ):
        raise MetadataReadError(
            f"Metadata cache must be refreshed before saving: {source.uri}"
        )
    return _refresh_editor_document_routing(
        source, json.loads(materialization.editor_document_json)
    )


def load_environment_editor_document(
    session: Session,
    sources: list[EnvironmentSource],
    *,
    secret_store: CredentialSecretStore | None = None,
) -> dict:
    documents: list[dict] = []
    errors: list[dict] = []
    for source in sources:
        if not source.enabled:
            continue
        try:
            documents.append(load_cached_editor_document(session, source, secret_store=secret_store))
            validation_error = stored_source_validation_error(source)
            if validation_error:
                errors.append(
                    {
                        "metadata_source_id": source.id,
                        "uri": source.uri,
                        **validation_error,
                        "cache_status": "stale",
                    }
                )
        except MetadataReadError as exc:
            error = {
                "metadata_source_id": source.id,
                "uri": source.uri,
                "message": str(exc),
            }
            validation_error = stored_source_validation_error(source)
            if validation_error:
                error.update(validation_error)
                error["message"] = str(exc)
            errors.append(error)

    _raise_if_all_metadata_reads_failed(sources, documents, errors)
    return merge_environment_editor_documents(sources, documents, errors=errors)


def merge_environment_editor_documents(
    sources: list[EnvironmentSource],
    documents: list[dict],
    *,
    errors: list[dict] | None = None,
) -> dict:
    sheets = {
        sheet_name: merge_editor_sheet_documents(documents, sheet_name)
        for sheet_name in ("connections", "dataflows", "schema_hints")
    }
    enabled_metadata_sources = [
        source
        for source in sources
        if source.enabled and source.source_kind == "metadata"
    ]
    environment_read_only = bool(enabled_metadata_sources) and all(
        source_is_read_only(source) for source in enabled_metadata_sources
    )
    metadata_documents = _metadata_document_entries(documents)
    document = canonicalize_editor_document({
        "source": {
            "source_id": 0,
            "environment_id": sources[0].environment_id if sources else 0,
            "project_id": sources[0].environment.project_id if sources and sources[0].environment else None,
            "uri": "environment://metadata",
            "name": "All metadata sources",
            "format": "merged",
            "scope": "environment",
            "read_only": environment_read_only,
            "revision": {
                "sources": [
                    doc.get("source", {})
                    for doc in documents
                ],
                "errors": errors or [],
            },
        },
        "sheets": sheets,
        "metadata_documents": metadata_documents,
        "issues": [],
    })
    document["issues"] = []
    return document


def _metadata_document_entries(documents: list[dict]) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for document in documents:
        source = document.get("source") or {}
        source_id = source.get("source_id")
        if source_id is None:
            continue
        state = document.get("metadata_document")
        if not isinstance(state, dict):
            state = {}
        extras = state.get("extras")
        entries[str(source_id)] = {
            "wrapper": state.get("wrapper") if state.get("wrapper") == "metadata" else None,
            "extras": deepcopy(extras) if isinstance(extras, dict) else {},
        }
    return entries



def store_metadata_materialization_from_bytes(
    session: Session,
    source: EnvironmentSource,
    content: bytes,
    revision: dict,
    *,
    commit: bool = True,
) -> MetadataMaterialization:
    """Refresh the materialization from bytes already confirmed by a write."""
    document_info = read_metadata_document_bytes(
        source.uri,
        content,
        strict=metadata_source_uses_strict_validation(source),
    )
    raw = document_info.payload
    editor_document = load_editor_document_from_raw(
        source,
        raw,
        revision,
        document_info=document_info,
    )
    normalized = normalize_metadata_document(source.id, source.uri, raw)
    materialization = _upsert_metadata_materialization(
        session,
        source,
        revision,
        editor_document,
        normalized,
    )
    sync.record_source_observation(
        session,
        source=source,
        status="ok",
        revision=revision,
        error=None,
        checked_at=utc_now(),
    )
    if commit:
        session.commit()
    else:
        session.flush()
    return materialization


def load_environment_editor_workspace(
    session: Session,
    environment_id: int,
    sources: list[EnvironmentSource],
    *,
    document: dict | None = None,
    draft: dict | None = None,
    secret_store: CredentialSecretStore | None = None,
) -> dict:
    resolved_document = document or load_environment_editor_document(
        session, sources, secret_store=secret_store
    )
    resolved_draft = (
        load_environment_editor_draft(session, environment_id)
        if draft is None
        else draft
    )
    if resolved_draft is not None:
        resolved_draft = canonicalize_editor_document(resolved_draft)
        resolved_draft["issues"] = []
    return {
        "schema_version": "metadata-editor-workspace.v1",
        "environment_id": environment_id,
        "metadata_catalog_version": metadata_catalog_version(session, sources),
        "document": resolved_document,
        "draft": resolved_draft,
    }


def _refresh_editor_document_routing(source: EnvironmentSource, document: dict) -> dict:
    source_name = metadata_source_name(source)
    source_info = document.setdefault("source", {})
    source_info["source_id"] = source.id
    source_info["environment_id"] = source.environment_id
    source_info["project_id"] = source.environment.project_id if source.environment else None
    source_info["uri"] = source.uri
    source_info["name"] = source_name
    source_info["record_counts"] = _editor_sheet_counts(document)
    for sheet in (document.get("sheets") or {}).values():
        if not isinstance(sheet, dict):
            continue
        rows = sheet.get("rows") or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            row["__metadata_source_id"] = source.id
            row["__metadata_source_name"] = source_name
            row["__metadata_source_uri"] = source.uri
            row["__metadata_source_kind"] = "metadata"
            row["__metadata_source_read_only"] = source_is_read_only(source)
    return document


def _editor_sheet_counts(document: dict) -> dict[str, int]:
    result: dict[str, int] = {}
    for sheet_name in ("connections", "dataflows", "schema_hints"):
        rows = ((document.get("sheets") or {}).get(sheet_name) or {}).get("rows") or []
        result[sheet_name] = len(rows) if isinstance(rows, list) else 0
    return result


def ensure_metadata_materialization(
    session: Session,
    source: EnvironmentSource,
    *,
    force: bool = False,
    secret_store: CredentialSecretStore | None = None,
) -> MetadataMaterialization:
    materialization, error = ensure_metadata_materialization_result(
        session,
        source,
        force=force,
        secret_store=secret_store,
    )
    if force and error is not None:
        raise MetadataReadError(str(error.get("message") or "Metadata refresh failed"))
    return materialization


def ensure_metadata_materialization_result(
    session: Session,
    source: EnvironmentSource,
    *,
    force: bool = False,
    secret_store: CredentialSecretStore | None = None,
) -> tuple[MetadataMaterialization, dict | None]:
    if source.source_kind != "metadata":
        raise MetadataReadError("Source is not a metadata source")

    current_materialization = metadata_materialization(session, source.id)
    validation_error = None if force else stored_source_validation_error(source)
    if validation_error is not None:
        if current_materialization is not None:
            return current_materialization, validation_error
        raise MetadataReadError(validation_error["message"])

    try:
        storage = storage_for_source(
            session, source, secret_store=secret_store
        )
        current = storage_current_revision(
            storage, source, include_content_hash=False
        )
    except Exception as exc:
        missing = isinstance(exc, FileNotFoundError)
        error = {
            "message": (
                f"Metadata file not found: {source.uri}"
                if missing
                else "Metadata storage is not readable"
            ),
            "code": (
                "not_found"
                if missing
                else getattr(exc, "code", "metadata_storage_error")
            ),
        }
        if current_materialization is not None:
            if force:
                _record_metadata_refresh_failure(
                    session,
                    source,
                    error,
                    job_type="force_refresh",
                )
            return current_materialization, error
        job = sync.begin_sync_job(
            session, source, "force_refresh" if force else "auto_refresh"
        )
        checked_at = utc_now()
        sync.record_source_observation(
            session,
            source=source,
            status="error",
            revision=None,
            error=error,
            checked_at=checked_at,
        )
        sync.finish_sync_job(
            session,
            job,
            status="failed",
            message=error["message"],
            result={
                "status": "error",
                "message": error["message"],
                "revision": None,
                "error": error,
            },
            completed_at=checked_at,
        )
        raise MetadataReadError(error["message"]) from exc
    if (
        not force
        and current_materialization is not None
        and _same_revision_json(current, current_materialization.source_revision_json)
        and _materialization_uses_current_normalizer(current_materialization)
        and _materialization_has_editor_routing(current_materialization)
    ):
        return current_materialization, None

    job = sync.begin_sync_job(session, source, "force_refresh" if force else "auto_refresh")
    error = _revision_error(source, current)
    if error:
        sync.record_source_observation(session, source=source, status="error", revision=None, error=error, checked_at=utc_now())
        sync.finish_sync_job(
            session,
            job,
            status="failed",
            message=error["message"],
            result={
                "status": "error",
                "message": error["message"],
                "revision": None,
                "error": error,
                "storage_io": storage_diagnostics(storage.adapter),
            },
        )
        if current_materialization is not None:
            return current_materialization, error
        raise MetadataReadError(error["message"])

    try:
        content, current = read_source_bytes(
            storage,
            source,
            expected_revision=storage_revision_from_dict(current),
        )
        document_info = read_metadata_document_bytes(
            source.uri,
            content,
            strict=metadata_source_uses_strict_validation(source),
        )
        raw = document_info.payload
        editor_document = load_editor_document_from_raw(
            source,
            raw,
            current,
            document_info=document_info,
        )
        normalized = normalize_metadata_document(source.id, source.uri, raw)
    except MetadataReadError as exc:
        error = {"message": str(exc), "code": "metadata_read_error"}
        sync.record_source_observation(session, source=source, status="error", revision=None, error=error, checked_at=utc_now())
        sync.finish_sync_job(
            session,
            job,
            status="failed",
            message=str(exc),
            result={
                "status": "error",
                "message": str(exc),
                "revision": None,
                "error": error,
                "storage_io": storage_diagnostics(storage.adapter),
            },
        )
        if current_materialization is not None:
            return current_materialization, error
        raise

    current_materialization = _upsert_metadata_materialization(
        session,
        source,
        current,
        editor_document,
        normalized,
        current_materialization=current_materialization,
    )
    sync.record_source_observation(session, source=source, status="ok", revision=current, error=None, checked_at=utc_now())
    sync.finish_sync_job(
        session,
        job,
        status="succeeded",
        message="Metadata source materialization refreshed",
        result={
            "status": "ok",
            "message": "Metadata source materialization refreshed",
            "revision": current,
            "error": None,
            "storage_io": storage_diagnostics(storage.adapter),
        },
    )
    return current_materialization, None


def _record_metadata_refresh_failure(
    session: Session,
    source: EnvironmentSource,
    error: dict,
    *,
    job_type: str,
) -> None:
    """Persist a failed refresh attempt without replacing last-good data."""

    checked_at = utc_now()
    job = sync.begin_sync_job(session, source, job_type)
    sync.record_source_observation(
        session,
        source=source,
        status="error",
        revision=None,
        error=error,
        checked_at=checked_at,
    )
    sync.finish_sync_job(
        session,
        job,
        status="failed",
        message=str(error.get("message") or "Metadata refresh failed"),
        result={
            "status": "error",
            "message": str(error.get("message") or "Metadata refresh failed"),
            "revision": None,
            "error": error,
        },
        completed_at=checked_at,
    )


def metadata_materialization(session: Session, source_id: int) -> MetadataMaterialization | None:
    return session.scalar(
        select(MetadataMaterialization).where(MetadataMaterialization.source_id == source_id)
    )


def _raise_if_all_metadata_reads_failed(
    sources: list[EnvironmentSource],
    documents: list[dict],
    errors: list[dict],
) -> None:
    enabled_sources = [source for source in sources if source.enabled]
    if (
        enabled_sources
        and errors
        and not documents
        and any(error.get("code") == "source_validation_failed" for error in errors)
    ):
        raise MetadataSourcesUnavailableError(errors)


def _upsert_metadata_materialization(
    session: Session,
    source: EnvironmentSource,
    revision: dict,
    editor_document: dict,
    normalized: dict,
    *,
    current_materialization: MetadataMaterialization | None = None,
) -> MetadataMaterialization:
    materialization = current_materialization or metadata_materialization(
        session, source.id
    )
    revision_json = json.dumps(revision, sort_keys=True)
    fingerprint = _materialization_fingerprint(revision)
    previous_fingerprint = (
        materialization.materialization_fingerprint
        if materialization is not None
        else None
    )
    if materialization is None:
        materialization = MetadataMaterialization(
            source_id=source.id,
            source_revision_json=revision_json,
            normalizer_version=METADATA_NORMALIZER_VERSION,
            materialization_fingerprint=fingerprint,
            editor_document_json=json.dumps(editor_document, ensure_ascii=False),
            normalized_metadata_json=json.dumps(normalized, ensure_ascii=False),
        )
        session.add(materialization)
    else:
        materialization.source_revision_json = revision_json
        materialization.normalizer_version = METADATA_NORMALIZER_VERSION
        materialization.materialization_fingerprint = fingerprint
        materialization.editor_document_json = json.dumps(
            editor_document, ensure_ascii=False
        )
        materialization.normalized_metadata_json = json.dumps(
            normalized, ensure_ascii=False
        )
        materialization.materialized_at = utc_now()
    if previous_fingerprint != fingerprint:
        invalidate_environment_derived_caches(
            source.environment_id, structural=True
        )
    return materialization


def _same_revision_json(current: dict, stored_json: str | None) -> bool:
    if not stored_json:
        return False
    try:
        stored = json.loads(stored_json)
    except json.JSONDecodeError:
        return False
    if current.get("provider_revision") is not None:
        return same_revision(current, stored)
    return (
        current.get("exists") is True
        and stored.get("object_type") == current.get("object_type")
        and stored.get("uri") == current.get("uri")
        and stored.get("path") == current.get("path")
        and stored.get("size") == current.get("size")
        and stored.get("mtime_ns") == current.get("mtime_ns")
    )


def _materialization_uses_current_normalizer(materialization: MetadataMaterialization) -> bool:
    return materialization.normalizer_version == METADATA_NORMALIZER_VERSION


def _materialization_has_editor_routing(materialization: MetadataMaterialization) -> bool:
    try:
        editor = json.loads(materialization.editor_document_json or "{}")
    except json.JSONDecodeError:
        return False
    sheets = editor.get("sheets") if isinstance(editor, dict) else {}
    if not isinstance(sheets, dict):
        return False
    for sheet in sheets.values():
        if not isinstance(sheet, dict):
            continue
        columns = sheet.get("columns")
        if not isinstance(columns, list):
            return False
        if not any(
            isinstance(column, dict)
            and column.get("key") == "__metadata_source_name"
            and column.get("name") == "metadata_source"
            for column in columns
        ):
            return False
    return True


def _materialization_fingerprint(revision: dict) -> str:
    canonical = json.dumps(
        {
            "revision": revision,
            "normalizer_version": METADATA_NORMALIZER_VERSION,
            "schema_version": METADATA_MATERIALIZATION_SCHEMA_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _revision_error(source: EnvironmentSource, revision: dict) -> dict | None:
    if not revision.get("exists"):
        return {"message": f"Metadata file not found: {source.uri}", "code": "not_found"}
    if revision.get("object_type") != "file":
        return {"message": f"Metadata source must be a file: {source.uri}", "code": "invalid_type"}
    return None
