from __future__ import annotations

from datetime import datetime
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.metrics.failure import (
    failure_all_messages_sql,
    failure_category_sql,
    failure_message_sql,
    failure_phase_sql,
    failure_rule_id_sql,
    failure_tags_sql,
    normalized_failure_message_sql,
)
from datacoolie_studio.domains.monitoring.read_models.common import (
    AnalyticsContext,
    filtered_ctes,
    one,
    reader_context,
    rows,
)
from datacoolie_studio.domains.monitoring.read_models.operation_windows import (
    empty_operation_windows,
    operation_windows,
)


_DATAFLOW_COLUMNS = (
    "_source_id", "job_id", "dataflow_id", "dataflow_run_id", "dataflow_name",
    "stage", "status", "start_time", "end_time", "operation_type",
    "source_name", "source_connection_type", "source_format", "source_status",
    "source_error_message", "transform_status", "transform_error_message",
    "destination_name", "destination_connection_type", "destination_format",
    "destination_status", "destination_error_message", "destination_full_table",
    "destination_table", "error_message",
)
_JOB_COLUMNS = (
    "_source_id", "job_id", "status", "start_time", "end_time", "error_message",
    "engine_name", "metadata_provider_name", "platform_name", "stages", "operation_types",
    "total_running", "total_pending", "total_skipped",
)
_FAILED_DATAFLOWS_TEMP_TABLE = "monitoring_failed_dataflows"


def failures_read_model(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    *,
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
        classification_ctes = _failure_ctes(ctes)
        conn.execute(
            f"CREATE OR REPLACE TEMP TABLE {_FAILED_DATAFLOWS_TEMP_TABLE} AS "
            f"{classification_ctes} SELECT * FROM failed_dataflows",
            params,
        )
        failure_ctes = _materialized_failure_ctes(ctes)
        summary = one(conn.execute(f"{failure_ctes} {_SUMMARY_SQL}", params))
        windows = operation_windows(
            conn,
            ctes,
            params,
            timezone_name=timezone_name,
            now=now,
        )
        failed_records = rows(conn.execute(f"{failure_ctes} {_FAILED_RECORDS_SQL}", params))
        repeated = rows(conn.execute(f"{failure_ctes} {_REPEATED_SQL}", params))
        endpoint_impact = rows(conn.execute(f"{failure_ctes} {_ENDPOINT_SQL}", params))
        category_phase = rows(conn.execute(f"{failure_ctes} {_CATEGORY_PHASE_SQL}", params))
        failed_by_stage = rows(conn.execute(f"{failure_ctes} {_STAGE_SQL}", params))
        source_types = rows(conn.execute(f"{failure_ctes} {_SOURCE_TYPE_SQL}", params))
        top_dataflows = rows(conn.execute(f"{failure_ctes} {_TOP_DATAFLOWS_SQL}", params))
        error_categories = rows(conn.execute(f"{failure_ctes} {_CATEGORY_SQL}", params))
        failure_by_phase = rows(conn.execute(f"{failure_ctes} {_PHASE_SQL}", params))
        trend = rows(conn.execute(f"{failure_ctes} {_TREND_SQL}", params))
    top_signature = repeated[0] if repeated else None
    total_failed = int(summary.get("failed_dataflows") or 0)
    repeated_runs = int(summary.get("repeated_failure_runs") or 0)
    top_cause_runs = int(top_signature.get("failed_runs") or 0) if top_signature else 0
    summary.update({
        "affected_job_contexts": int(summary.get("affected_job_shapes") or 0),
        "affected_stages": int(summary.get("affected_job_shapes") or 0),
        "total_failed_records": total_failed,
        "repeated_failure_share": _rate(repeated_runs, total_failed),
        "top_cause_runs": top_cause_runs,
        "top_cause_share": _rate(top_cause_runs, total_failed),
        "top_cause_category": top_signature.get("failure_category") if top_signature else None,
        "top_cause_phase": top_signature.get("failure_phase") if top_signature else None,
        "top_cause_signature": top_signature.get("failure_signature") if top_signature else None,
    })
    return {
        "generation": generation,
        "summary": summary,
        "windows": windows,
        "latest_queue": failed_records[:60],
        "failed_records": failed_records,
        "repeated_signatures": repeated,
        "endpoint_impact": endpoint_impact,
        "category_phase": category_phase,
        "failed_by_stage": failed_by_stage,
        "source_types": source_types,
        "top_dataflows": top_dataflows,
        "error_categories": error_categories,
        "failure_by_phase": failure_by_phase,
        "trend": trend,
    }


def _empty_read_model(generation: str) -> dict[str, Any]:
    return {
        "generation": generation,
        "summary": {},
        "windows": empty_operation_windows(),
        "latest_queue": [],
        "failed_records": [],
        "repeated_signatures": [],
        "endpoint_impact": [],
        "category_phase": [],
        "failed_by_stage": [],
        "source_types": [],
        "top_dataflows": [],
        "error_categories": [],
        "failure_by_phase": [],
        "trend": [],
    }


def _failure_ctes(ctes: str) -> str:
    message = failure_message_sql("d")
    phase = failure_phase_sql("m")
    category = failure_category_sql("p.failure_message")
    normalized = normalized_failure_message_sql("c.failure_message")
    return f"""{ctes}
    , failure_messages AS (
      SELECT d.*, {message} AS failure_message
      FROM filtered_dataflows d
      WHERE d.normalized_status = 'failed'
    ), failure_phases AS (
      SELECT m.*, {phase} AS failure_phase
      FROM failure_messages m
    ), failure_categories AS (
      SELECT p.*, {category} AS failure_category
      FROM failure_phases p
    ), failed_dataflows AS (
      SELECT c.*,
        c.failure_category || '|' || c.failure_phase || '|' ||
          COALESCE(NULLIF({normalized}, ''), 'unknown') AS failure_signature,
        COALESCE(NULLIF(c.destination_full_table, ''), NULLIF(c.destination_table, ''),
          NULLIF(c.destination_name, ''), NULLIF(c.source_name, ''),
          NULLIF(c.dataflow_name, ''), NULLIF(c.dataflow_id, ''), 'unknown') AS failure_target,
        c.event_time AS failure_time,
        'dataflow' AS failure_kind
      FROM failure_categories c
    ), failed_jobs AS (
      SELECT j.*, COALESCE(j.error_message, '') AS failure_message,
        {failure_category_sql("COALESCE(j.error_message, '')")} AS failure_category,
        'job' AS failure_phase, j.event_time AS failure_time, 'job' AS failure_kind
      FROM filtered_jobs j WHERE j.normalized_status = 'failed'
    )
    """


def _materialized_failure_ctes(ctes: str) -> str:
    return f"""{ctes}
    , failed_dataflows AS (
      SELECT * FROM {_FAILED_DATAFLOWS_TEMP_TABLE}
    ), failed_jobs AS (
      SELECT j.*, COALESCE(j.error_message, '') AS failure_message,
        {failure_category_sql("COALESCE(j.error_message, '')")} AS failure_category,
        'job' AS failure_phase, j.event_time AS failure_time, 'job' AS failure_kind
      FROM filtered_jobs j WHERE j.normalized_status = 'failed'
    )
    """


def _rate(part: int | float, whole: int | float) -> float:
    return round((part / whole) * 100, 2) if whole else 0


_SUMMARY_SQL = """
, signature_counts AS (
  SELECT failure_signature, COUNT(*) AS failed_runs FROM failed_dataflows GROUP BY failure_signature
)
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
  (SELECT COUNT(DISTINCT NULLIF(engine_name, '')) FROM filtered_jobs) AS active_engines,
  (SELECT COUNT(DISTINCT NULLIF(metadata_provider_name, '')) FROM filtered_jobs) AS active_metadata_providers,
  (SELECT COUNT(*) FROM failed_jobs) AS failed_jobs,
  (SELECT COUNT(*) FROM failed_dataflows) AS failed_dataflows,
  (SELECT COUNT(DISTINCT NULLIF(job_id, '')) FROM failed_jobs) AS affected_jobs,
  (SELECT COUNT(DISTINCT NULLIF(job_id, '')) FROM failed_dataflows) AS affected_dataflow_jobs,
  (SELECT COUNT(DISTINCT COALESCE(operation_types, '') || '|' || COALESCE(stages, '')) FROM failed_jobs) AS affected_job_shapes,
  (SELECT COUNT(DISTINCT NULLIF(dataflow_id, '')) FROM failed_dataflows) AS affected_dataflows,
  (SELECT COUNT(DISTINCT COALESCE(source_name, 'unknown') || '|' || COALESCE(destination_name, 'unknown')) FROM failed_dataflows) AS affected_routes,
  (SELECT COUNT(*) FROM signature_counts WHERE failed_runs >= 2) AS repeated_signatures,
  (SELECT COUNT(*) FROM signature_counts) AS unique_signatures,
  (SELECT COALESCE(SUM(failed_runs) FILTER (WHERE failed_runs >= 2), 0) FROM signature_counts) AS repeated_failure_runs,
  (SELECT MAX(failure_time) FROM failed_dataflows) AS latest_failure_at,
  (SELECT ARG_MAX(COALESCE(NULLIF(dataflow_name, ''), job_id), failure_time) FROM failed_dataflows) AS latest_failure_name,
  (SELECT COUNT(*) FROM filtered_jobs WHERE normalized_status = 'succeeded') AS succeeded_jobs,
  (SELECT COUNT(*) FROM filtered_jobs WHERE normalized_status = 'failed') AS failed_job_runs,
  (SELECT COUNT(*) FROM filtered_dataflows WHERE normalized_status = 'succeeded') AS succeeded_dataflows
"""

_FAILED_RECORDS_SQL = f"""
, bounded_failed_records AS (
  SELECT * FROM failed_dataflows
  ORDER BY failure_time DESC NULLS LAST, dataflow_run_id
  LIMIT 100
)
SELECT b.*,
       {failure_rule_id_sql("b.failure_message")} AS failure_rule_id,
       {failure_tags_sql(failure_all_messages_sql("b"), "b.failure_category")} AS failure_tags
FROM bounded_failed_records b
ORDER BY failure_time DESC NULLS LAST, dataflow_run_id
"""

_REPEATED_SQL = """
SELECT failure_signature,
       ARG_MAX(failure_category, failure_time) AS failure_category,
       ARG_MAX(failure_phase, failure_time) AS failure_phase,
       COUNT(*) AS failed_runs,
       COUNT(DISTINCT NULLIF(job_id, '')) AS affected_jobs,
       COUNT(DISTINCT COALESCE(NULLIF(dataflow_id, ''), NULLIF(dataflow_name, ''))) AS affected_dataflows,
       MAX(failure_time) AS latest_time,
       ARG_MAX(failure_message, failure_time) AS latest_error,
       ARG_MAX(dataflow_name, failure_time) AS sample_dataflow,
       ARG_MAX(job_id, failure_time) AS sample_job_id,
       ARG_MAX(failure_target, failure_time) AS failure_target
FROM failed_dataflows
GROUP BY failure_signature
ORDER BY failed_runs DESC, affected_jobs DESC, latest_time DESC
LIMIT 30
"""

_ENDPOINT_SQL = """
SELECT COALESCE(NULLIF(source_name, ''), 'unknown') AS source_name,
       COALESCE(NULLIF(destination_name, ''), 'unknown') AS destination_name,
       COALESCE(MODE(NULLIF(source_format, '')), 'unknown') AS source_format,
       COALESCE(MODE(NULLIF(destination_format, '')), 'unknown') AS destination_format,
       COALESCE(MODE(NULLIF(source_connection_type, '')), 'unknown') AS source_connection_type,
       COALESCE(MODE(NULLIF(destination_connection_type, '')), 'unknown') AS destination_connection_type,
       COUNT(*) AS failed_runs, COUNT(DISTINCT NULLIF(job_id, '')) AS affected_jobs,
       COALESCE(MODE(failure_category), 'Unspecified') AS failure_category,
       COALESCE(MODE(failure_phase), 'unknown') AS failure_phase,
       MAX(failure_time) AS latest_time, ARG_MAX(failure_message, failure_time) AS latest_error
FROM failed_dataflows
GROUP BY source_name, destination_name
ORDER BY failed_runs DESC, affected_jobs DESC, latest_time DESC
LIMIT 30
"""

_CATEGORY_PHASE_SQL = """
SELECT failure_category AS category,
       COUNT(*) FILTER (WHERE failure_phase = 'source') AS source,
       COUNT(*) FILTER (WHERE failure_phase = 'transform') AS transform,
       COUNT(*) FILTER (WHERE failure_phase = 'destination') AS destination,
       COUNT(*) FILTER (WHERE failure_phase = 'overhead') AS overhead,
       COUNT(*) FILTER (WHERE failure_phase NOT IN ('source','transform','destination','overhead')) AS unknown,
       COUNT(*) AS total
FROM failed_dataflows
GROUP BY failure_category
ORDER BY total DESC, category
"""

_STAGE_SQL = """
SELECT COALESCE(NULLIF(stage, ''), 'unknown') AS name,
       COUNT(*) FILTER (WHERE failure_phase = 'source') AS source,
       COUNT(*) FILTER (WHERE failure_phase = 'transform') AS transform,
       COUNT(*) FILTER (WHERE failure_phase = 'destination') AS destination,
       COUNT(*) FILTER (WHERE failure_phase = 'overhead') AS overhead,
       COUNT(*) FILTER (WHERE failure_phase NOT IN ('source','transform','destination','overhead')) AS unknown,
       COUNT(*) AS count
FROM failed_dataflows GROUP BY name ORDER BY count DESC, name LIMIT 30
"""

_SOURCE_TYPE_SQL = """
SELECT COALESCE(NULLIF(source_connection_type, ''), 'unknown') AS name, COUNT(*) AS count
FROM failed_dataflows GROUP BY name ORDER BY count DESC, name LIMIT 20
"""

_TOP_DATAFLOWS_SQL = """
SELECT dataflow_id, COALESCE(ARG_MAX(NULLIF(dataflow_name, ''), failure_time), dataflow_id) AS dataflow_name,
       COUNT(*) AS error_count,
       COUNT(*) FILTER (WHERE failure_phase = 'source') AS source,
       COUNT(*) FILTER (WHERE failure_phase = 'transform') AS transform,
       COUNT(*) FILTER (WHERE failure_phase = 'destination') AS destination,
       COUNT(*) FILTER (WHERE failure_phase = 'overhead') AS overhead,
       COUNT(*) FILTER (WHERE failure_phase NOT IN ('source','transform','destination','overhead')) AS unknown,
       COUNT(DISTINCT NULLIF(job_id, '')) AS affected_job_count,
       ARG_MAX(failure_message, failure_time) AS last_error, MAX(failure_time) AS last_time,
       ARG_MAX(stage, failure_time) AS stage, ARG_MAX(engine_name, failure_time) AS engine_name
FROM failed_dataflows
WHERE dataflow_id IS NOT NULL AND TRIM(dataflow_id) <> ''
GROUP BY dataflow_id
ORDER BY error_count DESC, affected_job_count DESC, last_time DESC, dataflow_name
LIMIT 30
"""

_CATEGORY_SQL = """
SELECT failure_category AS category, COUNT(*) AS count
FROM failed_dataflows GROUP BY failure_category ORDER BY count DESC, category
"""

_PHASE_SQL = """
SELECT failure_phase AS name, COUNT(*) AS count
FROM failed_dataflows GROUP BY failure_phase ORDER BY count DESC, name LIMIT 12
"""

_TREND_SQL = """
SELECT run_date AS date,
       SUM(failed_jobs) + SUM(failed_dataflows) AS failed,
       SUM(failed_jobs) AS failed_jobs,
       SUM(failed_dataflows) AS failed_dataflows
FROM (
  SELECT run_date, COUNT(*) AS failed_dataflows, 0 AS failed_jobs FROM failed_dataflows GROUP BY run_date
  UNION ALL
  SELECT run_date, 0, COUNT(*) FROM failed_jobs GROUP BY run_date
) counts
GROUP BY run_date
ORDER BY run_date
"""
