from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from datacoolie_studio.api.v1.contracts.lineage import LineageResponse
from datacoolie_studio.db.session import get_session
from datacoolie_studio.domains.lineage.service import (
    draft_lineage_graph_etag,
    lineage_graph_etag,
    load_or_build_lineage_graph,
    project_lineage_graph,
)

router = APIRouter(tags=["lineage"])


@router.get("/environments/{environment_id}/lineage", response_model=LineageResponse)
def get_lineage(
    environment_id: int,
    request: Request,
    response: Response,
    draft: bool = Query(False, description="Compose the current Studio draft without publishing it"),
    session: Session = Depends(get_session),
):
    etag = (
        draft_lineage_graph_etag(session, environment_id)
        if draft
        else lineage_graph_etag(session, environment_id)
    )
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "private, must-revalidate"})
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, must-revalidate"
    return project_lineage_graph(
        load_or_build_lineage_graph(
            session,
            environment_id,
            include_draft=draft,
        )
    )
