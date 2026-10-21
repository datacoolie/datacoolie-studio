from __future__ import annotations

from datacoolie_studio.domains.monitoring.dimensions import known_dimension_sql

from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.read_models.common import (
    AnalyticsContext,
    discrete_percentile,
    nullable_discrete_percentile,
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
    v6_timing_complete_sql,
    v6_attempt_duration_sql,
    timing_input_cte,
    v6_phase_elapsed_sql,
)


_DATAFLOW_COLUMNS = (
    "_source_id", "_ingested_at", "_record_line", "_file_uri", "raw_record_json",
    "log_schema_version", "attempts",
    "job_id", "dataflow_id", "dataflow_run_id", "dataflow_name",
    "stage", "status", "message", "source_message", "transform_message", "destination_message",
    "start_time", "end_time", "duration_seconds", "operation_type",
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

# Narrow native inputs needed when the shared dataflow registry composes the
# Performance facet. Attempts are read only by the timing CTE; they are never
# returned as part of the registry DTO.
PERFORMANCE_REGISTRY_INPUT_COLUMNS = (
    "log_schema_version", "attempts", "dataflow_id", "dataflow_run_id",
    "status", "duration_seconds", "operation_type", "source_rows_read",
    "destination_rows_written", "destination_bytes_added",
    "destination_bytes_removed", "destination_bytes_saved",
    "destination_files_added", "destination_files_removed",
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


def _metric_rate(numerator: str, denominator: str, eligible: str) -> str:
    population = _metric_eligible(eligible)
    numerator_measured = _metric_measured(numerator, eligible)
    denominator_measured = (
        f"COUNT({denominator}) FILTER (WHERE {eligible} AND {numerator} IS NOT NULL)"
    )
    numerator_observed = _metric_observed(numerator, eligible)
    denominator_observed = (
        f"SUM({denominator}) FILTER (WHERE {eligible} "
        f"AND {numerator} IS NOT NULL AND {denominator} IS NOT NULL)"
    )
    rate = f"ROUND({numerator_observed} / NULLIF({denominator_observed}, 0), 3)"
    return (
        f"CASE WHEN {population} = 0 THEN NULL "
        f"WHEN EXISTS (SELECT 1 FROM all_log_versions WHERE log_schema_version >= 7) "
        f"THEN CASE WHEN {numerator_measured} = 0 OR {denominator_measured} <> {numerator_measured} THEN NULL ELSE {rate} END "
        f"WHEN {numerator_measured} = {population} AND {denominator_measured} = {population} THEN {rate} END"
    )


def _registry_workload_aggregates(unit: str, quantity: str, eligible: str) -> str:
    """Descriptive totals and positive-duration pairs, separate from strict rates.

    The caller supplies only Studio-owned expressions. Both quantity components
    must be observed; SQL null propagation preserves missing measurements.
    """
    prefix = f"performance_processed_{unit}"
    paired = f"({eligible}) AND ({quantity}) IS NOT NULL AND duration_seconds > 0"
    paired_quantity = f"SUM({quantity}) FILTER (WHERE {paired})"
    paired_seconds = f"SUM(duration_seconds) FILTER (WHERE {paired})"
    return f"""
         {_metric_total(quantity, eligible)} AS {prefix},
         SUM({quantity}) FILTER (WHERE {eligible}) AS performance_observed_processed_{unit},
         {_metric_measured(quantity, eligible)} AS {prefix}_measured,
         {_metric_eligible(eligible)} AS {prefix}_eligible,
         COUNT(*) FILTER (WHERE {paired}) AS {prefix}_paired_runs,
         {paired_quantity} AS {prefix}_paired_quantity,
         {paired_seconds} AS {prefix}_paired_seconds,
         ROUND({paired_quantity} / NULLIF({paired_seconds}, 0), 3) AS {prefix}_per_second"""


# Only absence of a Performance population becomes zero; measured values stay
# nullable. The registry's LEFT JOIN still retains all-skipped entities.
PERFORMANCE_REGISTRY_COUNT_FIELDS = (
    "performance_timing_eligible", "performance_timing_measured",
    "performance_duration_measured", "performance_unknown_operation_runs",
    "performance_candidate_count", "performance_slow_small_workload_count",
    "performance_slow_small_maintenance_count", "performance_high_overhead_count",
    "performance_phase_skew_count",
    *(f"performance_processed_{unit}_{suffix}"
      for unit in ("rows", "bytes")
      for suffix in ("measured", "eligible", "paired_runs")),
)


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
        "end_time": "end_time", "status": "status", "message": "COALESCE(message, error_message)",
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
            f"ORDER BY {order_column} {direction} NULLS LAST, duration_seconds DESC, event_time DESC NULLS LAST, _source_id ASC, log_session_id ASC NULLS LAST, dataflow_run_id ASC "
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


_ROWS_PROCESSED = "(source_rows_read + destination_rows_written)"
_MAINT_BYTES = "(destination_bytes_added + destination_bytes_removed + destination_bytes_saved)"
_MAINT_FILES = "(destination_files_added + destination_files_removed)"
_TIMING_COMPLETE = v6_timing_complete_sql()
_SOURCE_ELAPSED = v6_phase_elapsed_sql("source")
_TRANSFORM_ELAPSED = v6_phase_elapsed_sql("transform")
_OVERHEAD = f"CASE WHEN {_TIMING_COMPLETE} THEN GREATEST(0, {v6_attempt_duration_sql()} - ({_SOURCE_ELAPSED}) - ({_TRANSFORM_ELAPSED}) - __v6_destination_duration) END"

_DERIVED_CTES = f"""
{timing_input_cte()}
, executable AS (
  SELECT *, COALESCE(NULLIF(operation_type, ''), 'unknown') AS operation_group,
         {_ROWS_PROCESSED} AS rows_processed,
         {_MAINT_BYTES} AS maintenance_bytes_processed,
         {_MAINT_FILES} AS maintenance_files_processed,
         CASE WHEN {_TIMING_COMPLETE} THEN {_SOURCE_ELAPSED} END AS source_phase,
         CASE WHEN {_TIMING_COMPLETE} THEN {_TRANSFORM_ELAPSED} END AS transform_phase,
         CASE WHEN {_TIMING_COMPLETE} THEN __v6_destination_duration END AS destination_phase,
         {_OVERHEAD} AS overhead_phase
  FROM timed_dataflows
  WHERE normalized_status IN ('succeeded','failed')
), thresholds AS (
  SELECT operation_group,
         {nullable_discrete_percentile('duration_seconds', 0.75)} AS duration_p75,
         {nullable_discrete_percentile('duration_seconds', 0.95)} AS duration_p95,
         {discrete_percentile('rows_processed', 0.50, 'rows_processed > 0')} AS rows_p50,
         {discrete_percentile('maintenance_bytes_processed', 0.50, 'maintenance_bytes_processed > 0')} AS maintenance_bytes_p50,
         {discrete_percentile('maintenance_files_processed', 0.50, 'maintenance_files_processed > 0')} AS maintenance_files_p50
  FROM executable GROUP BY operation_group
), measured AS (
  SELECT e.*, t.* EXCLUDE (operation_group),
         source_phase + transform_phase + destination_phase + overhead_phase AS phase_total,
         GREATEST(source_phase, transform_phase, destination_phase, overhead_phase) AS largest_phase_duration,
         CASE WHEN source_phase + transform_phase + destination_phase + overhead_phase <= 0 THEN 'unknown'
              WHEN GREATEST(source_phase, transform_phase, destination_phase, overhead_phase) = source_phase THEN 'source'
              WHEN GREATEST(source_phase, transform_phase, destination_phase, overhead_phase) = transform_phase THEN 'transform'
              WHEN GREATEST(source_phase, transform_phase, destination_phase, overhead_phase) = destination_phase THEN 'destination'
              WHEN GREATEST(source_phase, transform_phase, destination_phase, overhead_phase) = overhead_phase THEN 'overhead'
              ELSE 'unknown' END AS performance_bottleneck_phase
  FROM executable e JOIN thresholds t USING (operation_group)
), classified AS (
  SELECT *,
    phase_total IS NOT NULL AND operation_group <> 'maintenance' AND rows_p50 > 0 AND rows_processed > 0 AND rows_processed <= rows_p50 AND duration_p95 > 0 AND duration_seconds >= duration_p95 AS slow_small_workload,
    phase_total IS NOT NULL AND operation_group = 'maintenance' AND duration_p95 > 0 AND duration_seconds >= duration_p95 AND
      ((maintenance_bytes_p50 > 0 AND maintenance_bytes_processed > 0 AND maintenance_bytes_processed <= maintenance_bytes_p50) OR
       (maintenance_bytes_processed <= 0 AND maintenance_files_p50 > 0 AND maintenance_files_processed > 0 AND maintenance_files_processed <= maintenance_files_p50)) AS slow_small_maintenance,
    phase_total IS NOT NULL AND duration_p75 > 0 AND duration_seconds >= duration_p75 AND overhead_phase / NULLIF(phase_total, 0) >= 0.20 AS high_overhead,
    phase_total IS NOT NULL AND operation_group <> 'maintenance' AND duration_p75 > 0 AND duration_seconds >= duration_p75 AND largest_phase_duration / NULLIF(phase_total, 0) >= 0.90 AS phase_skew
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


def dataflow_registry_performance_ctes() -> str:
    """Return the shared Performance candidates and entity-level summaries.

    The caller supplies the already filtered canonical ``filtered_dataflows``
    relation and the schema-version relation ``all_log_versions``. Eligibility,
    timing validation, thresholds, and candidate classification stay owned by
    this module; the registry only composes their results at dataflow grain.
    """

    complete_coverage = "COUNT(*) > 0 AND COUNT(phase_total) = COUNT(*)"
    input_columns = ("normalized_status", *PERFORMANCE_REGISTRY_INPUT_COLUMNS)
    derived_ctes = _DERIVED_CTES.replace(
        "FROM filtered_dataflows fd", "FROM registry_performance_inputs fd",
    )
    return f"""
, registry_performance_inputs AS (
  SELECT {', '.join(input_columns)}
  FROM filtered_dataflows
)
{derived_ctes}, registry_performance_by_dataflow AS (
  SELECT dataflow_id,
         COUNT(*) AS performance_timing_eligible,
         COUNT(phase_total) AS performance_timing_measured,
         CASE WHEN {complete_coverage} THEN SUM(source_phase) END AS performance_source_duration_seconds,
         CASE WHEN {complete_coverage} THEN SUM(transform_phase) END AS performance_transform_duration_seconds,
         CASE WHEN {complete_coverage} THEN SUM(destination_phase) END AS performance_destination_duration_seconds,
         CASE WHEN {complete_coverage} THEN SUM(overhead_phase) END AS performance_overhead_duration_seconds,
         CASE WHEN {complete_coverage} THEN AVG(source_phase) END AS performance_source_avg_duration_seconds,
         CASE WHEN {complete_coverage} THEN AVG(transform_phase) END AS performance_transform_avg_duration_seconds,
         CASE WHEN {complete_coverage} THEN AVG(destination_phase) END AS performance_destination_avg_duration_seconds,
         CASE WHEN {complete_coverage} THEN AVG(overhead_phase) END AS performance_overhead_avg_duration_seconds,
         CASE WHEN {complete_coverage} THEN
           CASE WHEN SUM(phase_total) <= 0 THEN 'unknown'
                WHEN SUM(source_phase) >= GREATEST(SUM(transform_phase), SUM(destination_phase), SUM(overhead_phase)) THEN 'source'
                WHEN SUM(transform_phase) >= GREATEST(SUM(source_phase), SUM(destination_phase), SUM(overhead_phase)) THEN 'transform'
                WHEN SUM(destination_phase) >= GREATEST(SUM(source_phase), SUM(transform_phase), SUM(overhead_phase)) THEN 'destination'
                WHEN SUM(overhead_phase) > 0 THEN 'overhead' ELSE 'unknown' END
         END AS performance_bottleneck_phase,
         CASE WHEN {complete_coverage} THEN ROUND(100.0 * SUM(source_phase) / NULLIF(SUM(phase_total), 0), 2) END AS performance_source_contribution,
         CASE WHEN {complete_coverage} THEN ROUND(100.0 * SUM(transform_phase) / NULLIF(SUM(phase_total), 0), 2) END AS performance_transform_contribution,
         CASE WHEN {complete_coverage} THEN ROUND(100.0 * SUM(destination_phase) / NULLIF(SUM(phase_total), 0), 2) END AS performance_destination_contribution,
         CASE WHEN {complete_coverage} THEN ROUND(100.0 * SUM(overhead_phase) / NULLIF(SUM(phase_total), 0), 2) END AS performance_overhead_contribution,
         ROUND(AVG(duration_seconds), 3) AS performance_avg_duration_seconds,
         ROUND(SUM(duration_seconds), 3) AS performance_total_run_time_seconds,
         COUNT(duration_seconds) AS performance_duration_measured,
         MIN(duration_seconds) AS performance_min_duration_seconds,
         {nullable_discrete_percentile('duration_seconds', 0.25)} AS performance_q1_duration_seconds,
         {nullable_discrete_percentile('duration_seconds', 0.50)} AS performance_p50_duration_seconds,
         {nullable_discrete_percentile('duration_seconds', 0.75)} AS performance_q3_duration_seconds,
         {nullable_discrete_percentile('duration_seconds', 0.95)} AS performance_p95_duration_seconds,
         MAX(duration_seconds) AS performance_max_duration_seconds,
         {_metric_rate('source_rows_read', 'duration_seconds', 'TRUE')} AS performance_rows_read_per_second,
         {_registry_workload_aggregates('rows', 'rows_processed', "operation_group <> 'maintenance'")},
         {_registry_workload_aggregates('bytes', '(destination_bytes_added + destination_bytes_removed)', "operation_group = 'maintenance'")},
         COUNT(*) FILTER (WHERE operation_group = 'unknown') AS performance_unknown_operation_runs,
         COUNT(*) FILTER (WHERE performance_candidate_code IS NOT NULL) AS performance_candidate_count,
         COUNT(*) FILTER (WHERE slow_small_workload) AS performance_slow_small_workload_count,
         COUNT(*) FILTER (WHERE slow_small_maintenance) AS performance_slow_small_maintenance_count,
         COUNT(*) FILTER (WHERE high_overhead) AS performance_high_overhead_count,
         COUNT(*) FILTER (WHERE phase_skew) AS performance_phase_skew_count,
         LIST(DISTINCT performance_candidate_reason ORDER BY performance_candidate_reason)
           FILTER (WHERE performance_candidate_reason IS NOT NULL) AS performance_candidate_reasons
  FROM candidates
  WHERE dataflow_id IS NOT NULL
  GROUP BY dataflow_id
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
  (SELECT COUNT(DISTINCT {known_dimension_sql('engine_name')}) FROM filtered_dataflows) AS active_engines,
  (SELECT COUNT(DISTINCT {known_dimension_sql('metadata_provider_name')}) FROM filtered_dataflows) AS active_metadata_providers,
  COUNT(*) AS run_count,
  COUNT(*) FILTER (WHERE operation_group <> 'maintenance') AS workload_efficiency_pipeline_eligible,
  COUNT(*) FILTER (
    WHERE operation_group <> 'maintenance'
      AND duration_seconds IS NOT NULL AND rows_processed IS NOT NULL
  ) AS workload_efficiency_pipeline_measured,
  COUNT(*) FILTER (WHERE operation_group = 'maintenance') AS workload_efficiency_maintenance_eligible,
  COUNT(*) FILTER (
    WHERE operation_group = 'maintenance'
      AND duration_seconds IS NOT NULL AND maintenance_bytes_processed IS NOT NULL
  ) AS workload_efficiency_maintenance_measured,
  ROUND(SUM(duration_seconds), 3) AS total_run_time_seconds,
  COUNT(duration_seconds) AS duration_runs_measured,
  COUNT(*) AS duration_runs_eligible,
  ROUND(AVG(duration_seconds), 3) AS avg_duration_seconds,
  {nullable_discrete_percentile('duration_seconds', 0.50)} AS p50_duration_seconds,
  {nullable_discrete_percentile('duration_seconds', 0.75)} AS p75_duration_seconds,
  {nullable_discrete_percentile('duration_seconds', 0.95)} AS p95_duration_seconds,
  {nullable_discrete_percentile('duration_seconds', 0.99)} AS p99_duration_seconds,
  MAX(duration_seconds) AS max_duration_seconds,
  MAX(duration_seconds) AS slowest_run_duration_seconds,
  ARG_MAX(dataflow_name, duration_seconds) AS slowest_run_dataflow_name,
  ARG_MAX(dataflow_id, duration_seconds) AS slowest_run_dataflow_id,
  ARG_MAX(dataflow_run_id, duration_seconds) AS slowest_run_dataflow_run_id,
  ARG_MAX(job_id, duration_seconds) AS slowest_run_job_id,
  ARG_MAX(start_time, duration_seconds) AS slowest_run_start_time,
  ARG_MAX(end_time, duration_seconds) AS slowest_run_end_time,
  ARG_MAX(stage, duration_seconds) AS slowest_run_stage,
  ARG_MAX(operation_group, duration_seconds) AS slowest_run_operation_type,
  ARG_MAX(normalized_status, duration_seconds) AS slowest_run_status,
  CASE WHEN COUNT(*) > 0 AND COUNT(phase_total) = COUNT(*) THEN
       CASE WHEN SUM(phase_total) <= 0 THEN 'unknown'
            WHEN SUM(source_phase) >= GREATEST(SUM(transform_phase), SUM(destination_phase), SUM(overhead_phase)) THEN 'source'
            WHEN SUM(transform_phase) >= GREATEST(SUM(source_phase), SUM(destination_phase), SUM(overhead_phase)) THEN 'transform'
            WHEN SUM(destination_phase) >= GREATEST(SUM(source_phase), SUM(transform_phase), SUM(overhead_phase)) THEN 'destination'
            WHEN SUM(overhead_phase) > 0 THEN 'overhead' ELSE 'unknown' END
       ELSE 'unknown' END AS bottleneck_phase,
  CASE WHEN COUNT(phase_total) = COUNT(*) THEN ROUND(100.0 * SUM(source_phase) / NULLIF(SUM(phase_total), 0), 2) END AS source_duration_percent,
  CASE WHEN COUNT(phase_total) = COUNT(*) THEN ROUND(100.0 * SUM(transform_phase) / NULLIF(SUM(phase_total), 0), 2) END AS transform_duration_percent,
  CASE WHEN COUNT(phase_total) = COUNT(*) THEN ROUND(100.0 * SUM(destination_phase) / NULLIF(SUM(phase_total), 0), 2) END AS destination_duration_percent,
  CASE WHEN COUNT(phase_total) = COUNT(*) THEN ROUND(100.0 * SUM(overhead_phase) / NULLIF(SUM(phase_total), 0), 2) END AS overhead_duration_percent,
  COUNT(*) AS phase_runs_eligible,
  COUNT(phase_total) AS phase_runs_measured,
  ROUND(100.0 * COUNT(phase_total) / NULLIF(COUNT(*), 0), 2) AS phase_coverage_percent,
  {_metric_rate('source_rows_read', 'duration_seconds', 'TRUE')} AS rows_read_per_second,
  {_metric_total('source_rows_read', 'TRUE')} AS total_rows_read,
  {_metric_observed('source_rows_read', 'TRUE')} AS observed_rows_read,
  {_metric_measured('source_rows_read', 'TRUE')} AS rows_read_measured,
  {_metric_eligible('TRUE')} AS rows_read_eligible,
  {_metric_total('destination_rows_written', 'TRUE')} AS total_rows_written,
  {_metric_observed('destination_rows_written', 'TRUE')} AS observed_rows_written,
  {_metric_measured('destination_rows_written', 'TRUE')} AS rows_written_measured,
  {_metric_eligible('TRUE')} AS rows_written_eligible,
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
WITH measurable AS (
  SELECT *,
         CASE WHEN operation_group = 'maintenance' THEN 'maintenance' ELSE 'pipeline' END AS efficiency_scope,
         CASE WHEN performance_candidate_priority > 0 THEN 'signal' ELSE 'baseline' END AS efficiency_class
  FROM candidates
  WHERE duration_seconds IS NOT NULL
    AND CASE
      WHEN operation_group = 'maintenance' THEN maintenance_bytes_processed IS NOT NULL
      ELSE rows_processed IS NOT NULL
    END
), stratified AS (
  SELECT *,
         ROW_NUMBER() OVER (
           PARTITION BY efficiency_scope, efficiency_class, operation_group,
                        COALESCE(NULLIF(stage, ''), 'unknown'),
                        COALESCE(performance_bottleneck_phase, 'unknown'),
                        CASE WHEN efficiency_class = 'signal'
                             THEN COALESCE(performance_candidate_reason, 'Performance signal') END,
                        COALESCE(dataflow_id, ''), date_trunc('day', event_time)
           ORDER BY CASE WHEN efficiency_class = 'signal' THEN performance_candidate_priority END DESC NULLS LAST,
                    CASE WHEN efficiency_class = 'signal' THEN duration_seconds END DESC NULLS LAST,
                    hash(COALESCE(dataflow_run_id, '')), dataflow_run_id ASC NULLS LAST,
                    _source_id ASC, _record_line ASC NULLS LAST, _file_uri ASC NULLS LAST
         ) AS efficiency_stratum_rank
  FROM measurable
), ranked AS (
  SELECT *,
         ROW_NUMBER() OVER (
           PARTITION BY efficiency_scope, efficiency_class
           ORDER BY efficiency_stratum_rank,
                    CASE WHEN efficiency_class = 'signal' THEN performance_candidate_priority END DESC NULLS LAST,
                    CASE WHEN efficiency_class = 'signal' THEN duration_seconds END DESC NULLS LAST,
                    hash(COALESCE(dataflow_run_id, '')), dataflow_run_id ASC NULLS LAST,
                    _source_id ASC, _record_line ASC NULLS LAST, _file_uri ASC NULLS LAST
         ) AS efficiency_class_rank,
         ROW_NUMBER() OVER (
           PARTITION BY efficiency_scope
           ORDER BY performance_candidate_priority DESC, duration_seconds DESC,
                    event_time DESC NULLS LAST, dataflow_run_id ASC NULLS LAST,
                    _source_id ASC, _record_line ASC NULLS LAST, _file_uri ASC NULLS LAST
         ) AS efficiency_scope_rank
  FROM stratified
), preferred AS (
  -- Each scope gets its own 200-point budget. Prefer stratified signals while
  -- reserving up to 50 rows for a representative non-candidate baseline.
  SELECT * FROM ranked
  WHERE (efficiency_class = 'signal' AND efficiency_class_rank <= 150)
     OR (efficiency_class = 'baseline' AND efficiency_class_rank <= 50)
), selected AS (
  SELECT *, 0 AS efficiency_selection_group FROM preferred
  UNION ALL
  SELECT ranked.*, 1 AS efficiency_selection_group
  FROM ranked
  WHERE NOT EXISTS (
    SELECT 1 FROM preferred
    WHERE preferred.efficiency_scope = ranked.efficiency_scope
      AND preferred.efficiency_scope_rank = ranked.efficiency_scope_rank
  )
), bounded AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY efficiency_scope
    ORDER BY efficiency_selection_group, efficiency_scope_rank
  ) AS efficiency_output_rank
  FROM selected
)
SELECT dataflow_run_id,
       COALESCE(NULLIF(dataflow_name, ''), NULLIF(dataflow_id, ''), 'unknown') AS dataflow_name,
       COALESCE(NULLIF(stage, ''), 'unknown') AS stage, operation_group AS operation_type,
       duration_seconds, rows_processed, maintenance_bytes_processed, maintenance_files_processed,
       CASE WHEN source_rows_read IS NOT NULL AND duration_seconds > 0 THEN source_rows_read / duration_seconds END AS rows_read_per_second,
       destination_bytes_added, destination_bytes_removed,
       performance_bottleneck_phase, performance_candidate_reason, performance_candidate_priority
FROM bounded
WHERE efficiency_output_rank <= 200
ORDER BY efficiency_scope, efficiency_selection_group, efficiency_scope_rank
"""

_PROFILE_SQL = f"""
WITH profile_latest AS (
  SELECT *
  FROM candidates
  WHERE dataflow_id IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY _source_id, dataflow_id
    ORDER BY event_time DESC NULLS LAST, _ingested_at DESC NULLS LAST,
             _record_line DESC NULLS LAST, _file_uri DESC NULLS LAST,
             log_session_id DESC NULLS LAST, dataflow_run_id DESC NULLS LAST
  ) = 1
), profile_aggregate AS (
  SELECT _source_id, dataflow_id,
         MODE(operation_group) AS operation_type, COUNT(*) AS run_count,
         ROUND(AVG(duration_seconds), 3) AS avg_duration_seconds,
         {nullable_discrete_percentile('duration_seconds', 0.50)} AS p50_duration_seconds,
         {nullable_discrete_percentile('duration_seconds', 0.95)} AS p95_duration_seconds,
         MAX(duration_seconds) AS max_duration_seconds,
         MODE(performance_bottleneck_phase) AS performance_bottleneck_phase,
         COUNT(*) FILTER (WHERE performance_candidate_code IS NOT NULL) AS candidate_count
  FROM candidates
  WHERE dataflow_id IS NOT NULL
  GROUP BY _source_id, dataflow_id
)
SELECT a._source_id, l.log_session_id, l.log_session_id AS latest_log_session_id,
       a.dataflow_id,
       COALESCE(NULLIF(l.dataflow_name, ''), a.dataflow_id) AS dataflow_name,
       COALESCE(NULLIF(l.stage, ''), 'unknown') AS stage,
       a.operation_type, a.run_count, a.avg_duration_seconds,
       a.p50_duration_seconds, a.p95_duration_seconds, a.max_duration_seconds,
       a.performance_bottleneck_phase, a.candidate_count,
       l.dataflow_run_id AS latest_dataflow_run_id,
       l.event_time AS latest_run_at,
       l._source_id AS latest_source_id,
       l._file_uri AS latest_file_uri, l._file_kind AS latest_file_kind,
       l._record_line AS latest_record_line,
       NULL::VARCHAR AS latest_raw_record_json
FROM profile_aggregate a
JOIN profile_latest l USING (_source_id, dataflow_id)
ORDER BY a.p95_duration_seconds DESC, a.run_count DESC, a._source_id, a.dataflow_id
LIMIT 40
"""

_CONTEXT_SQL = f"""
SELECT COALESCE(NULLIF(platform_name, ''), 'unknown') AS platform_name,
       COALESCE(NULLIF(engine_name, ''), 'unknown') AS engine_name,
       COALESCE(NULLIF(metadata_provider_name, ''), 'unknown') AS metadata_provider_name,
       COUNT(*) AS run_count,
       COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS succeeded,
       COUNT(*) FILTER (WHERE normalized_status = 'failed') AS failed,
       ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'succeeded') / NULLIF(COUNT(*), 0), 2) AS success_rate,
       ROUND(AVG(duration_seconds), 3) AS avg_duration_seconds,
       {nullable_discrete_percentile('duration_seconds', 0.50)} AS p50_duration_seconds,
       {nullable_discrete_percentile('duration_seconds', 0.95)} AS p95_duration_seconds,
       {_metric_rate('source_rows_read', 'duration_seconds', 'TRUE')} AS rows_read_per_second,
       COUNT(*) FILTER (WHERE performance_candidate_code IS NOT NULL) AS candidate_count
FROM candidates GROUP BY platform_name, engine_name, metadata_provider_name
ORDER BY candidate_count DESC, p95_duration_seconds DESC, run_count DESC LIMIT 30
"""

_TREND_SQL = f"""
SELECT date_trunc(?, timezone(?, event_time)) AS bucket_start, COUNT(*) AS run_count,
       {nullable_discrete_percentile('duration_seconds', 0.50)} AS p50_duration_seconds,
       {nullable_discrete_percentile('duration_seconds', 0.95)} AS p95_duration_seconds,
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
       COALESCE(message, error_message) AS message, source_message, transform_message, destination_message,
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
       CASE WHEN rows_processed IS NOT NULL AND duration_seconds > 0 THEN rows_processed / duration_seconds END AS performance_rows_per_second,
       overhead_phase / NULLIF(phase_total, 0) AS performance_overhead_ratio,
       largest_phase_duration / NULLIF(phase_total, 0) AS performance_dominant_phase_ratio
FROM candidates
"""
