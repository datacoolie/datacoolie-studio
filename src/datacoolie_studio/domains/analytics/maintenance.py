from __future__ import annotations

from pathlib import Path
from typing import Any

from datacoolie_studio.core.config import analytics_database_path
from datacoolie_studio.domains.analytics import access, schema, store
from datacoolie_studio.domains.analytics.connections import analytics_connections


def cache_stats() -> dict[str, Any]:
    path = analytics_database_path()
    exists = path.exists()
    stats: dict[str, Any] = {
        "path": str(path),
        "exists": exists,
        "size_bytes": path.stat().st_size if exists else None,
        "scope": "studio",
        "schema_version": None,
        "generation": None,
        "build_state": "rebuild_required",
        "published_at": None,
        "dataflow_row_count": 0,
        "job_row_count": 0,
        "filter_value_count": 0,
        "cached_source_ids": [],
    }
    if not exists:
        return stats
    conn = access.connect(path, read_only=True)
    try:
        meta = store.analytics_meta(conn)
        if meta is not None:
            stats.update(meta)
        stats["dataflow_row_count"] = table_row_count(conn, schema.DATAFLOW_TABLE)
        stats["job_row_count"] = table_row_count(conn, schema.JOB_TABLE)
        stats["filter_value_count"] = table_row_count(
            conn,
            schema.FILTER_VALUES_TABLE,
        )
        stats["cached_source_ids"] = sorted(store.cache_source_ids(conn))
    finally:
        conn.close()
    return stats


def clear_cache() -> dict[str, int]:
    """Delete only the rebuildable DuckDB analytics cache files."""
    path = analytics_database_path()
    candidate_path = access.candidate_path(path)
    if not path.exists() and not candidate_path.exists():
        return {
            "deleted_files": 0,
            "deleted_file_bytes": 0,
            "deleted_rows": 0,
        }
    stats = cache_stats()
    deleted_rows = sum(
        int(stats.get(key, 0))
        for key in ("dataflow_row_count", "job_row_count", "filter_value_count")
    )
    with access.analytics_maintenance_lock:
        with analytics_connections.exclusive_maintenance():
            candidates = [
                path,
                Path(f"{path}.wal"),
                candidate_path,
                Path(f"{candidate_path}.wal"),
            ]
            deleted_files = 0
            deleted_file_bytes = 0
            for candidate in candidates:
                if not candidate.exists():
                    continue
                deleted_file_bytes += candidate.stat().st_size
                candidate.unlink()
                deleted_files += 1
    return {
        "deleted_files": deleted_files,
        "deleted_file_bytes": deleted_file_bytes,
        "deleted_rows": deleted_rows,
    }


def table_row_count(conn, table_name: str) -> int:
    if not schema.table_exists(conn, table_name):
        return 0
    return int(conn.execute(f"SELECT count(*) FROM {table_name}").fetchone()[0])
