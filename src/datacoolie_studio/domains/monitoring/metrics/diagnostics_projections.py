from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from datacoolie_studio.core.time import parse_utc_datetime
from datacoolie_studio.domains.monitoring.metrics.common_projections import (
    _date_bucket,
    _ensure_aware,
    _has_value,
    _num,
    _rate,
    _run_date,
    _status,
    _time_value,
)


def _diagnostics_page(
    rows: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    errors: list[dict[str, str]],
    reconciliation: dict[str, Any],
    *,
    trend_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = _diagnostics_context(rows, jobs)
    evidence = _diagnostics_job_id_evidence(context)
    record_evidence = _diagnostics_record_evidence_by_date(
        rows, jobs, trend_context=trend_context
    )
    field_completeness = _diagnostics_field_completeness(rows, jobs)
    source_coverage = _diagnostics_source_coverage(rows, jobs, errors)
    reconciliation_by_metric = _diagnostics_reconciliation_by_metric(reconciliation)
    investigation_queue = _diagnostics_investigation_queue(
        context,
        errors=errors,
        reconciliation=reconciliation,
        field_completeness=field_completeness,
        source_coverage=source_coverage,
    )
    read_errors = len(errors)
    orphan_count = len(context["orphan_job_ids"])
    job_only_count = len(context["job_only_ids"])
    matched_count = len(context["matched_ids"])
    union_count = len(context["all_job_ids"])
    field_issues = sum(
        1
        for row in field_completeness
        if row.get("actionable")
        and float(
            row.get("completeness_rate")
            if row.get("completeness_rate") is not None
            else 100
        )
        < 95
    )
    conditional_evidence_groups = sum(
        1 for row in field_completeness if row.get("applicability") == "conditional"
    )
    cache_partial_sources = sum(
        1 for row in source_coverage if row.get("warning_count")
    )
    health_status = _diagnostics_health_status(
        rows,
        jobs,
        read_errors=read_errors,
        orphan_job_ids=orphan_count,
        jobs_without_dataflow_records=job_only_count,
        reconciliation_mismatches=int(reconciliation.get("mismatch_count") or 0),
        cache_partial_source_count=cache_partial_sources,
    )
    return {
        "kpis": {
            "health_status": health_status,
            "matched_job_ids": matched_count,
            "orphan_dataflow_job_ids": orphan_count,
            "jobs_without_dataflow_records": job_only_count,
            "job_linkage_rate": _rate(matched_count, union_count),
            "reconciliation_mismatches": reconciliation.get("mismatch_count", 0),
            "affected_reconciliation_jobs": len(
                {
                    str(check.get("job_id") or "")
                    for check in reconciliation.get("checks", [])
                    if check.get("job_id")
                }
            ),
            "read_errors": read_errors,
            "cache_warning_count": cache_partial_sources,
            "field_readiness_rate": _diagnostics_field_readiness_rate(
                field_completeness
            ),
            "field_readiness_issues": field_issues,
            "conditional_evidence_groups": conditional_evidence_groups,
        },
        "record_evidence_by_date": record_evidence,
        "job_linkage_summary": _diagnostics_job_linkage_summary(context),
        "reconciliation_by_metric": reconciliation_by_metric,
        "field_completeness": field_completeness,
        "source_coverage": source_coverage,
        "investigation_queue": investigation_queue,
        "job_id_evidence": evidence,
        "read_errors": errors[:50],
    }


def _diagnostics_context(
    rows: list[dict[str, Any]], jobs: list[dict[str, Any]]
) -> dict[str, Any]:
    rows_by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        job_id = _diagnostics_job_id(row)
        if job_id:
            rows_by_job[job_id].append(row)
    jobs_by_id = {
        _diagnostics_job_id(job): job for job in jobs if _diagnostics_job_id(job)
    }
    dataflow_job_ids = set(rows_by_job)
    job_ids = set(jobs_by_id)
    return {
        "rows_by_job": rows_by_job,
        "jobs_by_id": jobs_by_id,
        "dataflow_job_ids": dataflow_job_ids,
        "job_ids": job_ids,
        "matched_ids": dataflow_job_ids & job_ids,
        "orphan_job_ids": dataflow_job_ids - job_ids,
        "job_only_ids": job_ids - dataflow_job_ids,
        "all_job_ids": dataflow_job_ids | job_ids,
    }


def _diagnostics_job_id(row: dict[str, Any]) -> str:
    value = str(row.get("job_id") or "").strip()
    return (
        ""
        if not value or value.lower() in {"none", "null", "nan", "unknown"}
        else value
    )


def _diagnostics_job_id_evidence(
    context: dict[str, Any], limit_per_category: int = 50
) -> list[dict[str, Any]]:
    rows_by_job: dict[str, list[dict[str, Any]]] = context["rows_by_job"]
    jobs_by_id: dict[str, dict[str, Any]] = context["jobs_by_id"]
    evidence: list[dict[str, Any]] = []
    for job_id in sorted(context["matched_ids"])[:limit_per_category]:
        job = jobs_by_id[job_id]
        items = rows_by_job[job_id]
        evidence.append(_diagnostics_job_linkage_row("matched", job_id, job, items))
    for job_id in sorted(context["orphan_job_ids"])[:limit_per_category]:
        items = rows_by_job[job_id]
        latest = max(
            items,
            key=lambda item: _time_value(
                item.get("end_time") or item.get("start_time")
            ),
        )
        evidence.append(
            _diagnostics_job_linkage_row(
                "orphan_dataflow_job_id", job_id, None, items, latest=latest
            )
        )
    for job_id in sorted(context["job_only_ids"])[:limit_per_category]:
        job = jobs_by_id[job_id]
        evidence.append(
            _diagnostics_job_linkage_row(
                "job_without_dataflow_records", job_id, job, []
            )
        )
    return evidence


def _diagnostics_job_linkage_row(
    category: str,
    job_id: str,
    job: dict[str, Any] | None,
    items: list[dict[str, Any]],
    latest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    latest_row = (
        latest
        or job
        or (
            max(
                items,
                key=lambda item: _time_value(
                    item.get("end_time") or item.get("start_time")
                ),
            )
            if items
            else {}
        )
    )
    return {
        "category": category,
        "job_id": job_id,
        "job_status": _status(job) if job else "missing_job_log",
        "dataflow_records": len(items),
        "job_total_dataflows": int(_num(job, "total_dataflows") or 0) if job else None,
        "latest_time": latest_row.get("end_time") or latest_row.get("start_time"),
    }


def _diagnostics_record_evidence_by_date(
    rows: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    *,
    trend_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "bucket": "",
            "date": "",
            "job_records": 0,
            "dataflow_records": 0,
            "matched_job_ids": 0,
            "orphan_dataflow_job_ids": 0,
            "jobs_without_dataflow_records": 0,
        }
    )
    rows_by_bucket_job: dict[str, set[str]] = defaultdict(set)
    jobs_by_bucket_id: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        bucket = _diagnostics_time_bucket(row, trend_context)
        item = buckets[bucket]
        item["bucket"] = bucket
        item["date"] = bucket
        item["dataflow_records"] += 1
        job_id = _diagnostics_job_id(row)
        if job_id:
            rows_by_bucket_job[bucket].add(job_id)
    for job in jobs:
        bucket = _diagnostics_time_bucket(job, trend_context)
        item = buckets[bucket]
        item["bucket"] = bucket
        item["date"] = bucket
        item["job_records"] += 1
        job_id = _diagnostics_job_id(job)
        if job_id:
            jobs_by_bucket_id[bucket].add(job_id)
    for bucket, item in buckets.items():
        dataflow_ids = rows_by_bucket_job[bucket]
        job_ids = jobs_by_bucket_id[bucket]
        item["matched_job_ids"] = len(dataflow_ids & job_ids)
        item["orphan_dataflow_job_ids"] = len(dataflow_ids - job_ids)
        item["jobs_without_dataflow_records"] = len(job_ids - dataflow_ids)
        item["linkage_rate"] = _rate(
            item["matched_job_ids"], len(dataflow_ids | job_ids)
        )
    for bucket in _diagnostics_expected_trend_buckets(trend_context):
        item = buckets[bucket]
        item["bucket"] = bucket
        item["date"] = bucket
        item.setdefault("linkage_rate", 0)
    return sorted(buckets.values(), key=lambda item: str(item["bucket"]))


def _diagnostics_time_bucket(
    row: dict[str, Any], trend_context: dict[str, Any] | None
) -> str:
    if trend_context:
        bucket = _date_bucket(row, trend_context).get("bucket")
        if bucket:
            return str(bucket)
    return _run_date(row)


def _diagnostics_expected_trend_buckets(
    trend_context: dict[str, Any] | None,
) -> list[str]:
    if not trend_context:
        return []
    start = parse_utc_datetime(trend_context.get("start"))
    end = parse_utc_datetime(trend_context.get("end"))
    if start is None or end is None:
        return []
    timezone_info = trend_context.get("timezone_info") or timezone.utc
    start = _ensure_aware(start).astimezone(timezone_info)
    end = _ensure_aware(end).astimezone(timezone_info)
    if end < start:
        start, end = end, start
    grain = str(trend_context.get("effective_grain") or "day")
    if grain == "hour":
        current = start.replace(minute=0, second=0, microsecond=0)
        step = timedelta(hours=1)
    elif grain == "week":
        current = (start - timedelta(days=start.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        step = timedelta(days=7)
    elif grain == "month":
        current = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        step = None
    else:
        current = start.replace(hour=0, minute=0, second=0, microsecond=0)
        step = timedelta(days=1)
    buckets: list[str] = []
    while current <= end:
        buckets.append(_diagnostics_bucket_label(current, grain))
        if grain == "month":
            current = (
                current.replace(year=current.year + 1, month=1)
                if current.month == 12
                else current.replace(month=current.month + 1)
            )
        else:
            current = current + (step or timedelta(days=1))
    return buckets


def _diagnostics_bucket_label(value: datetime, grain: str) -> str:
    if grain == "hour":
        return value.strftime("%Y-%m-%d %H:00")
    if grain == "week":
        iso_year, iso_week, _ = value.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if grain == "month":
        return value.strftime("%Y-%m")
    return value.strftime("%Y-%m-%d")


def _diagnostics_job_linkage_summary(context: dict[str, Any]) -> list[dict[str, Any]]:
    orphan_count = len(context["orphan_job_ids"])
    job_only_count = len(context["job_only_ids"])
    return [
        {
            "category": "matched",
            "label": "Matched",
            "count": len(context["matched_ids"]),
            "share": _rate(len(context["matched_ids"]), len(context["all_job_ids"])),
            "severity": "good",
        },
        {
            "category": "orphan_dataflow_job_id",
            "label": "Orphan dataflow job IDs",
            "count": orphan_count,
            "share": _rate(orphan_count, len(context["all_job_ids"])),
            "severity": "bad" if orphan_count else "good",
        },
        {
            "category": "job_without_dataflow_records",
            "label": "Job-only IDs",
            "count": job_only_count,
            "share": _rate(job_only_count, len(context["all_job_ids"])),
            "severity": "warning" if job_only_count else "good",
        },
    ]


def _diagnostics_reconciliation_by_metric(
    reconciliation: dict[str, Any],
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "metric": "",
            "mismatch_count": 0,
            "affected_jobs": 0,
            "absolute_difference": 0,
            "severity": "warning",
        }
    )
    job_ids_by_metric: dict[str, set[str]] = defaultdict(set)
    for check in reconciliation.get("checks", []):
        metric = str(check.get("metric") or "unknown")
        bucket = buckets[metric]
        bucket["metric"] = metric
        bucket["mismatch_count"] += 1
        bucket["absolute_difference"] += abs(int(_num(check, "difference") or 0))
        job_id = str(check.get("job_id") or "")
        if job_id:
            job_ids_by_metric[metric].add(job_id)
    for metric, bucket in buckets.items():
        bucket["affected_jobs"] = len(job_ids_by_metric[metric])
    return sorted(
        buckets.values(),
        key=lambda item: (-int(item["mismatch_count"]), str(item["metric"])),
    )


def _diagnostics_field_completeness(
    rows: list[dict[str, Any]], jobs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    groups = [
        (
            "identity/linkage",
            "dataflow",
            rows,
            ("job_id", "dataflow_id", "dataflow_run_id", "dataflow_name"),
            "universal",
        ),
        (
            "time/status",
            "dataflow",
            rows,
            ("status", "start_time", "end_time"),
            "universal",
        ),
        (
            "runtime duration",
            "dataflow",
            rows,
            (
                "duration_seconds",
                "source_duration_seconds",
                "transform_duration_seconds",
                "destination_duration_seconds",
            ),
            "universal",
        ),
        (
            "source evidence",
            "dataflow",
            rows,
            ("source_name", "source_connection_type", "source_rows_read"),
            "universal",
        ),
        (
            "destination evidence",
            "dataflow",
            rows,
            (
                "destination_name",
                "destination_connection_type",
                "destination_load_type",
            ),
            "universal",
        ),
        (
            "watermark evidence",
            "dataflow",
            rows,
            (
                "source_watermark_columns",
                "source_watermark_before",
                "source_watermark_after",
            ),
            "conditional",
        ),
        (
            "maintenance evidence",
            "dataflow",
            rows,
            (
                "destination_operation_type",
                "destination_files_removed",
                "destination_bytes_removed",
            ),
            "conditional",
        ),
        ("identity/linkage", "job", jobs, ("job_id",), "universal"),
        ("time/status", "job", jobs, ("status", "start_time", "end_time"), "universal"),
        ("runtime duration", "job", jobs, ("duration_seconds",), "universal"),
        (
            "job totals",
            "job",
            jobs,
            ("total_dataflows", "total_succeeded", "total_failed", "total_skipped"),
            "universal",
        ),
        (
            "runtime context",
            "job",
            jobs,
            ("engine_name", "metadata_provider_name", "platform_name"),
            "universal",
        ),
    ]
    result = []
    for group, record_type, items, fields, applicability in groups:
        total_slots = len(items) * len(fields)
        present = sum(
            1 for item in items for field in fields if _has_value(item.get(field))
        )
        missing = max(0, total_slots - present)
        completeness = _rate(present, total_slots)
        result.append(
            {
                "group": group,
                "record_type": record_type,
                "fields": ", ".join(fields),
                "records": len(items),
                "required_fields": len(fields),
                "present_values": present,
                "missing_values": missing,
                "completeness_rate": completeness,
                "severity": _diagnostics_completeness_severity(
                    completeness, len(items)
                ),
                "applicability": applicability,
                "actionable": applicability == "universal",
            }
        )
    return result


def _diagnostics_completeness_severity(rate: float, records: int) -> str:
    if records == 0:
        return "info"
    if rate < 80:
        return "bad"
    if rate < 95:
        return "warning"
    return "good"


def _diagnostics_field_readiness_rate(rows: list[dict[str, Any]]) -> float:
    weighted_total = sum(
        float(row.get("records") or 0) * float(row.get("required_fields") or 0)
        for row in rows
    )
    weighted_present = sum(float(row.get("present_values") or 0) for row in rows)
    return _rate(weighted_present, weighted_total)


def _diagnostics_source_coverage(
    rows: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "source": "",
            "source_id": None,
            "file_kind": "unknown",
            "file_count": 0,
            "job_records": 0,
            "dataflow_records": 0,
            "records": 0,
            "latest_log_at": None,
            "latest_ingested_at": None,
            "warning_count": 0,
        }
    )
    files_by_source: dict[str, set[str]] = defaultdict(set)
    for record_type, items in (("dataflow", rows), ("job", jobs)):
        for row in items:
            key = _diagnostics_source_key(row)
            bucket = buckets[key]
            bucket["source"] = key
            bucket["source_id"] = row.get("_source_id")
            bucket["file_kind"] = (
                row.get("_file_kind") or bucket["file_kind"] or "unknown"
            )
            file_uri = str(row.get("_file_uri") or "")
            if file_uri:
                files_by_source[key].add(file_uri)
            bucket["records"] += 1
            bucket[f"{record_type}_records"] += 1
            bucket["latest_log_at"] = _latest_timestamp_value(
                bucket["latest_log_at"], row.get("end_time") or row.get("start_time")
            )
            bucket["latest_ingested_at"] = _latest_timestamp_value(
                bucket["latest_ingested_at"], row.get("_ingested_at")
            )
    for error in errors:
        key = str(error.get("uri") or error.get("path") or "read/cache warnings")
        bucket = buckets[key]
        bucket["source"] = key
        bucket["warning_count"] += 1
    for key, bucket in buckets.items():
        bucket["file_count"] = len(files_by_source[key])
        bucket["status"] = "warning" if bucket["warning_count"] else "ok"
    return sorted(
        buckets.values(),
        key=lambda item: (
            -int(item["warning_count"]),
            -int(item["records"]),
            str(item["source"]),
        ),
    )


def _diagnostics_source_key(row: dict[str, Any]) -> str:
    source_id = row.get("_source_id")
    if source_id not in (None, ""):
        return f"source:{source_id}"
    file_uri = row.get("_file_uri")
    if file_uri:
        return str(file_uri)
    return "direct-reader"


def _latest_timestamp_value(current: object, candidate: object) -> str | None:
    current_time = _time_value(current)
    candidate_time = _time_value(candidate)
    if candidate_time == datetime.min.replace(tzinfo=timezone.utc):
        return str(current) if current else None
    return (
        candidate_time.isoformat()
        if candidate_time > current_time
        else (str(current) if current else None)
    )


def _diagnostics_investigation_queue(
    context: dict[str, Any],
    *,
    errors: list[dict[str, str]],
    reconciliation: dict[str, Any],
    field_completeness: list[dict[str, Any]],
    source_coverage: list[dict[str, Any]],
    limit: int = 200,
) -> list[dict[str, Any]]:
    rows_by_job: dict[str, list[dict[str, Any]]] = context["rows_by_job"]
    jobs_by_id: dict[str, dict[str, Any]] = context["jobs_by_id"]
    queue: list[dict[str, Any]] = []
    for error in errors:
        queue.append(
            _diagnostics_queue_row(
                severity="bad",
                category="read/cache warning",
                issue=str(
                    error.get("message") or error.get("error") or "Read/cache warning"
                ),
                target=str(error.get("uri") or error.get("path") or "log source"),
                evidence=error,
                action_hint="Check source path, credentials, file format, then sync logs again.",
            )
        )
    for job_id in sorted(context["orphan_job_ids"]):
        items = rows_by_job[job_id]
        latest = max(
            items,
            key=lambda item: _time_value(
                item.get("end_time") or item.get("start_time")
            ),
        )
        queue.append(
            _diagnostics_queue_row(
                severity="bad",
                category="orphan dataflow job id",
                issue="Dataflow records reference a job_id with no matching job log.",
                target=job_id,
                latest_time=latest.get("end_time") or latest.get("start_time"),
                evidence={"job_id": job_id, "dataflow_records": len(items)},
                action_hint="Check job_run_log coverage for the same run window.",
            )
        )
    for job_id in sorted(context["job_only_ids"]):
        job = jobs_by_id[job_id]
        queue.append(
            _diagnostics_queue_row(
                severity="warning",
                category="job without dataflows",
                issue="Job log exists but no child dataflow records were found.",
                target=job_id,
                latest_time=job.get("end_time") or job.get("start_time"),
                evidence={
                    "job_id": job_id,
                    "job_total_dataflows": int(_num(job, "total_dataflows") or 0),
                },
                action_hint="Check dataflow_run_log coverage and cache sync for this job.",
            )
        )
    for check in reconciliation.get("checks", []):
        queue.append(
            _diagnostics_queue_row(
                severity=str(check.get("severity") or "warning"),
                category="reconciliation mismatch",
                issue=f"{check.get('metric') or 'metric'} expected {check.get('expected')} but observed {check.get('observed')}.",
                target=str(check.get("job_id") or "job"),
                evidence=check,
                action_hint="Inspect the job drawer and child dataflow records.",
            )
        )
    for row in field_completeness:
        if not row.get("actionable"):
            continue
        severity = str(row.get("severity") or "good")
        if severity not in {"bad", "warning"}:
            continue
        queue.append(
            _diagnostics_queue_row(
                severity=severity,
                category="field completeness",
                issue=f"{row.get('record_type')} {row.get('group')} completeness is {row.get('completeness_rate')}%.",
                target=f"{row.get('record_type')} · {row.get('group')}",
                evidence=row,
                action_hint="Confirm the log version emits the fields used by Monitoring pages.",
            )
        )
    for row in source_coverage:
        if not row.get("warning_count"):
            continue
        queue.append(
            _diagnostics_queue_row(
                severity="warning",
                category="source coverage",
                issue=f"{row.get('warning_count')} warning(s) for this log source.",
                target=str(row.get("source") or "source"),
                latest_time=row.get("latest_log_at"),
                evidence=row,
                action_hint="Open Sources and sync or validate this ETL log path.",
            )
        )
    queue.sort(
        key=lambda row: (
            -_diagnostics_severity_rank(str(row.get("severity"))),
            -_diagnostics_sort_timestamp(row.get("latest_time")),
            str(row.get("category")),
        )
    )
    return queue[:limit]


def _diagnostics_queue_row(
    *,
    severity: str,
    category: str,
    issue: str,
    target: str,
    evidence: dict[str, Any],
    action_hint: str,
    latest_time: object = None,
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


def _diagnostics_severity_rank(value: str) -> int:
    return {"bad": 4, "error": 4, "warning": 3, "info": 2, "good": 1}.get(
        value.lower(), 0
    )


def _diagnostics_sort_timestamp(value: object) -> float:
    timestamp = _time_value(value)
    if timestamp == datetime.min.replace(tzinfo=timezone.utc):
        return 0.0
    try:
        return timestamp.timestamp()
    except (OSError, OverflowError, ValueError):
        return 0.0


def _diagnostics_health_status(
    rows: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    *,
    read_errors: int,
    orphan_job_ids: int,
    jobs_without_dataflow_records: int,
    reconciliation_mismatches: int,
    cache_partial_source_count: int,
) -> str:
    if not rows and not jobs:
        return "no_evidence"
    if (
        read_errors
        or orphan_job_ids
        or jobs_without_dataflow_records
        or reconciliation_mismatches
    ):
        return "has_issues"
    if bool(rows) != bool(jobs) or cache_partial_source_count:
        return "warning"
    return "healthy"
