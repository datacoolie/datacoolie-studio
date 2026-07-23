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


_DATAFLOW_COLUMNS = (
    "_source_id", "job_id", "dataflow_id", "dataflow_run_id", "dataflow_name",
    "stage", "start_time", "end_time", "duration_seconds", "operation_type",
    "source_rows_read", "destination_rows_written", "destination_bytes_added",
)
_JOB_COLUMNS = (
    "_source_id", "job_id", "status", "start_time", "end_time", "duration_seconds",
    "engine_name", "metadata_provider_name", "platform_name", "stages", "operation_types",
    "total_dataflows", "total_succeeded", "total_failed", "total_skipped",
    "total_running", "total_pending", "total_rows_read", "total_rows_written",
)


def jobs_read_model(
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
        trend = rows(conn.execute(f"{ctes} {_TREND_SQL}", [*params, grain, timezone_name]))
        windows = operation_windows(
            conn,
            ctes,
            params,
            timezone_name=timezone_name,
            now=now,
        )
        job_duration_by_operation = duration_distribution(
            conn,
            ctes,
            params,
            group_column="operation_types",
            output_key="operation_type",
            limit=12,
            fact_kind="job",
        )
        workload_efficiency = rows(conn.execute(f"{ctes} {_WORKLOAD_EFFICIENCY_SQL}", params))
        child_fanout = rows(conn.execute(f"{ctes} {_CHILD_FANOUT_SQL}", params))
        status_by_stage = rows(conn.execute(f"{ctes} {_STATUS_BY_STAGE_SQL}", params))
        latest_failed = one(conn.execute(f"{ctes} {_LATEST_FAILED_SQL}", params))
        reconciliation_checks = rows(conn.execute(f"{ctes} {_RECONCILIATION_SQL}", params))
    return {
        "generation": generation,
        "summary": summary,
        "status_trend": trend,
        "windows": windows,
        "job_duration_by_operation": job_duration_by_operation,
        "workload_efficiency": workload_efficiency,
        "child_fanout": child_fanout,
        "status_by_stage": status_by_stage,
        "latest_failed_job": latest_failed or None,
        "reconciliation_checks": reconciliation_checks,
    }


def _empty_read_model(generation: str) -> dict[str, Any]:
    return {
        "generation": generation,
        "summary": {},
        "status_trend": [],
        "windows": empty_operation_windows(),
        "job_duration_by_operation": [],
        "workload_efficiency": [],
        "child_fanout": [],
        "status_by_stage": [],
        "latest_failed_job": None,
        "reconciliation_checks": [],
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
  COUNT(DISTINCT NULLIF(engine_name, '')) AS active_engines,
  COUNT(DISTINCT NULLIF(metadata_provider_name, '')) AS active_metadata_providers,
  COUNT(*) AS total_jobs,
  COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS total_succeeded,
  COUNT(*) FILTER (WHERE normalized_status = 'failed') AS total_failures,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'succeeded') /
    NULLIF(COUNT(*) FILTER (WHERE normalized_status IN ('succeeded','failed')), 0), 2) AS job_success_rate,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'failed') /
    NULLIF(COUNT(*) FILTER (WHERE normalized_status IN ('succeeded','failed')), 0), 2) AS job_failure_rate,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'skipped') / NULLIF(COUNT(*), 0), 2) AS job_skip_rate,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'running') / NULLIF(COUNT(*), 0), 2) AS job_running_rate,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'pending') / NULLIF(COUNT(*), 0), 2) AS job_pending_rate,
  COALESCE(SUM(total_rows_read), 0) + COALESCE(SUM(total_rows_written), 0) AS total_rows_processed,
  COALESCE(SUM(total_skipped), 0) AS total_skipped,
  COALESCE(SUM(total_pending), 0) AS total_pending,
  COALESCE(SUM(total_running), 0) AS total_running,
  COUNT(duration_seconds) FILTER (WHERE normalized_status IN ('succeeded','failed')) AS duration_count,
  COALESCE(ROUND(AVG(duration_seconds) FILTER (WHERE normalized_status IN ('succeeded','failed')), 3), 0) AS avg_duration_seconds,
  {discrete_percentile("duration_seconds", 0.25, "normalized_status IN ('succeeded','failed')")} AS q1_duration_seconds,
  {discrete_percentile("duration_seconds", 0.50, "normalized_status IN ('succeeded','failed')")} AS p50_duration_seconds,
  {discrete_percentile("duration_seconds", 0.75, "normalized_status IN ('succeeded','failed')")} AS q3_duration_seconds,
  {discrete_percentile("duration_seconds", 0.95, "normalized_status IN ('succeeded','failed')")} AS p95_duration_seconds,
  {discrete_percentile("duration_seconds", 0.99, "normalized_status IN ('succeeded','failed')")} AS p99_duration_seconds,
  COALESCE(ROUND(MAX(duration_seconds) FILTER (WHERE normalized_status IN ('succeeded','failed')), 3), 0) AS max_duration_seconds,
  (SELECT COUNT(*) FROM filtered_dataflows) AS total_dataflows,
  (SELECT COUNT(*) FROM filtered_dataflows WHERE normalized_status = 'failed') AS failed_dataflows,
  (SELECT COUNT(*) FROM filtered_dataflows WHERE normalized_status = 'succeeded') AS succeeded_dataflows,
  (SELECT COUNT(*) FROM filtered_dataflows WHERE normalized_status = 'skipped') AS skipped_dataflows,
  (SELECT COUNT(*) FROM filtered_dataflows WHERE normalized_status = 'running') AS running_dataflows,
  (SELECT COUNT(*) FROM filtered_dataflows WHERE normalized_status = 'pending') AS pending_dataflows
FROM filtered_jobs
"""

_TREND_SQL = """
SELECT date_trunc(?, timezone(?, event_time)) AS bucket_start,
       normalized_status AS status, COUNT(*) AS count, MIN(event_time) AS end_time
FROM filtered_jobs
WHERE event_time IS NOT NULL
GROUP BY bucket_start, normalized_status
ORDER BY bucket_start, normalized_status
"""

_WORKLOAD_EFFICIENCY_SQL = """
, latest_jobs AS (
  SELECT * EXCLUDE (job_rank) FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY _source_id, job_id ORDER BY event_time DESC NULLS LAST) AS job_rank
    FROM filtered_jobs
  ) WHERE job_rank = 1
), child_workload AS (
  SELECT _source_id, job_id, COALESCE(NULLIF(operation_type, ''), 'unknown') AS operation_type,
         SUM(COALESCE(duration_seconds, 0)) AS duration_seconds,
         COUNT(*) AS child_dataflow_count,
         COUNT(*) FILTER (WHERE normalized_status = 'failed') AS failed_child_dataflows,
         COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skipped_child_dataflows,
         SUM(COALESCE(source_rows_read, 0)) AS rows_read,
         SUM(COALESCE(destination_rows_written, 0)) AS rows_written,
         SUM(COALESCE(destination_bytes_added, 0)) AS bytes_added
  FROM filtered_dataflows
  WHERE job_id IS NOT NULL
  GROUP BY _source_id, job_id, operation_type
)
SELECT w.job_id,
       COALESCE(j.normalized_status,
         CASE WHEN w.failed_child_dataflows > 0 THEN 'failed' ELSE 'unknown' END) AS status,
       w.operation_type,
       COALESCE(NULLIF(j.engine_name, ''), 'unknown') AS engine_name,
       COALESCE(NULLIF(j.metadata_provider_name, ''), 'unknown') AS metadata_provider_name,
       COALESCE(NULLIF(j.platform_name, ''), 'unknown') AS platform_name,
       w.duration_seconds, w.child_dataflow_count, w.failed_child_dataflows,
       w.skipped_child_dataflows, w.rows_read, w.rows_written, w.bytes_added,
       CASE WHEN w.rows_read > 0 AND w.duration_seconds > 0 THEN w.rows_read / w.duration_seconds ELSE 0 END AS workload_size,
       'rows_read_per_second' AS workload_size_metric
FROM child_workload w
LEFT JOIN latest_jobs j ON j._source_id = w._source_id AND j.job_id = w.job_id
WHERE w.duration_seconds > 0
ORDER BY w.duration_seconds DESC, w.child_dataflow_count DESC, w.job_id
LIMIT 500
"""

_CHILD_FANOUT_SQL = """
SELECT total_dataflows,
       COUNT(*) AS jobs,
       COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS succeeded,
       COUNT(*) FILTER (WHERE normalized_status = 'failed') AS failed,
       COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skipped,
       COUNT(*) FILTER (WHERE normalized_status = 'running') AS running,
       COUNT(*) FILTER (WHERE normalized_status = 'pending') AS pending,
       COUNT(*) FILTER (WHERE normalized_status NOT IN ('succeeded','failed','skipped','running','pending')) AS unknown
FROM filtered_jobs
WHERE total_dataflows > 0
GROUP BY total_dataflows
ORDER BY total_dataflows
"""

_STATUS_BY_STAGE_SQL = """
, latest_jobs AS (
  SELECT * EXCLUDE (job_rank) FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY _source_id, job_id ORDER BY event_time DESC NULLS LAST) AS job_rank
    FROM filtered_jobs
  ) WHERE job_rank = 1
), stage_jobs AS (
  SELECT DISTINCT _source_id, job_id, COALESCE(NULLIF(stage, ''), 'unknown') AS stage
  FROM filtered_dataflows WHERE job_id IS NOT NULL
)
SELECT s.stage,
       COUNT(*) AS touched_jobs,
       COUNT(*) FILTER (WHERE j.normalized_status = 'succeeded') AS succeeded,
       COUNT(*) FILTER (WHERE j.normalized_status = 'failed') AS failed,
       COUNT(*) FILTER (WHERE j.normalized_status = 'skipped') AS skipped,
       COUNT(*) FILTER (WHERE j.normalized_status = 'running') AS running,
       COUNT(*) FILTER (WHERE j.normalized_status = 'pending') AS pending,
       COUNT(*) FILTER (WHERE j.normalized_status IS NULL OR j.normalized_status NOT IN ('succeeded','failed','skipped','running','pending')) AS unknown
FROM stage_jobs s
LEFT JOIN latest_jobs j ON j._source_id = s._source_id AND j.job_id = s.job_id
GROUP BY s.stage
ORDER BY failed DESC, touched_jobs DESC, s.stage
"""

_LATEST_FAILED_SQL = """
SELECT job_id, status, start_time, end_time, duration_seconds, engine_name,
       metadata_provider_name, platform_name, stages, operation_types
FROM filtered_jobs
WHERE normalized_status = 'failed' AND event_time IS NOT NULL
ORDER BY event_time DESC, job_id
LIMIT 1
"""

_RECONCILIATION_SQL = """
, child_counts AS (
  SELECT _source_id, job_id,
         COUNT(*) AS total_dataflows,
         COUNT(*) FILTER (WHERE normalized_status = 'failed') AS total_failed,
         COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS total_skipped,
         COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS total_succeeded
  FROM filtered_dataflows WHERE job_id IS NOT NULL
  GROUP BY _source_id, job_id
), comparisons AS (
  SELECT j.job_id, metric, expected, observed
  FROM filtered_jobs j
  LEFT JOIN child_counts c ON c._source_id = j._source_id AND c.job_id = j.job_id
  CROSS JOIN LATERAL (VALUES
    ('total_dataflows', j.total_dataflows, COALESCE(c.total_dataflows, 0)),
    ('total_failed', j.total_failed, COALESCE(c.total_failed, 0)),
    ('total_skipped', j.total_skipped, COALESCE(c.total_skipped, 0)),
    ('total_succeeded', j.total_succeeded, COALESCE(c.total_succeeded, 0))
  ) metrics(metric, expected, observed)
)
SELECT 'warning' AS severity, job_id, metric,
       CAST(expected AS BIGINT) AS expected, CAST(observed AS BIGINT) AS observed,
       CAST(observed - expected AS BIGINT) AS difference
FROM comparisons
WHERE expected IS NOT NULL AND CAST(expected AS BIGINT) <> CAST(observed AS BIGINT)
ORDER BY job_id, metric
LIMIT 50
"""
