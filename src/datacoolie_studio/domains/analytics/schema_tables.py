from __future__ import annotations

import duckdb

from datacoolie_studio.domains.analytics import schema
from datacoolie_studio.domains.analytics.sql import quote_identifier


def ensure_tables(conn) -> None:
    """Ensure the complete analytics cache schema exists and is current."""

    recreated = [
        ensure_dataflow_cache_table(conn),
        ensure_typed_table(conn, schema.JOB_TABLE, schema.JOB_COLUMN_TYPES),
    ]
    drop_empty_generated_job_columns(conn)
    if any(recreated) and schema.table_exists(conn, schema.FILTER_VALUES_TABLE):
        conn.execute(f"DROP TABLE {schema.FILTER_VALUES_TABLE}")
    schema.ensure_filter_values_table(conn)
    schema.ensure_cache_sources_table(conn)
    schema.ensure_analytics_meta_table(conn)


def ensure_columns(
    conn,
    table_name: str,
    column_types: dict[str, str],
    existing: set[str],
) -> None:
    for column, data_type in column_types.items():
        if column not in existing:
            conn.execute(
                f"ALTER TABLE {table_name} ADD COLUMN "
                f"{quote_identifier(column)} {data_type}"
            )
            existing.add(column)


def ensure_typed_table(conn, table_name: str, column_types: dict[str, str]) -> bool:
    kinds = conn.execute(
        "SELECT table_type FROM information_schema.tables WHERE table_name = ?",
        [table_name],
    ).fetchall()
    if kinds == [("VIEW",)]:
        conn.execute(f"DROP VIEW {quote_identifier(table_name)}")
    columns = schema.table_columns(conn, table_name)
    if columns and (
        "_source_id" not in columns
        or set(columns) - set(schema.cache_table_column_types(column_types))
        or schema.has_legacy_raw_json_column(columns)
        or schema.has_incompatible_column_types(conn, table_name, schema.cache_table_column_types(column_types))
    ):
        conn.execute(f"DROP TABLE {table_name}")
        columns = []
    if not columns:
        definitions = [
            f"{quote_identifier(column)} {data_type}"
            for column, data_type in schema.cache_table_column_types(column_types).items()
        ]
        conn.execute(f"CREATE TABLE {table_name} ({', '.join(definitions)})")
        return True
    ensure_columns(
        conn,
        table_name,
        schema.cache_table_column_types(column_types),
        set(columns),
    )
    return False


def ensure_dataflow_cache_table(conn) -> bool:
    return ensure_typed_table(conn, schema.DATAFLOW_TABLE, schema.DATAFLOW_COLUMN_TYPES)


def drop_empty_generated_job_columns(conn) -> None:
    """Remove columns that older Studio cache versions generated for jobs."""

    if not schema.table_exists(conn, schema.JOB_TABLE):
        return
    columns = set(schema.table_columns(conn, schema.JOB_TABLE))
    for column in ("operation_type",):
        if column not in columns:
            continue
        quoted_column = quote_identifier(column)
        try:
            non_null_count = conn.execute(
                f"SELECT count(*) FROM {schema.JOB_TABLE} WHERE {quoted_column} IS NOT NULL"
            ).fetchone()[0]
            if int(non_null_count or 0) == 0:
                conn.execute(f"ALTER TABLE {schema.JOB_TABLE} DROP COLUMN {quoted_column}")
        except duckdb.Error:
            continue
