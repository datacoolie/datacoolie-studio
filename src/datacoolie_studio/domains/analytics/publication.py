from __future__ import annotations

from datetime import datetime
from pathlib import Path

from datacoolie_studio.db.models import utc_now
from datacoolie_studio.domains.analytics import schema
from datacoolie_studio.domains.analytics import schema_tables
from datacoolie_studio.domains.analytics.serving_facts import (
    rebuild_monitoring_serving_facts,
    validate_monitoring_serving_facts,
)
from datacoolie_studio.domains.analytics.sql import quote_identifier


def publish_generation(
    conn,
    *,
    dataflow_column_types: dict[str, str],
    job_column_types: dict[str, str],
    published_at: datetime,
    source_ids: list[int] | None = None,
) -> None:
    """Finalize derived serving facts and advance the published generation."""
    rebuild_monitoring_serving_facts(
        conn,
        dataflow_table=schema.DATAFLOW_TABLE,
        job_table=schema.JOB_TABLE,
        dataflow_column_types=schema.cache_table_column_types(dataflow_column_types),
        job_column_types=schema.cache_table_column_types(job_column_types),
        source_ids=source_ids,
    )
    validate_monitoring_serving_facts(
        conn,
        dataflow_table=schema.DATAFLOW_TABLE,
        job_table=schema.JOB_TABLE,
        source_ids=source_ids,
    )
    schema.ensure_analytics_meta_table(conn)
    schema.write_contract_metadata(conn)
    conn.execute(
        f"""
        UPDATE {schema.ANALYTICS_META_TABLE}
        SET schema_version = ?,
            generation = generation + 1,
            build_state = 'ready',
            published_at = ?::TIMESTAMPTZ
        WHERE singleton_id = 1
        """,
        [schema.ANALYTICS_SCHEMA_VERSION, published_at.isoformat()],
    )


def refresh_filter_values(conn, source_id: int) -> None:
    refresh_filter_values_for_sources(conn, [source_id])


def refresh_filter_values_for_sources(conn, source_ids: list[int]) -> None:
    """Rebuild source-scoped filter values before a generation is published."""
    source_ids = sorted({int(source_id) for source_id in source_ids})
    if not source_ids:
        return
    schema.ensure_filter_values_table(conn)
    placeholders = ", ".join("?" for _ in source_ids)
    source_predicate = f"_source_id IN ({placeholders})"
    conn.execute(
        f"DELETE FROM {schema.FILTER_VALUES_TABLE} WHERE {source_predicate}",
        source_ids,
    )
    updated_at = utc_now().isoformat()
    for field, (table_name, column_name) in schema.FILTER_VALUE_SOURCES.items():
        if not schema.table_exists(conn, table_name):
            continue
        if field == "destination_operation_type":
            column_expression = 'attempts[-1].destination.operation_type'
        elif column_name in schema.table_columns(conn, table_name):
            column_expression = quote_identifier(column_name)
        else:
            continue
        conn.execute(
            f"""
            INSERT INTO {schema.FILTER_VALUES_TABLE}
            SELECT
              _source_id,
              ?::VARCHAR AS field,
              TRIM(CAST({column_expression} AS VARCHAR)) AS value,
              COUNT(*)::BIGINT AS record_count,
              ?::TIMESTAMPTZ AS _updated_at
            FROM {table_name}
            WHERE {source_predicate}
              AND {column_expression} IS NOT NULL
              AND TRIM(CAST({column_expression} AS VARCHAR)) <> ''
            GROUP BY _source_id, TRIM(CAST({column_expression} AS VARCHAR))
            """,
            [field, updated_at, *source_ids],
        )
    if not schema.table_exists(conn, schema.DATAFLOW_TABLE):
        return
    dataflow_columns = set(schema.table_columns(conn, schema.DATAFLOW_TABLE))
    identity_sql = ", ".join(
        [
            "_source_id",
            "_file_uri",
            "dataflow_run_id" if "dataflow_run_id" in dataflow_columns else "NULL::VARCHAR AS dataflow_run_id",
            "job_id" if "job_id" in dataflow_columns else "NULL::VARCHAR AS job_id",
        ]
    )
    connection_selects: list[str] = []
    for column_name in ("source_name", "destination_name"):
        if column_name not in dataflow_columns:
            continue
        quoted_column = quote_identifier(column_name)
        connection_selects.append(
            f"""
            SELECT {identity_sql}, TRIM(CAST({quoted_column} AS VARCHAR)) AS connection_name
            FROM {schema.DATAFLOW_TABLE}
            WHERE {source_predicate}
              AND {quoted_column} IS NOT NULL
              AND TRIM(CAST({quoted_column} AS VARCHAR)) <> ''
            """
        )
    if not connection_selects:
        return
    union_sql = "\nUNION ALL\n".join(connection_selects)
    conn.execute(
        f"""
        INSERT INTO {schema.FILTER_VALUES_TABLE}
        SELECT
          _source_id,
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
        GROUP BY _source_id, connection_name
        """,
        [updated_at, *([source_id for _ in connection_selects for source_id in source_ids])],
    )


def finalize_candidate(
    candidate_path: Path,
    source_ids: list[int],
    *,
    published_at: datetime | None = None,
) -> None:
    """Build global derived structures and publish one candidate generation."""

    from datacoolie_studio.domains.analytics import access as analytics_access
    if not candidate_path.exists():
        raise RuntimeError("Analytics rebuild candidate is missing")
    unique_source_ids = sorted({int(source_id) for source_id in source_ids})
    with analytics_access.analytics_maintenance_lock:
        conn = analytics_access.connect(candidate_path)
        try:
            conn.execute("BEGIN TRANSACTION")
            try:
                schema_tables.ensure_tables(conn)
                refresh_filter_values_for_sources(conn, unique_source_ids)
                publish_generation(
                    conn,
                    dataflow_column_types=schema.DATAFLOW_COLUMN_TYPES,
                    job_column_types=schema.JOB_COLUMN_TYPES,
                    published_at=published_at or utc_now(),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()


def validate_analytics_candidate(
    candidate_path: Path,
    source_ids: list[int],
) -> None:
    """Validate candidate schema and exact source coverage before swapping it."""

    from datacoolie_studio.domains.analytics import access as analytics_access

    if not analytics_access.cache_is_ready(candidate_path):
        raise RuntimeError("Analytics rebuild candidate did not create the current typed schema")
    conn = analytics_access.connect(candidate_path, read_only=True)
    try:
        analytics_access.validate_source_complete_candidate(conn, source_ids)
    finally:
        conn.close()
