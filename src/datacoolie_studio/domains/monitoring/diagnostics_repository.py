from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.analytics.schema import DATAFLOW_TABLE, JOB_TABLE
from datacoolie_studio.domains.logs.cache import monitoring_filter_sql
from datacoolie_studio.domains.monitoring.context import reader as analytics_reader


_FIELD_GROUPS = (
    ("identity/linkage", "dataflow", ("job_id", "dataflow_id", "dataflow_run_id", "dataflow_name"), "universal"),
    ("time/status", "dataflow", ("status", "start_time", "end_time"), "universal"),
    ("runtime duration", "dataflow", ("duration_seconds", "source_duration_seconds", "transform_duration_seconds", "destination_duration_seconds"), "universal"),
    ("source evidence", "dataflow", ("source_name", "source_connection_type", "source_rows_read"), "universal"),
    ("destination evidence", "dataflow", ("destination_name", "destination_connection_type", "destination_load_type"), "universal"),
    ("watermark evidence", "dataflow", ("source_watermark_columns", "source_watermark_before", "source_watermark_after"), "conditional"),
    ("maintenance evidence", "dataflow", ("destination_operation_type", "destination_files_removed", "destination_bytes_removed"), "conditional"),
    ("identity/linkage", "job", ("job_id",), "universal"),
    ("time/status", "job", ("status", "start_time", "end_time"), "universal"),
    ("runtime duration", "job", ("duration_seconds",), "universal"),
    ("job totals", "job", ("total_dataflows", "total_succeeded", "total_failed", "total_skipped"), "universal"),
    ("runtime context", "job", ("engine_name", "metadata_provider_name", "platform_name"), "universal"),
)


def diagnostics_read_model(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    *,
    grain: str,
    timezone_name: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    """Build the Diagnostics Monitoring payload from bounded DuckDB aggregates.

    Diagnostics is intentionally assembled from summary, linkage, and evidence
    aggregates. No request transfers the full dataflow/job fact set into Python.
    """
    if analytics_context is not None:
        return _diagnostics_read_model_from_connection(
            analytics_context[0], analytics_context[1], analytics_context[2], filters, grain, timezone_name,
        )
    with analytics_reader(paths) as (conn, source_ids, generation):
        return _diagnostics_read_model_from_connection(
            conn, source_ids, generation, filters, grain, timezone_name,
        )


def _diagnostics_read_model_from_connection(
    conn: Any,
    source_ids: list[int],
    generation: str,
    filters: dict[str, str],
    grain: str,
    timezone_name: str,
) -> dict[str, Any]:
    if conn is None or not source_ids:
        return _empty_read_model(generation)

    ctes, params = _diagnostics_ctes(source_ids, filters)
    summary = _single_row(conn.execute(_summary_sql(ctes), params))
    effective_grain = _effective_grain(filters, summary, grain)
    linkage_counts = _linkage_counts(conn, ctes, params)
    linkage_evidence = (
        _linkage_evidence(conn, ctes, params)
        if any(linkage_counts.get(category) for category in ("orphan_dataflow_job_id", "job_without_dataflow_records"))
        else []
    )
    reconciliation = _reconciliation(conn, ctes, params)
    field_completeness = _field_completeness(conn, ctes, params)
    source_coverage = _source_coverage(conn, ctes, params)
    record_evidence = _record_evidence(conn, ctes, params, effective_grain, timezone_name)

    dataflow_records = int(summary.get("dataflow_records") or 0)
    job_records = int(summary.get("job_records") or 0)
    matched_count = int(linkage_counts.get("matched") or 0)
    orphan_count = int(linkage_counts.get("orphan_dataflow_job_id") or 0)
    job_only_count = int(linkage_counts.get("job_without_dataflow_records") or 0)
    union_count = matched_count + orphan_count + job_only_count
    field_issues = sum(
        1
        for item in field_completeness
        if item["actionable"] and float(item["completeness_rate"]) < 95
    )
    required_field_groups = [item for item in field_completeness if item["actionable"]]
    field_readiness_rate = _rate(
        sum(float(item["present_values"]) for item in required_field_groups),
        sum(float(item["records"]) * float(item["required_fields"]) for item in required_field_groups),
    )
    checks = reconciliation["checks"]
    reconciliation_by_metric = _reconciliation_by_metric(checks)
    health_status = _health_status(
        dataflow_records=dataflow_records,
        job_records=job_records,
        orphan_job_ids=orphan_count,
        jobs_without_dataflow_records=job_only_count,
        reconciliation_mismatches=int(reconciliation["mismatch_count"]),
    )
    diagnostics = {
        "kpis": {
            "health_status": health_status,
            "matched_job_ids": matched_count,
            "orphan_dataflow_job_ids": orphan_count,
            "jobs_without_dataflow_records": job_only_count,
            "job_linkage_rate": _rate(matched_count, union_count),
            "reconciliation_mismatches": int(reconciliation["mismatch_count"]),
            "affected_reconciliation_jobs": len({str(item["job_id"]) for item in checks}),
            "read_errors": 0,
            "cache_warning_count": 0,
            "field_readiness_rate": field_readiness_rate,
            "field_readiness_issues": field_issues,
            "conditional_evidence_groups": sum(1 for item in field_completeness if item["applicability"] == "conditional"),
        },
        "record_evidence_by_date": record_evidence,
        "job_linkage_summary": _linkage_summary(linkage_counts, union_count),
        "reconciliation_by_metric": reconciliation_by_metric,
        "field_completeness": field_completeness,
        "source_coverage": source_coverage,
        "investigation_queue": _investigation_queue(
            linkage_evidence,
            reconciliation,
            field_completeness,
        ),
        "read_errors": [],
    }
    coverage = {
        "dataflow_records": dataflow_records,
        "job_records": job_records,
        "linked_job_ids": matched_count,
        "dataflow_job_ids": matched_count + orphan_count,
        "job_ids": matched_count + job_only_count,
        "orphan_dataflow_job_ids": orphan_count,
        "jobs_without_dataflow_records": job_only_count,
        "read_errors": 0,
        "status": _coverage_status(dataflow_records, job_records),
    }
    return {
        "generation": generation,
        "effective_grain": effective_grain,
        "summary": summary,
        "coverage": coverage,
        "reconciliation": reconciliation,
        "diagnostics": diagnostics,
    }


def _empty_read_model(generation: str) -> dict[str, Any]:
    return {
        "generation": generation,
        "effective_grain": "day",
        "summary": {
            "dataflow_records": 0,
            "job_records": 0,
            "date_min": None,
            "date_max": None,
            "latest_log_at": None,
            "earliest_log_at": None,
            "latest_job_log_at": None,
            "latest_dataflow_log_at": None,
            "active_engines": 0,
            "active_metadata_providers": 0,
        },
        "coverage": {
            "dataflow_records": 0,
            "job_records": 0,
            "linked_job_ids": 0,
            "dataflow_job_ids": 0,
            "job_ids": 0,
            "orphan_dataflow_job_ids": 0,
            "jobs_without_dataflow_records": 0,
            "read_errors": 0,
            "status": "no_records",
        },
        "reconciliation": {"status": "ok", "mismatch_count": 0, "checks": []},
        "diagnostics": {
            "kpis": {
                "health_status": "no_evidence",
                "matched_job_ids": 0,
                "orphan_dataflow_job_ids": 0,
                "jobs_without_dataflow_records": 0,
                "job_linkage_rate": 0,
                "reconciliation_mismatches": 0,
                "affected_reconciliation_jobs": 0,
                "read_errors": 0,
                "cache_warning_count": 0,
                "field_readiness_rate": 0,
                "field_readiness_issues": 0,
                "conditional_evidence_groups": 0,
            },
            "record_evidence_by_date": [],
            "job_linkage_summary": _linkage_summary({}, 0),
            "reconciliation_by_metric": [],
            "field_completeness": [],
            "source_coverage": [],
            "investigation_queue": [],
            "read_errors": [],
        },
    }


def _diagnostics_ctes(source_ids: list[int], filters: dict[str, str]) -> tuple[str, list[Any]]:
    placeholders = ", ".join("?" for _ in source_ids)
    dataflow_where, dataflow_params = monitoring_filter_sql(filters, "d", "jl")
    job_where, job_params = monitoring_filter_sql(filters, "j", "j", include_dataflow_filters=False)
    job_scope = (
        " AND EXISTS (SELECT 1 FROM filtered_dataflows df "
        "WHERE df._source_id = j._source_id AND df.raw_job_id = j.job_id)"
        if _has_dataflow_scope(filters)
        else ""
    )
    normalized_job_id = """
        CASE
          WHEN lower(trim(COALESCE(CAST({alias}.job_id AS VARCHAR), ''))) IN ('', 'none', 'null', 'nan', 'unknown') THEN NULL
          ELSE trim(CAST({alias}.job_id AS VARCHAR))
        END
    """
    sql = f"""
        WITH job_lookup AS (
          SELECT _source_id, job_id,
                 ANY_VALUE(engine_name) AS engine_name,
                 ANY_VALUE(metadata_provider_name) AS metadata_provider_name,
                 ANY_VALUE(platform_name) AS platform_name
          FROM {JOB_TABLE}
          WHERE _source_id IN ({placeholders}) AND job_id IS NOT NULL
          GROUP BY _source_id, job_id
        ),
        filtered_dataflows AS (
          SELECT
            d._source_id, d._file_uri, d._file_kind, d._ingested_at,
            d.job_id AS raw_job_id,
            {normalized_job_id.format(alias='d')} AS job_id,
            d.dataflow_id, d.dataflow_run_id, d.dataflow_name,
            lower(COALESCE(NULLIF(CAST(d.status AS VARCHAR), ''), 'unknown')) AS status,
            d.start_time, d.end_time,
            COALESCE(d.end_time, d.start_time) AS event_time,
            COALESCE(d.__run_date, CAST(timezone('UTC', COALESCE(d.end_time, d.start_time)) AS DATE)) AS run_date,
            d.duration_seconds, d.source_duration_seconds, d.transform_duration_seconds,
            d.destination_duration_seconds, d.source_name, d.source_connection_type,
            d.source_rows_read, d.destination_name, d.destination_connection_type,
            d.destination_load_type, d.source_watermark_columns,
            d.source_watermark_before, d.source_watermark_after,
            d.destination_operation_type, d.destination_files_removed,
            d.destination_bytes_removed
          FROM {DATAFLOW_TABLE} d
          LEFT JOIN job_lookup jl ON jl._source_id = d._source_id AND jl.job_id = d.job_id
          WHERE d._source_id IN ({placeholders}){dataflow_where}
        ),
        filtered_jobs AS (
          SELECT
            j._source_id, j._file_uri, j._file_kind, j._ingested_at,
            {normalized_job_id.format(alias='j')} AS job_id,
            lower(COALESCE(NULLIF(CAST(j.status AS VARCHAR), ''), 'unknown')) AS status,
            TRY_CAST(j.start_time AS TIMESTAMPTZ) AS start_time,
            TRY_CAST(j.end_time AS TIMESTAMPTZ) AS end_time,
            j.__event_time AS event_time,
            j.duration_seconds, j.engine_name, j.metadata_provider_name, j.platform_name,
            j.total_dataflows, j.total_succeeded, j.total_failed, j.total_skipped
          FROM {JOB_TABLE} j
          WHERE j._source_id IN ({placeholders}){job_where}{job_scope}
        )
    """
    return sql, [*source_ids, *source_ids, *dataflow_params, *source_ids, *job_params]


def _summary_sql(ctes: str) -> str:
    return f"""
        {ctes}
        SELECT
          (SELECT COUNT(*) FROM filtered_dataflows) AS dataflow_records,
          (SELECT COUNT(*) FROM filtered_jobs) AS job_records,
          (SELECT MIN(run_date) FROM (
            SELECT run_date FROM filtered_dataflows
            UNION ALL
            SELECT CAST(timezone('UTC', event_time) AS DATE) AS run_date FROM filtered_jobs
          )) AS date_min,
          (SELECT MAX(run_date) FROM (
            SELECT run_date FROM filtered_dataflows
            UNION ALL
            SELECT CAST(timezone('UTC', event_time) AS DATE) AS run_date FROM filtered_jobs
          )) AS date_max,
          (SELECT MAX(event_time) FROM (SELECT event_time FROM filtered_dataflows UNION ALL SELECT event_time FROM filtered_jobs)) AS latest_log_at,
          (SELECT MIN(event_time) FROM (SELECT event_time FROM filtered_dataflows UNION ALL SELECT event_time FROM filtered_jobs)) AS earliest_log_at,
          (SELECT MAX(event_time) FROM filtered_jobs) AS latest_job_log_at,
          (SELECT MAX(event_time) FROM filtered_dataflows) AS latest_dataflow_log_at,
          (SELECT COUNT(DISTINCT engine_name) FROM filtered_jobs WHERE engine_name IS NOT NULL AND engine_name <> '') AS active_engines,
          (SELECT COUNT(DISTINCT metadata_provider_name) FROM filtered_jobs WHERE metadata_provider_name IS NOT NULL AND metadata_provider_name <> '') AS active_metadata_providers
    """


def _linkage_ctes(ctes: str) -> str:
    return f"""
        {ctes},
        dataflow_by_job AS (
          SELECT job_id, COUNT(*) AS dataflow_records, MAX(event_time) AS latest_dataflow_time
          FROM filtered_dataflows WHERE job_id IS NOT NULL GROUP BY job_id
        ),
        jobs_by_id AS (
          SELECT job_id, ANY_VALUE(status) AS job_status,
                 ANY_VALUE(total_dataflows) AS job_total_dataflows,
                 MAX(event_time) AS latest_job_time
          FROM filtered_jobs WHERE job_id IS NOT NULL GROUP BY job_id
        ),
        linkage AS (
          SELECT
            COALESCE(df.job_id, j.job_id) AS job_id,
            df.dataflow_records,
            df.latest_dataflow_time,
            j.job_status,
            j.job_total_dataflows,
            j.latest_job_time,
            CASE
              WHEN df.job_id IS NOT NULL AND j.job_id IS NOT NULL THEN 'matched'
              WHEN df.job_id IS NOT NULL THEN 'orphan_dataflow_job_id'
              ELSE 'job_without_dataflow_records'
            END AS category
          FROM dataflow_by_job df
          FULL OUTER JOIN jobs_by_id j ON j.job_id = df.job_id
        )
    """


def _linkage_counts(conn: Any, ctes: str, params: list[Any]) -> dict[str, int]:
    result = conn.execute(
        f"""
        {_linkage_ctes(ctes)}
        SELECT category, COUNT(*) AS count FROM linkage GROUP BY category
        """,
        params,
    )
    return {str(row["category"]): int(row["count"] or 0) for row in _rows(result)}


def _linkage_evidence(conn: Any, ctes: str, params: list[Any]) -> list[dict[str, Any]]:
    result = conn.execute(
        f"""
        {_linkage_ctes(ctes)},
        ranked AS (
          SELECT *, row_number() OVER (PARTITION BY category ORDER BY job_id) AS row_number
          FROM linkage
          WHERE category <> 'matched'
        )
        SELECT category, job_id, job_status, dataflow_records, job_total_dataflows,
               COALESCE(latest_dataflow_time, latest_job_time) AS latest_time
        FROM ranked
        WHERE row_number <= 200
        ORDER BY CASE category
          WHEN 'orphan_dataflow_job_id' THEN 0
          ELSE 2
        END, job_id
        """,
        params,
    )
    evidence = []
    for row in _rows(result):
        category = str(row["category"])
        evidence.append({
            "category": category,
            "job_id": row["job_id"],
            "job_status": row["job_status"] if category != "orphan_dataflow_job_id" else "missing_job_log",
            "dataflow_records": int(row["dataflow_records"] or 0),
            "job_total_dataflows": int(row["job_total_dataflows"]) if row.get("job_total_dataflows") is not None else None,
            "latest_time": row["latest_time"],
        })
    return evidence


def _reconciliation(conn: Any, ctes: str, params: list[Any]) -> dict[str, Any]:
    query = f"""
        {ctes},
        dataflow_rollups AS (
          SELECT
            job_id,
            COUNT(*) AS total_dataflows,
            COUNT(*) FILTER (WHERE status = 'failed') AS total_failed,
            COUNT(*) FILTER (WHERE status = 'skipped') AS total_skipped,
            COUNT(*) FILTER (WHERE status = 'succeeded') AS total_succeeded
          FROM filtered_dataflows
          WHERE job_id IS NOT NULL
          GROUP BY job_id
        ),
        checks AS (
          SELECT j.job_id, 'total_dataflows' AS metric, j.total_dataflows AS expected,
                 COALESCE(d.total_dataflows, 0) AS observed
          FROM filtered_jobs j LEFT JOIN dataflow_rollups d USING (job_id)
          WHERE j.job_id IS NOT NULL AND j.total_dataflows IS NOT NULL
            AND j.total_dataflows <> COALESCE(d.total_dataflows, 0)
          UNION ALL
          SELECT j.job_id, 'total_failed', j.total_failed, COALESCE(d.total_failed, 0)
          FROM filtered_jobs j LEFT JOIN dataflow_rollups d USING (job_id)
          WHERE j.job_id IS NOT NULL AND j.total_failed IS NOT NULL
            AND j.total_failed <> COALESCE(d.total_failed, 0)
          UNION ALL
          SELECT j.job_id, 'total_skipped', j.total_skipped, COALESCE(d.total_skipped, 0)
          FROM filtered_jobs j LEFT JOIN dataflow_rollups d USING (job_id)
          WHERE j.job_id IS NOT NULL AND j.total_skipped IS NOT NULL
            AND j.total_skipped <> COALESCE(d.total_skipped, 0)
          UNION ALL
          SELECT j.job_id, 'total_succeeded', j.total_succeeded, COALESCE(d.total_succeeded, 0)
          FROM filtered_jobs j LEFT JOIN dataflow_rollups d USING (job_id)
          WHERE j.job_id IS NOT NULL AND j.total_succeeded IS NOT NULL
            AND j.total_succeeded <> COALESCE(d.total_succeeded, 0)
        )
    """
    result = conn.execute(
        f"""
        {query}
        SELECT job_id, metric, expected, observed, observed - expected AS difference,
               COUNT(*) OVER () AS mismatch_count
        FROM checks
        ORDER BY job_id, metric
        LIMIT 50
        """,
        params,
    )
    rows = _rows(result)
    checks = [
        {
            "severity": "warning",
            "job_id": row["job_id"],
            "metric": row["metric"],
            "expected": int(row["expected"]),
            "observed": int(row["observed"]),
            "difference": int(row["difference"]),
        }
        for row in rows
    ]
    mismatch_count = int(rows[0]["mismatch_count"] or 0) if rows else 0
    return {"status": "warning" if mismatch_count else "ok", "mismatch_count": mismatch_count, "checks": checks}


def _field_completeness(conn: Any, ctes: str, params: list[Any]) -> list[dict[str, Any]]:
    dataflow_fields = tuple(field for _, record_type, fields, _ in _FIELD_GROUPS if record_type == "dataflow" for field in fields)
    job_fields = tuple(field for _, record_type, fields, _ in _FIELD_GROUPS if record_type == "job" for field in fields)
    dataflow_counts = _presence_counts(conn, ctes, params, "filtered_dataflows", dataflow_fields)
    job_counts = _presence_counts(conn, ctes, params, "filtered_jobs", job_fields)
    counts_by_type = {"dataflow": dataflow_counts, "job": job_counts}
    result = []
    for group, record_type, fields, applicability in _FIELD_GROUPS:
        counts = counts_by_type[record_type]
        records = int(counts.get("records") or 0)
        present = sum(int(counts.get(field) or 0) for field in fields)
        total_slots = records * len(fields)
        completeness = _rate(present, total_slots)
        result.append({
            "group": group,
            "record_type": record_type,
            "fields": ", ".join(fields),
            "records": records,
            "required_fields": len(fields),
            "present_values": present,
            "missing_values": max(0, total_slots - present),
            "completeness_rate": completeness,
            "severity": _completeness_severity(completeness, records),
            "applicability": applicability,
            "actionable": applicability == "universal",
        })
    return result


def _presence_counts(conn: Any, ctes: str, params: list[Any], table: str, fields: tuple[str, ...]) -> dict[str, Any]:
    expressions = ",\n".join(
        f"SUM(CASE WHEN {field} IS NULL OR CAST({field} AS VARCHAR) = '' THEN 0 ELSE 1 END) AS {field}"
        for field in dict.fromkeys(fields)
    )
    return _single_row(conn.execute(f"{ctes} SELECT COUNT(*) AS records, {expressions} FROM {table}", params))


def _source_coverage(conn: Any, ctes: str, params: list[Any]) -> list[dict[str, Any]]:
    result = conn.execute(
        f"""
        {ctes},
        source_records AS (
          SELECT _source_id, MAX(_file_kind) AS file_kind,
                 COUNT(*) AS dataflow_records, 0::BIGINT AS job_records,
                 MAX(event_time) AS latest_log_at, MAX(_ingested_at) AS latest_ingested_at
          FROM filtered_dataflows GROUP BY _source_id
          UNION ALL
          SELECT _source_id, MAX(_file_kind),
                 0::BIGINT, COUNT(*), MAX(event_time), MAX(_ingested_at)
          FROM filtered_jobs GROUP BY _source_id
        ),
        source_files AS (
          SELECT _source_id, _file_uri FROM filtered_dataflows WHERE _file_uri IS NOT NULL
          UNION
          SELECT _source_id, _file_uri FROM filtered_jobs WHERE _file_uri IS NOT NULL
        ),
        source_file_counts AS (
          SELECT _source_id, COUNT(*) AS file_count FROM source_files GROUP BY _source_id
        )
        SELECT sr._source_id, ANY_VALUE(sr.file_kind) AS file_kind,
               COALESCE(ANY_VALUE(sf.file_count), 0) AS file_count,
               SUM(sr.dataflow_records) AS dataflow_records, SUM(sr.job_records) AS job_records,
               MAX(sr.latest_log_at) AS latest_log_at, MAX(sr.latest_ingested_at) AS latest_ingested_at
        FROM source_records sr
        LEFT JOIN source_file_counts sf ON sf._source_id = sr._source_id
        GROUP BY sr._source_id
        ORDER BY SUM(sr.dataflow_records) + SUM(sr.job_records) DESC, sr._source_id
        """,
        params,
    )
    coverage = []
    for row in _rows(result):
        dataflow_records = int(row["dataflow_records"] or 0)
        job_records = int(row["job_records"] or 0)
        coverage.append({
            "source": f"source:{int(row['_source_id'])}",
            "source_id": int(row["_source_id"]),
            "file_kind": row["file_kind"] or "unknown",
            "file_count": int(row["file_count"] or 0),
            "job_records": job_records,
            "dataflow_records": dataflow_records,
            "records": dataflow_records + job_records,
            "latest_log_at": row["latest_log_at"],
            "latest_ingested_at": row["latest_ingested_at"],
            "warning_count": 0,
            "status": "ok",
        })
    return coverage


def _record_evidence(conn: Any, ctes: str, params: list[Any], grain: str, timezone_name: str) -> list[dict[str, Any]]:
    result = conn.execute(
        f"""
        {ctes},
        dataflow_counts AS (
          SELECT date_trunc(?, timezone(?, event_time)) AS bucket, COUNT(*) AS dataflow_records
          FROM filtered_dataflows GROUP BY bucket
        ),
        job_counts AS (
          SELECT date_trunc(?, timezone(?, event_time)) AS bucket, COUNT(*) AS job_records
          FROM filtered_jobs GROUP BY bucket
        ),
        dataflow_ids AS (
          SELECT DISTINCT date_trunc(?, timezone(?, event_time)) AS bucket, job_id
          FROM filtered_dataflows WHERE job_id IS NOT NULL
        ),
        job_ids AS (
          SELECT DISTINCT date_trunc(?, timezone(?, event_time)) AS bucket, job_id
          FROM filtered_jobs WHERE job_id IS NOT NULL
        ),
        linkage_counts AS (
          SELECT COALESCE(df.bucket, j.bucket) AS bucket,
                 SUM(CASE WHEN df.job_id IS NOT NULL AND j.job_id IS NOT NULL THEN 1 ELSE 0 END) AS matched_job_ids,
                 SUM(CASE WHEN df.job_id IS NOT NULL AND j.job_id IS NULL THEN 1 ELSE 0 END) AS orphan_dataflow_job_ids,
                 SUM(CASE WHEN df.job_id IS NULL AND j.job_id IS NOT NULL THEN 1 ELSE 0 END) AS jobs_without_dataflow_records
          FROM dataflow_ids df FULL OUTER JOIN job_ids j ON df.bucket IS NOT DISTINCT FROM j.bucket AND df.job_id = j.job_id
          GROUP BY COALESCE(df.bucket, j.bucket)
        )
        SELECT COALESCE(df.bucket, j.bucket, l.bucket) AS bucket,
               COALESCE(df.dataflow_records, 0) AS dataflow_records,
               COALESCE(j.job_records, 0) AS job_records,
               COALESCE(l.matched_job_ids, 0) AS matched_job_ids,
               COALESCE(l.orphan_dataflow_job_ids, 0) AS orphan_dataflow_job_ids,
               COALESCE(l.jobs_without_dataflow_records, 0) AS jobs_without_dataflow_records
        FROM dataflow_counts df
        FULL OUTER JOIN job_counts j ON df.bucket IS NOT DISTINCT FROM j.bucket
        FULL OUTER JOIN linkage_counts l ON COALESCE(df.bucket, j.bucket) IS NOT DISTINCT FROM l.bucket
        ORDER BY bucket
        """,
        [*params, grain, timezone_name, grain, timezone_name, grain, timezone_name, grain, timezone_name],
    )
    rows = []
    for row in _rows(result):
        bucket = row["bucket"]
        bucket_label = _bucket_label(bucket, grain)
        matched = int(row["matched_job_ids"] or 0)
        orphan = int(row["orphan_dataflow_job_ids"] or 0)
        job_only = int(row["jobs_without_dataflow_records"] or 0)
        rows.append({
            "bucket": bucket_label,
            "date": bucket_label,
            "bucket_start": bucket,
            "dataflow_records": int(row["dataflow_records"] or 0),
            "job_records": int(row["job_records"] or 0),
            "matched_job_ids": matched,
            "orphan_dataflow_job_ids": orphan,
            "jobs_without_dataflow_records": job_only,
            "linkage_rate": _rate(matched, matched + orphan + job_only),
        })
    return rows


def _reconciliation_by_metric(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "metric": "", "mismatch_count": 0, "affected_jobs": 0,
        "absolute_difference": 0, "severity": "warning",
    })
    job_ids: dict[str, set[str]] = defaultdict(set)
    for check in checks:
        metric = str(check["metric"])
        bucket = buckets[metric]
        bucket["metric"] = metric
        bucket["mismatch_count"] += 1
        bucket["absolute_difference"] += abs(int(check["difference"]))
        job_ids[metric].add(str(check["job_id"]))
    for metric, bucket in buckets.items():
        bucket["affected_jobs"] = len(job_ids[metric])
    return sorted(buckets.values(), key=lambda item: (-int(item["mismatch_count"]), str(item["metric"])))


def _linkage_summary(counts: dict[str, int], union_count: int) -> list[dict[str, Any]]:
    values = (
        ("matched", "Matched", "good"),
        ("orphan_dataflow_job_id", "Orphan dataflow job IDs", "bad"),
        ("job_without_dataflow_records", "Job-only IDs", "warning"),
    )
    return [
        {
            "category": category,
            "label": label,
            "count": int(counts.get(category) or 0),
            "share": _rate(int(counts.get(category) or 0), union_count),
            "severity": "good" if not counts.get(category) else severity,
        }
        for category, label, severity in values
    ]


def _investigation_queue(
    linkage_evidence: list[dict[str, Any]],
    reconciliation: dict[str, Any],
    field_completeness: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for evidence in linkage_evidence:
        category = str(evidence["category"])
        if category == "matched":
            continue
        if category == "orphan_dataflow_job_id":
            queue.append(_queue_row(
                "bad", "orphan dataflow job id",
                "Dataflow records reference a job_id with no matching job log.",
                str(evidence["job_id"]), evidence["latest_time"],
                {"job_id": evidence["job_id"], "dataflow_records": evidence["dataflow_records"]},
                "Check job_run_log coverage for the same run window.",
            ))
        else:
            queue.append(_queue_row(
                "warning", "job without dataflows",
                "Job log exists but no child dataflow records were found.",
                str(evidence["job_id"]), evidence["latest_time"],
                {"job_id": evidence["job_id"], "job_total_dataflows": evidence["job_total_dataflows"] or 0},
                "Check dataflow_run_log coverage and cache sync for this job.",
            ))
    for check in reconciliation["checks"]:
        queue.append(_queue_row(
            "warning", "reconciliation mismatch",
            f"{check['metric']} expected {check['expected']} but observed {check['observed']}.",
            str(check["job_id"]), None, check,
            "Inspect the job drawer and child dataflow records.",
        ))
    for row in field_completeness:
        if not row["actionable"] or row["severity"] not in {"bad", "warning"}:
            continue
        queue.append(_queue_row(
            str(row["severity"]), "field completeness",
            f"{row['record_type']} {row['group']} completeness is {row['completeness_rate']}%.",
            f"{row['record_type']} · {row['group']}", None, row,
            "Confirm the log version emits the fields used by Monitoring pages.",
        ))
    return sorted(
        queue,
        key=lambda row: (-_severity_rank(str(row["severity"])), -_timestamp_value(row["latest_time"]), str(row["category"])),
    )[:200]


def _queue_row(
    severity: str,
    category: str,
    issue: str,
    target: str,
    latest_time: object,
    evidence: dict[str, Any],
    action_hint: str,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "category": category,
        "issue": issue,
        "target": target,
        "evidence": evidence,
        "latest_time": latest_time,
        "action_hint": action_hint,
    }


def _has_dataflow_scope(filters: dict[str, str]) -> bool:
    connection = str(filters.get("connection") or "").strip()
    if connection and connection != "all":
        return True
    kind = str(filters.get("investigateKind") or "").strip()
    value = str(filters.get("investigateValue") or "").strip()
    return bool(kind and value and kind != "job_id")


def _effective_grain(filters: dict[str, str], summary: dict[str, Any], fallback: str) -> str:
    if str(filters.get("range") or "").strip().lower() != "all":
        return fallback
    start = _timestamp(summary.get("earliest_log_at"))
    end = _timestamp(summary.get("latest_log_at"))
    if start is None or end is None:
        return fallback
    requested = str(filters.get("grain") or "auto").strip().lower()
    if requested not in {"auto", "hour", "day", "week", "month"}:
        requested = "auto"
    span_seconds = max(0, (end - start).total_seconds())
    minimum = (
        "hour" if span_seconds <= 3 * 86400
        else "day" if span_seconds <= 90 * 86400
        else "week" if span_seconds <= 365 * 86400
        else "month"
    )
    if requested == "auto":
        return minimum
    grains = ("hour", "day", "week", "month")
    return grains[max(grains.index(requested), grains.index(minimum))]


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _coverage_status(dataflow_records: int, job_records: int) -> str:
    if not dataflow_records and not job_records:
        return "no_records"
    if not dataflow_records or not job_records:
        return "partial"
    return "ok"


def _health_status(
    *,
    dataflow_records: int,
    job_records: int,
    orphan_job_ids: int,
    jobs_without_dataflow_records: int,
    reconciliation_mismatches: int,
) -> str:
    if not dataflow_records and not job_records:
        return "no_evidence"
    if orphan_job_ids or jobs_without_dataflow_records or reconciliation_mismatches:
        return "has_issues"
    if bool(dataflow_records) != bool(job_records):
        return "warning"
    return "healthy"


def _completeness_severity(rate: float, records: int) -> str:
    if records == 0:
        return "info"
    if rate < 80:
        return "bad"
    if rate < 95:
        return "warning"
    return "good"


def _bucket_label(value: object, grain: str) -> str:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return "unknown"
    if not isinstance(value, datetime):
        return "unknown"
    if grain == "hour":
        return value.strftime("%Y-%m-%d %H:00")
    if grain == "week":
        year, week, _ = value.isocalendar()
        return f"{year}-W{week:02d}"
    if grain == "month":
        return value.strftime("%Y-%m")
    return value.strftime("%Y-%m-%d")


def _rate(part: int | float, whole: int | float) -> float:
    return round((part / whole) * 100, 2) if whole else 0


def _severity_rank(value: str) -> int:
    return {"bad": 4, "error": 4, "warning": 3, "info": 2, "good": 1}.get(value.lower(), 0)


def _timestamp_value(value: object) -> float:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
    if not isinstance(value, datetime):
        return 0.0
    try:
        return value.timestamp()
    except (OSError, OverflowError, ValueError):
        return 0.0


def _single_row(result: Any) -> dict[str, Any]:
    row = result.fetchone()
    if row is None:
        return {}
    return {
        column[0]: _json_value(value)
        for column, value in zip(result.description, row)
    }


def _rows(result: Any) -> list[dict[str, Any]]:
    columns = [column[0] for column in result.description]
    return [
        {column: _json_value(value) for column, value in zip(columns, row)}
        for row in result.fetchall()
    ]


def _json_value(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (datetime, date)) else value
