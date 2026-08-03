from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource, MetadataMaterialization, utc_now
from datacoolie_studio.domains.freshness.service import metadata_catalog_version
from datacoolie_studio.domains.metadata.editor import (
    _canonical_editor_columns,
    _metadata_source_name,
    load_editor_document_from_raw,
    load_environment_editor_draft,
    validate_editor_document,
)
from datacoolie_studio.domains.metadata.normalizer import (
    METADATA_NORMALIZER_VERSION,
    enrich_metadata_documents_with_connections,
    normalize_metadata_document,
)
from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.domains.metadata.reader import read_metadata_file  # noqa: F401
from datacoolie_studio.domains.metadata.reader import read_metadata_bytes
from datacoolie_studio.domains.metadata.storage_io import (
    current_revision as storage_current_revision,
    read_source_bytes,
    same_revision,
    storage_revision_from_dict,
    storage_for_source,
)
from datacoolie_studio.domains.credentials.store import CredentialSecretStore
from datacoolie_studio.domains.environment_caches import invalidate_environment_derived_caches
from datacoolie_studio.domains.storage.inventory import storage_diagnostics
from datacoolie_studio.domains.sync import service as sync


METADATA_MATERIALIZATION_SCHEMA_VERSION = "metadata-materialization.v1"


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
                        "message": warning["message"],
                        "cache_status": "stale",
                    }
                )
        except MetadataReadError as exc:
            errors.append({"metadata_source_id": source.id, "uri": source.uri, "message": str(exc)})
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
            documents.append(
                load_cached_editor_document(
                    session, source, secret_store=secret_store
                )
            )
        except MetadataReadError as exc:
            errors.append({"metadata_source_id": source.id, "uri": source.uri, "message": str(exc)})

    return merge_environment_editor_documents(sources, documents, errors=errors)


def merge_environment_editor_documents(
    sources: list[EnvironmentSource],
    documents: list[dict],
    *,
    errors: list[dict] | None = None,
) -> dict:
    sheets = {
        sheet_name: _merge_editor_sheet_documents(documents, sheet_name)
        for sheet_name in ("connections", "dataflows", "schema_hints")
    }
    document = {
        "source": {
            "source_id": 0,
            "environment_id": sources[0].environment_id if sources else 0,
            "project_id": sources[0].environment.project_id if sources and sources[0].environment else None,
            "uri": "environment://metadata",
            "name": "All metadata sources",
            "format": "merged",
            "scope": "environment",
            "read_only": False,
            "revision": {
                "sources": [
                    doc.get("source", {})
                    for doc in documents
                ],
                "errors": errors or [],
            },
        },
        "sheets": sheets,
        "issues": [],
    }
    document["issues"] = validate_editor_document(document)["issues"]
    return document


def store_metadata_materialization_from_bytes(
    session: Session,
    source: EnvironmentSource,
    content: bytes,
    revision: dict,
) -> MetadataMaterialization:
    """Refresh the materialization from bytes already confirmed by a write."""
    raw = read_metadata_bytes(source.uri, content)
    editor_document = load_editor_document_from_raw(source, raw, revision)
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
    session.commit()
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
    return {
        "schema_version": "metadata-editor-workspace.v1",
        "environment_id": environment_id,
        "metadata_catalog_version": metadata_catalog_version(session, sources),
        "document": resolved_document,
        "draft": resolved_draft,
    }


def _refresh_editor_document_routing(source: EnvironmentSource, document: dict) -> dict:
    source_name = _metadata_source_name(source)
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
    materialization, _ = ensure_metadata_materialization_result(
        session,
        source,
        force=force,
        secret_store=secret_store,
    )
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
        raw = read_metadata_bytes(source.uri, content)
        editor_document = load_editor_document_from_raw(source, raw, current)
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


def metadata_materialization(session: Session, source_id: int) -> MetadataMaterialization | None:
    return session.scalar(
        select(MetadataMaterialization).where(MetadataMaterialization.source_id == source_id)
    )


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
            session, source.environment_id, structural=True
        )
    return materialization


def _merge_editor_sheet_documents(documents: list[dict], sheet_name: str) -> dict:
    columns: list[dict] = []
    rows: list[dict] = []
    seen_columns: set[str] = set()
    for document in documents:
        sheet = (document.get("sheets") or {}).get(sheet_name) or {}
        for column in sheet.get("columns") or []:
            if not isinstance(column, dict):
                continue
            key = column.get("key")
            if not key or key in seen_columns:
                continue
            seen_columns.add(str(key))
            columns.append(column)
        rows.extend(row for row in sheet.get("rows") or [] if isinstance(row, dict))
    return {
        "columns": _canonical_editor_columns(sheet_name, columns, rows),
        "rows": rows,
    }


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
