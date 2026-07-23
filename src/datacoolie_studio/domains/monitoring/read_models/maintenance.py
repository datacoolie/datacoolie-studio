from __future__ import annotations

from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.metrics.volume import lakehouse_destination_sql
from datacoolie_studio.domains.monitoring.read_models.common import (
    AnalyticsContext,
    discrete_percentile,
    filtered_ctes,
    one,
    paged_rows,
    reader_context,
    rows,
    standalone_derived_query,
)


_DATAFLOW_COLUMNS = (
    "_source_id", "job_id", "dataflow_id", "dataflow_name", "stage", "status",
    "start_time", "end_time", "duration_seconds", "operation_type", "source_name",
    "source_full_table", "source_table", "source_path", "source_rows_read",
    "destination_name", "destination_connection_type", "destination_format",
    "destination_catalog", "destination_database", "destination_schema",
    "destination_full_table", "destination_table", "destination_path",
    "destination_load_type", "destination_operation_type", "destination_rows_written",
    "destination_rows_inserted", "destination_rows_updated", "destination_rows_deleted",
    "destination_bytes_added", "destination_bytes_removed", "destination_bytes_saved",
    "destination_files_added", "destination_files_removed",
)
_JOB_COLUMNS = ("_source_id", "job_id")
_IS_LAKEHOUSE = lakehouse_destination_sql()


def maintenance_read_model(
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
        maintenance_ctes = f"{ctes} {_DERIVED_CTES}"
        registry = rows(conn.execute(f"{maintenance_ctes} {_REGISTRY_SQL}", params))
        summary = one(conn.execute(f"{maintenance_ctes} {_SUMMARY_SQL}", params))
        status_trend = rows(conn.execute(
            f"{maintenance_ctes} {_STATUS_TREND_SQL}",
            [*params, grain, timezone_name],
        ))
        reclaim_trend = rows(conn.execute(
            f"{maintenance_ctes} {_RECLAIM_TREND_SQL}",
            [*params, grain, timezone_name],
        ))
        formats = rows(conn.execute(f"{maintenance_ctes} {_FORMAT_SQL}", params))
    table_outcome = [_maintenance_chart_row(row) for row in registry[:200]]
    efficiency = [_maintenance_chart_row(row) for row in registry[:800]]
    return {
        "generation": generation, "summary": summary,
        "table_outcome": table_outcome, "table_efficiency_points": efficiency,
        "status_by_date": [_trend_row(row, grain) for row in status_trend],
        "reclaim_by_date": [_trend_row(row, grain) for row in reclaim_trend],
        "bytes_reclaimed_by_date": [_trend_row(row, grain) for row in reclaim_trend],
        "format_comparison": formats,
    }


def maintenance_evidence_read_model(
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
        "target": "target", "target_display": "target_display",
        "destination_format": "destination_format", "table_health": "table_health",
        "latest_status": "latest_status", "latest_maintenance_time": "latest_maintenance_time",
        "latest_etl_write_time": "latest_etl_write_time",
        "maintenance_lag_seconds": "maintenance_lag_seconds", "run_count": "run_count",
        "files_removed": "files_removed", "bytes_reclaimed": "bytes_reclaimed",
        "bytes_reclaimed_per_second": "bytes_reclaimed_per_second",
        "no_op_runs": "no_op_runs", "duration_seconds": "duration_seconds",
        "attention_priority": "attention_priority",
    }
    order_column = sort_columns.get(sort_by, "attention_priority")
    direction = "ASC" if sort_dir == "asc" else "DESC"
    with reader_context(paths, analytics_context) as (conn, source_ids, generation):
        if conn is None or not source_ids:
            return {"generation": generation, "records": [], "total_records": 0}
        ctes, params = filtered_ctes(
            source_ids, filters,
            dataflow_columns=_DATAFLOW_COLUMNS,
            job_columns=_JOB_COLUMNS,
        )
        registry_query = f"{ctes} {_DERIVED_CTES} {_REGISTRY_SQL}"
        records, total = paged_rows(conn.execute(
            f"SELECT evidence.*, COUNT(*) OVER () AS __total_records "
            f"FROM ({registry_query}) evidence "
            f"ORDER BY {order_column} {direction} NULLS LAST, target ASC "
            "LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ))
    return {"generation": generation, "records": records, "total_records": total}


def maintenance_attention_sql() -> str:
    return standalone_derived_query(_DERIVED_CTES, _ATTENTION_SQL)


def _empty(generation: str) -> dict[str, Any]:
    return {
        "generation": generation, "summary": {}, "table_outcome": [],
        "table_efficiency_points": [], "status_by_date": [], "reclaim_by_date": [],
        "bytes_reclaimed_by_date": [], "format_comparison": [],
    }


def _maintenance_chart_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in (
        "target", "target_display", "destination_format", "format", "latest_status",
        "status", "run_count", "duration_seconds", "bytes_reclaimed", "bytes_removed",
        "files_removed", "bytes_reclaimed_per_second", "table_health", "attention_reason",
        "attention_priority",
    )}


def _trend_row(row: dict[str, Any], grain: str) -> dict[str, Any]:
    return {
        "date": row.get("bucket_start"), "bucket": row.get("bucket_start"),
        "bucket_start": row.get("bucket_start"), "bucket_end": None, "grain": grain,
        **{key: value for key, value in row.items() if key != "bucket_start"},
    }


_TARGET = """CONCAT(
  COALESCE(NULLIF(destination_name, ''), 'unknown'),
  CASE
    WHEN COALESCE(NULLIF(destination_full_table, ''), NULLIF(destination_table, ''), NULLIF(destination_path, '')) IS NOT NULL
      THEN CONCAT('::', COALESCE(NULLIF(destination_full_table, ''), NULLIF(destination_table, ''), NULLIF(destination_path, '')))
    ELSE ':unknown'
  END
)"""
_IS_MAINTENANCE = """(
  LOWER(COALESCE(operation_type, '')) = 'maintenance' OR
  LOWER(COALESCE(destination_operation_type, '')) IN ('compact', 'cleanup', 'maintenance')
)"""
_IS_ACTIVE = f"""(
  LOWER(COALESCE(operation_type, '')) = 'etl' AND {_IS_LAKEHOUSE} AND
  (COALESCE(destination_files_added, 0) > 0 OR COALESCE(destination_bytes_added, 0) > 0 OR
   COALESCE(destination_rows_written, 0) > 0 OR COALESCE(destination_rows_inserted, 0) > 0 OR
   COALESCE(destination_rows_updated, 0) > 0 OR COALESCE(destination_rows_deleted, 0) > 0)
)"""

_DERIVED_CTES = f"""
, classified AS (
  SELECT *, {_TARGET} AS target, {_IS_MAINTENANCE} AS is_maintenance, {_IS_ACTIVE} AS is_active,
         normalized_status = 'succeeded' AND COALESCE(destination_bytes_removed, 0) = 0
           AND COALESCE(destination_files_removed, 0) = 0 AS is_no_op
  FROM filtered_dataflows
), relevant AS (
  SELECT * FROM classified WHERE is_maintenance OR is_active
), target_rollup AS (
  SELECT target,
    BOOL_OR(is_active) AS active_lakehouse_table,
    BOOL_OR(is_maintenance) AS maintained_table,
    COUNT(*) FILTER (WHERE is_maintenance) AS run_count,
    COUNT(*) FILTER (WHERE is_maintenance AND normalized_status = 'succeeded') AS succeeded,
    COUNT(*) FILTER (WHERE is_maintenance AND normalized_status = 'failed') AS failed,
    COUNT(*) FILTER (WHERE is_maintenance AND normalized_status = 'skipped') AS skipped,
    COUNT(*) FILTER (WHERE is_maintenance AND normalized_status = 'running') AS running,
    COUNT(*) FILTER (WHERE is_maintenance AND normalized_status = 'pending') AS pending,
    COUNT(*) FILTER (WHERE is_maintenance AND normalized_status NOT IN ('succeeded','failed','skipped','running','pending')) AS unknown,
    ARG_MAX(normalized_status, event_time) FILTER (WHERE is_maintenance) AS latest_status,
    MAX(event_time) FILTER (WHERE is_maintenance) AS latest_maintenance_time,
    MAX(event_time) FILTER (WHERE is_active) AS latest_etl_write_time,
    SUM(COALESCE(destination_files_removed, 0)) FILTER (WHERE is_maintenance) AS files_removed,
    SUM(COALESCE(destination_bytes_removed, 0)) FILTER (WHERE is_maintenance) AS bytes_reclaimed,
    SUM(COALESCE(destination_bytes_saved, 0)) FILTER (WHERE is_maintenance) AS bytes_saved,
    SUM(COALESCE(duration_seconds, 0)) FILTER (WHERE is_maintenance) AS duration_seconds,
    COUNT(*) FILTER (WHERE is_maintenance AND is_no_op) AS no_op_runs,
    SUM(COALESCE(duration_seconds, 0)) FILTER (WHERE is_maintenance AND is_no_op) AS no_op_duration_seconds,
    ARG_MAX(destination_table, event_time) AS destination_table,
    ARG_MAX(destination_full_table, event_time) AS destination_full_table,
    ARG_MAX(destination_path, event_time) AS destination_path,
    ARG_MAX(destination_name, event_time) AS destination_name,
    ARG_MAX(destination_connection_type, event_time) AS destination_connection_type,
    COALESCE(ARG_MAX(NULLIF(destination_format, ''), event_time), 'unknown') AS destination_format,
    COUNT(*) FILTER (WHERE is_active) AS upstream_run_count
  FROM relevant GROUP BY target
), upstream_dataflow_rollup AS (
  SELECT target, dataflow_id,
    ARG_MAX(dataflow_name, event_time) AS dataflow_name,
    ARG_MAX(stage, event_time) AS stage,
    ARG_MAX(operation_type, event_time) AS operation_type,
    ARG_MAX(destination_operation_type, event_time) AS destination_operation_type,
    ARG_MAX(source_name, event_time) AS source_name,
    ARG_MAX(source_full_table, event_time) AS source_full_table,
    ARG_MAX(source_table, event_time) AS source_table,
    ARG_MAX(source_path, event_time) AS source_path,
    ARG_MAX(destination_load_type, event_time) AS load_type,
    ARG_MAX(normalized_status, event_time) AS latest_status,
    MAX(event_time) AS latest_time,
    COUNT(*) AS run_count,
    COALESCE(SUM(source_rows_read), 0) AS rows_read
  FROM classified
  WHERE is_active
  GROUP BY target, dataflow_id
), upstream_by_target AS (
  SELECT target, LIST(STRUCT_PACK(
    dataflow_id := dataflow_id,
    dataflow_name := COALESCE(dataflow_name, dataflow_id, 'unknown'),
    stage := COALESCE(stage, 'unknown'),
    operation_type := COALESCE(operation_type, 'unknown'),
    destination_operation_type := destination_operation_type,
    source_name := COALESCE(source_name, 'unknown'),
    source_full_table := source_full_table,
    source_table := source_table,
    source_path := source_path,
    load_type := COALESCE(load_type, destination_operation_type, '-'),
    latest_status := COALESCE(latest_status, 'unknown'),
    latest_time := latest_time,
    run_count := run_count,
    rows_read := rows_read
  ) ORDER BY dataflow_name, dataflow_id) AS upstream_dataflows
  FROM upstream_dataflow_rollup
  GROUP BY target
), registry AS (
  SELECT *, GREATEST(0, COALESCE(epoch(latest_etl_write_time - latest_maintenance_time), 0)) AS maintenance_lag_seconds,
    CASE WHEN NOT maintained_table AND active_lakehouse_table THEN 'warning'
         WHEN latest_status = 'failed' THEN 'has_issues'
         WHEN latest_status IN ('running','pending','skipped') THEN 'warning'
         WHEN GREATEST(0, COALESCE(epoch(latest_etl_write_time - latest_maintenance_time), 0)) > 7 * 86400 THEN 'warning'
         WHEN maintained_table THEN 'healthy' ELSE 'no_evidence' END AS table_health,
    CASE WHEN NOT maintained_table AND active_lakehouse_table THEN 'Missing maintenance coverage'
         WHEN latest_status = 'failed' THEN 'Latest maintenance failed'
         WHEN latest_status IN ('running','pending') THEN CONCAT('Latest maintenance is ', latest_status)
         WHEN latest_status = 'skipped' THEN 'Latest maintenance skipped'
         WHEN GREATEST(0, COALESCE(epoch(latest_etl_write_time - latest_maintenance_time), 0)) > 7 * 86400 THEN 'Maintenance lag exceeds 7 days'
         WHEN maintained_table THEN 'Maintained table' ELSE 'No maintenance evidence' END AS attention_reason,
    CASE WHEN latest_status = 'failed' THEN 100 WHEN latest_status IN ('running','pending') THEN 90
         WHEN NOT maintained_table AND active_lakehouse_table THEN 80 WHEN latest_status = 'skipped' THEN 70
         WHEN GREATEST(0, COALESCE(epoch(latest_etl_write_time - latest_maintenance_time), 0)) > 7 * 86400 THEN 60
         WHEN NOT maintained_table THEN 10 ELSE 0 END AS attention_priority
  FROM target_rollup
), maintenance_thresholds AS (
  SELECT {discrete_percentile('duration_seconds', 0.95, 'is_maintenance AND duration_seconds > 0')} AS duration_p95
  FROM classified
)
"""

_REGISTRY_SQL = """
SELECT target, CASE WHEN CONTAINS(target, '::') THEN split_part(target, '::', 2) ELSE target END AS target_display,
       target AS table, destination_table, destination_full_table, destination_path,
       destination_name, destination_name AS destination_connection_name,
       destination_connection_type, destination_format AS format, destination_format,
       active_lakehouse_table, maintained_table, run_count, succeeded, failed, skipped,
       running, pending, unknown, COALESCE(latest_status, 'missing') AS latest_status,
       COALESCE(latest_status, 'missing') AS status, latest_maintenance_time, latest_etl_write_time,
       maintenance_lag_seconds, maintenance_lag_seconds > 7 * 86400 AS maintenance_lag_warning,
       7 AS maintenance_lag_warning_days, COALESCE(files_removed, 0) AS files_removed,
       COALESCE(bytes_reclaimed, 0) AS bytes_removed, COALESCE(bytes_reclaimed, 0) AS bytes_reclaimed,
       COALESCE(bytes_saved, 0) AS bytes_saved, COALESCE(duration_seconds, 0) AS duration_seconds,
       COALESCE(ROUND(bytes_reclaimed / NULLIF(duration_seconds, 0), 3), 0) AS bytes_reclaimed_per_second,
       no_op_runs, COALESCE(no_op_duration_seconds, 0) AS no_op_duration_seconds,
       table_health, attention_reason, attention_priority,
       COALESCE(u.upstream_dataflows, []) AS upstream_dataflows,
       upstream_run_count
FROM registry r
LEFT JOIN upstream_by_target u USING (target)
ORDER BY attention_priority DESC, bytes_reclaimed DESC, files_removed DESC,
         COALESCE(latest_maintenance_time, latest_etl_write_time) DESC NULLS LAST, target
"""

_SUMMARY_SQL = """
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
  (SELECT COUNT(*) FROM classified WHERE is_maintenance) AS total_maintenance_runs,
  (SELECT COUNT(*) FROM classified WHERE is_maintenance AND normalized_status = 'succeeded') AS succeeded_ops,
  (SELECT COUNT(*) FROM classified WHERE is_maintenance AND normalized_status = 'failed') AS failed_ops,
  (SELECT COUNT(*) FROM classified WHERE is_maintenance AND normalized_status = 'skipped') AS skipped_ops,
  (SELECT COUNT(*) FROM classified WHERE is_maintenance AND normalized_status = 'running') AS running_ops,
  (SELECT COUNT(*) FROM classified WHERE is_maintenance AND normalized_status = 'pending') AS pending_ops,
  (SELECT COALESCE(SUM(destination_files_removed), 0) FROM classified WHERE is_maintenance) AS files_removed,
  (SELECT COALESCE(SUM(destination_bytes_removed), 0) FROM classified WHERE is_maintenance) AS bytes_reclaimed,
  (SELECT COALESCE(SUM(destination_bytes_saved), 0) FROM classified WHERE is_maintenance) AS bytes_saved,
  (SELECT COALESCE(SUM(duration_seconds), 0) FROM classified WHERE is_maintenance) AS duration_seconds,
  (SELECT COALESCE(SUM(duration_seconds), 0) FROM classified WHERE is_maintenance AND normalized_status = 'succeeded') AS succeeded_duration_seconds,
  (SELECT COUNT(*) FROM classified WHERE is_maintenance AND is_no_op) AS no_op_runs,
  (SELECT COUNT(*) FROM classified, maintenance_thresholds
    WHERE is_maintenance AND duration_p95 > 0 AND duration_seconds >= duration_p95) AS high_duration_runs,
  COUNT(*) FILTER (WHERE no_op_runs > 0) AS no_op_tables,
  COALESCE(SUM(no_op_duration_seconds), 0) AS no_op_duration_seconds,
  COUNT(*) FILTER (WHERE bytes_reclaimed > 0 OR files_removed > 0) AS tables_with_reclaim,
  COUNT(*) FILTER (WHERE table_health = 'has_issues') AS tables_with_issues,
  COUNT(*) FILTER (WHERE table_health = 'warning') AS tables_with_warnings,
  COUNT(*) FILTER (WHERE latest_status = 'failed') AS latest_failed_tables,
  COUNT(*) FILTER (WHERE latest_status = 'skipped') AS latest_skipped_tables,
  COUNT(*) FILTER (WHERE latest_status IN ('running','pending')) AS latest_active_tables,
  COUNT(*) FILTER (WHERE maintenance_lag_seconds > 7 * 86400) AS lagged_tables,
  COUNT(*) FILTER (WHERE active_lakehouse_table) AS active_lakehouse_tables,
  COUNT(*) FILTER (WHERE active_lakehouse_table AND maintained_table) AS maintained_tables,
  COUNT(*) FILTER (WHERE active_lakehouse_table AND NOT maintained_table) AS coverage_missing_tables,
  ROUND(100.0 * COUNT(*) FILTER (WHERE active_lakehouse_table AND maintained_table) /
    NULLIF(COUNT(*) FILTER (WHERE active_lakehouse_table), 0), 2) AS coverage_rate
FROM registry
"""

_ATTENTION_SQL = """
SELECT
  COUNT(*) FILTER (WHERE active_lakehouse_table AND NOT maintained_table)
    AS coverage_missing_tables,
  COUNT(*) FILTER (WHERE maintenance_lag_seconds > 7 * 86400) AS lagged_tables,
  COUNT(*) FILTER (WHERE latest_status IN ('running', 'pending')) AS latest_active_tables
FROM registry
"""

_STATUS_TREND_SQL = """
SELECT date_trunc(?, timezone(?, event_time)) AS bucket_start,
       COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS succeeded,
       COUNT(*) FILTER (WHERE normalized_status = 'failed') AS failed,
       COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skipped,
       COUNT(*) FILTER (WHERE normalized_status = 'running') AS running,
       COUNT(*) FILTER (WHERE normalized_status = 'pending') AS pending,
       COUNT(*) FILTER (WHERE normalized_status NOT IN ('succeeded','failed','skipped','running','pending')) AS unknown,
       COUNT(*) AS total,
       ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'succeeded') /
         NULLIF(COUNT(*) FILTER (WHERE normalized_status IN ('succeeded','failed')), 0), 2) AS success_rate
FROM classified WHERE is_maintenance AND event_time IS NOT NULL
GROUP BY bucket_start ORDER BY bucket_start
"""

_RECLAIM_TREND_SQL = """
SELECT date_trunc(?, timezone(?, event_time)) AS bucket_start,
       SUM(COALESCE(destination_bytes_removed, 0)) AS bytes_reclaimed,
       SUM(COALESCE(destination_bytes_saved, 0)) AS bytes_saved,
       SUM(COALESCE(destination_files_removed, 0)) AS files_removed,
       COUNT(*) AS runs
FROM classified WHERE is_maintenance AND event_time IS NOT NULL
GROUP BY bucket_start ORDER BY bucket_start
"""

_FORMAT_SQL = """
SELECT COALESCE(NULLIF(destination_format, ''), 'unknown') AS format,
       SUM(COALESCE(destination_files_removed, 0)) AS files_removed,
       SUM(COALESCE(destination_bytes_removed, 0)) AS bytes_removed,
       COUNT(*) AS count
FROM classified WHERE is_maintenance GROUP BY format ORDER BY format
"""
