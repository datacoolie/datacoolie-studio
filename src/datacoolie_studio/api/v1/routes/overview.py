from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from datacoolie_studio.api.v1.contracts.overview import EnvironmentOverviewResponse
from datacoolie_studio.db.session import get_session
from datacoolie_studio.domains.overview.service import load_environment_overview


router = APIRouter(tags=["overview"])


@router.get("/environments/{environment_id}/overview", response_model=EnvironmentOverviewResponse)
def get_environment_overview(environment_id: int, session: Session = Depends(get_session)):
    try:
        return load_environment_overview(session, environment_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
