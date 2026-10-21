from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.credentials.store import CredentialSecretStore
from datacoolie_studio.domains.sources.initialization import (
    queue_source_initialization_ids,
)
from datacoolie_studio.domains.sources.persistence import create_source
from datacoolie_studio.domains.sources.project_import import (
    import_datacoolie_project_sources as _import_datacoolie_project_sources,
    import_metadata_sources as _import_metadata_sources,
)
from datacoolie_studio.domains.sources.operations import (
    AnalyticsUpgradeBusyError,
    SourceObservationDisabledError,
    SourceRefreshBusyError,
    refresh_source,
    retry_source,
    validate_source,
)
from datacoolie_studio.domains.sources.deletions import (
    code_artifact_delete_impact,
    delete_code_artifact as _delete_code_artifact,
    delete_log_source as _delete_log_source,
    delete_metadata_source as _delete_metadata_source,
    delete_sql_source as _delete_sql_source,
    log_source_delete_impact,
    metadata_source_delete_impact,
    sql_source_delete_impact,
)
from datacoolie_studio.domains.sources.updates import (
    update_code_artifact as _update_code_artifact,
    update_log_source as _update_log_source,
    update_metadata_source as _update_metadata_source,
    update_sql_source as _update_sql_source,
)
from datacoolie_studio.domains.sources.mutation_support import UNSET
from datacoolie_studio.domains.metadata.write_operations import (
    ensure_source_not_in_active_write,
    guarded_publication,
)
from datacoolie_studio.domains.sync.service import source_refresh_guard
from datacoolie_studio.domains.sources.repository import get_source
from datacoolie_studio.domains.sources.mutation_context import source_mutation

__all__ = [
    "AnalyticsUpgradeBusyError",
    "SourceCreationResult",
    "SourceObservationDisabledError",
    "SourceRefreshBusyError",
    "create_code_artifact",
    "create_log_source",
    "create_metadata_source",
    "create_sql_source",
    "import_datacoolie_project_sources",
    "import_metadata_sources",
    "code_artifact_delete_impact",
    "delete_code_artifact",
    "delete_log_source",
    "delete_metadata_source",
    "delete_sql_source",
    "log_source_delete_impact",
    "metadata_source_delete_impact",
    "refresh_source",
    "retry_source",
    "sql_source_delete_impact",
    "update_code_artifact",
    "update_log_source",
    "update_metadata_source",
    "update_sql_source",
    "validate_source",
]


@dataclass(frozen=True)
class SourceCreationResult:
    """Result of a manual source creation use case."""

    source: EnvironmentSource
    initialization_job_ids: list[int]


@source_mutation
def import_metadata_sources(
    session: Session,
    environment_id: int,
    *,
    uri: str,
    label=UNSET,
    enabled: bool = True,
    storage: dict | None = None,
) -> dict:
    result = _import_metadata_sources(
        session,
        environment_id,
        uri=uri,
        label=label,
        enabled=enabled,
        storage=storage,
    )
    job_ids = queue_source_initialization_ids(
        session,
        _source_ids(result),
        commit=False,
    )
    session.commit()
    result["summary"]["initialization_queued"] = len(job_ids)
    result["initialization_job_ids"] = job_ids
    return result


@source_mutation
def import_datacoolie_project_sources(
    session: Session,
    environment_id: int,
    *,
    project_uri: str,
    metadata_subpath: str | None = None,
    sql_subpaths: Sequence[str] | None = None,
    code_subpaths: Sequence[str] | None = None,
    metadata_uri: str | None = None,
    code_uri: str | None = None,
    code_discovery_mode: str = "auto",
    enabled: bool = True,
    storage: dict | None = None,
    secret_store: CredentialSecretStore | None = None,
) -> dict:
    result = _import_datacoolie_project_sources(
        session,
        environment_id,
        project_uri=project_uri,
        metadata_subpath=metadata_subpath,
        sql_subpaths=sql_subpaths,
        code_subpaths=code_subpaths,
        metadata_uri=metadata_uri,
        code_uri=code_uri,
        code_discovery_mode=code_discovery_mode,
        enabled=enabled,
        storage=storage,
        secret_store=secret_store,
    )
    job_ids = queue_source_initialization_ids(
        session,
        _source_ids(result),
        commit=False,
    )
    session.commit()
    result["summary"]["initialization_queued"] = len(job_ids)
    result["initialization_job_ids"] = job_ids
    return result


@source_mutation
def update_code_artifact(
    session: Session,
    environment_id: int,
    source_id: int,
    *,
    uri: str | None = None,
    label=UNSET,
    enabled: bool | None = None,
    source_config: dict | None = None,
    storage: dict | None = None,
) -> EnvironmentSource | None:
    return _commit_source_update(
        session,
        _update_code_artifact(
            session,
            environment_id,
            source_id,
            uri=uri,
            label=label,
            enabled=enabled,
            source_config=source_config,
            storage=storage,
        ),
    )


@source_mutation
def update_metadata_source(
    session: Session,
    environment_id: int,
    source_id: int,
    *,
    uri: str | None = None,
    label=UNSET,
    enabled: bool | None = None,
    source_config: dict | None = None,
    storage: dict | None = None,
) -> EnvironmentSource | None:
    ensure_source_not_in_active_write(
        session, source_id, environment_id=environment_id
    )
    return _commit_source_update(
        session,
        _update_metadata_source(
            session,
            environment_id,
            source_id,
            uri=uri,
            label=label,
            enabled=enabled,
            source_config=source_config,
            storage=storage,
        ),
    )


@source_mutation
def update_sql_source(
    session: Session,
    environment_id: int,
    source_id: int,
    *,
    uri: str | None = None,
    label=UNSET,
    enabled: bool | None = None,
    source_config: dict | None = None,
    storage: dict | None = None,
) -> EnvironmentSource | None:
    ensure_source_not_in_active_write(
        session, source_id, environment_id=environment_id
    )
    return _commit_source_update(
        session,
        _update_sql_source(
            session,
            environment_id,
            source_id,
            uri=uri,
            label=label,
            enabled=enabled,
            source_config=source_config,
            storage=storage,
        ),
    )


@source_mutation
def update_log_source(
    session: Session,
    environment_id: int,
    source_id: int,
    *,
    uri: str | None = None,
    label=UNSET,
    enabled: bool | None = None,
    source_config: dict | None = None,
    storage: dict | None = None,
    sync_schedule_enabled: bool | None = None,
    sync_interval_minutes: int | None = None,
) -> EnvironmentSource | None:
    return _commit_source_update(
        session,
        _update_log_source(
            session,
            environment_id,
            source_id,
            uri=uri,
            label=label,
            enabled=enabled,
            source_config=source_config,
            storage=storage,
            sync_schedule_enabled=sync_schedule_enabled,
            sync_interval_minutes=sync_interval_minutes,
        ),
    )


@source_mutation
def delete_code_artifact(session: Session, environment_id: int, source_id: int) -> bool:
    return _commit_source_delete(
        session,
        _delete_code_artifact(session, environment_id, source_id),
    )


@guarded_publication
def delete_metadata_source(session: Session, environment_id: int, source_id: int) -> bool:
    cleanup = _delete_metadata_source_transaction(session, environment_id, source_id)
    if cleanup is False:
        return False
    from datacoolie_studio.domains.metadata.backups import remove_committed_artifacts
    remove_committed_artifacts(session, cleanup)
    return True


@source_mutation
def _delete_metadata_source_transaction(session, environment_id, source_id):
    ensure_source_not_in_active_write(
        session, source_id, environment_id=environment_id
    )
    cleanup = _delete_metadata_source(session, environment_id, source_id)
    if cleanup is False:
        return False
    session.commit()
    return cleanup


@source_mutation
def delete_sql_source(session: Session, environment_id: int, source_id: int) -> bool:
    ensure_source_not_in_active_write(
        session, source_id, environment_id=environment_id
    )
    return _commit_source_delete(
        session,
        _delete_sql_source(session, environment_id, source_id),
    )


@source_mutation
def delete_log_source(session: Session, environment_id: int, source_id: int) -> bool:
    if get_source(session, environment_id, source_id, "logs") is None:
        return False
    with source_refresh_guard(source_id) as acquired:
        if not acquired:
            raise SourceRefreshBusyError("logs")
        return _commit_source_delete(
            session, _delete_log_source(session, environment_id, source_id),
        )


def _source_ids(result: dict) -> list[int]:
    return [int(item["id"]) for item in [*result["created"], *result["existing"]]]


def _commit_source_update(
    session: Session,
    source: EnvironmentSource | None,
) -> EnvironmentSource | None:
    if source is None:
        return None
    session.commit()
    session.refresh(source)
    return source


def _commit_source_delete(session: Session, deleted: bool) -> bool:
    if deleted:
        session.commit()
    return deleted


@source_mutation
def create_metadata_source(
    session: Session,
    environment_id: int,
    *,
    uri: str,
    label: str | None,
    enabled: bool,
    source_config: dict | None = None,
    storage: dict | None = None,
) -> SourceCreationResult:
    return _create(
        session,
        create_source(
            session,
            environment_id,
            source_kind="metadata",
            uri=uri,
            label=label,
            enabled=enabled,
            source_config=source_config,
            storage=storage,
        ),
    )


@source_mutation
def create_sql_source(
    session: Session,
    environment_id: int,
    *,
    uri: str,
    label: str | None,
    enabled: bool,
    source_config: dict | None = None,
    storage: dict | None = None,
) -> SourceCreationResult:
    return _create(
        session,
        create_source(
            session,
            environment_id,
            source_kind="sql",
            uri=uri,
            label=label,
            enabled=enabled,
            source_config=source_config,
            storage=storage,
        ),
    )


@source_mutation
def create_log_source(
    session: Session,
    environment_id: int,
    *,
    uri: str,
    label: str | None,
    enabled: bool,
    source_config: dict | None = None,
    storage: dict | None = None,
) -> SourceCreationResult:
    return _create(
        session,
        create_source(
            session,
            environment_id,
            source_kind="logs",
            uri=uri,
            label=label,
            enabled=enabled,
            source_config=source_config,
            storage=storage,
        ),
    )


@source_mutation
def create_code_artifact(
    session: Session,
    environment_id: int,
    *,
    uri: str,
    label: str | None,
    enabled: bool,
    source_config: dict | None = None,
    storage: dict | None = None,
) -> SourceCreationResult:
    return _create(
        session,
        create_source(
            session,
            environment_id,
            source_kind="code",
            uri=uri,
            label=label,
            enabled=enabled,
            source_config=source_config,
            storage=storage,
        ),
    )


def _create(session: Session, source: EnvironmentSource) -> SourceCreationResult:
    job_ids = queue_source_initialization_ids(
        session,
        [source.id],
        commit=False,
    )
    session.commit()
    session.refresh(source)
    return SourceCreationResult(source=source, initialization_job_ids=job_ids)
