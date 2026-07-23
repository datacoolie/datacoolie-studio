from __future__ import annotations

from datetime import datetime
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.read_models.common import (
    AnalyticsContext,
    discrete_percentile,
    filtered_ctes,
    one,
    reader_context,
    rows,
)
from datacoolie_studio.domains.monitoring.read_models.duration_distribution import duration_distribution
from datacoolie_studio.domains.monitoring.read_models.operation_windows import (
    empty_operation_windows,
    operation_windows,
)
from datacoolie_studio.domains.monitoring.read_models.runtime_phase import runtime_phase_summary


_DATAFLOW_COLUMNS = (
    "_source_id", "job_id", "dataflow_id", "dataflow_run_id", "dataflow_name",
    "stage", "start_time", "end_time", "duration_seconds", "operation_type",
    "source_name", "source_connection_type", "source_format", "source_status",
    "source_duration_seconds", "source_rows_read", "transform_status",
    "transform_duration_seconds", "destination_name", "destination_connection_type",
    "destination_format", "destination_status", "destination_duration_seconds",
    "destination_rows_written", "destination_bytes_added", "destination_bytes_removed",
    "overhead_duration_seconds",
)
_JOB_COLUMNS = (
    "_source_id", "job_id", "engine_name", "metadata_provider_name", "platform_name",
    "total_running", "total_pending", "total_skipped",
)
def dataflows_read_model(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    *,
    grain: str,
    timezone_name: str,
    now: datetime | None = None,
    analytics_context: AnalyticsContext | None = None,
) -> dict[str, Any]:
    with reader_context(paths, analytics_context) as (conn, source_ids, generation):
        if conn is None or not source_ids:
            return _empty_read_model(generation)
        ctes, params = filtered_ctes(
            source_ids,
            filters,
            dataflow_columns=_DATAFLOW_COLUMNS,
            job_columns=_JOB_COLUMNS,
        )
        summary = one(conn.execute(f"{ctes} {_SUMMARY_SQL}", params))
        trend = rows(conn.execute(
            f"{ctes} {_TREND_SQL}",
            [*params, grain, timezone_name],
        ))
        windows = operation_windows(
            conn,
            ctes,
            params,
            timezone_name=timezone_name,
            now=now,
        )
        duration_by_stage = duration_distribution(
            conn,
            ctes,
            params,
            group_column="stage",
            output_key="stage",
            limit=100,
        )
        phase_health = runtime_phase_summary(
            conn,
            ctes,
            params,
            group_column="stage",
            limit=100,
        )
        endpoint_health = rows(conn.execute(f"{ctes} {_ENDPOINT_HEALTH_SQL}", params))
        name_status_health = rows(conn.execute(f"{ctes} {_NAME_STATUS_HEALTH_SQL}", params))

    return {
        "generation": generation,
        "summary": summary,
        "status_trend": trend,
        "windows": windows,
        "duration_by_stage": duration_by_stage,
        "phase_health_by_stage": phase_health,
        "endpoint_health": endpoint_health,
        "name_status_health": name_status_health,
    }


def _empty_read_model(generation: str) -> dict[str, Any]:
    return {
        "generation": generation,
        "summary": {},
        "status_trend": [],
        "windows": empty_operation_windows(),
        "duration_by_stage": [],
        "phase_health_by_stage": [],
        "endpoint_health": [],
        "name_status_health": [],
    }


_SUMMARY_SQL = f"""
SELECT
  (SELECT COUNT(*) FROM filtered_dataflows) AS dataflow_records,
  (SELECT COUNT(*) FROM filtered_jobs) AS job_records,
  (SELECT MIN(run_date) FROM (
    SELECT run_date FROM filtered_dataflows UNION ALL SELECT run_date FROM filtered_jobs
  )) AS date_min,
  (SELECT MAX(run_date) FROM (
    SELECT run_date FROM filtered_dataflows UNION ALL SELECT run_date FROM filtered_jobs
  )) AS date_max,
  (SELECT MAX(event_time) FROM (
    SELECT event_time FROM filtered_dataflows UNION ALL SELECT event_time FROM filtered_jobs
  )) AS latest_log_at,
  (SELECT MAX(event_time) FROM filtered_jobs) AS latest_job_log_at,
  (SELECT MAX(event_time) FROM filtered_dataflows) AS latest_dataflow_log_at,
  (SELECT COUNT(DISTINCT NULLIF(engine_name, 'unknown')) FROM filtered_jobs) AS active_engines,
  (SELECT COUNT(DISTINCT NULLIF(metadata_provider_name, 'unknown')) FROM filtered_jobs) AS active_metadata_providers,
  COUNT(*) AS total_dataflows,
  COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS succeeded,
  COUNT(*) FILTER (WHERE normalized_status = 'failed') AS failed,
  COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skipped,
  COUNT(*) FILTER (WHERE normalized_status = 'running') AS running,
  COUNT(*) FILTER (WHERE normalized_status = 'pending') AS pending,
  COUNT(*) FILTER (WHERE normalized_status NOT IN ('succeeded','failed','skipped','running','pending')) AS unknown,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'succeeded') /
    NULLIF(COUNT(*) FILTER (WHERE normalized_status IN ('succeeded','failed')), 0), 2) AS success_rate,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'failed') /
    NULLIF(COUNT(*) FILTER (WHERE normalized_status IN ('succeeded','failed')), 0), 2) AS failure_rate,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'skipped') / NULLIF(COUNT(*), 0), 2) AS skip_rate,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'running') / NULLIF(COUNT(*), 0), 2) AS running_rate,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'pending') / NULLIF(COUNT(*), 0), 2) AS pending_rate,
  COALESCE(SUM(destination_bytes_added), 0) AS total_bytes_written,
  COALESCE(SUM(source_rows_read), 0) AS total_rows_read,
  COALESCE(SUM(destination_rows_written), 0) AS total_rows_written,
  COALESCE(SUM(destination_bytes_added), 0) - COALESCE(SUM(destination_bytes_removed), 0) AS net_bytes_change,
  COUNT(duration_seconds) FILTER (WHERE normalized_status IN ('succeeded','failed')) AS duration_count,
  COALESCE(ROUND(AVG(duration_seconds) FILTER (WHERE normalized_status IN ('succeeded','failed')), 3), 0) AS avg_duration_seconds,
  {discrete_percentile("duration_seconds", 0.25, "normalized_status IN ('succeeded','failed')")} AS q1_duration_seconds,
  {discrete_percentile("duration_seconds", 0.50, "normalized_status IN ('succeeded','failed')")} AS p50_duration_seconds,
  {discrete_percentile("duration_seconds", 0.75, "normalized_status IN ('succeeded','failed')")} AS q3_duration_seconds,
  {discrete_percentile("duration_seconds", 0.95, "normalized_status IN ('succeeded','failed')")} AS p95_duration_seconds,
  {discrete_percentile("duration_seconds", 0.99, "normalized_status IN ('succeeded','failed')")} AS p99_duration_seconds,
  COALESCE(ROUND(MAX(duration_seconds) FILTER (WHERE normalized_status IN ('succeeded','failed')), 3), 0) AS max_duration_seconds,
  COUNT(DISTINCT NULLIF(engine_name, 'unknown')) AS dataflow_active_engines
FROM filtered_dataflows
"""

_TREND_SQL = """
SELECT
  date_trunc(?, timezone(?, event_time)) AS bucket_start,
  normalized_status AS status,
  COUNT(*) AS count,
  MIN(event_time) AS end_time
FROM filtered_dataflows
WHERE event_time IS NOT NULL
GROUP BY bucket_start, normalized_status
ORDER BY bucket_start, normalized_status
"""

_ENDPOINT_HEALTH_SQL = f"""
SELECT
  COALESCE(NULLIF(source_name, ''), 'unknown') AS source_name,
  COALESCE(NULLIF(destination_name, ''), 'unknown') AS destination_name,
  COALESCE(MODE(source_format), 'unknown') AS source_format,
  COALESCE(MODE(destination_format), 'unknown') AS destination_format,
  COALESCE(MODE(source_connection_type), 'unknown') AS source_connection_type,
  COALESCE(MODE(destination_connection_type), 'unknown') AS destination_connection_type,
  COUNT(*) AS runs,
  COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS succeeded,
  COUNT(*) FILTER (WHERE normalized_status = 'failed') AS failed,
  COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skipped,
  COUNT(*) FILTER (WHERE normalized_status = 'running') AS running,
  COUNT(*) FILTER (WHERE normalized_status = 'pending') AS pending,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'succeeded') /
    NULLIF(COUNT(*) FILTER (WHERE normalized_status IN ('succeeded','failed')), 0), 2) AS success_rate,
  COALESCE(ROUND(AVG(duration_seconds), 3), 0) AS avg_duration_seconds,
  {discrete_percentile("duration_seconds", 0.95)} AS p95_duration_seconds,
  COALESCE(SUM(source_rows_read), 0) AS rows_read,
  COALESCE(SUM(destination_rows_written), 0) AS rows_written,
  COALESCE(SUM(destination_bytes_added), 0) AS bytes_added,
  COALESCE(SUM(destination_bytes_removed), 0) AS bytes_removed
FROM filtered_dataflows
GROUP BY COALESCE(NULLIF(source_name, ''), 'unknown'), COALESCE(NULLIF(destination_name, ''), 'unknown')
ORDER BY failed DESC, p95_duration_seconds DESC, runs DESC, source_name, destination_name
LIMIT 18
"""

_NAME_STATUS_HEALTH_SQL = f"""
SELECT
  dataflow_id,
  COALESCE(ARG_MAX(NULLIF(dataflow_name, ''), event_time), dataflow_id) AS dataflow_name,
  COALESCE(ARG_MAX(NULLIF(stage, ''), event_time), 'unknown') AS stage,
  COALESCE(MODE(NULLIF(operation_type, '')), 'unknown') AS operation_type,
  ARG_MAX(NULLIF(source_name, ''), event_time) AS source_name,
  ARG_MAX(NULLIF(destination_name, ''), event_time) AS destination_name,
  COUNT(*) AS runs,
  COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS succeeded,
  COUNT(*) FILTER (WHERE normalized_status = 'failed') AS failed,
  COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skipped,
  COUNT(*) FILTER (WHERE normalized_status = 'running') AS running,
  COUNT(*) FILTER (WHERE normalized_status = 'pending') AS pending,
  COUNT(*) FILTER (WHERE normalized_status NOT IN ('succeeded','failed','skipped','running','pending')) AS unknown,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'succeeded') /
    NULLIF(COUNT(*) FILTER (WHERE normalized_status IN ('succeeded','failed')), 0), 2) AS success_rate,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'failed') /
    NULLIF(COUNT(*) FILTER (WHERE normalized_status IN ('succeeded','failed')), 0), 2) AS failure_rate,
  COALESCE(ROUND(AVG(duration_seconds), 3), 0) AS avg_duration_seconds,
  {discrete_percentile("duration_seconds", 0.95)} AS p95_duration_seconds,
  COALESCE(ROUND(MAX(duration_seconds), 3), 0) AS max_duration_seconds,
  COALESCE(SUM(source_rows_read), 0) AS rows_read,
  COALESCE(SUM(destination_rows_written), 0) AS rows_written,
  MAX(event_time) AS latest_time
FROM filtered_dataflows
WHERE dataflow_id IS NOT NULL AND TRIM(dataflow_id) NOT IN ('', 'unknown', 'none', 'null', 'nan')
GROUP BY dataflow_id
ORDER BY runs DESC, failed DESC, running + pending DESC, p95_duration_seconds DESC, dataflow_name
LIMIT 40
"""
