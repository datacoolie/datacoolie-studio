from __future__ import annotations

from contextlib import nullcontext
from datetime import date, datetime, timezone
from typing import Any, ContextManager, Iterable, TypeAlias

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.analytics.record_codec import (
    root_column_types,
)
from datacoolie_studio.domains.analytics.serving_facts import (
    DATAFLOW_FACT_COLUMNS,
    DATAFLOW_LOGICAL_COLUMNS,
    JOB_FACT_COLUMNS,
    JOB_LOGICAL_COLUMNS,
    MONITORING_DATAFLOW_FACTS_TABLE,
    MONITORING_JOB_FACTS_TABLE,
)
from datacoolie_studio.domains.analytics.v6_projection import field_expression
from datacoolie_studio.domains.monitoring.context import reader as analytics_reader
from datacoolie_studio.domains.monitoring.dimensions import PROJECTED_DIMENSIONS, dimension_key_sql
from datacoolie_studio.domains.monitoring.log_repository import (
    monitoring_filter_sql,
    monitoring_requires_child_existence,
    monitoring_job_direct_filters,
)


AnalyticsContext: TypeAlias = tuple[Any, list[int], str]

_DATAFLOW_COLUMNS = frozenset(DATAFLOW_LOGICAL_COLUMNS)
_JOB_COLUMNS = frozenset(JOB_LOGICAL_COLUMNS)
_EVIDENCE_PROVENANCE_COLUMNS = ("_source_id", "_file_uri", "_file_kind", "_record_line")
_DATAFLOW_SOURCE_TYPES = {
    **root_column_types("dataflow_run_log"),
    "_record_line": "BIGINT",
}
_JOB_SOURCE_TYPES = {
    **root_column_types("job_run_log"),
    "_record_line": "BIGINT",
}
_DATAFLOW_SOURCE_ONLY = frozenset(DATAFLOW_LOGICAL_COLUMNS) - frozenset(DATAFLOW_FACT_COLUMNS)
_JOB_SOURCE_ONLY = frozenset(JOB_LOGICAL_COLUMNS) - frozenset(JOB_FACT_COLUMNS)

def reader_context(
    paths: list[EnvironmentSource],
    analytics_context: AnalyticsContext | None,
    *,
    snapshot: bool = False,
) -> ContextManager[AnalyticsContext]:
    if analytics_context is not None:
        # The owner of an analytics reader prepares the shared UDF catalog
        # before opening a snapshot.  A supplied context is already fenced;
        # mutating its catalog here would violate that lifecycle and can race
        # another reader on DuckDB's shared catalog.
        return nullcontext(analytics_context)
    return analytics_reader(paths, snapshot=True) if snapshot else analytics_reader(paths)


def filtered_ctes(
    source_ids: list[int],
    filters: dict[str, str],
    *,
    dataflow_columns: Iterable[str],
    job_columns: Iterable[str],
) -> tuple[str, list[Any]]:
    """Build the canonical filtered Monitoring population for DuckDB queries.

    Page read models must request explicit columns. Filter values remain bound
    parameters; only validated Studio-owned identifiers are interpolated.
    """
    requested_dataflow = tuple(dict.fromkeys(dataflow_columns))
    requested_jobs = tuple(job_columns)
    dataflow_projection = _logical_projection(
        "d",
        "ds",
        (*requested_dataflow, "log_session_id", *_EVIDENCE_PROVENANCE_COLUMNS),
        physical=frozenset(DATAFLOW_FACT_COLUMNS),
        logical=_DATAFLOW_COLUMNS,
        source_types=_DATAFLOW_SOURCE_TYPES,
    )
    job_projection = _logical_projection(
        "j",
        "js",
        (*requested_jobs, "log_session_id", *_EVIDENCE_PROVENANCE_COLUMNS),
        physical=frozenset(JOB_FACT_COLUMNS),
        logical=_JOB_COLUMNS,
        source_types=_JOB_SOURCE_TYPES,
    )
    dataflow_source_join = _source_join(
        "d", "ds", analytics_schema.DATAFLOW_TABLE,
        requested_dataflow, _DATAFLOW_SOURCE_ONLY,
    )
    job_source_join = _source_join(
        "j", "js", analytics_schema.JOB_TABLE,
        requested_jobs, _JOB_SOURCE_ONLY,
    )
    placeholders = ", ".join("?" for _ in source_ids)
    dataflow_where, dataflow_params = monitoring_filter_sql(
        filters,
        "d",
        "jc",
        dataflow_table=MONITORING_DATAFLOW_FACTS_TABLE,
        dataflow_event_time_column="event_time",
    )
    job_where, job_params = monitoring_filter_sql(
        monitoring_job_direct_filters(filters),
        "j",
        "j",
        include_dataflow_filters=False,
        dataflow_table=MONITORING_DATAFLOW_FACTS_TABLE,
    )
    job_scope = (
        " AND EXISTS ("
        "SELECT 1 FROM filtered_dataflows df "
        "WHERE df._source_id = j._source_id AND df.job_id = j.job_id "
        "AND df.log_session_id IS NOT DISTINCT FROM j.log_session_id"
        ")"
        if monitoring_requires_child_existence(filters)
        else ""
    )
    sql = f"""
        WITH all_log_versions AS (
          SELECT d.log_schema_version
          FROM {MONITORING_DATAFLOW_FACTS_TABLE} d
          WHERE d._source_id IN ({placeholders})
          UNION ALL
          SELECT j.log_schema_version
          FROM {MONITORING_JOB_FACTS_TABLE} j
          WHERE j._source_id IN ({placeholders})
        ),
        filtered_dataflows AS (
          SELECT
            {dataflow_projection},
            d.normalized_status,
            d.event_time,
            d.run_date,
            {dimension_key_sql('jc.engine_name')} AS engine_name,
            {dimension_key_sql('jc.metadata_provider_name')} AS metadata_provider_name,
            {dimension_key_sql('jc.platform_name')} AS platform_name
          FROM {MONITORING_DATAFLOW_FACTS_TABLE} d
          {dataflow_source_join}
          LEFT JOIN {MONITORING_JOB_FACTS_TABLE} jc
            ON jc._source_id = d._source_id AND jc.job_id = d.job_id
            AND jc.log_session_id IS NOT DISTINCT FROM d.log_session_id
          WHERE d._source_id IN ({placeholders}){dataflow_where}
        ),
        filtered_jobs AS (
          SELECT
            {job_projection},
            j.normalized_status,
            j.event_time,
            j.run_date
          FROM {MONITORING_JOB_FACTS_TABLE} j
          {job_source_join}
          WHERE j._source_id IN ({placeholders}){job_where}{job_scope}
        ),
        comparison_dataflows AS (
          SELECT
            d2._source_id,
            d2.log_session_id,
            CAST(d2.job_id AS VARCHAR) AS job_id,
            LOWER(COALESCE(NULLIF(CAST(d2.status AS VARCHAR), ''), 'unknown')) AS normalized_status,
            d2.event_time,
            CASE WHEN fd._source_id IS NOT NULL THEN TRUE ELSE FALSE END AS in_filter
          FROM {MONITORING_DATAFLOW_FACTS_TABLE} d2
          INNER JOIN (
            SELECT DISTINCT _source_id, log_session_id, job_id
            FROM filtered_jobs
            WHERE job_id IS NOT NULL
          ) selected_jobs
            ON selected_jobs._source_id = d2._source_id
           AND selected_jobs.log_session_id IS NOT DISTINCT FROM d2.log_session_id
           AND selected_jobs.job_id = CAST(d2.job_id AS VARCHAR)
          LEFT JOIN filtered_dataflows fd
            ON fd._source_id = d2._source_id
           AND fd.log_session_id IS NOT DISTINCT FROM d2.log_session_id
           AND fd._file_uri = d2._file_uri
           AND fd._record_line = d2._record_line
          WHERE d2._source_id IN ({placeholders})
        )
    """
    return sql, [
        *source_ids,
        *source_ids,
        *source_ids,
        *dataflow_params,
        *source_ids,
        *job_params,
        *source_ids,
    ]


def rows(result: Any) -> list[dict[str, Any]]:
    columns = [column[0] for column in result.description]
    return [
        {key: json_value(value) for key, value in zip(columns, record)}
        for record in result.fetchall()
    ]


def one(result: Any) -> dict[str, Any]:
    record = result.fetchone()
    if record is None:
        return {}
    return {
        column[0]: json_value(value)
        for column, value in zip(result.description, record)
    }


def paged_rows(result: Any) -> tuple[list[dict[str, Any]], int]:
    """Return a bounded result page and its pre-LIMIT window count."""
    records = rows(result)
    total = int(records[0].get("__total_records") or 0) if records else 0
    for record in records:
        record.pop("__total_records", None)
    return records, total


def json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc).isoformat()
    return value.isoformat() if isinstance(value, date) else value


def typed_metric(column: str, alias: str = "") -> str:
    """Return one native metrics member for SQL report expressions."""

    if column not in {
        "rows_read", "rows_written", "rows_inserted", "rows_updated", "rows_deleted",
        "files_added", "files_removed", "bytes_added", "bytes_removed", "bytes_saved",
    }:
        raise ValueError(f"Unsupported v6 metric: {column}")
    prefix = f"{alias}." if alias else ""
    return f"({prefix}metrics).{column}"


def typed_final_attempt(alias: str = "") -> str:
    """Return the final native attempt, preserving NULL for an empty list."""

    prefix = f"{alias}." if alias else ""
    return f"{prefix}attempts[-1]"


def trend_bucket_key(value: Any, grain: str) -> str:
    """Return the public bucket key used by Monitoring trend consumers."""
    text = str(value or "").strip()
    if not text:
        return "unknown"
    date_key = text[:10]
    normalized_grain = str(grain or "day").lower()
    if normalized_grain == "hour":
        hour = text[11:13] if len(text) >= 13 else "00"
        return f"{date_key} {hour}:00"
    if normalized_grain == "week":
        try:
            iso_year, iso_week, _ = date.fromisoformat(date_key).isocalendar()
            return f"{iso_year}-W{iso_week:02d}"
        except ValueError:
            return text
    if normalized_grain == "month":
        return date_key[:7]
    return date_key


def discrete_percentile(
    column: str,
    percentile: float,
    where: str | None = None,
) -> str:
    """Return DuckDB SQL matching the existing nearest-index metric contract."""
    filter_sql = f" FILTER (WHERE {where})" if where else ""
    list_filter = f" AND {where}" if where else ""
    return (
        "COALESCE(list_extract(list_sort(list("
        f"{column}) FILTER (WHERE {column} IS NOT NULL{list_filter})), "
        f"CAST(round_even((COUNT({column}){filter_sql} - 1) * {percentile}, 0) AS BIGINT) + 1), 0)"
    )


def sorted_list_percentile(
    list_column: str,
    count_column: str,
    percentile: float,
) -> str:
    """Extract the existing banker-nearest percentile from one sorted group list."""
    return (
        f"COALESCE(list_extract({list_column}, "
        f"CAST(round_even(({count_column} - 1) * {percentile}, 0) AS BIGINT) + 1), 0)"
    )


def nullable_discrete_percentile(column: str, percentile: float, where: str | None = None) -> str:
    filter_sql = f" FILTER (WHERE {where})" if where else ""
    return f"CASE WHEN COUNT({column}){filter_sql} > 0 THEN {discrete_percentile(column, percentile, where)} END"


def standalone_derived_query(derived_ctes: str, select_sql: str) -> str:
    """Nest append-style CTE SQL without duplicating its business expressions."""
    prefix = standalone_append_query(derived_ctes)
    return f"{prefix}\n{select_sql}"


def standalone_append_query(sql: str) -> str:
    nested = sql.lstrip()
    return f"WITH{nested[1:]}" if nested.startswith(",") else nested


def _projection(alias: str, columns: Iterable[str], allowed: frozenset[str]) -> str:
    requested = list(dict.fromkeys(columns))
    unknown = sorted(set(requested) - allowed)
    if unknown:
        raise ValueError(f"Unsupported Monitoring columns: {', '.join(unknown)}")
    if not requested:
        raise ValueError("A Monitoring read model must project at least one column")
    return ",\n            ".join(
        f'{alias}.{_quote_identifier(column)} AS {_quote_identifier(column)}'
        for column in requested
    )


def _logical_projection(
    fact_alias: str,
    source_alias: str,
    columns: Iterable[str],
    *,
    physical: frozenset[str],
    logical: frozenset[str],
    source_types: dict[str, str],
) -> str:
    requested = list(dict.fromkeys(columns))
    unknown = sorted(set(requested) - logical)
    if unknown:
        raise ValueError(f"Unsupported Monitoring columns: {', '.join(unknown)}")
    if not requested:
        raise ValueError("A Monitoring read model must project at least one column")
    source_available = set(source_types)
    expressions: list[str] = []
    for column in requested:
        if column in physical:
            expression = f'{fact_alias}.{_quote_identifier(column)}'
        elif column == "raw_record_json":
            # Whole-record JSON is bounded hydration output, never fact/source
            # storage.  Keep the marker in the read-model contract so existing
            # consumers can request it without causing a source scan.
            expression = "NULL::VARCHAR"
        else:
            expression = field_expression(
                column,
                source_types.get(column, "VARCHAR"),
                raw=source_alias,
                available=source_available,
            )
        if column in PROJECTED_DIMENSIONS:
            expression = dimension_key_sql(expression)
        expressions.append(f'{expression} AS {_quote_identifier(column)}')
    return ",\n            ".join(expressions)


def _source_join(
    fact_alias: str,
    source_alias: str,
    source_table: str,
    requested: Iterable[str],
    source_only: frozenset[str],
) -> str:
    # raw_record_json is a NULL marker and does not need source hydration.  All
    # other logical-only fields are read from the one exact source observation.
    needs_source = any(
        column in source_only and column != "raw_record_json"
        for column in requested
    )
    if not needs_source:
        return ""
    return (
        f"LEFT JOIN {source_table} {source_alias} "
        f"ON {source_alias}._source_id = {fact_alias}._source_id "
        f"AND {source_alias}._file_uri = {fact_alias}._file_uri "
        f"AND {source_alias}._record_line = {fact_alias}._record_line"
    )


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
