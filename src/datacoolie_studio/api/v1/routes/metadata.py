from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from datacoolie_studio.api.v1.contracts.metadata import (
    MetadataBackupResponse,
    MetadataBackupRestoreRequest,
    MetadataEditorDocumentResponse,
    MetadataEditorWorkspaceResponse,
    MetadataEditorPayloadRequest,
    MetadataResponse,
    MetadataWriteAbandonRequest,
    MetadataWriteApplyRequest,
    MetadataWriteOperationResponse,
    MetadataWritePrepareRequest,
)
from datacoolie_studio.db.session import get_session
from datacoolie_studio.api.v1.dependencies import (
    get_credential_secret_store,
    require_loopback_client,
)
from datacoolie_studio.domains.credentials.store import CredentialSecretStore
from datacoolie_studio.domains.metadata.backups import (
    delete_backup,
    delete_environment_backups,
    list_environment_backups,
)
from datacoolie_studio.domains.metadata.drafts import (
    delete_environment_editor_draft,
    save_environment_editor_draft,
)
from datacoolie_studio.domains.metadata.write_executor import (
    abandon_environment_write,
    apply_environment_write,
    operation_response,
    prepare_environment_write,
)
from datacoolie_studio.domains.metadata.backup_application import (
    load_backup_editor_document,
    restore_backup_workspace,
)
from datacoolie_studio.domains.metadata.errors import (
    MetadataConflictError,
    MetadataValidationError,
)
from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.domains.metadata.service import (
    MetadataSourcesUnavailableError,
    load_environment_editor_workspace,
    load_environment_metadata,
)
from datacoolie_studio.domains.sources import repository as source_repository

router = APIRouter(tags=["metadata"])


@router.post(
    "/environments/{environment_id}/metadata-write-operations/prepare",
    response_model=MetadataWriteOperationResponse,
)
def prepare_environment_metadata_write(
    environment_id: int,
    payload: MetadataWritePrepareRequest,
    request: Request,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    require_loopback_client(request)
    sources = source_repository.list_sources(session, environment_id, "metadata")
    _guard_profile_sources(request, sources)
    try:
        operation = prepare_environment_write(
            session,
            environment_id,
            sources,
            payload.editor_document.model_dump(),
            payload.expected_revision,
            secret_store=secret_store,
        )
        return operation_response(session, operation)
    except MetadataConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MetadataValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.validation) from exc
    except MetadataReadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/environments/{environment_id}/metadata-write-operations/{operation_key}",
    response_model=MetadataWriteOperationResponse,
)
def get_environment_metadata_write_operation(
    environment_id: int,
    operation_key: str,
    session: Session = Depends(get_session),
):
    from datacoolie_studio.domains.metadata.write_operations import operation_for_key

    operation = operation_for_key(
        session,
        operation_key,
        environment_id=environment_id,
    )
    if operation is None:
        raise HTTPException(status_code=404, detail="Metadata write operation not found")
    return operation_response(session, operation)


@router.post(
    "/environments/{environment_id}/metadata-write-operations/{operation_key}/apply",
    response_model=MetadataWriteOperationResponse,
)
def apply_environment_metadata_write(
    environment_id: int,
    operation_key: str,
    payload: MetadataWriteApplyRequest,
    request: Request,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    require_loopback_client(request)
    try:
        operation = apply_environment_write(
            session,
            operation_key,
            environment_id=environment_id,
            confirm=payload.confirm_apply,
            secret_store=secret_store,
        )
        return operation_response(session, operation)
    except MetadataConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MetadataReadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/environments/{environment_id}/metadata-write-operations/{operation_key}/retry",
    response_model=MetadataWriteOperationResponse,
)
def retry_environment_metadata_write(
    environment_id: int,
    operation_key: str,
    payload: MetadataWriteApplyRequest,
    request: Request,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    return apply_environment_metadata_write(
        environment_id,
        operation_key,
        payload,
        request,
        session,
        secret_store,
    )


@router.post(
    "/environments/{environment_id}/metadata-write-operations/{operation_key}/abandon",
    response_model=MetadataWriteOperationResponse,
)
def abandon_environment_metadata_write(
    environment_id: int,
    operation_key: str,
    payload: MetadataWriteAbandonRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    require_loopback_client(request)
    if not payload.confirm_abandon:
        raise HTTPException(status_code=422, detail="Abandoning metadata requires confirmation")
    try:
        operation = abandon_environment_write(
            session,
            operation_key,
            environment_id=environment_id,
        )
        return operation_response(session, operation)
    except MetadataConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/environments/{environment_id}/metadata", response_model=MetadataResponse)
def get_metadata(
    environment_id: int,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    sources = source_repository.list_sources(session, environment_id, "metadata")
    try:
        return _public_metadata(
            load_environment_metadata(
                session, sources, secret_store=secret_store
            )
        )
    except MetadataSourcesUnavailableError as exc:
        raise HTTPException(status_code=422, detail=_metadata_sources_error_detail(exc)) from exc


@router.get("/environments/{environment_id}/metadata-editor-workspace", response_model=MetadataEditorWorkspaceResponse)
def get_environment_metadata_editor_workspace(
    environment_id: int,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    sources = source_repository.list_sources(session, environment_id, "metadata")
    try:
        return load_environment_editor_workspace(
            session,
            environment_id,
            sources,
            secret_store=secret_store,
        )
    except MetadataSourcesUnavailableError as exc:
        raise HTTPException(status_code=422, detail=_metadata_sources_error_detail(exc)) from exc
    except MetadataReadError as exc:
        raise HTTPException(status_code=422, detail={"code": "metadata_read_failed", "message": str(exc)}) from exc


@router.put("/environments/{environment_id}/metadata-editor-document/draft", response_model=MetadataEditorDocumentResponse)
def put_environment_metadata_editor_draft(
    environment_id: int,
    payload: MetadataEditorPayloadRequest,
    request: Request,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    sources = source_repository.list_sources(session, environment_id, "metadata")
    _guard_profile_sources(request, sources)
    document = payload.model_dump()
    try:
        return save_environment_editor_draft(
            session,
            environment_id,
            document,
            document.get("source", {}).get("revision") or {},
            sources=sources,
            secret_store=secret_store,
        )
    except MetadataConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MetadataReadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/environments/{environment_id}/metadata-editor-document/draft", status_code=204)
def discard_environment_metadata_editor_draft(environment_id: int, session: Session = Depends(get_session)):
    source_repository.list_sources(session, environment_id, "metadata")
    delete_environment_editor_draft(session, environment_id)
    return Response(status_code=204)


@router.get("/environments/{environment_id}/metadata-backups", response_model=list[MetadataBackupResponse])
def get_environment_metadata_backups(environment_id: int, session: Session = Depends(get_session)):
    source_repository.list_sources(session, environment_id, "metadata")
    return list_environment_backups(session, environment_id)


@router.delete("/environments/{environment_id}/metadata-backups", status_code=204)
def delete_environment_metadata_backups(
    environment_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    require_loopback_client(request)
    source_repository.list_sources(session, environment_id, "metadata")
    delete_environment_backups(session, environment_id)
    return Response(status_code=204)


@router.get(
    "/environments/{environment_id}/metadata-backups/{backup_id}/editor-document",
    response_model=MetadataEditorDocumentResponse,
)
def get_metadata_backup_editor_document(
    environment_id: int,
    backup_id: int,
    session: Session = Depends(get_session),
):
    try:
        return load_backup_editor_document(
            session, backup_id, environment_id=environment_id
        )
    except MetadataReadError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/environments/{environment_id}/metadata-backups/{backup_id}/restore",
    response_model=MetadataEditorWorkspaceResponse,
)
def restore_metadata_backup(
    environment_id: int,
    backup_id: int,
    payload: MetadataBackupRestoreRequest,
    request: Request,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    require_loopback_client(request)
    try:
        return restore_backup_workspace(
            session,
            backup_id,
            payload.expected_revision,
            payload.confirm_restore,
            secret_store=secret_store,
            environment_id=environment_id,
        )
    except MetadataConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MetadataValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.validation) from exc
    except MetadataReadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete(
    "/environments/{environment_id}/metadata-backups/{backup_id}",
    status_code=204,
)
def delete_metadata_backup(
    environment_id: int,
    backup_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    require_loopback_client(request)
    if not delete_backup(session, backup_id, environment_id=environment_id):
        raise HTTPException(status_code=404, detail="Metadata backup not found")
    return Response(status_code=204)


def _public_metadata(metadata: dict) -> dict:
    return {key: value for key, value in metadata.items() if not key.startswith("_")}


def _metadata_sources_error_detail(exc: MetadataSourcesUnavailableError) -> dict:
    return {
        "code": exc.code,
        "message": str(exc),
        "source_ids": exc.source_ids,
    }


def _guard_profile_sources(
    request: Request, sources: list
) -> None:
    if any(
        source.storage_auth_mode == "credential_profile"
        for source in sources
    ):
        require_loopback_client(request)
