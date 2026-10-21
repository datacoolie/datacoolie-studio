from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from datacoolie_studio import __version__
from datacoolie_studio.api.v1 import api_router
from datacoolie_studio.core.request_timing import RequestTiming
from datacoolie_studio.db.session import create_session, init_db
from datacoolie_studio.domains.analytics.errors import AnalyticsRebuildRequired
from datacoolie_studio.domains.monitoring.identity_context import IdentityRevisionMismatch
from datacoolie_studio.domains.analytics.access import analytics_process_ownership
from datacoolie_studio.domains.analytics_upgrade.service import analytics_upgrade_loop
from datacoolie_studio.domains.sync.scheduler import scheduler_loop
from datacoolie_studio.domains.sync.service import reconcile_orphaned_sync_jobs
from datacoolie_studio.domains.logs.staging import cleanup_orphaned_staging
from datacoolie_studio.domains.metadata.backups import cleanup_orphaned_metadata_artifacts
from datacoolie_studio.domains.metadata.reconciliation import (
    reconcile_metadata_write_operations,
)
from datacoolie_studio.domains.workspace.derived_cleanup import (
    reconcile_orphaned_analytics_cache,
)


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    with analytics_process_ownership():
        init_db()
        reconciliation_session = create_session()
        try:
            metadata_write_recovery = reconcile_metadata_write_operations(
                reconciliation_session
            )
            if metadata_write_recovery["errors"]:
                logger.warning(
                    "Some metadata write operations remain pending after startup: %s",
                    metadata_write_recovery,
                )
            try:
                cleanup_orphaned_metadata_artifacts(reconciliation_session)
            except Exception:
                logger.warning("Metadata artifact cleanup deferred at startup", exc_info=True)
            reconcile_orphaned_sync_jobs(reconciliation_session)
            reconcile_orphaned_analytics_cache(reconciliation_session)
            staging_cleanup = cleanup_orphaned_staging()
            if staging_cleanup["errors"]:
                logger.warning(
                    "Some orphaned log staging directories could not be removed: %s",
                    staging_cleanup["errors"],
                )
        finally:
            reconciliation_session.close()
        stop_event = asyncio.Event()
        scheduler_task = asyncio.create_task(scheduler_loop(stop_event))
        upgrade_task = asyncio.create_task(analytics_upgrade_loop(stop_event))
        try:
            yield
        finally:
            stop_event.set()
            await asyncio.gather(scheduler_task, upgrade_task)


app = FastAPI(title="DataCoolie Studio", version=__version__, lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=1)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AnalyticsRebuildRequired)
async def analytics_rebuild_required_handler(
    _request: Request,
    exc: AnalyticsRebuildRequired,
) -> JSONResponse:
    timing = getattr(_request.state, "registry_request_timing", None)
    headers = {"Server-Timing": timing.server_timing()} if isinstance(timing, RequestTiming) else None
    return JSONResponse(status_code=409, content={"detail": exc.detail()}, headers=headers)


@app.exception_handler(IdentityRevisionMismatch)
async def destination_identity_revision_handler(_request: Request, exc: IdentityRevisionMismatch) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)}, headers={"Cache-Control": "no-store"})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(api_router)

static_dir = Path(__file__).parent / "static"
if (static_dir / "index.html").exists():
    static_root = static_dir.resolve()
    assets_dir = static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    def resolve_static_request(full_path: str) -> Path:
        normalized = full_path.replace("\\", "/")
        posix_path = PurePosixPath(normalized)
        windows_path = PureWindowsPath(full_path)
        if (
            posix_path.is_absolute()
            or ".." in posix_path.parts
            or windows_path.drive
            or windows_path.root
        ):
            raise HTTPException(status_code=404, detail="Not found")
        try:
            requested = static_root.joinpath(*posix_path.parts).resolve()
            requested.relative_to(static_root)
        except (OSError, RuntimeError, ValueError):
            raise HTTPException(status_code=404, detail="Not found") from None
        return requested

    @app.get("/")
    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str = ""):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        requested = resolve_static_request(full_path)
        if full_path and requested.is_file():
            return FileResponse(requested)
        return FileResponse(static_root / "index.html")
else:
    @app.get("/")
    def root() -> dict[str, str]:
        return {
            "name": "DataCoolie Studio",
            "message": "Frontend build not found. Run the Vite dev server or build frontend assets.",
        }
