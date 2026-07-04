from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from datacoolie_studio.api.v1 import api_router
from datacoolie_studio.db.session import init_db
from datacoolie_studio.domains.sync.scheduler import scheduler_loop


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    stop_event = asyncio.Event()
    task = asyncio.create_task(scheduler_loop(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        await task


app = FastAPI(title="DataCoolie Studio", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(api_router)

static_dir = Path(__file__).parent / "static"
if (static_dir / "index.html").exists():
    assets_dir = static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/")
    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str = ""):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        requested = static_dir / full_path
        if full_path and requested.is_file():
            return FileResponse(requested)
        return FileResponse(static_dir / "index.html")
else:
    @app.get("/")
    def root() -> dict[str, str]:
        return {
            "name": "DataCoolie Studio",
            "message": "Frontend build not found. Run the Vite dev server or build frontend assets.",
        }
