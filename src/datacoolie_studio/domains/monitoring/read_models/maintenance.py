from __future__ import annotations

from datacoolie_studio.domains.monitoring.dimensions import known_dimension_sql

from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.metrics.volume import lakehouse_destination_sql
from datacoolie_studio.domains.monitoring.metrics.investigation import declared_destination_key_sql
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
    "_source_id", "_file_uri", "_record_line", "raw_record_json", "job_id", "dataflow_id", "dataflow_run_id", "log_schema_version", "attempts", "dataflow_name", "stage", "status", "message", "error_message",
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
    table_outcome = _chart_candidates(registry, efficiency=False, limit=200)
    efficiency = _chart_candidates(registry, efficiency=True, limit=800)
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
        "files_removed": "files_removed_observed", "bytes_reclaimed": "bytes_reclaimed_observed",
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
        if not records and offset:
            total = int(conn.execute(f"SELECT COUNT(*) FROM ({registry_query}) evidence", params).fetchone()[0])
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
        "status", "run_count", "succeeded", "duration_seconds", "bytes_reclaimed", "bytes_removed",
        "files_removed", "bytes_reclaimed_per_second", "table_health", "attention_reason",
        "attention_priority", "no_op_runs", "no_op_duration_seconds",
        * _EVIDENCE_AGGREGATES,
    )}


def _chart_candidates(registry: list[dict[str, Any]], *, efficiency: bool, limit: int) -> list[dict[str, Any]]:
    # Bound each metric separately so byte-rich destinations cannot hide file-only evidence.
    selected: dict[str, dict[str, Any]] = {}
    for metric in ("bytes", "files"):
        field = f"efficiency_{metric}_removed" if efficiency else (
            "bytes_reclaimed_observed" if metric == "bytes" else "files_removed_observed"
        )
        candidates = [row for row in registry if row.get(field) is not None and (
            row[field] >= 0 and (row.get(f"efficiency_{metric}_duration_seconds") or 0) > 0
            if efficiency else row[field] > 0
        )]
        candidates.sort(key=lambda row: (-row[field], row["target"]))
        for row in candidates[:limit]:
            selected[row["target"]] = _maintenance_chart_row(row)
    return list(selected.values())


def _trend_row(row: dict[str, Any], grain: str) -> dict[str, Any]:
    return {
        "date": row.get("bucket_start"), "bucket": row.get("bucket_start"),
        "bucket_start": row.get("bucket_start"), "bucket_end": None, "grain": grain,
        **{key: value for key, value in row.items() if key != "bucket_start"},
    }


_TARGET = declared_destination_key_sql()
_TARGET_DISPLAY = """CONCAT(
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
_NO_OP = """(
  normalized_status = 'succeeded'
  AND EXISTS (
    SELECT 1 FROM json_each(CASE WHEN json_type(operation_details)='ARRAY' THEN operation_details
      WHEN json_type(operation_details)='OBJECT' THEN json_array(operation_details) ELSE '[]'::JSON END) detail
    WHERE LOWER(TRIM(json_extract_string(detail.value,'$.operation'))) IN ('compact','cleanup','maintenance')
      AND LOWER(TRIM(json_extract_string(detail.value,'$.status')))
        IN ('succeeded','success')
  )
  AND NOT EXISTS (
    SELECT 1 FROM json_each(CASE WHEN json_type(operation_details)='ARRAY' THEN operation_details
      WHEN json_type(operation_details)='OBJECT' THEN json_array(operation_details) ELSE '[]'::JSON END) detail
    WHERE NOT COALESCE(LOWER(TRIM(json_extract_string(detail.value,'$.operation')))='history'
      AND json_type(detail.value,'$.data')='ARRAY',FALSE)
      AND (LOWER(TRIM(COALESCE(json_extract_string(detail.value,'$.operation'),''))) NOT IN ('compact','cleanup','maintenance')
        OR COALESCE(LOWER(TRIM(json_extract_string(detail.value,'$.status'))), '')
          NOT IN ('succeeded','success')
        OR GREATEST(COALESCE(TRY_CAST(json_extract_string(detail.value,'$.metrics.files_added') AS DOUBLE),0),
COALESCE(TRY_CAST(json_extract_string(detail.value,'$.metrics.files_removed') AS DOUBLE),0),
COALESCE(TRY_CAST(json_extract_string(detail.value,'$.metrics.bytes_added') AS DOUBLE),0),
COALESCE(TRY_CAST(json_extract_string(detail.value,'$.metrics.bytes_removed') AS DOUBLE),0),
COALESCE(TRY_CAST(json_extract_string(detail.value,'$.metrics.bytes_saved') AS DOUBLE),0),
COALESCE(TRY_CAST(json_extract_string(detail.value,'$.metrics.rows_written') AS DOUBLE),0),
COALESCE(TRY_CAST(json_extract_string(detail.value,'$.metrics.rows_inserted') AS DOUBLE),0),
COALESCE(TRY_CAST(json_extract_string(detail.value,'$.metrics.rows_updated') AS DOUBLE),0),
COALESCE(TRY_CAST(json_extract_string(detail.value,'$.metrics.rows_deleted') AS DOUBLE),0))>0)
  )
  AND destination_files_added IS NOT NULL
  AND destination_files_removed IS NOT NULL
  AND destination_bytes_added IS NOT NULL
  AND destination_bytes_removed IS NOT NULL
  AND destination_bytes_saved IS NOT NULL
  AND (destination_rows_written IS NULL OR destination_rows_written = 0)
  AND (destination_rows_inserted IS NULL OR destination_rows_inserted = 0)
  AND (destination_rows_updated IS NULL OR destination_rows_updated = 0)
  AND (destination_rows_deleted IS NULL OR destination_rows_deleted = 0)
  AND destination_files_added = 0
  AND destination_files_removed = 0
  AND destination_bytes_added = 0
  AND destination_bytes_removed = 0
  AND destination_bytes_saved = 0
)"""


def _maintenance_metric_total(column: str, eligible: str) -> str:
    population = f"COUNT(*) FILTER (WHERE is_maintenance AND {eligible})"
    measured = f"COUNT({column}) FILTER (WHERE is_maintenance AND {eligible})"
    observed = f"SUM({column}) FILTER (WHERE is_maintenance AND {eligible})"
    return (
        f"CASE WHEN EXISTS (SELECT 1 FROM all_log_versions WHERE log_schema_version >= 7) "
        f"THEN CASE WHEN {measured} = 0 THEN NULL ELSE {observed} END "
        f"WHEN {population} = 0 THEN CASE WHEN COUNT(*) = 0 THEN 0 ELSE NULL END "
        f"WHEN {measured} = {population} THEN {observed} END"
    )


def _maintenance_metric_observed(column: str, eligible: str) -> str:
    return f"COALESCE(SUM({column}) FILTER (WHERE is_maintenance AND {eligible}), 0)"


def _maintenance_metric_measured(column: str, eligible: str) -> str:
    return f"COUNT({column}) FILTER (WHERE is_maintenance AND {eligible})"


_LATEST_RUN_ORDER = (
    "event_time DESC NULLS LAST, dataflow_run_id DESC NULLS LAST, "
    "_source_id DESC NULLS LAST, log_session_id DESC NULLS LAST, job_id DESC NULLS LAST, "
    "normalized_status DESC NULLS LAST, COALESCE(message, error_message) DESC NULLS LAST, "
    "error_message DESC NULLS LAST, _file_uri DESC NULLS LAST, _record_line DESC NULLS LAST"
)


def _evidence_aggregates() -> dict[str, str]:
    """Per-target and page metrics share the same filtered run populations."""
    result: dict[str, str] = {}
    eligible = "is_maintenance"
    population = f"COUNT(*) FILTER (WHERE {eligible})"
    observed_version = "EXISTS (SELECT 1 FROM all_log_versions WHERE log_schema_version >= 7)"
    for name, column in (
        ("bytes_reclaimed", "destination_bytes_removed"),
        ("files_removed", "destination_files_removed"),
        ("bytes_saved", "destination_bytes_saved"),
        ("duration", "duration_seconds"),
    ):
        result[f"{name}_observed"] = f"SUM({column}) FILTER (WHERE {eligible})"
        result[f"{name}_measured_runs"] = f"COUNT({column}) FILTER (WHERE {eligible})"
    result["avg_duration_seconds"] = "AVG(duration_seconds) FILTER (WHERE is_maintenance)"
    result["p95_duration_seconds"] = (
        "CASE WHEN COUNT(duration_seconds) FILTER (WHERE is_maintenance) > 0 THEN "
        + discrete_percentile("duration_seconds", 0.95, "is_maintenance AND duration_seconds IS NOT NULL") + " END"
    )
    for name, numerator, denominator in (
        ("bytes_runtime", "destination_bytes_removed", "duration_seconds"),
        ("bytes_files", "destination_bytes_removed", "destination_files_removed"),
    ):
        paired = f"{eligible} AND {numerator} >= 0 AND {denominator} >= 0"
        count = f"COUNT(*) FILTER (WHERE {paired})"
        result[f"{name}_paired_runs"] = count
        ratio = "bytes_reclaimed_per_second" if name == "bytes_runtime" else "avg_bytes_per_file_removed"
        result[ratio] = (
            f"CASE WHEN ({observed_version} OR {count} = {population}) AND {count} > 0 "
            f"THEN SUM({numerator}) FILTER (WHERE {paired}) / "
            f"NULLIF(SUM({denominator}) FILTER (WHERE {paired}), 0) END"
        )
    comparable = "is_maintenance AND is_comparable"
    comparable_count = f"COUNT(*) FILTER (WHERE {comparable})"
    comparable_duration_count = f"COUNT(duration_seconds) FILTER (WHERE {comparable})"
    result["comparable_runs"] = comparable_count
    result["comparable_duration_measured_runs"] = comparable_duration_count
    result["comparable_duration_seconds"] = (
        f"CASE WHEN {comparable_count} > 0 AND {comparable_duration_count} = {comparable_count} "
        f"THEN SUM(duration_seconds) FILTER (WHERE {comparable}) END"
    )
    result["no_op_rate"] = f"100.0 * COUNT(*) FILTER (WHERE is_maintenance AND is_no_op) / NULLIF({comparable_count}, 0)"
    result["no_op_runtime_share"] = (
        f"CASE WHEN {comparable_count} > 0 AND {comparable_duration_count} = {comparable_count} "
        "THEN 100.0 * COALESCE(SUM(duration_seconds) FILTER (WHERE is_maintenance AND is_no_op), 0) / "
        f"NULLIF(SUM(duration_seconds) FILTER (WHERE {comparable}), 0) END"
    )
    for metric, column in (("bytes", "destination_bytes_removed"), ("files", "destination_files_removed")):
        matched = f"is_maintenance AND normalized_status = 'succeeded' AND {column} >= 0 AND duration_seconds >= 0"
        count = f"COUNT(*) FILTER (WHERE {matched})"
        complete = f"{observed_version} OR {count} = COUNT(*) FILTER (WHERE is_maintenance AND normalized_status = 'succeeded')"
        result[f"efficiency_{metric}_paired_runs"] = count
        result[f"efficiency_{metric}_removed"] = f"CASE WHEN ({complete}) AND {count} > 0 THEN SUM({column}) FILTER (WHERE {matched}) END"
        result[f"efficiency_{metric}_duration_seconds"] = f"CASE WHEN ({complete}) AND {count} > 0 THEN SUM(duration_seconds) FILTER (WHERE {matched}) END"
    return result


_EVIDENCE_AGGREGATES = _evidence_aggregates()
MAINTENANCE_EVIDENCE_FIELDS = tuple(_EVIDENCE_AGGREGATES)
_EVIDENCE_SELECT = ",\n    ".join(f"{value} AS {key}" for key, value in _EVIDENCE_AGGREGATES.items())
_SUMMARY_EVIDENCE_SELECT = ",\n  ".join(
    f"(SELECT {value} FROM classified) AS {key}" for key, value in _EVIDENCE_AGGREGATES.items()
)
_POSITIVE_EFFECT = " OR ".join(
    f"{column} > 0" for column in (
        "destination_files_added", "destination_files_removed", "destination_bytes_added",
        "destination_bytes_removed", "destination_bytes_saved", "destination_rows_written",
        "destination_rows_inserted", "destination_rows_updated", "destination_rows_deleted",
    )
)

_DERIVED_CTES = f"""
, classified_base AS (
  SELECT *,
         {_TARGET} AS target, {_TARGET_DISPLAY} AS target_display,
         {_IS_MAINTENANCE} AS is_maintenance, {_IS_ACTIVE} AS is_active
  FROM filtered_dataflows
), maintenance_parsed AS (
  SELECT classified_base.*,
         attempts[-1].destination.operation_details AS operation_details
  FROM classified_base
  WHERE is_maintenance
), maintenance_effects AS (
  SELECT maintenance_parsed.*, {_NO_OP} AS is_no_op
  FROM maintenance_parsed
), maintenance AS (
  SELECT maintenance_effects.*,
         COALESCE(is_no_op OR (normalized_status = 'succeeded' AND ({_POSITIVE_EFFECT})), FALSE) AS is_comparable
  FROM maintenance_effects
), active_only AS (
  SELECT classified_base.*, NULL::JSON AS operation_details, FALSE AS is_no_op, FALSE AS is_comparable
  FROM classified_base
  WHERE is_active AND NOT is_maintenance
), relevant AS (
  SELECT * FROM maintenance
  UNION ALL
  SELECT * FROM active_only
), classified AS (
  SELECT * FROM relevant
), target_rollup AS (
  SELECT target,
    ARG_MAX(target_display, event_time) AS target_display,
    BOOL_OR(is_active) AS active_lakehouse_table,
    BOOL_OR(is_maintenance) AS maintained_table,
    COUNT(*) FILTER (WHERE is_maintenance) AS run_count,
    COUNT(*) FILTER (WHERE is_maintenance AND normalized_status = 'succeeded') AS succeeded,
    COUNT(*) FILTER (WHERE is_maintenance AND normalized_status = 'failed') AS failed,
    COUNT(*) FILTER (WHERE is_maintenance AND normalized_status = 'skipped') AS skipped,
    COUNT(*) FILTER (WHERE is_maintenance AND normalized_status = 'running') AS running,
    COUNT(*) FILTER (WHERE is_maintenance AND normalized_status = 'pending') AS pending,
    COUNT(*) FILTER (WHERE is_maintenance AND normalized_status NOT IN ('succeeded','failed','skipped','running','pending')) AS unknown,
    ARG_MAX(normalized_status, event_time) FILTER (WHERE is_maintenance) AS latest_status_aggregate,
    MAX(event_time) FILTER (WHERE is_maintenance) AS latest_maintenance_time,
    MAX(event_time) FILTER (WHERE is_maintenance AND normalized_status = 'succeeded') AS latest_successful_maintenance_time,
    MAX(event_time) FILTER (WHERE is_active) AS latest_etl_write_time,
    {_maintenance_metric_total('destination_files_removed', 'TRUE')} AS files_removed,
    {_maintenance_metric_total('destination_bytes_removed', 'TRUE')} AS bytes_reclaimed,
    {_maintenance_metric_total('destination_bytes_saved', 'TRUE')} AS bytes_saved,
    {_EVIDENCE_SELECT},
    CASE WHEN COUNT(*) FILTER (WHERE is_maintenance) = 0 THEN 0
         WHEN COUNT(duration_seconds) FILTER (WHERE is_maintenance)
              = COUNT(*) FILTER (WHERE is_maintenance)
         THEN SUM(duration_seconds) FILTER (WHERE is_maintenance)
         ELSE NULL END AS duration_seconds,
    CASE WHEN COUNT(destination_bytes_removed) FILTER (WHERE is_maintenance) = 0 THEN NULL
         WHEN COUNT(duration_seconds) FILTER (WHERE is_maintenance AND destination_bytes_removed IS NOT NULL)
              = COUNT(destination_bytes_removed) FILTER (WHERE is_maintenance)
         THEN SUM(duration_seconds) FILTER (WHERE is_maintenance AND destination_bytes_removed IS NOT NULL)
         ELSE NULL END AS bytes_reclaimed_duration_seconds,
    COUNT(*) FILTER (WHERE is_maintenance AND is_no_op) AS no_op_runs,
    CASE WHEN COUNT(*) FILTER (WHERE is_maintenance AND is_no_op) = 0 THEN 0
         WHEN COUNT(duration_seconds) FILTER (WHERE is_maintenance AND is_no_op)
              = COUNT(*) FILTER (WHERE is_maintenance AND is_no_op)
         THEN SUM(duration_seconds) FILTER (WHERE is_maintenance AND is_no_op)
         ELSE NULL END AS no_op_duration_seconds,
    ARG_MAX(destination_table, event_time) AS destination_table,
    ARG_MAX(destination_full_table, event_time) AS destination_full_table,
    ARG_MAX(destination_path, event_time) AS destination_path,
    ARG_MAX(destination_name, event_time) AS destination_name,
    ARG_MAX(destination_connection_type, event_time) AS destination_connection_type,
    COALESCE(ARG_MAX(NULLIF(destination_format, ''), event_time), 'unknown') AS destination_format,
    COUNT(*) FILTER (WHERE is_active) AS upstream_run_count
  FROM relevant GROUP BY target
), maintenance_latest AS (
  SELECT * FROM relevant
  WHERE is_maintenance
  QUALIFY ROW_NUMBER() OVER (PARTITION BY target ORDER BY {_LATEST_RUN_ORDER}) = 1
), active_latest AS (
  SELECT * FROM relevant
  WHERE is_active
  QUALIFY ROW_NUMBER() OVER (PARTITION BY target ORDER BY {_LATEST_RUN_ORDER}) = 1
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
    CASE WHEN EXISTS (SELECT 1 FROM all_log_versions WHERE log_schema_version >= 7)
         THEN SUM(source_rows_read) FILTER (WHERE is_active AND source_rows_read IS NOT NULL)
         WHEN COUNT(*) FILTER (WHERE is_active) = 0 THEN NULL
         WHEN COUNT(source_rows_read) FILTER (WHERE is_active) = COUNT(*) FILTER (WHERE is_active)
         THEN SUM(source_rows_read) FILTER (WHERE is_active) ELSE NULL END AS rows_read
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
  SELECT r.*,
    COALESCE(ml.normalized_status, r.latest_status_aggregate) AS latest_status,
    ml.log_session_id AS latest_log_session_id,
    ml.dataflow_run_id AS latest_dataflow_run_id,
    ml.message AS latest_message,
    ml.error_message AS latest_error_message,
    ml.operation_details AS latest_operation_details,
    NULL::VARCHAR AS latest_raw_record_json,
    ml._source_id AS latest_source_id,
    ml._file_uri AS latest_file_uri,
    ml._file_kind AS latest_file_kind,
    ml._record_line AS latest_record_line,
    CASE WHEN latest_etl_write_time IS NULL OR latest_successful_maintenance_time IS NULL THEN NULL
                 ELSE GREATEST(0, epoch(latest_etl_write_time - latest_successful_maintenance_time)) END AS maintenance_lag_seconds,
    CASE WHEN NOT maintained_table AND active_lakehouse_table THEN 'warning'
         WHEN latest_status = 'failed' THEN 'has_issues'
         WHEN latest_status IN ('running','pending','skipped') THEN 'warning'
         WHEN maintained_table AND COALESCE(latest_status, 'unknown') <> 'succeeded' THEN 'no_evidence'
         WHEN maintenance_lag_seconds > 7 * 86400 THEN 'warning'
         WHEN maintained_table THEN 'healthy' ELSE 'no_evidence' END AS table_health,
    CASE WHEN NOT maintained_table AND active_lakehouse_table THEN 'Missing maintenance coverage'
         WHEN latest_status = 'failed' THEN 'Latest maintenance failed'
         WHEN latest_status IN ('running','pending') THEN CONCAT('Latest maintenance is ', latest_status)
         WHEN latest_status = 'skipped' THEN 'Latest maintenance skipped'
         WHEN maintained_table AND COALESCE(latest_status, 'unknown') <> 'succeeded' THEN 'Latest maintenance status unknown'
         WHEN maintenance_lag_seconds > 7 * 86400 THEN 'ETL–maintenance gap exceeds 7 days'
         WHEN maintained_table THEN 'Latest maintenance succeeded' ELSE 'No maintenance evidence' END AS attention_reason,
    CASE WHEN latest_status = 'failed' THEN 100 WHEN latest_status IN ('running','pending') THEN 90
         WHEN NOT maintained_table AND active_lakehouse_table THEN 80 WHEN latest_status = 'skipped' THEN 70
         WHEN maintenance_lag_seconds > 7 * 86400 THEN 60
         WHEN NOT maintained_table OR COALESCE(latest_status, 'unknown') <> 'succeeded' THEN 10 ELSE 0 END AS attention_priority
  FROM target_rollup r
  LEFT JOIN maintenance_latest ml USING (target)
), maintenance_thresholds AS (
  SELECT {discrete_percentile('duration_seconds', 0.95, 'is_maintenance AND duration_seconds > 0')} AS duration_p95
  FROM classified
)
"""

_REGISTRY_SQL = f"""
SELECT target, target_display,
       target AS table, destination_table, destination_full_table, destination_path,
       destination_name, destination_name AS destination_connection_name,
       destination_connection_type, destination_format AS format, destination_format,
       active_lakehouse_table, maintained_table, run_count, succeeded, failed, skipped,
       running, pending, unknown, COALESCE(latest_status, 'missing') AS latest_status,
       COALESCE(latest_status, 'missing') AS status, latest_maintenance_time, latest_etl_write_time,
       latest_successful_maintenance_time,
       latest_log_session_id AS log_session_id,
       latest_dataflow_run_id AS dataflow_run_id,
       latest_message, latest_error_message,
       latest_operation_details,
       NULL::VARCHAR AS raw_record_json,
       NULL::VARCHAR AS latest_raw_record_json,
       latest_source_id, latest_file_uri, latest_file_kind, latest_record_line,
       maintenance_lag_seconds, maintenance_lag_seconds > 7 * 86400 AS maintenance_lag_warning,
       7 AS maintenance_lag_warning_days, files_removed,
       bytes_reclaimed AS bytes_removed, bytes_reclaimed,
       bytes_saved, duration_seconds,
       {', '.join(_EVIDENCE_AGGREGATES)},
       no_op_runs, no_op_duration_seconds,
       table_health, attention_reason, attention_priority,
       COALESCE(u.upstream_dataflows, []) AS upstream_dataflows,
       upstream_run_count
FROM registry r
LEFT JOIN upstream_by_target u USING (target)
ORDER BY attention_priority DESC, bytes_reclaimed DESC NULLS LAST, files_removed DESC NULLS LAST,
         COALESCE(latest_maintenance_time, latest_etl_write_time) DESC NULLS LAST, target
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
  (SELECT COUNT(DISTINCT {known_dimension_sql('engine_name')}) FROM filtered_dataflows) AS active_engines,
  (SELECT COUNT(DISTINCT {known_dimension_sql('metadata_provider_name')}) FROM filtered_dataflows) AS active_metadata_providers,
  (SELECT COUNT(*) FROM classified WHERE is_maintenance) AS total_maintenance_runs,
  (SELECT COUNT(*) FROM classified WHERE is_maintenance AND normalized_status = 'succeeded') AS succeeded_ops,
  (SELECT COUNT(*) FROM classified WHERE is_maintenance AND normalized_status = 'failed') AS failed_ops,
  (SELECT COUNT(*) FROM classified WHERE is_maintenance AND normalized_status = 'skipped') AS skipped_ops,
  (SELECT COUNT(*) FROM classified WHERE is_maintenance AND normalized_status = 'running') AS running_ops,
  (SELECT COUNT(*) FROM classified WHERE is_maintenance AND normalized_status = 'pending') AS pending_ops,
  (SELECT {_maintenance_metric_total('destination_files_removed', 'TRUE')}
   FROM classified WHERE is_maintenance) AS files_removed,
  (SELECT {_maintenance_metric_total('destination_bytes_removed', 'TRUE')}
   FROM classified WHERE is_maintenance) AS bytes_reclaimed,
  (SELECT {_maintenance_metric_total('destination_bytes_saved', 'TRUE')}
   FROM classified WHERE is_maintenance) AS bytes_saved,
  {_SUMMARY_EVIDENCE_SELECT},
  (SELECT CASE WHEN COUNT(*) = 0 THEN 0
               WHEN COUNT(duration_seconds) = COUNT(*) THEN SUM(duration_seconds)
               ELSE NULL END FROM classified WHERE is_maintenance) AS duration_seconds,
  (SELECT CASE WHEN COUNT(*) = 0 THEN 0
               WHEN COUNT(duration_seconds) = COUNT(*) THEN SUM(duration_seconds)
               ELSE NULL END FROM classified
   WHERE is_maintenance AND normalized_status = 'succeeded') AS succeeded_duration_seconds,
  (SELECT COUNT(*) FROM classified WHERE is_maintenance AND is_no_op) AS no_op_runs,
  (SELECT COUNT(*) FROM classified, maintenance_thresholds
    WHERE is_maintenance AND duration_p95 > 0 AND duration_seconds >= duration_p95) AS high_duration_runs,
  COUNT(*) FILTER (WHERE no_op_runs > 0) AS no_op_tables,
  CASE WHEN COUNT(no_op_duration_seconds) = COUNT(*) THEN SUM(no_op_duration_seconds) ELSE NULL END AS no_op_duration_seconds,
  COUNT(*) FILTER (WHERE bytes_reclaimed_observed > 0 OR files_removed_observed > 0) AS tables_with_reclaim,
  COUNT(*) FILTER (WHERE table_health = 'has_issues') AS tables_with_issues,
  COUNT(*) FILTER (WHERE table_health = 'warning') AS tables_with_warnings,
  COUNT(*) FILTER (WHERE table_health = 'healthy') AS healthy_tables,
  COUNT(*) FILTER (WHERE maintained_table AND table_health = 'no_evidence') AS unknown_status_tables,
  COUNT(*) FILTER (WHERE succeeded > 0) AS successful_maintenance_tables,
  COUNT(*) FILTER (WHERE latest_status = 'failed') AS latest_failed_tables,
  COUNT(*) FILTER (WHERE latest_status = 'skipped') AS latest_skipped_tables,
  COUNT(*) FILTER (WHERE latest_status IN ('running','pending')) AS latest_active_tables,
  COUNT(*) FILTER (WHERE maintenance_lag_seconds > 7 * 86400) AS lagged_tables,
  COUNT(*) FILTER (WHERE active_lakehouse_table) AS active_lakehouse_tables,
  COUNT(*) FILTER (WHERE active_lakehouse_table AND maintained_table) AS maintained_tables,
  COUNT(*) FILTER (WHERE active_lakehouse_table AND succeeded > 0) AS successfully_maintained_tables,
  ROUND(100.0 * COUNT(*) FILTER (WHERE active_lakehouse_table AND succeeded > 0) /
    NULLIF(COUNT(*) FILTER (WHERE active_lakehouse_table), 0), 2) AS successful_coverage_rate,
  COUNT(*) FILTER (WHERE active_lakehouse_table AND NOT maintained_table) AS coverage_missing_tables,
  ROUND(100.0 * COUNT(*) FILTER (WHERE active_lakehouse_table AND maintained_table) /
    NULLIF(COUNT(*) FILTER (WHERE active_lakehouse_table), 0), 2) AS coverage_rate
FROM registry
"""

_ATTENTION_SQL = """
SELECT
  0::BIGINT AS coverage_missing_tables,
  0::BIGINT AS lagged_tables,
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

_RECLAIM_TREND_SQL = f"""
SELECT date_trunc(?, timezone(?, event_time)) AS bucket_start,
       {_maintenance_metric_total('destination_bytes_removed', 'TRUE')} AS bytes_reclaimed,
       {_maintenance_metric_total('destination_bytes_saved', 'TRUE')} AS bytes_saved,
       {_maintenance_metric_total('destination_files_removed', 'TRUE')} AS files_removed,
       COUNT(*) AS runs
FROM classified WHERE is_maintenance AND event_time IS NOT NULL
GROUP BY bucket_start ORDER BY bucket_start
"""

_FORMAT_SQL = f"""
SELECT COALESCE(NULLIF(destination_format, ''), 'unknown') AS format,
       {_maintenance_metric_total('destination_files_removed', 'TRUE')} AS files_removed,
       {_maintenance_metric_total('destination_bytes_removed', 'TRUE')} AS bytes_removed,
       COUNT(*) AS count
FROM classified WHERE is_maintenance GROUP BY format ORDER BY format
"""
