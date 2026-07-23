from __future__ import annotations

from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.read_models.common import (
    AnalyticsContext,
    discrete_percentile,
    filtered_ctes,
    one,
    reader_context,
    rows,
    standalone_derived_query,
)
from datacoolie_studio.domains.monitoring.read_models.duration_distribution import (
    duration_distribution,
)
from datacoolie_studio.domains.monitoring.read_models.runtime_phase import (
    pivot_runtime_phase,
    runtime_phase_query,
)


_DATAFLOW_COLUMNS = (
    "_source_id", "job_id", "dataflow_id", "dataflow_run_id", "dataflow_name",
    "stage", "status", "start_time", "end_time", "duration_seconds", "operation_type",
    "source_name", "source_connection_type", "source_format", "source_full_table",
    "source_table", "source_path", "source_status", "source_duration_seconds",
    "source_rows_read", "source_error_message", "transform_status",
    "transform_duration_seconds", "transform_error_message", "destination_name",
    "destination_connection_type", "destination_format", "destination_full_table",
    "destination_table", "destination_path", "destination_load_type",
    "destination_status", "destination_duration_seconds", "destination_rows_written",
    "destination_rows_inserted", "destination_rows_updated", "destination_rows_deleted",
    "destination_bytes_added", "destination_bytes_removed", "destination_bytes_saved",
    "destination_files_added", "destination_files_removed", "destination_error_message",
    "overhead_duration_seconds", "error_message",
)
_JOB_COLUMNS = ("_source_id", "job_id")


def performance_read_model(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    *,
    grain: str,
    timezone_name: str,
    analytics_context: AnalyticsContext | None = None,
) -> dict[str, Any]:
    with reader_context(paths, analytics_context) as (conn, source_ids, generation):
        if conn is None or not source_ids:
            return _empty(generation)
        ctes, params = filtered_ctes(
            source_ids,
            filters,
            dataflow_columns=_DATAFLOW_COLUMNS,
            job_columns=_JOB_COLUMNS,
        )
        performance_ctes = f"{ctes} {_DERIVED_CTES}"
        bundle = one(conn.execute(
            f"{performance_ctes} {_BUNDLE_SQL}",
            [*params, grain, timezone_name],
        ))
        distribution = duration_distribution(
            conn, ctes, params, group_column="stage", output_key="stage", limit=100,
            eligible_statuses=("succeeded", "failed"),
        )
        phase = pivot_runtime_phase(
            bundle.get("phase") or [],
            "context",
            100,
        )
        efficiency = [_efficiency_point(row) for row in bundle.get("efficiency") or []]
        profiles = bundle.get("profiles") or []
        contexts = bundle.get("contexts") or []
        trend = bundle.get("trend") or []
    return {
        "generation": generation, "summary": bundle.get("summary") or {},
        "duration_distribution_by_stage": distribution,
        "phase_contribution_by_stage_operation": phase,
        "workload_efficiency_points": efficiency,
        "slowest_dataflow_profiles": profiles,
        "runtime_context_profiles": contexts,
        "performance_trend": [_trend_row(row, grain) for row in trend],
    }


def performance_attention_sql() -> str:
    return standalone_derived_query(_DERIVED_CTES, _ATTENTION_SQL)


def performance_evidence_read_model(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    *,
    limit: int,
    offset: int,
    sort_by: str,
    sort_dir: str,
    analytics_context: AnalyticsContext | None = None,
) -> dict[str, Any]:
    sort_columns = {
        "job_id": "job_id", "dataflow_name": "dataflow_name", "stage": "stage",
        "performance_bottleneck_phase": "performance_bottleneck_phase",
        "duration_seconds": "duration_seconds", "start_time": "start_time",
        "end_time": "end_time", "status": "status",
        "performance_candidate_priority": "performance_candidate_priority",
        "performance_candidate_reason": "performance_candidate_reason",
    }
    order_column = sort_columns.get(sort_by, "performance_candidate_priority")
    direction = "ASC" if sort_dir == "asc" else "DESC"
    with reader_context(paths, analytics_context) as (conn, source_ids, generation):
        if conn is None or not source_ids:
            return {"generation": generation, "records": [], "total_records": 0}
        ctes, params = filtered_ctes(
            source_ids,
            filters,
            dataflow_columns=_DATAFLOW_COLUMNS,
            job_columns=_JOB_COLUMNS,
        )
        performance_ctes = f"{ctes} {_DERIVED_CTES}"
        total = int(one(conn.execute(
            f"{performance_ctes} SELECT COUNT(*) AS total_records FROM candidates",
            params,
        )).get("total_records") or 0)
        records = rows(conn.execute(
            f"{performance_ctes} {_QUEUE_SELECT_SQL} "
            f"ORDER BY {order_column} {direction} NULLS LAST, duration_seconds DESC, event_time DESC NULLS LAST "
            "LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ))
    return {"generation": generation, "records": records, "total_records": total}


def _empty(generation: str) -> dict[str, Any]:
    return {
        "generation": generation, "summary": {}, "duration_distribution_by_stage": [],
        "phase_contribution_by_stage_operation": [],
        "workload_efficiency_points": [], "slowest_dataflow_profiles": [],
        "runtime_context_profiles": [], "performance_trend": [],
    }


def _trend_row(row: dict[str, Any], grain: str) -> dict[str, Any]:
    return {
        "date": row.get("bucket_start"), "bucket": row.get("bucket_start"),
        "bucket_start": row.get("bucket_start"), "bucket_end": None, "grain": grain,
        **{key: value for key, value in row.items() if key != "bucket_start"},
    }


def _efficiency_point(row: dict[str, Any]) -> list[Any]:
    return [row.get(key) for key in (
        "dataflow_run_id", "dataflow_name", "stage", "operation_type",
        "duration_seconds", "rows_processed", "maintenance_bytes_processed",
        "maintenance_files_processed", "rows_read_per_second",
        "destination_bytes_added", "destination_bytes_removed",
        "performance_bottleneck_phase", "performance_candidate_reason",
        "performance_candidate_priority",
    )]


_ROWS_PROCESSED = """GREATEST(
  COALESCE(source_rows_read, 0), COALESCE(destination_rows_written, 0),
  COALESCE(destination_rows_inserted, 0) + COALESCE(destination_rows_updated, 0) + COALESCE(destination_rows_deleted, 0)
)"""
_MAINT_BYTES = """GREATEST(0, COALESCE(destination_bytes_added, 0) + COALESCE(destination_bytes_removed, 0) + COALESCE(destination_bytes_saved, 0))"""
_MAINT_FILES = """GREATEST(0, COALESCE(destination_files_added, 0) + COALESCE(destination_files_removed, 0))"""
_OVERHEAD = """GREATEST(0, COALESCE(overhead_duration_seconds,
  COALESCE(duration_seconds, 0) - COALESCE(source_duration_seconds, 0) - COALESCE(transform_duration_seconds, 0) - COALESCE(destination_duration_seconds, 0)))"""

_DERIVED_CTES = f"""
, executable AS (
  SELECT *, COALESCE(NULLIF(operation_type, ''), 'unknown') AS operation_group,
         {_ROWS_PROCESSED} AS rows_processed,
         {_MAINT_BYTES} AS maintenance_bytes_processed,
         {_MAINT_FILES} AS maintenance_files_processed,
         GREATEST(0, COALESCE(source_duration_seconds, 0)) AS source_phase,
         GREATEST(0, COALESCE(transform_duration_seconds, 0)) AS transform_phase,
         GREATEST(0, COALESCE(destination_duration_seconds, 0)) AS destination_phase,
         {_OVERHEAD} AS overhead_phase
  FROM filtered_dataflows
  WHERE normalized_status IN ('succeeded','failed') AND duration_seconds IS NOT NULL
), thresholds AS (
  SELECT operation_group,
         {discrete_percentile('duration_seconds', 0.75)} AS duration_p75,
         {discrete_percentile('duration_seconds', 0.95)} AS duration_p95,
         {discrete_percentile('rows_processed', 0.50, 'rows_processed > 0')} AS rows_p50,
         {discrete_percentile('maintenance_bytes_processed', 0.50, 'maintenance_bytes_processed > 0')} AS maintenance_bytes_p50,
         {discrete_percentile('maintenance_files_processed', 0.50, 'maintenance_files_processed > 0')} AS maintenance_files_p50
  FROM executable GROUP BY operation_group
), measured AS (
  SELECT e.*, t.* EXCLUDE (operation_group),
         source_phase + transform_phase + destination_phase + overhead_phase AS phase_total,
         GREATEST(source_phase, transform_phase, destination_phase, overhead_phase) AS largest_phase_duration,
         CASE WHEN GREATEST(source_phase, transform_phase, destination_phase, overhead_phase) = source_phase THEN 'source'
              WHEN GREATEST(source_phase, transform_phase, destination_phase, overhead_phase) = transform_phase THEN 'transform'
              WHEN GREATEST(source_phase, transform_phase, destination_phase, overhead_phase) = destination_phase THEN 'destination'
              WHEN GREATEST(source_phase, transform_phase, destination_phase, overhead_phase) = overhead_phase THEN 'overhead'
              ELSE 'unknown' END AS performance_bottleneck_phase
  FROM executable e JOIN thresholds t USING (operation_group)
), classified AS (
  SELECT *,
    operation_group <> 'maintenance' AND rows_p50 > 0 AND rows_processed > 0 AND rows_processed <= rows_p50 AND duration_p95 > 0 AND duration_seconds >= duration_p95 AS slow_small_workload,
    operation_group = 'maintenance' AND duration_p95 > 0 AND duration_seconds >= duration_p95 AND
      ((maintenance_bytes_p50 > 0 AND maintenance_bytes_processed > 0 AND maintenance_bytes_processed <= maintenance_bytes_p50) OR
       (maintenance_bytes_processed <= 0 AND maintenance_files_p50 > 0 AND maintenance_files_processed > 0 AND maintenance_files_processed <= maintenance_files_p50)) AS slow_small_maintenance,
    duration_p75 > 0 AND duration_seconds >= duration_p75 AND overhead_phase / NULLIF(phase_total, 0) >= 0.20 AS high_overhead,
    operation_group <> 'maintenance' AND duration_p75 > 0 AND duration_seconds >= duration_p75 AND largest_phase_duration / NULLIF(phase_total, 0) >= 0.90 AS phase_skew
  FROM measured
), candidates AS (
  SELECT *,
    CASE WHEN slow_small_workload THEN 'slow_small_workload'
         WHEN slow_small_maintenance THEN 'slow_small_maintenance'
         WHEN high_overhead THEN 'high_overhead'
         WHEN phase_skew THEN 'phase_skew' END AS performance_candidate_code,
    CASE WHEN slow_small_workload THEN 'Slow small workload'
         WHEN slow_small_maintenance THEN 'Slow small maintenance workload'
         WHEN high_overhead THEN 'High overhead'
         WHEN phase_skew THEN CONCAT(UPPER(LEFT(performance_bottleneck_phase, 1)), SUBSTR(performance_bottleneck_phase, 2), ' phase skew') END AS performance_candidate_reason,
    CASE WHEN slow_small_workload OR slow_small_maintenance THEN 300 WHEN high_overhead THEN 200 WHEN phase_skew THEN 100 ELSE 0 END AS performance_candidate_priority
  FROM classified
)
"""

_SUMMARY_SQL = f"""
SELECT
  (SELECT COUNT(*) FROM filtered_dataflows) AS dataflow_records,
  (SELECT COUNT(*) FROM filtered_jobs) AS job_records,
  (SELECT MIN(run_date) FROM filtered_dataflows) AS date_min,
  (SELECT MAX(run_date) FROM filtered_dataflows) AS date_max,
  (SELECT MAX(event_time) FROM filtered_dataflows) AS latest_dataflow_log_at,
  (SELECT MAX(event_time) FROM filtered_jobs) AS latest_job_log_at,
  GREATEST((SELECT MAX(event_time) FROM filtered_dataflows), (SELECT MAX(event_time) FROM filtered_jobs)) AS latest_log_at,
  (SELECT COUNT(DISTINCT NULLIF(engine_name, 'unknown')) FROM filtered_dataflows) AS active_engines,
  (SELECT COUNT(DISTINCT NULLIF(metadata_provider_name, 'unknown')) FROM filtered_dataflows) AS active_metadata_providers,
  COUNT(*) AS run_count, COALESCE(ROUND(AVG(duration_seconds), 3), 0) AS avg_duration_seconds,
  {discrete_percentile('duration_seconds', 0.50)} AS p50_duration_seconds,
  {discrete_percentile('duration_seconds', 0.75)} AS p75_duration_seconds,
  {discrete_percentile('duration_seconds', 0.95)} AS p95_duration_seconds,
  {discrete_percentile('duration_seconds', 0.99)} AS p99_duration_seconds,
  COALESCE(MAX(duration_seconds), 0) AS max_duration_seconds,
  COALESCE(MAX(duration_seconds), 0) AS slowest_run_duration_seconds,
  ARG_MAX(dataflow_name, duration_seconds) AS slowest_run_dataflow_name,
  ARG_MAX(dataflow_id, duration_seconds) AS slowest_run_dataflow_id,
  ARG_MAX(dataflow_run_id, duration_seconds) AS slowest_run_dataflow_run_id,
  ARG_MAX(job_id, duration_seconds) AS slowest_run_job_id,
  ARG_MAX(start_time, duration_seconds) AS slowest_run_start_time,
  ARG_MAX(end_time, duration_seconds) AS slowest_run_end_time,
  ARG_MAX(stage, duration_seconds) AS slowest_run_stage,
  ARG_MAX(operation_group, duration_seconds) AS slowest_run_operation_type,
  ARG_MAX(normalized_status, duration_seconds) AS slowest_run_status,
  CASE WHEN SUM(source_phase) >= GREATEST(SUM(transform_phase), SUM(destination_phase), SUM(overhead_phase)) THEN 'source'
       WHEN SUM(transform_phase) >= GREATEST(SUM(source_phase), SUM(destination_phase), SUM(overhead_phase)) THEN 'transform'
       WHEN SUM(destination_phase) >= GREATEST(SUM(source_phase), SUM(transform_phase), SUM(overhead_phase)) THEN 'destination'
       WHEN SUM(overhead_phase) > 0 THEN 'overhead' ELSE 'unknown' END AS bottleneck_phase,
  ROUND(100.0 * SUM(source_phase) / NULLIF(SUM(phase_total), 0), 2) AS source_duration_percent,
  ROUND(100.0 * SUM(transform_phase) / NULLIF(SUM(phase_total), 0), 2) AS transform_duration_percent,
  ROUND(100.0 * SUM(destination_phase) / NULLIF(SUM(phase_total), 0), 2) AS destination_duration_percent,
  ROUND(100.0 * SUM(overhead_phase) / NULLIF(SUM(phase_total), 0), 2) AS overhead_duration_percent,
  COALESCE(ROUND(SUM(source_rows_read) / NULLIF(SUM(duration_seconds), 0), 3), 0) AS rows_read_per_second,
  COALESCE(SUM(source_rows_read), 0) AS total_rows_read,
  COALESCE(SUM(destination_rows_written), 0) AS total_rows_written,
  COUNT(*) FILTER (WHERE performance_candidate_code IS NOT NULL) AS optimization_candidate_count,
  COUNT(*) FILTER (WHERE slow_small_workload) AS slow_small_workload_count,
  COUNT(*) FILTER (WHERE slow_small_maintenance) AS slow_small_maintenance_count,
  COUNT(*) FILTER (WHERE high_overhead) AS high_overhead_count,
  COUNT(*) FILTER (WHERE phase_skew) AS phase_skew_count
FROM candidates
"""

_ATTENTION_SQL = """
SELECT COUNT(*) FILTER (WHERE performance_candidate_code IS NOT NULL)
         AS optimization_candidate_count
FROM candidates
"""

_EFFICIENCY_SQL = """
SELECT dataflow_run_id,
       COALESCE(NULLIF(dataflow_name, ''), NULLIF(dataflow_id, ''), 'unknown') AS dataflow_name,
       COALESCE(NULLIF(stage, ''), 'unknown') AS stage, operation_group AS operation_type,
       duration_seconds, rows_processed, maintenance_bytes_processed, maintenance_files_processed,
       CASE WHEN duration_seconds > 0 THEN source_rows_read / duration_seconds ELSE 0 END AS rows_read_per_second,
       destination_bytes_added, destination_bytes_removed,
       performance_bottleneck_phase, performance_candidate_reason, performance_candidate_priority
FROM candidates
ORDER BY performance_candidate_priority DESC, duration_seconds DESC, event_time DESC NULLS LAST
LIMIT 200
"""

_PROFILE_SQL = f"""
SELECT dataflow_id, COALESCE(ARG_MAX(NULLIF(dataflow_name, ''), event_time), dataflow_id) AS dataflow_name,
       COALESCE(ARG_MAX(NULLIF(stage, ''), event_time), 'unknown') AS stage,
       MODE(operation_group) AS operation_type, COUNT(*) AS run_count,
       COALESCE(ROUND(AVG(duration_seconds), 3), 0) AS avg_duration_seconds,
       {discrete_percentile('duration_seconds', 0.50)} AS p50_duration_seconds,
       {discrete_percentile('duration_seconds', 0.95)} AS p95_duration_seconds,
       MAX(duration_seconds) AS max_duration_seconds,
       MODE(performance_bottleneck_phase) AS performance_bottleneck_phase,
       COUNT(*) FILTER (WHERE performance_candidate_code IS NOT NULL) AS candidate_count
FROM candidates WHERE dataflow_id IS NOT NULL AND TRIM(dataflow_id) NOT IN ('', 'unknown', 'none', 'null', 'nan')
GROUP BY dataflow_id ORDER BY p95_duration_seconds DESC, run_count DESC LIMIT 40
"""

_CONTEXT_SQL = f"""
SELECT COALESCE(NULLIF(platform_name, ''), 'unknown') AS platform_name,
       COALESCE(NULLIF(engine_name, ''), 'unknown') AS engine_name,
       COALESCE(NULLIF(metadata_provider_name, ''), 'unknown') AS metadata_provider_name,
       COUNT(*) AS run_count,
       COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS succeeded,
       COUNT(*) FILTER (WHERE normalized_status = 'failed') AS failed,
       ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'succeeded') / NULLIF(COUNT(*), 0), 2) AS success_rate,
       COALESCE(ROUND(AVG(duration_seconds), 3), 0) AS avg_duration_seconds,
       {discrete_percentile('duration_seconds', 0.50)} AS p50_duration_seconds,
       {discrete_percentile('duration_seconds', 0.95)} AS p95_duration_seconds,
       COALESCE(ROUND(SUM(source_rows_read) / NULLIF(SUM(duration_seconds), 0), 3), 0) AS rows_read_per_second,
       COUNT(*) FILTER (WHERE performance_candidate_code IS NOT NULL) AS candidate_count
FROM candidates GROUP BY platform_name, engine_name, metadata_provider_name
ORDER BY candidate_count DESC, p95_duration_seconds DESC, run_count DESC LIMIT 30
"""

_TREND_SQL = f"""
SELECT date_trunc(?, timezone(?, event_time)) AS bucket_start, COUNT(*) AS run_count,
       {discrete_percentile('duration_seconds', 0.50)} AS p50_duration_seconds,
       {discrete_percentile('duration_seconds', 0.95)} AS p95_duration_seconds,
       COUNT(*) FILTER (WHERE performance_candidate_code IS NOT NULL) AS candidate_count
FROM candidates WHERE event_time IS NOT NULL GROUP BY bucket_start ORDER BY bucket_start
"""

_BUNDLE_SQL = f"""
, summary_result AS (
  {_SUMMARY_SQL}
), efficiency_result AS (
  {_EFFICIENCY_SQL}
), profile_result AS (
  {_PROFILE_SQL}
), context_result AS (
  {_CONTEXT_SQL}
), trend_result AS (
  {_TREND_SQL}
), phase_result AS (
  {runtime_phase_query("context", standalone=True)}
)
SELECT
  (SELECT summary_row FROM summary_result summary_row) AS summary,
  (SELECT list(efficiency_row) FROM efficiency_result efficiency_row) AS efficiency,
  (SELECT list(profile_row) FROM profile_result profile_row) AS profiles,
  (SELECT list(context_row) FROM context_result context_row) AS contexts,
  (SELECT list(trend_row) FROM trend_result trend_row) AS trend,
  (SELECT list(phase_row) FROM phase_result phase_row) AS phase
"""

_QUEUE_SELECT_SQL = """
SELECT job_id, dataflow_id, dataflow_run_id,
       COALESCE(NULLIF(dataflow_name, ''), NULLIF(dataflow_id, ''), 'unknown') AS dataflow_name,
       COALESCE(NULLIF(stage, ''), 'unknown') AS stage, normalized_status AS status,
       start_time, end_time, duration_seconds, operation_group AS operation_type,
       engine_name, metadata_provider_name, platform_name,
       source_name, source_connection_type, source_format, source_full_table, source_table, source_path,
       source_status, source_phase AS source_duration_seconds, source_rows_read, source_error_message,
       transform_status, transform_phase AS transform_duration_seconds, transform_error_message,
       destination_name, destination_connection_type, destination_format, destination_full_table,
       destination_table, destination_path, destination_load_type, destination_status,
       destination_phase AS destination_duration_seconds, destination_rows_written,
       destination_rows_inserted, destination_rows_updated, destination_rows_deleted,
       destination_files_added, destination_files_removed,
       destination_bytes_added, destination_bytes_removed, destination_bytes_saved,
       destination_error_message,
       overhead_phase AS overhead_duration_seconds, error_message,
       performance_bottleneck_phase, performance_candidate_code, performance_candidate_reason,
       performance_candidate_priority, rows_processed AS performance_rows_processed,
       CASE WHEN duration_seconds > 0 THEN rows_processed / duration_seconds ELSE 0 END AS performance_rows_per_second,
       overhead_phase / NULLIF(phase_total, 0) AS performance_overhead_ratio,
       largest_phase_duration / NULLIF(phase_total, 0) AS performance_dominant_phase_ratio
FROM candidates
"""
