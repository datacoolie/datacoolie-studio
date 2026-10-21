from __future__ import annotations

from collections import defaultdict
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.analytics.schema import (
    DATAFLOW_TABLE,
    FILTER_VALUES_TABLE,
)
from datacoolie_studio.domains.monitoring.read_models.common import (
    AnalyticsContext,
    filtered_ctes,
    one,
    reader_context,
    rows,
)
from datacoolie_studio.domains.monitoring.read_models.filter_counts import all_time_missing_counts, range_filter_counts
from datacoolie_studio.domains.monitoring.dimensions import UNKNOWN_KEY, dimension_key, dimension_label, dimension_text_sql, dimension_predicate_sql, known_dimension_sql


def monitoring_filter_options_read_model(
    paths: list[EnvironmentSource],
    analytics_context: AnalyticsContext | None = None,
    *,
    filters: dict[str, str] | None = None,
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
        catalog: dict[str, dict[str, int]] = defaultdict(dict)
        connections: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for row in rows(result):
            key = dimension_key(row['value'])
            if row['field'] == 'connection':
                if key != UNKNOWN_KEY:
                    connections[key].append((str(row['value']), int(row['record_count'])))
                continue
            if key == UNKNOWN_KEY:
                continue
            field = str(row['field'])
            catalog[field][key] = catalog[field].get(key, 0) + int(row['record_count'])
        options = {field: [{'value': key, 'label': dimension_label(key), 'count': count} for key, count in values.items()] for field, values in catalog.items()}
        # Published connection aggregates cannot recover deduplication after
        # canonical whitespace normalization merges two endpoint names.
        options['connection'] = _connection_filter_values(conn, source_ids, connections, counts_required=not filters or filters.get('range') == 'all')
        missing = all_time_missing_counts(conn, source_ids, catalog)
        for (field, key), count in missing.items():
            if count and field != 'connection':
                options.setdefault(field, []).append({'value': key, 'label': dimension_label(key), 'count': count})
        if filters and filters.get("range") != "all":
            counts = range_filter_counts(conn, source_ids, filters)
            for field, items in options.items():
                for item in items:
                    item["count"] = counts.get((field, str(item["value"])), 0)
        for items in options.values():
            items.sort(key=lambda item: (item['value'] == UNKNOWN_KEY, item['label'], item['value']))
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
              (SELECT COUNT(DISTINCT {known_dimension_sql('engine_name')}) FROM filtered_jobs) AS active_engines,
              (SELECT COUNT(DISTINCT {known_dimension_sql('metadata_provider_name')}) FROM filtered_jobs) AS active_metadata_providers,
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


def _connection_filter_values(conn: Any, source_ids: list[int], catalog: dict[str, list[tuple[str, int]]], *, counts_required: bool) -> list[dict[str, Any]]:
    """Reuse exact published counts; recount only normalization collisions/missing."""
    values = {key: sum(count for _, count in entries) for key, entries in catalog.items()}
    collisions = [key for key, entries in catalog.items() if len(entries) > 1] if counts_required else []
    placeholders = ', '.join('?' for _ in source_ids)
    selections, params = [], []
    for column in ('source_name', 'destination_name'):
        name = dimension_text_sql(column)
        predicate, bound = dimension_predicate_sql(column, [UNKNOWN_KEY, *collisions])
        selections.append(f"SELECT _source_id, _file_uri, dataflow_run_id, job_id, {name} AS connection_name FROM {DATAFLOW_TABLE} WHERE _source_id IN ({placeholders}) AND ({predicate})")
        params.extend([*source_ids, *bound])
    result = conn.execute(f"""
        SELECT connection_name AS value, COUNT(*) AS record_count
        FROM (SELECT DISTINCT _source_id, _file_uri, dataflow_run_id, job_id, connection_name
              FROM ({' UNION ALL '.join(selections)}) raw_connection_names) connection_names
        GROUP BY connection_name
    """, params)
    for row in rows(result):
        values[dimension_key(row['value'])] = int(row['record_count'])
    return [{'value': key, 'label': dimension_label(key), 'count': count} for key, count in values.items()]
