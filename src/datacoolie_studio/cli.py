from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser

import uvicorn

from datacoolie_studio import __version__
from datacoolie_studio.core.config import default_database_path
from datacoolie_studio.startup_banner import (
    build_application_url,
    format_startup_banner,
    terminal_supports_wordmark,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the DataCoolie Studio local web app.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind. Defaults to 127.0.0.1.")
    parser.add_argument("--port", default=8765, type=int, help="Port to bind. Defaults to 8765.")
    parser.add_argument("--db", default=str(default_database_path()), help="SQLite workspace database path.")
    parser.add_argument("--database-url", default=None, help="SQLAlchemy database URL. Overrides --db when set.")
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically.")
    parser.add_argument(
        "--no-banner",
        action="store_true",
        help="Do not print the startup introduction.",
    )
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn reload for local development.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.database_url:
        os.environ["DATACOOLIE_STUDIO_DATABASE_URL"] = args.database_url
    else:
        os.environ["DATACOOLIE_STUDIO_DB"] = args.db
    url = build_application_url(args.host, args.port)
    if not args.no_banner:
        print(
            format_startup_banner(
                __version__,
                url,
                use_wordmark=terminal_supports_wordmark(sys.stdout),
            ),
            file=sys.stdout,
            flush=True,
        )
    if url is not None and not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(
        "datacoolie_studio.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
