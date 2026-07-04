from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from datacoolie_studio.domains.logs import cache as logs_cache
from datacoolie_studio.db.models import (
    Environment,
    EnvironmentMetadataEditorDraft,
    EnvironmentSource,
    LogFileManifest,
    CodeArtifactSnapshot,
    LineageSnapshot,
    MetadataBackup,
    MetadataEditorDraft,
    MetadataSaveEvent,
    MetadataSourceSnapshot,
    MetadataValidationResult,
    Project,
    SourceRevision,
    SyncJob,
)
from datacoolie_studio.domains.sources.discovery import (
    DiscoveredSource,
    discover_datacoolie_project_sources,
    discover_metadata_sources,
)
from datacoolie_studio.domains.storage.uri import normalized_source_uri

METADATA_SOURCE_DELETE_DEPENDENCIES = [
    ("draft", "saved draft", MetadataEditorDraft, "warning"),
    ("environment_draft", "environment draft", EnvironmentMetadataEditorDraft, "warning"),
    ("backup", "backup version", MetadataBackup, "warning"),
    ("validation_result", "validation result", MetadataValidationResult, "info"),
    ("save_event", "save event", MetadataSaveEvent, "info"),
    ("snapshot", "metadata snapshot", MetadataSourceSnapshot, "info"),
    ("source_revision", "source revision", SourceRevision, "info"),
    ("sync_job", "sync job", SyncJob, "info"),
]


def list_projects(session: Session) -> list[Project]:
    return list(session.scalars(select(Project).order_by(Project.name)))


def list_project_summaries(session: Session) -> list[dict]:
    summaries: list[dict] = []
    for project in list_projects(session):
        environments = list_environments(session, project.id)
        env_summaries = []
        metadata_total = 0
        log_total = 0
        for env in environments:
            metadata_count = session.scalar(
                select(func.count()).select_from(EnvironmentSource).where(
                    EnvironmentSource.environment_id == env.id,
                    EnvironmentSource.source_kind == "metadata",
                )
            ) or 0
            log_count = session.scalar(
                select(func.count()).select_from(EnvironmentSource).where(
                    EnvironmentSource.environment_id == env.id,
                    EnvironmentSource.source_kind == "logs",
                )
            ) or 0
            code_count = session.scalar(
                select(func.count()).select_from(EnvironmentSource).where(
                    EnvironmentSource.environment_id == env.id,
                    EnvironmentSource.source_kind == "code",
                )
            ) or 0
            metadata_total += int(metadata_count)
            log_total += int(log_count)
            env_summaries.append(
                {
                    "id": env.id,
                    "name": env.name,
                    "metadata_source_count": int(metadata_count),
                    "etl_log_path_count": int(log_count),
                    "code_artifact_count": int(code_count),
                    "created_at": env.created_at,
                    "updated_at": env.updated_at,
                }
            )
        summaries.append(
            {
                "id": project.id,
                "name": project.name,
                "description": project.description,
                "environment_count": len(environments),
                "metadata_source_count": metadata_total,
                "etl_log_path_count": log_total,
                "environments": env_summaries,
                "created_at": project.created_at,
                "updated_at": project.updated_at,
            }
        )
    return summaries


def create_project(session: Session, name: str, description: str | None = None) -> Project:
    project = Project(name=name.strip(), description=description)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def delete_project(session: Session, project_id: int) -> bool:
    project = session.get(Project, project_id)
    if project is None:
        return False
    source_ids = _etl_source_ids_for_project(session, project_id)
    _purge_analytics_cache_by_source_ids(source_ids)
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
) -> dict:
    discovery = discover_metadata_sources(uri, label=label)
    return _import_discovered_sources(
        session,
        environment_id,
        metadata_sources=discovery.metadata_sources,
        code_artifacts=[],
        errors=discovery.errors,
        enabled=enabled,
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
) -> dict:
    created: list[dict] = []
    existing: list[dict] = []

    for discovered in [*metadata_sources, *code_artifacts]:
        current = _existing_source_by_uri(session, environment_id, discovered.source_kind, discovered.uri)
        if current is not None:
            existing.append(_source_import_item(current, discovered, "existing"))
            continue
        current = _add_discovered_source(session, environment_id, discovered, enabled)
        created.append(_source_import_item(current, discovered, "created"))

    return {
        "created": created,
        "existing": existing,
        "errors": errors,
        "summary": {
            "created": len(created),
            "existing": len(existing),
            "errors": len(errors),
            "metadata_sources": sum(1 for item in [*created, *existing] if item["source_kind"] == "metadata"),
            "code_artifacts": sum(1 for item in [*created, *existing] if item["source_kind"] == "code"),
        },
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


def update_code_artifact(
    session: Session,
    source_id: int,
    *,
    uri: str | None = None,
    label: str | None = None,
    enabled: bool | None = None,
    source_config: dict | None = None,
    sync_schedule_enabled: bool | None = None,
    sync_interval_minutes: int | None = None,
) -> EnvironmentSource | None:
    source = session.get(EnvironmentSource, source_id)
    if source is None or source.source_kind != "code":
        return None
    changed = False
    if uri is not None:
        normalized_uri = uri.strip()
        if normalized_uri != source.uri:
            source.uri = normalized_uri
            changed = True
    if source_config is not None:
        config_json = json.dumps(source_config, sort_keys=True)
        if config_json != (source.source_config_json or "{}"):
            source.source_config_json = config_json
            changed = True
    if changed:
        _clear_source_read_check(source)
        _delete_source_sync_records(session, source)
        for row in _source_rows(session, CodeArtifactSnapshot, source.id):
            session.delete(row)
        _delete_environment_lineage_snapshots(session, source.environment_id)
    if label is not None:
        source.label = label
    if enabled is not None:
        source.enabled = enabled
    _update_schedule(source, sync_schedule_enabled, sync_interval_minutes)
    session.commit()
    session.refresh(source)
    return source


def delete_code_artifact(session: Session, source_id: int) -> bool:
    source = session.get(EnvironmentSource, source_id)
    if source is None or source.source_kind != "code":
        return False
    _delete_source_sync_records(session, source)
    for row in _source_rows(session, CodeArtifactSnapshot, source.id):
        session.delete(row)
    _delete_environment_lineage_snapshots(session, source.environment_id)
    session.delete(source)
    session.commit()
    return True


def update_metadata_source(
    session: Session,
    source_id: int,
    *,
    uri: str | None = None,
    label: str | None = None,
    enabled: bool | None = None,
    sync_schedule_enabled: bool | None = None,
    sync_interval_minutes: int | None = None,
) -> EnvironmentSource | None:
    source = session.get(EnvironmentSource, source_id)
    if source is not None and source.source_kind != "metadata":
        return None
    if source is None:
        return None
    if uri is not None:
        normalized_uri = uri.strip()
        if normalized_uri != source.uri:
            _clear_source_read_check(source)
            _delete_source_sync_records(session, source)
            source.uri = normalized_uri
    if label is not None:
        source.label = label
    if enabled is not None:
        source.enabled = enabled
    _update_schedule(source, sync_schedule_enabled, sync_interval_minutes)
    session.commit()
    session.refresh(source)
    return source


def delete_metadata_source(session: Session, source_id: int) -> bool:
    source = session.get(EnvironmentSource, source_id)
    if source is not None and source.source_kind != "metadata":
        return False
    if source is None:
        return False
    _delete_metadata_source_dependencies(session, source)
    session.delete(source)
    session.commit()
    return True


def metadata_source_delete_impact(session: Session, source_id: int) -> dict | None:
    source = session.get(EnvironmentSource, source_id)
    if source is not None and source.source_kind != "metadata":
        return None
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
        source_config_json=json.dumps(source_config or {}, sort_keys=True),
    )
    session.add(path)
    session.commit()
    session.refresh(path)
    return path


def update_log_source(
    session: Session,
    path_id: int,
    *,
    uri: str | None = None,
    label: str | None = None,
    enabled: bool | None = None,
    source_config: dict | None = None,
    sync_schedule_enabled: bool | None = None,
    sync_interval_minutes: int | None = None,
) -> EnvironmentSource | None:
    path = session.get(EnvironmentSource, path_id)
    if path is not None and path.source_kind != "logs":
        return None
    if path is None:
        return None
    changed = False
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
    if label is not None:
        path.label = label
    if enabled is not None:
        path.enabled = enabled
    _update_schedule(path, sync_schedule_enabled, sync_interval_minutes)
    session.commit()
    session.refresh(path)
    return path


def delete_log_source(session: Session, path_id: int) -> bool:
    path = session.get(EnvironmentSource, path_id)
    if path is not None and path.source_kind != "logs":
        return False
    if path is None:
        return False
    _delete_source_sync_records(session, path)
    _delete_source_log_cache(session, path)
    session.delete(path)
    session.commit()
    return True


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
    try:
        logs_cache.purge_cached_source_ids([source.id])
    except Exception:
        # Purging analytics cache is best-effort and should not block source updates/deletes.
        pass


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
        logs_cache.purge_cached_source_ids(source_ids)
    except Exception:
        # Purging analytics cache is best-effort and should not block project/environment deletes.
        pass


def _delete_environment_lineage_snapshots(session: Session, environment_id: int) -> None:
    rows = session.scalars(
        select(LineageSnapshot).where(LineageSnapshot.environment_id == environment_id)
    ).all()
    for row in rows:
        session.delete(row)


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


def _update_schedule(source: EnvironmentSource, enabled: bool | None, interval_minutes: int | None) -> None:
    if interval_minutes is not None and interval_minutes < 1:
        raise ValueError("Sync interval must be at least 1 minute")
    if enabled is not None:
        source.sync_schedule_enabled = enabled
    if interval_minutes is not None:
        source.sync_interval_minutes = interval_minutes
    if source.sync_schedule_enabled and not source.sync_interval_minutes:
        source.sync_interval_minutes = 60


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
