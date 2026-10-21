from __future__ import annotations

from datacoolie_studio.domains.monitoring.dimensions import known_dimension_sql

from datetime import datetime
from typing import Any
from datacoolie_studio.domains.monitoring.dimensions import dimension_key_sql

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.log_repository import monitoring_has_dataflow_scope
from datacoolie_studio.domains.monitoring.metrics.failure import (
    failure_category_sql,
    failure_message_sql,
)
from datacoolie_studio.domains.monitoring.read_models.common import (
    AnalyticsContext,
    nullable_discrete_percentile,
    filtered_ctes,
    one,
    reader_context,
    rows,
    trend_bucket_key,
)
from datacoolie_studio.domains.monitoring.read_models.operation_windows import (
    empty_operation_windows,
    operation_windows,
)
from datacoolie_studio.domains.monitoring.read_models.freshness import freshness_attention_sql
from datacoolie_studio.domains.monitoring.metrics.v6_semantics import register_freshness_functions
from datacoolie_studio.domains.monitoring.read_models.maintenance import maintenance_attention_sql
from datacoolie_studio.domains.monitoring.read_models.performance import performance_attention_sql
from datacoolie_studio.domains.monitoring.read_models.runtime_phase import runtime_phase_summary


_DATAFLOW_COLUMNS = (
    "destination_catalog", "destination_database", "destination_schema",
    "_source_id", "_ingested_at", "_record_line", "_file_uri", "_file_kind",
    "log_schema_version", "attempts", "read_request", "raw_record_json", "job_id", "dataflow_id", "dataflow_run_id", "dataflow_name",
    "stage", "status", "message", "start_time", "end_time", "duration_seconds", "operation_type",
    "source_name", "source_connection_type", "source_format", "source_status",
    "source_error_message", "source_duration_seconds", "source_rows_read",
    "source_configure",
    "source_watermark_columns", "transform_status", "transform_error_message",
    "transform_duration_seconds", "destination_name", "destination_path",
    "destination_table", "destination_full_table", "destination_load_type",
    "destination_connection_type", "destination_format", "destination_status",
    "destination_error_message", "destination_duration_seconds",
    "destination_operation_type", "destination_rows_written", "destination_rows_inserted",
    "destination_rows_updated", "destination_rows_deleted", "destination_bytes_added",
    "destination_bytes_removed", "destination_bytes_saved", "destination_files_added",
    "destination_files_removed", "overhead_duration_seconds", "error_message",
)
_JOB_COLUMNS = (
    "_source_id", "job_id", "status", "start_time", "end_time", "duration_seconds",
    "engine_name", "metadata_provider_name", "platform_name", "operation_types",
    "total_dataflows", "total_succeeded", "total_failed", "total_skipped",
    "total_running", "total_pending", "total_rows_read", "total_rows_written", "observed_at", "log_records_dropped",
)


def _metric_total(column: str, eligible: str) -> str:
    population = f"COUNT(*) FILTER (WHERE {eligible})"
    measured = f"COUNT({column}) FILTER (WHERE {eligible})"
    observed = f"SUM({column}) FILTER (WHERE {eligible})"
    return (
        f"CASE WHEN EXISTS (SELECT 1 FROM all_log_versions WHERE log_schema_version >= 7) "
        f"THEN CASE WHEN {measured} = 0 THEN NULL ELSE {observed} END "
        f"WHEN {population} = 0 THEN CASE WHEN COUNT(*) = 0 THEN 0 ELSE NULL END "
        f"WHEN {measured} = {population} THEN {observed} END"
    )


def _metric_observed(column: str, eligible: str) -> str:
    return f"COALESCE(SUM({column}) FILTER (WHERE {eligible}), 0)"


def _metric_measured(column: str, eligible: str) -> str:
    return f"COUNT({column}) FILTER (WHERE {eligible})"


def _metric_eligible(eligible: str) -> str:
    return f"COUNT(*) FILTER (WHERE {eligible})"


def _metric_difference(left: str, left_eligible: str, right: str, right_eligible: str) -> str:
    left_population = _metric_eligible(left_eligible)
    right_population = _metric_eligible(right_eligible)
    left_measured = _metric_measured(left, left_eligible)
    right_measured = _metric_measured(right, right_eligible)
    return (
        f"CASE WHEN {left_population} = 0 OR {right_population} = 0 THEN NULL "
        f"WHEN EXISTS (SELECT 1 FROM all_log_versions WHERE log_schema_version >= 7) "
        f"THEN CASE WHEN {left_measured} = 0 OR {right_measured} = 0 THEN NULL "
        f"ELSE {_metric_observed(left, left_eligible)} - {_metric_observed(right, right_eligible)} END "
        f"WHEN {left_measured} = {left_population} AND {right_measured} = {right_population} "
        f"THEN {_metric_observed(left, left_eligible)} - {_metric_observed(right, right_eligible)} END"
    )


def overview_read_model(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    *,
    grain: str,
    timezone_name: str,
    now: datetime | None = None,
    analytics_context: AnalyticsContext | None = None,
) -> dict[str, Any]:
    """Return only bounded aggregates consumed by the Monitoring Overview page."""
    with reader_context(paths, analytics_context) as (conn, source_ids, generation):
        if conn is None or not source_ids:
            return _empty_read_model(generation)
        register_freshness_functions(conn)
        ctes, params = filtered_ctes(
            source_ids,
            filters,
            dataflow_columns=_DATAFLOW_COLUMNS,
            job_columns=_JOB_COLUMNS,
        )
        summary = one(conn.execute(f"{ctes} {_SUMMARY_SQL}", params))
        trends = rows(conn.execute(
            f"{ctes} {_TRENDS_SQL}",
            [*params, grain, timezone_name, grain, timezone_name],
        ))
        runtime_contexts = rows(conn.execute(f"{ctes} {_RUNTIME_CONTEXT_SQL}", params))
        operation_health = rows(conn.execute(f"{ctes} {_OPERATION_HEALTH_SQL}", params))
        workload = rows(conn.execute(
            f"{ctes} {_WORKLOAD_SQL}", [*params, grain, timezone_name],
        ))
        categories = rows(conn.execute(f"{ctes} {_FAILURE_CATEGORY_SQL}", params))
        top_failures = rows(conn.execute(f"{ctes} {_TOP_FAILURES_SQL}", params))
        health = one(conn.execute(f"{ctes} {_HEALTH_SQL}", params))
        if monitoring_has_dataflow_scope(filters):
            health["mismatch_count"] = None
        attention = one(conn.execute(f"{ctes} {_ATTENTION_BUNDLE_SQL}", params))
        windows = operation_windows(
            conn, ctes, params, timezone_name=timezone_name, now=now,
        )
        phase_health = runtime_phase_summary(
            conn, ctes, params, group_column="operation_type", limit=40,
        )

    return {
        "generation": generation,
        "summary": summary,
        "job_status_trend": [row for row in trends if row.get("record_type") == "job"],
        "dataflow_status_trend": [row for row in trends if row.get("record_type") == "dataflow"],
        "runtime_contexts": runtime_contexts,
        "job_operation_health": [row for row in operation_health if row.get("record_type") == "job"],
        "dataflow_operation_health": [row for row in operation_health if row.get("record_type") == "dataflow"],
        "rows_by_date": [_workload_row(row, grain, "rows") for row in workload],
        "bytes_by_date": [_workload_row(row, grain, "bytes") for row in workload],
        "error_categories": categories,
        "top_failing_dataflows": top_failures,
        "health": health,
        "attention": attention,
        "windows": windows,
        "phase_health": phase_health,
    }


def _workload_row(row: dict[str, Any], grain: str, kind: str) -> dict[str, Any]:
    bucket_key = trend_bucket_key(row.get("bucket_start"), grain)
    shared = {
        "date": bucket_key,
        "bucket": bucket_key,
        "bucket_start": row.get("bucket_start"),
        "bucket_end": row.get("bucket_end"),
        "grain": grain,
    }
    if kind == "rows":
        keys = (
            "rows_read", "observed_rows_read", "rows_read_measured", "TRUE",
            "rows_written", "observed_rows_written", "rows_written_measured", "TRUE",
            "rows_output", "rows_inserted", "rows_updated", "rows_deleted",
            "dataflow_runs",
        )
    else:
        keys = (
            "bytes_added", "bytes_removed", "bytes_saved", "net_bytes",
            "files_added", "files_removed",
        )
    return {**shared, **{key: row.get(key) for key in keys}}


def _empty_read_model(generation: str) -> dict[str, Any]:
    return {
        "generation": generation,
        "summary": {},
        "job_status_trend": [],
        "dataflow_status_trend": [],
        "runtime_contexts": [],
        "job_operation_health": [],
        "dataflow_operation_health": [],
        "rows_by_date": [],
        "bytes_by_date": [],
        "error_categories": [],
        "top_failing_dataflows": [],
        "health": {},
        "attention": {},
        "windows": empty_operation_windows(),
        "phase_health": [],
    }


_JOB_DURATION_FIELDS = f"""
  COUNT(duration_seconds) FILTER (WHERE normalized_status IN ('succeeded','failed')) AS job_duration_count,
  ROUND(AVG(duration_seconds) FILTER (WHERE normalized_status IN ('succeeded','failed')), 3) AS job_avg_duration_seconds,
  {nullable_discrete_percentile('duration_seconds', 0.25, "normalized_status IN ('succeeded','failed')")} AS job_q1_duration_seconds,
  {nullable_discrete_percentile('duration_seconds', 0.50, "normalized_status IN ('succeeded','failed')")} AS job_p50_duration_seconds,
  {nullable_discrete_percentile('duration_seconds', 0.75, "normalized_status IN ('succeeded','failed')")} AS job_q3_duration_seconds,
  {nullable_discrete_percentile('duration_seconds', 0.95, "normalized_status IN ('succeeded','failed')")} AS job_p95_duration_seconds,
  {nullable_discrete_percentile('duration_seconds', 0.99, "normalized_status IN ('succeeded','failed')")} AS job_p99_duration_seconds,
  ROUND(MAX(duration_seconds) FILTER (WHERE normalized_status IN ('succeeded','failed')), 3) AS job_max_duration_seconds
"""
_DATAFLOW_DURATION_FIELDS = f"""
  COUNT(duration_seconds) FILTER (WHERE normalized_status IN ('succeeded','failed')) AS dataflow_duration_count,
  ROUND(AVG(duration_seconds) FILTER (WHERE normalized_status IN ('succeeded','failed')), 3) AS dataflow_avg_duration_seconds,
  {nullable_discrete_percentile('duration_seconds', 0.25, "normalized_status IN ('succeeded','failed')")} AS dataflow_q1_duration_seconds,
  {nullable_discrete_percentile('duration_seconds', 0.50, "normalized_status IN ('succeeded','failed')")} AS dataflow_p50_duration_seconds,
  {nullable_discrete_percentile('duration_seconds', 0.75, "normalized_status IN ('succeeded','failed')")} AS dataflow_q3_duration_seconds,
  {nullable_discrete_percentile('duration_seconds', 0.95, "normalized_status IN ('succeeded','failed')")} AS dataflow_p95_duration_seconds,
  {nullable_discrete_percentile('duration_seconds', 0.99, "normalized_status IN ('succeeded','failed')")} AS dataflow_p99_duration_seconds,
  ROUND(MAX(duration_seconds) FILTER (WHERE normalized_status IN ('succeeded','failed')), 3) AS dataflow_max_duration_seconds
"""

_SUMMARY_SQL = f"""
, job_summary AS (
  SELECT COUNT(*) AS job_records,
         COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS job_succeeded,
         COUNT(*) FILTER (WHERE normalized_status = 'failed') AS job_failed,
         COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS job_skipped,
         CASE
           WHEN COUNT(*) = 0 THEN 0
           WHEN COUNT(total_running) = COUNT(*) THEN SUM(total_running)
           ELSE NULL
         END AS job_running,
         CASE
           WHEN COUNT(*) = 0 THEN 0
           WHEN COUNT(total_pending) = COUNT(*) THEN SUM(total_pending)
           ELSE NULL
         END AS job_pending,
         CASE WHEN COUNT(*) = 0 THEN 0 WHEN COUNT(total_skipped) = COUNT(*) THEN SUM(total_skipped) END AS job_child_skipped,
         CASE WHEN EXISTS (SELECT 1 FROM all_log_versions WHERE log_schema_version >= 7) THEN
                CASE WHEN COUNT(total_rows_read) + COUNT(total_rows_written) = 0 THEN NULL
                     ELSE COALESCE(SUM(total_rows_read), 0) + COALESCE(SUM(total_rows_written), 0) END
              WHEN COUNT(*) = 0 THEN 0
              WHEN COUNT(total_rows_read) = COUNT(*) AND COUNT(total_rows_written) = COUNT(*)
              THEN SUM(total_rows_read) + SUM(total_rows_written) END AS total_rows_processed,
         COALESCE(SUM(total_rows_read), 0) AS observed_job_rows_read,
         COUNT(total_rows_read) AS job_rows_read_measured,
         COUNT(*) AS job_rows_read_eligible,
         COALESCE(SUM(total_rows_written), 0) AS observed_job_rows_written,
         COUNT(total_rows_written) AS job_rows_written_measured,
         COUNT(*) AS job_rows_written_eligible,
         COUNT(DISTINCT {known_dimension_sql('engine_name')}) AS active_engines,
         COUNT(DISTINCT {known_dimension_sql('metadata_provider_name')}) AS active_metadata_providers,
         MAX(event_time) AS latest_job_log_at,
         {_JOB_DURATION_FIELDS}
  FROM filtered_jobs
), dataflow_summary AS (
  SELECT COUNT(*) AS dataflow_records,
         COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS dataflow_succeeded,
         COUNT(*) FILTER (WHERE normalized_status = 'failed') AS dataflow_failed,
         COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS dataflow_skipped,
         COUNT(*) FILTER (WHERE normalized_status = 'running') AS dataflow_running,
         COUNT(*) FILTER (WHERE normalized_status = 'pending') AS dataflow_pending,
          {_metric_total('destination_bytes_added', 'TRUE')} AS total_bytes_written,
          {_metric_observed('destination_bytes_added', 'TRUE')} AS observed_bytes_written,
          {_metric_measured('destination_bytes_added', 'TRUE')} AS bytes_written_measured,
          {_metric_eligible('TRUE')} AS bytes_written_eligible,
          {_metric_total('source_rows_read', 'TRUE')} AS total_rows_read,
          {_metric_observed('source_rows_read', 'TRUE')} AS observed_rows_read,
          {_metric_measured('source_rows_read', 'TRUE')} AS rows_read_measured,
          {_metric_eligible('TRUE')} AS rows_read_eligible,
          {_metric_total('destination_rows_written', 'TRUE')} AS total_rows_written,
          {_metric_observed('destination_rows_written', 'TRUE')} AS observed_rows_written,
          {_metric_measured('destination_rows_written', 'TRUE')} AS rows_written_measured,
          {_metric_eligible('TRUE')} AS rows_written_eligible,
         COUNT(DISTINCT {known_dimension_sql('engine_name')}) AS dataflow_active_engines,
         MAX(event_time) AS latest_dataflow_log_at,
         {_DATAFLOW_DURATION_FIELDS}
  FROM filtered_dataflows
), all_dates AS (
  SELECT run_date, event_time FROM filtered_jobs
  UNION ALL SELECT run_date, event_time FROM filtered_dataflows
)
SELECT j.*, d.*,
       (SELECT MIN(run_date) FROM all_dates) AS date_min,
       (SELECT MAX(run_date) FROM all_dates) AS date_max,
       (SELECT MAX(event_time) FROM all_dates) AS latest_log_at
FROM job_summary j CROSS JOIN dataflow_summary d
"""

_TRENDS_SQL = """
SELECT 'job' AS record_type, date_trunc(?, timezone(?, event_time)) AS bucket_start,
       normalized_status AS status, COUNT(*) AS count, MIN(event_time) AS end_time
FROM filtered_jobs WHERE event_time IS NOT NULL
GROUP BY bucket_start, normalized_status
UNION ALL
SELECT 'dataflow' AS record_type, date_trunc(?, timezone(?, event_time)) AS bucket_start,
       normalized_status AS status, COUNT(*) AS count, MIN(event_time) AS end_time
FROM filtered_dataflows WHERE event_time IS NOT NULL
GROUP BY bucket_start, normalized_status
ORDER BY bucket_start, record_type, status
"""

_RUNTIME_CONTEXT_SQL = f"""
SELECT COALESCE(NULLIF(engine_name, ''), 'unknown') AS engine_name,
       COALESCE(NULLIF(metadata_provider_name, ''), 'unknown') AS metadata_provider_name,
       COALESCE(NULLIF(platform_name, ''), 'unknown') AS platform_name,
       CONCAT(COALESCE(NULLIF(engine_name, ''), 'unknown'), ' / ', COALESCE(NULLIF(metadata_provider_name, ''), 'unknown')) AS name,
       COUNT(*) AS jobs,
       COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS succeeded,
       COUNT(*) FILTER (WHERE normalized_status = 'failed') AS failed,
       COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skipped,
       COUNT(*) FILTER (WHERE normalized_status = 'running') AS running,
       COUNT(*) FILTER (WHERE normalized_status = 'pending') AS pending,
       ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'succeeded') /
         NULLIF(COUNT(*) FILTER (WHERE normalized_status IN ('succeeded','failed')), 0), 2) AS success_rate,
       ROUND(AVG(duration_seconds) FILTER (WHERE normalized_status IN ('succeeded','failed')), 3) AS avg_duration_seconds,
       {nullable_discrete_percentile('duration_seconds', 0.95, "normalized_status IN ('succeeded','failed')")} AS p95_duration_seconds
FROM filtered_jobs
GROUP BY engine_name, metadata_provider_name, platform_name
ORDER BY failed DESC, jobs DESC, engine_name, metadata_provider_name
LIMIT 30
"""

_OPERATION_HEALTH_SQL = f"""
, job_operations AS (
  SELECT DISTINCT _source_id, log_session_id, job_id, normalized_status,
         {dimension_key_sql('value')} AS operation_type
  FROM filtered_jobs,
  UNNEST(regexp_split_to_array(
    regexp_replace(COALESCE(CAST(operation_types AS VARCHAR), ''), '[\\[\\]\"'']', '', 'g'),
    '\\s*[,;|]\\s*'
  )) values(value)
), health AS (
  SELECT 'job' AS record_type, operation_type, normalized_status FROM job_operations
  UNION ALL
  SELECT 'dataflow', COALESCE(NULLIF(operation_type, ''), 'unknown'), normalized_status
  FROM filtered_dataflows
)
SELECT record_type, operation_type, COUNT(*) AS count,
       COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS succeeded,
       COUNT(*) FILTER (WHERE normalized_status = 'failed') AS failed,
       COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skipped,
       COUNT(*) FILTER (WHERE normalized_status = 'running') AS running,
       COUNT(*) FILTER (WHERE normalized_status = 'pending') AS pending,
       COUNT(*) FILTER (WHERE normalized_status = 'failed') AS failed_count
FROM health
GROUP BY record_type, operation_type
ORDER BY record_type, count DESC, operation_type
"""


_WORKLOAD_SQL = f"""
SELECT date_trunc(?, timezone(?, event_time)) AS bucket_start,
       {_metric_total('source_rows_read', 'TRUE')} AS rows_read,
       {_metric_observed('source_rows_read', 'TRUE')} AS observed_rows_read,
       {_metric_measured('source_rows_read', 'TRUE')} AS rows_read_measured,
       {_metric_eligible('TRUE')} AS rows_read_eligible,
       {_metric_total('destination_rows_written', 'TRUE')} AS rows_written,
       {_metric_observed('destination_rows_written', 'TRUE')} AS observed_rows_written,
       {_metric_measured('destination_rows_written', 'TRUE')} AS rows_written_measured,
       {_metric_eligible('TRUE')} AS rows_written_eligible,
       {_metric_total('destination_rows_inserted', 'TRUE')} AS rows_inserted,
       {_metric_total('destination_rows_updated', 'TRUE')} AS rows_updated,
       {_metric_total('destination_rows_deleted', 'TRUE')} AS rows_deleted,
       {_metric_total('destination_bytes_added', 'TRUE')} AS bytes_added,
       {_metric_total('destination_bytes_removed', 'TRUE')} AS bytes_removed,
       {_metric_total('destination_bytes_saved', 'TRUE')} AS bytes_saved,
       {_metric_total('destination_files_added', 'TRUE')} AS files_added,
       {_metric_total('destination_files_removed', 'TRUE')} AS files_removed,
       {_metric_total('destination_rows_written', 'TRUE')} AS rows_output,
       COUNT(*) AS dataflow_runs,
       {_metric_difference('destination_bytes_added', 'TRUE', 'destination_bytes_removed', 'TRUE')} AS net_bytes
FROM filtered_dataflows WHERE event_time IS NOT NULL
GROUP BY bucket_start ORDER BY bucket_start
"""

_FAILURE_MESSAGE_SQL = failure_message_sql("filtered_dataflows", native=True)
_FAILURE_CATEGORY_SQL = f"""
SELECT {failure_category_sql(_FAILURE_MESSAGE_SQL)} AS category, COUNT(*) AS count
FROM filtered_dataflows
WHERE normalized_status = 'failed'
GROUP BY category
ORDER BY count DESC, category
"""

_TOP_FAILURES_SQL = """
SELECT COALESCE(NULLIF(dataflow_name, ''), NULLIF(dataflow_id, ''), 'unknown') AS dataflow_name,
       dataflow_id, COUNT(*) AS error_count
FROM filtered_dataflows
WHERE normalized_status = 'failed'
GROUP BY dataflow_name, dataflow_id
ORDER BY error_count DESC, dataflow_name
LIMIT 1
"""

_HEALTH_SQL = """
, child_counts AS (
  SELECT _source_id, log_session_id, job_id, COUNT(*) AS total_dataflows,
         COUNT(*) FILTER (WHERE normalized_status = 'failed') AS total_failed,
         COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS total_skipped,
         COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS total_succeeded
  FROM filtered_dataflows WHERE job_id IS NOT NULL GROUP BY _source_id, log_session_id, job_id
), mismatch AS (
  SELECT COUNT(*) AS mismatch_count
  FROM filtered_jobs j LEFT JOIN child_counts c ON c._source_id = j._source_id AND c.job_id = j.job_id AND c.log_session_id IS NOT DISTINCT FROM j.log_session_id
  WHERE (j.observed_at IS NULL OR j.observed_at >= (SELECT MAX(d.event_time) FROM filtered_dataflows d WHERE d._source_id=j._source_id AND d.job_id=j.job_id AND d.log_session_id IS NOT DISTINCT FROM j.log_session_id))
    AND j.log_records_dropped = 0
    AND ((j.total_dataflows IS NOT NULL AND j.total_dataflows <> COALESCE(c.total_dataflows, 0))
     OR (j.total_failed IS NOT NULL AND j.total_failed <> COALESCE(c.total_failed, 0))
     OR (j.total_skipped IS NOT NULL AND j.total_skipped <> COALESCE(c.total_skipped, 0))
     OR (j.total_succeeded IS NOT NULL AND j.total_succeeded <> COALESCE(c.total_succeeded, 0)))
), ids AS (
  SELECT
    (SELECT COUNT(DISTINCT (d._source_id, d.log_session_id, d.job_id)) FROM filtered_dataflows d
      WHERE d.job_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM filtered_jobs j WHERE j._source_id = d._source_id AND j.job_id = d.job_id AND j.log_session_id IS NOT DISTINCT FROM d.log_session_id
      )) AS orphan_dataflow_job_ids,
    (SELECT COUNT(DISTINCT (j._source_id, j.log_session_id, j.job_id)) FROM filtered_jobs j
      WHERE j.job_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM filtered_dataflows d WHERE d._source_id = j._source_id AND d.job_id = j.job_id AND d.log_session_id IS NOT DISTINCT FROM j.log_session_id
      )) AS jobs_without_dataflow_records
)
SELECT
  COUNT(*) FILTER (WHERE normalized_status = 'failed' AND event_time >= current_timestamp - INTERVAL 3 DAY) AS failed_dataflows_last_3_days,
  COUNT(*) FILTER (WHERE normalized_status = 'failed' AND event_time >= current_timestamp - INTERVAL 7 DAY) AS failed_dataflows_last_7_days,
  COUNT(*) FILTER (WHERE normalized_status = 'failed' AND event_time >= current_timestamp - INTERVAL 7 DAY
    AND (LOWER(COALESCE(operation_type, '')) = 'maintenance' OR LOWER(COALESCE(destination_operation_type, '')) IN ('compact','cleanup','maintenance'))) AS maintenance_failed_last_7_days,
  COUNT(*) FILTER (WHERE normalized_status = 'failed' AND event_time >= current_timestamp - INTERVAL 14 DAY
    AND (LOWER(COALESCE(operation_type, '')) = 'maintenance' OR LOWER(COALESCE(destination_operation_type, '')) IN ('compact','cleanup','maintenance'))) AS maintenance_failed_last_14_days,
  COUNT(*) FILTER (WHERE normalized_status = 'skipped' AND event_time >= current_timestamp - INTERVAL 7 DAY
    AND (LOWER(COALESCE(operation_type, '')) = 'maintenance' OR LOWER(COALESCE(destination_operation_type, '')) IN ('compact','cleanup','maintenance'))) AS maintenance_skipped_last_7_days,
  (SELECT COUNT(*) FROM filtered_jobs WHERE normalized_status = 'failed' AND event_time >= current_timestamp - INTERVAL 3 DAY) AS failed_jobs_last_3_days,
  (SELECT COUNT(*) FROM filtered_jobs WHERE normalized_status = 'failed' AND event_time >= current_timestamp - INTERVAL 7 DAY) AS failed_jobs_last_7_days,
  (SELECT mismatch_count FROM mismatch) AS mismatch_count,
  (SELECT orphan_dataflow_job_ids FROM ids) AS orphan_dataflow_job_ids,
  (SELECT jobs_without_dataflow_records FROM ids) AS jobs_without_dataflow_records
FROM filtered_dataflows
"""

_ATTENTION_BUNDLE_SQL = f"""
SELECT
  (SELECT performance_row FROM ({performance_attention_sql()}) performance_row)
    AS performance,
  (SELECT freshness_row FROM ({freshness_attention_sql()}) freshness_row)
    AS freshness,
  (SELECT maintenance_row FROM ({maintenance_attention_sql()}) maintenance_row)
    AS maintenance
"""
