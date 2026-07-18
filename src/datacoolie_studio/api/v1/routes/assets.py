from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from datacoolie_studio.api.v1.schemas import (
    AssetDetailResponse,
    AssetInventoryResponse,
    AssetReferenceDetailResponse,
    AssetReferenceListResponse,
    AssetSourceResponse,
    ReferenceOccurrenceSourceResponse,
)
from datacoolie_studio.db.session import get_session
from datacoolie_studio.domains.assets.service import (
    get_environment_asset,
    get_environment_asset_source,
    get_environment_asset_reference,
    get_reference_occurrence_source,
    list_environment_asset_references,
    list_environment_assets,
    ASSETS_PROJECTOR_VERSION,
)
from datacoolie_studio.domains.lineage.service import lineage_input_fingerprint
from datacoolie_studio.domains.read_models.cache import fingerprint

router = APIRouter(tags=["assets"])


@router.get("/environments/{environment_id}/assets", response_model=AssetInventoryResponse)
def get_assets(
    environment_id: int,
    request: Request,
    response: Response,
    q: str | None = None,
    connection: str | None = None,
    format: str | None = None,
    asset_type: str | None = None,
    role: str | None = None,
    attention_state: str | None = Query(None, pattern="^(with_attention|clean)$"),
    scope: str | None = Query(None, pattern="^(entry|transit|exit|isolated)$"),
    sort_by: str = Query("display_name", pattern="^(display_name|asset_type|connection_name|upstream_count|downstream_count|attention_count)$"),
    sort_dir: str = Query("asc", pattern="^(asc|desc)$"),
    session: Session = Depends(get_session),
):
    parameters = {
        "q": q, "connection": connection, "format": format,
        "asset_type": asset_type, "role": role, "attention_state": attention_state, "scope": scope,
        "sort_by": sort_by, "sort_dir": sort_dir,
    }
    etag, input_fingerprint = _assets_etag(session, environment_id, "inventory", parameters)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=_cache_headers(etag))
    try:
        payload = list_environment_assets(
            session, environment_id, query=q, connection=connection,
            format_name=format, asset_type=asset_type, role=role, attention_state=attention_state,
            scope=scope,
            sort_by=sort_by, sort_dir=sort_dir, input_fingerprint=input_fingerprint,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response.headers.update(_cache_headers(etag))
    return payload


@router.get("/environments/{environment_id}/asset-references", response_model=AssetReferenceListResponse)
def get_asset_references(
    environment_id: int,
    request: Request,
    response: Response,
    q: str | None = None,
    reference_type: str | None = None,
    provenance: str | None = None,
    group_status: str | None = None,
    attention_state: str | None = Query(None, pattern="^(with_attention|clean)$"),
    sort_by: str = Query("display_name", pattern="^(display_name|reference_type|group_status|dependency_count|attention_count)$"),
    sort_dir: str = Query("asc", pattern="^(asc|desc)$"),
    session: Session = Depends(get_session),
):
    parameters = {
        "q": q, "reference_type": reference_type,
        "provenance": provenance, "group_status": group_status, "attention_state": attention_state,
        "sort_by": sort_by, "sort_dir": sort_dir,
    }
    etag, input_fingerprint = _assets_etag(session, environment_id, "references", parameters)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=_cache_headers(etag))
    payload = list_environment_asset_references(
        session, environment_id, query=q,
        reference_type=reference_type, provenance=provenance, group_status=group_status,
        attention_state=attention_state, sort_by=sort_by, sort_dir=sort_dir,
        input_fingerprint=input_fingerprint,
    )
    response.headers.update(_cache_headers(etag))
    return payload


@router.get(
    "/environments/{environment_id}/asset-references/{reference_id}",
    response_model=AssetReferenceDetailResponse,
)
def get_asset_reference(
    environment_id: int,
    reference_id: str,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    etag, input_fingerprint = _assets_etag(session, environment_id, "reference-detail", {"reference_id": reference_id})
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=_cache_headers(etag))
    payload = get_environment_asset_reference(
        session, environment_id, reference_id, input_fingerprint=input_fingerprint,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="Asset reference not found")
    response.headers.update(_cache_headers(etag))
    return payload


@router.get(
    "/environments/{environment_id}/reference-occurrences/{occurrence_id}/source",
    response_model=ReferenceOccurrenceSourceResponse,
)
def get_reference_source(environment_id: int, occurrence_id: str, session: Session = Depends(get_session)):
    try:
        source = get_reference_occurrence_source(session, environment_id, occurrence_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if source is None:
        raise HTTPException(status_code=404, detail="Reference occurrence not found")
    return source


@router.get(
    "/environments/{environment_id}/assets/{asset_id}",
    response_model=AssetDetailResponse,
    response_model_exclude_none=True,
)
def get_asset(
    environment_id: int,
    asset_id: str,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    etag, input_fingerprint = _assets_etag(session, environment_id, "asset-detail", {"asset_id": asset_id})
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=_cache_headers(etag))
    asset = get_environment_asset(
        session, environment_id, asset_id, input_fingerprint=input_fingerprint,
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    response.headers.update(_cache_headers(etag))
    return asset


@router.get("/environments/{environment_id}/assets/{asset_id}/source", response_model=AssetSourceResponse)
def get_asset_source(environment_id: int, asset_id: str, response: Response, session: Session = Depends(get_session)):
    try:
        payload = get_environment_asset_source(session, environment_id, asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="Asset source not found")
    response.headers["Cache-Control"] = "private, no-store"
    return payload


def _assets_etag(session: Session, environment_id: int, resource: str, parameters: dict) -> tuple[str, str]:
    try:
        input_version = lineage_input_fingerprint(session, environment_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    etag = f'"{fingerprint({"input": input_version, "producer": ASSETS_PROJECTOR_VERSION, "resource": resource, "parameters": parameters})}"'
    return etag, input_version


def _cache_headers(etag: str) -> dict[str, str]:
    return {"ETag": etag, "Cache-Control": "private, must-revalidate"}
