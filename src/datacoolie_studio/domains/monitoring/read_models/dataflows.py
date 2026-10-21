from __future__ import annotations

from datacoolie_studio.domains.monitoring.dimensions import known_dimension_sql

from datetime import datetime
from typing import Any
from datacoolie_studio.domains.monitoring.dimensions import dimension_key_sql

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.read_models.common import (
    AnalyticsContext,
    nullable_discrete_percentile,
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
    "_source_id", "_ingested_at", "_record_line", "_file_uri", "raw_record_json",
    "log_schema_version", "attempts",
    "job_id", "dataflow_id", "dataflow_run_id", "dataflow_name",
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
  (SELECT COUNT(DISTINCT {known_dimension_sql('engine_name')}) FROM filtered_jobs) AS active_engines,
  (SELECT COUNT(DISTINCT {known_dimension_sql('metadata_provider_name')}) FROM filtered_jobs) AS active_metadata_providers,
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
  {_metric_total("destination_bytes_added", "TRUE")} AS total_bytes_written,
  {_metric_total("source_rows_read", "TRUE")} AS total_rows_read,
  {_metric_total("destination_rows_written", "TRUE")} AS total_rows_written,
  {_metric_observed("destination_bytes_added", "TRUE")} AS observed_bytes_written,
  {_metric_measured("destination_bytes_added", "TRUE")} AS bytes_written_measured,
  {_metric_eligible("TRUE")} AS bytes_written_eligible,
  {_metric_observed("source_rows_read", "TRUE")} AS observed_rows_read,
  {_metric_measured("source_rows_read", "TRUE")} AS rows_read_measured,
  {_metric_eligible("TRUE")} AS rows_read_eligible,
  {_metric_observed("destination_rows_written", "TRUE")} AS observed_rows_written,
  {_metric_measured("destination_rows_written", "TRUE")} AS rows_written_measured,
  {_metric_eligible("TRUE")} AS rows_written_eligible,
  {_metric_difference("destination_bytes_added", "TRUE", "destination_bytes_removed", "TRUE")} AS net_bytes_change,
  COUNT(duration_seconds) FILTER (WHERE normalized_status IN ('succeeded','failed')) AS duration_count,
  ROUND(AVG(duration_seconds) FILTER (WHERE normalized_status IN ('succeeded','failed')), 3) AS avg_duration_seconds,
  {nullable_discrete_percentile("duration_seconds", 0.25, "normalized_status IN ('succeeded','failed')")} AS q1_duration_seconds,
  {nullable_discrete_percentile("duration_seconds", 0.50, "normalized_status IN ('succeeded','failed')")} AS p50_duration_seconds,
  {nullable_discrete_percentile("duration_seconds", 0.75, "normalized_status IN ('succeeded','failed')")} AS q3_duration_seconds,
  {nullable_discrete_percentile("duration_seconds", 0.95, "normalized_status IN ('succeeded','failed')")} AS p95_duration_seconds,
  {nullable_discrete_percentile("duration_seconds", 0.99, "normalized_status IN ('succeeded','failed')")} AS p99_duration_seconds,
  ROUND(MAX(duration_seconds) FILTER (WHERE normalized_status IN ('succeeded','failed')), 3) AS max_duration_seconds,
  COUNT(DISTINCT {known_dimension_sql('engine_name')}) AS dataflow_active_engines
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
  {dimension_key_sql('source_name')} AS source_name,
  {dimension_key_sql('destination_name')} AS destination_name,
  COALESCE(MODE(source_format), 'unknown') AS source_format,
  MODE({dimension_key_sql('destination_format')}) AS destination_format,
  COALESCE(MODE(source_connection_type), 'unknown') AS source_connection_type,
  MODE({dimension_key_sql('destination_connection_type')}) AS destination_connection_type,
  COUNT(*) AS runs,
  COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS succeeded,
  COUNT(*) FILTER (WHERE normalized_status = 'failed') AS failed,
  COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skipped,
  COUNT(*) FILTER (WHERE normalized_status = 'running') AS running,
  COUNT(*) FILTER (WHERE normalized_status = 'pending') AS pending,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'succeeded') /
    NULLIF(COUNT(*) FILTER (WHERE normalized_status IN ('succeeded','failed')), 0), 2) AS success_rate,
  ROUND(AVG(duration_seconds), 3) AS avg_duration_seconds,
  {nullable_discrete_percentile("duration_seconds", 0.95)} AS p95_duration_seconds,
  {_metric_total("source_rows_read", "TRUE")} AS rows_read,
  {_metric_observed("source_rows_read", "TRUE")} AS observed_rows_read,
  {_metric_measured("source_rows_read", "TRUE")} AS rows_read_measured,
  {_metric_eligible("TRUE")} AS rows_read_eligible,
  {_metric_total("destination_rows_written", "TRUE")} AS rows_written,
  {_metric_observed("destination_rows_written", "TRUE")} AS observed_rows_written,
  {_metric_measured("destination_rows_written", "TRUE")} AS rows_written_measured,
  {_metric_eligible("TRUE")} AS rows_written_eligible,
  {_metric_total("destination_bytes_added", "TRUE")} AS bytes_added,
  {_metric_total("destination_bytes_removed", "TRUE")} AS bytes_removed
FROM filtered_dataflows
GROUP BY {dimension_key_sql('source_name')}, {dimension_key_sql('destination_name')}
ORDER BY failed DESC, p95_duration_seconds DESC, runs DESC, source_name, destination_name
LIMIT 18
"""

_NAME_STATUS_HEALTH_SQL = f"""
, latest AS (
  SELECT _source_id, log_session_id, dataflow_id, dataflow_run_id, raw_record_json,
         _file_uri, _file_kind, _record_line,
         dataflow_name, stage, source_name, destination_name, event_time
  FROM filtered_dataflows
  WHERE dataflow_id IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY _source_id, dataflow_id
    ORDER BY event_time DESC NULLS LAST, _ingested_at DESC NULLS LAST,
             _record_line DESC NULLS LAST, _file_uri DESC NULLS LAST,
             log_session_id DESC NULLS LAST, dataflow_run_id DESC NULLS LAST
  ) = 1
), aggregate AS (
  SELECT _source_id, dataflow_id,
    COALESCE(MODE(NULLIF(operation_type, '')), 'unknown') AS operation_type,
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
    ROUND(AVG(duration_seconds), 3) AS avg_duration_seconds,
    {nullable_discrete_percentile("duration_seconds", 0.95)} AS p95_duration_seconds,
    ROUND(MAX(duration_seconds), 3) AS max_duration_seconds,
    {_metric_total("source_rows_read", "TRUE")} AS rows_read,
    {_metric_observed("source_rows_read", "TRUE")} AS observed_rows_read,
    {_metric_measured("source_rows_read", "TRUE")} AS rows_read_measured,
    {_metric_eligible("TRUE")} AS rows_read_eligible,
    {_metric_total("destination_rows_written", "TRUE")} AS rows_written,
    {_metric_observed("destination_rows_written", "TRUE")} AS observed_rows_written,
    {_metric_measured("destination_rows_written", "TRUE")} AS rows_written_measured,
    {_metric_eligible("TRUE")} AS rows_written_eligible
  FROM filtered_dataflows
  WHERE dataflow_id IS NOT NULL
  GROUP BY _source_id, dataflow_id
)
SELECT a._source_id, l.log_session_id, a.dataflow_id,
  COALESCE(NULLIF(l.dataflow_name, ''), a.dataflow_id) AS dataflow_name,
  COALESCE(NULLIF(l.stage, ''), 'unknown') AS stage,
  a.operation_type,
  l.source_name, l.destination_name,
  a.runs, a.succeeded, a.failed, a.skipped, a.running, a.pending, a.unknown,
  a.success_rate, a.failure_rate, a.avg_duration_seconds,
  a.p95_duration_seconds, a.max_duration_seconds,
  a.rows_read, a.observed_rows_read, a.rows_read_measured, a.rows_read_eligible,
  a.rows_written, a.observed_rows_written, a.rows_written_measured, a.rows_written_eligible,
  l.event_time AS latest_time, l.dataflow_run_id AS latest_dataflow_run_id,
  l._source_id AS latest_source_id, l._file_uri AS latest_file_uri,
  l._file_kind AS latest_file_kind, l._record_line AS latest_record_line,
  NULL::VARCHAR AS latest_raw_record_json
FROM aggregate a
JOIN latest l USING (_source_id, dataflow_id)
ORDER BY a.runs DESC, a.failed DESC, (a.running + a.pending) DESC,
         a.p95_duration_seconds DESC, a._source_id ASC NULLS LAST,
         a.dataflow_id ASC, dataflow_name
LIMIT 40
"""
