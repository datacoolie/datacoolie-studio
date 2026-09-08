from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from datacoolie_studio import __version__
from datacoolie_studio.startup_banner import (
    build_application_url,
    format_startup_banner,
    terminal_supports_wordmark,
)


def _port_value(raw_value: str) -> int:
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer between 0 and 65535") from exc
    if not 0 <= value <= 65535:
        raise argparse.ArgumentTypeError("port must be an integer between 0 and 65535")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the DataCoolie Studio local web app.")
    parser.add_argument(
        "--host",
        default=os.environ.get("DATACOOLIE_STUDIO_HOST") or "127.0.0.1",
        help="Host to bind. Defaults to DATACOOLIE_STUDIO_HOST or 127.0.0.1.",
    )
    parser.add_argument(
        "--port",
        default=os.environ.get("DATACOOLIE_STUDIO_PORT", "8765"),
        type=_port_value,
        help="Port to bind. Defaults to DATACOOLIE_STUDIO_PORT or 8765.",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        type=Path,
        help="Persistent Studio data root. Defaults to DATACOOLIE_STUDIO_DATA_DIR or ~/.datacoolie/datacoolie-studio.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Workspace SQLAlchemy database URL. Defaults to DATACOOLIE_STUDIO_DATABASE_URL or a SQLite file under --data-dir.",
    )
    parser.add_argument(
        "--result-cache-url",
        default=None,
        help="Result-cache URL. Defaults to DATACOOLIE_STUDIO_RESULT_CACHE_URL or SQLite under --data-dir.",
    )
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
    if args.data_dir is not None:
        os.environ["DATACOOLIE_STUDIO_DATA_DIR"] = str(args.data_dir)
    if args.database_url is not None:
        os.environ["DATACOOLIE_STUDIO_DATABASE_URL"] = args.database_url
    if args.result_cache_url is not None:
        os.environ["DATACOOLIE_STUDIO_RESULT_CACHE_URL"] = args.result_cache_url
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
