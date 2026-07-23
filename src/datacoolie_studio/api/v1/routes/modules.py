from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from datacoolie_studio.api.v1.contracts.modules import (
    ModuleInfo,
    ModuleStateUpdateRequest,
)
from datacoolie_studio.db.session import get_session
from datacoolie_studio.domains.modules import service as modules

router = APIRouter(tags=["modules"])


@router.get("/studio/modules", response_model=list[ModuleInfo])
def list_modules(session: Session = Depends(get_session)):
    return modules.list_modules(session)


@router.patch("/studio/modules/{key}", response_model=ModuleInfo)
def patch_module(key: str, payload: ModuleStateUpdateRequest, session: Session = Depends(get_session)):
    try:
        return modules.set_module_enabled(session, key, payload.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown module: {key}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
