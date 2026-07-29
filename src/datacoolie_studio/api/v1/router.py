from __future__ import annotations

from fastapi import APIRouter

from datacoolie_studio.api.v1.routes import (
    assets,
    credentials,
    lineage,
    metadata,
    modules,
    monitoring,
    overview,
    sources,
    workspace,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(workspace.router)
api_router.include_router(modules.router)
api_router.include_router(credentials.router)
api_router.include_router(metadata.router)
api_router.include_router(assets.router)
api_router.include_router(lineage.router)
api_router.include_router(overview.router)
api_router.include_router(monitoring.router)
api_router.include_router(sources.router)
