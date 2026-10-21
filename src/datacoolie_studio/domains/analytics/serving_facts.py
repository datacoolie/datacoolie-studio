"""Compact published facts consumed by Monitoring read models."""

from __future__ import annotations

from datacoolie_studio.domains.analytics.record_codec import root_column_types

from typing import Any


_FAILURE_STATUSES = frozenset({"failed", "failure", "error", "cancelled", "canceled"})
_DERIVED_ERROR_COLUMNS = (
    "error_message",
    "source_error_message",
    "transform_error_message",
    "destination_error_message",
)


def error_field_expression(
    alias: str,
    column: str,
    available: set[str] | frozenset[str] | list[str] | tuple[str, ...],
) -> str:
    """Use the canonical v6 message only when its phase failed."""
    columns = set(available)
    phase = column.removesuffix("_error_message")
    canonical = "message" if column == "error_message" else f"{phase}_message"
    status = "status" if column == "error_message" else f"{phase}_status"
    message = _optional_ref(alias, canonical, columns)
    if message is None:
        return "NULL::VARCHAR"
    status_ref = _optional_ref(alias, status, columns) or _optional_ref(alias, "status", columns)
    if status_ref is None:
        return f"CAST({message} AS VARCHAR)"
    failure = (
        f"LOWER(COALESCE(NULLIF(TRIM(CAST({status_ref} AS VARCHAR)), ''), 'unknown')) "
        f"IN ({', '.join(repr(value) for value in sorted(_FAILURE_STATUSES))})"
    )
    return f"CASE WHEN {failure} THEN CAST({message} AS VARCHAR) ELSE NULL::VARCHAR END"


def error_truncated_expression(
    alias: str,
    available: set[str] | frozenset[str] | list[str] | tuple[str, ...],
) -> str:
    """Return the authored v6 truncation flag."""
    return _optional_ref(alias, "message_truncated", set(available)) or "NULL::BOOLEAN"



def _optional_ref(alias: str, column: str, available: set[str]) -> str | None:
    if column not in available:
        return None
    return f'{alias}."{column.replace(chr(34), chr(34) * 2)}"'


MONITORING_DATAFLOW_FACTS_TABLE = "monitoring_dataflow_facts"
MONITORING_JOB_FACTS_TABLE = "monitoring_job_facts"

DATAFLOW_FACT_COLUMNS = (
    "_source_id",
    "log_schema_version",
    "_file_uri",
    "_file_kind",
    "_ingested_at",
    "_record_line",
    "datacoolie_version",
    "log_session_id",
    "job_id",
    "job_num",
    "job_index",
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
    "message",
    "message_truncated",
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
    "source_message",
    "source_error_message",
    "source_duration_seconds",
    "source_rows_read",
    "source_watermark_columns",
    "transform_status",
    "transform_message",
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
    "destination_message",
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
    "observed_at",
    "__event_time",
    "__run_date",
)

JOB_FACT_COLUMNS = (
    "_source_id",
    "log_schema_version",
    "_file_uri",
    "_file_kind",
    "_ingested_at",
    "_record_line",
    "datacoolie_version",
    "log_session_id",
    "job_id",
    "message",
    "message_truncated",
    "status",
    "start_time",
    "end_time",
    "duration_seconds",
    "engine_name",
    "metadata_provider_name",
    "platform_name",
    "run_attributes",
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
    "log_records_dropped",
    "log_bytes_dropped",
    "error_message_truncated",
    "observed_at",
    "__event_time",
    "__run_date",
)

# Physical facts keep scalar report fields. Complete root/nested evidence belongs
# to the source tables and is selected through exact provenance when required.
DATAFLOW_FACT_COLUMNS = (*DATAFLOW_FACT_COLUMNS,
    "source_start_time", "source_end_time", "transform_start_time",
    "transform_end_time", "destination_start_time", "destination_end_time",
)
DATAFLOW_LOGICAL_COLUMNS = tuple(dict.fromkeys((
    *DATAFLOW_FACT_COLUMNS, *root_column_types("dataflow_run_log"),
    "raw_record_json", "destination_operation_details", "transformers_applied",
)))
JOB_LOGICAL_COLUMNS = tuple(dict.fromkeys((
    *JOB_FACT_COLUMNS, *root_column_types("job_run_log"), "raw_record_json",
)))

DATAFLOW_DERIVED_COLUMNS = (
    "engine_name",
    "metadata_provider_name",
    "platform_name",
    "normalized_status",
    "event_time",
    "run_date",
)
JOB_DERIVED_COLUMNS = ("normalized_status", "event_time", "run_date")


def _serving_column_types(kind: str, column_types: dict[str, str]) -> dict[str, str]:
    from datacoolie_studio.domains.analytics import schema

    historical = (
        schema.HISTORICAL_DATAFLOW_COLUMN_TYPES
        if kind == "dataflow" else schema.HISTORICAL_JOB_COLUMN_TYPES
    )
    types = {**{key: "HUGEINT" if value == "BIGNUM" else value for key, value in historical.items()},
             **schema.cache_table_column_types(column_types)}
    types["duration_seconds"] = "DOUBLE"
    for name in ("start_time", "end_time", "observed_at"):
        types[name] = "TIMESTAMPTZ"
    return types


def rebuild_monitoring_serving_facts(
    conn: Any,
    *,
    dataflow_table: str,
    job_table: str,
    dataflow_column_types: dict[str, str],
    job_column_types: dict[str, str],
    source_ids: list[int] | None = None,
) -> None:
    if source_ids is None or not monitoring_serving_schema_is_ready(conn):
        _build_monitoring_serving_facts(conn, dataflow_table=dataflow_table, job_table=job_table,
                                       dataflow_column_types=dataflow_column_types, job_column_types=job_column_types)
        return
    ids = ",".join(str(int(value)) for value in sorted(set(source_ids))) or "NULL"
    scoped_df, scoped_job = "__dc_scope_df", "__dc_scope_job"
    new_df, new_job = "__dc_new_df_facts", "__dc_new_job_facts"
    try:
        conn.execute(f"CREATE TEMP VIEW {scoped_df} AS SELECT * FROM {dataflow_table} WHERE _source_id IN ({ids})")
        conn.execute(f"CREATE TEMP VIEW {scoped_job} AS SELECT * FROM {job_table} WHERE _source_id IN ({ids})")
        _build_monitoring_serving_facts(conn, dataflow_table=scoped_df, job_table=scoped_job,
                                       dataflow_column_types=dataflow_column_types, job_column_types=job_column_types,
                                       output_dataflow=new_df, output_job=new_job, temporary=True)
        for target, staged in ((MONITORING_JOB_FACTS_TABLE, new_job), (MONITORING_DATAFLOW_FACTS_TABLE, new_df)):
            conn.execute(f"DELETE FROM {target} WHERE _source_id IN ({ids})")
            conn.execute(f"INSERT INTO {target} BY NAME SELECT * FROM {staged}")
    finally:
        for name in (scoped_df, scoped_job):
            conn.execute(f"DROP VIEW IF EXISTS {name}")
        for name in (new_df, new_job):
            conn.execute(f"DROP TABLE IF EXISTS {name}")


def _build_monitoring_serving_facts(
    conn: Any, *, dataflow_table: str, job_table: str,
    dataflow_column_types: dict[str, str], job_column_types: dict[str, str],
    output_dataflow: str = MONITORING_DATAFLOW_FACTS_TABLE,
    output_job: str = MONITORING_JOB_FACTS_TABLE,
    temporary: bool = False,
) -> None:
    """Materialize canonical scalar facts inside the publication transaction."""
    MONITORING_DATAFLOW_FACTS_TABLE = output_dataflow
    MONITORING_JOB_FACTS_TABLE = output_job
    table_ddl = "CREATE OR REPLACE TEMP TABLE" if temporary else "CREATE OR REPLACE TABLE"
    from datacoolie_studio.domains.analytics.serving_validation import validate_run_conflicts
    validate_run_conflicts(conn, dataflow_table)
    from datacoolie_studio.domains.analytics.v6_projection import field_expression
    for relation in (MONITORING_DATAFLOW_FACTS_TABLE, MONITORING_JOB_FACTS_TABLE):
        if conn.execute(
            "SELECT table_type FROM information_schema.tables WHERE table_name = ?",
            [relation],
        ).fetchall() == [("VIEW",)]:
            conn.execute(f"DROP VIEW {relation}")
    dataflow_available = _table_columns(conn, dataflow_table)
    job_available = _table_columns(conn, job_table)
    def project(columns, types, available):
        return ",\n          ".join(
            f'{field_expression(column, types.get(column, "VARCHAR"), raw="raw", available=available)} AS "{column}"'
            for column in columns
        )
    # Evidence numbers stay JSON in bases; reports keep their duration/time types.
    df_types = _serving_column_types("dataflow", dataflow_column_types)
    job_types = _serving_column_types("job", job_column_types)
    dataflow_source_projection = project(DATAFLOW_FACT_COLUMNS, df_types, dataflow_available)
    job_source_projection = project(JOB_FACT_COLUMNS, job_types, job_available)
    dataflow_projection = ",\n          ".join(f'd."{column}" AS "{column}"' for column in DATAFLOW_FACT_COLUMNS)
    job_projection = ",\n          ".join(f'j."{column}" AS "{column}"' for column in JOB_FACT_COLUMNS)
    conn.execute(
        f"""
        {table_ddl} {MONITORING_JOB_FACTS_TABLE} AS
        SELECT
          {job_projection},
          LOWER(COALESCE(NULLIF(TRIM(CAST(j.status AS VARCHAR)), ''), 'unknown'))
            AS normalized_status,
          j.__event_time AS event_time,
          COALESCE(
            j.__run_date,
            CAST(timezone('UTC', j.__event_time) AS DATE)
          ) AS run_date
        FROM (
          SELECT
            {job_source_projection}
          FROM (
            SELECT * FROM {job_table}
            QUALIFY row_number() OVER (
              PARTITION BY _source_id, log_session_id, job_id
              ORDER BY TRY_CAST(observed_at AS TIMESTAMPTZ) DESC NULLS LAST, __event_time DESC NULLS LAST,
                       _file_uri DESC, _record_line DESC
            ) = 1
          ) raw
        ) j
        ORDER BY j._source_id, run_date, event_time, j.log_session_id, j.job_id
        """
    )
    conn.execute(
        f"""
        {table_ddl} {MONITORING_DATAFLOW_FACTS_TABLE} AS
        WITH dataflow_source AS (
          SELECT
            {dataflow_source_projection}
          FROM (
            SELECT * FROM {dataflow_table}
            QUALIFY row_number() OVER (
              PARTITION BY _source_id, log_session_id, dataflow_run_id
              ORDER BY _file_uri, _record_line
            ) = 1
          ) raw
        ),
        job_context AS (
          SELECT
            _source_id,
            log_session_id,
            job_id,
            engine_name,
            metadata_provider_name,
            platform_name
          FROM {MONITORING_JOB_FACTS_TABLE}
          WHERE job_id IS NOT NULL
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
          ON j._source_id = d._source_id
         AND j.log_session_id IS NOT DISTINCT FROM d.log_session_id
         AND j.job_id = d.job_id
        ORDER BY d._source_id, run_date, event_time, d.dataflow_id, d.dataflow_run_id
        """
    )


def validate_monitoring_serving_facts(
    conn: Any,
    *,
    dataflow_table: str,
    job_table: str,
    source_ids: list[int] | None = None,
) -> None:
    if not monitoring_serving_schema_is_ready(conn):
        raise RuntimeError("Monitoring serving facts do not match the current schema")
    from datacoolie_studio.domains.analytics.serving_validation import validate_serving_parity
    if source_ids is not None:
        ids = ",".join(str(int(value)) for value in sorted(set(source_ids))) or "NULL"
        views = ("__dc_validate_df", "__dc_validate_job", "__dc_validate_df_facts", "__dc_validate_job_facts")
        try:
            for view, base in zip(views, (dataflow_table, job_table, MONITORING_DATAFLOW_FACTS_TABLE, MONITORING_JOB_FACTS_TABLE)):
                conn.execute(f"CREATE TEMP VIEW {view} AS SELECT * FROM {base} WHERE _source_id IN ({ids})")
            validate_serving_parity(conn, views[0], views[1], dataflow_facts=views[2], job_facts=views[3])
        finally:
            for view in views:
                conn.execute(f"DROP VIEW IF EXISTS {view}")
    else:
        validate_serving_parity(conn, dataflow_table, job_table)
    predicate = "" if source_ids is None else f"(_source_id IN ({ids})) AND "
    invalid_dataflows = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM {MONITORING_DATAFLOW_FACTS_TABLE}
        WHERE {predicate}(normalized_status IS DISTINCT FROM
              LOWER(COALESCE(NULLIF(TRIM(CAST(status AS VARCHAR)), ''), 'unknown'))
           OR event_time IS DISTINCT FROM __event_time
           OR run_date IS DISTINCT FROM COALESCE(
                __run_date,
                CAST(timezone('UTC', __event_time) AS DATE)
              ))
        """
    ).fetchone()[0]
    invalid_jobs = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM {MONITORING_JOB_FACTS_TABLE}
        WHERE {predicate}(normalized_status IS DISTINCT FROM
              LOWER(COALESCE(NULLIF(TRIM(CAST(status AS VARCHAR)), ''), 'unknown'))
           OR event_time IS DISTINCT FROM __event_time
           OR run_date IS DISTINCT FROM COALESCE(
                __run_date,
                CAST(timezone('UTC', __event_time) AS DATE)
              ))
        """
    ).fetchone()[0]
    if invalid_dataflows or invalid_jobs:
        raise RuntimeError("Monitoring serving-fact derived columns do not reconcile")


def monitoring_serving_schema_is_ready(conn: Any) -> bool:
    from datacoolie_studio.domains.analytics import schema

    derived_types = {
        "engine_name": "VARCHAR", "metadata_provider_name": "VARCHAR",
        "platform_name": "VARCHAR", "normalized_status": "VARCHAR",
        "event_time": "TIMESTAMPTZ", "run_date": "DATE",
    }
    for table, columns, derived, types in (
        (MONITORING_DATAFLOW_FACTS_TABLE, DATAFLOW_FACT_COLUMNS, DATAFLOW_DERIVED_COLUMNS,
         _serving_column_types("dataflow", schema.DATAFLOW_COLUMN_TYPES)),
        (MONITORING_JOB_FACTS_TABLE, JOB_FACT_COLUMNS, JOB_DERIVED_COLUMNS,
         _serving_column_types("job", schema.JOB_COLUMN_TYPES)),
    ):
        physical = conn.execute(
            "SELECT table_name FROM duckdb_tables() WHERE table_name = ? AND NOT internal "
            "AND NOT temporary AND schema_name = 'main' AND database_name = current_database()",
            [table],
        ).fetchall()
        if len(physical) != 1 or _table_columns(conn, table) != {*columns, *derived}:
            return False
        actual = {row[0]: row[1] for row in conn.execute(f"DESCRIBE {table}").fetchall()}
        expected = {
            **{column: types.get(column, "VARCHAR") for column in columns},
            **{column: derived_types[column] for column in derived},
        }
        if any(not schema.duckdb_type_matches(actual[column], dtype) for column, dtype in expected.items()):
            return False
    return True



def _table_columns(conn: Any, table_name: str) -> set[str]:
    exists = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        [table_name],
    ).fetchone()[0]
    if not exists:
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()}
