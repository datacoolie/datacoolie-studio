from __future__ import annotations

from datacoolie_studio.domains.monitoring.dimensions import known_dimension_sql

from typing import Any
from datacoolie_studio.domains.monitoring.dimensions import CONTEXT_PREFIX, UNKNOWN_KEY, dimension_key_sql

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.metrics.volume import (
    lakehouse_destination_sql,
    measured_rows_written_sql,
)
from datacoolie_studio.domains.monitoring.read_models.common import (
    AnalyticsContext,
    discrete_percentile,
    filtered_ctes,
    one,
    paged_rows,
    reader_context,
    rows,
    trend_bucket_key,
)


_DATAFLOW_COLUMNS = (
    "_source_id", "_ingested_at", "_record_line", "_file_uri", "raw_record_json",
    "job_id", "dataflow_id", "dataflow_run_id", "dataflow_name",
    "stage", "status", "start_time", "end_time", "duration_seconds", "operation_type",
    "source_name", "source_connection_type", "source_format", "source_full_table",
    "source_table", "source_path", "source_rows_read", "destination_name",
    "destination_connection_type", "destination_format", "destination_full_table",
    "destination_table", "destination_path", "destination_load_type",
    "destination_operation_type", "destination_rows_written", "destination_rows_inserted",
    "destination_rows_updated", "destination_rows_deleted", "destination_bytes_added",
    "destination_bytes_removed", "destination_bytes_saved", "destination_files_added",
    "destination_files_removed",
)
_JOB_COLUMNS = ("_source_id", "job_id")
# Narrow native inputs for the environment/dataflow registry. The registry
# projects only scalar summaries from these fields; it never returns raw rows.
VOLUME_REGISTRY_INPUT_COLUMNS = (
    "dataflow_id", "dataflow_run_id", "source_rows_read",
    "destination_rows_written", "destination_rows_inserted",
    "destination_rows_updated", "destination_rows_deleted",
    "destination_bytes_added", "destination_bytes_removed",
    "destination_bytes_saved", "destination_files_added",
    "destination_files_removed", "destination_connection_type",
    "destination_format",
)
_MEASURED_ROWS = measured_rows_written_sql()
_IS_LAKEHOUSE = lakehouse_destination_sql()


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


def _metric_average(column: str, eligible: str) -> str:
    """Average recorded values, retaining the existing version-aware total rules."""
    return (
        f"ROUND(({_metric_total(column, eligible)}) "
        f"/ NULLIF({_metric_measured(column, eligible)}, 0), 3)"
    )


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


def _metric_addition(left: str, left_eligible: str, right: str, right_eligible: str) -> str:
    left_population = _metric_eligible(left_eligible)
    right_population = _metric_eligible(right_eligible)
    left_measured = _metric_measured(left, left_eligible)
    right_measured = _metric_measured(right, right_eligible)
    return (
        f"CASE WHEN {left_population} = 0 OR {right_population} = 0 THEN NULL "
        f"WHEN EXISTS (SELECT 1 FROM all_log_versions WHERE log_schema_version >= 7) "
        f"THEN CASE WHEN {left_measured} = 0 OR {right_measured} = 0 THEN NULL "
        f"ELSE {_metric_observed(left, left_eligible)} + {_metric_observed(right, right_eligible)} END "
        f"WHEN {left_measured} = {left_population} AND {right_measured} = {right_population} "
        f"THEN {_metric_observed(left, left_eligible)} + {_metric_observed(right, right_eligible)} END"
    )


def _metric_ratio(numerator: str, denominator: str, eligible: str) -> str:
    population = _metric_eligible(eligible)
    numerator_measured = _metric_measured(numerator, eligible)
    denominator_measured = (
        f"COUNT({denominator}) FILTER (WHERE {eligible} AND {numerator} IS NOT NULL)"
    )
    numerator_observed = _metric_observed(
        numerator, f"{eligible} AND {denominator} IS NOT NULL"
    )
    denominator_observed = (
        f"SUM({denominator}) FILTER (WHERE {eligible} "
        f"AND {numerator} IS NOT NULL AND {denominator} IS NOT NULL)"
    )
    ratio = f"ROUND({numerator_observed} / NULLIF({denominator_observed}, 0), 3)"
    return (
        f"CASE WHEN {population} = 0 THEN NULL "
        f"WHEN EXISTS (SELECT 1 FROM all_log_versions WHERE log_schema_version >= 7) "
        f"THEN CASE WHEN {numerator_measured} = 0 OR {denominator_measured} = 0 THEN NULL ELSE {ratio} END "
        f"WHEN {numerator_measured} = {population} AND {denominator_measured} = {population} THEN {ratio} END"
    )


_VOLUME_BASE_CTES = f"""
, volume_rows AS (
  SELECT *, {_MEASURED_ROWS} AS measured_rows_written, {_IS_LAKEHOUSE} AS is_lakehouse,
         destination_bytes_added - destination_bytes_removed AS paired_net_bytes,
         CASE WHEN destination_bytes_added IS NOT NULL AND destination_bytes_removed IS NOT NULL
              THEN ABS(destination_bytes_added - destination_bytes_removed) END AS absolute_net_bytes,
         CASE WHEN destination_files_added IS NOT NULL AND destination_files_removed IS NOT NULL
              THEN destination_files_added + destination_files_removed END AS files_changed
  FROM filtered_dataflows
)
"""


def _dataflow_volume_candidate_ctes(*, source_scoped: bool) -> str:
    """Build Volume's shared entity-threshold and candidate-score formulas."""

    source_projection = "totals._source_id, " if source_scoped else ""
    grouping = "_source_id, dataflow_id" if source_scoped else "dataflow_id"
    priority = "GREATEST(read_ratio, rows_written_ratio, lakehouse_rows_ratio, bytes_ratio, files_ratio)"
    kind = f"""CASE WHEN {priority} <= 0 THEN 'none'
               WHEN read_ratio = {priority} THEN 'read'
               WHEN rows_written_ratio = {priority} THEN 'rows_written'
               WHEN lakehouse_rows_ratio = {priority} THEN 'lakehouse_rows'
               WHEN bytes_ratio = {priority} THEN 'bytes'
              ELSE 'files' END"""
    return f"""
, dataflow_volume_totals AS (
  SELECT {grouping},
         {_metric_total('source_rows_read', 'TRUE')} AS rows_read,
         {_metric_total('measured_rows_written', 'TRUE')} AS rows_written,
         {_metric_total('destination_rows_written', 'is_lakehouse')} AS lakehouse_rows_written,
         {_metric_difference('destination_bytes_added', 'TRUE', 'destination_bytes_removed', 'TRUE')} AS net_bytes,
         {_metric_addition('destination_files_added', 'TRUE', 'destination_files_removed', 'TRUE')} AS files_changed
  FROM volume_rows
  WHERE dataflow_id IS NOT NULL
  GROUP BY {grouping}
), dataflow_volume_thresholds AS (
  SELECT {discrete_percentile('rows_read', 0.95, 'rows_read > 0')} AS read_p95,
         {discrete_percentile('rows_written', 0.95, 'rows_written > 0')} AS rows_written_p95,
         {discrete_percentile('lakehouse_rows_written', 0.95, 'lakehouse_rows_written > 0')} AS lakehouse_rows_p95,
         {discrete_percentile('ABS(net_bytes)', 0.95, 'ABS(net_bytes) > 0')} AS bytes_p95,
         {discrete_percentile('files_changed', 0.95, 'files_changed > 0')} AS files_p95
  FROM dataflow_volume_totals
), dataflow_candidate_ratios AS (
  SELECT totals.dataflow_id,
         {source_projection}
         CASE WHEN thresholds.read_p95 > 0 AND totals.rows_read >= thresholds.read_p95 THEN totals.rows_read / thresholds.read_p95 ELSE 0 END AS read_ratio,
         CASE WHEN thresholds.rows_written_p95 > 0 AND totals.rows_written >= thresholds.rows_written_p95 THEN totals.rows_written / thresholds.rows_written_p95 ELSE 0 END AS rows_written_ratio,
         CASE WHEN thresholds.lakehouse_rows_p95 > 0 AND totals.lakehouse_rows_written >= thresholds.lakehouse_rows_p95 THEN totals.lakehouse_rows_written / thresholds.lakehouse_rows_p95 ELSE 0 END AS lakehouse_rows_ratio,
         CASE WHEN thresholds.bytes_p95 > 0 AND ABS(totals.net_bytes) >= thresholds.bytes_p95 THEN ABS(totals.net_bytes) / thresholds.bytes_p95 ELSE 0 END AS bytes_ratio,
         CASE WHEN thresholds.files_p95 > 0 AND totals.files_changed >= thresholds.files_p95 THEN totals.files_changed / thresholds.files_p95 ELSE 0 END AS files_ratio,
         totals.rows_read, totals.rows_written, totals.lakehouse_rows_written,
         totals.net_bytes, totals.files_changed,
         thresholds.read_p95, thresholds.rows_written_p95,
         thresholds.lakehouse_rows_p95, thresholds.bytes_p95, thresholds.files_p95
  FROM dataflow_volume_totals totals CROSS JOIN dataflow_volume_thresholds thresholds
), dataflow_candidate_scores AS (
  SELECT *, {priority} AS candidate_priority,
         {kind} AS candidate_kind
  FROM dataflow_candidate_ratios
)
"""


_RUN_VOLUME_CANDIDATE_CTES = f"""
, volume_thresholds AS (
  SELECT {discrete_percentile('source_rows_read', 0.95, 'source_rows_read > 0')} AS read_p95,
         {discrete_percentile('measured_rows_written', 0.95, 'measured_rows_written > 0')} AS rows_written_p95,
         {discrete_percentile('destination_rows_written', 0.95, 'is_lakehouse AND destination_rows_written > 0')} AS lakehouse_rows_p95,
         {discrete_percentile('absolute_net_bytes', 0.95, 'absolute_net_bytes > 0')} AS bytes_p95,
         {discrete_percentile('files_changed', 0.95, 'files_changed > 0')} AS files_p95
  FROM volume_rows
), run_ratios AS (
  SELECT v.*,
    CASE WHEN t.read_p95 > 0 AND v.source_rows_read > 0 AND v.source_rows_read >= t.read_p95 THEN v.source_rows_read / t.read_p95 ELSE 0 END AS read_ratio,
    CASE WHEN t.rows_written_p95 > 0 AND v.measured_rows_written > 0 AND v.measured_rows_written >= t.rows_written_p95 THEN v.measured_rows_written / t.rows_written_p95 ELSE 0 END AS rows_written_ratio,
    CASE WHEN t.lakehouse_rows_p95 > 0 AND v.is_lakehouse AND v.destination_rows_written > 0 AND v.destination_rows_written >= t.lakehouse_rows_p95 THEN v.destination_rows_written / t.lakehouse_rows_p95 ELSE 0 END AS lakehouse_rows_ratio,
    CASE WHEN t.bytes_p95 > 0 AND v.absolute_net_bytes > 0 AND v.absolute_net_bytes >= t.bytes_p95 THEN v.absolute_net_bytes / t.bytes_p95 ELSE 0 END AS bytes_ratio,
    CASE WHEN t.files_p95 > 0 AND v.files_changed > 0 AND v.files_changed >= t.files_p95 THEN v.files_changed / t.files_p95 ELSE 0 END AS files_ratio
  FROM volume_rows v CROSS JOIN volume_thresholds t
), run_candidates AS (
  SELECT *,
         GREATEST(read_ratio, rows_written_ratio, lakehouse_rows_ratio, bytes_ratio, files_ratio) AS candidate_priority,
         CASE WHEN GREATEST(read_ratio, rows_written_ratio, lakehouse_rows_ratio, bytes_ratio, files_ratio) <= 0 THEN 'none'
              WHEN read_ratio = GREATEST(read_ratio, rows_written_ratio, lakehouse_rows_ratio, bytes_ratio, files_ratio) THEN 'read'
              WHEN rows_written_ratio = GREATEST(read_ratio, rows_written_ratio, lakehouse_rows_ratio, bytes_ratio, files_ratio) THEN 'rows_written'
              WHEN lakehouse_rows_ratio = GREATEST(read_ratio, rows_written_ratio, lakehouse_rows_ratio, bytes_ratio, files_ratio) THEN 'lakehouse_rows'
              WHEN bytes_ratio = GREATEST(read_ratio, rows_written_ratio, lakehouse_rows_ratio, bytes_ratio, files_ratio) THEN 'bytes'
              ELSE 'files' END AS candidate_kind
  FROM run_ratios
)
"""

_VOLUME_CANDIDATE_CTES = f"""
{_RUN_VOLUME_CANDIDATE_CTES}
{_dataflow_volume_candidate_ctes(source_scoped=True)}
"""
_VOLUME_CTES = f"{_VOLUME_BASE_CTES} {_VOLUME_CANDIDATE_CTES}"


def dataflow_registry_volume_ctes() -> str:
    """Return Volume metrics/candidates at the shared environment/dataflow grain."""

    volume_inputs = _VOLUME_BASE_CTES.replace(
        "FROM filtered_dataflows", "FROM registry_volume_inputs",
    )
    return f"""
, registry_volume_inputs AS (
  SELECT {', '.join(VOLUME_REGISTRY_INPUT_COLUMNS)}
  FROM filtered_dataflows
)
{volume_inputs}
{_RUN_VOLUME_CANDIDATE_CTES}
{_dataflow_volume_candidate_ctes(source_scoped=False)}, registry_volume_aggregate AS (
  SELECT dataflow_id,
         {_metric_average('source_rows_read', 'TRUE')} AS volume_rows_read_avg_per_run,
         {_metric_average('destination_rows_written', 'TRUE')} AS volume_rows_written_avg_per_run,
         {_metric_average('destination_rows_inserted', 'TRUE')} AS volume_rows_inserted_avg_per_run,
         {_metric_average('destination_rows_updated', 'TRUE')} AS volume_rows_updated_avg_per_run,
         {_metric_average('destination_rows_deleted', 'TRUE')} AS volume_rows_deleted_avg_per_run,
         {_metric_average('destination_files_added', 'TRUE')} AS volume_files_added_avg_per_run,
         {_metric_average('destination_files_removed', 'TRUE')} AS volume_files_removed_avg_per_run,
         {_metric_average('destination_bytes_added', 'TRUE')} AS volume_bytes_added_avg_per_run,
         {_metric_average('destination_bytes_removed', 'TRUE')} AS volume_bytes_removed_avg_per_run,
         {_metric_total('source_rows_read', 'TRUE')} AS volume_rows_read,
         {_metric_observed('source_rows_read', 'TRUE')} AS volume_rows_read_observed,
         {_metric_measured('source_rows_read', 'TRUE')} AS volume_rows_read_measured,
         {_metric_eligible('TRUE')} AS volume_rows_read_eligible,
         {_metric_total('destination_rows_written', 'TRUE')} AS volume_rows_written,
         {_metric_observed('destination_rows_written', 'TRUE')} AS volume_rows_written_observed,
         {_metric_measured('destination_rows_written', 'TRUE')} AS volume_rows_written_measured,
         {_metric_eligible('TRUE')} AS volume_rows_written_eligible,
         {_metric_total('destination_rows_inserted', 'TRUE')} AS volume_rows_inserted,
         {_metric_observed('destination_rows_inserted', 'TRUE')} AS volume_rows_inserted_observed,
         {_metric_measured('destination_rows_inserted', 'TRUE')} AS volume_rows_inserted_measured,
         {_metric_eligible('TRUE')} AS volume_rows_inserted_eligible,
         {_metric_total('destination_rows_updated', 'TRUE')} AS volume_rows_updated,
         {_metric_observed('destination_rows_updated', 'TRUE')} AS volume_rows_updated_observed,
         {_metric_measured('destination_rows_updated', 'TRUE')} AS volume_rows_updated_measured,
         {_metric_eligible('TRUE')} AS volume_rows_updated_eligible,
         {_metric_total('destination_rows_deleted', 'TRUE')} AS volume_rows_deleted,
         {_metric_observed('destination_rows_deleted', 'TRUE')} AS volume_rows_deleted_observed,
         {_metric_measured('destination_rows_deleted', 'TRUE')} AS volume_rows_deleted_measured,
         {_metric_eligible('TRUE')} AS volume_rows_deleted_eligible,
         {_metric_total('destination_bytes_added', 'TRUE')} AS volume_bytes_added,
         {_metric_observed('destination_bytes_added', 'TRUE')} AS volume_bytes_added_observed,
         {_metric_measured('destination_bytes_added', 'TRUE')} AS volume_bytes_added_measured,
         {_metric_eligible('TRUE')} AS volume_bytes_added_eligible,
         {_metric_total('destination_bytes_removed', 'TRUE')} AS volume_bytes_removed,
         {_metric_observed('destination_bytes_removed', 'TRUE')} AS volume_bytes_removed_observed,
         {_metric_measured('destination_bytes_removed', 'TRUE')} AS volume_bytes_removed_measured,
         {_metric_eligible('TRUE')} AS volume_bytes_removed_eligible,
         CASE WHEN COUNT(paired_net_bytes) > 0 THEN {_metric_total('paired_net_bytes', 'TRUE')} END AS volume_net_bytes,
         {_metric_observed('paired_net_bytes', 'TRUE')} AS volume_net_bytes_observed,
         COUNT(paired_net_bytes) AS volume_net_bytes_measured,
         COUNT(*) AS volume_net_bytes_eligible,
         {_metric_addition('destination_bytes_added', 'TRUE', 'destination_bytes_removed', 'TRUE')} AS volume_bytes_changed,
         {_metric_total('destination_files_added', 'TRUE')} AS volume_files_added,
         {_metric_observed('destination_files_added', 'TRUE')} AS volume_files_added_observed,
         {_metric_measured('destination_files_added', 'TRUE')} AS volume_files_added_measured,
         {_metric_eligible('TRUE')} AS volume_files_added_eligible,
         {_metric_total('destination_files_removed', 'TRUE')} AS volume_files_removed,
         {_metric_observed('destination_files_removed', 'TRUE')} AS volume_files_removed_observed,
         {_metric_measured('destination_files_removed', 'TRUE')} AS volume_files_removed_measured,
         {_metric_eligible('TRUE')} AS volume_files_removed_eligible,
         {_metric_addition('destination_files_added', 'TRUE', 'destination_files_removed', 'TRUE')} AS volume_files_changed
  FROM volume_rows
  WHERE dataflow_id IS NOT NULL
  GROUP BY dataflow_id
), registry_volume_candidate_runs AS (
  SELECT dataflow_id,
         COUNT(*) AS volume_candidate_run_count,
         LIST(DISTINCT CASE candidate_kind
           WHEN 'read' THEN 'High rows read'
           WHEN 'rows_written' THEN 'High rows written'
           WHEN 'lakehouse_rows' THEN 'High lakehouse rows written'
           WHEN 'bytes' THEN 'High lakehouse net bytes'
           WHEN 'files' THEN 'High lakehouse file churn' END)
           FILTER (WHERE candidate_priority > 0) AS volume_candidate_run_reasons
  FROM run_candidates
  WHERE candidate_priority > 0 AND dataflow_id IS NOT NULL
  GROUP BY dataflow_id
), registry_volume_by_dataflow AS (
  SELECT aggregate.*,
         COALESCE(run_candidate.volume_candidate_run_count, 0) AS volume_candidate_run_count,
         COALESCE(run_candidate.volume_candidate_run_reasons, []::VARCHAR[]) AS volume_candidate_run_reasons,
         scores.candidate_kind AS volume_candidate_kind,
         CASE scores.candidate_kind
           WHEN 'read' THEN 'High rows read'
           WHEN 'rows_written' THEN 'High rows written'
           WHEN 'lakehouse_rows' THEN 'High lakehouse rows written'
           WHEN 'bytes' THEN 'High lakehouse net bytes'
           WHEN 'files' THEN 'High lakehouse file churn'
           ELSE '' END AS volume_candidate_reason,
         ROUND(scores.candidate_priority, 3) AS volume_candidate_priority,
         LIST_FILTER([
           STRUCT_PACK(kind := 'read', label := 'High rows read', value := scores.rows_read, threshold := scores.read_p95, ratio := ROUND(scores.read_ratio, 3)),
           STRUCT_PACK(kind := 'rows_written', label := 'High rows written', value := scores.rows_written, threshold := scores.rows_written_p95, ratio := ROUND(scores.rows_written_ratio, 3)),
           STRUCT_PACK(kind := 'lakehouse_rows', label := 'High lakehouse rows written', value := scores.lakehouse_rows_written, threshold := scores.lakehouse_rows_p95, ratio := ROUND(scores.lakehouse_rows_ratio, 3)),
           STRUCT_PACK(kind := 'bytes', label := 'High lakehouse net bytes', value := scores.net_bytes, threshold := scores.bytes_p95, ratio := ROUND(scores.bytes_ratio, 3)),
           STRUCT_PACK(kind := 'files', label := 'High lakehouse file churn', value := scores.files_changed, threshold := scores.files_p95, ratio := ROUND(scores.files_ratio, 3))
         ], signal -> signal.ratio > 0) AS volume_candidate_signals
  FROM registry_volume_aggregate aggregate
  LEFT JOIN registry_volume_candidate_runs run_candidate USING (dataflow_id)
  LEFT JOIN dataflow_candidate_scores scores USING (dataflow_id)
)
"""


def volume_read_model(
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
        volume_base_ctes = f"{ctes} {_VOLUME_BASE_CTES}"
        volume_candidate_ctes = f"{volume_base_ctes} {_VOLUME_CANDIDATE_CTES}"
        summary = one(conn.execute(f"{volume_candidate_ctes} {_SUMMARY_SQL}", params))
        trend = rows(conn.execute(
            f"{volume_base_ctes} {_TREND_SQL}", [*params, grain, timezone_name],
        ))
        workload_mix = rows(conn.execute(f"{volume_base_ctes} {_WORKLOAD_MIX_SQL}", params))
        load_mix = rows(conn.execute(f"{volume_base_ctes} {_LOAD_MIX_SQL}", params))
        routes = rows(conn.execute(f"{volume_base_ctes} {_ROUTE_SQL}", params))
        tops = rows(conn.execute(f"{volume_base_ctes} {_TOP_SQL}", params))
    return {
        "generation": generation,
        "summary": summary,
        "rows_by_date": [_trend_row(row, grain) for row in trend],
        "bytes_by_date": [_trend_row(row, grain) for row in trend],
        "volume_by_workload_type": workload_mix,
        "volume_by_load_type": load_mix,
        "route_volume": routes,
        "top_dataflows_by_rows_read": _top_rows(tops, "rows_read"),
        "top_dataflows_by_rows_written": _top_rows(tops, "rows_written"),
        "top_dataflows_by_lakehouse_rows_written": _top_rows(tops, "lakehouse_rows_written"),
        "top_dataflows_by_bytes_added": _top_rows(tops, "bytes_added"),
        "top_dataflows_by_net_bytes": _top_rows(tops, "net_bytes"),
    }


def volume_evidence_read_model(
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
        "dataflow_name": "dataflow_name", "stage": "stage", "run_count": "run_count",
        "volume_rows_read": "volume_rows_read",
        "volume_rows_written": "volume_rows_written",
        "volume_rows_inserted": "volume_rows_inserted",
        "volume_files_changed": "volume_files_changed",
        "volume_net_bytes": "volume_net_bytes",
        "volume_candidate_priority": "volume_candidate_priority",
    }
    order_column = sort_columns.get(sort_by, "volume_candidate_priority")
    direction = "ASC" if sort_dir == "asc" else "DESC"
    with reader_context(paths, analytics_context) as (conn, source_ids, generation):
        if conn is None or not source_ids:
            return {"generation": generation, "records": [], "total_records": 0}
        ctes, params = filtered_ctes(
            source_ids, filters,
            dataflow_columns=_DATAFLOW_COLUMNS,
            job_columns=_JOB_COLUMNS,
        )
        volume_ctes = f"{ctes} {_VOLUME_CTES}"
        registry_query = f"{volume_ctes} {_REGISTRY_SQL}"
        records, total = paged_rows(conn.execute(
            f"SELECT evidence.*, COUNT(*) OVER () AS __total_records "
            f"FROM ({registry_query}) evidence "
            f"ORDER BY {order_column} {direction} NULLS LAST, _source_id ASC, dataflow_id ASC, latest_run_at DESC NULLS LAST, log_session_id ASC NULLS LAST, dataflow_run_id ASC NULLS LAST "
            "LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ))
        if not records and offset:
            total = int(conn.execute(f"SELECT COUNT(*) FROM ({registry_query}) evidence", params).fetchone()[0])
    return {"generation": generation, "records": records, "total_records": total}


def _empty(generation: str) -> dict[str, Any]:
    return {
        "generation": generation, "summary": {}, "rows_by_date": [], "bytes_by_date": [],
        "volume_by_workload_type": [], "volume_by_load_type": [], "route_volume": [],
        "top_dataflows_by_rows_read": [], "top_dataflows_by_rows_written": [],
        "top_dataflows_by_lakehouse_rows_written": [],
        "top_dataflows_by_bytes_added": [],
        "top_dataflows_by_net_bytes": [],
    }


def _trend_row(row: dict[str, Any], grain: str) -> dict[str, Any]:
    bucket_key = trend_bucket_key(row.get("bucket_start"), grain)
    return {
        "date": bucket_key, "bucket": bucket_key,
        "bucket_start": row.get("bucket_start"), "bucket_end": None, "grain": grain,
        **{key: value for key, value in row.items() if key != "bucket_start"},
    }


def _top_rows(rows_: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    return [
        {
            "_source_id": row.get("_source_id"),
            "dataflow_id": row["dataflow_id"],
            "name": row["name"], "value": row["value"], "count": row["count"],
            "latest_log_session_id": row.get("latest_log_session_id"),
            "latest_dataflow_run_id": row.get("latest_dataflow_run_id"),
        }
        for row in rows_ if row.get("metric") == metric
    ]


_SUMMARY_SQL = f"""
SELECT
  COUNT(*) AS dataflow_records,
  (SELECT COUNT(*) FROM filtered_jobs) AS job_records,
  MIN(run_date) AS date_min, MAX(run_date) AS date_max, MAX(event_time) AS latest_dataflow_log_at,
  (SELECT MAX(event_time) FROM filtered_jobs) AS latest_job_log_at,
  GREATEST(MAX(event_time), (SELECT MAX(event_time) FROM filtered_jobs)) AS latest_log_at,
  COUNT(DISTINCT {known_dimension_sql('engine_name')}) AS active_engines,
  COUNT(DISTINCT {known_dimension_sql('metadata_provider_name')}) AS active_metadata_providers,
  {_metric_total('source_rows_read', 'TRUE')} AS total_rows_read,
  {_metric_total('destination_rows_written', 'TRUE')} AS total_rows_written,
  {_metric_total('destination_rows_written', 'NOT is_lakehouse')} AS total_non_lakehouse_rows_written,
  {_metric_total('destination_rows_written', 'is_lakehouse')} AS total_lakehouse_rows_written,
  {_metric_observed('source_rows_read', 'TRUE')} AS observed_rows_read,
  {_metric_measured('source_rows_read', 'TRUE')} AS rows_read_measured,
  {_metric_eligible('TRUE')} AS rows_read_eligible,
  {_metric_observed('destination_rows_written', 'TRUE')} AS observed_rows_written,
  {_metric_measured('destination_rows_written', 'TRUE')} AS rows_written_measured,
  {_metric_eligible('TRUE')} AS rows_written_eligible,
  COALESCE(SUM(destination_rows_written) FILTER (WHERE is_lakehouse), 0) AS observed_lakehouse_rows_written,
  COUNT(destination_rows_written) FILTER (WHERE is_lakehouse) AS lakehouse_rows_written_measured,
  COUNT(*) FILTER (WHERE is_lakehouse) AS lakehouse_rows_written_eligible,
  {_metric_total('destination_rows_inserted', 'TRUE')} AS total_rows_inserted,
  {_metric_total('destination_rows_updated', 'TRUE')} AS total_rows_updated,
  {_metric_total('destination_rows_deleted', 'TRUE')} AS total_rows_deleted,
  {_metric_observed('destination_rows_inserted', 'TRUE')} AS observed_rows_inserted,
  {_metric_measured('destination_rows_inserted', 'TRUE')} AS rows_inserted_measured,
  {_metric_eligible('TRUE')} AS rows_inserted_eligible,
  {_metric_observed('destination_rows_updated', 'TRUE')} AS observed_rows_updated,
  {_metric_measured('destination_rows_updated', 'TRUE')} AS rows_updated_measured,
  {_metric_eligible('TRUE')} AS rows_updated_eligible,
  {_metric_observed('destination_rows_deleted', 'TRUE')} AS observed_rows_deleted,
  {_metric_measured('destination_rows_deleted', 'TRUE')} AS rows_deleted_measured,
  {_metric_eligible('TRUE')} AS rows_deleted_eligible,
  COUNT(*) FILTER (WHERE is_lakehouse) AS lakehouse_destination_run_count,
  ROUND(100.0 * COUNT(*) FILTER (WHERE is_lakehouse) / NULLIF(COUNT(*), 0), 2) AS lakehouse_destination_share,
  {_metric_total('destination_files_added', 'TRUE')} AS files_added,
  {_metric_total('destination_files_removed', 'TRUE')} AS files_removed,
  {_metric_observed('destination_files_added', 'TRUE')} AS observed_files_added,
  {_metric_measured('destination_files_added', 'TRUE')} AS files_added_measured,
  {_metric_eligible('TRUE')} AS files_added_eligible,
  {_metric_observed('destination_files_removed', 'TRUE')} AS observed_files_removed,
  {_metric_measured('destination_files_removed', 'TRUE')} AS files_removed_measured,
  {_metric_eligible('TRUE')} AS files_removed_eligible,
  {_metric_total('destination_bytes_added', 'TRUE')} AS total_bytes_added,
  {_metric_total('destination_bytes_removed', 'TRUE')} AS total_bytes_removed,
  {_metric_total('destination_bytes_saved', 'TRUE')} AS total_bytes_saved,
  {_metric_observed('destination_bytes_added', 'TRUE')} AS observed_bytes_added,
  {_metric_measured('destination_bytes_added', 'TRUE')} AS bytes_added_measured,
  {_metric_eligible('TRUE')} AS bytes_added_eligible,
  {_metric_observed('destination_bytes_removed', 'TRUE')} AS observed_bytes_removed,
  {_metric_measured('destination_bytes_removed', 'TRUE')} AS bytes_removed_measured,
  {_metric_eligible('TRUE')} AS bytes_removed_eligible,
  CASE WHEN COUNT(paired_net_bytes) > 0
       THEN {_metric_total('paired_net_bytes', 'TRUE')} END AS net_bytes_change,
  {_metric_observed('paired_net_bytes', 'TRUE')} AS observed_net_bytes_change,
  COUNT(paired_net_bytes) AS net_bytes_change_measured,
  COUNT(*) AS net_bytes_change_eligible,
  {_metric_ratio('destination_bytes_added', 'destination_files_added', 'TRUE')} AS avg_bytes_per_file_added,
  COUNT(*) FILTER (WHERE destination_bytes_added IS NOT NULL AND destination_files_added IS NOT NULL) AS bytes_per_file_measured,
  COUNT(*) AS bytes_per_file_eligible,
  COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skip_count,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'skipped') / NULLIF(COUNT(*), 0), 2) AS skip_rate,
  (SELECT COUNT(*) FROM run_candidates WHERE candidate_priority > 0) AS high_volume_run_count,
  (SELECT COUNT(*) FROM run_candidates WHERE candidate_priority > 0) AS high_volume_candidate_run_count,
  (SELECT COUNT(*) FROM run_candidates WHERE candidate_kind = 'read') AS high_volume_rows_count,
  (SELECT COUNT(*) FROM run_candidates WHERE candidate_kind = 'rows_written') AS high_volume_rows_written_count,
  (SELECT COUNT(*) FROM run_candidates WHERE candidate_kind = 'lakehouse_rows') AS high_volume_lakehouse_rows_count,
  (SELECT COUNT(*) FROM run_candidates WHERE candidate_kind = 'bytes') AS high_volume_bytes_count,
  (SELECT COUNT(*) FROM run_candidates WHERE candidate_kind = 'files') AS high_volume_files_count,
  (SELECT COUNT(*) FROM dataflow_candidate_scores WHERE candidate_priority > 0) AS high_volume_dataflow_count
FROM volume_rows
"""

_TREND_SQL = f"""
SELECT date_trunc(?, timezone(?, event_time)) AS bucket_start,
       {_metric_total('source_rows_read', 'TRUE')} AS rows_read,
       {_metric_observed('source_rows_read', 'TRUE')} AS observed_rows_read,
       {_metric_measured('source_rows_read', 'TRUE')} AS rows_read_measured,
       {_metric_eligible('TRUE')} AS rows_read_eligible,
       {_metric_total('destination_rows_written', 'TRUE')} AS rows_written,
       {_metric_observed('destination_rows_written', 'TRUE')} AS observed_rows_written,
       {_metric_measured('destination_rows_written', 'TRUE')} AS rows_written_measured,
       {_metric_eligible('TRUE')} AS rows_written_eligible,
       {_metric_total('destination_rows_written', 'TRUE')} AS rows_output,
       {_metric_total('destination_rows_written', 'is_lakehouse')} AS lakehouse_rows_written,
       {_metric_total('destination_rows_inserted', 'TRUE')} AS rows_inserted,
       {_metric_total('destination_rows_updated', 'TRUE')} AS rows_updated,
       {_metric_total('destination_rows_deleted', 'TRUE')} AS rows_deleted,
       COUNT(*) AS dataflow_runs,
       {_metric_total('destination_bytes_added', 'TRUE')} AS bytes_added,
       {_metric_total('destination_bytes_removed', 'TRUE')} AS bytes_removed,
       {_metric_total('destination_bytes_saved', 'TRUE')} AS bytes_saved,
       {_metric_difference('destination_bytes_added', 'TRUE', 'destination_bytes_removed', 'TRUE')} AS net_bytes,
       {_metric_total('destination_files_added', 'TRUE')} AS files_added,
       {_metric_total('destination_files_removed', 'TRUE')} AS files_removed
FROM volume_rows WHERE event_time IS NOT NULL
GROUP BY bucket_start ORDER BY bucket_start
"""

_WORKLOAD_MIX_SQL = f"""
SELECT *
FROM (
SELECT '{CONTEXT_PREFIX}' || CAST(json_array(operation_type,
              COALESCE(NULLIF(destination_load_type, '{UNKNOWN_KEY}'), destination_operation_type)) AS VARCHAR) AS workload_type,
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
       COUNT(*) AS runs, COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skipped
FROM volume_rows
GROUP BY workload_type
) workload_totals
ORDER BY rows_read DESC, rows_written DESC, bytes_added + bytes_removed DESC, runs DESC
LIMIT 100
"""

_LOAD_MIX_SQL = f"""
SELECT COALESCE(NULLIF(destination_load_type, '{UNKNOWN_KEY}'), destination_operation_type) AS load_type,
       {_metric_total('destination_rows_written', 'TRUE')} AS rows_written,
       {_metric_observed('destination_rows_written', 'TRUE')} AS observed_rows_written,
       {_metric_measured('destination_rows_written', 'TRUE')} AS rows_written_measured,
       {_metric_eligible('TRUE')} AS rows_written_eligible,
       {_metric_total('destination_bytes_added', 'TRUE')} AS bytes_added,
       COUNT(*) AS count
FROM volume_rows GROUP BY load_type ORDER BY rows_written DESC LIMIT 100
"""

_ROUTE_SQL = f"""
SELECT {dimension_key_sql('source_name')} AS source_name,
       {dimension_key_sql('destination_name')} AS destination_name,
       COALESCE(MODE(NULLIF(source_format, '')), 'unknown') AS source_format,
       MODE({dimension_key_sql('destination_format')}) AS destination_format,
       COALESCE(MODE(NULLIF(source_connection_type, '')), 'unknown') AS source_connection_type,
       MODE({dimension_key_sql('destination_connection_type')}) AS destination_connection_type,
       COUNT(*) AS runs, COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skipped,
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
       {_metric_total('destination_files_added', 'TRUE')} AS files_added,
       {_metric_total('destination_files_removed', 'TRUE')} AS files_removed
FROM volume_rows GROUP BY {dimension_key_sql('source_name')}, {dimension_key_sql('destination_name')}
ORDER BY rows_read DESC, rows_written DESC, runs DESC LIMIT 100
"""

_TOP_DERIVED_CTES = f"""
, top_latest AS (
  SELECT _source_id, dataflow_id,
         COALESCE(NULLIF(dataflow_name, ''), dataflow_id) AS name,
         log_session_id AS latest_log_session_id,
         dataflow_run_id AS latest_dataflow_run_id
  FROM volume_rows
  WHERE dataflow_id IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY _source_id, dataflow_id
    ORDER BY event_time DESC NULLS LAST, _ingested_at DESC NULLS LAST,
             _record_line DESC NULLS LAST, _file_uri DESC NULLS LAST,
             log_session_id DESC NULLS LAST, dataflow_run_id DESC NULLS LAST
  ) = 1
), dataflow_totals AS (
  SELECT _source_id, dataflow_id,
          COUNT(*) AS count,
         {_metric_total('source_rows_read', 'TRUE')} AS rows_read,
         {_metric_total('destination_rows_written', 'TRUE')} AS rows_written,
         {_metric_total('destination_rows_written', 'is_lakehouse')} AS lakehouse_rows_written,
         {_metric_total('destination_bytes_added', 'TRUE')} AS bytes_added,
         {_metric_difference('destination_bytes_added', 'TRUE', 'destination_bytes_removed', 'TRUE')} AS net_bytes
  FROM volume_rows
  WHERE dataflow_id IS NOT NULL
  GROUP BY _source_id, dataflow_id
), metrics AS (
  SELECT totals._source_id, latest.latest_log_session_id, latest.latest_dataflow_run_id,
         totals.dataflow_id, latest.name, totals.count,
         'rows_read' AS metric, totals.rows_read AS value
  FROM dataflow_totals totals JOIN top_latest latest USING (_source_id, dataflow_id)
   UNION ALL SELECT totals._source_id, latest.latest_log_session_id, latest.latest_dataflow_run_id,
          totals.dataflow_id, latest.name, totals.count, 'rows_written', totals.rows_written
   FROM dataflow_totals totals JOIN top_latest latest USING (_source_id, dataflow_id)
   UNION ALL SELECT totals._source_id, latest.latest_log_session_id, latest.latest_dataflow_run_id,
          totals.dataflow_id, latest.name, totals.count, 'lakehouse_rows_written', totals.lakehouse_rows_written
   FROM dataflow_totals totals JOIN top_latest latest USING (_source_id, dataflow_id)
  UNION ALL SELECT totals._source_id, latest.latest_log_session_id, latest.latest_dataflow_run_id,
         totals.dataflow_id, latest.name, totals.count, 'bytes_added', totals.bytes_added
  FROM dataflow_totals totals JOIN top_latest latest USING (_source_id, dataflow_id)
  UNION ALL SELECT totals._source_id, latest.latest_log_session_id, latest.latest_dataflow_run_id,
         totals.dataflow_id, latest.name, totals.count, 'net_bytes', totals.net_bytes
  FROM dataflow_totals totals JOIN top_latest latest USING (_source_id, dataflow_id)
)
"""
_TOP_SELECT_SQL = """
SELECT * FROM metrics
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY metric
  ORDER BY CASE WHEN metric = 'net_bytes' THEN ABS(value) ELSE value END DESC,
           _source_id ASC NULLS LAST, dataflow_id ASC, name ASC
) <= 20
ORDER BY metric, CASE WHEN metric = 'net_bytes' THEN ABS(value) ELSE value END DESC,
         _source_id ASC NULLS LAST, dataflow_id ASC, name ASC
"""
_TOP_SQL = f"{_TOP_DERIVED_CTES} {_TOP_SELECT_SQL}"

_REGISTRY_SQL = f"""
, registry_latest AS (
  SELECT *
  FROM volume_rows
  WHERE dataflow_id IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY _source_id, dataflow_id
    ORDER BY event_time DESC NULLS LAST, _ingested_at DESC NULLS LAST,
             _record_line DESC NULLS LAST, _file_uri DESC NULLS LAST,
             log_session_id DESC NULLS LAST, dataflow_run_id DESC NULLS LAST
  ) = 1
), registry_aggregate AS (
  SELECT _source_id, dataflow_id,
       COUNT(*) AS run_count,
       {_metric_total('source_rows_read', 'TRUE')} AS volume_rows_read,
       {_metric_observed('source_rows_read', 'TRUE')} AS volume_rows_read_observed,
       {_metric_measured('source_rows_read', 'TRUE')} AS volume_rows_read_measured,
       {_metric_eligible('TRUE')} AS volume_rows_read_eligible,
       {_metric_total('destination_rows_written', 'TRUE')} AS volume_rows_written,
       {_metric_observed('destination_rows_written', 'TRUE')} AS volume_rows_written_observed,
       {_metric_measured('destination_rows_written', 'TRUE')} AS volume_rows_written_measured,
       {_metric_eligible('TRUE')} AS volume_rows_written_eligible,
       {_metric_total('destination_rows_written', 'is_lakehouse')} AS volume_lakehouse_rows_written,
       {_metric_total('destination_rows_inserted', 'TRUE')} AS volume_rows_inserted,
       {_metric_total('destination_rows_updated', 'TRUE')} AS volume_rows_updated,
       {_metric_total('destination_rows_deleted', 'TRUE')} AS volume_rows_deleted,
       {_metric_total('destination_bytes_added', 'TRUE')} AS volume_bytes_added,
       {_metric_total('destination_bytes_removed', 'TRUE')} AS volume_bytes_removed,
       {_metric_difference('destination_bytes_added', 'TRUE', 'destination_bytes_removed', 'TRUE')} AS volume_net_bytes,
       {_metric_total('destination_files_added', 'TRUE')} AS volume_files_added,
       {_metric_total('destination_files_removed', 'TRUE')} AS volume_files_removed,
       {_metric_addition('destination_files_added', 'TRUE', 'destination_files_removed', 'TRUE')} AS volume_files_changed,
       CASE WHEN {_metric_measured('source_rows_read', 'TRUE')} = {_metric_eligible('TRUE')} THEN MAX(source_rows_read) FILTER (WHERE TRUE) END AS peak_rows_read,
       CASE WHEN {_metric_measured('destination_rows_written', 'TRUE')} = {_metric_eligible('TRUE')} THEN MAX(destination_rows_written) FILTER (WHERE TRUE) END AS peak_rows_written,
       MAX(destination_rows_written) FILTER (WHERE is_lakehouse) AS peak_lakehouse_rows_written,
       {discrete_percentile('source_rows_read', 0.95)} AS p95_rows_read,
       {discrete_percentile('destination_rows_written', 0.95)} AS p95_rows_written,
       {discrete_percentile('destination_rows_written', 0.95, 'is_lakehouse')} AS p95_lakehouse_rows_written,
       COALESCE(ROUND(AVG(duration_seconds), 3), 0) AS avg_duration_seconds,
       {discrete_percentile('duration_seconds', 0.95)} AS p95_duration_seconds
  FROM volume_rows
  WHERE dataflow_id IS NOT NULL
  GROUP BY _source_id, dataflow_id
), registry AS (
  SELECT a._source_id, l.log_session_id, a.dataflow_id,
       l.job_id, l.dataflow_run_id,
       COALESCE(NULLIF(l.dataflow_name, ''), a.dataflow_id) AS dataflow_name,
       COALESCE(NULLIF(l.stage, ''), 'unknown') AS stage,
       l.normalized_status AS status,
       l.start_time, l.end_time, l.duration_seconds,
       COALESCE(NULLIF(l.operation_type, ''), 'unknown') AS operation_type,
       l.source_name, l.source_connection_type, l.source_format,
       l.source_full_table, l.source_table, l.source_path,
       l.destination_name, l.destination_connection_type, l.destination_format,
       l.destination_full_table, l.destination_table, l.destination_path,
       l.destination_load_type,
       l.event_time AS latest_run_at, l.normalized_status AS latest_run_status,
       l._source_id AS latest_source_id,
       l._file_uri AS latest_file_uri, l._file_kind AS latest_file_kind,
       l._record_line AS latest_record_line,
       NULL::VARCHAR AS latest_raw_record_json,
       a.* EXCLUDE (_source_id, dataflow_id)
  FROM registry_aggregate a JOIN registry_latest l USING (_source_id, dataflow_id)
), candidate_by_dataflow AS (
  SELECT _source_id, dataflow_id,
         COUNT(*) AS candidate_run_count,
         list(DISTINCT CASE candidate_kind
           WHEN 'read' THEN 'High rows read'
           WHEN 'rows_written' THEN 'High rows written'
           WHEN 'lakehouse_rows' THEN 'High lakehouse rows written'
           WHEN 'bytes' THEN 'High lakehouse net bytes'
           WHEN 'files' THEN 'High lakehouse file churn' END) AS candidate_run_reasons
  FROM run_candidates
  WHERE candidate_priority > 0 AND dataflow_id IS NOT NULL
  GROUP BY _source_id, dataflow_id
)
SELECT registry.*,
       COALESCE(run_candidate.candidate_run_count, 0) AS candidate_run_count,
       COALESCE(run_candidate.candidate_run_reasons, []::VARCHAR[]) AS candidate_run_reasons,
       scores.candidate_kind AS volume_candidate_kind,
       CASE scores.candidate_kind
         WHEN 'read' THEN 'High rows read'
         WHEN 'rows_written' THEN 'High rows written'
         WHEN 'lakehouse_rows' THEN 'High lakehouse rows written'
         WHEN 'bytes' THEN 'High lakehouse net bytes'
         WHEN 'files' THEN 'High lakehouse file churn'
         ELSE '' END AS volume_candidate_reason,
       ROUND(scores.candidate_priority, 3) AS volume_candidate_priority,
       list_filter([
         struct_pack(kind := 'read', label := 'High rows read', value := scores.rows_read, threshold := scores.read_p95, ratio := ROUND(scores.read_ratio, 3)),
         struct_pack(kind := 'rows_written', label := 'High rows written', value := scores.rows_written, threshold := scores.rows_written_p95, ratio := ROUND(scores.rows_written_ratio, 3)),
         struct_pack(kind := 'lakehouse_rows', label := 'High lakehouse rows written', value := scores.lakehouse_rows_written, threshold := scores.lakehouse_rows_p95, ratio := ROUND(scores.lakehouse_rows_ratio, 3)),
         struct_pack(kind := 'bytes', label := 'High lakehouse net bytes', value := scores.net_bytes, threshold := scores.bytes_p95, ratio := ROUND(scores.bytes_ratio, 3)),
         struct_pack(kind := 'files', label := 'High lakehouse file churn', value := scores.files_changed, threshold := scores.files_p95, ratio := ROUND(scores.files_ratio, 3))
       ], signal -> signal.ratio > 0) AS volume_candidate_signals
FROM registry
LEFT JOIN candidate_by_dataflow run_candidate
  ON run_candidate._source_id = registry._source_id
 AND run_candidate.dataflow_id = registry.dataflow_id
JOIN dataflow_candidate_scores scores
  ON scores._source_id = registry._source_id
 AND scores.dataflow_id = registry.dataflow_id
ORDER BY volume_candidate_priority DESC, volume_rows_read DESC, dataflow_name
"""
