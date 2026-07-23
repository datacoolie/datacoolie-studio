from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource, MetadataMaterialization, utc_now
from datacoolie_studio.domains.freshness.service import metadata_catalog_version
from datacoolie_studio.domains.metadata.editor import (
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
from datacoolie_studio.domains.metadata.reader import MetadataReadError, read_metadata_file
from datacoolie_studio.domains.environment_caches import invalidate_environment_derived_caches
from datacoolie_studio.domains.sources import service as source_validation
from datacoolie_studio.domains.sync import service as sync


METADATA_MATERIALIZATION_SCHEMA_VERSION = "metadata-materialization.v1"


def load_environment_metadata(session: Session, sources: list[EnvironmentSource]) -> dict:
    documents = []
    errors = []
    for source in sources:
        if not source.enabled:
            continue
        try:
            materialization, warning = _ensure_metadata_materialization_result(session, source)
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


def load_cached_editor_document(session: Session, source: EnvironmentSource) -> dict:
    materialization = ensure_metadata_materialization(session, source)
    return _refresh_editor_document_routing(source, json.loads(materialization.editor_document_json))


def load_environment_editor_document(session: Session, sources: list[EnvironmentSource]) -> dict:
    documents: list[dict] = []
    errors: list[dict] = []
    for source in sources:
        if not source.enabled:
            continue
        try:
            documents.append(load_cached_editor_document(session, source))
        except MetadataReadError as exc:
            errors.append({"metadata_source_id": source.id, "uri": source.uri, "message": str(exc)})

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
                "errors": errors,
            },
        },
        "sheets": sheets,
        "issues": [],
    }
    document["issues"] = validate_editor_document(document)["issues"]
    return document


def load_environment_editor_workspace(
    session: Session,
    environment_id: int,
    sources: list[EnvironmentSource],
    *,
    document: dict | None = None,
    draft: dict | None = None,
) -> dict:
    resolved_document = document or load_environment_editor_document(session, sources)
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
) -> MetadataMaterialization:
    materialization, _ = _ensure_metadata_materialization_result(session, source, force=force)
    return materialization


def _ensure_metadata_materialization_result(
    session: Session,
    source: EnvironmentSource,
    *,
    force: bool = False,
) -> tuple[MetadataMaterialization, dict | None]:
    if source.source_kind != "metadata":
        raise MetadataReadError("Source is not a metadata source")

    current = sync.stat_source(source, include_content_hash=False)
    current_materialization = metadata_materialization(session, source.id)
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
        sync.record_source_revision(session, source=source, status="error", revision=None, error=error, checked_at=utc_now())
        sync.finish_sync_job(
            session,
            job,
            status="failed",
            message=error["message"],
            result={"status": "error", "message": error["message"], "revision": None, "error": error},
        )
        source_validation.validate_metadata_source(session, source)
        if current_materialization is not None:
            return current_materialization, error
        raise MetadataReadError(error["message"])

    try:
        raw = read_metadata_file(source.uri)
        current = sync.stat_source(source, include_content_hash=True)
        editor_document = load_editor_document_from_raw(source, raw, current)
        normalized = normalize_metadata_document(source.id, source.uri, raw)
    except MetadataReadError as exc:
        error = {"message": str(exc), "code": "metadata_read_error"}
        sync.record_source_revision(session, source=source, status="error", revision=None, error=error, checked_at=utc_now())
        sync.finish_sync_job(
            session,
            job,
            status="failed",
            message=str(exc),
            result={"status": "error", "message": str(exc), "revision": None, "error": error},
        )
        source_validation.record_source_validation(
            session,
            source,
            source_validation.source_validation_error(source, str(exc)),
        )
        if current_materialization is not None:
            return current_materialization, error
        raise

    revision_json = json.dumps(current, sort_keys=True)
    fingerprint = _materialization_fingerprint(current)
    previous_fingerprint = (
        current_materialization.materialization_fingerprint
        if current_materialization is not None
        else None
    )
    if current_materialization is None:
        current_materialization = MetadataMaterialization(
            source_id=source.id,
            source_revision_json=revision_json,
            normalizer_version=METADATA_NORMALIZER_VERSION,
            materialization_fingerprint=fingerprint,
            editor_document_json=json.dumps(editor_document, ensure_ascii=False),
            normalized_metadata_json=json.dumps(normalized, ensure_ascii=False),
        )
        session.add(current_materialization)
    else:
        current_materialization.source_revision_json = revision_json
        current_materialization.normalizer_version = METADATA_NORMALIZER_VERSION
        current_materialization.materialization_fingerprint = fingerprint
        current_materialization.editor_document_json = json.dumps(editor_document, ensure_ascii=False)
        current_materialization.normalized_metadata_json = json.dumps(normalized, ensure_ascii=False)
        current_materialization.materialized_at = utc_now()
    if previous_fingerprint != fingerprint:
        invalidate_environment_derived_caches(session, source.environment_id, structural=True)
    sync.record_source_revision(session, source=source, status="ok", revision=current, error=None, checked_at=utc_now())
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
        },
    )
    source_validation.validate_metadata_source(session, source)
    return current_materialization, None


def metadata_materialization(session: Session, source_id: int) -> MetadataMaterialization | None:
    return session.scalar(
        select(MetadataMaterialization).where(MetadataMaterialization.source_id == source_id)
    )


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
    return {"columns": columns, "rows": rows}


def _same_revision_json(current: dict, stored_json: str | None) -> bool:
    if not stored_json:
        return False
    try:
        stored = json.loads(stored_json)
    except json.JSONDecodeError:
        return False
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
