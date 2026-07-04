from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from datacoolie_studio.api.v1.schemas import AssetDetailResponse, AssetsResponse
from datacoolie_studio.db.session import get_session
from datacoolie_studio.domains.assets.service import (
    get_environment_asset,
    list_environment_assets,
)

router = APIRouter(tags=["assets"])


@router.get("/environments/{environment_id}/assets", response_model=AssetsResponse)
def get_assets(environment_id: int, session: Session = Depends(get_session)):
    return list_environment_assets(session, environment_id)


@router.get("/environments/{environment_id}/assets/{asset_id:path}", response_model=AssetDetailResponse)
def get_asset(environment_id: int, asset_id: str, session: Session = Depends(get_session)):
    asset = get_environment_asset(session, environment_id, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset
