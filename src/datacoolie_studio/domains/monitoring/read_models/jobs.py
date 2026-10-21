from __future__ import annotations

from datacoolie_studio.domains.monitoring.dimensions import known_dimension_sql

from datetime import datetime
from typing import Any

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
from datacoolie_studio.domains.monitoring.log_repository import monitoring_requires_comparison_subset


_DATAFLOW_COLUMNS = (
    "_source_id", "raw_record_json", "job_id", "dataflow_id", "dataflow_run_id", "dataflow_name",
    "stage", "start_time", "end_time", "duration_seconds", "operation_type",
    "source_rows_read", "destination_rows_written", "destination_bytes_added",
    "destination_bytes_removed", "destination_operation_type",
)
_JOB_COLUMNS = (
    "_source_id", "raw_record_json", "job_id", "status", "start_time", "end_time", "duration_seconds", "observed_at",
    "engine_name", "metadata_provider_name", "platform_name", "stages", "operation_types",
    "total_dataflows", "total_succeeded", "total_failed", "total_skipped",
    "total_running", "total_pending", "total_rows_read", "total_rows_written",
    "log_records_dropped", "log_bytes_dropped",
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


def jobs_read_model(
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
        trend = rows(conn.execute(f"{ctes} {_TREND_SQL}", [*params, grain, timezone_name]))
        windows = operation_windows(
            conn,
            ctes,
            params,
            timezone_name=timezone_name,
            now=now,
        )
        job_duration_by_operation = duration_distribution(
            conn,
            ctes,
            params,
            group_column="operation_types",
            output_key="operation_type",
            limit=12,
            fact_kind="job",
        )
        workload_efficiency = rows(conn.execute(f"{ctes} {_WORKLOAD_EFFICIENCY_SQL}", params))
        child_fanout = rows(conn.execute(f"{ctes} {_CHILD_FANOUT_SQL}", params))
        status_by_stage = rows(conn.execute(f"{ctes} {_STATUS_BY_STAGE_SQL}", params))
        latest_failed = one(conn.execute(f"{ctes} {_LATEST_FAILED_SQL}", params))
        comparison_scope = "subset" if monitoring_requires_comparison_subset(filters) else "full"
        comparison_relation = "filtered_dataflows" if comparison_scope == "subset" else "comparison_dataflows"
        comparison_query = reconciliation_sql(
            comparison_scope,
            child_relation=comparison_relation,
        )
        reconciliation_checks = rows(conn.execute(f"{ctes} {comparison_query}", params))
        if comparison_scope != "full":
            reconciliation_checks = _compact_subset_reconciliation(reconciliation_checks)
    return {
        "generation": generation,
        "summary": summary,
        "status_trend": trend,
        "windows": windows,
        "job_duration_by_operation": job_duration_by_operation,
        "workload_efficiency": workload_efficiency,
        "child_fanout": child_fanout,
        "status_by_stage": status_by_stage,
        "latest_failed_job": latest_failed or None,
        "reconciliation_checks": reconciliation_checks,
    }


def _empty_read_model(generation: str) -> dict[str, Any]:
    return {
        "generation": generation,
        "summary": {},
        "status_trend": [],
        "windows": empty_operation_windows(),
        "job_duration_by_operation": [],
        "workload_efficiency": [],
        "child_fanout": [],
        "status_by_stage": [],
        "latest_failed_job": None,
        "reconciliation_checks": [],
    }


def _compact_subset_reconciliation(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose one bounded scope notice per job on the Jobs page.

    The Diagnostics page consumes the four metric rows from ``reconciliation_sql``
    so it can report check counts and metric-level evidence.  Jobs only needs to
    tell the user that a filtered child population cannot be reconciled; keeping
    the total-dataflow count retains the observed scope while avoiding four
    repeated notices for the same job.
    """
    compact: list[dict[str, Any]] = []
    for check in checks:
        if check.get("metric") != "total_dataflows":
            continue
        compact.append({
            "severity": "insufficient_coverage",
            "_source_id": check.get("_source_id"),
            "job_id": check.get("job_id"),
            "log_session_id": check.get("log_session_id"),
            "metric": "child_scope",
            "expected": None,
            "observed": check.get("observed"),
            "difference": None,
            "scope_coverage": "subset",
        })
    return compact


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
  COUNT(DISTINCT {known_dimension_sql('engine_name')}) AS active_engines,
  COUNT(DISTINCT {known_dimension_sql('metadata_provider_name')}) AS active_metadata_providers,
  COUNT(*) AS total_jobs,
  COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS total_succeeded,
  COUNT(*) FILTER (WHERE normalized_status = 'failed') AS total_failures,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'succeeded') /
    NULLIF(COUNT(*) FILTER (WHERE normalized_status IN ('succeeded','failed')), 0), 2) AS job_success_rate,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'failed') /
    NULLIF(COUNT(*) FILTER (WHERE normalized_status IN ('succeeded','failed')), 0), 2) AS job_failure_rate,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'skipped') / NULLIF(COUNT(*), 0), 2) AS job_skip_rate,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'running') / NULLIF(COUNT(*), 0), 2) AS job_running_rate,
  ROUND(100.0 * COUNT(*) FILTER (WHERE normalized_status = 'pending') / NULLIF(COUNT(*), 0), 2) AS job_pending_rate,
  CASE WHEN EXISTS (SELECT 1 FROM all_log_versions WHERE log_schema_version >= 7) THEN
         CASE WHEN COUNT(total_rows_read) + COUNT(total_rows_written) = 0 THEN NULL
              ELSE COALESCE(SUM(total_rows_read), 0) + COALESCE(SUM(total_rows_written), 0) END
       WHEN COUNT(*) = 0 THEN 0
       WHEN COUNT(total_rows_read) = COUNT(*) AND COUNT(total_rows_written) = COUNT(*)
       THEN SUM(total_rows_read) + SUM(total_rows_written)
       ELSE NULL END AS total_rows_processed,
  CASE WHEN COUNT(*) = 0 THEN 0
       WHEN COUNT(total_skipped) = COUNT(*) THEN SUM(total_skipped)
       ELSE NULL END AS total_skipped,
  CASE
    WHEN COUNT(*) = 0 THEN 0
    WHEN COUNT(total_pending) = COUNT(*) THEN SUM(total_pending)
    ELSE NULL
  END AS total_pending,
  CASE
    WHEN COUNT(*) = 0 THEN 0
    WHEN COUNT(total_running) = COUNT(*) THEN SUM(total_running)
    ELSE NULL
  END AS total_running,
  COUNT(duration_seconds) FILTER (WHERE normalized_status IN ('succeeded','failed')) AS duration_count,
  ROUND(AVG(duration_seconds) FILTER (WHERE normalized_status IN ('succeeded','failed')), 3) AS avg_duration_seconds,
  {nullable_discrete_percentile("duration_seconds", 0.25, "normalized_status IN ('succeeded','failed')")} AS q1_duration_seconds,
  {nullable_discrete_percentile("duration_seconds", 0.50, "normalized_status IN ('succeeded','failed')")} AS p50_duration_seconds,
  {nullable_discrete_percentile("duration_seconds", 0.75, "normalized_status IN ('succeeded','failed')")} AS q3_duration_seconds,
  {nullable_discrete_percentile("duration_seconds", 0.95, "normalized_status IN ('succeeded','failed')")} AS p95_duration_seconds,
  {nullable_discrete_percentile("duration_seconds", 0.99, "normalized_status IN ('succeeded','failed')")} AS p99_duration_seconds,
  ROUND(MAX(duration_seconds) FILTER (WHERE normalized_status IN ('succeeded','failed')), 3) AS max_duration_seconds,
  (SELECT COUNT(*) FROM filtered_dataflows) AS total_dataflows,
  (SELECT COUNT(*) FROM filtered_dataflows WHERE normalized_status = 'failed') AS failed_dataflows,
  (SELECT COUNT(*) FROM filtered_dataflows WHERE normalized_status = 'succeeded') AS succeeded_dataflows,
  (SELECT COUNT(*) FROM filtered_dataflows WHERE normalized_status = 'skipped') AS skipped_dataflows,
  (SELECT COUNT(*) FROM filtered_dataflows WHERE normalized_status = 'running') AS running_dataflows,
  (SELECT COUNT(*) FROM filtered_dataflows WHERE normalized_status = 'pending') AS pending_dataflows
FROM filtered_jobs
"""

_TREND_SQL = """
SELECT date_trunc(?, timezone(?, event_time)) AS bucket_start,
       normalized_status AS status, COUNT(*) AS count, MIN(event_time) AS end_time
FROM filtered_jobs
WHERE event_time IS NOT NULL
GROUP BY bucket_start, normalized_status
ORDER BY bucket_start, normalized_status
"""

_WORKLOAD_EFFICIENCY_SQL = f"""
, latest_jobs AS (
  SELECT * EXCLUDE (job_rank) FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY _source_id, log_session_id, job_id ORDER BY event_time DESC NULLS LAST) AS job_rank
    FROM filtered_jobs
  ) WHERE job_rank = 1
), classified_dataflows AS (
  SELECT _source_id, log_session_id, job_id, normalized_status,
         CASE
           WHEN LOWER(TRIM(COALESCE(destination_operation_type, ''))) IN ('compact', 'cleanup', 'maintenance')
             THEN LOWER(TRIM(destination_operation_type))
           ELSE COALESCE(NULLIF(operation_type, ''), 'unknown')
         END AS operation_type,
         CASE
           WHEN LOWER(COALESCE(operation_type, '')) = 'maintenance'
             OR LOWER(TRIM(COALESCE(destination_operation_type, ''))) IN ('compact', 'cleanup', 'maintenance')
             THEN 'bytes_per_second'
           ELSE 'rows_per_second'
         END AS workload_size_metric,
         duration_seconds, source_rows_read, destination_rows_written,
         destination_bytes_added, destination_bytes_removed
  FROM filtered_dataflows
  WHERE job_id IS NOT NULL
), child_workload AS (
  SELECT _source_id, log_session_id, job_id, operation_type, workload_size_metric,
         SUM(COALESCE(duration_seconds, 0)) AS duration_seconds,
         SUM(duration_seconds) FILTER (
           WHERE source_rows_read IS NOT NULL AND duration_seconds IS NOT NULL
         ) AS measured_duration_seconds,
         COUNT(source_rows_read) AS rows_read_measured,
         COUNT(source_rows_read) FILTER (WHERE duration_seconds IS NOT NULL)
           AS rows_read_duration_count,
         COUNT(*) AS child_dataflow_count,
         COUNT(*) FILTER (WHERE normalized_status = 'failed') AS failed_child_dataflows,
         COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skipped_child_dataflows,
         {_metric_total('source_rows_read', 'TRUE')} AS rows_read,
         {_metric_total('destination_rows_written', 'TRUE')} AS rows_written,
         {_metric_total('destination_bytes_added', 'TRUE')} AS bytes_added,
         {_metric_total('destination_bytes_removed', 'TRUE')} AS bytes_removed,
         SUM(destination_bytes_added + destination_bytes_removed) FILTER (
           WHERE destination_bytes_added IS NOT NULL AND destination_bytes_removed IS NOT NULL
         ) AS bytes_processed,
         COUNT(*) FILTER (
           WHERE destination_bytes_added IS NOT NULL AND destination_bytes_removed IS NOT NULL
         ) AS bytes_processed_measured,
         COUNT(*) FILTER (
           WHERE destination_bytes_added IS NOT NULL AND destination_bytes_removed IS NOT NULL
             AND duration_seconds IS NOT NULL
         ) AS bytes_processed_duration_count,
         SUM(duration_seconds) FILTER (
           WHERE destination_bytes_added IS NOT NULL AND destination_bytes_removed IS NOT NULL
             AND duration_seconds IS NOT NULL
         ) AS bytes_processed_duration_seconds
  FROM classified_dataflows
  GROUP BY _source_id, log_session_id, job_id, operation_type, workload_size_metric
), ranked_workload AS (
  SELECT *,
         ROW_NUMBER() OVER (
           PARTITION BY operation_type
           ORDER BY duration_seconds DESC, child_dataflow_count DESC, job_id,
                    _source_id, log_session_id NULLS FIRST
         ) AS operation_rank,
         ROW_NUMBER() OVER (
           ORDER BY duration_seconds DESC, child_dataflow_count DESC, job_id,
                    _source_id, log_session_id NULLS FIRST
         ) AS overall_rank
  FROM child_workload
  WHERE duration_seconds > 0
)
SELECT w.job_id, w.log_session_id,
       COALESCE(j.normalized_status, 'unknown') AS status,
       w.operation_type,
       COALESCE(NULLIF(j.engine_name, ''), 'unknown') AS engine_name,
       COALESCE(NULLIF(j.metadata_provider_name, ''), 'unknown') AS metadata_provider_name,
       COALESCE(NULLIF(j.platform_name, ''), 'unknown') AS platform_name,
       w.duration_seconds, w.child_dataflow_count, w.failed_child_dataflows,
       w.skipped_child_dataflows, w.rows_read, w.rows_written,
       w.bytes_added, w.bytes_removed,
       CASE WHEN w.workload_size_metric = 'bytes_per_second' THEN
              CASE WHEN w.bytes_processed_measured = 0
                         OR w.bytes_processed_measured <> w.bytes_processed_duration_count
                         OR w.bytes_processed_duration_seconds IS NULL
                         OR w.bytes_processed_duration_seconds <= 0
                    THEN NULL
                    ELSE w.bytes_processed / w.bytes_processed_duration_seconds
              END
            WHEN w.rows_read IS NULL
              OR w.rows_read_measured = 0
              OR w.rows_read_measured <> w.rows_read_duration_count
              OR w.measured_duration_seconds IS NULL
              OR w.measured_duration_seconds <= 0
            THEN NULL
            ELSE w.rows_read / w.measured_duration_seconds
       END AS workload_size,
       w.workload_size_metric
FROM ranked_workload w
LEFT JOIN latest_jobs j ON j._source_id = w._source_id AND j.job_id = w.job_id AND j.log_session_id IS NOT DISTINCT FROM w.log_session_id
WHERE w.operation_rank = 1 OR w.overall_rank <= 500
ORDER BY w.duration_seconds DESC, w.child_dataflow_count DESC, w.job_id,
         w._source_id, w.log_session_id NULLS FIRST
"""

_CHILD_FANOUT_SQL = """
SELECT total_dataflows,
       COUNT(*) AS jobs,
       COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS succeeded,
       COUNT(*) FILTER (WHERE normalized_status = 'failed') AS failed,
       COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skipped,
       COUNT(*) FILTER (WHERE normalized_status = 'running') AS running,
       COUNT(*) FILTER (WHERE normalized_status = 'pending') AS pending,
       COUNT(*) FILTER (WHERE normalized_status NOT IN ('succeeded','failed','skipped','running','pending')) AS unknown
FROM filtered_jobs
WHERE total_dataflows > 0
GROUP BY total_dataflows
ORDER BY total_dataflows
"""

_STATUS_BY_STAGE_SQL = """
, latest_jobs AS (
  SELECT * EXCLUDE (job_rank) FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY _source_id, log_session_id, job_id ORDER BY event_time DESC NULLS LAST) AS job_rank
    FROM filtered_jobs
  ) WHERE job_rank = 1
), stage_jobs AS (
  SELECT DISTINCT _source_id, log_session_id, job_id, COALESCE(NULLIF(stage, ''), 'unknown') AS stage
  FROM filtered_dataflows WHERE job_id IS NOT NULL
)
SELECT s.stage,
       COUNT(*) AS touched_jobs,
       COUNT(*) FILTER (WHERE j.normalized_status = 'succeeded') AS succeeded,
       COUNT(*) FILTER (WHERE j.normalized_status = 'failed') AS failed,
       COUNT(*) FILTER (WHERE j.normalized_status = 'skipped') AS skipped,
       COUNT(*) FILTER (WHERE j.normalized_status = 'running') AS running,
       COUNT(*) FILTER (WHERE j.normalized_status = 'pending') AS pending,
       COUNT(*) FILTER (WHERE j.normalized_status IS NULL OR j.normalized_status NOT IN ('succeeded','failed','skipped','running','pending')) AS unknown
FROM stage_jobs s
LEFT JOIN latest_jobs j ON j._source_id = s._source_id AND j.job_id = s.job_id AND j.log_session_id IS NOT DISTINCT FROM s.log_session_id
GROUP BY s.stage
ORDER BY failed DESC, touched_jobs DESC, s.stage
"""

_LATEST_FAILED_SQL = """
SELECT job_id, status, start_time, end_time, duration_seconds, engine_name,
       metadata_provider_name, platform_name, stages, operation_types
FROM filtered_jobs
WHERE normalized_status = 'failed' AND event_time IS NOT NULL
ORDER BY event_time DESC, job_id
LIMIT 1
"""

def _reconciliation_relation(relation: str) -> str:
    """Return the only internal relations allowed in reconciliation SQL."""

    if relation not in {"filtered_dataflows", "comparison_dataflows"}:
        raise ValueError(f"Unsupported reconciliation child relation: {relation}")
    return relation


def reconciliation_sql(scope_coverage: str, *, child_relation: str = "filtered_dataflows") -> str:
    """Compare a full child population, or expose why a filtered one is partial.

    Diagnostics may provide ``comparison_dataflows`` when it has selected the
    whole recorded child population for each job.  Jobs continues to use the
    canonical filtered relation and keeps its existing bounded detail shape.
    """

    child_relation = _reconciliation_relation(child_relation)
    excluded_child_count = (
        "COUNT(*) FILTER (WHERE in_filter = FALSE)"
        if child_relation == "comparison_dataflows"
        else "0::BIGINT"
    )
    if scope_coverage != "full":
        return f"""
  , child_counts AS (
    SELECT _source_id, log_session_id, job_id,
           COUNT(*) AS total_dataflows,
           COUNT(*) FILTER (WHERE normalized_status = 'failed') AS total_failed,
           COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS total_skipped,
           COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS total_succeeded
    FROM {child_relation}
    WHERE job_id IS NOT NULL
    GROUP BY _source_id, job_id, log_session_id
  ), selected_jobs AS (
    SELECT DISTINCT j._source_id, j.job_id, j.log_session_id
    FROM filtered_jobs j
    WHERE j.job_id IS NOT NULL
  ), comparisons AS (
    SELECT j._source_id, j.job_id, j.log_session_id,
           metrics.metric, metrics.observed
    FROM selected_jobs j
    LEFT JOIN child_counts c
      ON c._source_id = j._source_id
     AND c.job_id = j.job_id
     AND c.log_session_id IS NOT DISTINCT FROM j.log_session_id
    CROSS JOIN LATERAL (VALUES
      ('total_dataflows', COALESCE(c.total_dataflows, 0)),
      ('total_failed', COALESCE(c.total_failed, 0)),
      ('total_skipped', COALESCE(c.total_skipped, 0)),
      ('total_succeeded', COALESCE(c.total_succeeded, 0))
    ) metrics(metric, observed)
  )
SELECT 'insufficient_coverage' AS severity, _source_id, job_id, log_session_id,
       metric, NULL AS expected, observed,
       NULL AS difference, 'subset' AS scope_coverage
FROM comparisons
ORDER BY job_id, metric
LIMIT 50
"""
    return f"""
, child_counts AS (
  SELECT _source_id, log_session_id, job_id, MAX(event_time) AS latest_child_time,
         COUNT(*) AS total_dataflows,
         COUNT(*) FILTER (WHERE normalized_status = 'failed') AS total_failed,
         COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS total_skipped,
         COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS total_succeeded,
         {excluded_child_count} AS excluded_child_count
  FROM {child_relation} WHERE job_id IS NOT NULL
  GROUP BY _source_id, log_session_id, job_id
), comparisons AS (
  SELECT j._source_id, j.job_id, j.log_session_id, metric, expected, observed,
         j.observed_at, c.latest_child_time,
         j.log_records_dropped, j.log_bytes_dropped,
         CASE WHEN j.normalized_status NOT IN ('succeeded','failed','skipped') THEN 'nonterminal'
              WHEN j.log_records_dropped IS NULL THEN 'unknown_loss'
              WHEN j.log_records_dropped > 0 THEN 'lost_detail'
              WHEN j.observed_at IS NULL THEN 'unknown_snapshot'
              WHEN j.observed_at < c.latest_child_time THEN 'stale_snapshot'
              WHEN expected IS NULL THEN 'unknown_total'
              WHEN COALESCE(c.excluded_child_count, 0) > 0 THEN 'range_incomplete'
              ELSE 'full' END AS scope_coverage
  FROM filtered_jobs j
  LEFT JOIN child_counts c ON c._source_id = j._source_id AND c.job_id = j.job_id AND c.log_session_id IS NOT DISTINCT FROM j.log_session_id
  CROSS JOIN LATERAL (VALUES
    ('total_dataflows', j.total_dataflows, COALESCE(c.total_dataflows, 0)),
    ('total_failed', j.total_failed, COALESCE(c.total_failed, 0)),
    ('total_skipped', j.total_skipped, COALESCE(c.total_skipped, 0)),
    ('total_succeeded', j.total_succeeded, COALESCE(c.total_succeeded, 0))
  ) metrics(metric, expected, observed)
)
SELECT CASE WHEN scope_coverage = 'full' THEN 'warning' ELSE 'insufficient_coverage' END AS severity,
       _source_id, job_id, log_session_id, metric, expected, observed,
       observed_at, latest_child_time, log_records_dropped, log_bytes_dropped,
       CASE WHEN scope_coverage = 'full' THEN observed - expected END AS difference, scope_coverage,
       COUNT(*) FILTER (WHERE scope_coverage = 'full' AND expected <> observed) OVER () AS mismatch_count,
       COUNT(*) FILTER (WHERE scope_coverage <> 'full') OVER () AS insufficient_count
FROM comparisons
WHERE scope_coverage <> 'full' OR expected <> observed
ORDER BY CASE WHEN scope_coverage = 'full' AND expected <> observed THEN 0 ELSE 1 END,
         _source_id, log_session_id, job_id, metric
LIMIT 50
"""


def reconciliation_comparison_ctes(
    scope_coverage: str,
    *,
    child_relation: str = "filtered_dataflows",
) -> str:
    """Return the comparison CTEs shared by the summary and Diagnostics findings.

    A filtered child scope exposes ``selected_jobs`` only; a full scope exposes
    ``comparisons`` with one ``eligibility`` per job/metric check.
    """

    child_relation = _reconciliation_relation(child_relation)
    excluded_child_count = (
        "COUNT(*) FILTER (WHERE in_filter = FALSE)"
        if child_relation == "comparison_dataflows"
        else "0::BIGINT"
    )
    if scope_coverage != "full":
        return """
        , selected_jobs AS (
          SELECT DISTINCT _source_id, log_session_id, job_id
          FROM filtered_jobs WHERE job_id IS NOT NULL
        )
        """
    return f"""
    , child_counts AS (
      SELECT _source_id, log_session_id, job_id,
             MAX(event_time) AS latest_child_time,
             COUNT(*) AS total_dataflows,
             COUNT(*) FILTER (WHERE normalized_status = 'failed') AS total_failed,
             COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS total_skipped,
             COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS total_succeeded,
             {excluded_child_count} AS excluded_child_count
      FROM {child_relation}
      WHERE job_id IS NOT NULL
      GROUP BY _source_id, log_session_id, job_id
    ), comparisons AS (
      SELECT j._source_id, j.job_id, j.log_session_id,
             metric, expected, observed,
             CASE WHEN j.normalized_status NOT IN ('succeeded','failed','skipped') THEN 'nonterminal'
                  WHEN j.log_records_dropped IS NULL THEN 'unknown_loss'
                  WHEN j.log_records_dropped > 0 THEN 'lost_detail'
                  WHEN j.observed_at IS NULL THEN 'unknown_snapshot'
                  WHEN j.observed_at < c.latest_child_time THEN 'stale_snapshot'
                  WHEN expected IS NULL THEN 'unknown_total'
                  WHEN COALESCE(c.excluded_child_count, 0) > 0 THEN 'range_incomplete'
                  ELSE 'full' END AS eligibility
      FROM filtered_jobs j
      LEFT JOIN child_counts c ON c._source_id = j._source_id
        AND c.job_id = j.job_id
        AND c.log_session_id IS NOT DISTINCT FROM j.log_session_id
      CROSS JOIN LATERAL (VALUES
        ('total_dataflows', j.total_dataflows, COALESCE(c.total_dataflows, 0)),
        ('total_failed', j.total_failed, COALESCE(c.total_failed, 0)),
        ('total_skipped', j.total_skipped, COALESCE(c.total_skipped, 0)),
        ('total_succeeded', j.total_succeeded, COALESCE(c.total_succeeded, 0))
      ) metrics(metric, expected, observed)
    )
    """


def reconciliation_summary_sql(
    scope_coverage: str,
    *,
    child_relation: str = "filtered_dataflows",
) -> str:
    """Aggregate reconciliation eligibility before the bounded detail LIMIT."""

    ctes = reconciliation_comparison_ctes(scope_coverage, child_relation=child_relation)
    if scope_coverage != "full":
        return f"""{ctes}
        SELECT
          COUNT(*) * 4 AS total_checks_count,
          COUNT(*) AS total_jobs_count,
          0::BIGINT AS comparable_checks_count,
          0::BIGINT AS comparable_jobs_count,
          0::BIGINT AS mismatch_checks_count,
          0::BIGINT AS mismatch_jobs_count,
          COUNT(*) * 4 AS unassessed_checks_count,
          COUNT(*) AS unassessed_jobs_count,
          'partial' AS scope_coverage,
          'filtered_child_scope' AS unassessed_reason
        FROM selected_jobs
        """
    return f"""{ctes}
    SELECT
      COUNT(*) AS total_checks_count,
      COUNT(DISTINCT (_source_id, log_session_id, job_id)) AS total_jobs_count,
      COUNT(*) FILTER (WHERE eligibility = 'full') AS comparable_checks_count,
      COUNT(DISTINCT (_source_id, log_session_id, job_id)) FILTER (WHERE eligibility = 'full') AS comparable_jobs_count,
      COUNT(*) FILTER (WHERE eligibility = 'full' AND expected <> observed) AS mismatch_checks_count,
      COUNT(DISTINCT (_source_id, log_session_id, job_id)) FILTER (WHERE eligibility = 'full' AND expected <> observed) AS mismatch_jobs_count,
      COUNT(*) FILTER (WHERE eligibility <> 'full') AS unassessed_checks_count,
      COUNT(DISTINCT (_source_id, log_session_id, job_id)) FILTER (WHERE eligibility <> 'full') AS unassessed_jobs_count,
      CASE WHEN COUNT(*) FILTER (WHERE eligibility <> 'full') > 0 THEN 'partial' ELSE 'full' END AS scope_coverage,
      NULL::VARCHAR AS unassessed_reason
    FROM comparisons
    """


def reconciliation_metric_summary_sql(
    scope_coverage: str,
    *,
    child_relation: str = "filtered_dataflows",
) -> str:
    """Aggregate reconciliation eligibility by metric before detail limits.

    The registry detail query is intentionally bounded. Diagnostics charts use
    this companion aggregate so a large mismatch population does not make the
    metric breakdown depend on whichever 50 detail rows happen to be returned.
    """

    child_relation = _reconciliation_relation(child_relation)
    excluded_child_count = (
        "COUNT(*) FILTER (WHERE in_filter = FALSE)"
        if child_relation == "comparison_dataflows"
        else "0::BIGINT"
    )
    if scope_coverage != "full":
        return """
        , selected_jobs AS (
          SELECT DISTINCT _source_id, log_session_id, job_id
          FROM filtered_jobs WHERE job_id IS NOT NULL
        ), metrics AS (
          SELECT metric
          FROM (VALUES
            ('total_dataflows'),
            ('total_failed'),
            ('total_skipped'),
            ('total_succeeded')
          ) values_table(metric)
        )
        SELECT
          metrics.metric,
          COUNT(*) AS total_checks_count,
          0::BIGINT AS comparable_checks_count,
          0::BIGINT AS mismatch_count,
          0::BIGINT AS affected_jobs,
          0::BIGINT AS absolute_difference,
          COUNT(*) AS unassessed_checks_count,
          COUNT(*) AS unassessed_jobs_count,
          'info' AS severity
        FROM selected_jobs CROSS JOIN metrics
        GROUP BY metrics.metric
        ORDER BY metrics.metric
        """
    return f"""
    , child_counts AS (
      SELECT _source_id, log_session_id, job_id,
             MAX(event_time) AS latest_child_time,
             {excluded_child_count} AS excluded_child_count,
             COUNT(*) AS total_dataflows,
             COUNT(*) FILTER (WHERE normalized_status = 'failed') AS total_failed,
             COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS total_skipped,
             COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS total_succeeded
      FROM {child_relation}
      WHERE job_id IS NOT NULL
      GROUP BY _source_id, log_session_id, job_id
    ), comparisons AS (
      SELECT j._source_id, j.job_id, j.log_session_id,
             metric, expected, observed,
             CASE WHEN j.normalized_status NOT IN ('succeeded','failed','skipped') THEN 'nonterminal'
                  WHEN j.log_records_dropped IS NULL THEN 'unknown_loss'
                  WHEN j.log_records_dropped > 0 THEN 'lost_detail'
                  WHEN j.observed_at IS NULL THEN 'unknown_snapshot'
                  WHEN j.observed_at < c.latest_child_time THEN 'stale_snapshot'
                  WHEN expected IS NULL THEN 'unknown_total'
                  WHEN COALESCE(c.excluded_child_count, 0) > 0 THEN 'range_incomplete'
                  ELSE 'full' END AS eligibility
      FROM filtered_jobs j
      LEFT JOIN child_counts c ON c._source_id = j._source_id
        AND c.job_id = j.job_id
        AND c.log_session_id IS NOT DISTINCT FROM j.log_session_id
      CROSS JOIN LATERAL (VALUES
        ('total_dataflows', j.total_dataflows, COALESCE(c.total_dataflows, 0)),
        ('total_failed', j.total_failed, COALESCE(c.total_failed, 0)),
        ('total_skipped', j.total_skipped, COALESCE(c.total_skipped, 0)),
        ('total_succeeded', j.total_succeeded, COALESCE(c.total_succeeded, 0))
      ) metrics(metric, expected, observed)
    )
    SELECT
      metric,
      COUNT(*) AS total_checks_count,
      COUNT(*) FILTER (WHERE eligibility = 'full') AS comparable_checks_count,
      COUNT(*) FILTER (WHERE eligibility = 'full' AND expected <> observed) AS mismatch_count,
      COUNT(DISTINCT (_source_id, log_session_id, job_id)) FILTER (
        WHERE eligibility = 'full' AND expected <> observed
      ) AS affected_jobs,
      COALESCE(SUM(ABS(observed - expected)) FILTER (
        WHERE eligibility = 'full' AND expected <> observed
      ), 0) AS absolute_difference,
      COUNT(*) FILTER (WHERE eligibility <> 'full') AS unassessed_checks_count,
      COUNT(DISTINCT (_source_id, log_session_id, job_id)) FILTER (
        WHERE eligibility <> 'full'
      ) AS unassessed_jobs_count,
      CASE WHEN COUNT(*) FILTER (WHERE eligibility = 'full' AND expected <> observed) > 0
           THEN 'warning'
           WHEN COUNT(*) FILTER (WHERE eligibility <> 'full') > 0
           THEN 'info'
           ELSE 'good' END AS severity
    FROM comparisons
    GROUP BY metric
    ORDER BY metric
    """


# Keep a stable module-level name for callers which inspect the SQL contract.
_RECONCILIATION_SQL = reconciliation_sql("full")
