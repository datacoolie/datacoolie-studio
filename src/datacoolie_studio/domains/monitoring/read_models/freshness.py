from __future__ import annotations

from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.read_models.common import (
    AnalyticsContext,
    discrete_percentile,
    filtered_ctes,
    one,
    paged_rows,
    reader_context,
    standalone_derived_query,
)


_DATAFLOW_COLUMNS = (
    "_source_id", "job_id", "dataflow_id", "dataflow_run_id", "workspace_id",
    "dataflow_name", "dataflow_description", "stage", "group_number",
    "execution_order", "processing_mode", "is_active", "configure",
    "status", "error_message", "start_time", "end_time", "operation_type",
    "source_name", "source_id", "source_format", "source_connection_type",
    "source_catalog", "source_database", "source_schema", "source_table",
    "source_full_table", "source_path", "source_query", "source_python_function",
    "source_watermark_before", "source_watermark_after", "source_watermark_effective",
    "source_watermark_columns", "source_filter_expression", "source_configure",
    "source_action", "transform_deduplicate_columns", "transform_latest_data_columns",
    "transform_filter_expression", "transform_additional_columns",
    "transform_schema_hints", "transform_configure", "destination_name",
    "destination_id", "destination_format", "destination_connection_type",
    "destination_catalog", "destination_database", "destination_schema",
    "destination_table", "destination_full_table", "destination_path",
    "destination_load_type", "destination_merge_keys", "destination_partition_columns",
    "destination_configure",
)
_JOB_COLUMNS = ("_source_id", "job_id")


def freshness_read_model(
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
            source_ids, filters,
            dataflow_columns=_DATAFLOW_COLUMNS,
            job_columns=_JOB_COLUMNS,
        )
        freshness_ctes = f"{ctes} {_DERIVED_CTES}"
        bundle = one(conn.execute(
            f"{freshness_ctes} {_BUNDLE_SQL}",
            [*params, grain, timezone_name],
        ))
    return {
        "generation": generation, "summary": bundle.get("summary") or {},
        "age_by_dataflow": bundle.get("age_by_dataflow") or [],
        "watermark_movement_by_date": [
            _trend_row(row, grain) for row in bundle.get("movement_trend") or []
        ],
        "age_distribution": bundle.get("age_distribution") or [],
        "watermark_coverage_by_stage": bundle.get("stage_coverage") or [],
        "skipped_streak_distribution": bundle.get("streak_distribution") or [],
    }


def freshness_evidence_read_model(
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
        "dataflow_name": "dataflow_name", "stage": "stage",
        "source_name": "source_name", "destination_name": "destination_name",
        "destination_load_type": "destination_load_type",
        "latest_freshness_at": "latest_freshness_at", "age_days": "age_days",
        "latest_run_status": "latest_status", "movement_state": "movement_state",
        "latest_success_watermark": "latest_success_watermark",
    }
    order_column = sort_columns.get(sort_by, "latest_freshness_at")
    direction = "ASC" if sort_dir == "asc" else "DESC"
    with reader_context(paths, analytics_context) as (conn, source_ids, generation):
        if conn is None or not source_ids:
            return {"generation": generation, "records": [], "total_records": 0}
        ctes, params = filtered_ctes(
            source_ids, filters,
            dataflow_columns=_DATAFLOW_COLUMNS,
            job_columns=_JOB_COLUMNS,
        )
        freshness_ctes = f"{ctes} {_DERIVED_CTES}"
        records, total = paged_rows(conn.execute(
            f"SELECT evidence.*, COUNT(*) OVER () AS __total_records FROM ("
            f"{freshness_ctes} {_REGISTRY_SQL}) evidence "
            f"ORDER BY {order_column} {direction} NULLS LAST, dataflow_id ASC "
            "LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ))
    return {"generation": generation, "records": records, "total_records": total}


def freshness_attention_sql() -> str:
    return standalone_derived_query(_ATTENTION_DERIVED_CTES, _ATTENTION_SQL)


def _empty(generation: str) -> dict[str, Any]:
    return {
        "generation": generation, "summary": {}, "age_by_dataflow": [],
        "watermark_movement_by_date": [], "age_distribution": [],
        "watermark_coverage_by_stage": [], "skipped_streak_distribution": [],
    }


def _trend_row(row: dict[str, Any], grain: str) -> dict[str, Any]:
    return {
        "date": row.get("bucket_start"), "bucket": row.get("bucket_start"),
        "bucket_start": row.get("bucket_start"), "bucket_end": None, "grain": grain,
        **{key: value for key, value in row.items() if key != "bucket_start"},
    }


_HAS_WATERMARK = """(
  NULLIF(TRIM(CAST(source_watermark_before AS VARCHAR)), '') IS NOT NULL OR
  NULLIF(TRIM(CAST(source_watermark_after AS VARCHAR)), '') IS NOT NULL OR
  NULLIF(TRIM(CAST(source_watermark_effective AS VARCHAR)), '') IS NOT NULL OR
  NULLIF(TRIM(CAST(source_watermark_columns AS VARCHAR)), '') IS NOT NULL
)"""
_INVALID_WATERMARK = """(
  (regexp_matches(TRIM(COALESCE(CAST(source_watermark_before AS VARCHAR), '')), '^[\\[{]') AND NOT json_valid(CAST(source_watermark_before AS VARCHAR))) OR
  (regexp_matches(TRIM(COALESCE(CAST(source_watermark_after AS VARCHAR), '')), '^[\\[{]') AND NOT json_valid(CAST(source_watermark_after AS VARCHAR))) OR
  (regexp_matches(TRIM(COALESCE(CAST(source_watermark_effective AS VARCHAR), '')), '^[\\[{]') AND NOT json_valid(CAST(source_watermark_effective AS VARCHAR)))
)"""

_DERIVED_CTES = f"""
, etl_rows AS (
  SELECT *, {_HAS_WATERMARK} AS watermark_enabled,
         CASE
           WHEN NOT {_HAS_WATERMARK} THEN 'not_configured'
           WHEN {_INVALID_WATERMARK} THEN 'invalid'
           WHEN normalized_status IN ('running', 'pending') THEN 'incomplete'
           WHEN normalized_status IN ('failed', 'skipped') THEN 'unchanged'
           WHEN NULLIF(TRIM(CAST(source_watermark_before AS VARCHAR)), '') IS NOT NULL
            AND NULLIF(TRIM(CAST(source_watermark_after AS VARCHAR)), '') IS NOT NULL
             THEN CASE WHEN TRIM(CAST(source_watermark_before AS VARCHAR)) <> TRIM(CAST(source_watermark_after AS VARCHAR)) THEN 'advanced' ELSE 'unchanged' END
           WHEN NULLIF(TRIM(CAST(source_watermark_before AS VARCHAR)), '') IS NULL
            AND NULLIF(TRIM(CAST(source_watermark_after AS VARCHAR)), '') IS NOT NULL THEN 'initialized'
           ELSE 'incomplete'
         END AS movement_state,
         CASE WHEN NULLIF(TRIM(CAST(source_watermark_before AS VARCHAR)), '') IS NOT NULL
                    AND NULLIF(TRIM(CAST(source_watermark_effective AS VARCHAR)), '') IS NOT NULL
                    AND TRIM(CAST(source_watermark_before AS VARCHAR)) <> TRIM(CAST(source_watermark_effective AS VARCHAR))
              THEN 'adjusted' ELSE 'not_adjusted' END AS adjustment_state
  FROM filtered_dataflows
  WHERE LOWER(COALESCE(operation_type, 'unknown')) = 'etl'
), ranked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY dataflow_id ORDER BY event_time DESC NULLS LAST) AS latest_rank,
         ROW_NUMBER() OVER (PARTITION BY dataflow_id ORDER BY event_time DESC NULLS LAST, dataflow_run_id DESC) AS run_rank,
         SUM(CASE WHEN normalized_status = 'skipped' THEN 0 ELSE 1 END) OVER (
           PARTITION BY dataflow_id
           ORDER BY event_time DESC NULLS LAST, dataflow_run_id DESC
           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
         ) AS non_skipped_seen
  FROM etl_rows
  WHERE dataflow_id IS NOT NULL AND TRIM(dataflow_id) NOT IN ('', 'unknown', 'none', 'null', 'nan')
), per_dataflow AS (
  SELECT dataflow_id,
         ARG_MAX(NULLIF(workspace_id, ''), event_time) AS workspace_id,
         ARG_MAX(NULLIF(dataflow_name, ''), event_time) AS dataflow_name,
         ARG_MAX(NULLIF(dataflow_description, ''), event_time) AS dataflow_description,
         ARG_MAX(NULLIF(stage, ''), event_time) AS stage,
         ARG_MAX(group_number, event_time) AS group_number,
         ARG_MAX(execution_order, event_time) AS execution_order,
         ARG_MAX(NULLIF(processing_mode, ''), event_time) AS processing_mode,
         ARG_MAX(is_active, event_time) AS is_active,
         ARG_MAX(NULLIF(configure, ''), event_time) AS configure,
         ARG_MAX(NULLIF(operation_type, ''), event_time) AS operation_type,
         ARG_MAX(NULLIF(source_name, ''), event_time) AS source_name,
         ARG_MAX(NULLIF(destination_name, ''), event_time) AS destination_name,
         ARG_MAX(NULLIF(source_format, ''), event_time) AS source_format,
         ARG_MAX(NULLIF(destination_format, ''), event_time) AS destination_format,
         ARG_MAX(NULLIF(source_connection_type, ''), event_time) AS source_connection_type,
         ARG_MAX(NULLIF(destination_connection_type, ''), event_time) AS destination_connection_type,
         ARG_MAX(NULLIF(source_id, ''), event_time) AS source_id,
         ARG_MAX(NULLIF(source_catalog, ''), event_time) AS source_catalog,
         ARG_MAX(NULLIF(source_database, ''), event_time) AS source_database,
         ARG_MAX(NULLIF(source_schema, ''), event_time) AS source_schema,
         ARG_MAX(NULLIF(source_table, ''), event_time) AS source_table,
         ARG_MAX(NULLIF(source_full_table, ''), event_time) AS source_full_table,
         ARG_MAX(NULLIF(source_path, ''), event_time) AS source_path,
         ARG_MAX(NULLIF(source_query, ''), event_time) AS source_query,
         ARG_MAX(NULLIF(source_python_function, ''), event_time) AS source_python_function,
         ARG_MAX(NULLIF(source_watermark_columns, ''), event_time) AS source_watermark_columns,
         ARG_MAX(NULLIF(source_filter_expression, ''), event_time) AS source_filter_expression,
         ARG_MAX(NULLIF(source_configure, ''), event_time) AS source_configure,
         ARG_MAX(NULLIF(source_action, ''), event_time) AS source_action,
         ARG_MAX(NULLIF(transform_deduplicate_columns, ''), event_time) AS transform_deduplicate_columns,
         ARG_MAX(NULLIF(transform_latest_data_columns, ''), event_time) AS transform_latest_data_columns,
         ARG_MAX(NULLIF(transform_filter_expression, ''), event_time) AS transform_filter_expression,
         ARG_MAX(NULLIF(transform_additional_columns, ''), event_time) AS transform_additional_columns,
         ARG_MAX(NULLIF(transform_schema_hints, ''), event_time) AS transform_schema_hints,
         ARG_MAX(NULLIF(transform_configure, ''), event_time) AS transform_configure,
         ARG_MAX(NULLIF(destination_id, ''), event_time) AS destination_id,
         ARG_MAX(NULLIF(destination_catalog, ''), event_time) AS destination_catalog,
         ARG_MAX(NULLIF(destination_database, ''), event_time) AS destination_database,
         ARG_MAX(NULLIF(destination_schema, ''), event_time) AS destination_schema,
         ARG_MAX(NULLIF(destination_table, ''), event_time) AS destination_table,
         ARG_MAX(NULLIF(destination_full_table, ''), event_time) AS destination_full_table,
         ARG_MAX(NULLIF(destination_path, ''), event_time) AS destination_path,
         ARG_MAX(NULLIF(destination_load_type, ''), event_time) AS destination_load_type,
         ARG_MAX(NULLIF(destination_merge_keys, ''), event_time) AS destination_merge_keys,
         ARG_MAX(NULLIF(destination_partition_columns, ''), event_time) AS destination_partition_columns,
         ARG_MAX(NULLIF(destination_configure, ''), event_time) AS destination_configure,
         ARG_MAX(normalized_status, event_time) AS latest_status,
         MAX(event_time) AS latest_run_at,
         FIRST(error_message ORDER BY event_time DESC NULLS LAST, dataflow_run_id DESC) AS latest_error_message,
         ARG_MAX(movement_state, event_time) AS latest_movement_state,
         ARG_MAX(adjustment_state, event_time) AS latest_adjustment_state,
         MAX(event_time) FILTER (WHERE watermark_enabled) AS watermark_time,
         ARG_MAX(CAST(source_watermark_before AS VARCHAR), event_time) FILTER (WHERE watermark_enabled) AS source_watermark_before,
         ARG_MAX(CAST(source_watermark_after AS VARCHAR), event_time) FILTER (WHERE watermark_enabled) AS source_watermark_after,
         ARG_MAX(CAST(source_watermark_effective AS VARCHAR), event_time) FILTER (WHERE watermark_enabled) AS source_watermark_effective,
         MAX(event_time) FILTER (WHERE normalized_status IN ('succeeded','skipped')) AS latest_freshness_at,
         ARG_MAX(normalized_status, event_time) FILTER (WHERE normalized_status IN ('succeeded','skipped')) AS latest_freshness_status,
         ARG_MAX(
           COALESCE(
             NULLIF(TRIM(CAST(source_watermark_after AS VARCHAR)), ''),
             NULLIF(TRIM(CAST(source_watermark_effective AS VARCHAR)), ''),
             NULLIF(TRIM(CAST(source_watermark_before AS VARCHAR)), '')
           ),
           event_time
         ) FILTER (WHERE normalized_status = 'succeeded') AS latest_success_watermark,
         LIST(STRUCT_PACK(
           status := normalized_status,
           "time" := event_time,
           dataflow_run_id := dataflow_run_id
         ) ORDER BY event_time DESC NULLS LAST, dataflow_run_id DESC)
           FILTER (WHERE run_rank <= 5) AS last_statuses,
         BOOL_OR(watermark_enabled) AS watermark_enabled,
         COUNT(*) AS run_count,
         COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS succeeded_runs,
         COUNT(*) FILTER (WHERE normalized_status = 'failed') AS failed_runs,
         COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skipped_runs,
         COUNT(*) FILTER (WHERE normalized_status = 'running') AS running_runs,
         COUNT(*) FILTER (WHERE normalized_status = 'pending') AS pending_runs,
         COUNT(*) FILTER (WHERE normalized_status = 'skipped' AND non_skipped_seen = 0) AS skipped_streak
  FROM ranked GROUP BY dataflow_id
), registry AS (
  SELECT *,
         CASE WHEN latest_freshness_at IS NULL THEN NULL ELSE GREATEST(0, epoch(current_timestamp - latest_freshness_at)) END AS age_seconds,
         CASE WHEN latest_freshness_at IS NULL THEN NULL ELSE GREATEST(0, epoch(current_timestamp - latest_freshness_at)) / 86400.0 END AS age_days
  FROM per_dataflow
)
"""

_REGISTRY_SQL = """
SELECT dataflow_id, workspace_id, COALESCE(dataflow_name, dataflow_id) AS dataflow_name,
       dataflow_description, COALESCE(stage, 'unknown') AS stage, group_number,
       execution_order, processing_mode, is_active, configure, operation_type,
       COALESCE(source_name, 'unknown') AS source_name,
       COALESCE(destination_name, 'unknown') AS destination_name,
       COALESCE(source_format, source_connection_type, 'unknown') AS source_format,
       COALESCE(destination_format, destination_connection_type, 'unknown') AS destination_format,
       COALESCE(source_connection_type, 'unknown') AS source_connection_type,
       COALESCE(destination_connection_type, 'unknown') AS destination_connection_type,
       source_id, source_catalog, source_database, source_schema, source_table,
       source_full_table, source_path, source_query, source_python_function,
       source_watermark_columns, source_filter_expression, source_configure, source_action,
       transform_deduplicate_columns, transform_latest_data_columns,
       transform_filter_expression, transform_additional_columns,
       transform_schema_hints, transform_configure,
       destination_id, destination_catalog, destination_database, destination_schema,
       destination_table, destination_full_table, destination_path,
       COALESCE(destination_load_type, 'unknown') AS destination_load_type,
       destination_merge_keys, destination_partition_columns, destination_configure,
       latest_status AS status, latest_status AS latest_run_status, latest_run_at,
       latest_error_message, latest_freshness_at, latest_freshness_status,
       age_seconds, age_days, watermark_enabled,
       CASE WHEN watermark_enabled THEN 'configured' ELSE 'not_configured' END AS coverage_state,
       latest_movement_state AS movement_state, latest_adjustment_state AS adjustment_state,
       watermark_time, source_watermark_before, source_watermark_after,
       source_watermark_effective, latest_success_watermark, last_statuses,
       skipped_streak, run_count,
       succeeded_runs AS succeeded_count, failed_runs AS failed_count,
       skipped_runs AS skipped_count, running_runs AS running_count, pending_runs AS pending_count,
       CASE WHEN age_days > 7 THEN 1 ELSE 0 END AS is_stale
FROM registry
"""

_AGE_BY_DATAFLOW_SQL = """
SELECT dataflow_id, COALESCE(dataflow_name, dataflow_id) AS dataflow_name,
       CONCAT(COALESCE(source_name, 'unknown'), ' -> ', COALESCE(destination_name, 'unknown')) AS target,
       latest_freshness_at, latest_freshness_status, age_seconds, age_days
FROM registry
WHERE age_days IS NOT NULL
ORDER BY age_days DESC, dataflow_name
LIMIT 200
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
  (SELECT COUNT(*) FROM etl_rows WHERE normalized_status = 'succeeded') AS successful_runs,
  (SELECT COUNT(*) FROM etl_rows WHERE normalized_status IN ('succeeded','skipped')) AS freshness_runs,
  (SELECT COUNT(*) FROM etl_rows WHERE normalized_status = 'failed') AS failed_runs,
  (SELECT COUNT(*) FROM etl_rows WHERE normalized_status = 'skipped') AS skipped_runs,
  COUNT(*) AS observed_dataflows,
  (SELECT COUNT(*) FROM etl_rows WHERE dataflow_id IS NULL OR TRIM(dataflow_id) IN ('', 'unknown', 'none', 'null', 'nan')) AS missing_dataflow_id_runs,
  COUNT(*) FILTER (WHERE latest_freshness_at IS NOT NULL) AS dataflows_with_freshness_evidence,
  COUNT(*) FILTER (WHERE latest_status NOT IN ('succeeded','skipped')) AS latest_status_issue_dataflows,
  COUNT(*) FILTER (WHERE latest_movement_state = 'invalid') AS latest_watermark_invalid_dataflows,
  COUNT(*) FILTER (WHERE latest_movement_state = 'incomplete') AS latest_watermark_incomplete_dataflows,
  COUNT(*) FILTER (WHERE latest_movement_state IN ('invalid','incomplete')) AS latest_watermark_issue_dataflows,
  COUNT(*) FILTER (WHERE watermark_enabled) AS watermark_enabled_dataflows,
  ROUND(100.0 * COUNT(*) FILTER (WHERE watermark_enabled) / NULLIF(COUNT(*), 0), 2) AS watermark_coverage_rate,
  (SELECT COUNT(*) FROM etl_rows WHERE movement_state = 'advanced') AS watermark_advanced_runs,
  (SELECT COUNT(*) FROM etl_rows WHERE movement_state = 'initialized') AS watermark_initialized_runs,
  (SELECT COUNT(*) FROM etl_rows WHERE movement_state = 'unchanged') AS watermark_unchanged_runs,
  (SELECT COUNT(*) FROM etl_rows WHERE movement_state = 'incomplete') AS watermark_incomplete_runs,
  (SELECT COUNT(*) FROM etl_rows WHERE adjustment_state = 'adjusted') AS watermark_adjusted_runs,
  (SELECT COUNT(*) FROM etl_rows WHERE movement_state = 'invalid') AS watermark_invalid_runs,
  (SELECT COUNT(*) FROM etl_rows WHERE movement_state = 'unknown') AS watermark_unknown_runs,
  ROUND(100.0 * (SELECT COUNT(*) FROM etl_rows WHERE movement_state = 'advanced') /
    NULLIF((SELECT COUNT(*) FROM etl_rows WHERE movement_state IN ('advanced','unchanged')), 0), 2) AS watermark_advanced_rate,
  COUNT(*) FILTER (WHERE skipped_streak >= 3) AS skipped_streak_dataflows,
  3 AS skipped_streak_threshold,
  COUNT(*) FILTER (WHERE age_days > 7) AS stale_candidates,
  COUNT(*) FILTER (WHERE age_days > 7) AS stale_dataflows,
  7 AS stale_threshold_days,
  ROUND(100.0 * COUNT(*) FILTER (WHERE age_days > 7) / NULLIF(COUNT(*), 0), 2) AS stale_dataflow_rate,
  COALESCE(MIN(age_days), 0) AS min_age_days,
  {discrete_percentile('age_days', 0.50)} AS p50_age_days,
  {discrete_percentile('age_days', 0.95)} AS p95_age_days,
  COALESCE(MAX(age_days), 0) AS max_age_days,
  COALESCE(MIN(age_seconds), 0) AS min_age_seconds,
  {discrete_percentile('age_seconds', 0.50)} AS p50_age_seconds,
  {discrete_percentile('age_seconds', 0.95)} AS p95_age_seconds,
  COALESCE(MAX(age_seconds), 0) AS max_age_seconds
FROM registry
"""

_ATTENTION_SQL = """
SELECT
  COUNT(*) FILTER (WHERE age_days > 7) AS stale_candidates,
  (SELECT COUNT(*) FROM etl_rows WHERE movement_state = 'unchanged')
    AS watermark_unchanged_runs
FROM registry
"""

_ATTENTION_DERIVED_CTES = f"""
, etl_rows AS (
  SELECT *, {_HAS_WATERMARK} AS watermark_enabled,
         CASE
           WHEN NOT {_HAS_WATERMARK} THEN 'not_configured'
           WHEN {_INVALID_WATERMARK} THEN 'invalid'
           WHEN normalized_status IN ('running', 'pending') THEN 'incomplete'
           WHEN normalized_status IN ('failed', 'skipped') THEN 'unchanged'
           WHEN NULLIF(TRIM(CAST(source_watermark_before AS VARCHAR)), '') IS NOT NULL
            AND NULLIF(TRIM(CAST(source_watermark_after AS VARCHAR)), '') IS NOT NULL
             THEN CASE WHEN TRIM(CAST(source_watermark_before AS VARCHAR)) <> TRIM(CAST(source_watermark_after AS VARCHAR)) THEN 'advanced' ELSE 'unchanged' END
           WHEN NULLIF(TRIM(CAST(source_watermark_before AS VARCHAR)), '') IS NULL
            AND NULLIF(TRIM(CAST(source_watermark_after AS VARCHAR)), '') IS NOT NULL THEN 'initialized'
           ELSE 'incomplete'
         END AS movement_state
  FROM filtered_dataflows
  WHERE LOWER(COALESCE(operation_type, 'unknown')) = 'etl'
), per_dataflow AS (
  SELECT dataflow_id,
         ARG_MAX(movement_state, event_time) AS latest_movement_state,
         MAX(event_time) FILTER (WHERE normalized_status IN ('succeeded','skipped')) AS latest_freshness_at
  FROM etl_rows
  WHERE dataflow_id IS NOT NULL AND TRIM(dataflow_id) NOT IN ('', 'unknown', 'none', 'null', 'nan')
  GROUP BY dataflow_id
), registry AS (
  SELECT *, CASE WHEN latest_freshness_at IS NULL THEN NULL
                 ELSE GREATEST(0, epoch(current_timestamp - latest_freshness_at)) / 86400.0 END AS age_days
  FROM per_dataflow
)
"""

_MOVEMENT_TREND_SQL = """
SELECT date_trunc(?, timezone(?, event_time)) AS bucket_start,
       COUNT(*) FILTER (WHERE movement_state = 'advanced') AS advanced,
       COUNT(*) FILTER (WHERE movement_state = 'initialized') AS initialized,
       COUNT(*) FILTER (WHERE movement_state = 'unchanged') AS unchanged,
       COUNT(*) FILTER (WHERE movement_state = 'incomplete') AS incomplete,
       COUNT(*) FILTER (WHERE movement_state = 'invalid') AS invalid,
       COUNT(*) FILTER (WHERE movement_state = 'unknown') AS unknown,
       COUNT(*) FILTER (WHERE adjustment_state = 'adjusted') AS adjusted,
       COUNT(*) AS watermark_enabled_runs,
       ROUND(100.0 * COUNT(*) FILTER (WHERE movement_state = 'advanced') /
         NULLIF(COUNT(*) FILTER (WHERE movement_state IN ('advanced','unchanged')), 0), 2) AS advanced_rate
FROM etl_rows WHERE watermark_enabled AND event_time IS NOT NULL
GROUP BY bucket_start ORDER BY bucket_start
"""

_AGE_DISTRIBUTION_SQL = """
SELECT CASE WHEN age_days IS NULL THEN 'Unknown' WHEN age_days <= 1 THEN '≤1d'
            WHEN age_days <= 3 THEN '1–3d' WHEN age_days <= 7 THEN '3–7d'
            WHEN age_days <= 30 THEN '7–30d' ELSE '>30d' END AS bucket,
       COUNT(*) AS dataflows,
       CASE WHEN MIN(age_days) > 7 THEN 1 ELSE 0 END AS is_stale
FROM registry GROUP BY bucket
ORDER BY CASE bucket WHEN '≤1d' THEN 1 WHEN '1–3d' THEN 2 WHEN '3–7d' THEN 3 WHEN '7–30d' THEN 4 WHEN '>30d' THEN 5 ELSE 6 END
"""

_STAGE_COVERAGE_SQL = """
SELECT COALESCE(NULLIF(stage, ''), 'unknown') AS stage,
       COUNT(DISTINCT dataflow_id) AS observed_dataflows,
       COUNT(DISTINCT dataflow_id) FILTER (WHERE watermark_enabled) AS watermark_enabled_dataflows,
       COUNT(DISTINCT dataflow_id) - COUNT(DISTINCT dataflow_id) FILTER (WHERE watermark_enabled) AS not_configured_dataflows,
       ROUND(100.0 * COUNT(DISTINCT dataflow_id) FILTER (WHERE watermark_enabled) / NULLIF(COUNT(DISTINCT dataflow_id), 0), 2) AS coverage_rate
FROM etl_rows WHERE dataflow_id IS NOT NULL GROUP BY stage ORDER BY observed_dataflows DESC, stage LIMIT 100
"""

_STREAK_DISTRIBUTION_SQL = """
SELECT CASE WHEN skipped_streak = 1 THEN '1'
            WHEN skipped_streak <= 3 THEN '2–3'
            WHEN skipped_streak <= 7 THEN '4–7'
            ELSE '>7' END AS bucket,
       COUNT(*) AS dataflows
FROM registry WHERE skipped_streak > 0
GROUP BY bucket
ORDER BY CASE bucket WHEN '1' THEN 1 WHEN '2–3' THEN 2 WHEN '4–7' THEN 3 ELSE 4 END
"""

_MOVEMENT_EVIDENCE_SQL = """
SELECT dataflow_id, COALESCE(NULLIF(dataflow_name, ''), dataflow_id) AS dataflow_name,
       event_time AS end_time, movement_state, movement_state AS movement,
       adjustment_state, normalized_status AS status,
       CAST(source_watermark_before AS VARCHAR) AS before,
       CAST(source_watermark_after AS VARCHAR) AS after,
       CAST(source_watermark_effective AS VARCHAR) AS effective
FROM etl_rows WHERE watermark_enabled AND dataflow_id IS NOT NULL
ORDER BY event_time DESC NULLS LAST LIMIT 100
"""

_BUNDLE_SQL = f"""
, summary_result AS ({_SUMMARY_SQL}),
age_by_dataflow_result AS ({_AGE_BY_DATAFLOW_SQL}),
movement_trend_result AS ({_MOVEMENT_TREND_SQL}),
age_distribution_result AS ({_AGE_DISTRIBUTION_SQL}),
stage_coverage_result AS ({_STAGE_COVERAGE_SQL}),
streak_distribution_result AS ({_STREAK_DISTRIBUTION_SQL})
SELECT
  (SELECT summary_row FROM summary_result summary_row) AS summary,
  (SELECT list(age_row) FROM age_by_dataflow_result age_row) AS age_by_dataflow,
  (SELECT list(movement_row) FROM movement_trend_result movement_row) AS movement_trend,
  (SELECT list(age_bucket) FROM age_distribution_result age_bucket) AS age_distribution,
  (SELECT list(stage_row) FROM stage_coverage_result stage_row) AS stage_coverage,
  (SELECT list(streak_row) FROM streak_distribution_result streak_row) AS streak_distribution
"""
