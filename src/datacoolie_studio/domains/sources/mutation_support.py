from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import (
    CodeArtifactMaterialization,
    EnvironmentMetadataEditorDraft,
    EnvironmentSource,
    MetadataBackup,
    MetadataEditorDraft,
    MetadataMaterialization,
    MetadataSaveEvent,
    MetadataValidationResult,
    SourceObservation,
    SourceRegistration,
    SyncJob,
)
from datacoolie_studio.domains.analytics import maintenance as analytics_maintenance
from datacoolie_studio.domains.metadata.backups import ArtifactCleanup
from datacoolie_studio.domains.logs import pending as log_pending
from datacoolie_studio.domains.logs.control import reset_log_control_state
from datacoolie_studio.domains.read_models.sqlite_store import SqliteResultCacheStore
from datacoolie_studio.domains.sources.registration import (
    LOCATION_CONFIG_INPUT_KEYS,
)
from datacoolie_studio.domains.storage.errors import StorageConfigurationError
from datacoolie_studio.domains.storage.uri import normalized_source_uri


# Internal PATCH sentinel. It keeps omitted nullable fields distinct from an
# explicit value without exposing a second public API shape.
UNSET = object()


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


def is_managed_project_metadata_source(
    source: EnvironmentSource,
    config: dict[str, Any],
) -> bool:
    if config.get("discovery_managed") is False:
        return False
    if config.get("discovery_managed") is True:
        registration = source.registration
        return registration is not None and registration.purpose == "project"
    if config.get("discovery_mode") != "datacoolie_project":
        return False
    project_uri = config.get("project_uri")
    registration = source.registration
    return registration is not None and registration.purpose == "project" and (
        not project_uri
        or normalized_source_uri(str(project_uri))
        == normalized_source_uri(registration.canonical_uri)
    )


def is_managed_project_code_source(
    source: EnvironmentSource,
    config: dict[str, Any],
) -> bool:
    if config.get("discovery_managed") is False:
        return False
    if config.get("discovery_managed") is True:
        return True
    if config.get("discovery_mode") != "datacoolie_project":
        return False
    project_uri = config.get("project_uri")
    registration = source.registration
    return registration is not None and (
        not project_uri
        or normalized_source_uri(str(project_uri))
        == normalized_source_uri(registration.canonical_uri)
    )


def is_managed_project_sql_source(
    source: EnvironmentSource,
    config: dict[str, Any],
) -> bool:
    if config.get("discovery_managed") is False:
        return False
    if config.get("discovery_managed") is True:
        registration = source.registration
        return registration is not None and registration.purpose == "project"
    if config.get("discovery_mode") != "datacoolie_project":
        return False
    project_uri = config.get("project_uri")
    registration = source.registration
    return registration is not None and registration.purpose == "project" and (
        not project_uri
        or normalized_source_uri(str(project_uri))
        == normalized_source_uri(registration.canonical_uri)
    )


def invalidate_code_artifact_state(
    session: Session,
    source: EnvironmentSource,
) -> None:
    clear_source_read_check(source)
    delete_source_sync_records(session, source)
    for row in source_rows(session, CodeArtifactMaterialization, source.id):
        session.delete(row)
    from datacoolie_studio.domains.code_artifacts.materializer import (
        clear_remote_artifact_snapshot,
    )

    clear_remote_artifact_snapshot(source.id)


def delete_metadata_source_dependencies(
    session: Session,
    source: EnvironmentSource,
) -> ArtifactCleanup:
    from datacoolie_studio.domains.metadata.backups import delete_backup_rows

    backups = source_rows(session, MetadataBackup, source.id)
    cleanup = delete_backup_rows(session, backups)

    for _, _, model, _ in METADATA_SOURCE_DELETE_DEPENDENCIES:
        rows = (
            environment_rows(session, model, source.environment_id)
            if model is EnvironmentMetadataEditorDraft
            else source_rows(session, model, source.id)
        )
        for row in rows:
            session.delete(row)
    for row in source_rows(session, SourceObservation, source.id):
        session.delete(row)
    return cleanup


def delete_source_sync_records(session: Session, source: EnvironmentSource) -> None:
    for model in (SourceObservation, SyncJob):
        for row in source_rows(session, model, source.id):
            session.delete(row)


def delete_source_log_cache(session: Session, source: EnvironmentSource) -> None:
    reset_log_control_state(session, source.id)
    log_pending.invalidate_pending_changes(source.id)
    analytics_maintenance.purge_source_ids([source.id])


def clear_source_read_check(source: EnvironmentSource) -> None:
    source.read_check_status = None
    source.read_checked_at = None
    source.read_check_result_json = None


def source_rows(session: Session, model, source_id: int) -> list:
    return list(session.scalars(select(model).where(model.source_id == source_id)).all())


def source_rows_by_column(session: Session, model, column, source_id: int) -> list:
    return list(session.scalars(select(model).where(column == source_id)).all())


def count_source_rows(session: Session, model, source_id: int) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(model).where(model.source_id == source_id)
        )
        or 0
    )


def environment_rows(session: Session, model, environment_id: int) -> list:
    return list(session.scalars(select(model).where(model.environment_id == environment_id)).all())


def count_environment_rows(session: Session, model, environment_id: int) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(model).where(model.environment_id == environment_id)
        )
        or 0
    )


def count_environment_read_models(environment_id: int, model_key: str) -> int:
    return SqliteResultCacheStore().entry_count(environment_id, model_key)


def delete_impact_summary(impacts: list[dict]) -> str:
    if not impacts:
        return "No related Studio data will be removed. The metadata file will not be deleted."
    parts = [f"{item['count']} {item['label']}" for item in impacts]
    related = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])}, and {parts[-1]}"
    return f"Deleting this metadata source will also remove {related}. The metadata file will not be deleted."


def pluralize(label: str, count: int) -> str:
    if count == 1:
        return label
    if label.endswith("y"):
        return f"{label[:-1]}ies"
    return f"{label}s"


def source_input_uri(source: EnvironmentSource) -> str:
    return source.registration.input_uri if source.registration is not None else source.uri


def has_location_input(uri: str | None, source_config: dict | None) -> bool:
    return uri is not None or (
        source_config is not None
        and any(key in source_config for key in LOCATION_CONFIG_INPUT_KEYS)
    )


def replace_source_registration(
    session: Session,
    source: EnvironmentSource,
    registration_id: int,
) -> None:
    previous_id = source.registration_id
    source.registration_id = registration_id
    if previous_id is not None and previous_id != registration_id:
        session.flush()
        delete_registration_if_orphan(session, previous_id)


def ensure_registration_available(
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


def delete_source_and_orphan_registration(
    session: Session,
    source: EnvironmentSource,
) -> None:
    registration_id = source.registration_id
    session.delete(source)
    session.flush()
    if registration_id is not None:
        delete_registration_if_orphan(session, registration_id)


def delete_registration_if_orphan(session: Session, registration_id: int) -> None:
    remaining = session.scalar(
        select(func.count())
        .select_from(EnvironmentSource)
        .where(EnvironmentSource.registration_id == registration_id)
    )
    if not remaining:
        registration = session.get(SourceRegistration, registration_id)
        if registration is not None:
            session.delete(registration)


def update_schedule(
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


def log_delete_impact_summary(impacts: list[dict]) -> str:
    if not impacts:
        return "No cached Studio data will be removed. Original log files will not be deleted."
    parts = [f"{item['count']} {item['label']}" for item in impacts]
    related = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])}, and {parts[-1]}"
    return f"Deleting this log source will also remove {related}. Original log files will not be deleted."


def source_delete_impact_summary(impacts: list[dict], source_label: str) -> str:
    if not impacts:
        return f"No related Studio data will be removed. The original {source_label} will not be deleted."
    parts = [f"{item['count']} {item['label']}" for item in impacts]
    related = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])}, and {parts[-1]}"
    return f"Deleting this source will also remove {related}. The original {source_label} will not be deleted."


def json_object(value: str | None) -> dict:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def merge_source_config(
    current: dict[str, Any],
    patch: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply the source PATCH contract: shallow top-level merge."""
    merged = dict(current)
    if patch is not None:
        merged.update(patch)
    return merged
