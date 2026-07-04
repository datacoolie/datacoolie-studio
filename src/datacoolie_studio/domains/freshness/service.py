from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import (
    EnvironmentSource,
    LogFileManifest,
    MetadataSourceSnapshot,
    SourceRevision,
)
from datacoolie_studio.domains.sync import service as sync

FRESHNESS_ORDER = {
    "missing": 5,
    "sync_failed": 4,
    "source_changed": 3,
    "not_cached": 2,
    "unknown": 1,
    "current": 0,
}


def environment_freshness(session: Session, environment_id: int) -> dict[str, Any]:
    sources = list(
        session.scalars(
            select(EnvironmentSource)
            .where(EnvironmentSource.environment_id == environment_id, EnvironmentSource.enabled.is_(True))
            .order_by(EnvironmentSource.id)
        )
    )
    items = [_source_freshness(session, source) for source in sources]
    metadata_items = [item for item in items if item["source_kind"] == "metadata"]
    log_items = [item for item in items if item["source_kind"] == "logs"]
    status = _aggregate_status(items)
    max_modified_at = _max_datetime([item.get("source_modified_at") for item in items])
    return {
        "environment_id": environment_id,
        "status": status,
        "message": _freshness_message(status, max_modified_at),
        "max_source_modified_at": max_modified_at,
        "metadata_source_count": len(metadata_items),
        "etl_log_path_count": len(log_items),
        "metadata": _group_summary(metadata_items),
        "etl_logs": _group_summary(log_items),
        "items": items,
    }


def _source_freshness(session: Session, source: EnvironmentSource) -> dict[str, Any]:
    current = sync.stat_source(source, include_content_hash=False)
    revision = session.scalar(select(SourceRevision).where(SourceRevision.source_id == source.id))
    stored_revision = _json_or_none(revision.revision_json) if revision else None
    cache_revision = _cache_revision(session, source)
    source_modified_at = _revision_modified_at(current)
    cached_modified_at = _revision_modified_at(cache_revision)
    cache_synced_at = _cache_synced_at(session, source, revision)
    status = _source_status(current, revision, cache_revision or stored_revision)
    return {
        "source_id": source.id,
        "source_kind": source.source_kind,
        "label": source.label,
        "uri": source.uri,
        "status": status,
        "source_modified_at": source_modified_at,
        "cache_synced_at": cache_synced_at,
        "cache_source_modified_at": cached_modified_at,
        "revision": _public_revision(current),
        "cache_revision": _public_revision(cache_revision or stored_revision),
        "message": _source_message(status, source.source_kind),
    }


def _cache_revision(session: Session, source: EnvironmentSource) -> dict[str, Any] | None:
    if source.source_kind == "metadata":
        snapshot = session.scalars(
            select(MetadataSourceSnapshot)
            .where(MetadataSourceSnapshot.source_id == source.id)
            .order_by(MetadataSourceSnapshot.created_at.desc(), MetadataSourceSnapshot.id.desc())
        ).first()
        return _json_or_none(snapshot.source_revision_json) if snapshot else None
    if source.source_kind == "logs":
        rows = session.scalars(select(LogFileManifest).where(LogFileManifest.source_id == source.id)).all()
        if not rows:
            return None
        revisions = [_json_or_none(row.revision_json) for row in rows]
        revisions = [revision for revision in revisions if revision]
        return {
            "object_type": "directory",
            "file_count": len(rows),
            "max_mtime_ns": max((revision.get("mtime_ns") or 0 for revision in revisions), default=None),
            "total_size": sum((revision.get("size") or 0 for revision in revisions), start=0),
        }
    return None


def _source_status(
    current: dict[str, Any],
    revision: SourceRevision | None,
    cache_revision: dict[str, Any] | None,
) -> str:
    if not current.get("exists"):
        return "missing"
    if revision and revision.status == "error":
        return "sync_failed"
    if cache_revision is None:
        return "not_cached"
    if _same_light_revision(current, cache_revision):
        return "current"
    return "source_changed"


def _same_light_revision(current: dict[str, Any], cached: dict[str, Any]) -> bool:
    if current.get("object_type") != cached.get("object_type"):
        return False
    if current.get("object_type") == "file":
        return current.get("size") == cached.get("size") and current.get("mtime_ns") == cached.get("mtime_ns")
    if current.get("object_type") == "directory":
        return (
            current.get("file_count") == cached.get("file_count")
            and current.get("total_size") == cached.get("total_size")
            and current.get("max_mtime_ns") == cached.get("max_mtime_ns")
        )
    return False


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


def _cache_synced_at(session: Session, source: EnvironmentSource, revision: SourceRevision | None) -> datetime | None:
    if source.source_kind == "metadata":
        snapshot = session.scalars(
            select(MetadataSourceSnapshot)
            .where(MetadataSourceSnapshot.source_id == source.id)
            .order_by(MetadataSourceSnapshot.created_at.desc(), MetadataSourceSnapshot.id.desc())
        ).first()
        return snapshot.created_at if snapshot else (revision.checked_at if revision and revision.status != "error" else None)
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
        "source_changed": f"{noun.capitalize()} source changed",
        "not_cached": f"{noun.capitalize()} source is not cached",
        "missing": f"{noun.capitalize()} source is missing",
        "sync_failed": f"{noun.capitalize()} sync failed",
        "unknown": f"{noun.capitalize()} freshness unknown",
    }.get(status, f"{noun.capitalize()} freshness unknown")
