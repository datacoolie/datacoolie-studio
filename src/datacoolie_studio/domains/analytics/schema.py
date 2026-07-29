from __future__ import annotations

import duckdb

from datacoolie_studio.domains.analytics.serving_facts import (
    monitoring_serving_schema_is_ready,
)


STUDIO_CACHE_COLUMNS = {
    "_source_id": "BIGINT",
    "_file_uri": "VARCHAR",
    "_file_kind": "VARCHAR",
    "_file_date": "DATE",
    "_source_size": "BIGINT",
    "_source_mtime_ns": "BIGINT",
    "_ingested_at": "TIMESTAMPTZ",
}
GENERATED_CACHE_COLUMNS = {"__event_time", "__run_date"}

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
    "__event_time": "TIMESTAMPTZ",
    "__run_date": "DATE",
}


DATAFLOW_TABLE = "etl_dataflow_runs"
JOB_TABLE = "etl_job_runs"
FILTER_VALUES_TABLE = "etl_monitoring_filter_values"
CACHE_SOURCES_TABLE = "etl_cache_sources"
ANALYTICS_META_TABLE = "etl_analytics_meta"
ANALYTICS_SCHEMA_VERSION = 6
LEGACY_DATAFLOW_TABLE = "etl_dataflow_run_cache"
LEGACY_JOB_TABLE = "etl_job_run_cache"

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



def cache_table_column_types(column_types: dict[str, str]) -> dict[str, str]:
    return {**column_types, **STUDIO_CACHE_COLUMNS}


def table_exists(conn, table_name: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {table_name} LIMIT 0")
        return True
    except duckdb.CatalogException:
        return False


def table_columns(conn, table_name: str) -> list[str]:
    try:
        return [
            row[1]
            for row in conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        ]
    except duckdb.CatalogException:
        return []


def table_column_types(conn, table_name: str) -> dict[str, str]:
    try:
        return {
            str(row[1]): str(row[2])
            for row in conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        }
    except duckdb.CatalogException:
        return {}


def duckdb_type_matches(actual_type: str, expected_type: str) -> bool:
    actual = actual_type.upper()
    expected = expected_type.upper()
    if expected == "TIMESTAMPTZ":
        return actual in {"TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE"}
    return actual == expected or actual.startswith(f"{expected}(")


def ensure_filter_values_table(conn) -> None:
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


def ensure_cache_sources_table(conn) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {CACHE_SOURCES_TABLE} (
          source_id BIGINT PRIMARY KEY,
          refreshed_at TIMESTAMPTZ,
          generation BIGINT NOT NULL DEFAULT 0
        )
        """
    )
    if "generation" not in table_columns(conn, CACHE_SOURCES_TABLE):
        conn.execute(
            f"ALTER TABLE {CACHE_SOURCES_TABLE} ADD COLUMN generation BIGINT DEFAULT 0"
        )


def ensure_analytics_meta_table(conn) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {ANALYTICS_META_TABLE} (
          singleton_id INTEGER PRIMARY KEY,
          schema_version INTEGER NOT NULL,
          generation BIGINT NOT NULL,
          build_state VARCHAR NOT NULL,
          published_at TIMESTAMPTZ
        )
        """
    )
    if conn.execute(f"SELECT COUNT(*) FROM {ANALYTICS_META_TABLE}").fetchone()[0] == 0:
        conn.execute(
            f"""
            INSERT INTO {ANALYTICS_META_TABLE}
              (singleton_id, schema_version, generation, build_state, published_at)
            VALUES (1, ?, 0, 'rebuild_required', NULL)
            """,
            [ANALYTICS_SCHEMA_VERSION],
        )


def expected_column_order(
    actual_columns: list[str],
    source_column_types: dict[str, str],
) -> list[str]:
    actual = set(actual_columns)
    source_columns = [column for column in source_column_types if column in actual]
    extra_source_columns = [
        column
        for column in actual_columns
        if column not in source_column_types and column not in STUDIO_CACHE_COLUMNS
    ]
    studio_columns = [column for column in STUDIO_CACHE_COLUMNS if column in actual]
    return [*source_columns, *extra_source_columns, *studio_columns]


def actual_source_column_types(conn, table_name: str) -> dict[str, str]:
    actual_types = table_column_types(conn, table_name)
    return {
        column: actual_types[column]
        for column in table_columns(conn, table_name)
        if column not in STUDIO_CACHE_COLUMNS and column in actual_types
    }


def has_legacy_raw_json_column(columns: list[str]) -> bool:
    return "_raw_json" in columns


def has_column_order_mismatch(
    conn,
    table_name: str,
    column_types: dict[str, str],
) -> bool:
    columns = table_columns(conn, table_name)
    return bool(columns) and columns != expected_column_order(columns, column_types)


def has_incompatible_column_types(
    conn,
    table_name: str,
    column_types: dict[str, str],
) -> bool:
    actual_types = table_column_types(conn, table_name)
    expected_types = {**STUDIO_CACHE_COLUMNS, **column_types}
    return any(
        actual_type
        and not duckdb_type_matches(actual_type, expected_type)
        for column, expected_type in expected_types.items()
        if (actual_type := actual_types.get(column))
    )


def typed_table_schema_is_current(
    conn,
    table_name: str,
    column_types: dict[str, str],
) -> bool:
    columns = table_columns(conn, table_name)
    if table_name == DATAFLOW_TABLE:
        actual_source_types = actual_source_column_types(conn, table_name)
        return (
            bool(columns)
            and "_source_id" in columns
            and not has_legacy_raw_json_column(columns)
            and not has_incompatible_column_types(conn, table_name, {})
            and not has_column_order_mismatch(
                conn,
                table_name,
                actual_source_types,
            )
        )
    return (
        bool(columns)
        and "_source_id" in columns
        and set(column_types).issubset(columns)
        and not has_legacy_raw_json_column(columns)
        and not has_incompatible_column_types(conn, table_name, column_types)
        and not has_column_order_mismatch(conn, table_name, column_types)
    )


def has_empty_generated_job_columns(conn) -> bool:
    if not table_exists(conn, JOB_TABLE):
        return False
    columns = set(table_columns(conn, JOB_TABLE))
    for column in ("operation_type",):
        if column not in columns:
            continue
        quoted_column = quote_identifier(column)
        try:
            non_null_count = conn.execute(
                f"SELECT count(*) FROM {JOB_TABLE} "
                f"WHERE {quoted_column} IS NOT NULL"
            ).fetchone()[0]
        except duckdb.Error:
            return False
        if int(non_null_count or 0) == 0:
            return True
    return False


def typed_cache_schema_is_ready(conn) -> bool:
    return (
        table_exists(conn, DATAFLOW_TABLE)
        and table_exists(conn, JOB_TABLE)
        and table_exists(conn, FILTER_VALUES_TABLE)
        and table_exists(conn, CACHE_SOURCES_TABLE)
        and table_exists(conn, ANALYTICS_META_TABLE)
        and "generation" in table_columns(conn, CACHE_SOURCES_TABLE)
        and typed_table_schema_is_current(
            conn,
            DATAFLOW_TABLE,
            DATAFLOW_COLUMN_TYPES,
        )
        and typed_table_schema_is_current(conn, JOB_TABLE, JOB_COLUMN_TYPES)
        and monitoring_serving_schema_is_ready(conn)
        and not has_empty_generated_job_columns(conn)
        and not table_exists(conn, LEGACY_DATAFLOW_TABLE)
        and not table_exists(conn, LEGACY_JOB_TABLE)
    )


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
