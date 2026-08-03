from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from datacoolie_studio import __version__
from datacoolie_studio.api.v1 import api_router
from datacoolie_studio.db.session import init_db
from datacoolie_studio.domains.analytics.errors import AnalyticsRebuildRequired
from datacoolie_studio.domains.analytics_upgrade.service import analytics_upgrade_loop
from datacoolie_studio.domains.sync.scheduler import scheduler_loop


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
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
    return JSONResponse(status_code=409, content={"detail": exc.detail()})


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
