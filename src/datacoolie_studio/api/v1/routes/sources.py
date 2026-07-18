from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from datacoolie_studio.api.v1.schemas import (
    CodeArtifactRead,
    LogSourceRead,
    MetadataSourceRead,
    SourceDeleteImpactResponse,
    SourceSyncStatusResponse,
    SourceUpdate,
    SourceValidationResponse,
)
from datacoolie_studio.domains.code_artifacts.service import refresh_code_artifact, validate_code_artifact
from datacoolie_studio.db.session import get_session
from datacoolie_studio.domains.logs.cache import refresh_log_source_cache
from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.domains.metadata.service import ensure_metadata_snapshot
from datacoolie_studio.domains.sources import service as sources
from datacoolie_studio.domains.sync import service as sync
from datacoolie_studio.domains.workspace import service as workspace

router = APIRouter(tags=["sources"])


@router.patch("/environments/{environment_id}/metadata-sources/{source_id}", response_model=MetadataSourceRead)
def patch_metadata_source(environment_id: int, source_id: int, payload: SourceUpdate, session: Session = Depends(get_session)):
    _reject_non_log_schedule(payload)
    source = workspace.update_metadata_source(
        session,
        environment_id,
        source_id,
        uri=payload.uri,
        label=payload.label,
        enabled=payload.enabled,
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Metadata source not found")
    return source


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
def validate_metadata_source(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    source = workspace.environment_source_by_id(session, environment_id, source_id, "metadata")
    if source is None:
        raise HTTPException(status_code=404, detail="Metadata source not found")
    return sources.validate_metadata_source(session, source)


@router.get("/environments/{environment_id}/metadata-sources/{source_id}/sync-status", response_model=SourceSyncStatusResponse)
def get_metadata_source_sync_status(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    source = workspace.environment_source_by_id(session, environment_id, source_id, "metadata")
    if source is None:
        raise HTTPException(status_code=404, detail="Metadata source not found")
    return sync.source_sync_status(session, source)


@router.post("/environments/{environment_id}/metadata-sources/{source_id}/refresh", response_model=SourceSyncStatusResponse)
def refresh_metadata_source(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    source = workspace.environment_source_by_id(session, environment_id, source_id, "metadata")
    if source is None:
        raise HTTPException(status_code=404, detail="Metadata source not found")
    with sync.source_refresh_guard(source.id) as acquired:
        if not acquired:
            raise HTTPException(status_code=409, detail="Metadata source refresh is already running")
        try:
            ensure_metadata_snapshot(session, source, force=True)
        except MetadataReadError:
            pass
    return sync.source_sync_status(session, source)


@router.patch("/environments/{environment_id}/log-sources/{source_id}", response_model=LogSourceRead)
def patch_log_source(environment_id: int, source_id: int, payload: SourceUpdate, session: Session = Depends(get_session)):
    path = workspace.update_log_source(
        session,
        environment_id,
        source_id,
        uri=payload.uri,
        label=payload.label,
        enabled=payload.enabled,
        source_config=payload.source_config,
        sync_schedule_enabled=payload.sync_schedule_enabled,
        sync_interval_minutes=payload.sync_interval_minutes,
    )
    if path is None:
        raise HTTPException(status_code=404, detail="Log source not found")
    return path


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
def validate_log_source(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    path = workspace.environment_source_by_id(session, environment_id, source_id, "logs")
    if path is None:
        raise HTTPException(status_code=404, detail="Log source not found")
    return sources.validate_log_source(session, path)


@router.get("/environments/{environment_id}/log-sources/{source_id}/sync-status", response_model=SourceSyncStatusResponse)
def get_log_source_sync_status(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    path = workspace.environment_source_by_id(session, environment_id, source_id, "logs")
    if path is None:
        raise HTTPException(status_code=404, detail="Log source not found")
    return sync.source_sync_status(session, path)


@router.post("/environments/{environment_id}/log-sources/{source_id}/refresh", response_model=SourceSyncStatusResponse)
def refresh_log_source(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    path = workspace.environment_source_by_id(session, environment_id, source_id, "logs")
    if path is None:
        raise HTTPException(status_code=404, detail="Log source not found")
    with sync.source_refresh_guard(path.id) as acquired:
        if not acquired:
            raise HTTPException(status_code=409, detail="Log source refresh is already running")
        return refresh_log_source_cache(session, path)


@router.patch("/environments/{environment_id}/code-artifacts/{source_id}", response_model=CodeArtifactRead)
def patch_code_artifact(environment_id: int, source_id: int, payload: SourceUpdate, session: Session = Depends(get_session)):
    _reject_non_log_schedule(payload)
    source = workspace.update_code_artifact(
        session,
        environment_id,
        source_id,
        uri=payload.uri,
        label=payload.label,
        enabled=payload.enabled,
        source_config=payload.source_config,
    )
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
def validate_code_artifact_source(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    source = workspace.environment_source_by_id(session, environment_id, source_id, "code")
    if source is None:
        raise HTTPException(status_code=404, detail="Code artifact not found")
    return validate_code_artifact(session, source)


@router.get("/environments/{environment_id}/code-artifacts/{source_id}/sync-status", response_model=SourceSyncStatusResponse)
def get_code_artifact_sync_status(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    source = workspace.environment_source_by_id(session, environment_id, source_id, "code")
    if source is None:
        raise HTTPException(status_code=404, detail="Code artifact not found")
    return sync.source_sync_status(session, source)


@router.post("/environments/{environment_id}/code-artifacts/{source_id}/refresh", response_model=SourceSyncStatusResponse)
def refresh_code_artifact_source(environment_id: int, source_id: int, session: Session = Depends(get_session)):
    source = workspace.environment_source_by_id(session, environment_id, source_id, "code")
    if source is None:
        raise HTTPException(status_code=404, detail="Code artifact not found")
    with sync.source_refresh_guard(source.id) as acquired:
        if not acquired:
            raise HTTPException(status_code=409, detail="Code artifact refresh is already running")
        return refresh_code_artifact(session, source)


def _reject_non_log_schedule(payload: SourceUpdate) -> None:
    if payload.sync_schedule_enabled is not None or payload.sync_interval_minutes is not None:
        raise HTTPException(status_code=422, detail="Only Log sources support scheduled refresh")
