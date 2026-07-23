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


def ensure_ingest_control_tables(conn) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {INGEST_CHECKPOINT_TABLE} (
          source_id BIGINT NOT NULL,
          log_kind VARCHAR NOT NULL,
          partition_format VARCHAR NOT NULL,
          partition_value DATE NOT NULL,
          boundary_last_modified TIMESTAMPTZ NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL,
          PRIMARY KEY (source_id, log_kind)
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {INGEST_MANIFEST_TABLE} (
          source_id BIGINT NOT NULL,
          log_kind VARCHAR NOT NULL,
          file_uri VARCHAR NOT NULL,
          partition_value DATE NOT NULL,
          partition_format VARCHAR NOT NULL,
          revision_json VARCHAR NOT NULL,
          row_count BIGINT NOT NULL,
          ingested_at TIMESTAMPTZ NOT NULL,
          PRIMARY KEY (source_id, log_kind, file_uri)
        )
        """
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
