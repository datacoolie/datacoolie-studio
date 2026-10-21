from __future__ import annotations

from datacoolie_studio.domains.analytics import schema
from datacoolie_studio.domains.analytics.sql import quote_identifier, sql_date


def dataflow_source_projection(
    conn,
    file_uri: str,
    *,
    file_kind: str,
    partition_date: str | None = None,
    source_relation: str | None = None,
    partition_expression: str | None = None,
    column_prefix: str = "",
) -> str:
    source_columns = set(schema.DATAFLOW_COLUMN_TYPES) - schema.GENERATED_CACHE_COLUMNS
    def column_value(column: str) -> str:
        return column_prefix + quote_identifier(column)

    passthrough = ", ".join(f"{column_value(column)} AS {quote_identifier(column)}" for column in source_columns)
    def timestamp_value(column: str) -> str:
        return (
            f"TRY_CAST({column_value(column)} AS TIMESTAMPTZ)"
            if column in source_columns
            else "NULL::TIMESTAMPTZ"
        )

    event_time_values = ", ".join(
        timestamp_value(column)
        for column in ("__event_time", "end_time", "start_time")
    )
    event_time = f"COALESCE({event_time_values})"
    source_run_date = (
        f'TRY_CAST({column_value("__run_date")} AS DATE)'
        if "__run_date" in source_columns
        else "NULL::DATE"
    )
    partition_value = partition_expression or sql_date(partition_date)
    run_date = (
        f"COALESCE({partition_value}, {source_run_date}, "
        f"CAST(timezone('UTC', {event_time}) AS DATE))"
    )
    projection_parts = [passthrough]
    projection_parts.extend(
        (f"{event_time} AS __event_time", f"{run_date} AS __run_date")
    )
    return ", ".join(projection_parts)
