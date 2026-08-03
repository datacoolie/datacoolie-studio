from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from datacoolie_studio.api.v1.contracts.sources import (
    CodeArtifactRead,
    DatacoolieProjectSourceImportRequest,
    EnvironmentContextResponse,
    EnvironmentFreshnessResponse,
    LogSourceRead,
    MetadataSourceImportRequest,
    MetadataSourceRead,
    SourceCreate,
    SourceImportResponse,
    SourcesWorkspaceResponse,
)
from datacoolie_studio.api.v1.contracts.workspace import (
    AnalyticsUpgradeStatusResponse,
    EnvironmentCreate,
    EnvironmentRead,
    EnvironmentRename,
    ProjectReferenceMappingCreate,
    ProjectReferenceMappingRead,
    ProjectReferenceMappingUpdate,
    ProjectCreate,
    ProjectRead,
    ProjectRename,
    ProjectSummaryResponse,
    StudioDiagnosticsResponse,
    StudioCacheClearRequest,
    StudioCacheMaintenanceRequest,
    StudioCacheMutationResponse,
    StudioCacheStatusResponse,
    StudioSettingsResponse,
    StudioSettingsUpdateRequest,
    StudioPathInfo,
    StudioWorkspaceDatabaseMaintenanceRequest,
)
from datacoolie_studio.db.session import get_session
from datacoolie_studio.api.v1.routes.credentials import (
    get_credential_secret_store,
    require_loopback_client,
)
from datacoolie_studio.domains.credentials.store import CredentialSecretStore
from datacoolie_studio.domains.freshness.service import environment_context, environment_freshness
from datacoolie_studio.domains.sources.initialization import (
    queue_source_initialization_ids,
    run_source_initialization_jobs,
)
from datacoolie_studio.domains.cache_admin import service as cache_admin
from datacoolie_studio.domains.analytics_upgrade.service import (
    request_analytics_upgrade_retry,
    run_analytics_upgrade_once,
)
from datacoolie_studio.domains.studio_settings import service as studio_settings
from datacoolie_studio.domains.workspace import service as workspace
from datacoolie_studio.domains.storage.errors import (
    StorageConfigurationError,
    StorageError,
)

router = APIRouter(tags=["workspace"])


@router.get("/studio/settings", response_model=StudioSettingsResponse)
def get_studio_settings(session: Session = Depends(get_session)):
    return studio_settings.get_studio_settings(session)


@router.get("/studio/diagnostics", response_model=StudioDiagnosticsResponse)
def get_studio_diagnostics(session: Session = Depends(get_session)):
    return studio_settings.get_studio_diagnostics(session)


@router.post("/studio/workspace-database/compact", response_model=StudioPathInfo)
def compact_workspace_database(_payload: StudioWorkspaceDatabaseMaintenanceRequest):
    try:
        return studio_settings.compact_workspace_database()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/studio/cache", response_model=StudioCacheStatusResponse)
def get_studio_cache(session: Session = Depends(get_session)):
    return cache_admin.cache_status(session)


@router.post(
    "/studio/cache/analytics-upgrade/retry",
    response_model=AnalyticsUpgradeStatusResponse,
)
def retry_analytics_upgrade(
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    status = request_analytics_upgrade_retry(session)
    background_tasks.add_task(run_analytics_upgrade_once)
    return status


@router.post("/studio/cache/clear", response_model=StudioCacheMutationResponse)
def clear_studio_cache(payload: StudioCacheClearRequest, session: Session = Depends(get_session)):
    try:
        return cache_admin.clear_cache(
            session,
            scope=payload.scope,
            environment_id=payload.environment_id,
            features=set(payload.features),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/studio/cache/prune", response_model=StudioCacheMutationResponse)
def prune_studio_cache(_payload: StudioCacheMaintenanceRequest):
    return cache_admin.prune_cache()


@router.post("/studio/cache/compact", response_model=StudioCacheMutationResponse)
def compact_studio_cache(_payload: StudioCacheMaintenanceRequest):
    return cache_admin.compact_cache()


@router.patch("/studio/settings", response_model=StudioSettingsResponse)
def patch_studio_settings(payload: StudioSettingsUpdateRequest, session: Session = Depends(get_session)):
    try:
        changes = payload.model_dump(exclude_unset=True)
        return studio_settings.update_studio_settings(session, changes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects", response_model=list[ProjectRead])
def get_projects(session: Session = Depends(get_session)):
    return workspace.list_projects(session)


@router.get("/projects/summary", response_model=list[ProjectSummaryResponse])
def get_project_summaries(session: Session = Depends(get_session)):
    return workspace.list_project_summaries(session)


@router.post("/projects", response_model=ProjectRead)
def post_project(payload: ProjectCreate, session: Session = Depends(get_session)):
    try:
        return workspace.create_project(session, payload.name, payload.description)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/projects/{project_id}", response_model=ProjectRead)
def patch_project(
    project_id: int,
    payload: ProjectRename,
    session: Session = Depends(get_session),
):
    try:
        return workspace.rename_project(session, project_id, payload.name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/projects/{project_id}/reference-mappings",
    response_model=list[ProjectReferenceMappingRead],
)
def get_project_reference_mappings(project_id: int, session: Session = Depends(get_session)):
    try:
        return workspace.list_project_reference_mappings(session, project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/projects/{project_id}/reference-mappings",
    response_model=ProjectReferenceMappingRead,
)
def post_project_reference_mapping(
    project_id: int,
    payload: ProjectReferenceMappingCreate,
    session: Session = Depends(get_session),
):
    try:
        return workspace.create_project_reference_mapping(
            session,
            project_id,
            reference_type=payload.reference_type,
            reference_value=payload.reference_value,
            target_identifier_kind=payload.target_identifier_kind,
            target_value=payload.target_value,
            target_display_value=payload.target_display_value,
            note=payload.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch(
    "/projects/{project_id}/reference-mappings/{mapping_id}",
    response_model=ProjectReferenceMappingRead,
)
def patch_project_reference_mapping(
    project_id: int,
    mapping_id: int,
    payload: ProjectReferenceMappingUpdate,
    session: Session = Depends(get_session),
):
    try:
        updated = workspace.update_project_reference_mapping(
            session,
            project_id,
            mapping_id,
            payload.model_dump(exclude_unset=True),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Mapping not found")
    return updated


@router.delete("/projects/{project_id}/reference-mappings/{mapping_id}", status_code=204)
def delete_project_reference_mapping(
    project_id: int,
    mapping_id: int,
    session: Session = Depends(get_session),
):
    try:
        deleted = workspace.delete_project_reference_mapping(session, project_id, mapping_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Mapping not found")
    return Response(status_code=204)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: int, session: Session = Depends(get_session)):
    if not workspace.delete_project(session, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return Response(status_code=204)


@router.get("/projects/{project_id}/environments", response_model=list[EnvironmentRead])
def get_environments(project_id: int, session: Session = Depends(get_session)):
    return workspace.list_environments(session, project_id)


@router.post("/projects/{project_id}/environments", response_model=EnvironmentRead)
def post_environment(project_id: int, payload: EnvironmentCreate, session: Session = Depends(get_session)):
    try:
        return workspace.create_environment(session, project_id, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/environments/{environment_id}", response_model=EnvironmentRead)
def patch_environment(
    environment_id: int,
    payload: EnvironmentRename,
    session: Session = Depends(get_session),
):
    try:
        return workspace.rename_environment(session, environment_id, payload.name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/environments/{environment_id}", status_code=204)
def delete_environment(environment_id: int, session: Session = Depends(get_session)):
    if not workspace.delete_environment(session, environment_id):
        raise HTTPException(status_code=404, detail="Environment not found")
    return Response(status_code=204)


@router.get("/environments/{environment_id}/freshness", response_model=EnvironmentFreshnessResponse)
def get_environment_freshness(environment_id: int, session: Session = Depends(get_session)):
    try:
        return environment_freshness(session, environment_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/environments/{environment_id}/context", response_model=EnvironmentContextResponse)
def get_environment_context(environment_id: int, session: Session = Depends(get_session)):
    try:
        return environment_context(session, environment_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/environments/{environment_id}/sources/workspace",
    response_model=SourcesWorkspaceResponse,
)
def get_sources_workspace(environment_id: int, session: Session = Depends(get_session)):
    try:
        return workspace.sources_workspace(session, environment_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/environments/{environment_id}/metadata-sources", response_model=list[MetadataSourceRead])
def get_metadata_sources(environment_id: int, session: Session = Depends(get_session)):
    return workspace.list_metadata_sources_with_validation(session, environment_id)


@router.post("/environments/{environment_id}/metadata-sources", response_model=MetadataSourceRead)
def post_metadata_source(
    environment_id: int,
    payload: SourceCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    _guard_profile_mutation(request, payload.storage)
    try:
        source = workspace.add_metadata_source(
            session,
            environment_id,
            payload.uri,
            payload.label,
            payload.enabled,
            payload.source_config,
            _storage_payload(payload.storage),
        )
        job_ids = queue_source_initialization_ids(session, [source.id])
        if job_ids:
            background_tasks.add_task(run_source_initialization_jobs, job_ids)
        return workspace.source_to_dict(source)
    except StorageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/environments/{environment_id}/metadata-sources/import", response_model=SourceImportResponse)
def import_metadata_sources(
    environment_id: int,
    payload: MetadataSourceImportRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    _guard_profile_mutation(request, payload.storage)
    try:
        result = workspace.import_metadata_sources(
            session,
            environment_id,
            uri=payload.uri,
            label=payload.label,
            enabled=payload.enabled,
            storage=_storage_payload(payload.storage),
        )
        source_ids = [
            int(item["id"])
            for item in [*result["created"], *result["existing"]]
        ]
        job_ids = queue_source_initialization_ids(session, source_ids)
        result["summary"]["initialization_queued"] = len(job_ids)
        if job_ids:
            background_tasks.add_task(run_source_initialization_jobs, job_ids)
        return result
    except StorageConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/environments/{environment_id}/datacoolie-project-sources", response_model=SourceImportResponse)
def import_datacoolie_project_sources(
    environment_id: int,
    payload: DatacoolieProjectSourceImportRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    _guard_profile_mutation(request, payload.storage)
    try:
        result = workspace.import_datacoolie_project_sources(
            session,
            environment_id,
            project_uri=payload.project_uri,
            metadata_subpath=payload.metadata_subpath,
            code_subpath=payload.code_subpath,
            metadata_uri=payload.metadata_uri,
            code_uri=payload.code_uri,
            include_metadata=payload.include_metadata,
            include_code=payload.include_code,
            enabled=payload.enabled,
            storage=_storage_payload(payload.storage),
            secret_store=secret_store,
        )
        source_ids = [
            int(item["id"])
            for item in [*result["created"], *result["existing"]]
        ]
        job_ids = queue_source_initialization_ids(session, source_ids)
        result["summary"].pop("auto_validated", None)
        result["summary"].pop("auto_synced", None)
        result["summary"]["initialization_queued"] = len(job_ids)
        if job_ids:
            background_tasks.add_task(run_source_initialization_jobs, job_ids)
        return result
    except StorageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/environments/{environment_id}/log-sources", response_model=list[LogSourceRead])
def get_log_sources(environment_id: int, session: Session = Depends(get_session)):
    return workspace.list_log_sources_with_validation(session, environment_id)


@router.post("/environments/{environment_id}/log-sources", response_model=LogSourceRead)
def post_log_source(
    environment_id: int,
    payload: SourceCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    _guard_profile_mutation(request, payload.storage)
    try:
        source = workspace.add_log_source(
            session,
            environment_id,
            payload.uri,
            payload.label,
            payload.enabled,
            payload.source_config,
            _storage_payload(payload.storage),
            secret_store=secret_store,
        )
        job_ids = queue_source_initialization_ids(session, [source.id])
        if job_ids:
            background_tasks.add_task(run_source_initialization_jobs, job_ids)
        return workspace.source_to_dict(source)
    except StorageConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/environments/{environment_id}/code-artifacts", response_model=list[CodeArtifactRead])
def get_code_artifacts(environment_id: int, session: Session = Depends(get_session)):
    return workspace.list_code_artifacts_with_validation(session, environment_id)


@router.post("/environments/{environment_id}/code-artifacts", response_model=CodeArtifactRead)
def post_code_artifact(
    environment_id: int,
    payload: SourceCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    _guard_profile_mutation(request, payload.storage)
    try:
        artifact = workspace.add_code_artifact(
            session,
            environment_id,
            payload.uri,
            payload.label,
            payload.enabled,
            payload.source_config,
            _storage_payload(payload.storage),
        )
        job_ids = queue_source_initialization_ids(session, [artifact.id])
        if job_ids:
            background_tasks.add_task(run_source_initialization_jobs, job_ids)
        return workspace.source_to_dict(artifact)
    except StorageConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _storage_payload(storage) -> dict | None:
    return storage.model_dump() if storage is not None else None


def _guard_profile_mutation(request: Request, storage) -> None:
    if storage is not None and storage.auth_mode == "credential_profile":
        require_loopback_client(request)
