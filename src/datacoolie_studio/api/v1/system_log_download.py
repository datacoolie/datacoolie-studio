"""HTTP response lifecycle for prepared system-log downloads."""

from __future__ import annotations

from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from datacoolie_studio.domains.logs.system_log_download import (
    PreparedSystemLogDownload,
)


class PreparedSystemLogStreamingResponse(StreamingResponse):
    """Release a prepared download even when ASGI send aborts early."""

    def __init__(self, prepared: PreparedSystemLogDownload) -> None:
        super().__init__(
            prepared.iter_chunks(),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": f'attachment; filename="{prepared.filename}"',
                "Content-Length": str(prepared.size),
                "Cache-Control": "no-store",
            },
            background=BackgroundTask(prepared.close),
        )
        self._prepared = prepared

    async def __call__(self, scope, receive, send):
        try:
            return await super().__call__(scope, receive, send)
        finally:
            self._prepared.close()
