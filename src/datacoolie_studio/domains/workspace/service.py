from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from datacoolie_studio.domains.analytics import maintenance as analytics_maintenance
from datacoolie_studio.domains.logs import ingestion as log_ingestion
from datacoolie_studio.db.models import (
    Environment,
    EnvironmentMetadataEditorDraft,
    EnvironmentSource,
    LogFileManifest,
    CodeArtifactMaterialization,
    MetadataBackup,
    MetadataEditorDraft,
    MetadataSaveEvent,
    MetadataMaterialization,
    MetadataValidationResult,
    ProjectReferenceMapping,
    Project,
    SourceRevision,
    SyncJob,
)
from datacoolie_studio.domains.assets.reference_mappings import normalize_target_identifier
from datacoolie_studio.domains.assets.reference_identity import normalize_reference_signature
from datacoolie_studio.domains.code_artifacts.service import ensure_code_artifact_materialization
from datacoolie_studio.domains.environment_caches import invalidate_environment_derived_caches
from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.domains.metadata.service import ensure_metadata_materialization
from datacoolie_studio.domains.read_models.cache import (
    invalidate_environment_read_models,
    invalidate_project_read_models,
)
from datacoolie_studio.domains.read_models.keys import ASSETS_CATALOG, LINEAGE_GRAPH, OVERVIEW
from datacoolie_studio.domains.read_models.sqlite_store import SqliteResultCacheStore
from datacoolie_studio.domains.sources.discovery import (
    DiscoveredSource,
    discover_datacoolie_project_sources,
    discover_metadata_sources,
)
from datacoolie_studio.domains.sources import service as source_validation
from datacoolie_studio.domains.storage.uri import normalized_source_uri
from datacoolie_studio.domains.sync import service as sync

METADATA_SOURCE_DELETE_DEPENDENCIES = [
    ("draft", "saved draft", MetadataEditorDraft, "warning"),
    ("environment_draft", "environment draft", EnvironmentMetadataEditorDraft, "warning"),
    ("backup", "backup version", MetadataBackup, "warning"),
    ("validation_result", "validation result", MetadataValidationResult, "info"),
    ("save_event", "save event", MetadataSaveEvent, "info"),
    ("materialization", "metadata materialization", MetadataMaterialization, "info"),
    ("source_revision", "source revision", SourceRevision, "info"),
    ("sync_job", "sync job", SyncJob, "info"),
]


def list_projects(session: Session) -> list[Project]:
    return list(session.scalars(select(Project).order_by(Project.name)))


def list_project_reference_mappings(session: Session, project_id: int) -> list[dict]:
    _require_project(session, project_id)
    statement = (
        select(ProjectReferenceMapping)
        .where(ProjectReferenceMapping.project_id == project_id)
        .order_by(ProjectReferenceMapping.updated_at.desc(), ProjectReferenceMapping.id.desc())
    )
    return [_project_reference_mapping_to_dict(item) for item in session.scalars(statement)]


def create_project_reference_mapping(
    session: Session,
    project_id: int,
    *,
    reference_type: str,
    reference_value: str,
    target_identifier_kind: str,
    target_value: str,
    target_display_value: str | None = None,
    note: str | None = None,
) -> dict:
    _require_project(session, project_id)
    signature = normalize_reference_signature(
        reference_type=reference_type,
        value=reference_value,
    )
    target_kind, target_normalized_value = normalize_target_identifier(target_identifier_kind, target_value)
    duplicate = session.scalars(
        select(ProjectReferenceMapping).where(
            ProjectReferenceMapping.project_id == project_id,
            ProjectReferenceMapping.reference_type == signature.reference_type,
            ProjectReferenceMapping.reference_normalized_value == signature.normalized_value,
        )
    ).first()
    if duplicate is not None:
        raise ValueError("Mapping already exists for this reference signature")
    mapping = ProjectReferenceMapping(
        project_id=project_id,
        reference_type=signature.reference_type,
        reference_normalized_value=signature.normalized_value,
        reference_signature_json=signature.to_json(),
        target_identifier_kind=target_kind,
        target_normalized_value=target_normalized_value,
        target_display_value=(target_display_value or target_value).strip(),
        note=((str(note).strip() or None) if note is not None else None),
    )
    session.add(mapping)
    invalidate_project_read_models(session, project_id, model_keys={ASSETS_CATALOG, OVERVIEW, LINEAGE_GRAPH})
    session.commit()
    session.refresh(mapping)
    return _project_reference_mapping_to_dict(mapping)


def update_project_reference_mapping(
    session: Session,
    project_id: int,
    mapping_id: int,
    payload: dict[str, Any],
) -> dict | None:
    _require_project(session, project_id)
    mapping = session.get(ProjectReferenceMapping, mapping_id)
    if mapping is None or mapping.project_id != project_id:
        return None

    reference_type = payload.get("reference_type", mapping.reference_type)
    reference_value = payload.get("reference_value", mapping.reference_normalized_value)
    signature = normalize_reference_signature(
        reference_type=str(reference_type),
        value=str(reference_value),
    )

    target_kind_value = payload.get("target_identifier_kind", mapping.target_identifier_kind)
    target_value = payload.get("target_value", mapping.target_normalized_value)
    target_kind, target_normalized_value = normalize_target_identifier(str(target_kind_value), str(target_value))

    target_display_value = payload.get("target_display_value", mapping.target_display_value)
    normalized_target_display_value = (target_display_value or str(target_value)).strip()
    note = payload.get("note", mapping.note)
    normalized_note = ((str(note).strip() or None) if note is not None else None)
    duplicate = session.scalars(
        select(ProjectReferenceMapping).where(
            ProjectReferenceMapping.project_id == project_id,
            ProjectReferenceMapping.reference_type == signature.reference_type,
            ProjectReferenceMapping.reference_normalized_value == signature.normalized_value,
            ProjectReferenceMapping.id != mapping.id,
        )
    ).first()
    if duplicate is not None:
        raise ValueError("Mapping already exists for this reference signature")

    mapping.reference_type = signature.reference_type
    mapping.reference_normalized_value = signature.normalized_value
    mapping.reference_signature_json = signature.to_json()
    mapping.target_identifier_kind = target_kind
    mapping.target_normalized_value = target_normalized_value
    mapping.target_display_value = normalized_target_display_value
    mapping.note = normalized_note
    invalidate_project_read_models(session, project_id, model_keys={ASSETS_CATALOG, OVERVIEW, LINEAGE_GRAPH})
    session.commit()
    session.refresh(mapping)
    return _project_reference_mapping_to_dict(mapping)


def delete_project_reference_mapping(session: Session, project_id: int, mapping_id: int) -> bool:
    _require_project(session, project_id)
    mapping = session.get(ProjectReferenceMapping, mapping_id)
    if mapping is None or mapping.project_id != project_id:
        return False
    invalidate_project_read_models(session, project_id, model_keys={ASSETS_CATALOG, OVERVIEW, LINEAGE_GRAPH})
    session.delete(mapping)
    session.commit()
    return True


def list_project_summaries(session: Session) -> list[dict]:
    mapping_counts = (
        select(
            ProjectReferenceMapping.project_id.label("project_id"),
            func.count(ProjectReferenceMapping.id).label("reference_mapping_count"),
        )
        .group_by(ProjectReferenceMapping.project_id)
        .subquery()
    )
    statement = (
        select(
            Project.id.label("project_id"),
            Project.name.label("project_name"),
            Project.description.label("project_description"),
            Project.created_at.label("project_created_at"),
            Project.updated_at.label("project_updated_at"),
            func.coalesce(mapping_counts.c.reference_mapping_count, 0).label("reference_mapping_count"),
            Environment.id.label("environment_id"),
            Environment.name.label("environment_name"),
            Environment.created_at.label("environment_created_at"),
            Environment.updated_at.label("environment_updated_at"),
            func.sum(case((EnvironmentSource.source_kind == "metadata", 1), else_=0)).label("metadata_count"),
            func.sum(case((EnvironmentSource.source_kind == "logs", 1), else_=0)).label("log_count"),
            func.sum(case((EnvironmentSource.source_kind == "code", 1), else_=0)).label("code_count"),
        )
        .select_from(Project)
        .outerjoin(mapping_counts, mapping_counts.c.project_id == Project.id)
        .outerjoin(Environment, Environment.project_id == Project.id)
        .outerjoin(EnvironmentSource, EnvironmentSource.environment_id == Environment.id)
        .group_by(
            Project.id,
            Project.name,
            Project.description,
            Project.created_at,
            Project.updated_at,
            mapping_counts.c.reference_mapping_count,
            Environment.id,
            Environment.name,
            Environment.created_at,
            Environment.updated_at,
        )
        .order_by(Project.name, Environment.name)
    )

    summaries_by_id: dict[int, dict[str, Any]] = {}
    for row in session.execute(statement):
        summary = summaries_by_id.setdefault(
            row.project_id,
            {
                "id": row.project_id,
                "name": row.project_name,
                "description": row.project_description,
                "environment_count": 0,
                "metadata_source_count": 0,
                "etl_log_path_count": 0,
                "reference_mapping_count": int(row.reference_mapping_count or 0),
                "environments": [],
                "created_at": row.project_created_at,
                "updated_at": row.project_updated_at,
            },
        )
        if row.environment_id is None:
            continue

        metadata_count = int(row.metadata_count or 0)
        log_count = int(row.log_count or 0)
        summary["environment_count"] += 1
        summary["metadata_source_count"] += metadata_count
        summary["etl_log_path_count"] += log_count
        summary["environments"].append(
            {
                "id": row.environment_id,
                "name": row.environment_name,
                "metadata_source_count": metadata_count,
                "etl_log_path_count": log_count,
                "code_artifact_count": int(row.code_count or 0),
                "created_at": row.environment_created_at,
                "updated_at": row.environment_updated_at,
            }
        )
    return list(summaries_by_id.values())


def _project_reference_mapping_to_dict(mapping: ProjectReferenceMapping) -> dict[str, Any]:
    signature = normalize_reference_signature(
        reference_type=mapping.reference_type,
        value=mapping.reference_normalized_value,
    ).to_dict()
    return {
        "id": mapping.id,
        "project_id": mapping.project_id,
        "reference_type": signature["reference_type"],
        "reference_normalized_value": mapping.reference_normalized_value,
        "reference_signature": signature,
        "target_identifier_kind": mapping.target_identifier_kind,
        "target_normalized_value": mapping.target_normalized_value,
        "target_display_value": mapping.target_display_value,
        "note": mapping.note,
        "created_at": mapping.created_at,
        "updated_at": mapping.updated_at,
    }


def _require_project(session: Session, project_id: int) -> None:
    if session.get(Project, project_id) is None:
        raise LookupError("Project not found")


def create_project(session: Session, name: str, description: str | None = None) -> Project:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Project name cannot be blank")
    if len(normalized) > 255:
        raise ValueError("Project name cannot exceed 255 characters")
    project = Project(name=normalized, description=description)
    session.add(project)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError(f"Project already exists: {normalized}") from exc
    session.refresh(project)
    return project


def delete_project(session: Session, project_id: int) -> bool:
    project = session.get(Project, project_id)
    if project is None:
        return False
    source_ids = _etl_source_ids_for_project(session, project_id)
    _purge_analytics_cache_by_source_ids(source_ids)
    invalidate_project_read_models(session, project_id)
    session.delete(project)
    session.commit()
    return True


def list_environments(session: Session, project_id: int) -> list[Environment]:
    return list(session.scalars(select(Environment).where(Environment.project_id == project_id).order_by(Environment.name)))


def create_environment(session: Session, project_id: int, name: str) -> Environment:
    normalized = name.strip().lower()
    existing = session.scalar(
        select(Environment).where(Environment.project_id == project_id, Environment.name == normalized)
    )
    if existing is not None:
        raise ValueError(f"Environment already exists: {normalized}")
    env = Environment(project_id=project_id, name=normalized)
    session.add(env)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ValueError(f"Environment already exists: {normalized}")
    session.refresh(env)
    return env


def delete_environment(session: Session, environment_id: int) -> bool:
    environment = session.get(Environment, environment_id)
    if environment is None:
        return False
    source_ids = _etl_source_ids_for_environment(session, environment_id)
    _purge_analytics_cache_by_source_ids(source_ids)
    invalidate_environment_read_models(session, environment_id)
    session.delete(environment)
    session.commit()
    return True


def list_sources(session: Session, environment_id: int, source_kind: str | None = None) -> list[EnvironmentSource]:
    statement = select(EnvironmentSource).where(EnvironmentSource.environment_id == environment_id)
    if source_kind is not None:
        statement = statement.where(EnvironmentSource.source_kind == source_kind)
    return list(session.scalars(statement.order_by(EnvironmentSource.id)))


def list_metadata_sources(session: Session, environment_id: int) -> list[EnvironmentSource]:
    return list_sources(session, environment_id, "metadata")


def list_metadata_sources_with_validation(session: Session, environment_id: int) -> list[dict]:
    return [source_to_dict(item) for item in list_metadata_sources(session, environment_id)]


def add_metadata_source(
    session: Session,
    environment_id: int,
    uri: str,
    label: str | None,
    enabled: bool,
    source_config: dict | None = None,
) -> EnvironmentSource:
    source = EnvironmentSource(
        environment_id=environment_id,
        source_kind="metadata",
        uri=uri.strip(),
        label=label,
        enabled=enabled,
        source_config_json=json.dumps(source_config or {}, sort_keys=True),
    )
    session.add(source)
    invalidate_environment_derived_caches(session, environment_id, structural=True)
    session.commit()
    session.refresh(source)
    if source.enabled:
        _initialize_discovered_sources(session, [source])
    return source


def import_metadata_sources(
    session: Session,
    environment_id: int,
    *,
    uri: str,
    label: str | None = None,
    enabled: bool = True,
) -> dict:
    discovery = discover_metadata_sources(uri, label=label)
    return _import_discovered_sources(
        session,
        environment_id,
        metadata_sources=discovery.metadata_sources,
        code_artifacts=[],
        errors=discovery.errors,
        enabled=enabled,
        materialize_created_sources=True,
    )


def import_datacoolie_project_sources(
    session: Session,
    environment_id: int,
    *,
    project_uri: str,
    metadata_subpath: str = "metadata",
    code_subpath: str = "functions",
    metadata_uri: str | None = None,
    code_uri: str | None = None,
    include_metadata: bool = True,
    include_code: bool = True,
    enabled: bool = True,
) -> dict:
    discovery = discover_datacoolie_project_sources(
        project_uri,
        metadata_subpath=metadata_subpath,
        code_subpath=code_subpath,
        metadata_uri=metadata_uri,
        code_uri=code_uri,
        include_metadata=include_metadata,
        include_code=include_code,
    )
    return _import_discovered_sources(
        session,
        environment_id,
        metadata_sources=discovery.metadata_sources,
        code_artifacts=discovery.code_artifacts,
        errors=discovery.errors,
        enabled=enabled,
        materialize_created_sources=True,
    )


def list_code_artifacts(session: Session, environment_id: int) -> list[EnvironmentSource]:
    return list_sources(session, environment_id, "code")


def list_code_artifacts_with_validation(session: Session, environment_id: int) -> list[dict]:
    return [source_to_dict(item) for item in list_code_artifacts(session, environment_id)]


def add_code_artifact(
    session: Session,
    environment_id: int,
    uri: str,
    label: str | None,
    enabled: bool,
    source_config: dict | None,
) -> EnvironmentSource:
    artifact = EnvironmentSource(
        environment_id=environment_id,
        source_kind="code",
        uri=uri.strip(),
        label=label,
        enabled=enabled,
        source_config_json=json.dumps(source_config or {}, sort_keys=True),
    )
    session.add(artifact)
    invalidate_environment_derived_caches(session, environment_id, structural=True)
    session.commit()
    session.refresh(artifact)
    if artifact.enabled:
        _initialize_discovered_sources(session, [artifact])
    return artifact


def _import_discovered_sources(
    session: Session,
    environment_id: int,
    *,
    metadata_sources: list[DiscoveredSource],
    code_artifacts: list[DiscoveredSource],
    errors: list[dict],
    enabled: bool,
    materialize_created_sources: bool = False,
) -> dict:
    created: list[dict] = []
    created_sources: list[EnvironmentSource] = []
    existing: list[dict] = []

    for discovered in [*metadata_sources, *code_artifacts]:
        current = _existing_source_by_uri(session, environment_id, discovered.source_kind, discovered.uri)
        if current is not None:
            existing.append(_source_import_item(current, discovered, "existing"))
            continue
        current = _add_discovered_source(session, environment_id, discovered, enabled)
        created_sources.append(current)
        created.append(_source_import_item(current, discovered, "created"))

    initialization = _initialize_discovered_sources(session, created_sources) if materialize_created_sources else {
        "auto_validated": 0,
        "auto_synced": 0,
        "auto_sync_errors": [],
    }
    all_errors = [*errors, *initialization["auto_sync_errors"]]

    return {
        "created": created,
        "existing": existing,
        "errors": all_errors,
        "summary": {
            "created": len(created),
            "existing": len(existing),
            "errors": len(all_errors),
            "metadata_sources": sum(1 for item in [*created, *existing] if item["source_kind"] == "metadata"),
            "code_artifacts": sum(1 for item in [*created, *existing] if item["source_kind"] == "code"),
            "auto_validated": initialization["auto_validated"],
            "auto_synced": initialization["auto_synced"],
        },
    }


def _initialize_discovered_sources(session: Session, sources: list[EnvironmentSource]) -> dict[str, Any]:
    """Materialize only new Metadata/Code configurations discovered by a project scan."""
    auto_validated = 0
    auto_synced = 0
    errors: list[dict[str, Any]] = []
    for source in sources:
        if not source.enabled:
            continue
        try:
            with sync.source_refresh_guard(source.id) as acquired:
                if not acquired:
                    errors.append(_source_initialization_error(source, "Source refresh is already running"))
                    continue
                if source.source_kind == "metadata":
                    ensure_metadata_materialization(session, source)
                elif source.source_kind == "code":
                    ensure_code_artifact_materialization(session, source)
                else:
                    continue
            status = sync.source_sync_status(session, source)
            if source.read_checked_at is not None:
                auto_validated += 1
            if status["status"] == "ok":
                auto_synced += 1
            else:
                errors.append(_source_initialization_error(source, status.get("message") or "Initial sync failed"))
        except MetadataReadError as exc:
            if source.read_checked_at is not None:
                auto_validated += 1
            errors.append(_source_initialization_error(source, str(exc)))
        except Exception as exc:
            session.rollback()
            errors.append(_source_initialization_error(source, str(exc)))
    return {
        "auto_validated": auto_validated,
        "auto_synced": auto_synced,
        "auto_sync_errors": errors,
    }


def _source_initialization_error(source: EnvironmentSource, message: str) -> dict[str, Any]:
    return {
        "source_id": source.id,
        "source_kind": source.source_kind,
        "uri": source.uri,
        "message": message,
    }


def _add_discovered_source(
    session: Session,
    environment_id: int,
    discovered: DiscoveredSource,
    enabled: bool,
) -> EnvironmentSource:
    if discovered.source_kind == "metadata":
        return add_metadata_source(
            session,
            environment_id,
            discovered.uri,
            discovered.label,
            enabled,
            discovered.source_config,
        )
    if discovered.source_kind == "code":
        return add_code_artifact(
            session,
            environment_id,
            discovered.uri,
            discovered.label,
            enabled,
            discovered.source_config,
        )
    raise ValueError(f"Unsupported discovered source kind: {discovered.source_kind}")


def _existing_source_by_uri(
    session: Session,
    environment_id: int,
    source_kind: str,
    uri: str,
) -> EnvironmentSource | None:
    target = normalized_source_uri(uri)
    for source in list_sources(session, environment_id, source_kind):
        if normalized_source_uri(source.uri) == target:
            return source
    return None


def _source_import_item(source: EnvironmentSource, discovered: DiscoveredSource, status: str) -> dict:
    return {
        "status": status,
        "id": source.id,
        "source_kind": source.source_kind,
        "uri": source.uri,
        "label": source.label,
        "record_counts": discovered.record_counts,
        "source_config": _json_object(source.source_config_json),
    }


def environment_source_by_id(
    session: Session,
    environment_id: int,
    source_id: int,
    source_kind: str,
) -> EnvironmentSource | None:
    """Return a source only when it belongs to the requested Environment.

    Route handlers use this lookup before any source-detail operation.  A
    missing, wrong-kind, or cross-Environment source deliberately has the
    same result so callers cannot enumerate resources outside their scope.
    """
    return session.scalar(
        select(EnvironmentSource).where(
            EnvironmentSource.id == source_id,
            EnvironmentSource.environment_id == environment_id,
            EnvironmentSource.source_kind == source_kind,
        )
    )


def update_code_artifact(
    session: Session,
    environment_id: int,
    source_id: int,
    *,
    uri: str | None = None,
    label: str | None = None,
    enabled: bool | None = None,
    source_config: dict | None = None,
) -> EnvironmentSource | None:
    source = environment_source_by_id(session, environment_id, source_id, "code")
    if source is None:
        return None
    structural_changed = False
    if uri is not None:
        normalized_uri = uri.strip()
        if normalized_uri != source.uri:
            source.uri = normalized_uri
            structural_changed = True
    if source_config is not None:
        config_json = json.dumps(source_config, sort_keys=True)
        if config_json != (source.source_config_json or "{}"):
            source.source_config_json = config_json
            structural_changed = True
    if structural_changed:
        _clear_source_read_check(source)
        _delete_source_sync_records(session, source)
        for row in _source_rows(session, CodeArtifactMaterialization, source.id):
            session.delete(row)
    if label is not None:
        source.label = label
    if enabled is not None:
        structural_changed = structural_changed or source.enabled != enabled
        source.enabled = enabled
    if structural_changed:
        invalidate_environment_derived_caches(session, source.environment_id, structural=True)
    session.commit()
    session.refresh(source)
    return source


def delete_code_artifact(session: Session, environment_id: int, source_id: int) -> bool:
    source = environment_source_by_id(session, environment_id, source_id, "code")
    if source is None:
        return False
    _delete_source_sync_records(session, source)
    for row in _source_rows(session, CodeArtifactMaterialization, source.id):
        session.delete(row)
    invalidate_environment_derived_caches(session, source.environment_id, structural=True)
    session.delete(source)
    session.commit()
    return True


def code_artifact_delete_impact(session: Session, environment_id: int, source_id: int) -> dict | None:
    source = environment_source_by_id(session, environment_id, source_id, "code")
    if source is None:
        return None
    impact_specs = [
        (
            "materialization",
            "code materialization",
            _count_source_rows(session, CodeArtifactMaterialization, source.id),
            "warning",
        ),
        ("lineage_cache", "lineage graph cache entry", _count_environment_read_models(session, source.environment_id, LINEAGE_GRAPH), "warning"),
        ("source_revision", "source revision", _count_source_rows(session, SourceRevision, source.id), "info"),
        ("sync_job", "sync history entry", _count_source_rows(session, SyncJob, source.id), "info"),
    ]
    impacts = [
        {"kind": kind, "label": _pluralize(label, count), "count": count, "severity": severity}
        for kind, label, count, severity in impact_specs
        if count
    ]
    return {
        "source_id": source.id,
        "source_kind": source.source_kind,
        "source_uri": source.uri,
        "mode": "hard_delete",
        "metadata_file_deleted": False,
        "has_impact": bool(impacts),
        "impacts": impacts,
        "summary": _source_delete_impact_summary(impacts, "code artifact"),
    }


def update_metadata_source(
    session: Session,
    environment_id: int,
    source_id: int,
    *,
    uri: str | None = None,
    label: str | None = None,
    enabled: bool | None = None,
) -> EnvironmentSource | None:
    source = environment_source_by_id(session, environment_id, source_id, "metadata")
    if source is None:
        return None
    structural_changed = False
    if uri is not None:
        normalized_uri = uri.strip()
        if normalized_uri != source.uri:
            _clear_source_read_check(source)
            _delete_source_sync_records(session, source)
            source.uri = normalized_uri
            structural_changed = True
    if label is not None:
        source.label = label
    if enabled is not None:
        structural_changed = structural_changed or source.enabled != enabled
        source.enabled = enabled
    if structural_changed:
        invalidate_environment_derived_caches(session, source.environment_id, structural=True)
    session.commit()
    session.refresh(source)
    return source


def delete_metadata_source(session: Session, environment_id: int, source_id: int) -> bool:
    source = environment_source_by_id(session, environment_id, source_id, "metadata")
    if source is None:
        return False
    _delete_metadata_source_dependencies(session, source)
    invalidate_environment_derived_caches(session, source.environment_id, structural=True)
    session.delete(source)
    session.commit()
    return True


def metadata_source_delete_impact(session: Session, environment_id: int, source_id: int) -> dict | None:
    source = environment_source_by_id(session, environment_id, source_id, "metadata")
    if source is None:
        return None

    impacts = []
    for kind, label, model, severity in METADATA_SOURCE_DELETE_DEPENDENCIES:
        count = (
            _count_environment_rows(session, model, source.environment_id)
            if kind == "environment_draft"
            else _count_source_rows(session, model, source.id)
        )
        if count:
            impacts.append(
                {
                    "kind": kind,
                    "label": _pluralize(label, count),
                    "count": count,
                    "severity": severity,
                }
            )

    return {
        "source_id": source.id,
        "source_kind": source.source_kind,
        "source_uri": source.uri,
        "mode": "hard_delete",
        "metadata_file_deleted": False,
        "has_impact": bool(impacts),
        "impacts": impacts,
        "summary": _delete_impact_summary(impacts),
    }


def list_log_sources(session: Session, environment_id: int) -> list[EnvironmentSource]:
    return list_sources(session, environment_id, "logs")


def list_log_sources_with_validation(session: Session, environment_id: int) -> list[dict]:
    return [source_to_dict(item) for item in list_log_sources(session, environment_id)]


def add_log_source(
    session: Session,
    environment_id: int,
    uri: str,
    label: str | None,
    enabled: bool,
    source_config: dict | None = None,
) -> EnvironmentSource:
    path = EnvironmentSource(
        environment_id=environment_id,
        source_kind="logs",
        uri=uri.strip(),
        label=label,
        enabled=enabled,
        sync_schedule_enabled=False,
        sync_interval_minutes=1,
        source_config_json=json.dumps(source_config or {}, sort_keys=True),
    )
    session.add(path)
    invalidate_environment_derived_caches(session, environment_id, structural=False)
    session.commit()
    session.refresh(path)
    if path.enabled:
        source_validation.validate_log_source(session, path)
    return path


def update_log_source(
    session: Session,
    environment_id: int,
    path_id: int,
    *,
    uri: str | None = None,
    label: str | None = None,
    enabled: bool | None = None,
    source_config: dict | None = None,
    sync_schedule_enabled: bool | None = None,
    sync_interval_minutes: int | None = None,
) -> EnvironmentSource | None:
    path = environment_source_by_id(session, environment_id, path_id, "logs")
    if path is None:
        return None
    changed = False
    enabled_changed = enabled is not None and enabled != path.enabled
    if uri is not None:
        normalized_uri = uri.strip()
        if normalized_uri != path.uri:
            path.uri = normalized_uri
            changed = True
    if source_config is not None:
        config_json = json.dumps(source_config, sort_keys=True)
        if config_json != (path.source_config_json or "{}"):
            path.source_config_json = config_json
            changed = True
    if changed:
        _clear_source_read_check(path)
        _delete_source_sync_records(session, path)
        _delete_source_log_cache(session, path)
    elif enabled_changed:
        _clear_source_read_check(path)
        log_ingestion.invalidate_pending_changes(path.id)
        analytics_maintenance.purge_source_ids([path.id])
    if label is not None:
        path.label = label
    if enabled is not None:
        path.enabled = enabled
    _update_schedule(path, sync_schedule_enabled, sync_interval_minutes, default_interval_minutes=1)
    invalidate_environment_derived_caches(session, path.environment_id, structural=False)
    session.commit()
    session.refresh(path)
    return path


def delete_log_source(session: Session, environment_id: int, path_id: int) -> bool:
    path = environment_source_by_id(session, environment_id, path_id, "logs")
    if path is None:
        return False
    _delete_source_sync_records(session, path)
    _delete_source_log_cache(session, path)
    invalidate_environment_derived_caches(session, path.environment_id, structural=False)
    session.delete(path)
    session.commit()
    return True


def log_source_delete_impact(session: Session, environment_id: int, path_id: int) -> dict | None:
    path = environment_source_by_id(session, environment_id, path_id, "logs")
    if path is None:
        return None
    cache_stats = analytics_maintenance.source_stats(path.id)
    impact_specs = [
        ("manifest", "indexed log file", _count_source_rows(session, LogFileManifest, path.id), "warning"),
        ("dataflow_cache", "cached dataflow run", cache_stats["dataflow_row_count"], "warning"),
        ("job_cache", "cached job run", cache_stats["job_row_count"], "warning"),
        ("filter_cache", "cached monitoring filter value", cache_stats["filter_value_count"], "info"),
        ("source_revision", "source revision", _count_source_rows(session, SourceRevision, path.id), "info"),
        ("sync_job", "refresh history entry", _count_source_rows(session, SyncJob, path.id), "info"),
        ("schedule", "auto-refresh schedule", 1 if path.sync_schedule_enabled else 0, "info"),
    ]
    impacts = [
        {"kind": kind, "label": _pluralize(label, count), "count": count, "severity": severity}
        for kind, label, count, severity in impact_specs
        if count
    ]
    return {
        "source_id": path.id,
        "source_kind": path.source_kind,
        "source_uri": path.uri,
        "mode": "hard_delete",
        "metadata_file_deleted": False,
        "has_impact": bool(impacts),
        "impacts": impacts,
        "summary": _log_delete_impact_summary(impacts),
    }


def _delete_metadata_source_dependencies(session: Session, source: EnvironmentSource) -> None:
    backups = _source_rows(session, MetadataBackup, source.id)
    for backup in backups:
        path = Path(backup.backup_path).expanduser()
        if path.exists():
            path.unlink()

    for _, _, model, _ in METADATA_SOURCE_DELETE_DEPENDENCIES:
        rows = (
            _environment_rows(session, model, source.environment_id)
            if model is EnvironmentMetadataEditorDraft
            else _source_rows(session, model, source.id)
        )
        for row in rows:
            session.delete(row)


def _delete_source_sync_records(session: Session, source: EnvironmentSource) -> None:
    for model in (SourceRevision, SyncJob):
        for row in _source_rows(session, model, source.id):
            session.delete(row)


def _delete_source_log_cache(session: Session, source: EnvironmentSource) -> None:
    for row in _source_rows(session, LogFileManifest, source.id):
        session.delete(row)
    log_ingestion.invalidate_pending_changes(source.id)
    analytics_maintenance.purge_source_ids([source.id])


def _etl_source_ids_for_environment(session: Session, environment_id: int) -> list[int]:
    rows = session.scalars(
        select(EnvironmentSource.id).where(
            EnvironmentSource.environment_id == environment_id,
            EnvironmentSource.source_kind == "logs",
        )
    ).all()
    return [int(row) for row in rows]


def _etl_source_ids_for_project(session: Session, project_id: int) -> list[int]:
    rows = session.scalars(
        select(EnvironmentSource.id)
        .join(Environment, EnvironmentSource.environment_id == Environment.id)
        .where(
            Environment.project_id == project_id,
            EnvironmentSource.source_kind == "logs",
        )
    ).all()
    return [int(row) for row in rows]


def _purge_analytics_cache_by_source_ids(source_ids: list[int]) -> None:
    if not source_ids:
        return
    try:
        for source_id in source_ids:
            log_ingestion.invalidate_pending_changes(source_id)
        analytics_maintenance.purge_source_ids(source_ids)
    except Exception:
        # Purging analytics cache is best-effort and should not block project/environment deletes.
        pass


def _clear_source_read_check(source: EnvironmentSource) -> None:
    source.read_check_status = None
    source.read_checked_at = None
    source.read_check_result_json = None


def _source_rows(session: Session, model, source_id: int) -> list:
    return list(session.scalars(select(model).where(model.source_id == source_id)).all())


def _source_rows_by_column(session: Session, model, column, source_id: int) -> list:
    return list(session.scalars(select(model).where(column == source_id)).all())


def _count_source_rows(session: Session, model, source_id: int) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(model).where(model.source_id == source_id)
        )
        or 0
    )


def _environment_rows(session: Session, model, environment_id: int) -> list:
    return list(session.scalars(select(model).where(model.environment_id == environment_id)).all())


def _count_environment_rows(session: Session, model, environment_id: int) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(model).where(model.environment_id == environment_id)
        )
        or 0
    )


def _count_environment_read_models(session: Session, environment_id: int, model_key: str) -> int:
    return SqliteResultCacheStore().entry_count(environment_id, model_key)


def _delete_impact_summary(impacts: list[dict]) -> str:
    if not impacts:
        return "No related Studio data will be removed. The metadata file will not be deleted."
    parts = [f"{item['count']} {item['label']}" for item in impacts]
    if len(parts) == 1:
        related = parts[0]
    else:
        related = f"{', '.join(parts[:-1])}, and {parts[-1]}"
    return f"Deleting this metadata source will also remove {related}. The metadata file will not be deleted."


def _pluralize(label: str, count: int) -> str:
    if count == 1:
        return label
    if label.endswith("y"):
        return f"{label[:-1]}ies"
    return f"{label}s"


def source_to_dict(item: EnvironmentSource) -> dict:
    return {
        "id": item.id,
        "environment_id": item.environment_id,
        "uri": item.uri,
        "label": item.label,
        "enabled": item.enabled,
        "sync_schedule_enabled": item.sync_schedule_enabled,
        "sync_interval_minutes": item.sync_interval_minutes,
        "last_scheduled_sync_at": _as_utc(item.last_scheduled_sync_at) if item.last_scheduled_sync_at else None,
        "created_at": item.created_at,
        "source_config": _json_object(item.source_config_json),
        "latest_validation": _validation_from_source(item),
    }


def _update_schedule(
    source: EnvironmentSource,
    enabled: bool | None,
    interval_minutes: int | None,
    *,
    default_interval_minutes: int = 60,
) -> None:
    if interval_minutes is not None and interval_minutes < 1:
        raise ValueError("Sync interval must be at least 1 minute")
    if enabled is not None:
        source.sync_schedule_enabled = enabled
    if interval_minutes is not None:
        source.sync_interval_minutes = interval_minutes
    if source.sync_schedule_enabled and not source.sync_interval_minutes:
        source.sync_interval_minutes = default_interval_minutes


def _log_delete_impact_summary(impacts: list[dict]) -> str:
    if not impacts:
        return "No cached Studio data will be removed. Original log files will not be deleted."
    parts = [f"{item['count']} {item['label']}" for item in impacts]
    related = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])}, and {parts[-1]}"
    return f"Deleting this log source will also remove {related}. Original log files will not be deleted."


def _source_delete_impact_summary(impacts: list[dict], source_label: str) -> str:
    if not impacts:
        return f"No related Studio data will be removed. The original {source_label} will not be deleted."
    parts = [f"{item['count']} {item['label']}" for item in impacts]
    related = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])}, and {parts[-1]}"
    return f"Deleting this source will also remove {related}. The original {source_label} will not be deleted."


def _validation_from_source(source: EnvironmentSource) -> dict | None:
    if not source.read_check_status and not source.read_check_result_json:
        return None
    result = {}
    if source.read_check_result_json:
        try:
            loaded = json.loads(source.read_check_result_json)
            if isinstance(loaded, dict):
                result = loaded
        except json.JSONDecodeError:
            result = {}
    return {
        "source_id": source.id,
        "source_kind": source.source_kind,
        "status": source.read_check_status or result.get("status") or "unknown",
        "message": result.get("message") or "",
        "detected_provider": result.get("detected_provider"),
        "detected_format": result.get("detected_format"),
        "record_counts": result.get("record_counts") or {},
        "records_scanned": result.get("records_scanned") or 0,
        "validated_at": _as_utc(source.read_checked_at) if source.read_checked_at else None,
        "errors": result.get("errors") or [],
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _json_object(value: str | None) -> dict:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
