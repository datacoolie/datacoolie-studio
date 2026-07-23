from __future__ import annotations

from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.metrics.volume import (
    estimated_rows_written_sql,
    lakehouse_destination_sql,
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
    "_source_id", "job_id", "dataflow_id", "dataflow_run_id", "dataflow_name",
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
_EST_ROWS = estimated_rows_written_sql()
_IS_LAKEHOUSE = lakehouse_destination_sql()
_VOLUME_BASE_CTES = f"""
, volume_rows AS (
  SELECT *, {_EST_ROWS} AS est_rows_written, {_IS_LAKEHOUSE} AS is_lakehouse,
         ABS(COALESCE(destination_bytes_added, 0) - COALESCE(destination_bytes_removed, 0)) AS absolute_net_bytes,
         COALESCE(destination_files_added, 0) + COALESCE(destination_files_removed, 0) AS files_changed
  FROM filtered_dataflows
)
"""
_VOLUME_CANDIDATE_CTES = f"""
, volume_thresholds AS (
  SELECT {discrete_percentile('source_rows_read', 0.95, 'source_rows_read > 0')} AS read_p95,
         {discrete_percentile('est_rows_written', 0.95, 'est_rows_written > 0')} AS est_rows_p95,
         {discrete_percentile('destination_rows_written', 0.95, 'destination_rows_written > 0')} AS lakehouse_rows_p95,
         {discrete_percentile('absolute_net_bytes', 0.95, 'absolute_net_bytes > 0')} AS bytes_p95,
         {discrete_percentile('files_changed', 0.95, 'files_changed > 0')} AS files_p95
  FROM volume_rows
), run_ratios AS (
  SELECT v.*,
    CASE WHEN t.read_p95 > 0 AND v.source_rows_read > 0 AND v.source_rows_read >= t.read_p95 THEN v.source_rows_read / t.read_p95 ELSE 0 END AS read_ratio,
    CASE WHEN t.est_rows_p95 > 0 AND v.est_rows_written > 0 AND v.est_rows_written >= t.est_rows_p95 THEN v.est_rows_written / t.est_rows_p95 ELSE 0 END AS est_rows_ratio,
    CASE WHEN t.lakehouse_rows_p95 > 0 AND v.destination_rows_written > 0 AND v.destination_rows_written >= t.lakehouse_rows_p95 THEN v.destination_rows_written / t.lakehouse_rows_p95 ELSE 0 END AS lakehouse_rows_ratio,
    CASE WHEN t.bytes_p95 > 0 AND v.absolute_net_bytes > 0 AND v.absolute_net_bytes >= t.bytes_p95 THEN v.absolute_net_bytes / t.bytes_p95 ELSE 0 END AS bytes_ratio,
    CASE WHEN t.files_p95 > 0 AND v.files_changed > 0 AND v.files_changed >= t.files_p95 THEN v.files_changed / t.files_p95 ELSE 0 END AS files_ratio
  FROM volume_rows v CROSS JOIN volume_thresholds t
), run_candidates AS (
  SELECT *, GREATEST(read_ratio, est_rows_ratio, lakehouse_rows_ratio, bytes_ratio, files_ratio) AS candidate_priority,
    CASE WHEN GREATEST(read_ratio, est_rows_ratio, lakehouse_rows_ratio, bytes_ratio, files_ratio) <= 0 THEN 'none'
         WHEN read_ratio = GREATEST(read_ratio, est_rows_ratio, lakehouse_rows_ratio, bytes_ratio, files_ratio) THEN 'read'
         WHEN est_rows_ratio = GREATEST(read_ratio, est_rows_ratio, lakehouse_rows_ratio, bytes_ratio, files_ratio) THEN 'est_rows'
         WHEN lakehouse_rows_ratio = GREATEST(read_ratio, est_rows_ratio, lakehouse_rows_ratio, bytes_ratio, files_ratio) THEN 'lakehouse_rows'
         WHEN bytes_ratio = GREATEST(read_ratio, est_rows_ratio, lakehouse_rows_ratio, bytes_ratio, files_ratio) THEN 'bytes'
         ELSE 'files' END AS candidate_kind
  FROM run_ratios
), dataflow_volume_totals AS (
  SELECT dataflow_id,
         SUM(COALESCE(source_rows_read, 0)) AS rows_read,
         SUM(est_rows_written) AS est_rows_written,
         SUM(COALESCE(destination_rows_written, 0)) AS lakehouse_rows_written,
         ABS(SUM(COALESCE(destination_bytes_added, 0) - COALESCE(destination_bytes_removed, 0))) AS net_bytes,
         SUM(COALESCE(destination_files_added, 0) + COALESCE(destination_files_removed, 0)) AS files_changed
  FROM volume_rows
  WHERE dataflow_id IS NOT NULL AND TRIM(dataflow_id) NOT IN ('', 'unknown', 'none', 'null', 'nan')
  GROUP BY dataflow_id
), dataflow_volume_thresholds AS (
  SELECT {discrete_percentile('rows_read', 0.95, 'rows_read > 0')} AS read_p95,
         {discrete_percentile('est_rows_written', 0.95, 'est_rows_written > 0')} AS est_rows_p95,
         {discrete_percentile('lakehouse_rows_written', 0.95, 'lakehouse_rows_written > 0')} AS lakehouse_rows_p95,
         {discrete_percentile('net_bytes', 0.95, 'net_bytes > 0')} AS bytes_p95,
         {discrete_percentile('files_changed', 0.95, 'files_changed > 0')} AS files_p95
  FROM dataflow_volume_totals
), dataflow_candidate_ratios AS (
  SELECT totals.dataflow_id,
         CASE WHEN thresholds.read_p95 > 0 AND totals.rows_read >= thresholds.read_p95 THEN totals.rows_read / thresholds.read_p95 ELSE 0 END AS read_ratio,
         CASE WHEN thresholds.est_rows_p95 > 0 AND totals.est_rows_written >= thresholds.est_rows_p95 THEN totals.est_rows_written / thresholds.est_rows_p95 ELSE 0 END AS est_rows_ratio,
         CASE WHEN thresholds.lakehouse_rows_p95 > 0 AND totals.lakehouse_rows_written >= thresholds.lakehouse_rows_p95 THEN totals.lakehouse_rows_written / thresholds.lakehouse_rows_p95 ELSE 0 END AS lakehouse_rows_ratio,
         CASE WHEN thresholds.bytes_p95 > 0 AND totals.net_bytes >= thresholds.bytes_p95 THEN totals.net_bytes / thresholds.bytes_p95 ELSE 0 END AS bytes_ratio,
         CASE WHEN thresholds.files_p95 > 0 AND totals.files_changed >= thresholds.files_p95 THEN totals.files_changed / thresholds.files_p95 ELSE 0 END AS files_ratio,
         totals.* EXCLUDE (dataflow_id), thresholds.*
  FROM dataflow_volume_totals totals CROSS JOIN dataflow_volume_thresholds thresholds
), dataflow_candidate_scores AS (
  SELECT *, GREATEST(read_ratio, est_rows_ratio, lakehouse_rows_ratio, bytes_ratio, files_ratio) AS candidate_priority,
         CASE WHEN GREATEST(read_ratio, est_rows_ratio, lakehouse_rows_ratio, bytes_ratio, files_ratio) <= 0 THEN 'none'
              WHEN read_ratio = GREATEST(read_ratio, est_rows_ratio, lakehouse_rows_ratio, bytes_ratio, files_ratio) THEN 'read'
              WHEN est_rows_ratio = GREATEST(read_ratio, est_rows_ratio, lakehouse_rows_ratio, bytes_ratio, files_ratio) THEN 'est_rows'
              WHEN lakehouse_rows_ratio = GREATEST(read_ratio, est_rows_ratio, lakehouse_rows_ratio, bytes_ratio, files_ratio) THEN 'lakehouse_rows'
              WHEN bytes_ratio = GREATEST(read_ratio, est_rows_ratio, lakehouse_rows_ratio, bytes_ratio, files_ratio) THEN 'bytes'
              ELSE 'files' END AS candidate_kind
  FROM dataflow_candidate_ratios
)
"""
_VOLUME_CTES = f"{_VOLUME_BASE_CTES} {_VOLUME_CANDIDATE_CTES}"


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
        "top_dataflows_by_est_rows_written": _top_rows(tops, "est_rows_written"),
        "top_dataflows_by_rows_written": _top_rows(tops, "rows_written"),
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
        "volume_est_rows_written": "volume_est_rows_written",
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
            f"ORDER BY {order_column} {direction} NULLS LAST, dataflow_id ASC "
            "LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ))
    return {"generation": generation, "records": records, "total_records": total}


def _empty(generation: str) -> dict[str, Any]:
    return {
        "generation": generation, "summary": {}, "rows_by_date": [], "bytes_by_date": [],
        "volume_by_workload_type": [], "volume_by_load_type": [], "route_volume": [],
        "top_dataflows_by_rows_read": [], "top_dataflows_by_est_rows_written": [],
        "top_dataflows_by_rows_written": [], "top_dataflows_by_bytes_added": [],
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
        {"dataflow_id": row["dataflow_id"], "name": row["name"], "value": row["value"], "count": row["count"]}
        for row in rows_ if row.get("metric") == metric
    ]


_SUMMARY_SQL = """
SELECT
  COUNT(*) AS dataflow_records,
  (SELECT COUNT(*) FROM filtered_jobs) AS job_records,
  MIN(run_date) AS date_min, MAX(run_date) AS date_max, MAX(event_time) AS latest_dataflow_log_at,
  (SELECT MAX(event_time) FROM filtered_jobs) AS latest_job_log_at,
  GREATEST(MAX(event_time), (SELECT MAX(event_time) FROM filtered_jobs)) AS latest_log_at,
  COUNT(DISTINCT NULLIF(engine_name, 'unknown')) AS active_engines,
  COUNT(DISTINCT NULLIF(metadata_provider_name, 'unknown')) AS active_metadata_providers,
  COALESCE(SUM(source_rows_read), 0) AS total_rows_read,
  COALESCE(SUM(destination_rows_written), 0) AS total_rows_written,
  COALESCE(SUM(est_rows_written), 0) AS total_est_rows_written,
  COALESCE(SUM(est_rows_written), 0) - COALESCE(SUM(destination_rows_written), 0) AS total_est_rows_written_non_lakehouse,
  COALESCE(SUM(destination_rows_inserted), 0) AS total_rows_inserted,
  COALESCE(SUM(destination_rows_updated), 0) AS total_rows_updated,
  COALESCE(SUM(destination_rows_deleted), 0) AS total_rows_deleted,
  COUNT(*) FILTER (WHERE is_lakehouse) AS lakehouse_destination_run_count,
  ROUND(100.0 * COUNT(*) FILTER (WHERE is_lakehouse) / NULLIF(COUNT(*), 0), 2) AS lakehouse_destination_share,
  COALESCE(SUM(destination_files_added), 0) AS files_added,
  COALESCE(SUM(destination_files_removed), 0) AS files_removed,
  COALESCE(SUM(destination_bytes_added), 0) AS total_bytes_added,
  COALESCE(SUM(destination_bytes_removed), 0) AS total_bytes_removed,
  COALESCE(SUM(destination_bytes_saved), 0) AS total_bytes_saved,
  COALESCE(SUM(destination_bytes_added), 0) - COALESCE(SUM(destination_bytes_removed), 0) AS net_bytes_change,
  COALESCE(ROUND(SUM(destination_bytes_added) / NULLIF(SUM(destination_files_added), 0), 3), 0) AS avg_bytes_per_file_added,
  COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skip_count,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'skipped') / NULLIF(COUNT(*), 0), 2) AS skip_rate,
  (SELECT COUNT(*) FROM run_candidates WHERE candidate_priority > 0) AS high_volume_run_count,
  (SELECT COUNT(*) FROM run_candidates WHERE candidate_priority > 0) AS high_volume_candidate_run_count,
  (SELECT COUNT(*) FROM run_candidates WHERE candidate_kind = 'read') AS high_volume_rows_count,
  (SELECT COUNT(*) FROM run_candidates WHERE candidate_kind = 'est_rows') AS high_volume_est_rows_count,
  (SELECT COUNT(*) FROM run_candidates WHERE candidate_kind = 'lakehouse_rows') AS high_volume_lakehouse_rows_count,
  (SELECT COUNT(*) FROM run_candidates WHERE candidate_kind = 'bytes') AS high_volume_bytes_count,
  (SELECT COUNT(*) FROM run_candidates WHERE candidate_kind = 'files') AS high_volume_files_count,
  (SELECT COUNT(*) FROM dataflow_candidate_scores WHERE candidate_priority > 0) AS high_volume_dataflow_count
FROM volume_rows
"""

_TREND_SQL = """
SELECT date_trunc(?, timezone(?, event_time)) AS bucket_start,
       SUM(COALESCE(source_rows_read, 0)) AS rows_read,
       SUM(COALESCE(destination_rows_written, 0)) AS rows_written,
       SUM(est_rows_written) AS est_rows_written,
       SUM(CASE WHEN destination_rows_written > 0 THEN destination_rows_written WHEN normalized_status = 'succeeded' AND NOT is_lakehouse THEN source_rows_read ELSE COALESCE(destination_rows_written, 0) END) AS rows_output,
       SUM(CASE WHEN COALESCE(destination_rows_written, 0) <= 0 AND normalized_status = 'succeeded' AND NOT is_lakehouse THEN COALESCE(source_rows_read, 0) ELSE 0 END) AS rows_output_estimated,
       SUM(COALESCE(destination_rows_inserted, 0)) AS rows_inserted,
       SUM(COALESCE(destination_rows_updated, 0)) AS rows_updated,
       SUM(COALESCE(destination_rows_deleted, 0)) AS rows_deleted,
       COUNT(*) AS dataflow_runs,
       SUM(COALESCE(destination_bytes_added, 0)) AS bytes_added,
       SUM(COALESCE(destination_bytes_removed, 0)) AS bytes_removed,
       SUM(COALESCE(destination_bytes_saved, 0)) AS bytes_saved,
       SUM(COALESCE(destination_bytes_added, 0) - COALESCE(destination_bytes_removed, 0)) AS net_bytes,
       SUM(COALESCE(destination_files_added, 0)) AS files_added,
       SUM(COALESCE(destination_files_removed, 0)) AS files_removed
FROM volume_rows WHERE event_time IS NOT NULL
GROUP BY bucket_start ORDER BY bucket_start
"""

_WORKLOAD_MIX_SQL = """
SELECT CONCAT(COALESCE(NULLIF(operation_type, ''), 'unknown'), ' · ',
              COALESCE(NULLIF(destination_load_type, ''), NULLIF(destination_operation_type, ''), 'unknown')) AS workload_type,
       SUM(COALESCE(source_rows_read, 0)) AS rows_read,
       SUM(COALESCE(destination_rows_written, 0)) AS rows_written,
       SUM(est_rows_written) AS est_rows_written,
       SUM(COALESCE(destination_rows_inserted, 0)) AS rows_inserted,
       SUM(COALESCE(destination_rows_updated, 0)) AS rows_updated,
       SUM(COALESCE(destination_rows_deleted, 0)) AS rows_deleted,
       SUM(COALESCE(destination_bytes_added, 0)) AS bytes_added,
       SUM(COALESCE(destination_bytes_removed, 0)) AS bytes_removed,
       COUNT(*) AS runs, COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skipped
FROM volume_rows
GROUP BY workload_type
ORDER BY rows_read DESC, est_rows_written DESC, bytes_added + bytes_removed DESC, runs DESC
LIMIT 100
"""

_LOAD_MIX_SQL = """
SELECT COALESCE(NULLIF(destination_load_type, ''), NULLIF(destination_operation_type, ''), 'unknown') AS load_type,
       SUM(COALESCE(destination_rows_written, 0)) AS rows_written,
       SUM(est_rows_written) AS est_rows_written,
       SUM(COALESCE(destination_bytes_added, 0)) AS bytes_added,
       COUNT(*) AS count
FROM volume_rows GROUP BY load_type ORDER BY est_rows_written DESC LIMIT 100
"""

_ROUTE_SQL = """
SELECT COALESCE(NULLIF(source_name, ''), 'unknown') AS source_name,
       COALESCE(NULLIF(destination_name, ''), 'unknown') AS destination_name,
       COALESCE(MODE(NULLIF(source_format, '')), 'unknown') AS source_format,
       COALESCE(MODE(NULLIF(destination_format, '')), 'unknown') AS destination_format,
       COALESCE(MODE(NULLIF(source_connection_type, '')), 'unknown') AS source_connection_type,
       COALESCE(MODE(NULLIF(destination_connection_type, '')), 'unknown') AS destination_connection_type,
       COUNT(*) AS runs, COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skipped,
       SUM(COALESCE(source_rows_read, 0)) AS rows_read,
       SUM(COALESCE(destination_rows_written, 0)) AS rows_written,
       SUM(est_rows_written) AS est_rows_written,
       SUM(COALESCE(destination_rows_inserted, 0)) AS rows_inserted,
       SUM(COALESCE(destination_rows_updated, 0)) AS rows_updated,
       SUM(COALESCE(destination_rows_deleted, 0)) AS rows_deleted,
       SUM(COALESCE(destination_bytes_added, 0)) AS bytes_added,
       SUM(COALESCE(destination_bytes_removed, 0)) AS bytes_removed,
       SUM(COALESCE(destination_files_added, 0)) AS files_added,
       SUM(COALESCE(destination_files_removed, 0)) AS files_removed
FROM volume_rows GROUP BY source_name, destination_name
ORDER BY rows_read DESC, est_rows_written DESC, runs DESC LIMIT 100
"""

_TOP_DERIVED_CTES = """
, dataflow_totals AS (
  SELECT dataflow_id, COALESCE(ARG_MAX(NULLIF(dataflow_name, ''), event_time), dataflow_id) AS name,
         COUNT(*) AS count, SUM(COALESCE(source_rows_read, 0)) AS rows_read,
         SUM(est_rows_written) AS est_rows_written,
         SUM(COALESCE(destination_rows_written, 0)) AS rows_written,
         SUM(COALESCE(destination_bytes_added, 0)) AS bytes_added,
         SUM(COALESCE(destination_bytes_added, 0) - COALESCE(destination_bytes_removed, 0)) AS net_bytes
  FROM volume_rows
  WHERE dataflow_id IS NOT NULL AND TRIM(dataflow_id) NOT IN ('', 'unknown', 'none', 'null', 'nan')
  GROUP BY dataflow_id
), metrics AS (
  SELECT dataflow_id, name, count, 'rows_read' AS metric, rows_read AS value FROM dataflow_totals
  UNION ALL SELECT dataflow_id, name, count, 'est_rows_written', est_rows_written FROM dataflow_totals
  UNION ALL SELECT dataflow_id, name, count, 'rows_written', rows_written FROM dataflow_totals
  UNION ALL SELECT dataflow_id, name, count, 'bytes_added', bytes_added FROM dataflow_totals
  UNION ALL SELECT dataflow_id, name, count, 'net_bytes', net_bytes FROM dataflow_totals
)
"""
_TOP_SELECT_SQL = """
SELECT * FROM metrics
QUALIFY ROW_NUMBER() OVER (PARTITION BY metric ORDER BY CASE WHEN metric = 'net_bytes' THEN ABS(value) ELSE value END DESC, name) <= 20
ORDER BY metric, CASE WHEN metric = 'net_bytes' THEN ABS(value) ELSE value END DESC
"""
_TOP_SQL = f"{_TOP_DERIVED_CTES} {_TOP_SELECT_SQL}"

_REGISTRY_SQL = f"""
, registry AS (
SELECT dataflow_id,
       ARG_MAX(job_id, event_time) AS job_id,
       ARG_MAX(dataflow_run_id, event_time) AS dataflow_run_id,
       COALESCE(ARG_MAX(NULLIF(dataflow_name, ''), event_time), dataflow_id) AS dataflow_name,
       COALESCE(ARG_MAX(NULLIF(stage, ''), event_time), 'unknown') AS stage,
       ARG_MAX(normalized_status, event_time) AS status,
       ARG_MAX(start_time, event_time) AS start_time, ARG_MAX(end_time, event_time) AS end_time,
       ARG_MAX(duration_seconds, event_time) AS duration_seconds,
       COALESCE(ARG_MAX(NULLIF(operation_type, ''), event_time), 'unknown') AS operation_type,
       ARG_MAX(source_name, event_time) AS source_name,
       ARG_MAX(source_connection_type, event_time) AS source_connection_type,
       ARG_MAX(source_format, event_time) AS source_format,
       ARG_MAX(source_full_table, event_time) AS source_full_table,
       ARG_MAX(source_table, event_time) AS source_table, ARG_MAX(source_path, event_time) AS source_path,
       ARG_MAX(destination_name, event_time) AS destination_name,
       ARG_MAX(destination_connection_type, event_time) AS destination_connection_type,
       ARG_MAX(destination_format, event_time) AS destination_format,
       ARG_MAX(destination_full_table, event_time) AS destination_full_table,
       ARG_MAX(destination_table, event_time) AS destination_table,
       ARG_MAX(destination_path, event_time) AS destination_path,
       ARG_MAX(destination_load_type, event_time) AS destination_load_type,
       MAX(event_time) AS latest_run_at, ARG_MAX(normalized_status, event_time) AS latest_run_status,
       COUNT(*) AS run_count,
       SUM(COALESCE(source_rows_read, 0)) AS volume_rows_read,
       SUM(est_rows_written) AS volume_est_rows_written,
       SUM(COALESCE(destination_rows_written, 0)) AS volume_lakehouse_rows_written,
       SUM(COALESCE(destination_rows_inserted, 0)) AS volume_rows_inserted,
       SUM(COALESCE(destination_rows_updated, 0)) AS volume_rows_updated,
       SUM(COALESCE(destination_rows_deleted, 0)) AS volume_rows_deleted,
       SUM(COALESCE(destination_bytes_added, 0)) AS volume_bytes_added,
       SUM(COALESCE(destination_bytes_removed, 0)) AS volume_bytes_removed,
       SUM(COALESCE(destination_bytes_added, 0) - COALESCE(destination_bytes_removed, 0)) AS volume_net_bytes,
       SUM(COALESCE(destination_files_added, 0)) AS volume_files_added,
       SUM(COALESCE(destination_files_removed, 0)) AS volume_files_removed,
       SUM(COALESCE(destination_files_added, 0) + COALESCE(destination_files_removed, 0)) AS volume_files_changed,
       MAX(COALESCE(source_rows_read, 0)) AS peak_rows_read,
       MAX(est_rows_written) AS peak_est_rows_written,
       MAX(COALESCE(destination_rows_written, 0)) AS peak_lakehouse_rows_written,
       {discrete_percentile('source_rows_read', 0.95)} AS p95_rows_read,
       {discrete_percentile('est_rows_written', 0.95)} AS p95_est_rows_written,
       {discrete_percentile('destination_rows_written', 0.95)} AS p95_lakehouse_rows_written,
       COALESCE(ROUND(AVG(duration_seconds), 3), 0) AS avg_duration_seconds,
       {discrete_percentile('duration_seconds', 0.95)} AS p95_duration_seconds
FROM volume_rows
WHERE dataflow_id IS NOT NULL AND TRIM(dataflow_id) NOT IN ('', 'unknown', 'none', 'null', 'nan')
GROUP BY dataflow_id
), candidate_by_dataflow AS (
  SELECT dataflow_id,
         COUNT(*) AS candidate_run_count,
         list(DISTINCT CASE candidate_kind
           WHEN 'read' THEN 'High rows read'
           WHEN 'est_rows' THEN 'High estimated rows written'
           WHEN 'lakehouse_rows' THEN 'High lakehouse rows written'
           WHEN 'bytes' THEN 'High lakehouse net bytes'
           WHEN 'files' THEN 'High lakehouse file churn' END) AS candidate_run_reasons
  FROM run_candidates
  WHERE candidate_priority > 0
    AND dataflow_id IS NOT NULL
    AND TRIM(dataflow_id) NOT IN ('', 'unknown', 'none', 'null', 'nan')
  GROUP BY dataflow_id
)
SELECT registry.*,
       COALESCE(run_candidate.candidate_run_count, 0) AS candidate_run_count,
       COALESCE(run_candidate.candidate_run_reasons, []::VARCHAR[]) AS candidate_run_reasons,
       scores.candidate_kind AS volume_candidate_kind,
       CASE scores.candidate_kind
         WHEN 'read' THEN 'High rows read'
         WHEN 'est_rows' THEN 'High estimated rows written'
         WHEN 'lakehouse_rows' THEN 'High lakehouse rows written'
         WHEN 'bytes' THEN 'High lakehouse net bytes'
         WHEN 'files' THEN 'High lakehouse file churn'
         ELSE '' END AS volume_candidate_reason,
       ROUND(scores.candidate_priority, 3) AS volume_candidate_priority,
       list_filter([
         struct_pack(kind := 'read', label := 'High rows read', value := scores.rows_read, threshold := scores.read_p95, ratio := ROUND(scores.read_ratio, 3)),
         struct_pack(kind := 'est_rows', label := 'High estimated rows written', value := scores.est_rows_written, threshold := scores.est_rows_p95, ratio := ROUND(scores.est_rows_ratio, 3)),
         struct_pack(kind := 'lakehouse_rows', label := 'High lakehouse rows written', value := scores.lakehouse_rows_written, threshold := scores.lakehouse_rows_p95, ratio := ROUND(scores.lakehouse_rows_ratio, 3)),
         struct_pack(kind := 'bytes', label := 'High lakehouse net bytes', value := scores.net_bytes, threshold := scores.bytes_p95, ratio := ROUND(scores.bytes_ratio, 3)),
         struct_pack(kind := 'files', label := 'High lakehouse file churn', value := scores.files_changed, threshold := scores.files_p95, ratio := ROUND(scores.files_ratio, 3))
       ], signal -> signal.ratio > 0) AS volume_candidate_signals
FROM registry
LEFT JOIN candidate_by_dataflow run_candidate USING (dataflow_id)
JOIN dataflow_candidate_scores scores USING (dataflow_id)
ORDER BY volume_candidate_priority DESC, volume_rows_read DESC, dataflow_name
"""
