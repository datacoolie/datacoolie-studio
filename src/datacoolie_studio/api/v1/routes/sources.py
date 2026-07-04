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
from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.db.session import get_session
from datacoolie_studio.domains.logs.cache import refresh_log_source_cache
from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.domains.metadata.service import ensure_metadata_snapshot
from datacoolie_studio.domains.sources import service as sources
from datacoolie_studio.domains.sync import service as sync
from datacoolie_studio.domains.workspace import service as workspace

router = APIRouter(tags=["sources"])


@router.patch("/metadata-sources/{source_id}", response_model=MetadataSourceRead)
def patch_metadata_source(source_id: int, payload: SourceUpdate, session: Session = Depends(get_session)):
    source = workspace.update_metadata_source(
        session,
        source_id,
        uri=payload.uri,
        label=payload.label,
        enabled=payload.enabled,
        sync_schedule_enabled=payload.sync_schedule_enabled,
        sync_interval_minutes=payload.sync_interval_minutes,
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Metadata source not found")
    return source


@router.get("/metadata-sources/{source_id}/delete-impact", response_model=SourceDeleteImpactResponse)
def get_metadata_source_delete_impact(source_id: int, session: Session = Depends(get_session)):
    impact = workspace.metadata_source_delete_impact(session, source_id)
    if impact is None:
        raise HTTPException(status_code=404, detail="Metadata source not found")
    return impact


@router.delete("/metadata-sources/{source_id}", status_code=204)
def delete_metadata_source(source_id: int, session: Session = Depends(get_session)):
    if not workspace.delete_metadata_source(session, source_id):
        raise HTTPException(status_code=404, detail="Metadata source not found")
    return Response(status_code=204)


@router.post("/metadata-sources/{source_id}/validate", response_model=SourceValidationResponse)
def validate_metadata_source(source_id: int, session: Session = Depends(get_session)):
    source = session.get(EnvironmentSource, source_id)
    if source is None or source.source_kind != "metadata":
        raise HTTPException(status_code=404, detail="Metadata source not found")
    return sources.validate_metadata_source(session, source)


@router.get("/metadata-sources/{source_id}/sync-status", response_model=SourceSyncStatusResponse)
def get_metadata_source_sync_status(source_id: int, session: Session = Depends(get_session)):
    source = session.get(EnvironmentSource, source_id)
    if source is None or source.source_kind != "metadata":
        raise HTTPException(status_code=404, detail="Metadata source not found")
    return sync.source_sync_status(session, source)


@router.post("/metadata-sources/{source_id}/refresh", response_model=SourceSyncStatusResponse)
def refresh_metadata_source(source_id: int, session: Session = Depends(get_session)):
    source = session.get(EnvironmentSource, source_id)
    if source is None or source.source_kind != "metadata":
        raise HTTPException(status_code=404, detail="Metadata source not found")
    try:
        ensure_metadata_snapshot(session, source, force=True)
    except MetadataReadError:
        pass
    return sync.source_sync_status(session, source)


@router.patch("/log-sources/{source_id}", response_model=LogSourceRead)
def patch_log_source(source_id: int, payload: SourceUpdate, session: Session = Depends(get_session)):
    path = workspace.update_log_source(
        session,
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


@router.delete("/log-sources/{source_id}", status_code=204)
def delete_log_source(source_id: int, session: Session = Depends(get_session)):
    if not workspace.delete_log_source(session, source_id):
        raise HTTPException(status_code=404, detail="Log source not found")
    return Response(status_code=204)


@router.post("/log-sources/{source_id}/validate", response_model=SourceValidationResponse)
def validate_log_source(source_id: int, session: Session = Depends(get_session)):
    path = session.get(EnvironmentSource, source_id)
    if path is None or path.source_kind != "logs":
        raise HTTPException(status_code=404, detail="Log source not found")
    return sources.validate_log_source(session, path)


@router.get("/log-sources/{source_id}/sync-status", response_model=SourceSyncStatusResponse)
def get_log_source_sync_status(source_id: int, session: Session = Depends(get_session)):
    path = session.get(EnvironmentSource, source_id)
    if path is None or path.source_kind != "logs":
        raise HTTPException(status_code=404, detail="Log source not found")
    return sync.source_sync_status(session, path)


@router.post("/log-sources/{source_id}/refresh", response_model=SourceSyncStatusResponse)
def refresh_log_source(source_id: int, session: Session = Depends(get_session)):
    path = session.get(EnvironmentSource, source_id)
    if path is None or path.source_kind != "logs":
        raise HTTPException(status_code=404, detail="Log source not found")
    return refresh_log_source_cache(session, path)


@router.patch("/code-artifacts/{source_id}", response_model=CodeArtifactRead)
def patch_code_artifact(source_id: int, payload: SourceUpdate, session: Session = Depends(get_session)):
    source = workspace.update_code_artifact(
        session,
        source_id,
        uri=payload.uri,
        label=payload.label,
        enabled=payload.enabled,
        source_config=payload.source_config,
        sync_schedule_enabled=payload.sync_schedule_enabled,
        sync_interval_minutes=payload.sync_interval_minutes,
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Code artifact not found")
    return workspace.source_to_dict(source)


@router.delete("/code-artifacts/{source_id}", status_code=204)
def delete_code_artifact(source_id: int, session: Session = Depends(get_session)):
    if not workspace.delete_code_artifact(session, source_id):
        raise HTTPException(status_code=404, detail="Code artifact not found")
    return Response(status_code=204)


@router.post("/code-artifacts/{source_id}/validate", response_model=SourceValidationResponse)
def validate_code_artifact_source(source_id: int, session: Session = Depends(get_session)):
    source = session.get(EnvironmentSource, source_id)
    if source is None or source.source_kind != "code":
        raise HTTPException(status_code=404, detail="Code artifact not found")
    return validate_code_artifact(session, source)


@router.get("/code-artifacts/{source_id}/sync-status", response_model=SourceSyncStatusResponse)
def get_code_artifact_sync_status(source_id: int, session: Session = Depends(get_session)):
    source = session.get(EnvironmentSource, source_id)
    if source is None or source.source_kind != "code":
        raise HTTPException(status_code=404, detail="Code artifact not found")
    return sync.source_sync_status(session, source)


@router.post("/code-artifacts/{source_id}/refresh", response_model=SourceSyncStatusResponse)
def refresh_code_artifact_source(source_id: int, session: Session = Depends(get_session)):
    source = session.get(EnvironmentSource, source_id)
    if source is None or source.source_kind != "code":
        raise HTTPException(status_code=404, detail="Code artifact not found")
    return refresh_code_artifact(session, source)
