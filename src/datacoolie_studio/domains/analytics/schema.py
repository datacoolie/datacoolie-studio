from __future__ import annotations

import duckdb


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

DATAFLOW_TABLE = "etl_dataflow_runs"
JOB_TABLE = "etl_job_runs"
FILTER_VALUES_TABLE = "etl_monitoring_filter_values"
CACHE_SOURCES_TABLE = "etl_cache_sources"
ANALYTICS_META_TABLE = "etl_analytics_meta"
INGEST_CHECKPOINT_TABLE = "log_ingest_checkpoint"
INGEST_MANIFEST_TABLE = "log_ingest_file_manifest"
ANALYTICS_SCHEMA_VERSION = 5
LEGACY_DATAFLOW_TABLE = "etl_dataflow_run_cache"
LEGACY_JOB_TABLE = "etl_job_run_cache"


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
