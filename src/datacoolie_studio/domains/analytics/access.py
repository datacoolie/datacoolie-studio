from __future__ import annotations

from pathlib import Path
from threading import Lock

import duckdb

from datacoolie_studio.domains.analytics import schema
from datacoolie_studio.domains.analytics.connections import analytics_connections


analytics_maintenance_lock = Lock()


def connect(path: Path, *, read_only: bool = False):
    return analytics_connections.connect(path, read_only=read_only)


def cache_is_ready(path: Path) -> bool:
    try:
        conn = connect(path, read_only=True)
    except duckdb.Error:
        return False
    try:
        return schema.typed_cache_schema_is_ready(conn)
    finally:
        conn.close()


def schema_rebuild_required(path: Path) -> bool:
    return path.exists() and not cache_is_ready(path)


def candidate_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.candidate{path.suffix}")


def discard_candidate(path: Path) -> None:
    for candidate in (path, Path(f"{path}.wal")):
        if candidate.exists():
            candidate.unlink()


def swap_candidate(candidate_path: Path, live_path: Path) -> None:
    """Replace a cache only after validation and managed-reader drain."""
    if not candidate_path.exists():
        raise RuntimeError("Analytics rebuild candidate is missing")
    with analytics_connections.exclusive_maintenance():
        candidate_path.replace(live_path)
        live_wal = Path(f"{live_path}.wal")
        candidate_wal = Path(f"{candidate_path}.wal")
        if candidate_wal.exists():
            candidate_wal.replace(live_wal)
        elif live_wal.exists():
            live_wal.unlink()
