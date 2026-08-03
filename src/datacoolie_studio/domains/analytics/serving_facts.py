"""Persisted projections consumed by Monitoring read models."""

from __future__ import annotations

from typing import Any


MONITORING_DATAFLOW_FACTS_TABLE = "monitoring_dataflow_facts"
MONITORING_JOB_FACTS_TABLE = "monitoring_job_facts"

DATAFLOW_FACT_COLUMNS = (
    "_source_id",
    "job_id",
    "dataflow_id",
    "dataflow_run_id",
    "workspace_id",
    "dataflow_name",
    "dataflow_description",
    "stage",
    "group_number",
    "execution_order",
    "processing_mode",
    "is_active",
    "configure",
    "operation_type",
    "status",
    "start_time",
    "end_time",
    "duration_seconds",
    "source_id",
    "source_name",
    "source_connection_type",
    "source_format",
    "source_catalog",
    "source_database",
    "source_schema",
    "source_table",
    "source_full_table",
    "source_path",
    "source_query",
    "source_python_function",
    "source_filter_expression",
    "source_configure",
    "source_action",
    "source_status",
    "source_error_message",
    "source_duration_seconds",
    "source_rows_read",
    "source_watermark_before",
    "source_watermark_after",
    "source_watermark_effective",
    "source_watermark_columns",
    "transform_status",
    "transform_error_message",
    "transform_duration_seconds",
    "transform_deduplicate_columns",
    "transform_latest_data_columns",
    "transform_filter_expression",
    "transform_additional_columns",
    "transform_schema_hints",
    "transform_select_columns",
    "transform_drop_columns",
    "transform_rename_columns",
    "transform_value_rules",
    "transform_hash_columns",
    "transform_masking_rules",
    "transform_configure",
    "destination_id",
    "destination_name",
    "destination_connection_type",
    "destination_format",
    "destination_catalog",
    "destination_database",
    "destination_schema",
    "destination_table",
    "destination_full_table",
    "destination_path",
    "destination_load_type",
    "destination_merge_keys",
    "destination_partition_columns",
    "destination_configure",
    "destination_status",
    "destination_error_message",
    "destination_duration_seconds",
    "destination_operation_type",
    "destination_rows_written",
    "destination_rows_inserted",
    "destination_rows_updated",
    "destination_rows_deleted",
    "destination_files_added",
    "destination_files_removed",
    "destination_bytes_added",
    "destination_bytes_removed",
    "destination_bytes_saved",
    "overhead_duration_seconds",
    "error_message",
    "__event_time",
    "__run_date",
)

JOB_FACT_COLUMNS = (
    "_source_id",
    "job_id",
    "status",
    "start_time",
    "end_time",
    "duration_seconds",
    "engine_name",
    "metadata_provider_name",
    "platform_name",
    "stages",
    "operation_types",
    "total_dataflows",
    "total_succeeded",
    "total_failed",
    "total_skipped",
    "total_running",
    "total_pending",
    "total_rows_read",
    "total_rows_written",
    "error_message",
    "__event_time",
    "__run_date",
)

DATAFLOW_DERIVED_COLUMNS = (
    "engine_name",
    "metadata_provider_name",
    "platform_name",
    "normalized_status",
    "event_time",
    "run_date",
)
JOB_DERIVED_COLUMNS = ("normalized_status", "event_time", "run_date")


def rebuild_monitoring_serving_facts(
    conn: Any,
    *,
    dataflow_table: str,
    job_table: str,
    dataflow_column_types: dict[str, str],
    job_column_types: dict[str, str],
) -> None:
    """Rebuild deterministic, read-optimized Monitoring facts in the active transaction."""
    dataflow_source_projection = _projection(
        "raw",
        DATAFLOW_FACT_COLUMNS,
        available=_table_columns(conn, dataflow_table),
        column_types=dataflow_column_types,
    )
    dataflow_projection = ",\n          ".join(
        f'd."{column}" AS "{column}"' for column in DATAFLOW_FACT_COLUMNS
    )
    job_projection = _projection(
        "raw",
        JOB_FACT_COLUMNS,
        available=_table_columns(conn, job_table),
        column_types=job_column_types,
    )
    conn.execute(
        f"""
        CREATE OR REPLACE TABLE {MONITORING_JOB_FACTS_TABLE} AS
        SELECT
          {job_projection},
          LOWER(COALESCE(NULLIF(TRIM(CAST(raw.status AS VARCHAR)), ''), 'unknown'))
            AS normalized_status,
          raw.__event_time AS event_time,
          COALESCE(
            raw.__run_date,
            CAST(timezone('UTC', raw.__event_time) AS DATE)
          ) AS run_date
        FROM {job_table} raw
        ORDER BY raw._source_id, run_date, event_time, raw.job_id
        """
    )
    conn.execute(
        f"""
        CREATE OR REPLACE TABLE {MONITORING_DATAFLOW_FACTS_TABLE} AS
        WITH dataflow_source AS (
          SELECT {dataflow_source_projection}
          FROM {dataflow_table} raw
        ),
        job_context AS (
          SELECT
            _source_id,
            job_id,
            ANY_VALUE(engine_name) AS engine_name,
            ANY_VALUE(metadata_provider_name) AS metadata_provider_name,
            ANY_VALUE(platform_name) AS platform_name
          FROM {MONITORING_JOB_FACTS_TABLE}
          WHERE job_id IS NOT NULL
          GROUP BY _source_id, job_id
        )
        SELECT
          {dataflow_projection},
          COALESCE(j.engine_name, 'unknown') AS engine_name,
          COALESCE(j.metadata_provider_name, 'unknown') AS metadata_provider_name,
          COALESCE(j.platform_name, 'unknown') AS platform_name,
          LOWER(COALESCE(NULLIF(TRIM(CAST(d.status AS VARCHAR)), ''), 'unknown'))
            AS normalized_status,
          d.__event_time AS event_time,
          COALESCE(
            d.__run_date,
            CAST(timezone('UTC', d.__event_time) AS DATE)
          ) AS run_date
        FROM dataflow_source d
        LEFT JOIN job_context j
          ON j._source_id = d._source_id AND j.job_id = d.job_id
        ORDER BY d._source_id, run_date, event_time, d.dataflow_id, d.stage
        """
    )


def validate_monitoring_serving_facts(
    conn: Any,
    *,
    dataflow_table: str,
    job_table: str,
) -> None:
    if not monitoring_serving_schema_is_ready(conn):
        raise RuntimeError("Monitoring serving facts do not match the current schema")
    source_counts = conn.execute(
        f"""
        SELECT
          (SELECT COUNT(*) FROM {dataflow_table}) AS source_dataflows,
          (SELECT COUNT(*) FROM {MONITORING_DATAFLOW_FACTS_TABLE}) AS serving_dataflows,
          (SELECT COUNT(*) FROM {job_table}) AS source_jobs,
          (SELECT COUNT(*) FROM {MONITORING_JOB_FACTS_TABLE}) AS serving_jobs
        """
    ).fetchone()
    if source_counts[0] != source_counts[1] or source_counts[2] != source_counts[3]:
        raise RuntimeError("Monitoring serving-fact row counts do not reconcile")
    invalid_dataflows = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM {MONITORING_DATAFLOW_FACTS_TABLE}
        WHERE normalized_status IS DISTINCT FROM
              LOWER(COALESCE(NULLIF(TRIM(CAST(status AS VARCHAR)), ''), 'unknown'))
           OR event_time IS DISTINCT FROM __event_time
           OR run_date IS DISTINCT FROM COALESCE(
                __run_date,
                CAST(timezone('UTC', __event_time) AS DATE)
              )
        """
    ).fetchone()[0]
    invalid_jobs = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM {MONITORING_JOB_FACTS_TABLE}
        WHERE normalized_status IS DISTINCT FROM
              LOWER(COALESCE(NULLIF(TRIM(CAST(status AS VARCHAR)), ''), 'unknown'))
           OR event_time IS DISTINCT FROM __event_time
           OR run_date IS DISTINCT FROM COALESCE(
                __run_date,
                CAST(timezone('UTC', __event_time) AS DATE)
              )
        """
    ).fetchone()[0]
    if invalid_dataflows or invalid_jobs:
        raise RuntimeError("Monitoring serving-fact derived columns do not reconcile")


def monitoring_serving_schema_is_ready(conn: Any) -> bool:
    return _table_columns(conn, MONITORING_DATAFLOW_FACTS_TABLE) == {
        *DATAFLOW_FACT_COLUMNS,
        *DATAFLOW_DERIVED_COLUMNS,
    } and _table_columns(conn, MONITORING_JOB_FACTS_TABLE) == {
        *JOB_FACT_COLUMNS,
        *JOB_DERIVED_COLUMNS,
    }


def _projection(
    alias: str,
    columns: tuple[str, ...],
    *,
    available: set[str],
    column_types: dict[str, str],
) -> str:
    return ",\n          ".join(
        (
            f'{alias}."{column}" AS "{column}"'
            if column in available
            else f'CAST(NULL AS {column_types[column]}) AS "{column}"'
        )
        for column in columns
    )


def _table_columns(conn: Any, table_name: str) -> set[str]:
    exists = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        [table_name],
    ).fetchone()[0]
    if not exists:
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()}
