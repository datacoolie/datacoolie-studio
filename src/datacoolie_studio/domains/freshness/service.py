from __future__ import annotations

import json
import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import (
    CodeArtifactMaterialization,
    Environment,
    EnvironmentSource,
    LogFileManifest,
    MetadataMaterialization,
    Project,
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


@dataclass(frozen=True)
class EnvironmentContextState:
    environment: Environment
    project: Project
    all_sources: list[EnvironmentSource]
    sources: list[EnvironmentSource]
    revisions: dict[int, SourceRevision]
    metadata_materializations: dict[int, MetadataMaterialization]
    code_materializations: dict[int, CodeArtifactMaterialization]
    manifest_rows: dict[int, list[LogFileManifest]]
    log_pending_changes: dict[int, bool]
    mappings: list[ProjectReferenceMapping]


def environment_freshness(session: Session, environment_id: int) -> dict[str, Any]:
    return _freshness_from_state(_load_environment_context_state(session, environment_id))


def environment_context(session: Session, environment_id: int) -> dict[str, Any]:
    state = _load_environment_context_state(session, environment_id)
    freshness = _freshness_from_state(state)
    return {
        "schema_version": "environment-context.v1",
        "project": {"id": state.project.id, "name": state.project.name},
        "environment": {
            "id": state.environment.id,
            "project_id": state.environment.project_id,
            "name": state.environment.name,
        },
        "source_counts": {
            "metadata": freshness["metadata_source_count"],
            "logs": freshness["etl_log_path_count"],
            "code": sum(source.source_kind == "code" for source in state.sources),
        },
        "freshness": {
            "status": freshness["status"],
            "message": freshness["message"],
            "max_source_modified_at": freshness["max_source_modified_at"],
            "metadata": freshness["metadata"],
            "etl_logs": freshness["etl_logs"],
        },
        "versions": _dependency_versions(state),
        "checked_at": datetime.now(timezone.utc),
    }


def metadata_catalog_version(
    session: Session,
    sources: list[EnvironmentSource],
) -> str:
    """Return the same Metadata dependency version exposed by Environment Context."""
    enabled_sources = [
        source
        for source in sources
        if source.enabled and source.source_kind == "metadata"
    ]
    materializations = _structural_materializations(
        session,
        MetadataMaterialization,
        [source.id for source in enabled_sources],
    )
    return _catalog_version_for_sources(enabled_sources, materializations)


def _load_environment_context_state(session: Session, environment_id: int) -> EnvironmentContextState:
    environment = session.get(Environment, environment_id)
    if environment is None:
        raise LookupError(f"Environment not found: {environment_id}")
    project = session.get(Project, environment.project_id)
    if project is None:
        raise LookupError(f"Project not found: {environment.project_id}")
    all_sources = list(
        session.scalars(
            select(EnvironmentSource)
            .where(EnvironmentSource.environment_id == environment_id)
            .order_by(EnvironmentSource.id)
        )
    )
    sources = [source for source in all_sources if source.enabled]
    source_ids = [source.id for source in sources]
    revisions = {
        item.source_id: item
        for item in session.scalars(
            select(SourceRevision).where(SourceRevision.source_id.in_(source_ids))
        )
    } if source_ids else {}
    metadata_materializations = _structural_materializations(
        session, MetadataMaterialization, [source.id for source in sources if source.source_kind == "metadata"],
    )
    code_materializations = _structural_materializations(
        session, CodeArtifactMaterialization, [source.id for source in sources if source.source_kind == "code"],
    )
    manifest_rows: dict[int, list[LogFileManifest]] = defaultdict(list)
    if source_ids:
        for row in session.scalars(
            select(LogFileManifest)
            .where(LogFileManifest.source_id.in_(source_ids))
            .order_by(LogFileManifest.source_id, LogFileManifest.id)
        ):
            manifest_rows[row.source_id].append(row)
    mappings = list(session.scalars(
        select(ProjectReferenceMapping)
        .where(ProjectReferenceMapping.project_id == environment.project_id)
        .order_by(ProjectReferenceMapping.id)
    ))
    # Detect Log sources whose files changed since their last sync. This is a
    # read-only live check (no ingestion); it lets the header show "Not synced"
    # for a cached Log source with new/changed files, on the same read that
    # reports Metadata and Code freshness. The scan result is cached for the
    # source-check interval (TTL) so it is not repeated on every read.
    from datacoolie_studio.domains.logs.ingestion import log_source_has_pending_changes
    from datacoolie_studio.domains.studio_settings.service import source_check_interval_seconds

    pending_ttl = source_check_interval_seconds(session)
    log_pending_changes = {
        source.id: log_source_has_pending_changes(session, source, ttl_seconds=pending_ttl)
        for source in sources
        if source.source_kind == "logs" and source.id in manifest_rows
    }
    return EnvironmentContextState(
        environment=environment,
        project=project,
        all_sources=all_sources,
        sources=sources,
        revisions=revisions,
        metadata_materializations=metadata_materializations,
        code_materializations=code_materializations,
        manifest_rows=dict(manifest_rows),
        log_pending_changes=log_pending_changes,
        mappings=mappings,
    )


def _freshness_from_state(state: EnvironmentContextState) -> dict[str, Any]:
    item_pairs = [
        _source_freshness(
            source,
            revision=state.revisions.get(source.id),
            metadata_materialization=state.metadata_materializations.get(source.id),
            code_materialization=state.code_materializations.get(source.id),
            manifest_rows=state.manifest_rows.get(source.id, []),
            pending_changes=state.log_pending_changes.get(source.id, False),
        )
        for source in state.sources
    ]
    items = [item for item, _ in item_pairs]
    metadata_items = [item for item in items if item["source_kind"] == "metadata"]
    log_items = [item for item in items if item["source_kind"] == "logs"]
    status = _aggregate_status(items)
    max_modified_at = _max_datetime([item.get("source_modified_at") for item in items])
    return {
        "environment_id": state.environment.id,
        "status": status,
        "message": _freshness_message(status, max_modified_at),
        "max_source_modified_at": max_modified_at,
        "metadata_source_count": len(metadata_items),
        "etl_log_path_count": len(log_items),
        "source_cache_version": _source_cache_version(state.sources, [materialized for _, materialized in item_pairs]),
        "structural_cache_version": _structural_cache_version(
            state.sources,
            state.metadata_materializations,
            state.code_materializations,
            state.mappings,
        ),
        "metadata": _group_summary(metadata_items),
        "etl_logs": _group_summary(log_items),
        "items": items,
    }


def _dependency_versions(state: EnvironmentContextState) -> dict[str, str]:
    return {
        "source_registry": _fingerprint([
            {
                "id": source.id,
                "kind": source.source_kind,
                "uri": source.uri,
                "label": source.label,
                "enabled": source.enabled,
                "config": source.source_config_json,
                "read_check_status": source.read_check_status,
                "read_checked_at": source.read_checked_at,
                "read_check_result": source.read_check_result_json,
                "updated_at": source.updated_at,
            }
            for source in state.all_sources
        ]),
        "metadata_catalog": _catalog_version_for_sources(
            [source for source in state.sources if source.source_kind == "metadata"],
            state.metadata_materializations,
        ),
        "code_catalog": _catalog_version_for_sources(
            [source for source in state.sources if source.source_kind == "code"],
            state.code_materializations,
        ),
        "operations": _fingerprint([
            {
                "source_id": source.id,
                "revision": state.revisions.get(source.id).revision_json if state.revisions.get(source.id) else None,
                "status": state.revisions.get(source.id).status if state.revisions.get(source.id) else None,
                "manifests": [
                    (row.id, row.revision_json, row.row_count, row.status, row.last_seen_at)
                    for row in state.manifest_rows.get(source.id, [])
                ],
            }
            for source in state.sources
            if source.source_kind == "logs"
        ]),
        "reference_mappings": _fingerprint([
            {
                "id": mapping.id,
                "reference_signature": mapping.reference_signature_json,
                "target_kind": mapping.target_identifier_kind,
                "target_value": mapping.target_normalized_value,
                "target_display": mapping.target_display_value,
                "note": mapping.note,
                "updated_at": mapping.updated_at,
            }
            for mapping in state.mappings
        ]),
    }


def _catalog_version_for_sources(
    sources: list[EnvironmentSource],
    materializations: dict[int, MetadataMaterialization | CodeArtifactMaterialization],
) -> str:
    return _fingerprint([
        {
            "source_id": source.id,
            "uri": source.uri,
            "config": source.source_config_json,
            "materialization_fingerprint": materialization.materialization_fingerprint if materialization else None,
        }
        for source in sources
        for materialization in [materializations.get(source.id)]
    ])


def _fingerprint(payload: Any) -> str:
    canonical = json.dumps({"version": 1, "payload": payload}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _source_freshness(
    source: EnvironmentSource,
    *,
    revision: SourceRevision | None,
    metadata_materialization: MetadataMaterialization | None,
    code_materialization: CodeArtifactMaterialization | None,
    manifest_rows: list[LogFileManifest],
    pending_changes: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    stored_revision = _json_or_none(revision.revision_json) if revision else None
    materialized_revision = _materialized_revision(
        source,
        revision,
        metadata_materialization=metadata_materialization,
        code_materialization=code_materialization,
        manifest_rows=manifest_rows,
    )
    cache_revision = _cache_revision(source, materialized_revision)
    observed_revision = _observed_revision(source, stored_revision)
    source_modified_at = _revision_modified_at(observed_revision)
    cached_modified_at = _revision_modified_at(cache_revision)
    cache_synced_at = _cache_synced_at(source, revision, metadata_materialization)
    status = _source_status(revision, cache_revision, pending_changes=pending_changes)
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
    metadata_materialization: MetadataMaterialization | None,
    code_materialization: CodeArtifactMaterialization | None,
    manifest_rows: list[LogFileManifest],
) -> dict[str, Any] | None:
    if source.source_kind == "metadata":
        return _json_or_none(metadata_materialization.source_revision_json) if metadata_materialization else None
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
        return _json_or_none(code_materialization.source_revision_json) if code_materialization else None
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


def _source_status(
    revision: SourceRevision | None,
    cache_revision: dict[str, Any] | None,
    *,
    pending_changes: bool = False,
) -> str:
    if revision and revision.status == "error":
        error = _json_or_none(revision.error_json)
        if error and error.get("code") == "not_found":
            return "missing"
        return "sync_failed"
    if cache_revision is None:
        return "not_cached"
    if pending_changes:
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
    metadata_materialization: MetadataMaterialization | None,
) -> datetime | None:
    if source.source_kind == "metadata":
        return metadata_materialization.materialized_at if metadata_materialization else (revision.checked_at if revision and revision.status != "error" else None)
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


def _structural_materializations(session: Session, model, source_ids: list[int]) -> dict[int, Any]:
    if not source_ids:
        return {}
    rows = session.scalars(select(model).where(model.source_id.in_(source_ids)))
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
    metadata_materializations: dict[int, MetadataMaterialization],
    code_materializations: dict[int, CodeArtifactMaterialization],
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
                "materialization_fingerprint": materialization.materialization_fingerprint if materialization else None,
            }
            for source in structural_sources
            for materialization in [
                (
                    metadata_materializations
                    if source.source_kind == "metadata"
                    else code_materializations
                ).get(source.id)
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
        "not_cached": f"{noun.capitalize()} source is not synced",
        "missing": f"{noun.capitalize()} source is missing",
        "sync_failed": f"{noun.capitalize()} sync failed",
        "unknown": f"{noun.capitalize()} freshness unknown",
    }.get(status, f"{noun.capitalize()} freshness unknown")
