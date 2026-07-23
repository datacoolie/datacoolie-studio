from __future__ import annotations

from contextlib import nullcontext
from datetime import date, datetime, timezone
from typing import Any, ContextManager, Iterable, TypeAlias

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.logs.cache import (
    analytics_reader,
    monitoring_filter_sql,
)
from datacoolie_studio.domains.monitoring.serving_facts import (
    DATAFLOW_FACT_COLUMNS,
    JOB_FACT_COLUMNS,
    MONITORING_DATAFLOW_FACTS_TABLE,
    MONITORING_JOB_FACTS_TABLE,
)


AnalyticsContext: TypeAlias = tuple[Any, list[int], str]

_DATAFLOW_COLUMNS = frozenset(DATAFLOW_FACT_COLUMNS)
_JOB_COLUMNS = frozenset(JOB_FACT_COLUMNS)


def reader_context(
    paths: list[EnvironmentSource],
    analytics_context: AnalyticsContext | None,
) -> ContextManager[AnalyticsContext]:
    return nullcontext(analytics_context) if analytics_context is not None else analytics_reader(paths)


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
    dataflow_projection = _projection("d", dataflow_columns, _DATAFLOW_COLUMNS)
    job_projection = _projection("j", job_columns, _JOB_COLUMNS)
    placeholders = ", ".join("?" for _ in source_ids)
    dataflow_where, dataflow_params = monitoring_filter_sql(
        filters,
        "d",
        "d",
        dataflow_table=MONITORING_DATAFLOW_FACTS_TABLE,
        dataflow_event_time_column="event_time",
    )
    job_where, job_params = monitoring_filter_sql(
        filters,
        "j",
        "j",
        include_dataflow_filters=False,
        dataflow_table=MONITORING_DATAFLOW_FACTS_TABLE,
    )
    job_scope = (
        " AND EXISTS ("
        "SELECT 1 FROM filtered_dataflows df "
        "WHERE df._source_id = j._source_id AND df.job_id = j.job_id"
        ")"
        if has_dataflow_scope(filters)
        else ""
    )
    sql = f"""
        WITH filtered_dataflows AS (
          SELECT
            {dataflow_projection},
            d.normalized_status,
            d.event_time,
            d.run_date,
            d.engine_name,
            d.metadata_provider_name,
            d.platform_name
          FROM {MONITORING_DATAFLOW_FACTS_TABLE} d
          WHERE d._source_id IN ({placeholders}){dataflow_where}
        ),
        filtered_jobs AS (
          SELECT
            {job_projection},
            j.normalized_status,
            j.event_time,
            j.run_date
          FROM {MONITORING_JOB_FACTS_TABLE} j
          WHERE j._source_id IN ({placeholders}){job_where}{job_scope}
        )
    """
    return sql, [*source_ids, *dataflow_params, *source_ids, *job_params]


def has_dataflow_scope(filters: dict[str, str]) -> bool:
    connection = str(filters.get("connection") or "").strip()
    if connection and connection != "all":
        return True
    kind = str(filters.get("investigateKind") or "").strip()
    value = str(filters.get("investigateValue") or "").strip()
    return bool(kind and value and kind != "job_id") or any(
        str(filters.get(name) or "").strip()
        for name in ("stage", "sourceType", "destinationType", "loadType", "operationType")
    )


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


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
