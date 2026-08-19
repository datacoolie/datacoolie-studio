from __future__ import annotations

import json
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from datacoolie_studio.domains.analytics import maintenance as analytics_maintenance
from datacoolie_studio.domains.logs import ingestion as log_ingestion
from datacoolie_studio.domains.logs.control import reset_log_control_state
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
    SourceRegistration,
    SourceObservation,
    SyncJob,
)
from datacoolie_studio.domains.assets.reference_mappings import normalize_target_identifier
from datacoolie_studio.domains.assets.reference_identity import normalize_reference_signature
from datacoolie_studio.domains.code_artifacts.service import ensure_code_artifact_materialization
from datacoolie_studio.domains.environment_caches import invalidate_environment_derived_caches
from datacoolie_studio.domains.credentials.store import (
    CredentialSecretStore,
    KeyringCredentialSecretStore,
)
from datacoolie_studio.domains.freshness.service import source_freshness_statuses
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
from datacoolie_studio.domains.storage.uri import (
    join_uri,
    normalized_source_uri,
    parse_storage_uri,
)
from datacoolie_studio.domains.storage.factory import create_storage_adapter
from datacoolie_studio.domains.storage.binding import StorageBinding
from datacoolie_studio.domains.storage.errors import StorageConfigurationError
from datacoolie_studio.domains.source_observation.repository import (
    reset_observation,
    resume_observation,
)
from datacoolie_studio.domains.studio_settings.service import (
    source_check_interval_seconds,
)
from datacoolie_studio.domains.sources.storage_binding import (
    apply_binding,
    binding_from_source,
    binding_to_dict,
    validate_and_normalize_binding,
)
from datacoolie_studio.domains.sources.registration import (
    LOCATION_CONFIG_KEYS,
    canonicalize_location_config,
    configured_location_dict,
    get_or_create_registration,
    source_input_locations,
    storage_identity_scope,
    update_registration_input,
)
from datacoolie_studio.domains.sync import service as sync

METADATA_SOURCE_DELETE_DEPENDENCIES = [
    ("draft", "saved draft", MetadataEditorDraft, "warning"),
    ("environment_draft", "environment draft", EnvironmentMetadataEditorDraft, "warning"),
    ("backup", "backup version", MetadataBackup, "warning"),
    ("validation_result", "validation result", MetadataValidationResult, "info"),
    ("save_event", "save event", MetadataSaveEvent, "info"),
    ("materialization", "metadata materialization", MetadataMaterialization, "info"),
    ("source_observation", "source observation", SourceObservation, "info"),
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


def rename_project(session: Session, project_id: int, name: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise LookupError("Project not found")
    normalized = name.strip()
    if project.name == normalized:
        return project
    existing = session.scalar(
        select(Project).where(
            Project.id != project_id,
            Project.name == normalized,
        )
    )
    if existing is not None:
        raise ValueError(f"Project already exists: {normalized}")
    project.name = normalized
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
    display_name = name.strip()
    existing = session.scalar(
        select(Environment).where(
            Environment.project_id == project_id,
            func.lower(Environment.name) == display_name.lower(),
        )
    )
    if existing is not None:
        raise ValueError(f"Environment already exists: {display_name}")
    env = Environment(project_id=project_id, name=display_name)
    session.add(env)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ValueError(f"Environment already exists: {display_name}")
    session.refresh(env)
    return env


def rename_environment(session: Session, environment_id: int, name: str) -> Environment:
    environment = session.get(Environment, environment_id)
    if environment is None:
        raise LookupError("Environment not found")
    display_name = name.strip()
    if environment.name == display_name:
        return environment
    existing = session.scalar(
        select(Environment).where(
            Environment.project_id == environment.project_id,
            Environment.id != environment_id,
            func.lower(Environment.name) == display_name.lower(),
        )
    )
    if existing is not None:
        raise ValueError(f"Environment already exists: {display_name}")
    environment.name = display_name
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError(f"Environment already exists: {display_name}") from exc
    session.refresh(environment)
    return environment


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
    statement = (
        select(EnvironmentSource)
        .options(selectinload(EnvironmentSource.registration))
        .where(EnvironmentSource.environment_id == environment_id)
    )
    if source_kind is not None:
        statement = statement.where(EnvironmentSource.source_kind == source_kind)
    return list(session.scalars(statement.order_by(EnvironmentSource.id)))


def sources_workspace(session: Session, environment_id: int) -> dict[str, Any]:
    if session.get(Environment, environment_id) is None:
        raise LookupError(f"Environment not found: {environment_id}")
    sources = list_sources(session, environment_id)
    by_kind = {
        kind: [source for source in sources if source.source_kind == kind]
        for kind in ("metadata", "logs", "code")
    }
    freshness_by_source, observations = source_freshness_statuses(session, sources)
    statuses = sync.source_sync_statuses(
        session,
        sources,
        observations=observations,
    )
    for status in statuses:
        item = freshness_by_source.get(status["source_id"])
        if item is None:
            continue
        status["freshness"] = {
            "state": "not_synced" if item["status"] == "not_cached" else item["status"],
            "source_modified_at": item.get("source_modified_at"),
            "cache_source_modified_at": item.get("cache_source_modified_at"),
            "cache_synced_at": item.get("cache_synced_at"),
            "summary": item.get("message"),
        }
    cloud_source_ids = {
        source.id for source in sources if source.storage_provider != "local"
    }
    due_times = [
        status["next_check_at"]
        for status in statuses
        if status["source_id"] in cloud_source_ids
        and status["next_check_at"] is not None
    ]
    version_payload = {
        "sources": [
            {
                "id": source.id,
                "kind": source.source_kind,
                "uri": source.uri,
                "enabled": source.enabled,
                "config": source.source_config_json,
                "storage_provider": source.storage_provider,
                "storage_auth_mode": source.storage_auth_mode,
                "updated_at": source.updated_at,
            }
            for source in sources
        ],
        "statuses": statuses,
    }
    dependency_version = hashlib.sha256(
        json.dumps(
            version_payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": "sources-workspace.v1",
        "environment_id": environment_id,
        "metadata_sources": [source_to_dict(item) for item in by_kind["metadata"]],
        "log_sources": [source_to_dict(item) for item in by_kind["logs"]],
        "code_artifacts": [source_to_dict(item) for item in by_kind["code"]],
        "statuses": statuses,
        "earliest_cloud_due_at": min(due_times) if due_times else None,
        "dependency_version": dependency_version,
    }


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
    storage: dict | None = None,
    registration: SourceRegistration | None = None,
    input_uri: str | None = None,
) -> EnvironmentSource:
    raw_uri = input_uri if input_uri is not None else uri
    normalized_uri, binding = validate_and_normalize_binding(
        session, uri=uri, storage=storage, source_config=source_config
    )
    canonical_config, input_locations, canonical_locations = canonicalize_location_config(
        source_config, binding
    )
    if registration is None:
        existing = _existing_source_by_uri(
            session, environment_id, "metadata", normalized_uri, binding
        )
        if existing is not None:
            return existing
        registration, _ = get_or_create_registration(
            session,
            environment_id=environment_id,
            purpose="metadata",
            input_uri=raw_uri,
            canonical_uri=normalized_uri,
            binding=binding,
            input_locations=input_locations,
            canonical_locations=canonical_locations,
        )
        existing = _source_for_registration(
            session, registration.id, "metadata", normalized_uri
        )
        if existing is not None:
            session.commit()
            return existing
    source = EnvironmentSource(
        environment_id=environment_id,
        registration_id=registration.id,
        source_kind="metadata",
        uri=normalized_uri,
        label=label,
        enabled=enabled,
        source_config_json=json.dumps(canonical_config, sort_keys=True),
    )
    apply_binding(source, binding)
    session.add(source)
    session.flush()
    reset_observation(
        session,
        source.id,
        due_at=datetime.now(timezone.utc)
        + timedelta(seconds=source_check_interval_seconds(session)),
    )
    invalidate_environment_derived_caches(session, environment_id, structural=True)
    session.commit()
    session.refresh(source)
    return source


def import_metadata_sources(
    session: Session,
    environment_id: int,
    *,
    uri: str,
    label: str | None = None,
    enabled: bool = True,
    storage: dict | None = None,
) -> dict:
    normalized_uri, binding = validate_and_normalize_binding(
        session, uri=uri, storage=storage, source_config=None
    )
    registration, _ = get_or_create_registration(
        session,
        environment_id=environment_id,
        purpose="metadata",
        input_uri=uri,
        canonical_uri=normalized_uri,
        binding=binding,
    )
    if storage and storage.get("provider") != "local":
        prior = _existing_source_by_uri(
            session, environment_id, "metadata", normalized_uri, binding
        )
        if prior is not None:
            source = prior
            if not registration.sources:
                session.delete(registration)
                session.commit()
        else:
            source = add_metadata_source(
                session,
                environment_id,
                normalized_uri,
                label,
                enabled,
                {"discovery_mode": "direct"},
                storage,
                registration,
                uri,
            )
        status = "existing" if prior is not None else "created"
        item = {
            "status": status,
            "id": source.id,
            "source_kind": source.source_kind,
            "uri": source.uri,
            "label": source.label,
            "record_counts": {},
            "source_config": _json_object(source.source_config_json),
            "configured_location": configured_location_dict(source),
        }
        return {
            "created": [item] if status == "created" else [],
            "existing": [item] if status == "existing" else [],
            "errors": [],
            "summary": {
                "created": 1 if status == "created" else 0,
                "existing": 1 if status == "existing" else 0,
                "errors": 0,
                "metadata_sources": 1,
                "code_artifacts": 0,
                "auto_validated": 0,
                "auto_synced": 0,
            },
        }
    discovery = discover_metadata_sources(normalized_uri, label=label)
    return _import_discovered_sources(
        session,
        environment_id,
        metadata_sources=discovery.metadata_sources,
        code_artifacts=[],
        errors=discovery.errors,
        enabled=enabled,
        materialize_created_sources=True,
        registration=registration,
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
    storage: dict | None = None,
    secret_store: CredentialSecretStore | None = None,
) -> dict:
    normalized_project_uri, binding = validate_and_normalize_binding(
        session,
        uri=project_uri,
        storage=storage,
        source_config=None,
    )
    binding_payload = {
        "provider": binding.provider,
        "auth_mode": binding.auth_mode,
        "credential_profile_id": binding.credential_profile_id,
        "options": binding.options,
    }
    input_locations: dict[str, str] = {}
    canonical_locations: dict[str, str] = {}
    normalized_metadata_uri = metadata_uri
    normalized_code_uri = code_uri
    for key, value in (("metadata_uri", metadata_uri), ("code_uri", code_uri)):
        if not value:
            continue
        canonical, _ = validate_and_normalize_binding(
            session, uri=value, storage=binding_payload, source_config=None
        )
        input_locations[key] = value.strip()
        canonical_locations[key] = canonical
        if key == "metadata_uri":
            normalized_metadata_uri = canonical
        else:
            normalized_code_uri = canonical
    if include_metadata:
        input_locations["metadata_uri"] = (
            metadata_uri.strip()
            if metadata_uri
            else join_uri(project_uri, metadata_subpath)
        )
        metadata_root = (
            normalized_metadata_uri
            if normalized_metadata_uri
            else join_uri(normalized_project_uri, metadata_subpath)
        )
        canonical_locations["metadata_uri"], _ = validate_and_normalize_binding(
            session,
            uri=metadata_root,
            storage=binding_payload,
            source_config=None,
        )
    if include_code:
        input_locations["code_uri"] = (
            code_uri.strip() if code_uri else join_uri(project_uri, code_subpath)
        )
        code_root = (
            normalized_code_uri
            if normalized_code_uri
            else join_uri(normalized_project_uri, code_subpath)
        )
        canonical_locations["code_uri"], _ = validate_and_normalize_binding(
            session,
            uri=code_root,
            storage=binding_payload,
            source_config=None,
        )
    registration, _ = get_or_create_registration(
        session,
        environment_id=environment_id,
        purpose="project",
        input_uri=project_uri,
        canonical_uri=normalized_project_uri,
        binding=binding,
        input_locations=input_locations,
        canonical_locations=canonical_locations,
    )
    adapter = (
        None
        if binding.provider == "local"
        else create_storage_adapter(
            binding,
            uri=normalized_project_uri,
            session=session,
            secret_store=secret_store or KeyringCredentialSecretStore(),
        )
    )
    discovery = discover_datacoolie_project_sources(
        normalized_project_uri,
        metadata_subpath=metadata_subpath,
        code_subpath=code_subpath,
        metadata_uri=normalized_metadata_uri,
        code_uri=normalized_code_uri,
        include_metadata=include_metadata,
        include_code=include_code,
        adapter=adapter,
    )
    return _import_discovered_sources(
        session,
        environment_id,
        metadata_sources=discovery.metadata_sources,
        code_artifacts=discovery.code_artifacts,
        errors=discovery.errors,
        enabled=enabled,
        # Initial validation/materialization is queued by the API after this
        # registration transaction so the discovered sources can render first.
        materialize_created_sources=False,
        materialize_existing_sources=False,
        storage=binding_payload,
        secret_store=secret_store,
        registration=registration,
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
    storage: dict | None = None,
    registration: SourceRegistration | None = None,
    input_uri: str | None = None,
) -> EnvironmentSource:
    raw_uri = input_uri if input_uri is not None else uri
    normalized_uri, binding = validate_and_normalize_binding(
        session, uri=uri, storage=storage, source_config=source_config
    )
    canonical_config, input_locations, canonical_locations = canonicalize_location_config(
        source_config, binding
    )
    if registration is None:
        existing = _existing_source_by_uri(
            session, environment_id, "code", normalized_uri, binding
        )
        if existing is not None:
            return existing
        registration, _ = get_or_create_registration(
            session,
            environment_id=environment_id,
            purpose="code",
            input_uri=raw_uri,
            canonical_uri=normalized_uri,
            binding=binding,
            input_locations=input_locations,
            canonical_locations=canonical_locations,
        )
        existing = _source_for_registration(
            session, registration.id, "code", normalized_uri
        )
        if existing is not None:
            session.commit()
            return existing
    artifact = EnvironmentSource(
        environment_id=environment_id,
        registration_id=registration.id,
        source_kind="code",
        uri=normalized_uri,
        label=label,
        enabled=enabled,
        source_config_json=json.dumps(canonical_config, sort_keys=True),
    )
    apply_binding(artifact, binding)
    session.add(artifact)
    session.flush()
    reset_observation(
        session,
        artifact.id,
        due_at=datetime.now(timezone.utc)
        + timedelta(seconds=source_check_interval_seconds(session)),
    )
    invalidate_environment_derived_caches(session, environment_id, structural=True)
    session.commit()
    session.refresh(artifact)
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
    materialize_existing_sources: bool = False,
    storage: dict | None = None,
    secret_store: CredentialSecretStore | None = None,
    registration: SourceRegistration | None = None,
) -> dict:
    created: list[dict] = []
    created_sources: list[EnvironmentSource] = []
    existing: list[dict] = []
    existing_sources: list[EnvironmentSource] = []

    for discovered in [*metadata_sources, *code_artifacts]:
        current = _existing_source_by_uri(
            session,
            environment_id,
            discovered.source_kind,
            discovered.uri,
            storage,
        )
        if current is not None:
            existing_sources.append(current)
            existing.append(_source_import_item(current, discovered, "existing"))
            continue
        current = _add_discovered_source(
            session,
            environment_id,
            discovered,
            enabled,
            storage=storage,
            registration=registration,
        )
        created_sources.append(current)
        created.append(_source_import_item(current, discovered, "created"))

    sources_to_initialize = [
        *(created_sources if materialize_created_sources else []),
        *(existing_sources if materialize_existing_sources else []),
    ]
    initialization = _initialize_discovered_sources(
        session, sources_to_initialize, secret_store=secret_store
    ) if sources_to_initialize else {
        "auto_validated": 0,
        "auto_synced": 0,
        "auto_sync_errors": [],
    }
    all_errors = [*errors, *initialization["auto_sync_errors"]]
    if registration is not None:
        _delete_registration_if_orphan(session, registration.id)
    session.commit()

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


def _initialize_discovered_sources(
    session: Session,
    sources: list[EnvironmentSource],
    *,
    secret_store: CredentialSecretStore | None = None,
) -> dict[str, Any]:
    """Validate and materialize the selected Metadata/Code sources."""
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
                    ensure_metadata_materialization(
                        session, source, secret_store=secret_store
                    )
                elif source.source_kind == "code":
                    ensure_code_artifact_materialization(
                        session, source, secret_store=secret_store
                    )
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
    *,
    storage: dict | None = None,
    registration: SourceRegistration | None = None,
) -> EnvironmentSource:
    if discovered.source_kind == "metadata":
        return add_metadata_source(
            session,
            environment_id,
            discovered.uri,
            discovered.label,
            enabled,
            discovered.source_config,
            storage,
            registration,
            discovered.uri,
        )
    if discovered.source_kind == "code":
        return add_code_artifact(
            session,
            environment_id,
            discovered.uri,
            discovered.label,
            enabled,
            discovered.source_config,
            storage,
            registration,
            discovered.uri,
        )
    raise ValueError(f"Unsupported discovered source kind: {discovered.source_kind}")


def _existing_source_by_uri(
    session: Session,
    environment_id: int,
    source_kind: str,
    uri: str,
    binding: object | None = None,
) -> EnvironmentSource | None:
    target = normalized_source_uri(uri)
    target_provider, target_scope = _storage_identity_from_binding(uri, binding)
    for source in list_sources(session, environment_id, source_kind):
        source_binding = binding_from_source(source)
        if (
            normalized_source_uri(source.uri) == target
            and source_binding.provider == target_provider
            and storage_identity_scope(
                source_binding.provider, source_binding.options
            ) == target_scope
        ):
            return source
    return None


def _source_for_registration(
    session: Session,
    registration_id: int,
    source_kind: str,
    uri: str,
) -> EnvironmentSource | None:
    target = normalized_source_uri(uri)
    candidates = session.scalars(
        select(EnvironmentSource).where(
            EnvironmentSource.registration_id == registration_id,
            EnvironmentSource.source_kind == source_kind,
        )
    )
    return next(
        (
            source
            for source in candidates
            if normalized_source_uri(source.uri) == target
        ),
        None,
    )


def _storage_identity_from_binding(
    uri: str, binding: object | None
) -> tuple[str, dict[str, object]]:
    if isinstance(binding, StorageBinding):
        provider = binding.provider
        options = binding.options
    elif isinstance(binding, dict):
        provider = str(binding.get("provider") or parse_storage_uri(uri).provider)
        raw_options = binding.get("options")
        options = raw_options if isinstance(raw_options, dict) else {}
    else:
        provider = parse_storage_uri(uri).provider
        options = {}
    return provider, storage_identity_scope(provider, options)


def _source_import_item(source: EnvironmentSource, discovered: DiscoveredSource, status: str) -> dict:
    return {
        "status": status,
        "id": source.id,
        "source_kind": source.source_kind,
        "uri": source.uri,
        "label": source.label,
        "record_counts": discovered.record_counts,
        "source_config": _json_object(source.source_config_json),
        "configured_location": configured_location_dict(source),
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
    storage: dict | None = None,
) -> EnvironmentSource | None:
    source = environment_source_by_id(session, environment_id, source_id, "code")
    if source is None:
        return None
    structural_changed = False
    enabled_changed = enabled is not None and source.enabled != enabled
    reenabled = enabled is True and not source.enabled
    if uri is not None or source_config is not None or storage is not None:
        candidate_config = (
            source_config
            if source_config is not None
            else _json_object(source.source_config_json)
        )
        normalized_uri, binding = validate_and_normalize_binding(
            session,
            uri=uri if uri is not None else source.uri,
            storage=storage if storage is not None else binding_to_dict(source),
            source_config=candidate_config,
        )
        canonical_config, input_locations, canonical_locations = canonicalize_location_config(
            candidate_config, binding
        )
        if _has_location_input(uri, source_config):
            if source_config is None:
                input_locations = source_input_locations(source)
            registration, _ = get_or_create_registration(
                session,
                environment_id=environment_id,
                purpose="code",
                input_uri=uri if uri is not None else _source_input_uri(source),
                canonical_uri=normalized_uri,
                binding=binding,
                input_locations=input_locations,
                canonical_locations=canonical_locations,
            )
            _ensure_registration_available(registration, source)
            update_registration_input(
                registration,
                input_uri=uri if uri is not None else _source_input_uri(source),
                input_locations=input_locations,
            )
            _replace_source_registration(session, source, registration.id)
        if normalized_uri != source.uri or binding_to_dict(source) != {
            "provider": binding.provider,
            "auth_mode": binding.auth_mode,
            "credential_profile_id": binding.credential_profile_id,
            "options": binding.options,
        }:
            source.uri = normalized_uri
            apply_binding(source, binding)
            structural_changed = True
    if source_config is not None:
        config_json = json.dumps(canonical_config, sort_keys=True)
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
        source.enabled = enabled
    if reenabled:
        resume_observation(session, source.id)
    elif structural_changed:
        reset_observation(session, source.id)
    if structural_changed or enabled_changed:
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
    _delete_source_and_orphan_registration(session, source)
    session.commit()
    from datacoolie_studio.domains.code_artifacts.materializer import (
        clear_remote_artifact_snapshot,
    )

    clear_remote_artifact_snapshot(source_id)
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
        ("source_observation", "source observation", _count_source_rows(session, SourceObservation, source.id), "info"),
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
    source_config: dict | None = None,
    storage: dict | None = None,
) -> EnvironmentSource | None:
    source = environment_source_by_id(session, environment_id, source_id, "metadata")
    if source is None:
        return None
    structural_changed = False
    enabled_changed = enabled is not None and source.enabled != enabled
    reenabled = enabled is True and not source.enabled
    if uri is not None or source_config is not None or storage is not None:
        candidate_config = (
            source_config
            if source_config is not None
            else _json_object(source.source_config_json)
        )
        normalized_uri, binding = validate_and_normalize_binding(
            session,
            uri=uri if uri is not None else source.uri,
            storage=storage if storage is not None else binding_to_dict(source),
            source_config=candidate_config,
        )
        canonical_config, input_locations, canonical_locations = canonicalize_location_config(
            candidate_config, binding
        )
        if _has_location_input(uri, source_config):
            if source_config is None:
                input_locations = source_input_locations(source)
            registration, _ = get_or_create_registration(
                session,
                environment_id=environment_id,
                purpose="metadata",
                input_uri=uri if uri is not None else _source_input_uri(source),
                canonical_uri=normalized_uri,
                binding=binding,
                input_locations=input_locations,
                canonical_locations=canonical_locations,
            )
            _ensure_registration_available(registration, source)
            update_registration_input(
                registration,
                input_uri=uri if uri is not None else _source_input_uri(source),
                input_locations=input_locations,
            )
            _replace_source_registration(session, source, registration.id)
        binding_changed = binding_to_dict(source) != {
            "provider": binding.provider,
            "auth_mode": binding.auth_mode,
            "credential_profile_id": binding.credential_profile_id,
            "options": binding.options,
        }
        if normalized_uri != source.uri or binding_changed:
            _clear_source_read_check(source)
            _delete_source_sync_records(session, source)
            source.uri = normalized_uri
            apply_binding(source, binding)
            structural_changed = True
        if source_config is not None:
            config_json = json.dumps(canonical_config, sort_keys=True)
            if config_json != (source.source_config_json or "{}"):
                source.source_config_json = config_json
                _clear_source_read_check(source)
                _delete_source_sync_records(session, source)
                structural_changed = True
    if label is not None:
        source.label = label
    if enabled is not None:
        source.enabled = enabled
    if reenabled:
        resume_observation(session, source.id)
    elif structural_changed:
        reset_observation(session, source.id)
    if structural_changed or enabled_changed:
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
    _delete_source_and_orphan_registration(session, source)
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
    storage: dict | None = None,
    *,
    secret_store: CredentialSecretStore | None = None,
) -> EnvironmentSource:
    raw_uri = uri
    normalized_uri, binding = validate_and_normalize_binding(
        session, uri=uri, storage=storage, source_config=source_config
    )
    canonical_config, input_locations, canonical_locations = canonicalize_location_config(
        source_config, binding
    )
    registration, _ = get_or_create_registration(
        session,
        environment_id=environment_id,
        purpose="logs",
        input_uri=raw_uri,
        canonical_uri=normalized_uri,
        binding=binding,
        input_locations=input_locations,
        canonical_locations=canonical_locations,
    )
    existing = _source_for_registration(
        session, registration.id, "logs", normalized_uri
    )
    if existing is not None:
        session.commit()
        return existing
    path = EnvironmentSource(
        environment_id=environment_id,
        registration_id=registration.id,
        source_kind="logs",
        uri=normalized_uri,
        label=label,
        enabled=enabled,
        sync_schedule_enabled=False,
        sync_interval_minutes=1,
        source_config_json=json.dumps(canonical_config, sort_keys=True),
    )
    apply_binding(path, binding)
    session.add(path)
    session.flush()
    reset_observation(
        session,
        path.id,
        due_at=datetime.now(timezone.utc)
        + timedelta(seconds=source_check_interval_seconds(session)),
    )
    invalidate_environment_derived_caches(session, environment_id, structural=False)
    session.commit()
    session.refresh(path)
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
    storage: dict | None = None,
    sync_schedule_enabled: bool | None = None,
    sync_interval_minutes: int | None = None,
) -> EnvironmentSource | None:
    path = environment_source_by_id(session, environment_id, path_id, "logs")
    if path is None:
        return None
    changed = False
    material_changed = False
    enabled_changed = enabled is not None and enabled != path.enabled
    reenabled = enabled is True and not path.enabled
    if uri is not None or source_config is not None or storage is not None:
        current_binding = binding_to_dict(path)
        candidate_config = (
            source_config
            if source_config is not None
            else _json_object(path.source_config_json)
        )
        normalized_uri, binding = validate_and_normalize_binding(
            session,
            uri=uri if uri is not None else path.uri,
            storage=storage if storage is not None else binding_to_dict(path),
            source_config=candidate_config,
        )
        canonical_config, input_locations, canonical_locations = canonicalize_location_config(
            candidate_config, binding
        )
        if _has_location_input(uri, source_config):
            if source_config is None:
                input_locations = source_input_locations(path)
            registration, _ = get_or_create_registration(
                session,
                environment_id=environment_id,
                purpose="logs",
                input_uri=uri if uri is not None else _source_input_uri(path),
                canonical_uri=normalized_uri,
                binding=binding,
                input_locations=input_locations,
                canonical_locations=canonical_locations,
            )
            _ensure_registration_available(registration, path)
            update_registration_input(
                registration,
                input_uri=uri if uri is not None else _source_input_uri(path),
                input_locations=input_locations,
            )
            _replace_source_registration(session, path, registration.id)
        next_binding = {
            "provider": binding.provider,
            "auth_mode": binding.auth_mode,
            "credential_profile_id": binding.credential_profile_id,
            "options": binding.options,
        }
        binding_changed = current_binding != next_binding
        if normalized_uri != path.uri or binding_changed:
            material_changed = (
                normalized_uri != path.uri
                or current_binding["provider"] != next_binding["provider"]
                or current_binding["options"] != next_binding["options"]
            )
            path.uri = normalized_uri
            apply_binding(path, binding)
            changed = True
    if source_config is not None:
        config_json = json.dumps(canonical_config, sort_keys=True)
        if config_json != (path.source_config_json or "{}"):
            path.source_config_json = config_json
            changed = True
            material_changed = True
    if changed:
        _clear_source_read_check(path)
        _delete_source_sync_records(session, path)
        if material_changed:
            _delete_source_log_cache(session, path)
        else:
            log_ingestion.invalidate_pending_changes(path.id)
    elif enabled_changed:
        _clear_source_read_check(path)
        _delete_source_log_cache(session, path)
    if label is not None:
        path.label = label
    if enabled is not None:
        path.enabled = enabled
    if reenabled:
        resume_observation(session, path.id, pending_changes=False)
    elif changed:
        reset_observation(session, path.id, pending_changes=False)
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
    _delete_source_and_orphan_registration(session, path)
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
        ("source_observation", "source observation", _count_source_rows(session, SourceObservation, path.id), "info"),
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
    for row in _source_rows(session, SourceObservation, source.id):
        session.delete(row)


def _delete_source_sync_records(session: Session, source: EnvironmentSource) -> None:
    for model in (SourceObservation, SyncJob):
        for row in _source_rows(session, model, source.id):
            session.delete(row)


def _delete_source_log_cache(session: Session, source: EnvironmentSource) -> None:
    reset_log_control_state(session, source.id)
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
        "storage": binding_to_dict(item),
        "configured_location": configured_location_dict(item),
        "latest_validation": _validation_from_source(item),
    }


def _source_input_uri(source: EnvironmentSource) -> str:
    return source.registration.input_uri if source.registration is not None else source.uri


def _has_location_input(uri: str | None, source_config: dict | None) -> bool:
    return uri is not None or (
        source_config is not None
        and any(key in source_config for key in LOCATION_CONFIG_KEYS)
    )


def _replace_source_registration(
    session: Session,
    source: EnvironmentSource,
    registration_id: int,
) -> None:
    previous_id = source.registration_id
    source.registration_id = registration_id
    if previous_id is not None and previous_id != registration_id:
        session.flush()
        _delete_registration_if_orphan(session, previous_id)


def _ensure_registration_available(
    registration: SourceRegistration,
    source: EnvironmentSource,
) -> None:
    duplicate = next(
        (
            candidate
            for candidate in registration.sources
            if candidate.id != source.id and candidate.source_kind == source.source_kind
        ),
        None,
    )
    if duplicate is not None:
        raise StorageConfigurationError(
            f"This location is already configured as source {duplicate.id}"
        )


def _delete_source_and_orphan_registration(
    session: Session, source: EnvironmentSource
) -> None:
    registration_id = source.registration_id
    session.delete(source)
    session.flush()
    if registration_id is not None:
        _delete_registration_if_orphan(session, registration_id)


def _delete_registration_if_orphan(
    session: Session, registration_id: int
) -> None:
    remaining = session.scalar(
        select(func.count())
        .select_from(EnvironmentSource)
        .where(EnvironmentSource.registration_id == registration_id)
    )
    if not remaining:
        registration = session.get(SourceRegistration, registration_id)
        if registration is not None:
            session.delete(registration)


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
