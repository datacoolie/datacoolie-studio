from __future__ import annotations

import json
import hashlib
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, load_only

from datacoolie_studio.db.models import (
    CodeArtifactSnapshot,
    Environment,
    EnvironmentSource,
    LogFileManifest,
    MetadataSourceSnapshot,
    ProjectReferenceMapping,
    SourceRevision,
)

FRESHNESS_ORDER = {
    "missing": 5,
    "sync_failed": 4,
    "not_cached": 2,
    "unknown": 1,
    "current": 0,
}


def environment_freshness(session: Session, environment_id: int) -> dict[str, Any]:
    environment = session.get(Environment, environment_id)
    if environment is None:
        raise LookupError(f"Environment not found: {environment_id}")
    sources = list(
        session.scalars(
            select(EnvironmentSource)
            .where(EnvironmentSource.environment_id == environment_id, EnvironmentSource.enabled.is_(True))
            .order_by(EnvironmentSource.id)
        )
    )
    source_ids = [source.id for source in sources]
    revisions = {
        item.source_id: item
        for item in session.scalars(
            select(SourceRevision).where(SourceRevision.source_id.in_(source_ids))
        )
    } if source_ids else {}
    metadata_snapshots = _latest_structural_snapshots(
        session, MetadataSourceSnapshot, [source.id for source in sources if source.source_kind == "metadata"],
    )
    code_snapshots = _latest_structural_snapshots(
        session, CodeArtifactSnapshot, [source.id for source in sources if source.source_kind == "code"],
    )
    manifest_rows: dict[int, list[LogFileManifest]] = defaultdict(list)
    if source_ids:
        for row in session.scalars(select(LogFileManifest).where(LogFileManifest.source_id.in_(source_ids))):
            manifest_rows[row.source_id].append(row)
    item_pairs = [
        _source_freshness(
            source,
            revision=revisions.get(source.id),
            metadata_snapshot=metadata_snapshots.get(source.id),
            code_snapshot=code_snapshots.get(source.id),
            manifest_rows=manifest_rows.get(source.id, []),
        )
        for source in sources
    ]
    items = [item for item, _ in item_pairs]
    metadata_items = [item for item in items if item["source_kind"] == "metadata"]
    log_items = [item for item in items if item["source_kind"] == "logs"]
    status = _aggregate_status(items)
    max_modified_at = _max_datetime([item.get("source_modified_at") for item in items])
    mappings = list(session.scalars(
        select(ProjectReferenceMapping)
        .where(ProjectReferenceMapping.project_id == environment.project_id)
        .order_by(ProjectReferenceMapping.id)
    ))
    return {
        "environment_id": environment_id,
        "status": status,
        "message": _freshness_message(status, max_modified_at),
        "max_source_modified_at": max_modified_at,
        "metadata_source_count": len(metadata_items),
        "etl_log_path_count": len(log_items),
        "source_cache_version": _source_cache_version(sources, [materialized for _, materialized in item_pairs]),
        "structural_cache_version": _structural_cache_version(
            sources,
            metadata_snapshots,
            code_snapshots,
            mappings,
        ),
        "metadata": _group_summary(metadata_items),
        "etl_logs": _group_summary(log_items),
        "items": items,
    }


def _source_freshness(
    source: EnvironmentSource,
    *,
    revision: SourceRevision | None,
    metadata_snapshot: MetadataSourceSnapshot | None,
    code_snapshot: CodeArtifactSnapshot | None,
    manifest_rows: list[LogFileManifest],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    stored_revision = _json_or_none(revision.revision_json) if revision else None
    materialized_revision = _materialized_revision(
        source,
        revision,
        metadata_snapshot=metadata_snapshot,
        code_snapshot=code_snapshot,
        manifest_rows=manifest_rows,
    )
    cache_revision = _cache_revision(source, materialized_revision)
    observed_revision = _observed_revision(source, stored_revision)
    source_modified_at = _revision_modified_at(observed_revision)
    cached_modified_at = _revision_modified_at(cache_revision)
    cache_synced_at = _cache_synced_at(source, revision, metadata_snapshot)
    status = _source_status(revision, cache_revision)
    return {
        "source_id": source.id,
        "source_kind": source.source_kind,
        "label": source.label,
        "uri": source.uri,
        "status": status,
        "source_modified_at": source_modified_at,
        "cache_synced_at": cache_synced_at,
        "cache_source_modified_at": cached_modified_at,
        "revision": _public_revision(observed_revision),
        "cache_revision": _public_revision(cache_revision),
        "message": _source_message(status, source.source_kind),
    }, materialized_revision


def _materialized_revision(
    source: EnvironmentSource,
    revision: SourceRevision | None,
    *,
    metadata_snapshot: MetadataSourceSnapshot | None,
    code_snapshot: CodeArtifactSnapshot | None,
    manifest_rows: list[LogFileManifest],
) -> dict[str, Any] | None:
    if source.source_kind == "metadata":
        return _json_or_none(metadata_snapshot.source_revision_json) if metadata_snapshot else None
    if source.source_kind == "logs":
        if not manifest_rows:
            return _json_or_none(revision.revision_json) if revision and revision.status != "error" else None
        revisions = [_json_or_none(row.revision_json) for row in manifest_rows]
        revisions = [revision for revision in revisions if revision]
        return {
            "object_type": "directory",
            "file_count": len(manifest_rows),
            "max_mtime_ns": max((revision.get("mtime_ns") or 0 for revision in revisions), default=None),
            "total_size": sum((revision.get("size") or 0 for revision in revisions), start=0),
        }
    if source.source_kind == "code":
        return _json_or_none(code_snapshot.source_revision_json) if code_snapshot else None
    return None


def _cache_revision(source: EnvironmentSource, materialized_revision: dict[str, Any] | None) -> dict[str, Any] | None:
    if source.source_kind == "code" and materialized_revision:
        source_stat = materialized_revision.get("source_stat")
        return source_stat if isinstance(source_stat, dict) else materialized_revision
    return materialized_revision


def _observed_revision(source: EnvironmentSource, revision: dict[str, Any] | None) -> dict[str, Any] | None:
    if source.source_kind == "code" and revision:
        source_stat = revision.get("source_stat")
        return source_stat if isinstance(source_stat, dict) else None
    return revision


def _source_status(revision: SourceRevision | None, cache_revision: dict[str, Any] | None) -> str:
    if revision and revision.status == "error":
        error = _json_or_none(revision.error_json)
        if error and error.get("code") == "not_found":
            return "missing"
        return "sync_failed"
    if cache_revision is None:
        return "not_cached"
    return "current"


def _group_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    status = _aggregate_status(items)
    return {
        "status": status,
        "max_source_modified_at": _max_datetime([item.get("source_modified_at") for item in items]),
        "cache_synced_at": _max_datetime([item.get("cache_synced_at") for item in items]),
        "count": len(items),
    }


def _aggregate_status(items: list[dict[str, Any]]) -> str:
    if not items:
        return "unknown"
    return max((item["status"] for item in items), key=lambda status: FRESHNESS_ORDER.get(status, 0))


def _revision_modified_at(revision: dict[str, Any] | None) -> datetime | None:
    if not revision:
        return None
    value = revision.get("mtime_ns") if revision.get("object_type") == "file" else revision.get("max_mtime_ns")
    if not isinstance(value, int):
        return None
    return datetime.fromtimestamp(value / 1_000_000_000, tz=timezone.utc)


def _cache_synced_at(
    source: EnvironmentSource,
    revision: SourceRevision | None,
    metadata_snapshot: MetadataSourceSnapshot | None,
) -> datetime | None:
    if source.source_kind == "metadata":
        return metadata_snapshot.created_at if metadata_snapshot else (revision.checked_at if revision and revision.status != "error" else None)
    if revision and revision.status != "error":
        return revision.checked_at
    return None


def _public_revision(revision: dict[str, Any] | None) -> dict[str, Any] | None:
    if not revision:
        return None
    return {
        key: value
        for key, value in revision.items()
        if key in {"object_type", "exists", "size", "mtime_ns", "file_count", "total_size", "max_mtime_ns"}
    }


def _json_or_none(payload: str | None) -> dict[str, Any] | None:
    if not payload:
        return None
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _latest_structural_snapshots(session: Session, model, source_ids: list[int]) -> dict[int, Any]:
    if not source_ids:
        return {}
    ranked = (
        select(
            model.id.label("snapshot_id"),
            func.row_number().over(
                partition_by=model.source_id,
                order_by=(model.created_at.desc(), model.id.desc()),
            ).label("snapshot_rank"),
        )
        .where(model.source_id.in_(source_ids))
        .subquery()
    )
    rows = session.scalars(
        select(model)
        .join(ranked, model.id == ranked.c.snapshot_id)
        .where(ranked.c.snapshot_rank == 1)
        .options(load_only(model.id, model.source_id, model.source_revision_json, model.created_at))
    )
    return {int(row.source_id): row for row in rows}


def _source_cache_version(
    sources: list[EnvironmentSource],
    materialized_revisions: list[dict[str, Any] | None],
) -> str:
    payload = {
        "version": 1,
        "sources": [
            {
                "id": source.id,
                "source_kind": source.source_kind,
                "uri": source.uri,
                "source_config": _json_or_none(source.source_config_json) or source.source_config_json or {},
                "materialized_revision": materialized_revision,
            }
            for source, materialized_revision in zip(sources, materialized_revisions, strict=True)
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _structural_cache_version(
    sources: list[EnvironmentSource],
    metadata_snapshots: dict[int, MetadataSourceSnapshot],
    code_snapshots: dict[int, CodeArtifactSnapshot],
    mappings: list[ProjectReferenceMapping],
) -> str:
    structural_sources = [source for source in sources if source.source_kind in {"metadata", "code"}]
    payload = {
        "version": 1,
        "sources": [
            {
                "id": source.id,
                "kind": source.source_kind,
                "uri": source.uri,
                "config": source.source_config_json,
                "snapshot_id": snapshot.id if snapshot else None,
                "snapshot_revision": snapshot.source_revision_json if snapshot else None,
            }
            for source in structural_sources
            for snapshot in [
                (metadata_snapshots if source.source_kind == "metadata" else code_snapshots).get(source.id)
            ]
        ],
        "reference_mappings": [
            {
                "id": mapping.id,
                "reference_type": mapping.reference_type,
                "reference_value": mapping.reference_normalized_value,
                "reference_signature": mapping.reference_signature_json,
                "target_kind": mapping.target_identifier_kind,
                "target_value": mapping.target_normalized_value,
                "target_display": mapping.target_display_value,
                "note": mapping.note,
                "updated_at": mapping.updated_at,
            }
            for mapping in mappings
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _max_datetime(values: list[datetime | None]) -> datetime | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _freshness_message(status: str, modified_at: datetime | None) -> str:
    if modified_at is None:
        return _source_message(status, "source")
    return _source_message(status, "source")


def _source_message(status: str, source_kind: str) -> str:
    noun = "source" if source_kind not in {"metadata", "logs"} else ("metadata" if source_kind == "metadata" else "logs")
    return {
        "current": f"{noun.capitalize()} cache is current",
        "not_cached": f"{noun.capitalize()} cache is empty",
        "missing": f"{noun.capitalize()} source is missing",
        "sync_failed": f"{noun.capitalize()} sync failed",
        "unknown": f"{noun.capitalize()} freshness unknown",
    }.get(status, f"{noun.capitalize()} freshness unknown")
