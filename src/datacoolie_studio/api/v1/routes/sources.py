from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from datacoolie_studio.api.v1.contracts.sources import (
    CodeArtifactRead,
    LocalSourceObservationResponse,
    LogSourceRead,
    LogSyncRequest,
    MetadataSourceRead,
    SourceDeleteImpactResponse,
    SourceSyncStatusResponse,
    SourceUpdate,
    SourceValidationResponse,
    SqlSourceRead,
    StorageConnectionValidationRequest,
    StorageConnectionValidationResponse,
)
from datacoolie_studio.api.v1.dependencies import (
    get_credential_secret_store,
    require_loopback_client,
)
from datacoolie_studio.domains.credentials.store import CredentialSecretStore
from datacoolie_studio.db.session import get_session
from datacoolie_studio.db.models import Environment
from datacoolie_studio.domains.logs.discovery import LogSyncMode, LogSyncSpec, LookbackRange
from datacoolie_studio.domains.sources import service as sources
from datacoolie_studio.domains.sources import application as source_application
from datacoolie_studio.domains.sources import repository as source_repository
from datacoolie_studio.domains.sources.mutation_support import UNSET
from datacoolie_studio.domains.sources.serialization import source_to_dict
from datacoolie_studio.domains.sync.scheduler import (
    ObservationRetryUnavailableError,
    observe_environment_local_sources,
)
from datacoolie_studio.domains.storage.errors import StorageConfigurationError
from datacoolie_studio.domains.metadata.write_operations import ActiveWriteOperationError
from datacoolie_studio.domains.metadata.errors import MetadataConflictError

router = APIRouter(tags=["sources"])


def _payload_value(payload: SourceUpdate, field: str):
    if field not in payload.model_fields_set:
        return UNSET
    return getattr(payload, field)


@router.post(
    "/environments/{environment_id}/sources/observe-local",
    response_model=LocalSourceObservationResponse,
)
def observe_local_sources(
    environment_id: int,
    session: Session = Depends(get_session),
):
    if session.get(Environment, environment_id) is None:
        raise HTTPException(status_code=404, detail="Environment not found")
    return observe_environment_local_sources(session, environment_id)


@router.patch("/environments/{environment_id}/metadata-sources/{source_id}", response_model=MetadataSourceRead)
def patch_metadata_source(
    environment_id: int,
    source_id: int,
    payload: SourceUpdate,
    request: Request,
    session: Session = Depends(get_session),
):
    _reject_non_log_schedule(payload)
    require_loopback_client(request)
    current = source_repository.get_source(
        session, environment_id, source_id, "metadata"
    )
    _guard_payload_or_source_profile(request, payload, current)
    try:
        source = source_application.update_metadata_source(
            session,
            environment_id,
            source_id,
            uri=payload.uri,
            label=_payload_value(payload, "label"),
            enabled=payload.enabled,
            source_config=payload.source_config,
            storage=_storage_payload(payload.storage),
        )
    except StorageConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (ActiveWriteOperationError, MetadataConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if source is None:
        raise HTTPException(status_code=404, detail="Metadata source not found")
    return source_to_dict(source)


@router.get("/environments/{environment_id}/metadata-sources/{source_id}/delete-impact", response_model=SourceDeleteImpactResponse)
def get_metadata_source_delete_impact(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    impact = source_application.metadata_source_delete_impact(session, environment_id, source_id)
    if impact is None:
        raise HTTPException(status_code=404, detail="Metadata source not found")
    return impact


@router.delete("/environments/{environment_id}/metadata-sources/{source_id}", status_code=204)
def delete_metadata_source(
    environment_id: int,
    source_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    require_loopback_client(request)
    try:
        deleted = source_application.delete_metadata_source(
            session, environment_id, source_id
        )
    except (ActiveWriteOperationError, MetadataConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Metadata source not found")
    return Response(status_code=204)


@router.post("/environments/{environment_id}/metadata-sources/{source_id}/validate", response_model=SourceValidationResponse)
def validate_metadata_source(
    environment_id: int,
    source_id: int,
    request: Request,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    source = source_repository.get_source(session, environment_id, source_id, "metadata")
    if source is None:
        raise HTTPException(status_code=404, detail="Metadata source not found")
    _guard_source_profile(request, source)
    return source_application.validate_source(
        session, source, secret_store=secret_store
    )


@router.post(
    "/environments/{environment_id}/metadata-sources/{source_id}/retry-observation",
    response_model=SourceSyncStatusResponse,
)
def retry_metadata_source_observation(
    environment_id: int,
    source_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    return _retry_observation(
        session, environment_id, source_id, "metadata", request
    )


@router.post("/environments/{environment_id}/metadata-sources/{source_id}/refresh", response_model=SourceSyncStatusResponse)
def refresh_metadata_source(
    environment_id: int,
    source_id: int,
    request: Request,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    source = source_repository.get_source(session, environment_id, source_id, "metadata")
    if source is None:
        raise HTTPException(status_code=404, detail="Metadata source not found")
    _guard_source_profile(request, source)
    try:
        return source_application.refresh_source(
            session,
            source,
            secret_store=secret_store,
        )
    except source_application.SourceRefreshBusyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Metadata source refresh is already running",
        ) from exc


@router.patch("/environments/{environment_id}/sql-sources/{source_id}", response_model=SqlSourceRead)
def patch_sql_source(
    environment_id: int,
    source_id: int,
    payload: SourceUpdate,
    request: Request,
    session: Session = Depends(get_session),
):
    _reject_non_log_schedule(payload)
    require_loopback_client(request)
    current = source_repository.get_source(session, environment_id, source_id, "sql")
    _guard_payload_or_source_profile(request, payload, current)
    try:
        source = source_application.update_sql_source(
            session,
            environment_id,
            source_id,
            uri=payload.uri,
            label=_payload_value(payload, "label"),
            enabled=payload.enabled,
            source_config=payload.source_config,
            storage=_storage_payload(payload.storage),
        )
    except StorageConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (ActiveWriteOperationError, MetadataConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if source is None:
        raise HTTPException(status_code=404, detail="SQL source not found")
    return source_to_dict(source)


@router.get("/environments/{environment_id}/sql-sources/{source_id}/delete-impact", response_model=SourceDeleteImpactResponse)
def get_sql_source_delete_impact(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    impact = source_application.sql_source_delete_impact(session, environment_id, source_id)
    if impact is None:
        raise HTTPException(status_code=404, detail="SQL source not found")
    return impact


@router.delete("/environments/{environment_id}/sql-sources/{source_id}", status_code=204)
def delete_sql_source(
    environment_id: int,
    source_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    require_loopback_client(request)
    try:
        deleted = source_application.delete_sql_source(session, environment_id, source_id)
    except (ActiveWriteOperationError, MetadataConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="SQL source not found")
    return Response(status_code=204)


@router.post("/environments/{environment_id}/sql-sources/{source_id}/validate", response_model=SourceValidationResponse)
def validate_sql_source(
    environment_id: int,
    source_id: int,
    request: Request,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    source = source_repository.get_source(session, environment_id, source_id, "sql")
    if source is None:
        raise HTTPException(status_code=404, detail="SQL source not found")
    _guard_source_profile(request, source)
    return source_application.validate_source(
        session, source, secret_store=secret_store
    )


@router.post(
    "/environments/{environment_id}/sql-sources/{source_id}/retry-observation",
    response_model=SourceSyncStatusResponse,
)
def retry_sql_source_observation(
    environment_id: int,
    source_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    return _retry_observation(session, environment_id, source_id, "sql", request)


@router.post("/environments/{environment_id}/sql-sources/{source_id}/refresh", response_model=SourceSyncStatusResponse)
def refresh_sql_source(
    environment_id: int,
    source_id: int,
    request: Request,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    source = source_repository.get_source(session, environment_id, source_id, "sql")
    if source is None:
        raise HTTPException(status_code=404, detail="SQL source not found")
    _guard_source_profile(request, source)
    try:
        return source_application.refresh_source(
            session,
            source,
            secret_store=secret_store,
        )
    except source_application.SourceRefreshBusyError as exc:
        raise HTTPException(
            status_code=409,
            detail="SQL source refresh is already running",
        ) from exc


@router.patch("/environments/{environment_id}/log-sources/{source_id}", response_model=LogSourceRead)
def patch_log_source(
    environment_id: int,
    source_id: int,
    payload: SourceUpdate,
    request: Request,
    session: Session = Depends(get_session),
):
    current = source_repository.get_source(
        session, environment_id, source_id, "logs"
    )
    _guard_payload_or_source_profile(request, payload, current)
    try:
        path = source_application.update_log_source(
            session,
            environment_id,
            source_id,
            uri=payload.uri,
            label=_payload_value(payload, "label"),
            enabled=payload.enabled,
            source_config=payload.source_config,
            storage=_storage_payload(payload.storage),
            sync_schedule_enabled=payload.sync_schedule_enabled,
            sync_interval_minutes=payload.sync_interval_minutes,
        )
    except StorageConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MetadataConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if path is None:
        raise HTTPException(status_code=404, detail="Log source not found")
    return source_to_dict(path)


@router.get("/environments/{environment_id}/log-sources/{source_id}/delete-impact", response_model=SourceDeleteImpactResponse)
def get_log_source_delete_impact(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    impact = source_application.log_source_delete_impact(session, environment_id, source_id)
    if impact is None:
        raise HTTPException(status_code=404, detail="Log source not found")
    return impact


@router.delete("/environments/{environment_id}/log-sources/{source_id}", status_code=204)
def delete_log_source(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    try:
        if not source_application.delete_log_source(session, environment_id, source_id):
            raise HTTPException(status_code=404, detail="Log source not found")
        return Response(status_code=204)
    except source_application.SourceRefreshBusyError as exc:
        raise HTTPException(status_code=409, detail="Log source refresh is already running; try deleting again when it finishes") from exc
    except MetadataConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/environments/{environment_id}/log-sources/{source_id}/validate", response_model=SourceValidationResponse)
def validate_log_source(
    environment_id: int,
    source_id: int,
    request: Request,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    path = source_repository.get_source(session, environment_id, source_id, "logs")
    if path is None:
        raise HTTPException(status_code=404, detail="Log source not found")
    _guard_source_profile(request, path)
    return source_application.validate_source(
        session, path, secret_store=secret_store
    )


@router.post(
    "/environments/{environment_id}/log-sources/{source_id}/retry-observation",
    response_model=SourceSyncStatusResponse,
)
def retry_log_source_observation(
    environment_id: int,
    source_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    return _retry_observation(
        session, environment_id, source_id, "logs", request
    )


@router.post("/environments/{environment_id}/log-sources/{source_id}/refresh", response_model=SourceSyncStatusResponse)
def refresh_log_source(
    environment_id: int,
    source_id: int,
    payload: LogSyncRequest,
    request: Request,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    path = source_repository.get_source(session, environment_id, source_id, "logs")
    if path is None:
        raise HTTPException(status_code=404, detail="Log source not found")
    _guard_source_profile(request, path)
    try:
        return source_application.refresh_source(
            session,
            path,
            secret_store=secret_store,
            log_sync_spec=_log_sync_spec(payload),
        )
    except source_application.AnalyticsUpgradeBusyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Analytics cache upgrade in progress; try syncing again shortly",
        ) from exc
    except source_application.SourceRefreshBusyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Log source refresh is already running",
        ) from exc


def _log_sync_spec(payload: LogSyncRequest) -> LogSyncSpec:
    mode = LogSyncMode(payload.mode)
    if payload.lookback is None:
        return LogSyncSpec(mode=mode)
    return LogSyncSpec(
        mode=mode,
        lookback=LookbackRange(
            from_partition=date.fromisoformat(payload.lookback.from_partition),
            to_partition=date.fromisoformat(payload.lookback.to_partition),
        ),
    )


@router.patch("/environments/{environment_id}/code-artifacts/{source_id}", response_model=CodeArtifactRead)
def patch_code_artifact(
    environment_id: int,
    source_id: int,
    payload: SourceUpdate,
    request: Request,
    session: Session = Depends(get_session),
):
    _reject_non_log_schedule(payload)
    current = source_repository.get_source(
        session, environment_id, source_id, "code"
    )
    _guard_payload_or_source_profile(request, payload, current)
    try:
        source = source_application.update_code_artifact(
            session,
            environment_id,
            source_id,
            uri=payload.uri,
            label=_payload_value(payload, "label"),
            enabled=payload.enabled,
            source_config=payload.source_config,
            storage=_storage_payload(payload.storage),
        )
    except StorageConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MetadataConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if source is None:
        raise HTTPException(status_code=404, detail="Code artifact not found")
    return source_to_dict(source)


@router.delete("/environments/{environment_id}/code-artifacts/{source_id}", status_code=204)
def delete_code_artifact(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    try:
        if not source_application.delete_code_artifact(session, environment_id, source_id):
            raise HTTPException(status_code=404, detail="Code artifact not found")
    except MetadataConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)


@router.get("/environments/{environment_id}/code-artifacts/{source_id}/delete-impact", response_model=SourceDeleteImpactResponse)
def get_code_artifact_delete_impact(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    impact = source_application.code_artifact_delete_impact(session, environment_id, source_id)
    if impact is None:
        raise HTTPException(status_code=404, detail="Code artifact not found")
    return impact


@router.post("/environments/{environment_id}/code-artifacts/{source_id}/validate", response_model=SourceValidationResponse)
def validate_code_artifact_source(
    environment_id: int,
    source_id: int,
    request: Request,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    source = source_repository.get_source(session, environment_id, source_id, "code")
    if source is None:
        raise HTTPException(status_code=404, detail="Code artifact not found")
    _guard_source_profile(request, source)
    return source_application.validate_source(
        session, source, secret_store=secret_store
    )


@router.post(
    "/environments/{environment_id}/code-artifacts/{source_id}/retry-observation",
    response_model=SourceSyncStatusResponse,
)
def retry_code_artifact_observation(
    environment_id: int,
    source_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    return _retry_observation(
        session, environment_id, source_id, "code", request
    )


@router.post("/environments/{environment_id}/code-artifacts/{source_id}/refresh", response_model=SourceSyncStatusResponse)
def refresh_code_artifact_source(
    environment_id: int,
    source_id: int,
    request: Request,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    source = source_repository.get_source(session, environment_id, source_id, "code")
    if source is None:
        raise HTTPException(status_code=404, detail="Code artifact not found")
    _guard_source_profile(request, source)
    try:
        return source_application.refresh_source(
            session,
            source,
            secret_store=secret_store,
        )
    except source_application.SourceRefreshBusyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Code artifact refresh is already running",
        ) from exc


def _reject_non_log_schedule(payload: SourceUpdate) -> None:
    if payload.sync_schedule_enabled is not None or payload.sync_interval_minutes is not None:
        raise HTTPException(status_code=422, detail="Only Log sources support scheduled refresh")


def _retry_observation(
    session: Session,
    environment_id: int,
    source_id: int,
    source_kind: str,
    request: Request,
) -> dict:
    source = source_repository.get_source(
        session, environment_id, source_id, source_kind
    )
    if source is None:
        labels = {
            "metadata": "Metadata source",
            "sql": "SQL source",
            "logs": "Log source",
            "code": "Code artifact",
        }
        raise HTTPException(
            status_code=404,
            detail=f"{labels[source_kind]} not found",
        )
    _guard_source_profile(request, source)
    try:
        return source_application.retry_source(session, source)
    except source_application.SourceObservationDisabledError as exc:
        raise HTTPException(
            status_code=409,
            detail="Enable the source before retrying automatic checks",
        ) from exc
    except ObservationRetryUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/storage-connections/validate",
    response_model=StorageConnectionValidationResponse,
)
def validate_storage_connection(
    payload: StorageConnectionValidationRequest,
    request: Request,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    if payload.storage and payload.storage.auth_mode == "credential_profile":
        require_loopback_client(request)
    return sources.validate_storage_connection(
        session,
        uri=payload.uri,
        storage=_storage_payload(payload.storage),
        source_config=payload.source_config,
        secret_store=secret_store,
    )


def _storage_payload(storage) -> dict | None:
    return storage.model_dump() if storage is not None else None


def _guard_payload_or_source_profile(
    request: Request, payload: SourceUpdate, source
) -> None:
    payload_uses_profile = (
        payload.storage is not None
        and payload.storage.auth_mode == "credential_profile"
    )
    source_uses_profile = (
        source is not None and source.storage_auth_mode == "credential_profile"
    )
    if payload_uses_profile or source_uses_profile:
        require_loopback_client(request)


def _guard_source_profile(request: Request, source) -> None:
    if source.storage_auth_mode == "credential_profile":
        require_loopback_client(request)
