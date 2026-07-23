from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
from datacoolie_studio.domains.monitoring.metrics.health import environment_health
from datacoolie_studio.domains.monitoring.metrics.common_projections import (
    _avg,
    _counter_rows,
    _dataflow_id,
    _dataflow_operation_type,
    _date_bucket,
    _destination_operation_type,
    _dominant_dataflow_operation_type,
    _dominant_value,
    _duration_stats,
    _enrich_job_run_for_investigation,
    _error_preview,
    _job_key,
    _job_runs_by_dataflow_operation_type,
    _job_shape_label,
    _listish_values,
    _num,
    _percentile,
    _percentile_clean,
    _phase_duration_summary,
    _rate,
    _row_timestamp,
    _status,
    _sum,
    _time_value,
    _trend_context,
    _watermark_classification,
)

_STATUS_KEYS = ("succeeded", "failed", "skipped", "running", "pending", "unknown")


def _operations_page(
    rows: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    timezone_info: tzinfo = timezone.utc,
    trend_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trend_context = trend_context or _trend_context({}, [*rows, *jobs], timezone_info)
    job_status = Counter(_status(row) for row in jobs)
    dataflow_status = Counter(_status(row) for row in rows)
    executable_job_runs = [
        job for job in jobs if _status(job) in {"succeeded", "failed"}
    ]
    executable_dataflow_runs = [
        row for row in rows if _status(row) in {"succeeded", "failed"}
    ]
    job_durations = [_num(job, "duration_seconds") for job in executable_job_runs]
    dataflow_durations = [
        _num(row, "duration_seconds") for row in executable_dataflow_runs
    ]
    total_jobs = len(jobs)
    total_dataflows = len(rows)
    executable_jobs = job_status.get("succeeded", 0) + job_status.get("failed", 0)
    executable_dataflows = dataflow_status.get("succeeded", 0) + dataflow_status.get(
        "failed", 0
    )
    failed_jobs = [
        _enrich_job_run_for_investigation(job)
        for job in jobs
        if _status(job) == "failed"
    ][:50]
    job_runs_by_dataflow_operation_type = _job_runs_by_dataflow_operation_type(
        rows, jobs
    )
    job_total_running = _sum(jobs, "total_running")
    job_total_pending = _sum(jobs, "total_pending")
    job_total_skipped = _sum(jobs, "total_skipped")
    return {
        "kpis": {
            "total_jobs": total_jobs,
            "total_succeeded": job_status.get("succeeded", 0),
            "job_success_rate": _rate(job_status.get("succeeded", 0), executable_jobs),
            "job_failure_rate": _rate(job_status.get("failed", 0), executable_jobs),
            "job_skip_rate": _rate(job_status.get("skipped", 0), total_jobs),
            "job_pending_rate": _rate(job_status.get("pending", 0), total_jobs),
            "job_running_rate": _rate(job_status.get("running", 0), total_jobs),
            "total_rows_processed": _sum(jobs, "total_rows_read")
            + _sum(jobs, "total_rows_written"),
            "total_failures": job_status.get("failed", 0),
            "total_skipped": job_total_skipped,
            "total_pending": job_total_pending,
            "total_running": job_total_running,
            "avg_duration_seconds": _avg(job_durations),
            "p95_duration_seconds": _percentile_clean(job_durations, 0.95),
        },
        "windows": _operation_windows(rows, jobs, timezone_info=timezone_info),
        "job_duration_stats": _duration_stats(executable_job_runs),
        "job_status_distribution": _counter_rows(job_status, "status"),
        "job_runs_by_operation_type": job_runs_by_dataflow_operation_type,
        "job_runs_by_dataflow_operation_type": job_runs_by_dataflow_operation_type,
        "job_duration_by_operation_types": _duration_by_group(
            jobs,
            "operation_type",
            _job_operation_types_value,
            entity_kind="job",
        ),
        "dataflow_duration_by_stage": _duration_by_group(
            rows, "stage", lambda row: str(row.get("stage") or "unknown"), limit=100
        ),
        "job_workload_efficiency": _job_workload_efficiency(jobs, rows),
        "job_child_fanout_distribution": _job_child_fanout_distribution(jobs),
        "jobs_by_date_status": _status_by_date(jobs, trend_context=trend_context),
        "latest_failed_job": _latest_failed_run(jobs),
        "slowest_jobs": _slowest_jobs(jobs),
        "jobs_by_engine_provider": _jobs_by_engine_provider(jobs),
        "jobs_by_child_impact": _jobs_by_child_impact(jobs),
        "job_attention": _job_attention_items(jobs),
        "dataflows_by_date_status": _status_by_date(rows, trend_context=trend_context),
        "failed_jobs": failed_jobs,
        "dataflow_kpis": {
            "total_dataflows": total_dataflows,
            "succeeded": dataflow_status.get("succeeded", 0),
            "failed": dataflow_status.get("failed", 0),
            "skipped": dataflow_status.get("skipped", 0),
            "pending": dataflow_status.get("pending", 0),
            "running": dataflow_status.get("running", 0),
            "success_rate": _rate(
                dataflow_status.get("succeeded", 0), executable_dataflows
            ),
            "failure_rate": _rate(
                dataflow_status.get("failed", 0), executable_dataflows
            ),
            "skip_rate": _rate(dataflow_status.get("skipped", 0), total_dataflows),
            "pending_rate": _rate(dataflow_status.get("pending", 0), total_dataflows),
            "running_rate": _rate(dataflow_status.get("running", 0), total_dataflows),
            "total_bytes_written": _sum(rows, "destination_bytes_added"),
            "avg_duration_seconds": _avg(dataflow_durations),
            "p95_duration_seconds": _percentile_clean(dataflow_durations, 0.95),
            "active_engines": len(
                {row.get("engine_name") for row in rows if row.get("engine_name")}
            ),
        },
        "dataflow_duration_stats": _duration_stats(executable_dataflow_runs),
        "dataflow_runs_by_operation_type": _operation_type_mix(
            rows, _dataflow_operation_type
        ),
        "dataflow_runs_by_destination_operation_type": _operation_type_mix(
            rows, _destination_operation_type
        ),
        "phase_health": _phase_health_summary(rows),
        "phase_health_by_stage": _phase_health_by_stage(rows),
        "dataflow_endpoint_health": _dataflow_endpoint_health(rows),
        "dataflow_name_status_health": _dataflow_name_status_health(rows),
        "dataflow_watermark_summary": _dataflow_watermark_summary(rows),
        "job_status_by_stage": _job_status_by_stage(rows, jobs),
        "dataflow_status_by_stage": _status_by_stage(rows),
        "status_by_stage": _status_by_stage(rows),
    }


def _latest_failed_run(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    failed = [
        row
        for row in rows
        if _status(row) == "failed" and _row_timestamp(row) is not None
    ]
    if not failed:
        return None
    return max(failed, key=lambda row: _row_timestamp(row) or datetime.min)


def _slowest_jobs(jobs: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    completed = [job for job in jobs if _num(job, "duration_seconds") is not None]
    return sorted(
        completed, key=lambda job: _num(job, "duration_seconds") or 0, reverse=True
    )[:limit]


def _duration_by_group(
    rows: list[dict[str, Any]],
    group_key: str,
    resolve_group,
    limit: int = 12,
    *,
    entity_kind: str = "dataflow",
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        status = _status(row)
        if status not in {"succeeded", "failed", "skipped"}:
            continue
        if _num(row, "duration_seconds") is None:
            continue
        groups[resolve_group(row)].append(row)

    result = []
    for group_value, items in groups.items():
        durations = [_num(item, "duration_seconds") for item in items]
        clean = [duration for duration in durations if duration is not None]
        statuses = Counter(_status(item) for item in items)
        executable = statuses.get("succeeded", 0) + statuses.get("failed", 0)
        operation_mix = Counter(
            _job_operation_types_value(item)
            if entity_kind == "job"
            else _dataflow_operation_type(item)
            for item in items
        )
        q1_duration = _percentile(clean, 0.25)
        q3_duration = _percentile(clean, 0.75)
        iqr = q3_duration - q1_duration
        lower_fence = q1_duration - 1.5 * iqr
        upper_fence = q3_duration + 1.5 * iqr
        non_outlier_durations = [
            duration for duration in clean if lower_fence <= duration <= upper_fence
        ] or clean
        outliers = [
            {
                "duration_seconds": round(duration, 3),
                "dataflow_name": (
                    item.get("job_id")
                    if entity_kind == "job"
                    else item.get("dataflow_name") or item.get("dataflow_id")
                )
                or "unknown",
                "dataflow_run_id": item.get("job_id")
                if entity_kind == "job"
                else item.get("dataflow_run_id"),
                "status": _status(item),
                "operation_type": (
                    _job_operation_types_value(item)
                    if entity_kind == "job"
                    else _dataflow_operation_type(item)
                ),
            }
            for item in items
            for duration in [_num(item, "duration_seconds")]
            if duration is not None
            and (duration < lower_fence or duration > upper_fence)
        ]
        outlier_count = len(outliers)
        outliers = sorted(
            outliers, key=lambda item: float(item["duration_seconds"]), reverse=True
        )[:40]
        result.append(
            {
                group_key: group_value,
                "group": group_value,
                "count": len(clean),
                "min_duration_seconds": round(min(clean), 3) if clean else 0,
                "whisker_min_duration_seconds": round(min(non_outlier_durations), 3)
                if non_outlier_durations
                else 0,
                "q1_duration_seconds": q1_duration,
                "p50_duration_seconds": _percentile(clean, 0.50),
                "q3_duration_seconds": q3_duration,
                "whisker_max_duration_seconds": round(max(non_outlier_durations), 3)
                if non_outlier_durations
                else 0,
                "p95_duration_seconds": _percentile(clean, 0.95),
                "max_duration_seconds": round(max(clean), 3) if clean else 0,
                "avg_duration_seconds": _avg(durations),
                "succeeded": statuses.get("succeeded", 0),
                "failed": statuses.get("failed", 0),
                "skipped": statuses.get("skipped", 0),
                "success_rate": _rate(statuses.get("succeeded", 0), executable),
                "operation_mix": ", ".join(
                    f"{name}: {count}" for name, count in sorted(operation_mix.items())
                ),
                "outlier_count": outlier_count,
                "outliers": outliers,
            }
        )
    return sorted(
        result,
        key=lambda row: (
            -float(row["p95_duration_seconds"]),
            -int(row["count"]),
            str(row["group"]),
        ),
    )[:limit]


def _job_workload_efficiency(
    jobs: list[dict[str, Any]], rows: list[dict[str, Any]], limit: int = 500
) -> list[dict[str, Any]]:
    jobs_by_id = {
        str(job.get("job_id") or "").strip(): job
        for job in jobs
        if str(job.get("job_id") or "").strip()
    }
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        job_id = str(row.get("job_id") or "").strip()
        if not job_id:
            continue
        groups[(job_id, _dataflow_operation_type(row))].append(row)

    points = []
    for (job_id, operation_type), items in groups.items():
        duration = sum(_num(item, "duration_seconds") or 0 for item in items)
        if duration <= 0:
            continue
        job = jobs_by_id.get(job_id, {})
        child_count = len(items)
        rows_read = sum(_num(item, "source_rows_read") or 0 for item in items)
        rows_written = sum(
            _num(item, "destination_rows_written") or 0 for item in items
        )
        bytes_added = sum(_num(item, "destination_bytes_added") or 0 for item in items)
        throughput = rows_read / duration if rows_read > 0 and duration > 0 else 0
        statuses = Counter(_status(item) for item in items)
        points.append(
            {
                "job_id": job_id,
                "job_key": _job_key(job) if job else "unknown",
                "job_shape_label": _job_shape_label(job) if job else "unknown",
                "status": _status(job) if job else _dominant_status(statuses),
                "operation_type": operation_type,
                "engine_name": job.get("engine_name") or "unknown",
                "metadata_provider_name": job.get("metadata_provider_name")
                or "unknown",
                "platform_name": job.get("platform_name") or "unknown",
                "duration_seconds": duration,
                "child_dataflow_count": child_count,
                "failed_child_dataflows": statuses.get("failed", 0),
                "skipped_child_dataflows": statuses.get("skipped", 0),
                "rows_read": rows_read,
                "rows_written": rows_written,
                "bytes_added": bytes_added,
                "workload_size": throughput,
                "workload_size_metric": "rows_read_per_second",
            }
        )
    return sorted(
        points,
        key=lambda item: (
            float(item["duration_seconds"]),
            float(item["child_dataflow_count"]),
        ),
        reverse=True,
    )[:limit]


def _job_child_fanout_distribution(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[int, Counter] = defaultdict(Counter)
    for job in jobs:
        child_count = int(_num(job, "total_dataflows") or 0)
        if child_count <= 0:
            continue
        status = _status(job)
        bucket = buckets[child_count]
        bucket["jobs"] += 1
        bucket[status if status in _STATUS_KEYS else "unknown"] += 1

    return [
        {
            "total_dataflows": total_dataflows,
            "jobs": int(values.get("jobs", 0)),
            "succeeded": int(values.get("succeeded", 0)),
            "failed": int(values.get("failed", 0)),
            "skipped": int(values.get("skipped", 0)),
            "running": int(values.get("running", 0)),
            "pending": int(values.get("pending", 0)),
            "unknown": int(values.get("unknown", 0)),
        }
        for total_dataflows, values in sorted(
            buckets.items(), key=lambda item: int(item[0])
        )
    ]


def _jobs_by_engine_provider(
    jobs: list[dict[str, Any]], limit: int = 30
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        key = (
            str(job.get("engine_name") or "unknown"),
            str(job.get("metadata_provider_name") or "unknown"),
            str(job.get("platform_name") or "unknown"),
        )
        groups[key].append(job)

    rows = []
    for (engine, provider, platform), items in groups.items():
        statuses = Counter(_status(item) for item in items)
        executable = statuses.get("succeeded", 0) + statuses.get("failed", 0)
        durations = [
            _num(item, "duration_seconds")
            for item in items
            if _status(item) in {"succeeded", "failed"}
        ]
        rows.append(
            {
                "engine_name": engine,
                "metadata_provider_name": provider,
                "platform_name": platform,
                "name": f"{engine} / {provider}",
                "jobs": len(items),
                "succeeded": statuses.get("succeeded", 0),
                "failed": statuses.get("failed", 0),
                "skipped": statuses.get("skipped", 0),
                "running": statuses.get("running", 0),
                "pending": statuses.get("pending", 0),
                "success_rate": _rate(statuses.get("succeeded", 0), executable),
                "avg_duration_seconds": _avg(durations),
                "p95_duration_seconds": _percentile_clean(durations, 0.95),
            }
        )
    return sorted(rows, key=lambda row: (row["failed"], row["jobs"]), reverse=True)[
        :limit
    ]


def _jobs_by_child_impact(
    jobs: list[dict[str, Any]], limit: int = 20
) -> list[dict[str, Any]]:
    candidates = []
    for job in jobs:
        total = _num(job, "child_dataflow_count") or _num(job, "total_dataflows") or 0
        failed = _num(job, "child_failed_count") or 0
        skipped = _num(job, "child_skipped_count") or 0
        p95 = _num(job, "child_p95_duration_seconds") or 0
        if total <= 0 and failed <= 0 and skipped <= 0 and p95 <= 0:
            continue
        candidates.append(
            {
                "job_id": job.get("job_id"),
                "status": _status(job),
                "child_dataflow_count": total,
                "child_failed_count": failed,
                "child_skipped_count": skipped,
                "child_p95_duration_seconds": p95,
                "duration_seconds": _num(job, "duration_seconds") or 0,
                "reconciliation_status": job.get("reconciliation_status"),
                "reconciliation_mismatch_count": _num(
                    job, "reconciliation_mismatch_count"
                )
                or 0,
            }
        )
    return sorted(
        candidates,
        key=lambda row: (
            row["child_failed_count"],
            row["reconciliation_mismatch_count"],
            row["child_skipped_count"],
            row["child_dataflow_count"],
            row["child_p95_duration_seconds"],
        ),
        reverse=True,
    )[:limit]


def _job_attention_items(
    jobs: list[dict[str, Any]], limit: int = 8
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    duration_stats = _duration_stats(
        [job for job in jobs if _status(job) in {"succeeded", "failed"}]
    )
    p95 = float(duration_stats.get("p95_duration_seconds") or 0)
    p99 = float(duration_stats.get("p99_duration_seconds") or 0)

    for job in jobs:
        job_id = str(job.get("job_id") or "unknown")
        status = _status(job)
        duration = _num(job, "duration_seconds") or 0
        child_failed = _num(job, "child_failed_count") or 0
        mismatches = _num(job, "reconciliation_mismatch_count") or 0
        timestamp = _row_timestamp(job)
        sort_time = timestamp.timestamp() if timestamp else 0

        if status == "failed":
            items.append(
                {
                    "severity": "bad",
                    "code": "job_failed",
                    "title": "Failed job",
                    "detail": _error_preview(job) or job_id,
                    "job_id": job_id,
                    "sort_score": 5000 + sort_time,
                }
            )
        if child_failed:
            items.append(
                {
                    "severity": "bad",
                    "code": "child_dataflow_failed",
                    "title": "Child dataflows failed",
                    "detail": f"{int(child_failed)} failed child dataflows in {job_id}",
                    "job_id": job_id,
                    "sort_score": 4000 + child_failed,
                }
            )
        if mismatches:
            items.append(
                {
                    "severity": "warning",
                    "code": "job_reconciliation_mismatch",
                    "title": "Reconciliation mismatch",
                    "detail": f"{int(mismatches)} job totals differ from child rollup in {job_id}",
                    "job_id": job_id,
                    "sort_score": 3000 + mismatches,
                }
            )
        if p99 and duration >= p99:
            items.append(
                {
                    "severity": "warning",
                    "code": "job_duration_p99",
                    "title": "P99 duration job",
                    "detail": f"{_format_seconds(duration)} duration in {job_id}",
                    "job_id": job_id,
                    "sort_score": 2000 + duration,
                }
            )
        elif p95 and duration >= p95:
            items.append(
                {
                    "severity": "info",
                    "code": "job_duration_p95",
                    "title": "P95 duration job",
                    "detail": f"{_format_seconds(duration)} duration in {job_id}",
                    "job_id": job_id,
                    "sort_score": 1000 + duration,
                }
            )

    return sorted(
        items, key=lambda item: float(item.get("sort_score") or 0), reverse=True
    )[:limit]


def _health_page(
    rows: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    operations: dict[str, Any],
    maintenance: dict[str, Any],
    coverage: dict[str, Any],
    reconciliation: dict[str, Any],
) -> dict[str, Any]:
    latest_log_at = _latest_log_at([*rows, *jobs])
    latest_job_log_at = _latest_log_at(jobs)
    latest_dataflow_log_at = _latest_log_at(rows)
    failed_jobs_3 = _failed_count_in_window(jobs, 3)
    failed_jobs_7 = _failed_count_in_window(jobs, 7)
    failed_dataflows_3 = _failed_count_in_window(rows, 3)
    failed_dataflows_7 = _failed_count_in_window(rows, 7)
    maintenance_failed_7 = _maintenance_count_in_window(rows, 7, {"failed"})
    maintenance_failed_14 = _maintenance_count_in_window(rows, 14, {"failed"})
    maintenance_skipped_7 = _maintenance_count_in_window(rows, 7, {"skipped"})
    return environment_health(
        latest_log_at=latest_log_at,
        latest_job_log_at=latest_job_log_at,
        latest_dataflow_log_at=latest_dataflow_log_at,
        coverage=coverage,
        reconciliation=reconciliation,
        failed_jobs_last_3_days=failed_jobs_3,
        failed_jobs_last_7_days=failed_jobs_7,
        failed_dataflows_last_3_days=failed_dataflows_3,
        failed_dataflows_last_7_days=failed_dataflows_7,
        maintenance_failed_last_7_days=maintenance_failed_7,
        maintenance_failed_last_14_days=maintenance_failed_14,
        maintenance_skipped_last_7_days=maintenance_skipped_7,
        has_jobs=bool(operations.get("kpis", {}).get("total_jobs")),
    )


def _attention_queue(
    rows: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    failures: dict[str, Any],
    performance: dict[str, Any],
    maintenance: dict[str, Any],
    coverage: dict[str, Any],
    reconciliation: dict[str, Any],
    freshness: dict[str, Any],
    health: dict[str, Any],
    operations: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    repeated_failure_min_errors = 3
    stale_log_warning_days = 7
    slow_stage_info_min_p95_seconds = 60
    max_attention_items = 8
    items = []
    operations = operations or {}
    diagnostics = diagnostics or {}
    if coverage.get("read_errors"):
        items.append(
            _attention(
                "bad",
                "log_read_errors",
                "Review log read errors",
                f"{coverage['read_errors']} read errors were found.",
                "diagnostics",
                {"impact": coverage["read_errors"]},
            )
        )
    failed_jobs_3 = int(health.get("failed_jobs_last_3_days") or 0)
    failed_jobs_7 = int(health.get("failed_jobs_last_7_days") or 0)
    failed_dataflows_3 = int(health.get("failed_dataflows_last_3_days") or 0)
    failed_dataflows_7 = int(health.get("failed_dataflows_last_7_days") or 0)
    if failed_jobs_3:
        items.append(
            _attention(
                "bad",
                "recent_failed_jobs",
                "Review recent failed jobs",
                f"{failed_jobs_3} jobs failed in the last 3 days.",
                "jobs",
                {"impact": failed_jobs_3},
            )
        )
    elif failed_jobs_7:
        items.append(
            _attention(
                "warning",
                "recent_failed_jobs",
                "Review recent failed jobs",
                f"{failed_jobs_7} jobs failed in the last 7 days.",
                "jobs",
                {"impact": failed_jobs_7},
            )
        )
    if failed_dataflows_3:
        items.append(
            _attention(
                "bad",
                "recent_failed_dataflows",
                "Review recent failed dataflows",
                f"{failed_dataflows_3} dataflow runs failed in the last 3 days.",
                "failures",
                {"impact": failed_dataflows_3},
            )
        )
    elif failed_dataflows_7:
        items.append(
            _attention(
                "warning",
                "recent_failed_dataflows",
                "Review recent failed dataflows",
                f"{failed_dataflows_7} dataflow runs failed in the last 7 days.",
                "failures",
                {"impact": failed_dataflows_7},
            )
        )
    top_failure = _first(failures.get("top_failing_dataflows"))
    if (
        top_failure
        and int(top_failure.get("error_count") or 0) >= repeated_failure_min_errors
    ):
        items.append(
            _attention(
                "bad",
                "repeated_failure",
                "Repeated dataflow failure",
                f"{top_failure.get('dataflow_name')} failed {top_failure.get('error_count')} times.",
                "failures",
                {**top_failure, "impact": top_failure.get("error_count")},
            )
        )
    if health.get("status") == "no_log_evidence":
        items.append(
            _attention(
                "warning",
                "no_log_evidence",
                "No log evidence",
                "No monitoring logs were found in current filters.",
                "overview",
            )
        )
    if (
        health.get("latest_log_age_days")
        and health["latest_log_age_days"] > stale_log_warning_days
    ):
        items.append(
            _attention(
                "warning",
                "stale_logs",
                "Check log freshness",
                f"Latest log is {health['latest_log_age_days']} days old.",
                "overview",
            )
        )
    maintenance_failed_7 = int(health.get("maintenance_failed_last_7_days") or 0)
    maintenance_failed_14 = int(health.get("maintenance_failed_last_14_days") or 0)
    maintenance_skipped_7 = int(health.get("maintenance_skipped_last_7_days") or 0)
    if maintenance_failed_7:
        items.append(
            _attention(
                "bad",
                "maintenance_failed",
                "Review failed maintenance",
                f"{maintenance_failed_7} maintenance operations failed in the last 7 days.",
                "maintenance",
            )
        )
    elif maintenance_failed_14:
        items.append(
            _attention(
                "warning",
                "maintenance_failed",
                "Review failed maintenance",
                f"{maintenance_failed_14} maintenance operations failed in the last 14 days.",
                "maintenance",
            )
        )
    if maintenance_skipped_7:
        items.append(
            _attention(
                "warning",
                "maintenance_skipped",
                "Review skipped maintenance",
                f"{maintenance_skipped_7} maintenance operations were skipped in the last 7 days.",
                "maintenance",
            )
        )
    maintenance_kpis = maintenance.get("kpis", {})
    maintenance_missing = int(maintenance_kpis.get("coverage_missing_tables") or 0)
    maintenance_lagged = int(maintenance_kpis.get("lagged_tables") or 0)
    maintenance_active = int(maintenance_kpis.get("latest_active_tables") or 0)
    if maintenance_missing:
        items.append(
            _attention(
                "warning",
                "maintenance_coverage",
                "Review maintenance coverage",
                f"{maintenance_missing} active lakehouse tables have no maintenance evidence.",
                "maintenance",
                {"impact": maintenance_missing},
            )
        )
    if maintenance_lagged:
        items.append(
            _attention(
                "warning",
                "maintenance_lag",
                "Review maintenance lag",
                f"{maintenance_lagged} tables exceed the maintenance lag threshold.",
                "maintenance",
                {"impact": maintenance_lagged},
            )
        )
    if maintenance_active:
        items.append(
            _attention(
                "info",
                "maintenance_active",
                "Inspect active maintenance",
                f"{maintenance_active} table maintenance targets are running or pending.",
                "maintenance",
                {"impact": maintenance_active},
            )
        )
    freshness_kpis = freshness.get("kpis", {})
    if freshness_kpis.get("stale_candidates"):
        items.append(
            _attention(
                "warning",
                "stale_dataflows",
                "Review stale dataflows",
                f"{freshness_kpis['stale_candidates']} stale dataflow candidates were detected.",
                "freshness",
            )
        )
    if freshness_kpis.get("watermark_unchanged_runs"):
        items.append(
            _attention(
                "warning",
                "watermark_not_advanced",
                "Review unchanged watermarks",
                f"{freshness_kpis['watermark_unchanged_runs']} runs did not advance watermark values.",
                "freshness",
            )
        )
    performance_kpis = performance.get("kpis", {})
    pressure_ratio = _num(performance_kpis, "duration_pressure_ratio") or 0
    pressure_p95 = _num(performance_kpis, "p95_duration_seconds") or 0
    pressure_severity = (
        "bad"
        if pressure_ratio >= 10 and pressure_p95 >= 60
        else "warning"
        if pressure_ratio >= 5 and pressure_p95 >= 30
        else None
    )
    if pressure_severity:
        items.append(
            _attention(
                pressure_severity,
                "performance_pressure",
                "Review performance pressure",
                f"P95 is {round(pressure_ratio, 1)}x P50 at {_format_seconds(pressure_p95)}.",
                "performance",
                {"impact": pressure_ratio, "p95_duration_seconds": pressure_p95},
            )
        )
    optimization_candidates = int(
        performance_kpis.get("optimization_candidate_count") or 0
    )
    if optimization_candidates:
        items.append(
            _attention(
                "warning",
                "optimization_candidates",
                "Review optimization candidates",
                f"{optimization_candidates} dataflow runs match performance optimization rules.",
                "performance",
                {"impact": optimization_candidates},
            )
        )
    slowest_stage = _first(performance.get("duration_by_stage"))
    if (
        not pressure_severity
        and slowest_stage
        and (_num(slowest_stage, "p95_duration_seconds") or 0)
        >= slow_stage_info_min_p95_seconds
    ):
        items.append(
            _attention(
                "info",
                "slowest_stage",
                "Inspect slowest stage",
                f"{slowest_stage.get('stage')} has p95 duration {_format_seconds(_num(slowest_stage, 'p95_duration_seconds') or 0)}.",
                "performance",
                slowest_stage,
            )
        )
    if reconciliation.get("mismatch_count"):
        items.append(
            _attention(
                "warning",
                "log_reconciliation",
                "Review log consistency",
                f"{reconciliation['mismatch_count']} job totals differ from dataflow rollups.",
                "diagnostics",
            )
        )
    diagnostics_kpis = diagnostics.get("kpis", {})
    linkage_gaps = int(diagnostics_kpis.get("orphan_dataflow_job_ids") or 0) + int(
        diagnostics_kpis.get("jobs_without_dataflow_records") or 0
    )
    if linkage_gaps:
        items.append(
            _attention(
                "bad",
                "job_linkage_gaps",
                "Review job linkage gaps",
                f"{linkage_gaps} job IDs are not linked across job and dataflow logs.",
                "diagnostics",
                {"impact": linkage_gaps},
            )
        )
    cache_warnings = int(diagnostics_kpis.get("cache_warning_count") or 0)
    if cache_warnings:
        items.append(
            _attention(
                "warning",
                "log_cache_warnings",
                "Review log cache warnings",
                f"{cache_warnings} log sources have partial cache or read coverage.",
                "diagnostics",
                {"impact": cache_warnings},
            )
        )
    dataflow_kpis = operations.get("dataflow_kpis", {})
    active_dataflows = int(dataflow_kpis.get("running") or 0) + int(
        dataflow_kpis.get("pending") or 0
    )
    if active_dataflows:
        items.append(
            _attention(
                "info",
                "active_dataflows",
                "Inspect active dataflows",
                f"{active_dataflows} dataflow runs are running or pending in the current filters.",
                "dataflows",
                {"impact": active_dataflows},
            )
        )
    runtime_contexts = operations.get("jobs_by_engine_provider", [])
    unhealthy_contexts = [
        context
        for context in runtime_contexts
        if int(context.get("failed") or 0) > 0
        and (_num(context, "success_rate") or 0) < 95
    ]
    if unhealthy_contexts:
        context = min(
            unhealthy_contexts,
            key=lambda item: (
                _num(item, "success_rate") or 0,
                -int(item.get("failed") or 0),
            ),
        )
        context_name = " / ".join(
            str(context.get(key) or "unknown")
            for key in ("engine_name", "metadata_provider_name")
        )
        context_success_rate = round(_num(context, "success_rate") or 0, 1)
        items.append(
            _attention(
                "warning",
                "runtime_context_health",
                "Review runtime context health",
                f"{context_name} is at {context_success_rate}% success.",
                "jobs",
                {**context, "impact": context.get("failed")},
            )
        )
    return _prioritize_attention(items, limit=max_attention_items)


def _prioritize_attention(
    items: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    severity_rank = {"bad": 0, "warning": 1, "info": 2, "good": 3}
    by_code: dict[str, dict[str, Any]] = {}
    for item in items:
        code = str(item.get("code") or "")
        current = by_code.get(code)
        if current is None or severity_rank.get(
            str(item.get("severity")), 4
        ) < severity_rank.get(str(current.get("severity")), 4):
            by_code[code] = item
    return sorted(
        by_code.values(),
        key=lambda item: (
            severity_rank.get(str(item.get("severity")), 4),
            -float((item.get("evidence") or {}).get("impact") or 0),
            str(item.get("title") or ""),
        ),
    )[:limit]


def _attention(
    severity: str,
    code: str,
    title: str,
    detail: str,
    target: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "title": title,
        "detail": detail,
        "target": target,
        "evidence": evidence or {},
    }


def _metric_definitions() -> dict[str, dict[str, str]]:
    return {
        "job_success_rate": {
            "label": "Job execution success rate",
            "formula": "succeeded jobs / (succeeded jobs + failed jobs)",
        },
        "job_failure_rate": {
            "label": "Job failure rate",
            "formula": "failed jobs / (succeeded jobs + failed jobs)",
        },
        "job_skip_rate": {
            "label": "Job skip rate",
            "formula": "skipped jobs / all job runs",
        },
        "job_pending_rate": {
            "label": "Job pending rate",
            "formula": "pending jobs / all job runs",
        },
        "job_running_rate": {
            "label": "Job running rate",
            "formula": "running jobs / all job runs",
        },
        "dataflow_success_rate": {
            "label": "Dataflow execution success rate",
            "formula": "succeeded dataflow runs / (succeeded dataflow runs + failed dataflow runs)",
        },
        "dataflow_failure_rate": {
            "label": "Dataflow failure rate",
            "formula": "failed dataflow runs / (succeeded dataflow runs + failed dataflow runs)",
        },
        "dataflow_skip_rate": {
            "label": "Dataflow skip rate",
            "formula": "skipped dataflow runs / all dataflow runs",
        },
        "dataflow_pending_rate": {
            "label": "Dataflow pending rate",
            "formula": "pending dataflow runs / all dataflow runs",
        },
        "dataflow_running_rate": {
            "label": "Dataflow running rate",
            "formula": "running dataflow runs / all dataflow runs",
        },
        "health_status": {
            "label": "Environment health",
            "formula": "highest severity matching rule: no evidence, stale logs, recent failures, maintenance issues, or reconciliation mismatch",
        },
        "today_window": {
            "label": "Today",
            "formula": "runs whose end_time/start_time falls on the current date in the configured Studio timezone",
        },
        "last_7_days_window": {
            "label": "Last 7 days",
            "formula": "runs whose end_time/start_time is within the last 7 * 24 hours",
        },
        "avg_duration": {
            "label": "Average duration",
            "formula": "average duration_seconds for executable runs (succeeded + failed) with duration present",
        },
        "duration_quartiles": {
            "label": "Duration percentiles",
            "formula": "P50/P75/P95/P99 duration_seconds for executable runs (succeeded + failed) with duration present",
        },
        "net_bytes_change": {
            "label": "Net bytes change",
            "formula": "destination bytes added - destination bytes removed",
        },
        "maintenance_efficiency": {
            "label": "Maintenance efficiency",
            "formula": "bytes removed / duration seconds",
        },
        "p95_duration": {
            "label": "P95 duration",
            "formula": "95th percentile of duration_seconds for executable runs (succeeded + failed) with duration present",
        },
        "log_coverage": {
            "label": "Log coverage",
            "formula": "presence and joinability of job logs and dataflow logs",
        },
    }


def _status_by_date(
    rows: list[dict[str, Any]], trend_context: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    trend_context = trend_context or _trend_context({}, rows, timezone.utc)
    return _status_by_date_counts(
        [{**row, "count": 1} for row in rows],
        trend_context,
    )


def _status_by_date_counts(
    rows: list[dict[str, Any]],
    trend_context: dict[str, Any],
) -> list[dict[str, Any]]:
    buckets: dict[str, Counter] = defaultdict(Counter)
    bucket_metadata: dict[str, dict[str, str | None]] = {}
    for row in rows:
        bucket = _date_bucket(row, trend_context)
        buckets[bucket["bucket"]][_status(row)] += int(row.get("count") or 0)
        bucket_metadata[bucket["bucket"]] = bucket

    result = []
    for bucket_key, counts in buckets.items():
        succeeded = int(counts.get("succeeded", 0))
        failed = int(counts.get("failed", 0))
        skipped = int(counts.get("skipped", 0))
        running = int(counts.get("running", 0))
        pending = int(counts.get("pending", 0))
        unknown = int(counts.get("unknown", 0))
        for status, value in counts.items():
            if status not in _STATUS_KEYS:
                unknown += int(value)

        total = succeeded + failed + skipped + running + pending + unknown
        executable_total = succeeded + failed
        bucket_info = bucket_metadata.get(
            bucket_key, {"bucket_start": None, "bucket_end": None}
        )
        item = {
            "date": bucket_key,
            "bucket": bucket_key,
            "bucket_start": bucket_info["bucket_start"],
            "bucket_end": bucket_info["bucket_end"],
            "grain": trend_context["effective_grain"],
            "succeeded": succeeded,
            "failed": failed,
            "skipped": skipped,
            "running": running,
            "pending": pending,
            "unknown": unknown,
            "total": total,
            "executable_total": executable_total,
            "success_rate": _rate(succeeded, executable_total)
            if executable_total
            else None,
            "failure_rate": _rate(failed, executable_total)
            if executable_total
            else None,
        }
        result.append(item)
    return sorted(result, key=lambda item: str(item["bucket_start"] or item["bucket"]))


def _status_by_stage(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        buckets[row.get("stage") or "unknown"][_status(row)] += 1
    result = []
    for stage, counts in buckets.items():
        item = {"stage": stage}
        item.update(dict(counts))
        result.append(item)
    return sorted(
        result,
        key=lambda item: (
            -(int(item.get("failed", 0))),
            -sum(
                int(item.get(status, 0))
                for status in (
                    "succeeded",
                    "failed",
                    "skipped",
                    "running",
                    "pending",
                    "unknown",
                )
            ),
            str(item["stage"]),
        ),
    )


def _job_status_by_stage(
    rows: list[dict[str, Any]], jobs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    latest_job_by_id: dict[str, dict[str, Any]] = {}
    for job in jobs:
        job_id = str(job.get("job_id") or "")
        if not job_id:
            continue
        existing = latest_job_by_id.get(job_id)
        if existing is None or _time_value(
            job.get("end_time") or job.get("start_time")
        ) > _time_value(existing.get("end_time") or existing.get("start_time")):
            latest_job_by_id[job_id] = job

    stage_job_ids: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        job_id = str(row.get("job_id") or "")
        if not job_id:
            continue
        stage = str(row.get("stage") or "unknown")
        stage_job_ids[stage].add(job_id)

    result: list[dict[str, Any]] = []
    for stage, job_ids in stage_job_ids.items():
        counts: Counter = Counter()
        for job_id in job_ids:
            job = latest_job_by_id.get(job_id)
            status = _status(job) if job is not None else "unknown"
            counts[status] += 1
        item = {"stage": stage}
        item.update(dict(counts))
        item["touched_jobs"] = len(job_ids)
        result.append(item)

    return sorted(
        result,
        key=lambda item: (
            -(int(item.get("failed", 0))),
            -sum(
                int(item.get(status, 0))
                for status in (
                    "succeeded",
                    "failed",
                    "skipped",
                    "running",
                    "pending",
                    "unknown",
                )
            ),
            str(item["stage"]),
        ),
    )


def _operation_type_mix(
    rows: list[dict[str, Any]],
    resolve_operation_type,
) -> list[dict[str, Any]]:
    buckets: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        operation_type = resolve_operation_type(row)
        bucket = buckets[operation_type]
        bucket["count"] += 1
        status = _status(row)
        bucket[status] += 1
        if status == "failed":
            bucket["failed_count"] += 1
    return sorted(
        (
            {"operation_type": operation_type, **dict(values)}
            for operation_type, values in buckets.items()
        ),
        key=lambda item: (-int(item["count"]), str(item["operation_type"])),
    )


def _job_operation_types_value(job: dict[str, Any]) -> str:
    return ", ".join(_listish_values(job.get("operation_types"))) or "unknown"


def _phase_health_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _phase_duration_summary(
        rows,
        group_key="operation_type",
        resolve_group=_dataflow_operation_type,
    )


def _phase_health_by_stage(
    rows: list[dict[str, Any]], limit: int = 100
) -> list[dict[str, Any]]:
    return _phase_duration_summary(
        rows,
        group_key="stage",
        resolve_group=lambda row: str(row.get("stage") or "unknown"),
        limit=limit,
    )


def _dataflow_name_status_health(
    rows: list[dict[str, Any]], limit: int = 40
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if dataflow_id:
            buckets[dataflow_id].append(row)

    result: list[dict[str, Any]] = []
    for dataflow_id, items in buckets.items():
        statuses = Counter(_status(item) for item in items)
        executable = statuses.get("succeeded", 0) + statuses.get("failed", 0)
        durations = [
            _num(item, "duration_seconds")
            for item in items
            if _num(item, "duration_seconds") is not None
        ]
        latest = max(items, key=lambda item: _row_timestamp(item) or datetime.min)
        rows_read = _sum(items, "source_rows_read")
        rows_written = _sum(items, "destination_rows_written")
        result.append(
            {
                "dataflow_name": latest.get("dataflow_name") or dataflow_id,
                "dataflow_id": dataflow_id,
                "stage": latest.get("stage") or "unknown",
                "operation_type": _dominant_value(items, "operation_type")
                if any(item.get("operation_type") for item in items)
                else _dominant_dataflow_operation_type(items),
                "source_name": latest.get("source_name")
                or latest.get("source_connection_name"),
                "destination_name": latest.get("destination_name")
                or latest.get("destination_connection_name"),
                "runs": len(items),
                "succeeded": statuses.get("succeeded", 0),
                "failed": statuses.get("failed", 0),
                "skipped": statuses.get("skipped", 0),
                "running": statuses.get("running", 0),
                "pending": statuses.get("pending", 0),
                "unknown": statuses.get("unknown", 0),
                "success_rate": _rate(statuses.get("succeeded", 0), executable),
                "failure_rate": _rate(statuses.get("failed", 0), executable),
                "avg_duration_seconds": _avg(durations),
                "p95_duration_seconds": _percentile_clean(durations, 0.95),
                "max_duration_seconds": round(max(durations), 3) if durations else 0,
                "rows_read": rows_read,
                "rows_written": rows_written,
                "latest_time": _latest_log_at(items),
            }
        )
    return sorted(
        result,
        key=lambda row: (
            -int(row["runs"]),
            -int(row["failed"]),
            -int(row["running"]) - int(row["pending"]),
            -float(row["p95_duration_seconds"]),
            str(row["dataflow_name"]),
        ),
    )[:limit]


def _dataflow_endpoint_health(
    rows: list[dict[str, Any]], limit: int = 18
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        source = str(
            row.get("source_name") or row.get("source_connection_name") or "unknown"
        )
        destination = str(
            row.get("destination_name")
            or row.get("destination_connection_name")
            or "unknown"
        )
        buckets[(source, destination)].append(row)

    result: list[dict[str, Any]] = []
    for (source, destination), items in buckets.items():
        statuses = Counter(_status(item) for item in items)
        executable = statuses.get("succeeded", 0) + statuses.get("failed", 0)
        durations = [
            _num(item, "duration_seconds")
            for item in items
            if _num(item, "duration_seconds") is not None
        ]
        result.append(
            {
                "source_name": source,
                "destination_name": destination,
                "source_format": _dominant_value(items, "source_format"),
                "destination_format": _dominant_value(items, "destination_format"),
                "source_connection_type": _dominant_value(
                    items, "source_connection_type"
                ),
                "destination_connection_type": _dominant_value(
                    items, "destination_connection_type"
                ),
                "runs": len(items),
                "succeeded": statuses.get("succeeded", 0),
                "failed": statuses.get("failed", 0),
                "skipped": statuses.get("skipped", 0),
                "running": statuses.get("running", 0),
                "pending": statuses.get("pending", 0),
                "success_rate": _rate(statuses.get("succeeded", 0), executable),
                "avg_duration_seconds": _avg(durations),
                "p95_duration_seconds": _percentile_clean(durations, 0.95),
                "rows_read": _sum(items, "source_rows_read"),
                "rows_written": _sum(items, "destination_rows_written"),
                "bytes_added": _sum(items, "destination_bytes_added"),
                "bytes_removed": _sum(items, "destination_bytes_removed"),
            }
        )
    return sorted(
        result,
        key=lambda row: (
            -int(row["failed"]),
            -float(row["p95_duration_seconds"]),
            -float(row["runs"]),
            str(row["source_name"]),
            str(row["destination_name"]),
        ),
    )[:limit]


def _dataflow_watermark_summary(
    rows: list[dict[str, Any]], limit: int = 18
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if dataflow_id:
            buckets[dataflow_id].append(row)

    result: list[dict[str, Any]] = []
    for dataflow_id, items in buckets.items():
        latest = max(
            items,
            key=lambda item: _time_value(
                item.get("end_time") or item.get("start_time")
            ),
        )
        signals = [_watermark_classification(item) for item in items]
        statuses = Counter(signal["movement_state"] for signal in signals)
        adjustments = Counter(signal["adjustment_state"] for signal in signals)
        run_statuses = Counter(_status(item) for item in items)
        not_configured = statuses.get("not_configured", 0) + statuses.get("missing", 0)
        result.append(
            {
                "dataflow_id": dataflow_id,
                "dataflow_name": latest.get("dataflow_name") or dataflow_id,
                "runs": len(items),
                "advanced": statuses.get("advanced", 0),
                "initialized": statuses.get("initialized", 0),
                "unchanged": statuses.get("unchanged", 0),
                "incomplete": statuses.get("incomplete", 0),
                "adjusted": adjustments.get("adjusted", 0),
                "missing": not_configured,
                "not_configured": not_configured,
                "invalid": statuses.get("invalid", 0),
                "unknown": statuses.get("unknown", 0),
                "skipped": run_statuses.get("skipped", 0),
                "failed": run_statuses.get("failed", 0),
                "latest_time": _latest_log_at(items),
            }
        )
    return sorted(
        result,
        key=lambda row: (
            -int(row["failed"]),
            -int(row["unchanged"]) - int(row["invalid"]),
            -int(row["skipped"]),
            -int(row["runs"]),
            str(row["dataflow_name"]),
        ),
    )[:limit]


def _latest_log_at(rows: list[dict[str, Any]]) -> str | None:
    timestamps = [
        timestamp for row in rows if (timestamp := _row_timestamp(row)) is not None
    ]
    return max(timestamps).isoformat() if timestamps else None


def _dominant_status(statuses: Counter) -> str:
    for status in ("failed", "running", "pending", "succeeded", "skipped"):
        if statuses.get(status, 0):
            return status
    return "unknown"


def _operation_windows(
    rows: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    timezone_info: tzinfo = timezone.utc,
    now: datetime | None = None,
) -> dict[str, Any]:
    now_value = now or datetime.now(timezone_info)
    if now_value.tzinfo is None:
        now_value = now_value.replace(tzinfo=timezone_info)
    else:
        now_value = now_value.astimezone(timezone_info)
    today = now_value.date()
    last_24_hours = now_value - timedelta(hours=24)
    last_7_days = now_value - timedelta(days=7)
    return {
        "today": _operation_window_summary(
            [job for job in jobs if _is_same_local_date(job, today, timezone_info)],
            [row for row in rows if _is_same_local_date(row, today, timezone_info)],
        ),
        "last_24_hours": _operation_window_summary(
            [job for job in jobs if _is_since(job, last_24_hours)],
            [row for row in rows if _is_since(row, last_24_hours)],
        ),
        "last_7_days": _operation_window_summary(
            [job for job in jobs if _is_since(job, last_7_days)],
            [row for row in rows if _is_since(row, last_7_days)],
        ),
    }


def _operation_window_summary(
    jobs: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    job_status = Counter(_status(row) for row in jobs)
    dataflow_status = Counter(_status(row) for row in rows)
    executable_jobs = job_status.get("succeeded", 0) + job_status.get("failed", 0)
    executable_dataflows = dataflow_status.get("succeeded", 0) + dataflow_status.get(
        "failed", 0
    )
    return {
        "job_runs": len(jobs),
        "job_succeeded": job_status.get("succeeded", 0),
        "job_failed": job_status.get("failed", 0),
        "job_running": _sum(jobs, "total_running"),
        "job_pending": _sum(jobs, "total_pending"),
        "job_skipped": _sum(jobs, "total_skipped"),
        "job_success_rate": _rate(job_status.get("succeeded", 0), executable_jobs),
        "job_failure_rate": _rate(job_status.get("failed", 0), executable_jobs),
        "dataflow_runs": len(rows),
        "dataflow_succeeded": dataflow_status.get("succeeded", 0),
        "dataflow_failed": dataflow_status.get("failed", 0),
        "dataflow_running": dataflow_status.get("running", 0),
        "dataflow_pending": dataflow_status.get("pending", 0),
        "dataflow_skipped": dataflow_status.get("skipped", 0),
        "dataflow_success_rate": _rate(
            dataflow_status.get("succeeded", 0), executable_dataflows
        ),
        "dataflow_failure_rate": _rate(
            dataflow_status.get("failed", 0), executable_dataflows
        ),
    }


def _is_same_local_date(row: dict[str, Any], date_value, timezone_info: tzinfo) -> bool:
    timestamp = _row_timestamp(row)
    if timestamp is None:
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone_info).date() == date_value


def _is_since(row: dict[str, Any], start: datetime) -> bool:
    timestamp = _row_timestamp(row)
    if timestamp is None:
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc) >= start


def _failed_count_in_window(rows: list[dict[str, Any]], days: int) -> int:
    now = datetime.now(timezone.utc)
    count = 0
    for row in rows:
        if _status(row) != "failed":
            continue
        timestamp = _row_timestamp(row)
        if timestamp is None:
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        if (now - timestamp.astimezone(timezone.utc)).total_seconds() <= days * 86400:
            count += 1
    return count


def _maintenance_count_in_window(
    rows: list[dict[str, Any]], days: int, statuses: set[str]
) -> int:
    now = datetime.now(timezone.utc)
    count = 0
    for row in rows:
        if not _is_maintenance_row(row):
            continue
        if _status(row) not in statuses:
            continue
        timestamp = _row_timestamp(row)
        if timestamp is None:
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        if (now - timestamp.astimezone(timezone.utc)).total_seconds() <= days * 86400:
            count += 1
    return count


def _is_maintenance_row(row: dict[str, Any]) -> bool:
    operation_type = str(row.get("operation_type") or "").strip().lower()
    destination_operation_type = (
        str(row.get("destination_operation_type") or "").strip().lower()
    )
    return operation_type == "maintenance" or destination_operation_type in {
        "compact",
        "cleanup",
        "maintenance",
    }


def _first(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None


def _format_seconds(value: float) -> str:
    if value < 60:
        return f"{value:.2f}s"
    return f"{value / 60:.2f}m"
