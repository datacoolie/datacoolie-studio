from __future__ import annotations

from datacoolie_studio.domains.monitoring.dimensions import known_dimension_sql

from datetime import datetime
from typing import Any
from datacoolie_studio.domains.monitoring.dimensions import dimension_key_sql

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

_LATEST_FAILURE_ACTIVITY_LIMIT = 200
_FAILED_RECORDS_RESPONSE_LIMIT = 100
_RECOVERED_ATTEMPTS_RESPONSE_LIMIT = 60


_DATAFLOW_COLUMNS = (
    "_source_id", "raw_record_json", "log_schema_version", "attempts", "job_id", "dataflow_id", "dataflow_run_id", "dataflow_name",
    "stage", "status", "message", "start_time", "end_time", "operation_type",
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
        recovered_ctes = f"""{ctes}, recovered_attempts AS (
          SELECT d._source_id, d.log_session_id, d.job_id, d.dataflow_id, d.dataflow_run_id,
                 d.dataflow_name, d.status, d.event_time,
                 COALESCE(
                   TRY_CAST(u.attempt.end_time AS TIMESTAMPTZ),
                   TRY_CAST(u.attempt.start_time AS TIMESTAMPTZ),
                   d.event_time
                 ) AS activity_time,
                 CASE
                   WHEN TRY_CAST(u.attempt.end_time AS TIMESTAMPTZ) IS NOT NULL THEN 'attempt_end_time'
                   WHEN TRY_CAST(u.attempt.start_time AS TIMESTAMPTZ) IS NOT NULL THEN 'attempt_start_time'
                   ELSE 'dataflow_run_time'
                 END AS activity_time_source,
                 d._file_uri, d._file_kind, d._record_line,
                 u.attempt.attempt_number AS attempt_number,
                 u.attempt.message AS attempt_message,
                 NULL::VARCHAR AS attempt_record_json
          FROM filtered_dataflows d
          CROSS JOIN UNNEST(d.attempts) AS u(attempt)
          WHERE d.normalized_status = 'succeeded'
            AND LOWER(CAST(u.attempt.status AS VARCHAR)) = 'failed'
        )"""
        recovery_counts = one(conn.execute(f"{recovered_ctes} SELECT COUNT(*) AS recovered_attempt_count, COUNT(DISTINCT (_source_id, log_session_id, dataflow_run_id)) AS recovered_run_count FROM recovered_attempts", params))
        recovered_activity_records = rows(conn.execute(f"{recovered_ctes} SELECT * FROM recovered_attempts ORDER BY activity_time DESC NULLS LAST, _source_id, dataflow_run_id, attempt_number DESC NULLS LAST LIMIT {_LATEST_FAILURE_ACTIVITY_LIMIT}", params))
        recovered_records = recovered_activity_records[:_RECOVERED_ATTEMPTS_RESPONSE_LIMIT]
        failed_activity_records = rows(conn.execute(f"{failure_ctes} {_FAILED_RECORDS_SQL}", params))
        failed_records = failed_activity_records[:_FAILED_RECORDS_RESPONSE_LIMIT]
        repeated = rows(conn.execute(f"{failure_ctes} {_REPEATED_SQL}", params))
        endpoint_impact = rows(conn.execute(f"{failure_ctes} {_ENDPOINT_SQL}", params))
        category_phase = rows(conn.execute(f"{failure_ctes} {_CATEGORY_PHASE_SQL}", params))
        failed_by_stage = rows(conn.execute(f"{failure_ctes} {_STAGE_SQL}", params))
        source_types = rows(conn.execute(f"{failure_ctes} {_SOURCE_TYPE_SQL}", params))
        top_dataflows = rows(conn.execute(f"{failure_ctes} {_TOP_DATAFLOWS_SQL}", params))
        error_categories = rows(conn.execute(f"{failure_ctes} {_CATEGORY_SQL}", params))
        failure_by_phase = rows(conn.execute(f"{failure_ctes} {_PHASE_SQL}", params))
        trend = rows(conn.execute(f"{failure_ctes} {_TREND_SQL}", params))
        latest_queue = _latest_failure_activity(failed_activity_records, recovered_activity_records)
    top_signature = repeated[0] if repeated else None
    total_failed = int(summary.get("failed_dataflows") or 0)
    repeated_runs = int(summary.get("repeated_failure_runs") or 0)
    top_cause_runs = int(top_signature.get("failed_runs") or 0) if top_signature else 0
    summary.update({
        **recovery_counts,
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
        "latest_queue": latest_queue,
        "failed_records": failed_records,
        "recovered_attempts": recovered_records,
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
        "recovered_attempts": [],
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


def _latest_failure_activity(
    failed_records: list[dict[str, Any]],
    recovered_records: list[dict[str, Any]],
    *,
    limit: int = _LATEST_FAILURE_ACTIVITY_LIMIT,
) -> list[dict[str, Any]]:
    activities: list[dict[str, Any]] = []
    for record in failed_records[:limit]:
        activities.append({
            **record,
            "activity_type": "failed_run",
            "activity_time": record.get("failure_time") or record.get("event_time"),
            "activity_time_source": "dataflow_run_time",
            "activity_context": record.get("failure_phase") or "unknown",
            "activity_message": record.get("failure_message"),
        })
    for record in recovered_records[:limit]:
        attempt_number = record.get("attempt_number")
        activities.append({
            **record,
            "activity_type": "recovered_attempt",
            "activity_time": record.get("activity_time") or record.get("event_time"),
            "activity_time_source": record.get("activity_time_source") or "dataflow_run_time",
            "activity_context": f"Attempt {attempt_number if attempt_number is not None else 'unk'}",
            "activity_message": record.get("attempt_message"),
        })
    activities.sort(
        key=lambda record: (
            record.get("activity_time") is not None,
            str(record.get("activity_time") or ""),
            str(record.get("_source_id") or ""),
            str(record.get("dataflow_run_id") or ""),
            str(record.get("activity_type") or ""),
        ),
        reverse=True,
    )
    return activities[:limit]


def _failure_ctes(ctes: str) -> str:
    message = failure_message_sql("d", native=True)
    phase = failure_phase_sql("m", native=True)
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


_SUMMARY_SQL = f"""
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
  (SELECT COUNT(DISTINCT {known_dimension_sql('engine_name')}) FROM filtered_jobs) AS active_engines,
  (SELECT COUNT(DISTINCT {known_dimension_sql('metadata_provider_name')}) FROM filtered_jobs) AS active_metadata_providers,
  (SELECT COUNT(*) FROM failed_jobs) AS failed_jobs,
  (SELECT COUNT(*) FROM failed_dataflows) AS failed_dataflows,
  (SELECT COUNT(DISTINCT (_source_id, log_session_id, job_id)) FILTER (WHERE job_id IS NOT NULL) FROM failed_jobs) AS affected_jobs,
  (SELECT COUNT(DISTINCT (_source_id, log_session_id, job_id)) FILTER (WHERE job_id IS NOT NULL) FROM failed_dataflows) AS affected_dataflow_jobs,
  (SELECT COUNT(DISTINCT COALESCE(operation_types, '') || '|' || COALESCE(stages, '')) FROM failed_jobs) AS affected_job_shapes,
  (SELECT COUNT(DISTINCT (_source_id, log_session_id, dataflow_id)) FILTER (WHERE dataflow_id IS NOT NULL) FROM failed_dataflows) AS affected_dataflows,
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
  ORDER BY failure_time DESC NULLS LAST, _source_id, dataflow_run_id
  LIMIT {_LATEST_FAILURE_ACTIVITY_LIMIT}
)
SELECT b.*,
       {failure_rule_id_sql("b.failure_message")} AS failure_rule_id,
       {failure_tags_sql(failure_all_messages_sql("b", native=True), "b.failure_category")} AS failure_tags
FROM bounded_failed_records b
ORDER BY failure_time DESC NULLS LAST, _source_id, dataflow_run_id
"""

_REPEATED_SQL = """
SELECT failure_signature,
       ARG_MAX(failure_category, failure_time) AS failure_category,
       ARG_MAX(failure_phase, failure_time) AS failure_phase,
       COUNT(*) AS failed_runs,
       COUNT(DISTINCT (_source_id, log_session_id, job_id)) FILTER (WHERE job_id IS NOT NULL) AS affected_jobs,
       COUNT(DISTINCT (_source_id, log_session_id, dataflow_id)) AS affected_dataflows,
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

_ENDPOINT_SQL = f"""
SELECT {dimension_key_sql('source_name')} AS source_name,
       {dimension_key_sql('destination_name')} AS destination_name,
       COALESCE(MODE(NULLIF(source_format, '')), 'unknown') AS source_format,
       MODE({dimension_key_sql('destination_format')}) AS destination_format,
       COALESCE(MODE(NULLIF(source_connection_type, '')), 'unknown') AS source_connection_type,
       MODE({dimension_key_sql('destination_connection_type')}) AS destination_connection_type,
       COUNT(*) AS failed_runs, COUNT(DISTINCT (_source_id, log_session_id, job_id)) FILTER (WHERE job_id IS NOT NULL) AS affected_jobs,
       COALESCE(MODE(failure_category), 'Unspecified') AS failure_category,
       COALESCE(MODE(failure_phase), 'unknown') AS failure_phase,
       MAX(failure_time) AS latest_time, ARG_MAX(failure_message, failure_time) AS latest_error
FROM failed_dataflows
GROUP BY {dimension_key_sql('source_name')}, {dimension_key_sql('destination_name')}
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
SELECT _source_id, log_session_id, dataflow_id, COALESCE(ARG_MAX(NULLIF(dataflow_name, ''), failure_time), dataflow_id) AS dataflow_name,
       COUNT(*) AS error_count,
       COUNT(*) FILTER (WHERE failure_phase = 'source') AS source,
       COUNT(*) FILTER (WHERE failure_phase = 'transform') AS transform,
       COUNT(*) FILTER (WHERE failure_phase = 'destination') AS destination,
       COUNT(*) FILTER (WHERE failure_phase = 'overhead') AS overhead,
       COUNT(*) FILTER (WHERE failure_phase NOT IN ('source','transform','destination','overhead')) AS unknown,
       COUNT(DISTINCT (_source_id, log_session_id, job_id)) FILTER (WHERE job_id IS NOT NULL) AS affected_job_count,
       ARG_MAX(failure_message, failure_time) AS last_error, MAX(failure_time) AS last_time,
       ARG_MAX(stage, failure_time) AS stage, ARG_MAX(engine_name, failure_time) AS engine_name
FROM failed_dataflows
WHERE dataflow_id IS NOT NULL
GROUP BY _source_id, log_session_id, dataflow_id
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
