from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource, MetadataSourceSnapshot, utc_now
from datacoolie_studio.domains.metadata.editor import _metadata_source_name, load_editor_document_from_raw, validate_editor_document
from datacoolie_studio.domains.metadata.normalizer import (
    METADATA_NORMALIZER_VERSION,
    enrich_metadata_documents_with_connections,
    normalize_metadata_document,
)
from datacoolie_studio.domains.metadata.reader import MetadataReadError, read_metadata_file
from datacoolie_studio.domains.sync import service as sync


def load_environment_metadata(session: Session, sources: list[EnvironmentSource]) -> dict:
    documents = []
    errors = []
    for source in sources:
        if not source.enabled:
            continue
        try:
            snapshot, warning = _ensure_metadata_snapshot_result(session, source)
            normalized = json.loads(snapshot.normalized_metadata_json or "{}")
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
    snapshot = ensure_metadata_snapshot(session, source)
    return _refresh_editor_document_routing(source, json.loads(snapshot.editor_document_json))


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


def ensure_metadata_snapshot(session: Session, source: EnvironmentSource, *, force: bool = False) -> MetadataSourceSnapshot:
    snapshot, _ = _ensure_metadata_snapshot_result(session, source, force=force)
    return snapshot


def _ensure_metadata_snapshot_result(
    session: Session,
    source: EnvironmentSource,
    *,
    force: bool = False,
) -> tuple[MetadataSourceSnapshot, dict | None]:
    if source.source_kind != "metadata":
        raise MetadataReadError("Source is not a metadata source")

    current = sync.stat_source(source, include_content_hash=False)
    latest = latest_metadata_snapshot(session, source.id)
    if (
        not force
        and latest is not None
        and _same_revision_json(current, latest.source_revision_json)
        and _snapshot_uses_current_normalizer(latest)
        and _snapshot_has_editor_routing(latest)
    ):
        return latest, None

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
        if latest is not None:
            return latest, error
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
        if latest is not None:
            return latest, error
        raise

    snapshot = MetadataSourceSnapshot(
        source_id=source.id,
        source_revision_json=json.dumps(current, sort_keys=True),
        editor_document_json=json.dumps(editor_document, ensure_ascii=False),
        normalized_metadata_json=json.dumps(normalized, ensure_ascii=False),
    )
    session.add(snapshot)
    sync.record_source_revision(session, source=source, status="ok", revision=current, error=None, checked_at=utc_now())
    sync.finish_sync_job(
        session,
        job,
        status="succeeded",
        message="Metadata source cache refreshed",
        result={"status": "ok", "message": "Metadata source cache refreshed", "revision": current, "error": None},
    )
    return snapshot, None


def latest_metadata_snapshot(session: Session, source_id: int) -> MetadataSourceSnapshot | None:
    return session.scalars(
        select(MetadataSourceSnapshot)
        .where(MetadataSourceSnapshot.source_id == source_id)
        .order_by(MetadataSourceSnapshot.created_at.desc(), MetadataSourceSnapshot.id.desc())
    ).first()


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


def _snapshot_uses_current_normalizer(snapshot: MetadataSourceSnapshot) -> bool:
    try:
        normalized = json.loads(snapshot.normalized_metadata_json or "{}")
    except json.JSONDecodeError:
        return False
    return normalized.get("_normalizer_version") == METADATA_NORMALIZER_VERSION


def _snapshot_has_editor_routing(snapshot: MetadataSourceSnapshot) -> bool:
    try:
        editor = json.loads(snapshot.editor_document_json or "{}")
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


def _revision_error(source: EnvironmentSource, revision: dict) -> dict | None:
    if not revision.get("exists"):
        return {"message": f"Metadata file not found: {source.uri}", "code": "not_found"}
    if revision.get("object_type") != "file":
        return {"message": f"Metadata source must be a file: {source.uri}", "code": "invalid_type"}
    return None
