from __future__ import annotations

from pathlib import Path
from typing import Any

from datacoolie_studio.core.config import (
    analytics_database_path,
    analytics_duckdb_memory_limit,
    analytics_duckdb_temp_directory,
    analytics_duckdb_threads,
    analytics_upgrade_batch_max_bytes,
    analytics_upgrade_batch_max_files,
    analytics_upgrade_build_concurrency,
    analytics_upgrade_max_inflight_bytes,
)
from datacoolie_studio.domains.analytics import (
    access,
    cache_state,
    row_maintenance,
    schema,
)
from datacoolie_studio.domains.analytics.connections import analytics_connections
from datacoolie_studio.db.models import utc_now


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
        "resource_limits": analytics_resource_limits(),
    }
    if not exists:
        return stats
    conn = access.connect(path, read_only=True)
    try:
        meta = cache_state.analytics_meta(conn)
        if meta is not None:
            stats.update(meta)
        stats["dataflow_row_count"] = table_row_count(conn, schema.DATAFLOW_TABLE)
        stats["job_row_count"] = table_row_count(conn, schema.JOB_TABLE)
        stats["filter_value_count"] = table_row_count(
            conn,
            schema.FILTER_VALUES_TABLE,
        )
        stats["cached_source_ids"] = sorted(cache_state.cache_source_ids(conn))
    finally:
        conn.close()
    return stats


def analytics_resource_limits() -> dict[str, Any]:
    """Return the effective analytics resource budget without secrets."""

    return {
        "duckdb_threads": analytics_duckdb_threads(),
        "duckdb_memory_limit": analytics_duckdb_memory_limit(),
        "duckdb_temp_directory": str(analytics_duckdb_temp_directory()),
        "upgrade_build_concurrency": analytics_upgrade_build_concurrency(),
        "upgrade_batch_max_files": analytics_upgrade_batch_max_files(),
        "upgrade_batch_max_bytes": analytics_upgrade_batch_max_bytes(),
        "upgrade_max_inflight_bytes": analytics_upgrade_max_inflight_bytes(),
    }


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


def source_stats(source_id: int) -> dict[str, int]:
    stats = {
        "dataflow_row_count": 0,
        "job_row_count": 0,
        "filter_value_count": 0,
    }
    path = analytics_database_path()
    if not path.exists():
        return stats
    conn = access.connect(path, read_only=True)
    try:
        stats["dataflow_row_count"] = row_maintenance.table_source_row_count(
            conn,
            schema.DATAFLOW_TABLE,
            source_id,
        )
        stats["job_row_count"] = row_maintenance.table_source_row_count(
            conn,
            schema.JOB_TABLE,
            source_id,
        )
        stats["filter_value_count"] = row_maintenance.table_source_row_count(
            conn,
            schema.FILTER_VALUES_TABLE,
            source_id,
        )
    finally:
        conn.close()
    return stats


def purge_source_ids(source_ids: list[int]) -> dict[str, int]:
    unique_ids = sorted({int(source_id) for source_id in source_ids if int(source_id) > 0})
    if not unique_ids:
        return {
            "dataflow_rows_deleted": 0,
            "job_rows_deleted": 0,
            "filter_values_deleted": 0,
        }
    path = analytics_database_path()
    if not path.exists():
        return {
            "dataflow_rows_deleted": 0,
            "job_rows_deleted": 0,
            "filter_values_deleted": 0,
        }
    with access.analytics_maintenance_lock:
        conn = access.connect(path)
        try:
            conn.execute("BEGIN TRANSACTION")
            meta = cache_state.analytics_meta(conn)
            compatible = (
                meta is not None
                and meta["schema_version"] == schema.ANALYTICS_SCHEMA_VERSION
                and meta["build_state"] == "ready"
                and schema.typed_cache_schema_is_ready(conn)
            )
            dataflow_rows = row_maintenance.delete_rows_by_source_ids(
                conn,
                schema.DATAFLOW_TABLE,
                unique_ids,
            )
            job_rows = row_maintenance.delete_rows_by_source_ids(
                conn,
                schema.JOB_TABLE,
                unique_ids,
            )
            filter_rows = row_maintenance.delete_rows_by_source_ids(
                conn,
                schema.FILTER_VALUES_TABLE,
                unique_ids,
            )
            for table_name in (schema.CACHE_SOURCES_TABLE,):
                row_maintenance.delete_rows_by_source_ids(
                    conn,
                    table_name,
                    unique_ids,
                    source_column="source_id",
                )
            from datacoolie_studio.domains.analytics import publication
            from datacoolie_studio.domains.analytics.serving_facts import (
                MONITORING_DATAFLOW_FACTS_TABLE, MONITORING_JOB_FACTS_TABLE,
            )
            if compatible:
                publication.publish_generation(
                    conn,
                    dataflow_column_types=schema.DATAFLOW_COLUMN_TYPES,
                    job_column_types=schema.JOB_COLUMN_TYPES,
                    published_at=utc_now(),
                    source_ids=unique_ids,
                )
            else:
                # Legacy caches remain rebuild-required; purge physical derived
                # rows without claiming that the old schema is current.
                for table_name in (MONITORING_DATAFLOW_FACTS_TABLE, MONITORING_JOB_FACTS_TABLE):
                    if conn.execute(
                        "SELECT count(*) FROM duckdb_tables() WHERE table_name = ? "
                        "AND NOT temporary AND schema_name = 'main'",
                        [table_name],
                    ).fetchone()[0]:
                        row_maintenance.delete_rows_by_source_ids(conn, table_name, unique_ids)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
    return {
        "dataflow_rows_deleted": dataflow_rows,
        "job_rows_deleted": job_rows,
        "filter_values_deleted": filter_rows,
    }
