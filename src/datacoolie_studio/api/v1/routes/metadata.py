from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from datacoolie_studio.api.v1.schemas import (
    MetadataBackupResponse,
    MetadataBackupRestoreRequest,
    MetadataEditorDocumentResponse,
    MetadataEditorSaveRequest,
    MetadataEditorValidationRequest,
    MetadataEditorValidationResponse,
    MetadataResponse,
)
from datacoolie_studio.db.session import get_session
from datacoolie_studio.domains.metadata.editor import (
    MetadataConflictError,
    MetadataValidationError,
    delete_backup,
    delete_environment_backups,
    delete_environment_editor_draft,
    list_environment_backups,
    load_environment_editor_draft,
    load_backup_editor_document,
    save_environment_editor_document,
    save_environment_editor_draft,
    restore_backup,
    validate_editor_document,
)
from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.domains.metadata.service import (
    ensure_metadata_snapshot,
    load_environment_editor_document,
    load_environment_metadata,
)
from datacoolie_studio.domains.workspace import service as workspace
from datacoolie_studio.db.models import EnvironmentSource

router = APIRouter(tags=["metadata"])


@router.get("/environments/{environment_id}/metadata", response_model=MetadataResponse)
def get_metadata(environment_id: int, session: Session = Depends(get_session)):
    sources = workspace.list_metadata_sources(session, environment_id)
    return _public_metadata(load_environment_metadata(session, sources))


@router.get("/environments/{environment_id}/metadata-editor-document", response_model=MetadataEditorDocumentResponse)
def get_environment_metadata_editor_document(environment_id: int, session: Session = Depends(get_session)):
    sources = workspace.list_metadata_sources(session, environment_id)
    try:
        return load_environment_editor_document(session, sources)
    except MetadataReadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/environments/{environment_id}/metadata-editor-document/validate", response_model=MetadataEditorValidationResponse)
def validate_environment_metadata_editor_document(environment_id: int, payload: MetadataEditorValidationRequest, session: Session = Depends(get_session)):
    workspace.list_metadata_sources(session, environment_id)
    return validate_editor_document(payload.model_dump())


@router.get("/environments/{environment_id}/metadata-editor-document/draft", response_model=MetadataEditorDocumentResponse | None)
def get_environment_metadata_editor_draft(environment_id: int, session: Session = Depends(get_session)):
    workspace.list_metadata_sources(session, environment_id)
    return load_environment_editor_draft(session, environment_id)


@router.put("/environments/{environment_id}/metadata-editor-document/draft", response_model=MetadataEditorDocumentResponse)
def put_environment_metadata_editor_draft(environment_id: int, payload: MetadataEditorValidationRequest, session: Session = Depends(get_session)):
    workspace.list_metadata_sources(session, environment_id)
    document = payload.model_dump()
    try:
        return save_environment_editor_draft(
            session,
            environment_id,
            document,
            document.get("source", {}).get("revision") or {},
        )
    except MetadataConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MetadataReadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/environments/{environment_id}/metadata-editor-document/draft", status_code=204)
def discard_environment_metadata_editor_draft(environment_id: int, session: Session = Depends(get_session)):
    workspace.list_metadata_sources(session, environment_id)
    delete_environment_editor_draft(session, environment_id)
    return Response(status_code=204)


@router.put("/environments/{environment_id}/metadata-editor-document", response_model=MetadataEditorDocumentResponse)
def put_environment_metadata_editor_document(environment_id: int, payload: MetadataEditorSaveRequest, session: Session = Depends(get_session)):
    sources = workspace.list_metadata_sources(session, environment_id)
    try:
        saved = save_environment_editor_document(
            session,
            environment_id,
            sources,
            payload.editor_document.model_dump(),
            payload.expected_revision,
            payload.confirm_overwrite,
        )
        return saved
    except MetadataConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MetadataValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.validation) from exc
    except MetadataReadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/environments/{environment_id}/metadata-backups", response_model=list[MetadataBackupResponse])
def get_environment_metadata_backups(environment_id: int, session: Session = Depends(get_session)):
    workspace.list_metadata_sources(session, environment_id)
    return list_environment_backups(session, environment_id)


@router.delete("/environments/{environment_id}/metadata-backups", status_code=204)
def delete_environment_metadata_backups(environment_id: int, session: Session = Depends(get_session)):
    workspace.list_metadata_sources(session, environment_id)
    delete_environment_backups(session, environment_id)
    return Response(status_code=204)


@router.get("/metadata-backups/{backup_id}/editor-document", response_model=MetadataEditorDocumentResponse)
def get_metadata_backup_editor_document(backup_id: int, session: Session = Depends(get_session)):
    try:
        return load_backup_editor_document(session, backup_id)
    except MetadataReadError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/metadata-backups/{backup_id}/restore", response_model=MetadataEditorDocumentResponse)
def restore_metadata_backup(backup_id: int, payload: MetadataBackupRestoreRequest, session: Session = Depends(get_session)):
    try:
        restored = restore_backup(session, backup_id, payload.expected_revision, payload.confirm_restore)
        source = _metadata_source(session, restored["source"]["source_id"])
        ensure_metadata_snapshot(session, source, force=True)
        return restored
    except MetadataConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MetadataValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.validation) from exc
    except MetadataReadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/metadata-backups/{backup_id}", status_code=204)
def delete_metadata_backup(backup_id: int, session: Session = Depends(get_session)):
    if not delete_backup(session, backup_id):
        raise HTTPException(status_code=404, detail="Metadata backup not found")
    return Response(status_code=204)


def _public_metadata(metadata: dict) -> dict:
    return {key: value for key, value in metadata.items() if not key.startswith("_")}


def _metadata_source(session: Session, source_id: int) -> EnvironmentSource:
    source = session.get(EnvironmentSource, source_id)
    if source is None or source.source_kind != "metadata":
        raise HTTPException(status_code=404, detail="Metadata source not found")
    return source
