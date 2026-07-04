from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from datacoolie_studio.core.config import analytics_database_path
from datacoolie_studio.core.time import utc_datetime_sort_key
from datacoolie_studio.db.models import EnvironmentSource, LogFileManifest, utc_now
from datacoolie_studio.domains.logs.reader import (
    discover_dataflow_parquet_files,
    discover_job_jsonl_files,
    discover_system_jsonl_files,
    parse_system_log_file_metadata,
    read_system_log_file,
    read_dataflow_logs,
    read_job_logs,
)
from datacoolie_studio.domains.logs.source_config import resolve_log_source_paths
from datacoolie_studio.domains.storage.uri import StorageProviderNotEnabled, require_local_path
from datacoolie_studio.domains.sync import service as sync


STUDIO_CACHE_COLUMNS = {
    "_source_id": "BIGINT",
    "_file_uri": "VARCHAR",
    "_file_kind": "VARCHAR",
    "_file_date": "DATE",
    "_source_size": "BIGINT",
    "_source_mtime_ns": "BIGINT",
    "_ingested_at": "TIMESTAMPTZ",
}

DATAFLOW_TABLE = "etl_dataflow_runs"
JOB_TABLE = "etl_job_runs"
FILTER_VALUES_TABLE = "etl_monitoring_filter_values"
LEGACY_DATAFLOW_TABLE = "etl_dataflow_run_cache"
LEGACY_JOB_TABLE = "etl_job_run_cache"

DATAFLOW_SORT_COLUMNS = {
    "end_time": "TRY_CAST(COALESCE(d.end_time, d.start_time) AS TIMESTAMPTZ)",
    "start_time": "TRY_CAST(d.start_time AS TIMESTAMPTZ)",
    "duration_seconds": "d.duration_seconds",
    "status": "d.status",
    "dataflow_run_id": "d.dataflow_run_id",
    "dataflow_name": "d.dataflow_name",
    "job_id": "d.job_id",
    "stage": "d.stage",
    "operation_type": "d.operation_type",
    "source_name": "d.source_name",
    "destination_name": "d.destination_name",
    "source_rows_read": "d.source_rows_read",
    "destination_rows_written": "d.destination_rows_written",
    "engine_name": "COALESCE(j.engine_name, 'unknown')",
}

JOB_SORT_COLUMNS = {
    "end_time": "TRY_CAST(COALESCE(j.end_time, j.start_time) AS TIMESTAMPTZ)",
    "start_time": "TRY_CAST(j.start_time AS TIMESTAMPTZ)",
    "duration_seconds": "j.duration_seconds",
    "status": "j.status",
    "job_id": "j.job_id",
    "stages": "j.stages",
    "operation_types": "j.operation_types",
    "engine_name": "j.engine_name",
    "metadata_provider_name": "j.metadata_provider_name",
    "total_dataflows": "j.total_dataflows",
    "total_rows_read": "j.total_rows_read",
    "total_rows_written": "j.total_rows_written",
}

FILTER_VALUE_SOURCES = {
    "operation_type": (DATAFLOW_TABLE, "operation_type"),
    "status": (DATAFLOW_TABLE, "status"),
    "stage": (DATAFLOW_TABLE, "stage"),
    "engine_name": (JOB_TABLE, "engine_name"),
    "metadata_provider_name": (JOB_TABLE, "metadata_provider_name"),
    "platform_name": (JOB_TABLE, "platform_name"),
    "source_connection_type": (DATAFLOW_TABLE, "source_connection_type"),
    "source_format": (DATAFLOW_TABLE, "source_format"),
    "source_table": (DATAFLOW_TABLE, "source_table"),
    "destination_connection_type": (DATAFLOW_TABLE, "destination_connection_type"),
    "destination_format": (DATAFLOW_TABLE, "destination_format"),
    "destination_table": (DATAFLOW_TABLE, "destination_table"),
    "destination_load_type": (DATAFLOW_TABLE, "destination_load_type"),
    "destination_operation_type": (DATAFLOW_TABLE, "destination_operation_type"),
}

DATAFLOW_COLUMN_TYPES = {
    "_type": "VARCHAR",
    "job_id": "VARCHAR",
    "dataflow_id": "VARCHAR",
    "workspace_id": "VARCHAR",
    "dataflow_name": "VARCHAR",
    "dataflow_description": "VARCHAR",
    "stage": "VARCHAR",
    "group_number": "BIGINT",
    "execution_order": "BIGINT",
    "processing_mode": "VARCHAR",
    "is_active": "BOOLEAN",
    "configure": "VARCHAR",
    "source_id": "VARCHAR",
    "source_name": "VARCHAR",
    "source_connection_type": "VARCHAR",
    "source_format": "VARCHAR",
    "source_catalog": "VARCHAR",
    "source_database": "VARCHAR",
    "source_schema": "VARCHAR",
    "source_table": "VARCHAR",
    "source_full_table": "VARCHAR",
    "source_path": "VARCHAR",
    "source_query": "VARCHAR",
    "source_python_function": "VARCHAR",
    "source_watermark_columns": "VARCHAR",
    "source_filter_expression": "VARCHAR",
    "source_configure": "VARCHAR",
    "transform_deduplicate_columns": "VARCHAR",
    "transform_latest_data_columns": "VARCHAR",
    "transform_filter_expression": "VARCHAR",
    "transform_additional_columns": "VARCHAR",
    "transform_schema_hints": "VARCHAR",
    "transform_configure": "VARCHAR",
    "destination_id": "VARCHAR",
    "destination_name": "VARCHAR",
    "destination_connection_type": "VARCHAR",
    "destination_format": "VARCHAR",
    "destination_catalog": "VARCHAR",
    "destination_database": "VARCHAR",
    "destination_schema": "VARCHAR",
    "destination_table": "VARCHAR",
    "destination_full_table": "VARCHAR",
    "destination_path": "VARCHAR",
    "destination_load_type": "VARCHAR",
    "destination_merge_keys": "VARCHAR",
    "destination_partition_columns": "VARCHAR",
    "destination_configure": "VARCHAR",
    "dataflow_run_id": "VARCHAR",
    "operation_type": "VARCHAR",
    "start_time": "TIMESTAMPTZ",
    "end_time": "TIMESTAMPTZ",
    "duration_seconds": "DOUBLE",
    "status": "VARCHAR",
    "error_message": "VARCHAR",
    "retry_attempts": "BIGINT",
    "source_start_time": "TIMESTAMPTZ",
    "source_end_time": "TIMESTAMPTZ",
    "source_duration_seconds": "DOUBLE",
    "source_status": "VARCHAR",
    "source_error_message": "VARCHAR",
    "source_rows_read": "BIGINT",
    "source_action": "VARCHAR",
    "source_watermark_before": "VARCHAR",
    "source_watermark_after": "VARCHAR",
    "source_watermark_effective": "VARCHAR",
    "transform_start_time": "TIMESTAMPTZ",
    "transform_end_time": "TIMESTAMPTZ",
    "transform_duration_seconds": "DOUBLE",
    "transform_status": "VARCHAR",
    "transform_error_message": "VARCHAR",
    "transformers_applied": "VARCHAR",
    "destination_start_time": "TIMESTAMPTZ",
    "destination_end_time": "TIMESTAMPTZ",
    "destination_duration_seconds": "DOUBLE",
    "destination_status": "VARCHAR",
    "destination_error_message": "VARCHAR",
    "destination_operation_type": "VARCHAR",
    "destination_rows_written": "BIGINT",
    "destination_rows_inserted": "BIGINT",
    "destination_rows_updated": "BIGINT",
    "destination_rows_deleted": "BIGINT",
    "destination_files_added": "BIGINT",
    "destination_files_removed": "BIGINT",
    "destination_bytes_added": "BIGINT",
    "destination_bytes_removed": "BIGINT",
    "destination_bytes_saved": "BIGINT",
    "destination_operation_details": "VARCHAR",
    "overhead_duration_seconds": "DOUBLE",
    "__run_date": "DATE",
}

JOB_COLUMN_TYPES = {
    "_type": "VARCHAR",
    "job_id": "VARCHAR",
    "workspace_id": "VARCHAR",
    "job_index": "BIGINT",
    "job_num": "BIGINT",
    "platform_name": "VARCHAR",
    "engine_name": "VARCHAR",
    "metadata_provider_name": "VARCHAR",
    "watermark_manager_name": "VARCHAR",
    "start_time": "VARCHAR",
    "end_time": "VARCHAR",
    "duration_seconds": "DOUBLE",
    "status": "VARCHAR",
    "error_message": "VARCHAR",
    "dry_run": "BOOLEAN",
    "stop_on_error": "BOOLEAN",
    "max_workers": "BIGINT",
    "retry_count": "BIGINT",
    "retry_delay": "DOUBLE",
    "retention_hours": "BIGINT",
    "stages": "VARCHAR",
    "total_dataflows": "BIGINT",
    "total_succeeded": "BIGINT",
    "total_failed": "BIGINT",
    "total_skipped": "BIGINT",
    "total_running": "BIGINT",
    "total_pending": "BIGINT",
    "total_rows_read": "BIGINT",
    "total_rows_written": "BIGINT",
    "total_rows_inserted": "BIGINT",
    "total_rows_updated": "BIGINT",
    "total_rows_deleted": "BIGINT",
    "total_files_added": "BIGINT",
    "total_files_removed": "BIGINT",
    "total_bytes_added": "BIGINT",
    "total_bytes_removed": "BIGINT",
    "operation_types": "VARCHAR",
}


def analytics_cache_stats() -> dict[str, Any]:
    path = analytics_database_path()
    exists = path.exists()
    stats: dict[str, Any] = {
        "path": str(path),
        "exists": exists,
        "size_bytes": path.stat().st_size if exists else None,
        "scope": "studio",
        "dataflow_row_count": 0,
        "job_row_count": 0,
        "filter_value_count": 0,
        "cached_source_ids": [],
    }
    if not exists:
        return stats
    _ensure_duckdb_cache_ready(path)
    conn = duckdb.connect(database=str(path), read_only=True)
    try:
        stats["dataflow_row_count"] = _table_row_count(conn, DATAFLOW_TABLE)
        stats["job_row_count"] = _table_row_count(conn, JOB_TABLE)
        stats["filter_value_count"] = _table_row_count(conn, FILTER_VALUES_TABLE)
        source_ids = set()
        source_ids.update(_table_source_ids(conn, DATAFLOW_TABLE))
        source_ids.update(_table_source_ids(conn, JOB_TABLE))
        source_ids.update(_table_source_ids(conn, FILTER_VALUES_TABLE))
        stats["cached_source_ids"] = sorted(source_ids)
    finally:
        conn.close()
    return stats


def purge_cached_source_ids(source_ids: list[int]) -> dict[str, int]:
    unique_ids = sorted({int(source_id) for source_id in source_ids if int(source_id) > 0})
    if not unique_ids:
        return {"dataflow_rows_deleted": 0, "job_rows_deleted": 0, "filter_values_deleted": 0}
    path = analytics_database_path()
    if not path.exists():
        return {"dataflow_rows_deleted": 0, "job_rows_deleted": 0, "filter_values_deleted": 0}
    _ensure_duckdb_cache_ready(path)
    conn = duckdb.connect(database=str(path))
    try:
        dataflow_rows = _delete_rows_by_source_ids(conn, DATAFLOW_TABLE, unique_ids)
        job_rows = _delete_rows_by_source_ids(conn, JOB_TABLE, unique_ids)
        filter_rows = _delete_rows_by_source_ids(conn, FILTER_VALUES_TABLE, unique_ids)
    finally:
        conn.close()
    return {
        "dataflow_rows_deleted": dataflow_rows,
        "job_rows_deleted": job_rows,
        "filter_values_deleted": filter_rows,
    }


def refresh_log_source_cache(session: Session, source: EnvironmentSource) -> dict[str, Any]:
    if source.source_kind != "logs":
        raise ValueError("Source is not a log source")

    job = sync.begin_sync_job(session, source, "force_refresh")
    checked_at = utc_now()
    revision = sync.stat_source(source)
    error = _revision_error(source, revision)
    if error:
        sync.record_source_revision(session, source=source, status="error", revision=None, error=error, checked_at=checked_at)
        sync.finish_sync_job(
            session,
            job,
            status="failed",
            message=error["message"],
            result={"status": "error", "message": error["message"], "revision": None, "error": error},
            completed_at=checked_at,
        )
        return sync.source_sync_status(session, source, job)

    log_paths = resolve_log_source_paths(source)
    try:
        etl_path = require_local_path(log_paths.etl_logs_uri or source.uri)
        system_path = require_local_path(log_paths.system_logs_uri) if log_paths.system_logs_uri else None
    except StorageProviderNotEnabled as exc:
        error = {"message": str(exc), "code": "provider_not_enabled", "provider": exc.provider}
        sync.record_source_revision(session, source=source, status="error", revision=None, error=error, checked_at=checked_at)
        sync.finish_sync_job(
            session,
            job,
            status="failed",
            message=error["message"],
            result={"status": "error", "message": error["message"], "revision": None, "error": error},
            completed_at=checked_at,
        )
        return sync.source_sync_status(session, source, job)
    dataflow_files = discover_dataflow_parquet_files(etl_path.as_posix())
    job_files = discover_job_jsonl_files(etl_path.as_posix())
    system_files = discover_system_jsonl_files(system_path.as_posix() if system_path else None)
    existing = _existing_manifest(session, source.id)
    current_files = {
        file_uri: _manifest_file_state(file_uri, "dataflow_parquet")
        for file_uri in dataflow_files
    }
    current_files.update({file_uri: _manifest_file_state(file_uri, "job_jsonl") for file_uri in job_files})
    current_files.update({file_uri: _manifest_file_state(file_uri, "system_jsonl") for file_uri in system_files})
    changed_files = [
        file_state
        for file_uri, file_state in current_files.items()
        if existing.get(file_uri) != file_state["revision_json"]
    ]
    removed_files = sorted(set(existing) - set(current_files))

    errors: list[dict[str, str]] = []
    parsed_dataflow_files: list[tuple[str, str, str]] = []
    parsed_job_rows: list[tuple[str, str, str, dict[str, Any]]] = []
    file_row_counts: dict[str, int] = {}
    for file_state in changed_files:
        file_uri = str(file_state["file_uri"])
        file_kind = str(file_state["file_kind"])
        if file_kind == "dataflow_parquet":
            parsed_dataflow_files.append((file_uri, file_kind, _file_revision_json(file_uri)))
            read_errors = []
        elif file_kind == "job_jsonl":
            rows, read_errors = _read_job_file(file_uri)
            parsed_job_rows.extend((file_uri, file_kind, _file_revision_json(file_uri), row) for row in rows)
            file_row_counts[file_uri] = len(rows)
        else:
            file_row_counts[file_uri] = _count_jsonl_lines(file_uri)
            read_errors = []
        errors.extend(read_errors)

    changed_file_uris = [str(file_state["file_uri"]) for file_state in changed_files]
    upsert_result = _upsert_duckdb_rows(source.id, parsed_dataflow_files, parsed_job_rows, removed_files, changed_file_uris)
    file_row_counts.update(upsert_result["file_row_counts"])
    errors.extend(upsert_result["errors"])
    _upsert_manifest(session, source.id, changed_files, removed_files, file_row_counts, checked_at)

    status = "warning" if errors else "ok"
    message = "Log source cache refreshed" if changed_files or removed_files else "Log source cache is current"
    if errors:
        message = "Log source cache refreshed with read warnings"
    sync.record_source_revision(session, source=source, status=status, revision=revision, error=None, checked_at=checked_at)
    sync.finish_sync_job(
        session,
        job,
        status="succeeded",
        message=message,
        result={
            "status": status,
            "message": message,
            "revision": revision,
            "error": None,
            "record_counts": {
                "parsed_dataflow_records": upsert_result["parsed_dataflow_records"],
                "parsed_job_records": len(parsed_job_rows),
                "dataflow_parquet_files": len(dataflow_files),
                "job_jsonl_files": len(job_files),
                "system_jsonl_files": len(system_files),
                "parsed_files": len(changed_files),
                "removed_files": len(removed_files),
            },
            "errors": errors,
        },
        completed_at=checked_at,
    )
    return sync.source_sync_status(session, source, job)


def cached_monitoring_rows(
    session: Session,
    paths: list[EnvironmentSource],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]] | None:
    enabled = [path for path in paths if path.enabled]
    if not enabled:
        return [], [], []
    cached_ids = {
        int(row)
        for row in session.scalars(
            select(LogFileManifest.source_id).where(
                LogFileManifest.source_id.in_([path.id for path in enabled]),
                LogFileManifest.file_kind.in_(["dataflow_parquet", "job_jsonl"]),
            )
        ).all()
    }
    if not cached_ids:
        return None

    missing = [path for path in enabled if path.id not in cached_ids]
    rows, jobs = _read_duckdb_rows(sorted(cached_ids))
    if cached_ids and not rows and not jobs:
        return None
    job_by_id = {job.get("job_id"): job for job in jobs if job.get("job_id")}
    enriched = [_enrich_dataflow(row, job_by_id.get(row.get("job_id"))) for row in rows]
    errors = [
        {"uri": path.uri, "message": "ETL log path has no cache yet; run Sync now"}
        for path in missing
    ]
    return enriched, jobs, errors


def cached_dataflow_logs(
    session: Session,
    paths: list[EnvironmentSource],
    limit: int = 1000,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]] | None:
    cached = cached_monitoring_rows(session, paths)
    if cached is None:
        return None
    rows, _, errors = cached
    return rows[:limit], errors


def cached_job_logs(
    session: Session,
    paths: list[EnvironmentSource],
    limit: int = 1000,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]] | None:
    cached = cached_monitoring_rows(session, paths)
    if cached is None:
        return None
    _, jobs, errors = cached
    return jobs[:limit], errors


def system_log_records(
    session: Session,
    paths: list[EnvironmentSource],
    *,
    job_id: str,
    dataflow_id: str | None = None,
    level: str | None = None,
    q: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    enabled_ids = [path.id for path in paths if path.enabled]
    if not enabled_ids or not job_id:
        return {"records": [], "total": 0, "files": [], "errors": []}
    files = list(
        session.scalars(
            select(LogFileManifest)
            .where(
                LogFileManifest.source_id.in_(enabled_ids),
                LogFileManifest.file_kind == "system_jsonl",
                LogFileManifest.job_id == job_id,
            )
            .order_by(LogFileManifest.log_timestamp.desc(), LogFileManifest.id.desc())
        )
    )
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    total = 0
    remaining_offset = offset
    for file in files:
        file_rows, file_total, file_errors = read_system_log_file(
            file.file_uri,
            job_id=job_id,
            dataflow_id=dataflow_id,
            level=level,
            q=q,
            limit=limit - len(records),
            offset=remaining_offset,
        )
        total += file_total
        errors.extend(file_errors)
        if remaining_offset:
            remaining_offset = max(0, remaining_offset - file_total)
        records.extend(file_rows)
        if len(records) >= limit:
            break
    return {
        "records": records,
        "total": total,
        "files": [
            {
                "source_id": file.source_id,
                "file_uri": file.file_uri,
                "row_count": file.row_count,
                "log_timestamp": file.log_timestamp,
                "run_date": file.run_date,
            }
            for file in files
        ],
        "errors": errors,
    }


def query_cached_dataflow_logs(
    session: Session,
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    limit: int = 1000,
    offset: int = 0,
    sort_by: str = "start_time",
    sort_dir: str = "desc",
) -> tuple[list[dict[str, Any]], int, list[dict[str, str]]] | None:
    context = _cached_source_context(session, paths)
    if context is None:
        return None
    source_ids, errors = context
    if not source_ids:
        return [], 0, errors
    path = analytics_database_path()
    if not path.exists():
        return [], 0, errors
    _ensure_duckdb_cache_ready(path)
    conn = duckdb.connect(database=str(path), read_only=True)
    try:
        if not _table_exists(conn, DATAFLOW_TABLE):
            return None
        if not _table_has_source_rows(conn, DATAFLOW_TABLE, source_ids):
            return None
        source_placeholders = ", ".join("?" for _ in source_ids)
        where_sql, params = _monitoring_filter_sql(filters, "d", "j")
        job_lookup_sql = (
            f"""
            SELECT
              _source_id,
              job_id,
              ANY_VALUE(engine_name) AS engine_name,
              ANY_VALUE(metadata_provider_name) AS metadata_provider_name,
              ANY_VALUE(platform_name) AS platform_name,
              ANY_VALUE(status) AS status,
              ANY_VALUE(duration_seconds) AS duration_seconds
            FROM {JOB_TABLE}
            WHERE _source_id IN ({source_placeholders})
              AND job_id IS NOT NULL
            GROUP BY _source_id, job_id
            """
        )
        from_sql = (
            f"FROM {DATAFLOW_TABLE} d "
            f"LEFT JOIN ({job_lookup_sql}) j ON j._source_id = d._source_id AND j.job_id = d.job_id "
            f"WHERE d._source_id IN ({source_placeholders}){where_sql}"
        )
        query_params = [*source_ids, *source_ids, *params]
        order_sql = _monitoring_order_sql(sort_by, sort_dir, DATAFLOW_SORT_COLUMNS, default_alias="d")
        total = int(conn.execute(f"SELECT count(*) {from_sql}", query_params).fetchone()[0])
        result = conn.execute(
            f"""
            SELECT
              d.*,
              COALESCE(j.engine_name, 'unknown') AS engine_name,
              COALESCE(j.metadata_provider_name, 'unknown') AS metadata_provider_name,
              COALESCE(j.platform_name, 'unknown') AS platform_name,
              j.status AS job_status,
              j.duration_seconds AS job_duration_seconds
            {from_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """,
            [*query_params, limit, offset],
        )
        rows = _result_rows(result)
    finally:
        conn.close()
    return rows, total, errors


def query_cached_job_logs(
    session: Session,
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    limit: int = 1000,
    offset: int = 0,
    sort_by: str = "start_time",
    sort_dir: str = "desc",
) -> tuple[list[dict[str, Any]], int, list[dict[str, str]]] | None:
    context = _cached_source_context(session, paths)
    if context is None:
        return None
    source_ids, errors = context
    if not source_ids:
        return [], 0, errors
    path = analytics_database_path()
    if not path.exists():
        return [], 0, errors
    _ensure_duckdb_cache_ready(path)
    conn = duckdb.connect(database=str(path), read_only=True)
    try:
        if not _table_exists(conn, JOB_TABLE):
            return None
        if not _table_has_source_rows(conn, JOB_TABLE, source_ids):
            return None
        job_select_sql = _select_alias_columns("j", _table_columns(conn, JOB_TABLE))
        source_placeholders = ", ".join("?" for _ in source_ids)
        where_sql, params = _monitoring_filter_sql(filters, "j", "j", include_dataflow_filters=False)
        child_summary_sql = (
            f"""
            SELECT
              _source_id,
              job_id,
              COUNT(*) AS child_dataflow_count,
              SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS child_succeeded_count,
              SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS child_failed_count,
              SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END) AS child_skipped_count,
              SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS child_running_count,
              SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS child_pending_count,
              quantile_cont(duration_seconds, 0.95) AS child_p95_duration_seconds,
              SUM(source_rows_read) AS child_total_rows_read,
              SUM(destination_rows_written) AS child_total_rows_written,
              SUM(destination_bytes_added) AS child_total_bytes_added,
              SUM(destination_bytes_removed) AS child_total_bytes_removed
            FROM {DATAFLOW_TABLE}
            WHERE _source_id IN ({source_placeholders})
              AND job_id IS NOT NULL
            GROUP BY _source_id, job_id
            """
        )
        from_sql = (
            f"FROM {JOB_TABLE} j "
            f"LEFT JOIN ({child_summary_sql}) c ON c._source_id = j._source_id AND c.job_id = j.job_id "
            f"WHERE j._source_id IN ({source_placeholders}){where_sql}"
        )
        query_params = [*source_ids, *source_ids, *params]
        order_sql = _monitoring_order_sql(
            sort_by,
            sort_dir,
            JOB_SORT_COLUMNS,
            default_alias="j",
        )
        total = int(conn.execute(f"SELECT count(*) {from_sql}", query_params).fetchone()[0])
        result = conn.execute(
            f"""
            SELECT
              {job_select_sql},
              COALESCE(c.child_dataflow_count, 0) AS child_dataflow_count,
              COALESCE(c.child_succeeded_count, 0) AS child_succeeded_count,
              COALESCE(c.child_failed_count, 0) AS child_failed_count,
              COALESCE(c.child_skipped_count, 0) AS child_skipped_count,
              COALESCE(c.child_running_count, 0) AS child_running_count,
              COALESCE(c.child_pending_count, 0) AS child_pending_count,
              COALESCE(c.child_p95_duration_seconds, 0) AS child_p95_duration_seconds,
              COALESCE(c.child_total_rows_read, 0) AS child_total_rows_read,
              COALESCE(c.child_total_rows_written, 0) AS child_total_rows_written,
              COALESCE(c.child_total_bytes_added, 0) AS child_total_bytes_added,
              COALESCE(c.child_total_bytes_removed, 0) AS child_total_bytes_removed
            {from_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """,
            [*query_params, limit, offset],
        )
        rows = _result_rows(result)
    finally:
        conn.close()
    return rows, total, errors


def query_cached_filter_values(
    session: Session,
    paths: list[EnvironmentSource],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]]] | None:
    context = _cached_source_context(session, paths)
    if context is None:
        return None
    source_ids, errors = context
    if not source_ids:
        return {}, errors
    path = analytics_database_path()
    if not path.exists():
        return {}, errors
    _ensure_duckdb_cache_ready(path)
    conn = duckdb.connect(database=str(path), read_only=True)
    try:
        if not _table_exists(conn, FILTER_VALUES_TABLE):
            return {}, errors
        if not _table_has_source_rows(conn, FILTER_VALUES_TABLE, source_ids):
            return None
        placeholders = ", ".join("?" for _ in source_ids)
        result = conn.execute(
            f"""
            SELECT field, value, SUM(record_count) AS record_count
            FROM {FILTER_VALUES_TABLE}
            WHERE _source_id IN ({placeholders})
            GROUP BY field, value
            ORDER BY field, value
            """,
            source_ids,
        )
        values: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in _result_rows(result):
            values[str(row["field"])].append({
                "value": row["value"],
                "label": row["value"],
                "count": row["record_count"],
            })
        if "connection" not in values and _table_exists(conn, DATAFLOW_TABLE):
            values["connection"] = _query_connection_filter_values(conn, source_ids)
    finally:
        conn.close()
    return values, errors


def _upsert_duckdb_rows(
    source_id: int,
    dataflow_files: list[tuple[str, str, str]],
    job_rows: list[tuple[str, str, str, dict[str, Any]]],
    removed_files: list[str],
    changed_files: list[str],
) -> dict[str, Any]:
    path = analytics_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    parsed_dataflow_records = 0
    file_row_counts: dict[str, int] = {}
    errors: list[dict[str, str]] = []
    conn = duckdb.connect(database=str(path))
    try:
        _ensure_duckdb_tables(conn)
        stale_files = [*removed_files, *changed_files]
        for file_uri in stale_files:
            if _table_exists(conn, DATAFLOW_TABLE):
                conn.execute(f"DELETE FROM {DATAFLOW_TABLE} WHERE _source_id = ? AND _file_uri = ?", [source_id, file_uri])
            if _table_exists(conn, JOB_TABLE):
                conn.execute(f"DELETE FROM {JOB_TABLE} WHERE _source_id = ? AND _file_uri = ?", [source_id, file_uri])
        for file_uri, file_kind, revision_json in dataflow_files:
            try:
                row_count = _insert_dataflow_file(conn, source_id, file_uri, file_kind, revision_json)
                parsed_dataflow_records += row_count
                file_row_counts[file_uri] = row_count
            except Exception as exc:
                errors.append({"uri": file_uri, "message": str(exc)})
        if job_rows:
            _insert_typed_rows(conn, JOB_TABLE, source_id, job_rows, JOB_COLUMN_TYPES)
        _refresh_filter_values(conn, source_id)
    finally:
        conn.close()
    return {
        "parsed_dataflow_records": parsed_dataflow_records,
        "file_row_counts": file_row_counts,
        "errors": errors,
    }


def _read_duckdb_rows(source_ids: list[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    path = analytics_database_path()
    if not path.exists():
        return [], []
    _ensure_duckdb_cache_ready(path)
    conn = duckdb.connect(database=str(path), read_only=True)
    try:
        placeholders = ", ".join("?" for _ in source_ids)
        dataflows = _select_typed_rows(conn, DATAFLOW_TABLE, placeholders, source_ids)
        jobs = _select_typed_rows(conn, JOB_TABLE, placeholders, source_ids)
    finally:
        conn.close()
    dataflows.sort(key=lambda row: _sort_time(row.get("end_time") or row.get("start_time")), reverse=True)
    jobs.sort(key=lambda row: _sort_time(row.get("end_time") or row.get("start_time")), reverse=True)
    return dataflows, jobs


def _cached_source_context(
    session: Session,
    paths: list[EnvironmentSource],
) -> tuple[list[int], list[dict[str, str]]] | None:
    enabled = [path for path in paths if path.enabled]
    if not enabled:
        return [], []
    cached_ids = {
        int(row)
        for row in session.scalars(
            select(LogFileManifest.source_id).where(
                LogFileManifest.source_id.in_([path.id for path in enabled]),
                LogFileManifest.file_kind.in_(["dataflow_parquet", "job_jsonl"]),
            )
        ).all()
    }
    if not cached_ids:
        return None
    missing = [path for path in enabled if path.id not in cached_ids]
    errors = [
        {"uri": path.uri, "message": "ETL log path has no cache yet; run Sync now"}
        for path in missing
    ]
    return sorted(cached_ids), errors


def _monitoring_filter_sql(
    filters: dict[str, str],
    row_alias: str,
    job_alias: str,
    include_dataflow_filters: bool = True,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    range_value = filters.get("range")
    timestamp_expression = f"TRY_CAST(COALESCE({row_alias}.end_time, {row_alias}.start_time) AS TIMESTAMPTZ)"
    if range_value in {"24h", "3d", "7d", "30d", "90d"}:
        days = {"24h": 1, "3d": 3, "7d": 7, "30d": 30, "90d": 90}[range_value]
        clauses.append(f"{timestamp_expression} >= ?")
        params.append(datetime.now(timezone.utc) - timedelta(days=days))
    elif range_value == "custom":
        start_time = _parse_filter_datetime(filters.get("startTime"))
        end_time = _parse_filter_datetime(filters.get("endTime"))
        if start_time is not None:
            clauses.append(f"{timestamp_expression} >= ?")
            params.append(start_time)
        if end_time is not None:
            clauses.append(f"{timestamp_expression} <= ?")
            params.append(end_time)

    for key, expression in {
        "status": f"{row_alias}.status",
        "engine": f"COALESCE({job_alias}.engine_name, 'unknown')",
        "provider": f"COALESCE({job_alias}.metadata_provider_name, 'unknown')",
    }.items():
        value = filters.get(key)
        values = _split_filter_values(value)
        if values:
            placeholders = ", ".join("?" for _ in values)
            clauses.append(f"{expression} IN ({placeholders})")
            params.extend(values)

    if include_dataflow_filters:
        for key, expression in {
            "stage": f"{row_alias}.stage",
            "sourceType": f"{row_alias}.source_connection_type",
            "destinationType": f"{row_alias}.destination_connection_type",
            "loadType": f"{row_alias}.destination_load_type",
            "operationType": f"{row_alias}.operation_type",
        }.items():
            value = filters.get(key)
            values = _split_filter_values(value)
            if values:
                placeholders = ", ".join("?" for _ in values)
                clauses.append(f"{expression} IN ({placeholders})")
                params.extend(values)

    connection_sql, connection_params = _monitoring_connection_sql(filters, row_alias, include_dataflow_filters)
    if connection_sql:
        clauses.append(connection_sql)
        params.extend(connection_params)

    search = (filters.get("search") or "").strip().lower()
    if search:
        search_columns = (
            [
                f"{row_alias}.job_id",
                f"{row_alias}.dataflow_run_id",
                f"{row_alias}.dataflow_id",
                f"{row_alias}.dataflow_name",
                f"{row_alias}.stage",
                f"{row_alias}.error_message",
                f"{row_alias}.source_name",
                f"{row_alias}.source_full_table",
                f"REPLACE(COALESCE({row_alias}.source_full_table::VARCHAR, ''), '`', '')",
                f"{row_alias}.source_table",
                f"{row_alias}.source_path",
                f"{row_alias}.destination_name",
                f"{row_alias}.destination_full_table",
                f"REPLACE(COALESCE({row_alias}.destination_full_table::VARCHAR, ''), '`', '')",
                f"{row_alias}.destination_table",
                f"{row_alias}.destination_path",
                f"CONCAT(COALESCE({row_alias}.destination_name::VARCHAR, 'unknown'), '::', REPLACE(COALESCE({row_alias}.destination_full_table::VARCHAR, ''), '`', ''))",
                f"COALESCE({job_alias}.engine_name, 'unknown')",
                f"COALESCE({job_alias}.metadata_provider_name, 'unknown')",
            ]
            if include_dataflow_filters
            else [
                f"{row_alias}.job_id",
                f"{row_alias}.engine_name",
                f"{row_alias}.metadata_provider_name",
                f"{row_alias}.status",
                f"{row_alias}.error_message",
            ]
        )
        clauses.append("(" + " OR ".join(f"LOWER(COALESCE(({column})::VARCHAR, '')) LIKE ?" for column in search_columns) + ")")
        params.extend([f"%{search}%"] * len(search_columns))

    investigation_sql, investigation_params = _monitoring_investigation_sql(filters, row_alias, include_dataflow_filters)
    if investigation_sql:
        clauses.append(investigation_sql)
        params.extend(investigation_params)

    return (" AND " + " AND ".join(clauses), params) if clauses else ("", params)


def _monitoring_investigation_sql(
    filters: dict[str, str],
    row_alias: str,
    include_dataflow_filters: bool,
) -> tuple[str, list[Any]]:
    kind = (filters.get("investigateKind") or "").strip()
    value = (filters.get("investigateValue") or "").strip()
    if not kind or not value:
        return "", []
    normalized = value.lower().replace("`", "")
    if include_dataflow_filters:
        return _dataflow_investigation_predicate(row_alias, kind, normalized)
    if kind == "job_id":
        return f"LOWER(COALESCE({row_alias}.job_id::VARCHAR, '')) = ?", [normalized]
    dataflow_predicate, params = _dataflow_investigation_predicate("d2", kind, normalized)
    if not dataflow_predicate:
        return "", []
    return (
        f"{row_alias}.job_id IN ("
        f"SELECT DISTINCT d2.job_id FROM {DATAFLOW_TABLE} d2 "
        f"WHERE d2.job_id IS NOT NULL AND d2._source_id = {row_alias}._source_id AND {dataflow_predicate}"
        f")",
        params,
    )


def _dataflow_investigation_predicate(alias: str, kind: str, normalized_value: str) -> tuple[str, list[Any]]:
    if kind == "job_id":
        return f"LOWER(COALESCE({alias}.job_id::VARCHAR, '')) = ?", [normalized_value]
    if kind == "dataflow_run_id":
        return f"LOWER(COALESCE({alias}.dataflow_run_id::VARCHAR, '')) = ?", [normalized_value]
    if kind == "dataflow":
        return (
            "("
            f"LOWER(COALESCE({alias}.dataflow_id::VARCHAR, '')) = ? OR "
            f"LOWER(COALESCE({alias}.dataflow_name::VARCHAR, '')) = ?"
            ")",
            [normalized_value, normalized_value],
        )
    if kind == "destination_table":
        full_table_expr = f"LOWER(REPLACE(COALESCE({alias}.destination_full_table::VARCHAR, ''), '`', ''))"
        table_expr = f"LOWER(COALESCE({alias}.destination_table::VARCHAR, ''))"
        path_expr = f"LOWER(COALESCE({alias}.destination_path::VARCHAR, ''))"
        connection_expr = f"LOWER(COALESCE({alias}.destination_name::VARCHAR, 'unknown'))"
        return (
            "("
            f"{full_table_expr} = ? OR "
            f"{table_expr} = ? OR "
            f"{path_expr} = ? OR "
            f"CONCAT({connection_expr}, '::', {full_table_expr}) = ? OR "
            f"CONCAT({connection_expr}, '::', {table_expr}) = ? OR "
            f"CONCAT({connection_expr}, '::', {path_expr}) = ?"
            ")",
            [normalized_value] * 6,
        )
    return "", []


def _monitoring_connection_sql(
    filters: dict[str, str],
    row_alias: str,
    include_dataflow_filters: bool,
) -> tuple[str, list[Any]]:
    values = _split_filter_values(filters.get("connection"))
    if not values:
        return "", []
    placeholders = ", ".join("?" for _ in values)
    if include_dataflow_filters:
        return (
            f"(COALESCE({row_alias}.source_name, 'unknown') IN ({placeholders}) "
            f"OR COALESCE({row_alias}.destination_name, 'unknown') IN ({placeholders}))",
            [*values, *values],
        )
    return (
        f"{row_alias}.job_id IN ("
        f"SELECT DISTINCT dc.job_id FROM {DATAFLOW_TABLE} dc "
        f"WHERE dc.job_id IS NOT NULL AND dc._source_id = {row_alias}._source_id "
        f"AND (COALESCE(dc.source_name, 'unknown') IN ({placeholders}) "
        f"OR COALESCE(dc.destination_name, 'unknown') IN ({placeholders}))"
        f")",
        [*values, *values],
    )


def _split_filter_values(value: str | None) -> list[str]:
    if not value or value == "all":
        return []
    return [item.strip() for item in value.split("|") if item.strip()]


def _query_connection_filter_values(conn, source_ids: list[int]) -> list[dict[str, Any]]:
    if not source_ids:
        return []
    placeholders = ", ".join("?" for _ in source_ids)
    result = conn.execute(
        f"""
        SELECT connection_name AS value, COUNT(*) AS record_count
        FROM (
          SELECT DISTINCT _source_id, _file_uri, dataflow_run_id, job_id, connection_name
          FROM (
            SELECT _source_id, _file_uri, dataflow_run_id, job_id, TRIM(CAST(source_name AS VARCHAR)) AS connection_name
            FROM {DATAFLOW_TABLE}
            WHERE _source_id IN ({placeholders})
              AND source_name IS NOT NULL
              AND TRIM(CAST(source_name AS VARCHAR)) <> ''
            UNION ALL
            SELECT _source_id, _file_uri, dataflow_run_id, job_id, TRIM(CAST(destination_name AS VARCHAR)) AS connection_name
            FROM {DATAFLOW_TABLE}
            WHERE _source_id IN ({placeholders})
              AND destination_name IS NOT NULL
              AND TRIM(CAST(destination_name AS VARCHAR)) <> ''
          ) raw_connection_names
        ) connection_names
        GROUP BY connection_name
        ORDER BY connection_name
        """,
        [*source_ids, *source_ids],
    )
    return [
        {"value": row["value"], "label": row["value"], "count": row["record_count"]}
        for row in _result_rows(result)
    ]


def _monitoring_order_sql(
    sort_by: str,
    sort_dir: str,
    allowed_columns: dict[str, str],
    default_alias: str,
) -> str:
    expression = allowed_columns.get(sort_by) or allowed_columns["start_time"]
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"
    fallback_time = f"TRY_CAST(COALESCE({default_alias}.end_time, {default_alias}.start_time) AS TIMESTAMPTZ)"
    stable_identity = (
        f"COALESCE({default_alias}.dataflow_run_id, {default_alias}.dataflow_id, {default_alias}.job_id)"
        if default_alias == "d"
        else f"{default_alias}.job_id"
    )
    return f"{expression} {direction} NULLS LAST, {fallback_time} DESC NULLS LAST, {stable_identity} DESC NULLS LAST"


def _parse_filter_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _result_rows(result) -> list[dict[str, Any]]:
    names = [desc[0] for desc in result.description]
    rows = []
    for values in result.fetchall():
        row = _json_ready(dict(zip(names, values)))
        rows.append(row)
    return rows


def _select_alias_columns(alias: str, columns: list[str], exclude: set[str] | None = None) -> str:
    excluded = exclude or set()
    selected = [
        f"{alias}.{_quote_identifier(column)} AS {_quote_identifier(column)}"
        for column in columns
        if column not in excluded
    ]
    return ",\n              ".join(selected) if selected else f"{alias}.*"


def _upsert_manifest(
    session: Session,
    source_id: int,
    changed_files: list[dict[str, Any]],
    removed_files: list[str],
    file_row_counts: dict[str, int],
    checked_at: datetime,
) -> None:
    if removed_files:
        session.execute(
            delete(LogFileManifest).where(
                LogFileManifest.source_id == source_id,
                LogFileManifest.file_uri.in_(removed_files),
            )
        )
    for file_state in changed_files:
        file_uri = str(file_state["file_uri"])
        session.execute(
            delete(LogFileManifest).where(
                LogFileManifest.source_id == source_id,
                LogFileManifest.file_uri == file_uri,
            )
        )
        session.add(_manifest_row(source_id, file_state, file_row_counts.get(file_uri, 0), checked_at))


def _manifest_row(source_id: int, file_state: dict[str, Any], row_count: int, checked_at: datetime) -> LogFileManifest:
    return LogFileManifest(
        source_id=source_id,
        file_uri=str(file_state["file_uri"]),
        file_kind=str(file_state["file_kind"]),
        revision_json=str(file_state["revision_json"]),
        row_count=row_count,
        job_id=file_state.get("job_id"),
        log_timestamp=file_state.get("log_timestamp"),
        run_date=file_state.get("run_date"),
        status="ok",
        first_seen_at=checked_at,
        last_seen_at=checked_at,
    )


def _existing_manifest(session: Session, source_id: int) -> dict[str, str]:
    rows = session.scalars(select(LogFileManifest).where(LogFileManifest.source_id == source_id)).all()
    return {row.file_uri: row.revision_json for row in rows}


def _manifest_file_state(file_uri: str, file_kind: str) -> dict[str, Any]:
    metadata = parse_system_log_file_metadata(file_uri) if file_kind == "system_jsonl" else {}
    return {
        "file_uri": file_uri,
        "file_kind": file_kind,
        "revision_json": _file_revision_json(file_uri),
        "job_id": metadata.get("job_id"),
        "log_timestamp": metadata.get("log_timestamp"),
        "run_date": metadata.get("run_date"),
    }


def _file_revision_json(file_uri: str) -> str:
    stat = Path(file_uri).stat()
    return json.dumps({"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}, sort_keys=True)


def _read_dataflow_file(file_uri: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    conn = duckdb.connect(database=":memory:")
    try:
        escaped = file_uri.replace("'", "''")
        result = conn.execute(f"SELECT * FROM read_parquet('{escaped}', union_by_name=true)")
        names = [desc[0] for desc in result.description]
        return [_json_ready(dict(zip(names, row))) for row in result.fetchall()], []
    except Exception as exc:
        return [], [{"uri": file_uri, "message": str(exc)}]
    finally:
        conn.close()


def _read_job_file(file_uri: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    path = Path(file_uri)
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    rows.append(_json_ready(json.loads(line)))
                except json.JSONDecodeError as exc:
                    errors.append({"uri": file_uri, "message": f"Invalid JSONL at line {line_number}: {exc}"})
    except OSError as exc:
        errors.append({"uri": file_uri, "message": str(exc)})
    return rows, errors


def _count_jsonl_lines(file_uri: str) -> int:
    try:
        with Path(file_uri).open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def _json_ready(row: dict[str, Any]) -> dict[str, Any]:
    ready = {}
    for key, value in row.items():
        if isinstance(value, (datetime, date)):
            ready[key] = value.isoformat()
        else:
            ready[key] = value
    return ready


def _ensure_duckdb_tables(conn) -> None:
    recreated = [
        _ensure_dataflow_cache_table(conn) if _table_exists(conn, DATAFLOW_TABLE) else False,
        _ensure_typed_table(conn, JOB_TABLE, JOB_COLUMN_TYPES),
    ]
    _drop_empty_generated_job_columns(conn)
    if any(recreated) and _table_exists(conn, FILTER_VALUES_TABLE):
        conn.execute(f"DROP TABLE {FILTER_VALUES_TABLE}")
    _ensure_filter_values_table(conn)
    _migrate_legacy_cache(conn)


def _ensure_duckdb_cache_ready(path: Path) -> None:
    if _typed_cache_is_ready(path):
        return
    try:
        conn = duckdb.connect(database=str(path))
    except duckdb.Error:
        return
    try:
        _ensure_duckdb_tables(conn)
    finally:
        conn.close()


def _typed_cache_is_ready(path: Path) -> bool:
    try:
        conn = duckdb.connect(database=str(path), read_only=True)
    except duckdb.Error:
        return False
    try:
        return (
            _table_exists(conn, DATAFLOW_TABLE)
            and _table_exists(conn, JOB_TABLE)
            and _table_exists(conn, FILTER_VALUES_TABLE)
            and _typed_table_schema_is_current(conn, DATAFLOW_TABLE, DATAFLOW_COLUMN_TYPES)
            and _typed_table_schema_is_current(conn, JOB_TABLE, JOB_COLUMN_TYPES)
            and not _has_empty_generated_job_columns(conn)
            and not _table_exists(conn, LEGACY_DATAFLOW_TABLE)
            and not _table_exists(conn, LEGACY_JOB_TABLE)
        )
    finally:
        conn.close()


def _ensure_filter_values_table(conn) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {FILTER_VALUES_TABLE} (
          _source_id BIGINT,
          field VARCHAR,
          value VARCHAR,
          record_count BIGINT,
          _updated_at TIMESTAMPTZ
        )
        """
    )


def _refresh_filter_values(conn, source_id: int) -> None:
    _ensure_filter_values_table(conn)
    conn.execute(f"DELETE FROM {FILTER_VALUES_TABLE} WHERE _source_id = ?", [source_id])
    updated_at = utc_now().isoformat()
    for field, (table_name, column_name) in FILTER_VALUE_SOURCES.items():
        if not _table_exists(conn, table_name):
            continue
        if column_name not in _table_columns(conn, table_name):
            continue
        conn.execute(
            f"""
            INSERT INTO {FILTER_VALUES_TABLE}
            SELECT
              ?::BIGINT AS _source_id,
              ?::VARCHAR AS field,
              TRIM(CAST({_quote_identifier(column_name)} AS VARCHAR)) AS value,
              COUNT(*)::BIGINT AS record_count,
              ?::TIMESTAMPTZ AS _updated_at
            FROM {table_name}
            WHERE _source_id = ?
              AND {_quote_identifier(column_name)} IS NOT NULL
              AND TRIM(CAST({_quote_identifier(column_name)} AS VARCHAR)) <> ''
            GROUP BY TRIM(CAST({_quote_identifier(column_name)} AS VARCHAR))
            """,
            [source_id, field, updated_at, source_id],
        )
    if _table_exists(conn, DATAFLOW_TABLE):
        dataflow_columns = set(_table_columns(conn, DATAFLOW_TABLE))
        connection_selects = []
        identity_sql = ", ".join(
            [
                "_source_id",
                "_file_uri",
                "dataflow_run_id" if "dataflow_run_id" in dataflow_columns else "NULL::VARCHAR AS dataflow_run_id",
                "job_id" if "job_id" in dataflow_columns else "NULL::VARCHAR AS job_id",
            ]
        )
        for column_name in ("source_name", "destination_name"):
            if column_name not in dataflow_columns:
                continue
            quoted_column = _quote_identifier(column_name)
            connection_selects.append(
                f"""
                SELECT {identity_sql}, TRIM(CAST({quoted_column} AS VARCHAR)) AS connection_name
                FROM {DATAFLOW_TABLE}
                WHERE _source_id = ?
                  AND {quoted_column} IS NOT NULL
                  AND TRIM(CAST({quoted_column} AS VARCHAR)) <> ''
                """
            )
        if not connection_selects:
            return
        union_sql = "\nUNION ALL\n".join(connection_selects)
        conn.execute(
            f"""
            INSERT INTO {FILTER_VALUES_TABLE}
            SELECT
              ?::BIGINT AS _source_id,
              'connection'::VARCHAR AS field,
              connection_name AS value,
              COUNT(*)::BIGINT AS record_count,
              ?::TIMESTAMPTZ AS _updated_at
            FROM (
              SELECT DISTINCT _source_id, _file_uri, dataflow_run_id, job_id, connection_name
              FROM (
                {union_sql}
              ) raw_connection_names
            ) connection_names
            GROUP BY connection_name
            """,
            [source_id, updated_at, *([source_id] * len(connection_selects))],
        )


def _migrate_legacy_cache(conn) -> None:
    migrated_source_ids = set()
    migrated_source_ids.update(
        _migrate_legacy_table(
            conn,
            legacy_table=LEGACY_DATAFLOW_TABLE,
            target_table=DATAFLOW_TABLE,
            column_types=DATAFLOW_COLUMN_TYPES,
            file_kind="legacy_dataflow_json",
        )
    )
    migrated_source_ids.update(
        _migrate_legacy_table(
            conn,
            legacy_table=LEGACY_JOB_TABLE,
            target_table=JOB_TABLE,
            column_types=JOB_COLUMN_TYPES,
            file_kind="legacy_job_json",
        )
    )
    for source_id in sorted(migrated_source_ids):
        _refresh_filter_values(conn, source_id)


def _migrate_legacy_table(
    conn,
    legacy_table: str,
    target_table: str,
    column_types: dict[str, str],
    file_kind: str,
) -> set[int]:
    if not _table_exists(conn, legacy_table):
        return set()
    legacy_columns = set(_table_columns(conn, legacy_table))
    if not {"source_id", "file_uri", "row_json"} <= legacy_columns:
        return set()

    if not _table_exists(conn, target_table):
        _ensure_typed_table(conn, target_table, column_types)

    migrated_source_ids = set()
    source_ids = [
        int(row[0])
        for row in conn.execute(
            f"SELECT DISTINCT source_id FROM {legacy_table} WHERE source_id IS NOT NULL ORDER BY source_id"
        ).fetchall()
    ]
    for source_id in source_ids:
        legacy_count = conn.execute(
            f"SELECT count(*) FROM {legacy_table} WHERE source_id = ?",
            [source_id],
        ).fetchone()[0]
        target_count = conn.execute(f"SELECT count(*) FROM {target_table} WHERE _source_id = ?", [source_id]).fetchone()[0]
        if target_count == legacy_count:
            migrated_source_ids.add(source_id)
            continue
        conn.execute(f"DELETE FROM {target_table} WHERE _source_id = ?", [source_id])
        legacy_rows = conn.execute(
            f"SELECT file_uri, row_json FROM {legacy_table} WHERE source_id = ?",
            [source_id],
        ).fetchall()
        typed_rows = []
        for file_uri, row_json in legacy_rows:
            try:
                row = json.loads(row_json) if row_json else {}
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(row, dict):
                continue
            typed_rows.append((str(file_uri or ""), file_kind, "{}", row))
        if typed_rows:
            if not _table_exists(conn, target_table):
                _ensure_typed_table(conn, target_table, column_types)
            _insert_typed_rows(conn, target_table, source_id, typed_rows, column_types)
            migrated_source_ids.add(source_id)
    conn.execute(f"DROP TABLE {legacy_table}")
    return migrated_source_ids


def _insert_typed_rows(
    conn,
    table_name: str,
    source_id: int,
    rows: list[tuple[str, str, str, dict[str, Any]]],
    column_types: dict[str, str],
) -> None:
    _ensure_source_columns(conn, table_name, rows, column_types)
    columns = _table_columns(conn, table_name)
    insert_columns = [column for column in columns if column in STUDIO_CACHE_COLUMNS or any(column in row for _, _, _, row in rows)]
    placeholders = ", ".join("?" for _ in insert_columns)
    column_sql = ", ".join(_quote_identifier(column) for column in insert_columns)
    values = [
        [
            _cache_value(column, source_id, file_uri, file_kind, revision_json, row, column_types.get(column))
            for column in insert_columns
        ]
        for file_uri, file_kind, revision_json, row in rows
    ]
    conn.executemany(f"INSERT INTO {table_name} ({column_sql}) VALUES ({placeholders})", values)


def _insert_dataflow_file(conn, source_id: int, file_uri: str, file_kind: str, revision_json: str) -> int:
    _ensure_dataflow_table_for_parquet(conn, file_uri)
    escaped = file_uri.replace("'", "''")
    row_count = conn.execute(f"SELECT count(*) FROM read_parquet('{escaped}', union_by_name=true)").fetchone()[0]
    conn.execute(
        f"""
        INSERT INTO {DATAFLOW_TABLE} BY NAME
        SELECT
          {int(source_id)}::BIGINT AS _source_id,
          {_sql_string(file_uri)} AS _file_uri,
          {_sql_string(file_kind)} AS _file_kind,
          {_sql_date(_file_date(file_uri, {}))} AS _file_date,
          {_sql_number(_revision_value(revision_json, "size"))}::BIGINT AS _source_size,
          {_sql_number(_revision_value(revision_json, "mtime_ns"))}::BIGINT AS _source_mtime_ns,
          {_sql_string(utc_now().isoformat())}::TIMESTAMPTZ AS _ingested_at,
          *
        FROM read_parquet('{escaped}', union_by_name=true)
        """
    )
    return int(row_count)


def _select_typed_rows(conn, table_name: str, placeholders: str, source_ids: list[int]) -> list[dict[str, Any]]:
    if not _table_exists(conn, table_name):
        return []
    result = conn.execute(f"SELECT * FROM {table_name} WHERE _source_id IN ({placeholders})", source_ids)
    names = [desc[0] for desc in result.description]
    rows = []
    for values in result.fetchall():
        row = _json_ready(dict(zip(names, values)))
        rows.append(row)
    return rows


def _ensure_typed_table(conn, table_name: str, column_types: dict[str, str]) -> bool:
    columns = _table_columns(conn, table_name)
    if columns and ("_source_id" not in columns or _has_legacy_raw_json_column(columns) or _has_incompatible_column_types(conn, table_name, column_types)):
        conn.execute(f"DROP TABLE {table_name}")
        columns = []
    if not columns:
        definitions = [
            f"{_quote_identifier(column)} {data_type}"
            for column, data_type in _cache_table_column_types(column_types).items()
        ]
        conn.execute(f"CREATE TABLE {table_name} ({', '.join(definitions)})")
        return True
    _ensure_columns(conn, table_name, _cache_table_column_types(column_types), set(columns))
    _ensure_column_order(conn, table_name, column_types)
    return False


def _ensure_dataflow_cache_table(conn) -> bool:
    columns = _table_columns(conn, DATAFLOW_TABLE)
    if columns and ("_source_id" not in columns or _has_legacy_raw_json_column(columns)):
        conn.execute(f"DROP TABLE {DATAFLOW_TABLE}")
        return True
    if not columns:
        return False
    existing = set(columns)
    _ensure_columns(conn, DATAFLOW_TABLE, STUDIO_CACHE_COLUMNS, existing)
    _ensure_column_order(conn, DATAFLOW_TABLE, _actual_source_column_types(conn, DATAFLOW_TABLE))
    return False


def _cache_table_column_types(column_types: dict[str, str]) -> dict[str, str]:
    return {**column_types, **STUDIO_CACHE_COLUMNS}


def _ensure_source_columns(
    conn,
    table_name: str,
    rows: list[tuple[str, str, str, dict[str, Any]]],
    column_types: dict[str, str],
) -> None:
    existing = set(_table_columns(conn, table_name))
    discovered: dict[str, str] = {}
    for _, _, _, row in rows:
        for column, value in row.items():
            if column in existing or column in STUDIO_CACHE_COLUMNS:
                continue
            discovered[column] = column_types.get(column) or _infer_duckdb_type(value)
    if discovered:
        _ensure_columns(conn, table_name, discovered, existing)
        _ensure_column_order(conn, table_name, {**column_types, **discovered})


def _ensure_dataflow_table_for_parquet(conn, file_uri: str) -> None:
    described = _describe_parquet_columns(conn, file_uri)
    parquet_column_types = {
        name: data_type
        for name, data_type in described
        if name not in STUDIO_CACHE_COLUMNS
    }
    if not _table_exists(conn, DATAFLOW_TABLE):
        definitions = [
            f"{_quote_identifier(column)} {data_type}"
            for column, data_type in _cache_table_column_types(parquet_column_types).items()
        ]
        conn.execute(f"CREATE TABLE {DATAFLOW_TABLE} ({', '.join(definitions)})")
        return
    existing = set(_table_columns(conn, DATAFLOW_TABLE))
    discovered = {
        column: data_type
        for column, data_type in parquet_column_types.items()
        if column not in existing
    }
    if discovered:
        _ensure_columns(conn, DATAFLOW_TABLE, discovered, existing)
    _ensure_column_order(conn, DATAFLOW_TABLE, parquet_column_types)


def _describe_parquet_columns(conn, file_uri: str) -> list[tuple[str, str]]:
    escaped = file_uri.replace("'", "''")
    described = conn.execute(f"DESCRIBE SELECT * FROM read_parquet('{escaped}', union_by_name=true)").fetchall()
    return [(str(row[0]), str(row[1])) for row in described]


def _ensure_columns(conn, table_name: str, column_types: dict[str, str], existing: set[str]) -> None:
    for column, data_type in column_types.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {_quote_identifier(column)} {data_type}")
            existing.add(column)


def _ensure_column_order(conn, table_name: str, source_column_types: dict[str, str]) -> None:
    actual_columns = _table_columns(conn, table_name)
    if not actual_columns:
        return
    expected_columns = _expected_column_order(actual_columns, source_column_types)
    if actual_columns == expected_columns:
        return
    actual_types = _table_column_types(conn, table_name)
    expected_types = _cache_table_column_types(source_column_types)
    temp_table = f"{table_name}__column_order"
    conn.execute(f"DROP TABLE IF EXISTS {temp_table}")
    definitions = [
        f"{_quote_identifier(column)} {actual_types.get(column) or expected_types.get(column) or 'VARCHAR'}"
        for column in expected_columns
    ]
    conn.execute(f"CREATE TABLE {temp_table} ({', '.join(definitions)})")
    common_columns = [column for column in expected_columns if column in actual_columns]
    if common_columns:
        column_sql = ", ".join(_quote_identifier(column) for column in common_columns)
        conn.execute(f"INSERT INTO {temp_table} ({column_sql}) SELECT {column_sql} FROM {table_name}")
    conn.execute(f"DROP TABLE {table_name}")
    conn.execute(f"ALTER TABLE {temp_table} RENAME TO {table_name}")


def _expected_column_order(actual_columns: list[str], source_column_types: dict[str, str]) -> list[str]:
    actual = set(actual_columns)
    source_columns = [column for column in source_column_types if column in actual]
    extra_source_columns = [
        column
        for column in actual_columns
        if column not in source_column_types and column not in STUDIO_CACHE_COLUMNS
    ]
    studio_columns = [column for column in STUDIO_CACHE_COLUMNS if column in actual]
    return [*source_columns, *extra_source_columns, *studio_columns]


def _actual_source_column_types(conn, table_name: str) -> dict[str, str]:
    actual_types = _table_column_types(conn, table_name)
    return {
        column: actual_types[column]
        for column in _table_columns(conn, table_name)
        if column not in STUDIO_CACHE_COLUMNS and column in actual_types
    }


def _table_exists(conn, table_name: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {table_name} LIMIT 0")
        return True
    except duckdb.CatalogException:
        return False


def _table_columns(conn, table_name: str) -> list[str]:
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()]
    except duckdb.CatalogException:
        return []


def _table_column_types(conn, table_name: str) -> dict[str, str]:
    try:
        return {str(row[1]): str(row[2]) for row in conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()}
    except duckdb.CatalogException:
        return {}


def _drop_empty_generated_job_columns(conn) -> None:
    """Remove columns that older studio cache versions generated for jobs."""
    if not _table_exists(conn, JOB_TABLE):
        return
    columns = set(_table_columns(conn, JOB_TABLE))
    for column in ("operation_type",):
        if column not in columns:
            continue
        quoted_column = _quote_identifier(column)
        try:
            non_null_count = conn.execute(
                f"SELECT count(*) FROM {JOB_TABLE} WHERE {quoted_column} IS NOT NULL"
            ).fetchone()[0]
            if int(non_null_count or 0) == 0:
                conn.execute(f"ALTER TABLE {JOB_TABLE} DROP COLUMN {quoted_column}")
        except duckdb.Error:
            continue


def _has_empty_generated_job_columns(conn) -> bool:
    if not _table_exists(conn, JOB_TABLE):
        return False
    columns = set(_table_columns(conn, JOB_TABLE))
    for column in ("operation_type",):
        if column not in columns:
            continue
        quoted_column = _quote_identifier(column)
        try:
            non_null_count = conn.execute(
                f"SELECT count(*) FROM {JOB_TABLE} WHERE {quoted_column} IS NOT NULL"
            ).fetchone()[0]
        except duckdb.Error:
            return False
        if int(non_null_count or 0) == 0:
            return True
    return False


def _typed_table_schema_is_current(conn, table_name: str, column_types: dict[str, str]) -> bool:
    columns = _table_columns(conn, table_name)
    if table_name == DATAFLOW_TABLE:
        return (
            bool(columns)
            and "_source_id" in columns
            and not _has_legacy_raw_json_column(columns)
            and not _has_incompatible_column_types(conn, table_name, {})
            and not _has_column_order_mismatch(conn, table_name, _actual_source_column_types(conn, table_name))
        )
    return (
        bool(columns)
        and "_source_id" in columns
        and not _has_legacy_raw_json_column(columns)
        and not _has_incompatible_column_types(conn, table_name, column_types)
        and not _has_column_order_mismatch(conn, table_name, column_types)
    )


def _has_legacy_raw_json_column(columns: list[str]) -> bool:
    return "_raw_json" in columns


def _has_column_order_mismatch(conn, table_name: str, column_types: dict[str, str]) -> bool:
    columns = _table_columns(conn, table_name)
    return bool(columns) and columns != _expected_column_order(columns, column_types)


def _has_incompatible_column_types(conn, table_name: str, column_types: dict[str, str]) -> bool:
    actual_types = _table_column_types(conn, table_name)
    expected_types = {**STUDIO_CACHE_COLUMNS, **column_types}
    for column, expected_type in expected_types.items():
        actual_type = actual_types.get(column)
        if actual_type and not _duckdb_type_matches(actual_type, expected_type):
            return True
    return False


def _duckdb_type_matches(actual_type: str, expected_type: str) -> bool:
    actual = actual_type.upper()
    expected = expected_type.upper()
    if expected == "TIMESTAMPTZ":
        return actual in {"TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE"}
    return actual == expected or actual.startswith(f"{expected}(")


def _table_row_count(conn, table_name: str) -> int:
    if not _table_exists(conn, table_name):
        return 0
    return int(conn.execute(f"SELECT count(*) FROM {table_name}").fetchone()[0])


def _table_source_ids(conn, table_name: str) -> list[int]:
    if not _table_exists(conn, table_name):
        return []
    if "_source_id" not in _table_columns(conn, table_name):
        return []
    rows = conn.execute(
        f"SELECT DISTINCT _source_id FROM {table_name} WHERE _source_id IS NOT NULL ORDER BY _source_id"
    ).fetchall()
    return [int(row[0]) for row in rows]


def _table_has_source_rows(conn, table_name: str, source_ids: list[int]) -> bool:
    if not source_ids or not _table_exists(conn, table_name):
        return False
    if "_source_id" not in _table_columns(conn, table_name):
        return False
    placeholders = ", ".join("?" for _ in source_ids)
    count = conn.execute(
        f"SELECT count(*) FROM {table_name} WHERE _source_id IN ({placeholders})",
        source_ids,
    ).fetchone()[0]
    return int(count or 0) > 0


def _delete_rows_by_source_ids(conn, table_name: str, source_ids: list[int]) -> int:
    if not source_ids or not _table_exists(conn, table_name):
        return 0
    if "_source_id" not in _table_columns(conn, table_name):
        return 0
    placeholders = ", ".join("?" for _ in source_ids)
    row_count = int(
        conn.execute(
            f"SELECT count(*) FROM {table_name} WHERE _source_id IN ({placeholders})",
            source_ids,
        ).fetchone()[0]
    )
    if row_count:
        conn.execute(f"DELETE FROM {table_name} WHERE _source_id IN ({placeholders})", source_ids)
    return row_count


def _cache_value(
    column: str,
    source_id: int,
    file_uri: str,
    file_kind: str,
    revision_json: str,
    row: dict[str, Any],
    data_type: str | None,
) -> Any:
    if column == "_source_id":
        return source_id
    if column == "_file_uri":
        return file_uri
    if column == "_file_kind":
        return file_kind
    if column == "_file_date":
        return _file_date(file_uri, row)
    if column == "_source_size":
        return _revision_value(revision_json, "size")
    if column == "_source_mtime_ns":
        return _revision_value(revision_json, "mtime_ns")
    if column == "_ingested_at":
        return utc_now().isoformat()
    return _typed_value(row.get(column), data_type)


def _typed_value(value: Any, data_type: str | None) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    if data_type == "VARCHAR":
        return str(value)
    if data_type == "DATE" and isinstance(value, str):
        return value[:10]
    return value


def _revision_value(revision_json: str, key: str) -> Any:
    try:
        revision = json.loads(revision_json)
    except json.JSONDecodeError:
        return None
    return revision.get(key)


def _file_date(file_uri: str, row: dict[str, Any]) -> str | None:
    if row.get("__run_date"):
        return str(row["__run_date"])[:10]
    for key in ("end_time", "start_time"):
        if row.get(key):
            return str(row[key])[:10]
    match = re.search(r"(20\d{2}[-_/]\d{2}[-_/]\d{2})", file_uri)
    return match.group(1).replace("_", "-").replace("/", "-") if match else None


def _infer_duckdb_type(value: Any) -> str:
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int) and not isinstance(value, bool):
        return "BIGINT"
    if isinstance(value, float):
        return "DOUBLE"
    if isinstance(value, (datetime, date)):
        return "TIMESTAMPTZ" if isinstance(value, datetime) else "DATE"
    return "VARCHAR"


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _sql_string(value: Any) -> str:
    if value is None:
        return "NULL::VARCHAR"
    return "'" + str(value).replace("'", "''") + "'"


def _sql_date(value: str | None) -> str:
    if not value:
        return "NULL::DATE"
    return f"DATE {_sql_string(value[:10])}"


def _sql_number(value: Any) -> str:
    return "NULL" if value is None else str(int(value))


def _revision_error(source: EnvironmentSource, revision: dict[str, Any]) -> dict[str, Any] | None:
    if revision.get("object_type") == "provider_not_enabled":
        provider = str(revision.get("provider") or "storage")
        return {"message": f"{provider.upper()} storage URI is recognized but not enabled yet: {source.uri}", "code": "provider_not_enabled"}
    if not revision.get("exists"):
        return {"message": f"ETL log path not found: {source.uri}", "code": "not_found"}
    if revision.get("object_type") != "directory":
        return {"message": f"ETL log source must be a directory: {source.uri}", "code": "invalid_type"}
    return None


def _enrich_dataflow(row: dict[str, Any], job: dict[str, Any] | None) -> dict[str, Any]:
    if not job:
        return {**row, "engine_name": "unknown", "metadata_provider_name": "unknown", "platform_name": "unknown"}
    return {
        **row,
        "engine_name": job.get("engine_name") or "unknown",
        "metadata_provider_name": job.get("metadata_provider_name") or "unknown",
        "platform_name": job.get("platform_name") or "unknown",
        "job_status": job.get("status"),
        "job_duration_seconds": job.get("duration_seconds"),
    }


def _sort_time(value: object) -> datetime:
    return utc_datetime_sort_key(value)
