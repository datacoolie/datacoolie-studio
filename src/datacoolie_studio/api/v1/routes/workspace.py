from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from datacoolie_studio.api.v1.schemas import (
    CodeArtifactRead,
    DatacoolieProjectSourceImportRequest,
    EnvironmentCreate,
    EnvironmentFreshnessResponse,
    EnvironmentRead,
    LogSourceRead,
    MetadataSourceImportRequest,
    MetadataSourceRead,
    ProjectReferenceMappingCreate,
    ProjectReferenceMappingRead,
    ProjectReferenceMappingUpdate,
    ProjectCreate,
    ProjectRead,
    ProjectSummaryResponse,
    SourceCreate,
    SourceImportResponse,
    StudioSettingsResponse,
    StudioSettingsUpdateRequest,
)
from datacoolie_studio.db.session import get_session
from datacoolie_studio.domains.freshness.service import environment_freshness
from datacoolie_studio.domains.studio_settings import service as studio_settings
from datacoolie_studio.domains.workspace import service as workspace

router = APIRouter(tags=["workspace"])


@router.get("/studio/settings", response_model=StudioSettingsResponse)
def get_studio_settings(session: Session = Depends(get_session)):
    return studio_settings.get_studio_settings(session)


@router.patch("/studio/settings", response_model=StudioSettingsResponse)
def patch_studio_settings(payload: StudioSettingsUpdateRequest, session: Session = Depends(get_session)):
    try:
        changes = payload.model_dump(exclude_unset=True)
        if "timezone" in changes:
            studio_settings.set_studio_timezone(session, changes["timezone"])
        if "source_check_interval_seconds" in changes:
            interval_seconds = changes["source_check_interval_seconds"]
            if interval_seconds is None:
                raise ValueError("Source check interval cannot be null")
            studio_settings.set_source_check_interval(session, interval_seconds)
        return studio_settings.get_studio_settings(session)
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
    return workspace.create_project(session, payload.name, payload.description)


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


@router.get("/environments/{environment_id}/metadata-sources", response_model=list[MetadataSourceRead])
def get_metadata_sources(environment_id: int, session: Session = Depends(get_session)):
    return workspace.list_metadata_sources_with_validation(session, environment_id)


@router.post("/environments/{environment_id}/metadata-sources", response_model=MetadataSourceRead)
def post_metadata_source(environment_id: int, payload: SourceCreate, session: Session = Depends(get_session)):
    return workspace.add_metadata_source(session, environment_id, payload.uri, payload.label, payload.enabled, payload.source_config)


@router.post("/environments/{environment_id}/metadata-sources/import", response_model=SourceImportResponse)
def import_metadata_sources(
    environment_id: int,
    payload: MetadataSourceImportRequest,
    session: Session = Depends(get_session),
):
    return workspace.import_metadata_sources(
        session,
        environment_id,
        uri=payload.uri,
        label=payload.label,
        enabled=payload.enabled,
    )


@router.post("/environments/{environment_id}/datacoolie-project-sources", response_model=SourceImportResponse)
def import_datacoolie_project_sources(
    environment_id: int,
    payload: DatacoolieProjectSourceImportRequest,
    session: Session = Depends(get_session),
):
    return workspace.import_datacoolie_project_sources(
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
    )


@router.get("/environments/{environment_id}/log-sources", response_model=list[LogSourceRead])
def get_log_sources(environment_id: int, session: Session = Depends(get_session)):
    return workspace.list_log_sources_with_validation(session, environment_id)


@router.post("/environments/{environment_id}/log-sources", response_model=LogSourceRead)
def post_log_source(environment_id: int, payload: SourceCreate, session: Session = Depends(get_session)):
    source = workspace.add_log_source(session, environment_id, payload.uri, payload.label, payload.enabled, payload.source_config)
    return workspace.source_to_dict(source)


@router.get("/environments/{environment_id}/code-artifacts", response_model=list[CodeArtifactRead])
def get_code_artifacts(environment_id: int, session: Session = Depends(get_session)):
    return workspace.list_code_artifacts_with_validation(session, environment_id)


@router.post("/environments/{environment_id}/code-artifacts", response_model=CodeArtifactRead)
def post_code_artifact(environment_id: int, payload: SourceCreate, session: Session = Depends(get_session)):
    artifact = workspace.add_code_artifact(
        session,
        environment_id,
        payload.uri,
        payload.label,
        payload.enabled,
        payload.source_config,
    )
    return workspace.source_to_dict(artifact)
