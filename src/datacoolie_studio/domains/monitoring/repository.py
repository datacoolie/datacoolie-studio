from __future__ import annotations

from collections import defaultdict
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.analytics.schema import (
    DATAFLOW_TABLE,
    FILTER_VALUES_TABLE,
    JOB_TABLE,
)
from datacoolie_studio.domains.monitoring.read_models.common import (
    AnalyticsContext,
    filtered_ctes,
    one,
    reader_context,
    rows,
)


def monitoring_filter_options_read_model(
    paths: list[EnvironmentSource],
    analytics_context: AnalyticsContext | None = None,
) -> dict[str, Any]:
    """Return exact filter dimensions from the published analytics generation."""
    with reader_context(paths, analytics_context) as (conn, source_ids, generation):
        if conn is None or not source_ids:
            return {"generation": generation, "options": {}}
        placeholders = ", ".join("?" for _ in source_ids)
        result = conn.execute(
            f"""
            SELECT field, value, SUM(record_count) AS record_count
            FROM {FILTER_VALUES_TABLE}
            WHERE _source_id IN ({placeholders})
            GROUP BY field, value
            ORDER BY field, value
            """,
            source_ids,
        )
        options: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows(result):
            options[str(row["field"])].append(
                {"value": row["value"], "label": row["value"], "count": row["record_count"]}
            )
        if "connection" not in options:
            options["connection"] = _connection_filter_values(conn, source_ids)
    return {"generation": generation, "options": dict(options)}


def environment_overview_read_model(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    *,
    grain: str,
    timezone_name: str,
    analytics_context: AnalyticsContext | None = None,
) -> dict[str, Any]:
    """Build the compact Environment Overview Monitoring model in DuckDB."""
    with reader_context(paths, analytics_context) as (conn, source_ids, generation):
        if conn is None or not source_ids:
            return {
                "generation": generation,
                "dataflow_records": 0,
                "job_records": 0,
                "dataflow_succeeded": 0,
                "dataflow_failed": 0,
                "job_failed": 0,
                "active_engines": 0,
                "active_metadata_providers": 0,
                "latest_log_at": None,
                "latest_job_log_at": None,
                "latest_dataflow_log_at": None,
                "date_min": None,
                "date_max": None,
                "jobs_by_date_status": [],
            }

        ctes, params = filtered_ctes(
            source_ids,
            filters,
            dataflow_columns=("_source_id", "job_id"),
            job_columns=(
                "_source_id", "job_id", "engine_name",
                "metadata_provider_name", "platform_name",
            ),
        )
        summary_result = conn.execute(
            f"""
            {ctes}
            SELECT
              (SELECT COUNT(*) FROM filtered_dataflows) AS dataflow_records,
              (SELECT COUNT(*) FROM filtered_jobs) AS job_records,
              (SELECT COUNT(*) FROM filtered_dataflows WHERE normalized_status = 'succeeded') AS dataflow_succeeded,
              (SELECT COUNT(*) FROM filtered_dataflows WHERE normalized_status = 'failed') AS dataflow_failed,
              (SELECT COUNT(*) FROM filtered_jobs WHERE normalized_status = 'failed') AS job_failed,
              (SELECT COUNT(DISTINCT engine_name) FROM filtered_jobs WHERE engine_name IS NOT NULL AND engine_name <> '') AS active_engines,
              (SELECT COUNT(DISTINCT metadata_provider_name) FROM filtered_jobs WHERE metadata_provider_name IS NOT NULL AND metadata_provider_name <> '') AS active_metadata_providers,
              (SELECT MAX(event_time) FROM (
                SELECT event_time FROM filtered_dataflows
                UNION ALL
                SELECT event_time FROM filtered_jobs
              )) AS latest_log_at,
              (SELECT MAX(event_time) FROM filtered_jobs) AS latest_job_log_at,
              (SELECT MAX(event_time) FROM filtered_dataflows) AS latest_dataflow_log_at,
              (SELECT MIN(run_date) FROM (
                SELECT run_date FROM filtered_dataflows
                UNION ALL
                SELECT CAST(timezone('UTC', event_time) AS DATE) AS run_date FROM filtered_jobs
              )) AS date_min,
              (SELECT MAX(run_date) FROM (
                SELECT run_date FROM filtered_dataflows
                UNION ALL
                SELECT CAST(timezone('UTC', event_time) AS DATE) AS run_date FROM filtered_jobs
              )) AS date_max
            """,
            params,
        )
        summary = one(summary_result)

        trend_result = conn.execute(
            f"""
            {ctes}
            SELECT
              date_trunc(?, timezone(?, event_time)) AS bucket_start,
              normalized_status AS status,
              COUNT(*)::BIGINT AS count,
              MIN(event_time) AS end_time
            FROM filtered_jobs
            WHERE event_time IS NOT NULL
            GROUP BY bucket_start, normalized_status
            ORDER BY bucket_start, normalized_status
            """,
            [*params, grain, timezone_name],
        )
        trend_counts = rows(trend_result)

    return {
        "generation": generation,
        **summary,
        "jobs_by_date_status": trend_counts,
    }


def _connection_filter_values(conn: Any, source_ids: list[int]) -> list[dict[str, Any]]:
    placeholders = ", ".join("?" for _ in source_ids)
    result = conn.execute(
        f"""
        SELECT connection_name AS value, COUNT(*) AS record_count
        FROM (
          SELECT DISTINCT _source_id, _file_uri, dataflow_run_id, job_id, connection_name
          FROM (
            SELECT _source_id, _file_uri, dataflow_run_id, job_id,
                   TRIM(CAST(source_name AS VARCHAR)) AS connection_name
            FROM {DATAFLOW_TABLE}
            WHERE _source_id IN ({placeholders})
              AND source_name IS NOT NULL
              AND TRIM(CAST(source_name AS VARCHAR)) <> ''
            UNION ALL
            SELECT _source_id, _file_uri, dataflow_run_id, job_id,
                   TRIM(CAST(destination_name AS VARCHAR)) AS connection_name
            FROM {DATAFLOW_TABLE}
            WHERE _source_id IN ({placeholders})
              AND destination_name IS NOT NULL
              AND TRIM(CAST(destination_name AS VARCHAR)) <> ''
          ) raw_connection_names
        ) connection_names
        GROUP BY connection_name
        ORDER BY connection_name
        """,
        [*source_ids, *source_ids],
    )
    return [
        {"value": row["value"], "label": row["value"], "count": row["record_count"]}
        for row in rows(result)
    ]
