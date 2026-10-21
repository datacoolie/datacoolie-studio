from __future__ import annotations

from datacoolie_studio.domains.monitoring.dimensions import dimension_text_sql

from datacoolie_studio.domains.monitoring.dimensions import dimension_predicate_sql

from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.analytics.errors import AnalyticsRebuildRequired
from datacoolie_studio.domains.analytics.record_codec import root_column_types
from datacoolie_studio.domains.analytics.serving_facts import (
    MONITORING_DATAFLOW_FACTS_TABLE,
    MONITORING_JOB_FACTS_TABLE,
    error_field_expression,
    error_truncated_expression,
)
from datacoolie_studio.domains.monitoring.context import (
    reader,
    source_ids as monitoring_source_ids,
)
from datacoolie_studio.domains.monitoring.evidence_hydration import (
    MAX_HYDRATION_ROWS,
    hydrate_rows,
)


DATAFLOW_SORT_COLUMNS = {
    "message": "d.message",
    "end_time": "d.__event_time",
    "start_time": "TRY_CAST(d.start_time AS TIMESTAMPTZ)",
    "duration_seconds": "d.duration_seconds",
    "status": "d.status",
    "dataflow_run_id": "d.dataflow_run_id",
    "dataflow_name": "d.dataflow_name",
    "job_id": "d.job_id",
    "stage": "d.stage",
    "operation_type": "d.operation_type",
    "source_name": "d.source_name",
    "destination_name": "d.destination_name",
    "source_rows_read": "d.source_rows_read",
    "destination_rows_written": "d.destination_rows_written",
    "destination_rows_inserted": "d.destination_rows_inserted",
    "destination_files_added": "d.destination_files_added",
    "destination_bytes_added": "d.destination_bytes_added - COALESCE(d.destination_bytes_removed, 0)",
    "error_message": "COALESCE(d.error_message, d.destination_error_message, d.transform_error_message, d.source_error_message, '')",
    "error_preview": "COALESCE(d.error_message, d.destination_error_message, d.transform_error_message, d.source_error_message, '')",
    "source": "COALESCE(d.source_name, '') || ' ' || COALESCE(d.source_full_table, d.source_table, d.source_path, '')",
    "volume_rows_written": "d.destination_rows_written",
    "movement_state": "NULL",
    "phase_health": "COALESCE(d.source_status, '') || ' ' || COALESCE(d.transform_status, '') || ' ' || COALESCE(d.destination_status, '')",
    "engine_name": "COALESCE(j.engine_name, 'unknown')",
}

JOB_SORT_COLUMNS = {
    "message": "j.message",
    "end_time": "j.__event_time",
    "start_time": "TRY_CAST(j.start_time AS TIMESTAMPTZ)",
    "duration_seconds": "j.duration_seconds",
    "status": "j.status",
    "job_id": "j.job_id",
    "stages": "j.stages",
    "operation_types": "j.operation_types",
    "engine_name": "j.engine_name",
    "metadata_provider_name": "j.metadata_provider_name",
    "total_dataflows": "j.total_dataflows",
    "total_rows_read": "j.total_rows_read",
    "total_rows_written": "j.total_rows_written",
}


def _child_metric_total(column: str, population: str) -> str:
    rows = f"COUNT(*) FILTER (WHERE {population})"
    measured = f"COUNT({column}) FILTER (WHERE {population})"
    observed = f"SUM({column}) FILTER (WHERE {population})"
    return (
        f"CASE WHEN EXISTS (SELECT 1 FROM all_log_versions WHERE log_schema_version >= 7) "
        f"THEN CASE WHEN {measured} = 0 THEN NULL ELSE {observed} END "
        f"WHEN {rows} = 0 THEN CASE WHEN COUNT(*) = 0 THEN 0 ELSE NULL END "
        f"WHEN {measured} = {rows} THEN {observed} END"
    )


def _reader_fence(
    analytics_context: tuple[Any, list[int], str] | None,
    paths: list[EnvironmentSource],
):
    """Keep the repository-owned reader seam patchable in direct tests."""

    return nullcontext(analytics_context) if analytics_context is not None else reader(paths)


def cached_monitoring_summary(
    session: Session,
    paths: list[EnvironmentSource],
    *,
    cutoff: datetime,
    timezone_name: str | None,
    utc_offset_seconds: int | None,
    local_today: date,
) -> tuple[dict[str, Any], list[dict[str, str]]] | None:
    """Aggregate the fixed Environment Overview Monitoring read model in DuckDB.

    A missing or incomplete analytics materialization raises a typed rebuild
    requirement. Request paths never fall back to parsing raw log files.
    """
    del session
    with reader(paths) as (conn, source_ids, _generation):
        if conn is None or not source_ids:
            return {
                "dataflow_records": 0,
                "job_records": 0,
                "dataflow_succeeded": 0,
                "dataflow_failed": 0,
                "total_failures": 0,
                "active_engines": 0,
                "failed_last7": 0,
                "failed_last30": 0,
                "failed_last365": 0,
                "latest_log_at": None,
                "date_min": None,
                "date_max": None,
            }, []
        if timezone_name:
            local_date_sql = "CAST(timezone(?, event_time) AS DATE)"
            local_date_param: str | int = timezone_name
        elif utc_offset_seconds is not None:
            local_date_sql = "CAST(event_time + (? * INTERVAL 1 SECOND) AS DATE)"
            local_date_param = utc_offset_seconds
        else:
            return None
        placeholders = ", ".join("?" for _ in source_ids)
        result = conn.execute(
            f"""
            WITH dataflows AS (
              SELECT
                status,
                __event_time AS event_time,
                COALESCE(__run_date, CAST(timezone('UTC', __event_time) AS DATE)) AS run_date
              FROM {MONITORING_DATAFLOW_FACTS_TABLE}
              WHERE _source_id IN ({placeholders})
                AND __event_time >= ?
            ),
            jobs AS (
              SELECT
                status,
                engine_name,
                __event_time AS event_time
              FROM {MONITORING_JOB_FACTS_TABLE}
              WHERE _source_id IN ({placeholders})
                AND __event_time >= ?
            ),
            failed_job_dates AS (
              -- Failure windows have their own scope, independent of the
              -- 30-day totals and timeline above.
              SELECT {local_date_sql} AS local_date
              FROM (
                SELECT __event_time AS event_time
                FROM {MONITORING_JOB_FACTS_TABLE}
                WHERE _source_id IN ({placeholders}) AND status = 'failed'
              ) failed_jobs
            ),
            failure_windows AS (
              SELECT COUNT(*) FILTER (WHERE local_date >= ? - 7) AS failed_last7,
                     COUNT(*) FILTER (WHERE local_date >= ? - 30) AS failed_last30,
                     COUNT(*) AS failed_last365
              FROM failed_job_dates
              WHERE local_date BETWEEN ? - 365 AND ?
            ),
            timeline AS (
              SELECT event_time, run_date FROM dataflows
              UNION ALL
              SELECT event_time, CAST(timezone('UTC', event_time) AS DATE) AS run_date FROM jobs
            )
            SELECT
              (SELECT COUNT(*) FROM dataflows) AS dataflow_records,
              (SELECT COUNT(*) FROM jobs) AS job_records,
              (SELECT COALESCE(SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END), 0) FROM dataflows) AS dataflow_succeeded,
              (SELECT COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) FROM dataflows) AS dataflow_failed,
              (SELECT COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) FROM jobs) AS total_failures,
              (SELECT COUNT(DISTINCT {dimension_text_sql('engine_name')}) FROM jobs) AS active_engines,
              (SELECT failed_last7 FROM failure_windows) AS failed_last7,
              (SELECT failed_last30 FROM failure_windows) AS failed_last30,
              (SELECT failed_last365 FROM failure_windows) AS failed_last365,
              (SELECT MAX(event_time) FROM timeline) AS latest_log_at,
              (SELECT MIN(run_date) FROM timeline) AS date_min,
              (SELECT MAX(run_date) FROM timeline) AS date_max
            """,
            [
                *source_ids,
                cutoff,
                *source_ids,
                cutoff,
                local_date_param,
                *source_ids,
                local_today,
                local_today,
                local_today,
                local_today,
            ],
        )
        row = result.fetchone()
        columns = [description[0] for description in result.description]

    if row is None:
        raise _rebuild_required(paths, reason="query_failed")
    summary = dict(zip(columns, row))
    return summary, []


def query_cached_latest_dataflow_runs(
    session: Session,
    paths: list[EnvironmentSource],
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, str]]] | None:
    """Return one narrow latest row per stable Dataflow identity from DuckDB."""
    del session
    with _reader_fence(analytics_context, paths) as analytics_fence:
        conn, source_ids, _generation = analytics_fence
        if conn is None or not source_ids:
            return [], [], []
        placeholders = ", ".join("?" for _ in source_ids)
        result = conn.execute(
            f"""
            WITH candidates AS (
              SELECT
                _source_id,
                log_session_id,
                CAST(job_id AS VARCHAR) AS job_id,
                CAST(dataflow_id AS VARCHAR) AS dataflow_id,
                CAST(dataflow_name AS VARCHAR) AS dataflow_name,
                CAST(status AS VARCHAR) AS status,
                start_time,
                end_time,
                duration_seconds,
                CAST(dataflow_run_id AS VARCHAR) AS dataflow_run_id,
                observed_at,
                _file_uri,
                _record_line,
                _file_kind,
                CASE
                  WHEN NULLIF(CAST(dataflow_id AS VARCHAR), '') IS NOT NULL
                    THEN 'id:' || CAST(dataflow_id AS VARCHAR)
                  ELSE 'name:' || COALESCE(CAST(dataflow_name AS VARCHAR), '')
                END AS identity_key,
                COALESCE(__event_time, TIMESTAMPTZ '1970-01-01 00:00:00+00') AS event_time
              FROM {MONITORING_DATAFLOW_FACTS_TABLE}
              WHERE _source_id IN ({placeholders})
                AND (NULLIF(CAST(dataflow_id AS VARCHAR), '') IS NOT NULL
                  OR NULLIF(CAST(dataflow_name AS VARCHAR), '') IS NOT NULL)
            ), ranked AS (
              SELECT *, row_number() OVER (
                PARTITION BY _source_id, log_session_id, identity_key
                ORDER BY COALESCE(observed_at, event_time) DESC NULLS LAST,
                         event_time DESC NULLS LAST,
                         COALESCE(dataflow_run_id, '') DESC,
                         _file_uri DESC NULLS LAST, _record_line DESC NULLS LAST
              ) AS row_number
              FROM candidates
            )
            SELECT _source_id, log_session_id, job_id, dataflow_id, dataflow_name,
                   status, start_time, end_time, duration_seconds, dataflow_run_id,
                   observed_at, _file_uri, _record_line,
                   CAST(_file_kind AS VARCHAR) AS _file_kind,
                   NULL::VARCHAR AS latest_raw_record_json
            FROM ranked
            WHERE row_number = 1
            ORDER BY identity_key
            """,
            source_ids,
        )
        rows = _result_rows(result)
        rows = hydrate_rows(
            rows,
            analytics_fence,
            kind="dataflow_run_log",
            evidence_fields=("latest_raw_record_json",),
            max_rows=MAX_HYDRATION_ROWS,
        )
        ambiguous_rows = conn.execute(
            f"""
            SELECT _source_id, log_session_id, CAST(dataflow_name AS VARCHAR)
            FROM {MONITORING_DATAFLOW_FACTS_TABLE}
            WHERE _source_id IN ({placeholders})
              AND NULLIF(CAST(dataflow_name AS VARCHAR), '') IS NOT NULL
              AND NULLIF(CAST(dataflow_id AS VARCHAR), '') IS NOT NULL
            GROUP BY _source_id, log_session_id, dataflow_name
            HAVING count(DISTINCT CAST(dataflow_id AS VARCHAR)) > 1
            ORDER BY dataflow_name
            """,
            source_ids,
        ).fetchall()
    return rows, [str(row[2]) for row in ambiguous_rows], []


def query_cached_dataflow_logs(
    session: Session,
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    limit: int = 1000,
    offset: int = 0,
    sort_by: str = "start_time",
    sort_dir: str = "desc",
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> tuple[list[dict[str, Any]], int, list[dict[str, str]], str] | None:
    with _reader_fence(analytics_context, paths) as analytics_fence:
        conn, source_ids, generation = analytics_fence
        if conn is None or not source_ids:
            return [], 0, [], generation
        identity_request = _prepare_destination_investigation(session, paths, filters, analytics_fence)
        dataflow_fact_columns = analytics_schema.table_columns(conn, MONITORING_DATAFLOW_FACTS_TABLE)
        dataflow_select_sql = _select_alias_columns(
            "d",
            dataflow_fact_columns,
            exclude=analytics_schema.IGNORED_DATAFLOW_SOURCE_COLUMNS,
        )
        source_placeholders = ", ".join("?" for _ in source_ids)
        where_sql, params = _monitoring_filter_sql(filters, "d", "j")
        job_lookup_sql = (
          f"""
            SELECT _source_id, log_session_id, job_id, engine_name,
                   metadata_provider_name, platform_name, status, duration_seconds
            FROM {MONITORING_JOB_FACTS_TABLE} j
            WHERE _source_id IN ({source_placeholders})
              AND job_id IS NOT NULL
            """
        )
        from_sql = (
            f"FROM {MONITORING_DATAFLOW_FACTS_TABLE} d "
          f"LEFT JOIN ({job_lookup_sql}) j ON j._source_id = d._source_id "
          f"AND j.log_session_id IS NOT DISTINCT FROM d.log_session_id "
          f"AND j.job_id = d.job_id "
            f"WHERE d._source_id IN ({source_placeholders}){where_sql}"
        )
        query_params = [*source_ids, *source_ids, *params]
        order_sql = _monitoring_order_sql(sort_by, sort_dir, DATAFLOW_SORT_COLUMNS, default_alias="d")
        result = conn.execute(
            f"""
            SELECT
              {dataflow_select_sql},
              j.engine_name AS engine_name,
              j.metadata_provider_name AS metadata_provider_name,
              j.platform_name AS platform_name,
              j.status AS job_status,
              j.duration_seconds AS job_duration_seconds,
              COUNT(*) OVER() AS __total_records
            {from_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """,
            [*query_params, limit, offset],
        )
        rows = _result_rows(result)
        total = _window_total(rows)
        if not rows and offset:
            total = int(conn.execute(f"SELECT count(*) {from_sql}", query_params).fetchone()[0])
        rows = hydrate_rows(
            rows,
            analytics_fence,
            kind="dataflow_run_log",
            evidence_fields=("raw_record_json", "latest_raw_record_json", "attempt_record_json"),
            native_fields=(*[field for field in root_column_types("dataflow_run_log")
                            if field not in dataflow_fact_columns],
                           "destination_operation_details", "transformers_applied"),
            max_rows=min(MAX_HYDRATION_ROWS, max(1, int(limit))),
        )
        if identity_request is not None:
            identity_request.assert_current()
    return rows, total, [], generation


def query_cached_job_logs(
    session: Session,
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    limit: int = 1000,
    offset: int = 0,
    sort_by: str = "start_time",
    sort_dir: str = "desc",
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> tuple[list[dict[str, Any]], int, list[dict[str, str]]] | None:
    with _reader_fence(analytics_context, paths) as analytics_fence:
        conn, source_ids, _generation = analytics_fence
        if conn is None or not source_ids:
            return [], 0, []
        identity_request = _prepare_destination_investigation(session, paths, filters, analytics_fence)
        job_fact_columns = analytics_schema.table_columns(conn, MONITORING_JOB_FACTS_TABLE)
        job_select_sql = _select_alias_columns("j", job_fact_columns)
        source_placeholders = ", ".join("?" for _ in source_ids)
        child_where_sql, child_params = _monitoring_filter_sql(
            filters,
            "d",
            "d",
            dataflow_table=MONITORING_DATAFLOW_FACTS_TABLE,
            dataflow_event_time_column="event_time",
        )
        where_sql, params = _monitoring_filter_sql(
            monitoring_job_direct_filters(filters),
            "j",
            "j",
            include_dataflow_filters=False,
            dataflow_table=MONITORING_DATAFLOW_FACTS_TABLE,
        )
        filtered_children_sql = f"""
            SELECT
              d._source_id, d._file_uri, d._record_line, d.log_schema_version, d.log_session_id, d.job_id, d.status, d.duration_seconds, d.event_time,
              d.operation_type,
              d.source_rows_read, d.destination_rows_written,
              d.destination_bytes_added, d.destination_bytes_removed,
              d.destination_operation_type,
              (LOWER(COALESCE(d.operation_type, '')) = 'maintenance'
               OR LOWER(COALESCE(d.destination_operation_type, '')) IN ('compact', 'cleanup', 'maintenance')) AS is_maintenance
            FROM {MONITORING_DATAFLOW_FACTS_TABLE} d
            WHERE d._source_id IN ({source_placeholders}){child_where_sql}
        """
        child_summary_sql = (
            f"""
            SELECT
              _source_id,
              log_session_id,
              job_id,
              COUNT(*) AS child_dataflow_count,
              SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS child_succeeded_count,
              SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS child_failed_count,
              SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END) AS child_skipped_count,
              SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS child_running_count,
              SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS child_pending_count,
              quantile_cont(duration_seconds, 0.95) AS child_p95_duration_seconds,
              MAX(event_time) AS latest_child_time,
              {_child_metric_total('source_rows_read', 'NOT is_maintenance')} AS child_total_rows_read,
              {_child_metric_total('destination_rows_written', 'NOT is_maintenance')} AS child_total_rows_written,
              {_child_metric_total('destination_bytes_added', 'TRUE')} AS child_total_bytes_added,
              {_child_metric_total('destination_bytes_removed', 'TRUE')} AS child_total_bytes_removed,
              COUNT(*) FILTER (WHERE NOT is_maintenance) AS child_metric_eligible_count,
              COUNT(source_rows_read) FILTER (WHERE NOT is_maintenance) AS child_rows_read_measured_count,
              COUNT(destination_rows_written) FILTER (WHERE NOT is_maintenance) AS child_rows_written_measured_count,
              COUNT(destination_bytes_added) AS child_bytes_added_measured_count,
              COUNT(destination_bytes_removed) AS child_bytes_removed_measured_count
            FROM filtered_children
            WHERE job_id IS NOT NULL
            GROUP BY _source_id, log_session_id, job_id
            """
        )
        selected_jobs_sql = f"""
            SELECT j._source_id, j.log_session_id, j.job_id
            FROM {MONITORING_JOB_FACTS_TABLE} j
            WHERE j._source_id IN ({source_placeholders}){where_sql}
        """
        recorded_child_summary_sql = f"""
            SELECT
              d._source_id,
              d.log_session_id,
              d.job_id,
              COUNT(*) AS recorded_child_dataflow_count,
              MAX(d.event_time) AS recorded_latest_child_time,
              COUNT(*) FILTER (WHERE fc._record_line IS NULL) AS excluded_child_dataflow_count
            FROM {MONITORING_DATAFLOW_FACTS_TABLE} d
            INNER JOIN ({selected_jobs_sql}) sj
              ON sj._source_id = d._source_id
             AND sj.log_session_id IS NOT DISTINCT FROM d.log_session_id
             AND sj.job_id = d.job_id
            LEFT JOIN filtered_children fc
              ON fc._source_id = d._source_id
             AND fc.log_session_id IS NOT DISTINCT FROM d.log_session_id
             AND fc.job_id = d.job_id
             AND fc._file_uri = d._file_uri
             AND fc._record_line = d._record_line
            GROUP BY d._source_id, d.log_session_id, d.job_id
        """
        job_scope_sql = (
            " AND EXISTS ("
            "SELECT 1 FROM filtered_children fc "
            "WHERE fc._source_id = j._source_id "
            "AND fc.log_session_id IS NOT DISTINCT FROM j.log_session_id "
            "AND fc.job_id = j.job_id"
            ")"
            if monitoring_requires_child_existence(filters)
            else ""
        )
        from_sql = (
            f"FROM {MONITORING_JOB_FACTS_TABLE} j "
            f"LEFT JOIN ({child_summary_sql}) c ON c._source_id = j._source_id "
            f"AND c.log_session_id IS NOT DISTINCT FROM j.log_session_id "
            f"AND c.job_id = j.job_id "
            f"LEFT JOIN recorded_child_summary rc ON rc._source_id = j._source_id "
            f"AND rc.log_session_id IS NOT DISTINCT FROM j.log_session_id "
            f"AND rc.job_id = j.job_id "
            f"WHERE j._source_id IN ({source_placeholders}){where_sql}{job_scope_sql}"
        )
        ctes_sql = (
            f"WITH all_log_versions AS ("
            f"SELECT log_schema_version FROM {MONITORING_DATAFLOW_FACTS_TABLE} "
            f"WHERE _source_id IN ({source_placeholders}) UNION ALL "
            f"SELECT log_schema_version FROM {MONITORING_JOB_FACTS_TABLE} "
            f"WHERE _source_id IN ({source_placeholders})"
            f"), filtered_children AS ({filtered_children_sql}), "
            f"recorded_child_summary AS ({recorded_child_summary_sql})"
        )
        query_params = [
            *source_ids,
            *source_ids,
            *source_ids,
            *child_params,
            *source_ids,
            *params,
            *source_ids,
            *params,
        ]
        order_sql = _monitoring_order_sql(
            sort_by,
            sort_dir,
            JOB_SORT_COLUMNS,
            default_alias="j",
        )
        result = conn.execute(
            f"""
            {ctes_sql}
            SELECT
              {job_select_sql},
              COALESCE(c.child_dataflow_count, 0) AS child_dataflow_count,
              COALESCE(c.child_succeeded_count, 0) AS child_succeeded_count,
              COALESCE(c.child_failed_count, 0) AS child_failed_count,
              COALESCE(c.child_skipped_count, 0) AS child_skipped_count,
              COALESCE(c.child_running_count, 0) AS child_running_count,
              COALESCE(c.child_pending_count, 0) AS child_pending_count,
              c.child_p95_duration_seconds,
              c.latest_child_time,
              c.child_total_rows_read,
              c.child_total_rows_written,
              c.child_total_bytes_added,
              c.child_total_bytes_removed,
              COALESCE(c.child_metric_eligible_count, 0) AS child_metric_eligible_count,
              c.child_rows_read_measured_count,
              c.child_rows_written_measured_count,
              c.child_bytes_added_measured_count,
              c.child_bytes_removed_measured_count,
              rc.recorded_child_dataflow_count,
              rc.recorded_latest_child_time,
              rc.excluded_child_dataflow_count,
               CASE WHEN '{"subset" if monitoring_requires_comparison_subset(filters) else "full"}' = 'subset' THEN 'subset'
                   WHEN LOWER(COALESCE(j.status::VARCHAR, '')) NOT IN ('succeeded', 'failed', 'skipped') THEN 'nonterminal'
                   WHEN j.total_dataflows IS NULL THEN 'unknown_total'
                   WHEN j.log_records_dropped IS NULL THEN 'unknown_loss'
                   WHEN j.log_records_dropped > 0 THEN 'lost_detail'
                   WHEN j.observed_at IS NULL THEN 'unknown_snapshot'
                   WHEN j.observed_at < rc.recorded_latest_child_time THEN 'stale_snapshot'
                   WHEN COALESCE(rc.excluded_child_dataflow_count, 0) > 0 THEN 'range_incomplete'
                   ELSE 'full' END AS child_scope_coverage,
              COUNT(*) OVER() AS __total_records
            {from_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """,
            [*query_params, limit, offset],
        )
        rows = _result_rows(result)
        total = _window_total(rows)
        if not rows and offset:
            total = int(
                conn.execute(
                    f"{ctes_sql} SELECT count(*) {from_sql}",
                    query_params,
                ).fetchone()[0]
            )
        rows = hydrate_rows(
            rows,
            analytics_fence,
            kind="job_run_log",
            evidence_fields=("raw_record_json", "latest_raw_record_json", "attempt_record_json"),
            native_fields=(*[field for field in root_column_types("job_run_log")
                            if field not in job_fact_columns],),
            max_rows=min(MAX_HYDRATION_ROWS, max(1, int(limit))),
        )
        if identity_request is not None:
            identity_request.assert_current()
    return rows, total, []


def monitoring_filter_sql(
    filters: dict[str, str],
    row_alias: str,
    job_alias: str,
    *,
    include_dataflow_filters: bool = True,
    dataflow_table: str = analytics_schema.DATAFLOW_TABLE,
    dataflow_event_time_column: str = "__event_time",
) -> tuple[str, list[Any]]:
    return _monitoring_filter_sql(
        filters,
        row_alias,
        job_alias,
        include_dataflow_filters=include_dataflow_filters,
        dataflow_table=dataflow_table,
        dataflow_event_time_column=dataflow_event_time_column,
    )


def _monitoring_filter_sql(
    filters: dict[str, str],
    row_alias: str,
    job_alias: str,
    include_dataflow_filters: bool = True,
    dataflow_table: str = analytics_schema.DATAFLOW_TABLE,
    dataflow_event_time_column: str = "__event_time",
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if "investigateSourceId" in filters and filters.get("investigateSourceId") not in (None, ""):
        clauses.append(f"{row_alias}._source_id = ?")
        params.append(int(filters["investigateSourceId"]))

    session_scope = filters.get("log_session_scope", "all")
    if session_scope not in {"all", "null", "exact"}:
        raise ValueError("Unsupported log_session_scope")
    if session_scope == "null":
        clauses.append(f"{row_alias}.log_session_id IS NULL")
    elif session_scope == "exact":
        if "log_session_id" not in filters or filters.get("log_session_id") is None:
            raise ValueError("log_session_id is required for exact session scope")
        session_value = filters.get("log_session_id")
        clauses.append(f"{row_alias}.log_session_id = ?")
        params.append(session_value)

    range_value = filters.get("range")
    if dataflow_event_time_column not in {"__event_time", "event_time"}:
        raise ValueError("Unsupported Monitoring event-time column")
    timestamp_expression = f"{row_alias}.__event_time"
    if include_dataflow_filters:
        timestamp_expression = f"{row_alias}.{dataflow_event_time_column}"
    if range_value in {"24h", "3d", "7d", "30d", "90d"}:
        days = {"24h": 1, "3d": 3, "7d": 7, "30d": 30, "90d": 90}[range_value]
        clauses.append(f"{timestamp_expression} >= ?")
        params.append(
            _parse_filter_datetime(filters.get("_relativeStartTime"))
            or datetime.now(timezone.utc) - timedelta(days=days)
        )
    elif range_value == "custom":
        start_time = _parse_filter_datetime(filters.get("startTime"))
        end_time = _parse_filter_datetime(filters.get("endTime"))
        if start_time is not None:
            clauses.append(f"{timestamp_expression} >= ?")
            params.append(start_time)
        if end_time is not None:
            clauses.append(f"{timestamp_expression} <= ?")
            params.append(end_time)

    for key, expression in {
        "status": f"{row_alias}.status",
        "engine": f"{job_alias}.engine_name",
        "provider": f"{job_alias}.metadata_provider_name",
    }.items():
        value = filters.get(key)
        values = _split_filter_values(value)
        if values:
            predicate, bound = dimension_predicate_sql(expression, values)
            clauses.append(predicate)
            params.extend(bound)

    if include_dataflow_filters:
        for key, expression in {
            "stage": f"{row_alias}.stage",
            "sourceType": f"{row_alias}.source_connection_type",
            "destinationType": f"{row_alias}.destination_connection_type",
            "loadType": f"{row_alias}.destination_load_type",
            "operationType": f"{row_alias}.operation_type",
        }.items():
            value = filters.get(key)
            values = _split_filter_values(value)
            if values:
                predicate, bound = dimension_predicate_sql(expression, values)
                clauses.append(predicate)
                params.extend(bound)

    connection_sql, connection_params = _monitoring_connection_sql(
        filters,
        row_alias,
        include_dataflow_filters,
        dataflow_table,
    )
    if connection_sql:
        clauses.append(connection_sql)
        params.extend(connection_params)

    search = (filters.get("search") or "").strip().lower()
    if search:
        search_columns = (
            [
                f"{row_alias}.job_id",
                f"{row_alias}.dataflow_run_id",
                f"{row_alias}.dataflow_id",
                f"{row_alias}.dataflow_name",
                f"{row_alias}.stage",
                f"{row_alias}.error_message",
                f"{row_alias}.source_name",
                f"{row_alias}.source_full_table",
                f"REPLACE(COALESCE({row_alias}.source_full_table::VARCHAR, ''), '`', '')",
                f"{row_alias}.source_table",
                f"{row_alias}.source_path",
                f"{row_alias}.destination_name",
                f"{row_alias}.destination_full_table",
                f"REPLACE(COALESCE({row_alias}.destination_full_table::VARCHAR, ''), '`', '')",
                f"{row_alias}.destination_table",
                f"{row_alias}.destination_path",
                f"CONCAT(COALESCE({row_alias}.destination_name::VARCHAR, 'unknown'), '::', REPLACE(COALESCE({row_alias}.destination_full_table::VARCHAR, ''), '`', ''))",
                f"COALESCE({job_alias}.engine_name, 'unknown')",
                f"COALESCE({job_alias}.metadata_provider_name, 'unknown')",
            ]
            if include_dataflow_filters
            else [
                f"{row_alias}.job_id",
                f"{row_alias}.engine_name",
                f"{row_alias}.metadata_provider_name",
                f"{row_alias}.status",
                f"{row_alias}.error_message",
            ]
        )
        clauses.append("(" + " OR ".join(f"LOWER(COALESCE(({column})::VARCHAR, '')) LIKE ?" for column in search_columns) + ")")
        params.extend([f"%{search}%"] * len(search_columns))

    investigation_sql, investigation_params = _monitoring_investigation_sql(
        filters,
        row_alias,
        include_dataflow_filters,
        dataflow_table,
    )
    if investigation_sql:
        clauses.append(investigation_sql)
        params.extend(investigation_params)

    return (" AND " + " AND ".join(clauses), params) if clauses else ("", params)


def _monitoring_investigation_sql(
    filters: dict[str, str],
    row_alias: str,
    include_dataflow_filters: bool,
    dataflow_table: str,
) -> tuple[str, list[Any]]:
    kind = (filters.get("investigateKind") or "").strip()
    value = (filters.get("investigateValue") or "")
    if not kind or (value == "" and kind not in {"dataflow_id", "declared_destination", "job_id", "dataflow_run_id"}):
        return "", []
    if include_dataflow_filters:
        return _dataflow_investigation_predicate(row_alias, kind, value)
    if kind == "job_id":
        return f"{row_alias}.job_id::VARCHAR = ?", [value]
    dataflow_predicate, params = _dataflow_investigation_predicate("d2", kind, value)
    if not dataflow_predicate:
        return "", []
    return (
        f"EXISTS (SELECT 1 FROM {dataflow_table} d2 "
        f"WHERE d2.job_id = {row_alias}.job_id AND d2._source_id = {row_alias}._source_id "
        f"AND d2.log_session_id IS NOT DISTINCT FROM {row_alias}.log_session_id AND {dataflow_predicate}"
        f")",
        params,
    )


def _prepare_destination_investigation(session, paths, filters, analytics_context):
    if filters.get('investigateKind') != 'physical_destination':
        return
    from datacoolie_studio.domains.monitoring.identity_context import DestinationIdentityRequest
    if not paths:
        return
    request = DestinationIdentityRequest.capture(session, paths[0].environment_id, analytics_context)
    request.prepare()
    return request


def _dataflow_investigation_predicate(alias: str, kind: str, value: str) -> tuple[str, list[Any]]:
    if kind == "job_id":
        return f"{alias}.job_id::VARCHAR = ?", [value]
    if kind == "dataflow_run_id":
        return f"{alias}.dataflow_run_id::VARCHAR = ?", [value]
    if kind == "dataflow_id":
        return f"{alias}.dataflow_id::VARCHAR = ?", [value]
    if kind == "declared_destination":
        from datacoolie_studio.domains.monitoring.metrics.investigation import declared_destination_key_sql
        return f"{declared_destination_key_sql(alias)} = ?", [value]
    if kind == "physical_destination":
        from datacoolie_studio.domains.assets.endpoint_identity import destination_signature_sql
        from datacoolie_studio.domains.assets.identity_mapping import IDENTITY_MAP_TABLE
        return (f"EXISTS (SELECT 1 FROM {IDENTITY_MAP_TABLE} ai "
                f"WHERE ai.signature={destination_signature_sql(alias)} AND ai.target=?)", [value])
    if kind == "dataflow":
        return (
            "("
            f"{alias}.dataflow_id::VARCHAR = ? OR "
            f"LOWER(COALESCE({alias}.dataflow_name::VARCHAR, '')) = ?"
            ")",
            [value, value.lower().replace("`", "")],
        )
    if kind == "destination_table":
        full_table_expr = f"LOWER(REPLACE(COALESCE({alias}.destination_full_table::VARCHAR, ''), '`', ''))"
        table_expr = f"LOWER(COALESCE({alias}.destination_table::VARCHAR, ''))"
        path_expr = f"LOWER(COALESCE({alias}.destination_path::VARCHAR, ''))"
        connection_expr = f"LOWER(COALESCE({alias}.destination_name::VARCHAR, 'unknown'))"
        return (
            "("
            f"{full_table_expr} = ? OR "
            f"{table_expr} = ? OR "
            f"{path_expr} = ? OR "
            f"CONCAT({connection_expr}, '::', {full_table_expr}) = ? OR "
            f"CONCAT({connection_expr}, '::', {table_expr}) = ? OR "
            f"CONCAT({connection_expr}, '::', {path_expr}) = ?"
            ")",
            [value.lower().replace("`", "")] * 6,
        )
    return "", []


def _monitoring_connection_sql(
    filters: dict[str, str],
    row_alias: str,
    include_dataflow_filters: bool,
    dataflow_table: str = analytics_schema.DATAFLOW_TABLE,
) -> tuple[str, list[Any]]:
    values = _split_filter_values(filters.get("connection"))
    if not values:
        return "", []
    if include_dataflow_filters:
        source, source_params = dimension_predicate_sql(f'{row_alias}.source_name', values)
        destination, destination_params = dimension_predicate_sql(f'{row_alias}.destination_name', values)
        return (
            f"({source} OR {destination})",
            [*source_params, *destination_params],
        )
    source, source_params = dimension_predicate_sql('dc.source_name', values)
    destination, destination_params = dimension_predicate_sql('dc.destination_name', values)
    return (
        f"EXISTS (SELECT 1 FROM {dataflow_table} dc "
        f"WHERE dc.job_id = {row_alias}.job_id AND dc._source_id = {row_alias}._source_id "
        f"AND dc.log_session_id IS NOT DISTINCT FROM {row_alias}.log_session_id "
        f"AND ({source} OR {destination})"
        f")",
        [*source_params, *destination_params],
    )


def _split_filter_values(value: str | None) -> list[str]:
    if not value or value == "all":
        return []
    return [item.strip() for item in value.split("|") if item.strip()]


def monitoring_has_dataflow_scope(filters: dict[str, str]) -> bool:
    # Selecting a nullable or exact session narrows the population even when
    # no stage/connection/investigation predicate is present.  Reconciliation
    # must therefore report subset coverage for either explicit session scope.
    if filters.get("log_session_scope", "all") != "all":
        return True
    if any(
        _split_filter_values(filters.get(name))
        for name in (
            "stage",
            "connection",
            "sourceType",
            "destinationType",
            "loadType",
            "operationType",
            "status",
            "engine",
            "provider",
        )
    ):
        return True
    if (filters.get("search") or "").strip():
        return True
    kind = str(filters.get("investigateKind") or "").strip()
    value = str(filters.get("investigateValue") or "")
    return bool(kind and kind != "job_id" and (value or kind in {"dataflow_id", "dataflow_run_id", "declared_destination"}))


def monitoring_requires_child_existence(filters: dict[str, str]) -> bool:
    """Whether a Job population must have a matching dataflow child.

    Session-only and exact ``job_id`` navigation remain valid for a job that
    reports zero children.  Child dimensions and dataflow identity filters do
    require an actual child match, so they keep the existing scoped behavior.
    """

    if any(
        _split_filter_values(filters.get(name))
        for name in (
            "stage",
            "connection",
            "sourceType",
            "destinationType",
            "loadType",
            "operationType",
        )
    ):
        return True
    kind = str(filters.get("investigateKind") or "").strip()
    if kind == "job_id":
        return False
    return bool(
        kind
        and kind != "job_id"
        and (
            filters.get("investigateValue")
            or kind in {"dataflow_id", "dataflow_run_id", "declared_destination"}
        )
    )


def monitoring_requires_comparison_subset(filters: dict[str, str]) -> bool:
    """Whether the selected child rows are a subset of a job's children.

    This is intentionally separate from :func:`monitoring_requires_child_existence`.
    A status, engine, provider, or search filter can narrow the child rows used
    for a comparison, but it must not make a valid zero-child job disappear from
    the Jobs population. Session selection and the time range are also not
    subset markers here: reconciliation compares the selected job's whole
    recorded child population and reports excluded time-window children
    explicitly.
    """

    return bool(monitoring_comparison_subset_restrictions(filters))


def monitoring_comparison_subset_restrictions(filters: dict[str, str]) -> list[str]:
    """Name the active filters that make child rows a subset of a job's children."""

    restrictions = [
        name
        for name in (
            "stage",
            "connection",
            "sourceType",
            "destinationType",
            "loadType",
            "operationType",
            "status",
            "engine",
            "provider",
        )
        if _split_filter_values(filters.get(name))
    ]
    if (filters.get("search") or "").strip():
        restrictions.append("search")
    kind = str(filters.get("investigateKind") or "").strip()
    value = str(filters.get("investigateValue") or "")
    if (
        kind
        and kind != "job_id"
        and (
            value
            or kind in {"dataflow_id", "dataflow_run_id", "declared_destination"}
        )
    ):
        restrictions.append("investigate")
    return restrictions


def monitoring_job_direct_filters(filters: dict[str, str]) -> dict[str, str]:
    direct_filters = dict(filters)
    direct_filters["connection"] = "all"
    if str(direct_filters.get("investigateKind") or "").strip() != "job_id":
        direct_filters["investigateKind"] = ""
        direct_filters["investigateValue"] = ""
    return direct_filters


def _monitoring_order_sql(
    sort_by: str,
    sort_dir: str,
    allowed_columns: dict[str, str],
    default_alias: str,
) -> str:
    expression = allowed_columns.get(sort_by) or allowed_columns["start_time"]
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"
    fallback_time = f"{default_alias}.__event_time"
    stable_identity = (
        f"COALESCE({default_alias}.dataflow_run_id, {default_alias}.dataflow_id, {default_alias}.job_id)"
        if default_alias == "d"
        else f"{default_alias}.job_id"
    )
    return f"{expression} {direction} NULLS LAST, {fallback_time} DESC NULLS LAST, {stable_identity} DESC NULLS LAST"


def _parse_filter_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _result_rows(result) -> list[dict[str, Any]]:
    names = [desc[0] for desc in result.description]
    temporal_indexes = {
        index
        for index, desc in enumerate(result.description)
        if str(desc[1]).startswith(("DATE", "TIMESTAMP"))
    }
    return [
        {
            name: (value.isoformat() if index in temporal_indexes and value is not None else value)
            for index, (name, value) in enumerate(zip(names, values))
        }
        for values in result.fetchall()
    ]


def _window_total(rows: list[dict[str, Any]]) -> int:
    total = int(rows[0].get("__total_records") or 0) if rows else 0
    for row in rows:
        row.pop("__total_records", None)
    return total


def _select_alias_columns(alias: str, columns: list[str], exclude: set[str] | None = None) -> str:
    excluded = exclude or set()
    selected = [
        f"{_record_column_expression(alias, column, columns)} AS {_quote_identifier(column)}"
        for column in columns
        if column not in excluded
    ]
    # Transitional views may be missing one of the provenance aliases while a
    # schema rebuild is in progress.  Keep the query shape stable and make the
    # absence explicit so the response hydrator can safely leave its marker
    # empty instead of guessing a source row.
    provenance_types = {
        "_source_id": "BIGINT",
        "_file_uri": "VARCHAR",
        "_record_line": "BIGINT",
        "_file_kind": "VARCHAR",
    }
    available = set(columns)
    for column, sql_type in provenance_types.items():
        if column not in available and column not in excluded:
            selected.append(f"CAST(NULL AS {sql_type}) AS {_quote_identifier(column)}")
    return ",\n              ".join(selected) if selected else f"{alias}.*"


def _rebuild_required(paths: list[EnvironmentSource], *, reason: str) -> AnalyticsRebuildRequired:
    source_ids = monitoring_source_ids(paths)
    return AnalyticsRebuildRequired(
        "Monitoring analytics are unavailable; sync the Log sources to rebuild them",
        source_ids=source_ids,
        missing_source_ids=source_ids,
        reason=reason,
    )


def _record_column_expression(alias: str, column: str, columns: list[str]) -> str:
    if column in {"error_message", "source_error_message", "transform_error_message", "destination_error_message"}:
        return error_field_expression(alias, column, columns)
    if column == "error_message_truncated":
        return error_truncated_expression(alias, columns)
    return f"{alias}.{_quote_identifier(column)}"


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
