from __future__ import annotations

from datacoolie_studio.domains.analytics import schema
from datacoolie_studio.domains.analytics.contracts import JobFile
from datacoolie_studio.domains.analytics.errors import AnalyticsSchemaIncompatibleError
from datacoolie_studio.domains.analytics.sql import sql_string


def preflight_job_schemas(
    conn,
    files: list[JobFile],
    *,
    source_relations: dict[int, str],
    source_types: dict[int, dict[str, str]],
) -> None:
    if not files:
        return
    actual_types = schema.table_column_types(conn, schema.JOB_TABLE)
    for item in files:
        for column, dtype in schema.JOB_COLUMN_TYPES.items():
            if column not in actual_types or not schema.duckdb_type_matches(actual_types[column], dtype):
                raise AnalyticsSchemaIncompatibleError(f"Job storage requires rebuild for {column!r} in {item.canonical_uri}")


def job_json_scalar(column: str) -> str:
    from datacoolie_studio.domains.analytics.sql import quote_identifier
    return quote_identifier(column)


def job_json_projection(column: str, source_type: str, target_type: str) -> str:
    del source_type, target_type
    return job_json_scalar(column)



def job_event_time_expression(source_columns: set[str], *, column_prefix: str = "") -> str:
    values = [
        f"TRY_CAST({column_prefix}{job_json_scalar(column)} AS TIMESTAMPTZ)"
        for column in ("__event_time", "end_time", "start_time")
        if column in source_columns
    ]
    return f"COALESCE({', '.join(values)})" if values else "NULL::TIMESTAMPTZ"


def uri_date_expression(file_uri: str, *, sql_expression: str | None = None) -> str:
    pattern = "(20[0-9]{2}[-_/][0-9]{2}[-_/][0-9]{2})"
    value = sql_expression or sql_string(file_uri)
    extracted = f"regexp_extract({value}, {sql_string(pattern)})"
    normalized = (
        f"replace(replace({extracted}, {sql_string('/')}, '-'), "
        f"{sql_string('_')}, '-')"
    )
    return f"TRY_CAST({normalized} AS DATE)"
