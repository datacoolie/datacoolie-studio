from __future__ import annotations

from datetime import date, timedelta

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
    StorageConnectionValidationRequest,
    StorageConnectionValidationResponse,
)
from datacoolie_studio.api.v1.routes.credentials import (
    get_credential_secret_store,
    require_loopback_client,
)
from datacoolie_studio.domains.credentials.store import CredentialSecretStore
from datacoolie_studio.domains.code_artifacts.service import refresh_code_artifact, validate_code_artifact
from datacoolie_studio.db.session import get_session
from datacoolie_studio.db.models import Environment, utc_now
from datacoolie_studio.domains.logs.ingestion import refresh_log_source_cache
from datacoolie_studio.domains.logs.discovery import LogSyncMode, LogSyncSpec, LookbackRange
from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.domains.metadata.service import ensure_metadata_materialization
from datacoolie_studio.domains.sources import service as sources
from datacoolie_studio.domains.sync import service as sync
from datacoolie_studio.domains.sync.scheduler import (
    ObservationRetryUnavailableError,
    observe_environment_local_sources,
    retry_source_observation,
)
from datacoolie_studio.domains.source_observation.repository import (
    reset_observation,
    resume_observation,
)
from datacoolie_studio.domains.studio_settings.service import (
    source_check_interval_seconds,
)
from datacoolie_studio.domains.workspace import service as workspace
from datacoolie_studio.domains.storage.errors import StorageConfigurationError

router = APIRouter(tags=["sources"])


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
    current = workspace.environment_source_by_id(
        session, environment_id, source_id, "metadata"
    )
    _guard_payload_or_source_profile(request, payload, current)
    try:
        source = workspace.update_metadata_source(
            session,
            environment_id,
            source_id,
            uri=payload.uri,
            label=payload.label,
            enabled=payload.enabled,
            source_config=payload.source_config,
            storage=_storage_payload(payload.storage),
        )
    except StorageConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if source is None:
        raise HTTPException(status_code=404, detail="Metadata source not found")
    return workspace.source_to_dict(source)


@router.get("/environments/{environment_id}/metadata-sources/{source_id}/delete-impact", response_model=SourceDeleteImpactResponse)
def get_metadata_source_delete_impact(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    impact = workspace.metadata_source_delete_impact(session, environment_id, source_id)
    if impact is None:
        raise HTTPException(status_code=404, detail="Metadata source not found")
    return impact


@router.delete("/environments/{environment_id}/metadata-sources/{source_id}", status_code=204)
def delete_metadata_source(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    if not workspace.delete_metadata_source(session, environment_id, source_id):
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
    source = workspace.environment_source_by_id(session, environment_id, source_id, "metadata")
    if source is None:
        raise HTTPException(status_code=404, detail="Metadata source not found")
    _guard_source_profile(request, source)
    result = sources.validate_metadata_source(
        session, source, secret_store=secret_store
    )
    _resume_after_successful_validation(session, source.id, result)
    return result


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
    source = workspace.environment_source_by_id(session, environment_id, source_id, "metadata")
    if source is None:
        raise HTTPException(status_code=404, detail="Metadata source not found")
    _guard_source_profile(request, source)
    with sync.source_refresh_guard(source.id) as acquired:
        if not acquired:
            raise HTTPException(status_code=409, detail="Metadata source refresh is already running")
        try:
            ensure_metadata_materialization(
                session, source, force=True, secret_store=secret_store
            )
        except MetadataReadError:
            pass
    result = sync.source_sync_status(session, source)
    if (result.get("latest_job") or {}).get("status") == "succeeded":
        reset_observation(
            session,
            source.id,
            due_at=utc_now()
            + timedelta(seconds=source_check_interval_seconds(session)),
            pending_changes=False,
        )
        session.commit()
        result = sync.source_sync_status(session, source)
    return result


@router.patch("/environments/{environment_id}/log-sources/{source_id}", response_model=LogSourceRead)
def patch_log_source(
    environment_id: int,
    source_id: int,
    payload: SourceUpdate,
    request: Request,
    session: Session = Depends(get_session),
):
    current = workspace.environment_source_by_id(
        session, environment_id, source_id, "logs"
    )
    _guard_payload_or_source_profile(request, payload, current)
    try:
        path = workspace.update_log_source(
            session,
            environment_id,
            source_id,
            uri=payload.uri,
            label=payload.label,
            enabled=payload.enabled,
            source_config=payload.source_config,
            storage=_storage_payload(payload.storage),
            sync_schedule_enabled=payload.sync_schedule_enabled,
            sync_interval_minutes=payload.sync_interval_minutes,
        )
    except StorageConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if path is None:
        raise HTTPException(status_code=404, detail="Log source not found")
    return workspace.source_to_dict(path)


@router.get("/environments/{environment_id}/log-sources/{source_id}/delete-impact", response_model=SourceDeleteImpactResponse)
def get_log_source_delete_impact(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    impact = workspace.log_source_delete_impact(session, environment_id, source_id)
    if impact is None:
        raise HTTPException(status_code=404, detail="Log source not found")
    return impact


@router.delete("/environments/{environment_id}/log-sources/{source_id}", status_code=204)
def delete_log_source(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    path = workspace.environment_source_by_id(session, environment_id, source_id, "logs")
    if path is None:
        raise HTTPException(status_code=404, detail="Log source not found")
    with sync.source_refresh_guard(path.id) as acquired:
        if not acquired:
            raise HTTPException(status_code=409, detail="Log source refresh is already running; try deleting again when it finishes")
        if not workspace.delete_log_source(session, environment_id, source_id):
            raise HTTPException(status_code=404, detail="Log source not found")
        return Response(status_code=204)


@router.post("/environments/{environment_id}/log-sources/{source_id}/validate", response_model=SourceValidationResponse)
def validate_log_source(
    environment_id: int,
    source_id: int,
    request: Request,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    path = workspace.environment_source_by_id(session, environment_id, source_id, "logs")
    if path is None:
        raise HTTPException(status_code=404, detail="Log source not found")
    _guard_source_profile(request, path)
    result = sources.validate_log_source(
        session, path, secret_store=secret_store
    )
    _resume_after_successful_validation(session, path.id, result)
    return result


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
    path = workspace.environment_source_by_id(session, environment_id, source_id, "logs")
    if path is None:
        raise HTTPException(status_code=404, detail="Log source not found")
    _guard_source_profile(request, path)
    with sync.source_refresh_guard(path.id) as acquired:
        if not acquired:
            raise HTTPException(status_code=409, detail="Log source refresh is already running")
        return refresh_log_source_cache(
            session,
            path,
            sync_spec=_log_sync_spec(payload),
            secret_store=secret_store,
        )


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
    current = workspace.environment_source_by_id(
        session, environment_id, source_id, "code"
    )
    _guard_payload_or_source_profile(request, payload, current)
    try:
        source = workspace.update_code_artifact(
            session,
            environment_id,
            source_id,
            uri=payload.uri,
            label=payload.label,
            enabled=payload.enabled,
            source_config=payload.source_config,
            storage=_storage_payload(payload.storage),
        )
    except StorageConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if source is None:
        raise HTTPException(status_code=404, detail="Code artifact not found")
    return workspace.source_to_dict(source)


@router.delete("/environments/{environment_id}/code-artifacts/{source_id}", status_code=204)
def delete_code_artifact(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    if not workspace.delete_code_artifact(session, environment_id, source_id):
        raise HTTPException(status_code=404, detail="Code artifact not found")
    return Response(status_code=204)


@router.get("/environments/{environment_id}/code-artifacts/{source_id}/delete-impact", response_model=SourceDeleteImpactResponse)
def get_code_artifact_delete_impact(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    impact = workspace.code_artifact_delete_impact(session, environment_id, source_id)
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
    source = workspace.environment_source_by_id(session, environment_id, source_id, "code")
    if source is None:
        raise HTTPException(status_code=404, detail="Code artifact not found")
    _guard_source_profile(request, source)
    result = validate_code_artifact(
        session, source, secret_store=secret_store
    )
    _resume_after_successful_validation(session, source.id, result)
    return result


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
    source = workspace.environment_source_by_id(session, environment_id, source_id, "code")
    if source is None:
        raise HTTPException(status_code=404, detail="Code artifact not found")
    _guard_source_profile(request, source)
    with sync.source_refresh_guard(source.id) as acquired:
        if not acquired:
            raise HTTPException(status_code=409, detail="Code artifact refresh is already running")
        result = refresh_code_artifact(
            session, source, secret_store=secret_store
        )
    if (result.get("latest_job") or {}).get("status") == "succeeded":
        reset_observation(
            session,
            source.id,
            due_at=utc_now()
            + timedelta(seconds=source_check_interval_seconds(session)),
            pending_changes=False,
        )
        session.commit()
        result = sync.source_sync_status(session, source)
    return result


def _reject_non_log_schedule(payload: SourceUpdate) -> None:
    if payload.sync_schedule_enabled is not None or payload.sync_interval_minutes is not None:
        raise HTTPException(status_code=422, detail="Only Log sources support scheduled refresh")


def _resume_after_successful_validation(
    session: Session,
    source_id: int,
    result: dict,
) -> None:
    if result.get("status") not in {"ok", "warning"}:
        return
    resume_observation(
        session,
        source_id,
        due_at=utc_now()
        + timedelta(seconds=source_check_interval_seconds(session)),
    )
    session.commit()


def _retry_observation(
    session: Session,
    environment_id: int,
    source_id: int,
    source_kind: str,
    request: Request,
) -> dict:
    source = workspace.environment_source_by_id(
        session, environment_id, source_id, source_kind
    )
    if source is None:
        labels = {
            "metadata": "Metadata source",
            "logs": "Log source",
            "code": "Code artifact",
        }
        raise HTTPException(
            status_code=404,
            detail=f"{labels[source_kind]} not found",
        )
    _guard_source_profile(request, source)
    if not source.enabled:
        raise HTTPException(
            status_code=409,
            detail="Enable the source before retrying automatic checks",
        )
    try:
        retry_source_observation(session, source)
    except ObservationRetryUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return sync.source_sync_status(session, source)


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
