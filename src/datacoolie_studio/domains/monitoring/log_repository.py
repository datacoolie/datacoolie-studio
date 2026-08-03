from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.analytics.errors import AnalyticsRebuildRequired
from datacoolie_studio.domains.analytics.serving_facts import (
    MONITORING_DATAFLOW_FACTS_TABLE,
)
from datacoolie_studio.domains.monitoring.context import (
    reader,
    source_ids as monitoring_source_ids,
)


DATAFLOW_SORT_COLUMNS = {
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
    "volume_est_rows_written": "CASE WHEN lower(COALESCE(d.destination_connection_type, '') || ' ' || COALESCE(d.destination_format, '') || ' ' || COALESCE(d.destination_name, '') || ' ' || COALESCE(d.destination_path, '')) SIMILAR TO '%(lakehouse|delta|iceberg|onelake|deltalake)%' THEN COALESCE(d.destination_rows_written, 0) WHEN lower(COALESCE(d.status, '')) = 'succeeded' THEN COALESCE(d.source_rows_read, d.destination_rows_written, 0) ELSE COALESCE(d.destination_rows_written, 0) END",
    "movement_state": "CASE WHEN NULLIF(CAST(d.source_watermark_after AS VARCHAR), '') IS NULL AND NULLIF(CAST(d.source_watermark_before AS VARCHAR), '') IS NULL THEN 'unknown' WHEN NULLIF(CAST(d.source_watermark_before AS VARCHAR), '') IS NULL THEN 'initialized' WHEN CAST(d.source_watermark_after AS VARCHAR) = CAST(d.source_watermark_before AS VARCHAR) THEN 'unchanged' ELSE 'advanced' END",
    "phase_health": "COALESCE(d.source_status, '') || ' ' || COALESCE(d.transform_status, '') || ' ' || COALESCE(d.destination_status, '')",
    "engine_name": "COALESCE(j.engine_name, 'unknown')",
}

DATAFLOW_CONFIGURE_DERIVED_COLUMNS = {
    "transform_missing_column_policy",
}


JOB_SORT_COLUMNS = {
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
              FROM {analytics_schema.DATAFLOW_TABLE}
              WHERE _source_id IN ({placeholders})
                AND __event_time >= ?
            ),
            jobs AS (
              SELECT
                status,
                engine_name,
                __event_time AS event_time
              FROM {analytics_schema.JOB_TABLE}
              WHERE _source_id IN ({placeholders})
                AND __event_time >= ?
            ),
            jobs_with_dates AS (
              SELECT *, {local_date_sql} AS local_date
              FROM jobs
            ),
            timeline AS (
              SELECT event_time, run_date FROM dataflows
              UNION ALL
              SELECT event_time, CAST(timezone('UTC', event_time) AS DATE) AS run_date FROM jobs
            )
            SELECT
              (SELECT COUNT(*) FROM dataflows) AS dataflow_records,
              (SELECT COUNT(*) FROM jobs_with_dates) AS job_records,
              (SELECT COALESCE(SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END), 0) FROM dataflows) AS dataflow_succeeded,
              (SELECT COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) FROM dataflows) AS dataflow_failed,
              (SELECT COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) FROM jobs_with_dates) AS total_failures,
              (SELECT COUNT(DISTINCT engine_name) FROM jobs_with_dates WHERE engine_name IS NOT NULL AND engine_name <> '') AS active_engines,
              (SELECT COALESCE(SUM(CASE WHEN status = 'failed' AND local_date BETWEEN ? - 7 AND ? THEN 1 ELSE 0 END), 0) FROM jobs_with_dates) AS failed_last7,
              (SELECT COALESCE(SUM(CASE WHEN status = 'failed' AND local_date BETWEEN ? - 30 AND ? THEN 1 ELSE 0 END), 0) FROM jobs_with_dates) AS failed_last30,
              (SELECT COALESCE(SUM(CASE WHEN status = 'failed' AND local_date BETWEEN ? - 365 AND ? THEN 1 ELSE 0 END), 0) FROM jobs_with_dates) AS failed_last365,
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
                local_today,
                local_today,
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
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, str]]] | None:
    """Return one narrow latest row per stable Dataflow identity from DuckDB."""
    del session
    with reader(paths) as (conn, source_ids, _generation):
        if conn is None or not source_ids:
            return [], [], []
        placeholders = ", ".join("?" for _ in source_ids)
        result = conn.execute(
            f"""
            WITH candidates AS (
              SELECT
                CAST(dataflow_id AS VARCHAR) AS dataflow_id,
                CAST(dataflow_name AS VARCHAR) AS dataflow_name,
                CAST(status AS VARCHAR) AS status,
                start_time,
                end_time,
                duration_seconds,
                CAST(dataflow_run_id AS VARCHAR) AS dataflow_run_id,
                CASE
                  WHEN NULLIF(CAST(dataflow_id AS VARCHAR), '') IS NOT NULL
                    THEN 'id:' || CAST(dataflow_id AS VARCHAR)
                  ELSE 'name:' || COALESCE(CAST(dataflow_name AS VARCHAR), '')
                END AS identity_key,
                COALESCE(__event_time, TIMESTAMPTZ '1970-01-01 00:00:00+00') AS event_time
              FROM {analytics_schema.DATAFLOW_TABLE}
              WHERE _source_id IN ({placeholders})
                AND (NULLIF(CAST(dataflow_id AS VARCHAR), '') IS NOT NULL
                  OR NULLIF(CAST(dataflow_name AS VARCHAR), '') IS NOT NULL)
            ), ranked AS (
              SELECT *, row_number() OVER (
                PARTITION BY identity_key
                ORDER BY event_time DESC, COALESCE(dataflow_run_id, '') DESC
              ) AS row_number
              FROM candidates
            )
            SELECT dataflow_id, dataflow_name, status, start_time, end_time,
                   duration_seconds, dataflow_run_id
            FROM ranked
            WHERE row_number = 1
            ORDER BY identity_key
            """,
            source_ids,
        )
        rows = _result_rows(result)
        ambiguous_rows = conn.execute(
            f"""
            SELECT CAST(dataflow_name AS VARCHAR)
            FROM {analytics_schema.DATAFLOW_TABLE}
            WHERE _source_id IN ({placeholders})
              AND NULLIF(CAST(dataflow_name AS VARCHAR), '') IS NOT NULL
              AND NULLIF(CAST(dataflow_id AS VARCHAR), '') IS NOT NULL
            GROUP BY dataflow_name
            HAVING count(DISTINCT CAST(dataflow_id AS VARCHAR)) > 1
            ORDER BY dataflow_name
            """,
            source_ids,
        ).fetchall()
    return rows, [str(row[0]) for row in ambiguous_rows], []


def query_cached_dataflow_logs(
    session: Session,
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    limit: int = 1000,
    offset: int = 0,
    sort_by: str = "start_time",
    sort_dir: str = "desc",
) -> tuple[list[dict[str, Any]], int, list[dict[str, str]]] | None:
    del session
    with reader(paths) as (conn, source_ids, _generation):
        if conn is None or not source_ids:
            return [], 0, []
        dataflow_select_sql = _select_alias_columns(
            "d",
            analytics_schema.table_columns(conn, analytics_schema.DATAFLOW_TABLE),
            exclude=DATAFLOW_CONFIGURE_DERIVED_COLUMNS,
        )
        source_placeholders = ", ".join("?" for _ in source_ids)
        where_sql, params = _monitoring_filter_sql(filters, "d", "j")
        job_lookup_sql = (
            f"""
            SELECT
              _source_id,
              job_id,
              ANY_VALUE(engine_name) AS engine_name,
              ANY_VALUE(metadata_provider_name) AS metadata_provider_name,
              ANY_VALUE(platform_name) AS platform_name,
              ANY_VALUE(status) AS status,
              ANY_VALUE(duration_seconds) AS duration_seconds
            FROM {analytics_schema.JOB_TABLE}
            WHERE _source_id IN ({source_placeholders})
              AND job_id IS NOT NULL
            GROUP BY _source_id, job_id
            """
        )
        from_sql = (
            f"FROM {analytics_schema.DATAFLOW_TABLE} d "
            f"LEFT JOIN ({job_lookup_sql}) j ON j._source_id = d._source_id AND j.job_id = d.job_id "
            f"WHERE d._source_id IN ({source_placeholders}){where_sql}"
        )
        query_params = [*source_ids, *source_ids, *params]
        order_sql = _monitoring_order_sql(sort_by, sort_dir, DATAFLOW_SORT_COLUMNS, default_alias="d")
        result = conn.execute(
            f"""
            SELECT
              {dataflow_select_sql},
              COALESCE(j.engine_name, 'unknown') AS engine_name,
              COALESCE(j.metadata_provider_name, 'unknown') AS metadata_provider_name,
              COALESCE(j.platform_name, 'unknown') AS platform_name,
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
    return rows, total, []


def query_cached_job_logs(
    session: Session,
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    limit: int = 1000,
    offset: int = 0,
    sort_by: str = "start_time",
    sort_dir: str = "desc",
) -> tuple[list[dict[str, Any]], int, list[dict[str, str]]] | None:
    del session
    with reader(paths) as (conn, source_ids, _generation):
        if conn is None or not source_ids:
            return [], 0, []
        job_select_sql = _select_alias_columns("j", analytics_schema.table_columns(conn, analytics_schema.JOB_TABLE))
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
              d._source_id, d.job_id, d.status, d.duration_seconds,
              d.source_rows_read, d.destination_rows_written,
              d.destination_bytes_added, d.destination_bytes_removed
            FROM {MONITORING_DATAFLOW_FACTS_TABLE} d
            WHERE d._source_id IN ({source_placeholders}){child_where_sql}
        """
        child_summary_sql = (
            f"""
            SELECT
              _source_id,
              job_id,
              COUNT(*) AS child_dataflow_count,
              SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS child_succeeded_count,
              SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS child_failed_count,
              SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END) AS child_skipped_count,
              SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS child_running_count,
              SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS child_pending_count,
              quantile_cont(duration_seconds, 0.95) AS child_p95_duration_seconds,
              SUM(source_rows_read) AS child_total_rows_read,
              SUM(destination_rows_written) AS child_total_rows_written,
              SUM(destination_bytes_added) AS child_total_bytes_added,
              SUM(destination_bytes_removed) AS child_total_bytes_removed
            FROM filtered_children
            WHERE job_id IS NOT NULL
            GROUP BY _source_id, job_id
            """
        )
        job_scope_sql = (
            " AND EXISTS ("
            "SELECT 1 FROM filtered_children fc "
            "WHERE fc._source_id = j._source_id AND fc.job_id = j.job_id"
            ")"
            if monitoring_has_dataflow_scope(filters)
            else ""
        )
        from_sql = (
            f"FROM {analytics_schema.JOB_TABLE} j "
            f"LEFT JOIN ({child_summary_sql}) c ON c._source_id = j._source_id AND c.job_id = j.job_id "
            f"WHERE j._source_id IN ({source_placeholders}){where_sql}{job_scope_sql}"
        )
        ctes_sql = f"WITH filtered_children AS ({filtered_children_sql})"
        query_params = [*source_ids, *child_params, *source_ids, *params]
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
              COALESCE(c.child_p95_duration_seconds, 0) AS child_p95_duration_seconds,
              COALESCE(c.child_total_rows_read, 0) AS child_total_rows_read,
              COALESCE(c.child_total_rows_written, 0) AS child_total_rows_written,
              COALESCE(c.child_total_bytes_added, 0) AS child_total_bytes_added,
              COALESCE(c.child_total_bytes_removed, 0) AS child_total_bytes_removed,
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
        "engine": f"COALESCE({job_alias}.engine_name, 'unknown')",
        "provider": f"COALESCE({job_alias}.metadata_provider_name, 'unknown')",
    }.items():
        value = filters.get(key)
        values = _split_filter_values(value)
        if values:
            placeholders = ", ".join("?" for _ in values)
            clauses.append(f"{expression} IN ({placeholders})")
            params.extend(values)

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
                placeholders = ", ".join("?" for _ in values)
                clauses.append(f"{expression} IN ({placeholders})")
                params.extend(values)

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
    value = (filters.get("investigateValue") or "").strip()
    if not kind or not value:
        return "", []
    normalized = value.lower().replace("`", "")
    if include_dataflow_filters:
        return _dataflow_investigation_predicate(row_alias, kind, normalized)
    if kind == "job_id":
        return f"LOWER(COALESCE({row_alias}.job_id::VARCHAR, '')) = ?", [normalized]
    dataflow_predicate, params = _dataflow_investigation_predicate("d2", kind, normalized)
    if not dataflow_predicate:
        return "", []
    return (
        f"{row_alias}.job_id IN ("
        f"SELECT DISTINCT d2.job_id FROM {dataflow_table} d2 "
        f"WHERE d2.job_id IS NOT NULL AND d2._source_id = {row_alias}._source_id AND {dataflow_predicate}"
        f")",
        params,
    )


def _dataflow_investigation_predicate(alias: str, kind: str, normalized_value: str) -> tuple[str, list[Any]]:
    if kind == "job_id":
        return f"LOWER(COALESCE({alias}.job_id::VARCHAR, '')) = ?", [normalized_value]
    if kind == "dataflow_run_id":
        return f"LOWER(COALESCE({alias}.dataflow_run_id::VARCHAR, '')) = ?", [normalized_value]
    if kind == "dataflow":
        return (
            "("
            f"LOWER(COALESCE({alias}.dataflow_id::VARCHAR, '')) = ? OR "
            f"LOWER(COALESCE({alias}.dataflow_name::VARCHAR, '')) = ?"
            ")",
            [normalized_value, normalized_value],
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
            [normalized_value] * 6,
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
    placeholders = ", ".join("?" for _ in values)
    if include_dataflow_filters:
        return (
            f"(COALESCE({row_alias}.source_name, 'unknown') IN ({placeholders}) "
            f"OR COALESCE({row_alias}.destination_name, 'unknown') IN ({placeholders}))",
            [*values, *values],
        )
    return (
        f"{row_alias}.job_id IN ("
        f"SELECT DISTINCT dc.job_id FROM {dataflow_table} dc "
        f"WHERE dc.job_id IS NOT NULL AND dc._source_id = {row_alias}._source_id "
        f"AND (COALESCE(dc.source_name, 'unknown') IN ({placeholders}) "
        f"OR COALESCE(dc.destination_name, 'unknown') IN ({placeholders}))"
        f")",
        [*values, *values],
    )


def _split_filter_values(value: str | None) -> list[str]:
    if not value or value == "all":
        return []
    return [item.strip() for item in value.split("|") if item.strip()]


def monitoring_has_dataflow_scope(filters: dict[str, str]) -> bool:
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
    value = str(filters.get("investigateValue") or "").strip()
    return bool(kind and value and kind != "job_id")


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
        f"{alias}.{_quote_identifier(column)} AS {_quote_identifier(column)}"
        for column in columns
        if column not in excluded
    ]
    return ",\n              ".join(selected) if selected else f"{alias}.*"


def _rebuild_required(paths: list[EnvironmentSource], *, reason: str) -> AnalyticsRebuildRequired:
    source_ids = monitoring_source_ids(paths)
    return AnalyticsRebuildRequired(
        "Monitoring analytics are unavailable; sync the Log sources to rebuild them",
        source_ids=source_ids,
        missing_source_ids=source_ids,
        reason=reason,
    )


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
