from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from datacoolie_studio.api.v1.schemas import LineageResponse
from datacoolie_studio.db.session import get_session
from datacoolie_studio.domains.lineage.service import load_or_build_lineage
from datacoolie_studio.domains.metadata.service import load_environment_metadata
from datacoolie_studio.domains.workspace import service as workspace

router = APIRouter(tags=["lineage"])


@router.get("/environments/{environment_id}/lineage", response_model=LineageResponse)
def get_lineage(environment_id: int, session: Session = Depends(get_session)):
    sources = workspace.list_metadata_sources(session, environment_id)
    code_artifacts = workspace.list_code_artifacts(session, environment_id)
    metadata = load_environment_metadata(session, sources)
    return load_or_build_lineage(session, metadata, environment_id, code_artifacts)
