from __future__ import annotations

import json
import re
import hashlib
from collections import Counter, defaultdict
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from datacoolie_studio.core.time import parse_utc_datetime
from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.logs.cache import (
    AnalyticsRebuildRequired,
    analytics_materialization_token,
    cached_monitoring_summary,
    query_cached_dataflow_logs,
    query_cached_job_logs,
    query_cached_latest_dataflow_runs,
)
from datacoolie_studio.domains.logs.reader import read_dataflow_logs, read_job_logs
from datacoolie_studio.domains.logs.source_config import resolve_log_source_paths
from datacoolie_studio.domains.monitoring.repository import monitoring_filter_options_read_model
from datacoolie_studio.domains.monitoring.metrics.failure import (
    categorize_failure,
    classify_failure,
    dataflow_failed_phases,
    dataflow_failure_phase_and_message,
)
from datacoolie_studio.domains.monitoring.metrics.health import environment_health
from datacoolie_studio.domains.read_models.cache import (
    cached_read_model,
    empty_parameters_fingerprint,
    read_model_build_lock,
    read_model_generation,
    replace_read_model,
)
from datacoolie_studio.domains.read_models.keys import LINEAGE_LATEST_RUNS


_DATE_GRAINS = ("hour", "day", "week", "month")
_STATUS_KEYS = ("succeeded", "failed", "skipped", "running", "pending", "unknown")
_FRESHNESS_STALE_DAYS = 7
_SKIPPED_STREAK_RUNS = 3
_MAINTENANCE_LAG_WARNING_DAYS = 7
_LATEST_RUNS_PRODUCER_VERSION = "lineage-latest-runs-v1"

def cached_environment_overview_summary(
    session: Session,
    paths: list[EnvironmentSource],
    *,
    timezone_info: tzinfo,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the SQL-backed Environment Overview Monitoring summary."""
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    local_boundary = now_utc.astimezone(timezone_info).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    cutoff = (local_boundary - timedelta(days=30)).astimezone(timezone.utc)
    timezone_name = timezone_info.key if isinstance(timezone_info, ZoneInfo) else None
    now_offset = now_utc.astimezone(timezone_info).utcoffset()
    cutoff_offset = cutoff.astimezone(timezone_info).utcoffset()
    if not timezone_name and now_offset != cutoff_offset:
        raise ValueError("Environment timezone must have a stable name across the overview range")
    cached = cached_monitoring_summary(
        session,
        paths,
        cutoff=cutoff,
        timezone_name=timezone_name,
        utc_offset_seconds=int(now_offset.total_seconds()) if now_offset else 0,
        local_today=now_utc.astimezone(timezone_info).date(),
    )
    if cached is None:
        raise ValueError("Environment timezone must provide a name or fixed UTC offset")

    summary, errors = cached
    succeeded = int(summary["dataflow_succeeded"] or 0)
    failed = int(summary["dataflow_failed"] or 0)
    latest_log_at = parse_utc_datetime(summary["latest_log_at"])
    return {
        "job_records": int(summary["job_records"] or 0),
        "total_failures": int(summary["total_failures"] or 0),
        "dataflow_success_rate": _rate(succeeded, succeeded + failed),
        "failed_job_windows": {
            "last7": int(summary["failed_last7"] or 0),
            "last30": int(summary["failed_last30"] or 0),
            "last365": int(summary["failed_last365"] or 0),
        },
        "active_engines": int(summary["active_engines"] or 0),
        "latest_log_at": latest_log_at.isoformat() if latest_log_at else None,
        "date_range": {
            "min": str(summary["date_min"]) if summary["date_min"] else None,
            "max": str(summary["date_max"]) if summary["date_max"] else None,
        },
        "errors": errors,
    }


def _empty_health_page() -> dict[str, Any]:
    return {"status": "unknown", "label": "Unknown", "reasons": []}


def _empty_operations_page() -> dict[str, Any]:
    return {
        "kpis": {},
        "windows": {},
        "job_status_distribution": [],
        "jobs_by_date_status": [],
        "dataflows_by_date_status": [],
        "failed_jobs": [],
        "dataflow_kpis": {},
        "status_by_stage": [],
    }


def _empty_failures_page() -> dict[str, Any]:
    return {
        "failed_by_stage": [],
        "failed_by_source_connection_type": [],
        "top_failing_dataflows": [],
        "error_categories": [],
        "failure_trend_by_date": [],
        "failed_records": [],
    }


def _empty_performance_page() -> dict[str, Any]:
    return {
        "duration_breakdown": [],
        "duration_vs_rows": [],
        "slowest_dataflows": [],
        "duration_by_stage": [],
        "engine_stage_matrix": [],
    }


def _empty_volume_page() -> dict[str, Any]:
    return {
        "kpis": {},
        "rows_by_date": [],
        "bytes_by_date": [],
        "volume_by_load_type": [],
        "top_dataflows_by_rows_written": [],
    }


def _empty_maintenance_page() -> dict[str, Any]:
    return {
        "kpis": {},
        "bytes_reclaimed_by_table": [],
        "format_comparison": [],
        "per_table": [],
        "duration_vs_files_removed": [],
        "bytes_reclaimed_by_date": [],
    }


def _empty_freshness_page() -> dict[str, Any]:
    return {
        "kpis": {},
        "latest_freshness_by_dataflow": [],
        "watermark_movement": [],
        "stale_candidates": [],
        "skipped_patterns": [],
    }


def _empty_diagnostics_page() -> dict[str, Any]:
    return {"kpis": {}, "job_id_evidence": [], "read_errors": []}


def _dataflows_operations_page(
    rows: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    timezone_info: tzinfo,
    trend_context: dict[str, Any],
) -> dict[str, Any]:
    result = _empty_operations_page()
    statuses = Counter(_status(row) for row in rows)
    executable = [row for row in rows if _status(row) in {"succeeded", "failed"}]
    durations = [_num(row, "duration_seconds") for row in executable]
    executable_count = statuses.get("succeeded", 0) + statuses.get("failed", 0)
    result.update({
        "windows": _operation_windows(rows, jobs, timezone_info=timezone_info),
        "dataflows_by_date_status": _status_by_date(rows, trend_context=trend_context),
        "dataflow_duration_by_stage": _duration_by_group(
            rows,
            "stage",
            lambda row: str(row.get("stage") or "unknown"),
            limit=100,
        ),
        "dataflow_duration_stats": _duration_stats(executable),
        "phase_health_by_stage": _phase_health_by_stage(rows),
        "dataflow_endpoint_health": _dataflow_endpoint_health(rows),
        "dataflow_name_status_health": _dataflow_name_status_health(rows),
        "dataflow_kpis": {
            "total_dataflows": len(rows),
            "succeeded": statuses.get("succeeded", 0),
            "failed": statuses.get("failed", 0),
            "skipped": statuses.get("skipped", 0),
            "pending": statuses.get("pending", 0),
            "running": statuses.get("running", 0),
            "success_rate": _rate(statuses.get("succeeded", 0), executable_count),
            "failure_rate": _rate(statuses.get("failed", 0), executable_count),
            "skip_rate": _rate(statuses.get("skipped", 0), len(rows)),
            "pending_rate": _rate(statuses.get("pending", 0), len(rows)),
            "running_rate": _rate(statuses.get("running", 0), len(rows)),
            "total_bytes_written": _sum(rows, "destination_bytes_added"),
            "avg_duration_seconds": _avg(durations),
            "p95_duration_seconds": _percentile_clean(durations, 0.95),
            "active_engines": len({row.get("engine_name") for row in rows if row.get("engine_name")}),
        },
    })
    return result


def _environment_overview_operations_page(
    rows: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    trend_context: dict[str, Any],
) -> dict[str, Any]:
    """Return only the monitoring fields consumed by Environment Overview."""
    result = _empty_operations_page()
    job_statuses = Counter(_status(job) for job in jobs)
    dataflow_statuses = Counter(_status(row) for row in rows)
    executable_dataflows = dataflow_statuses.get("succeeded", 0) + dataflow_statuses.get("failed", 0)
    result["kpis"] = {"total_failures": job_statuses.get("failed", 0)}
    result["dataflow_kpis"] = {
        "success_rate": _rate(dataflow_statuses.get("succeeded", 0), executable_dataflows),
    }
    result["jobs_by_date_status"] = _status_by_date(jobs, trend_context=trend_context)
    return result


def _dataflows_volume_page(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = _empty_volume_page()
    bytes_added = _sum(rows, "destination_bytes_added")
    bytes_removed = _sum(rows, "destination_bytes_removed")
    result["kpis"] = {
        "total_rows_read": _sum(rows, "source_rows_read"),
        "total_rows_written": _sum(rows, "destination_rows_written"),
        "net_bytes_change": bytes_added - bytes_removed,
    }
    return result


def _overview_volume_page(
    rows: list[dict[str, Any]],
    trend_context: dict[str, Any],
) -> dict[str, Any]:
    result = _empty_volume_page()
    result["rows_by_date"] = _rows_by_date(rows, trend_context=trend_context)
    result["bytes_by_date"] = _bytes_by_date(rows, trend_context=trend_context)
    return result


def _overview_performance_page(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute only the performance signals consumed by the Overview attention queue."""
    executable = [
        row
        for row in rows
        if _status(row) in {"succeeded", "failed"} and _num(row, "duration_seconds") is not None
    ]
    duration_stats = _duration_stats(executable)
    thresholds_by_operation = _performance_thresholds_by_operation(executable)
    candidate_count = sum(
        1
        for row in executable
        if _performance_enriched_run(
            row,
            thresholds_by_operation.get(
                _dataflow_operation_type(row),
                thresholds_by_operation["__all__"],
            ),
        ).get("performance_candidate_code")
    )
    result = _empty_performance_page()
    result["kpis"] = {
        "p50_duration_seconds": duration_stats.get("p50_duration_seconds", 0),
        "p95_duration_seconds": duration_stats.get("p95_duration_seconds", 0),
        "duration_pressure_ratio": _safe_ratio(
            duration_stats.get("p95_duration_seconds", 0) or 0,
            duration_stats.get("p50_duration_seconds", 0) or 0,
        ),
        "optimization_candidate_count": candidate_count,
    }
    result["duration_by_stage"] = _duration_by_stage(executable)
    return result


def _overview_freshness_page(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute only freshness signals consumed by Overview attention."""
    etl_rows = [row for row in rows if _dataflow_operation_type(row) == "etl"]
    freshness_rows = [row for row in etl_rows if _status(row) in {"succeeded", "skipped"}]
    latest_freshness = _latest_freshness_by_dataflow(freshness_rows)
    stale_candidates = _stale_freshness_candidates_from_latest(
        latest_freshness,
        days=_FRESHNESS_STALE_DAYS,
    )
    unchanged_watermarks = sum(
        1
        for row in etl_rows
        if _has_watermark(row) and _watermark_movement_row(row)["movement"] == "unchanged"
    )
    result = _empty_freshness_page()
    result["kpis"] = {
        "stale_candidates": len(stale_candidates),
        "watermark_unchanged_runs": unchanged_watermarks,
    }
    return result


def _overview_diagnostics_page(
    rows: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    """Compute only linkage/cache signals consumed by Overview attention."""
    context = _diagnostics_context(rows, jobs)
    source_coverage = _diagnostics_source_coverage(rows, jobs, errors)
    result = _empty_diagnostics_page()
    result["kpis"] = {
        "orphan_dataflow_job_ids": len(context["orphan_job_ids"]),
        "jobs_without_dataflow_records": len(context["job_only_ids"]),
        "cache_warning_count": sum(1 for row in source_coverage if row.get("warning_count")),
    }
    return result


def dataflow_logs(
    paths: list[EnvironmentSource],
    limit: int = 1000,
    offset: int = 0,
    filters: dict[str, str] | None = None,
    sort_by: str = "start_time",
    sort_dir: str = "desc",
    session: Session | None = None,
    timezone_info: tzinfo | None = None,
) -> dict[str, Any]:
    filters = _normalize_monitoring_filters_for_timezone(filters or {}, timezone_info=timezone_info)
    if session is not None:
        cached = query_cached_dataflow_logs(
            session,
            paths,
            filters=filters,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
        if cached is not None:
            rows, total, errors = cached
            rows = [_enrich_dataflow_run_for_investigation(row) for row in rows]
            return {
                "records": rows,
                "errors": errors,
                "summary": {"records": len(rows), "total_records": total, "limit": limit, "offset": offset, "cache": "duckdb"},
            }
        raise _analytics_unavailable(paths)
    enabled_paths = _enabled_etl_paths(paths)
    rows, errors = read_dataflow_logs(enabled_paths)
    jobs, job_errors = read_job_logs(enabled_paths)
    job_by_id = {job.get("job_id"): job for job in jobs if job.get("job_id")}
    rows = [_enrich_dataflow(row, job_by_id.get(row.get("job_id"))) for row in rows]
    rows = _filter_log_rows(rows, filters, include_dataflow_filters=True)
    rows = _sort_log_rows(rows, sort_by, sort_dir)
    total = len(rows)
    page = [_enrich_dataflow_run_for_investigation(row) for row in rows[offset:offset + limit]]
    return {"records": page, "errors": [*errors, *job_errors], "summary": {"records": len(page), "total_records": total, "limit": limit, "offset": offset}}


def job_logs(
    paths: list[EnvironmentSource],
    limit: int = 1000,
    offset: int = 0,
    filters: dict[str, str] | None = None,
    sort_by: str = "start_time",
    sort_dir: str = "desc",
    session: Session | None = None,
    timezone_info: tzinfo | None = None,
) -> dict[str, Any]:
    filters = _normalize_monitoring_filters_for_timezone(filters or {}, timezone_info=timezone_info)
    if session is not None:
        cached = query_cached_job_logs(
            session,
            paths,
            filters=filters,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
        if cached is not None:
            rows, total, errors = cached
            rows = [_enrich_job_run_for_investigation(row) for row in rows]
            return {
                "records": rows,
                "errors": errors,
                "summary": {"records": len(rows), "total_records": total, "limit": limit, "offset": offset, "cache": "duckdb"},
            }
        raise _analytics_unavailable(paths)
    enabled_paths = _enabled_etl_paths(paths)
    rows, errors = read_job_logs(enabled_paths)
    all_dataflow_rows, dataflow_errors = read_dataflow_logs(enabled_paths)
    dataflow_rows: list[dict[str, Any]] = []
    if _has_dataflow_scoped_filter(filters):
        dataflow_rows = _filter_log_rows(all_dataflow_rows, filters, include_dataflow_filters=True)
    rows = _filter_log_rows(rows, filters, include_dataflow_filters=False)
    rows = _filter_jobs_for_dataflow_scope(rows, dataflow_rows, filters)
    rows = _enrich_job_runs_for_investigation(rows, all_dataflow_rows)
    rows = _sort_log_rows(rows, sort_by, sort_dir)
    total = len(rows)
    page = rows[offset:offset + limit]
    return {"records": page, "errors": [*errors, *dataflow_errors], "summary": {"records": len(page), "total_records": total, "limit": limit, "offset": offset}}


def latest_status(paths: list[EnvironmentSource], session: Session | None = None) -> dict[str, Any]:
    environment_id = paths[0].environment_id if paths else None
    parameters_fingerprint = empty_parameters_fingerprint()
    input_fingerprint = _latest_runs_input_fingerprint(session, paths) if session is not None else ""
    if session is not None and environment_id is not None:
        cached_model = cached_read_model(
            session,
            environment_id=environment_id,
            model_key=LINEAGE_LATEST_RUNS,
            parameters_fingerprint=parameters_fingerprint,
            input_fingerprint=input_fingerprint,
            producer_version=_LATEST_RUNS_PRODUCER_VERSION,
        )
        if cached_model is not None:
            return cached_model.payload

    build_key = f"{environment_id}:{LINEAGE_LATEST_RUNS}:{parameters_fingerprint}"
    lock = read_model_build_lock(build_key) if session is not None and environment_id is not None else nullcontext()
    generation: str | None = None
    with lock:
        if session is not None and environment_id is not None:
            input_fingerprint = _latest_runs_input_fingerprint(session, paths)
            cached_model = cached_read_model(
                session,
                environment_id=environment_id,
                model_key=LINEAGE_LATEST_RUNS,
                parameters_fingerprint=parameters_fingerprint,
                input_fingerprint=input_fingerprint,
                producer_version=_LATEST_RUNS_PRODUCER_VERSION,
            )
            if cached_model is not None:
                return cached_model.payload
            generation = read_model_generation(
                environment_id=environment_id,
                model_key=LINEAGE_LATEST_RUNS,
                parameters_fingerprint=parameters_fingerprint,
                input_fingerprint=input_fingerprint,
                producer_version=_LATEST_RUNS_PRODUCER_VERSION,
            )
            focused = query_cached_latest_dataflow_runs(session, paths)
        else:
            focused = None
        if focused is not None:
            rows, ambiguous_names, errors = focused
        else:
            rows, errors = read_dataflow_logs(_enabled_etl_paths(paths))
            ambiguous_names = None
        response = _latest_runs_response(rows, errors, ambiguous_names)
        if session is not None and environment_id is not None:
            replace_read_model(
                session,
                environment_id=environment_id,
                model_key=LINEAGE_LATEST_RUNS,
                parameters_fingerprint=parameters_fingerprint,
                input_fingerprint=input_fingerprint,
                producer_version=_LATEST_RUNS_PRODUCER_VERSION,
                payload=response,
                expected_generation=generation,
            )
        return response


def latest_status_etag(session: Session, paths: list[EnvironmentSource]) -> str:
    return f'"{_latest_runs_input_fingerprint(session, paths)}:{_LATEST_RUNS_PRODUCER_VERSION}"'


def _latest_runs_response(
    rows: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    known_ambiguous_names: list[str] | None,
) -> dict[str, Any]:
    latest_by_id: dict[str, dict[str, Any]] = {}
    rows_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        dataflow_id = row.get("dataflow_id")
        if dataflow_id and (
            str(dataflow_id) not in latest_by_id
            or _is_later(row, latest_by_id[str(dataflow_id)])
        ):
            latest_by_id[str(dataflow_id)] = row
        dataflow_name = row.get("dataflow_name")
        if dataflow_name:
            rows_by_name[str(dataflow_name)].append(row)

    latest_by_name: dict[str, dict[str, Any]] = {}
    ambiguous_names = set(known_ambiguous_names or [])
    for name, items in rows_by_name.items():
        ids = {str(item["dataflow_id"]) for item in items if item.get("dataflow_id")}
        if name in ambiguous_names or len(ids) > 1:
            ambiguous_names.add(name)
            continue
        latest_by_name[name] = max(
            items,
            key=lambda item: _time_value(item.get("end_time") or item.get("start_time")),
        )
    return {
        "latest_by_id": latest_by_id,
        "latest_by_name": latest_by_name,
        "ambiguous_names": sorted(ambiguous_names),
        "errors": errors,
    }


def monitoring_input_fingerprint(session: Session, paths: list[EnvironmentSource]) -> str:
    del session  # Core manifests belong to sync change detection, not request cache keys.
    return analytics_materialization_token(paths)


def _latest_runs_input_fingerprint(session: Session, paths: list[EnvironmentSource]) -> str:
    return monitoring_input_fingerprint(session, paths)


def monitoring_filter_options(paths: list[EnvironmentSource], session: Session) -> dict[str, Any]:
    del session
    read_model = monitoring_filter_options_read_model(paths)
    values = read_model["options"]
    return {
        "options": values,
        "summary": {"source": "duckdb_filter_values", "fields": len(values)},
        "errors": [],
    }


def _filter_log_rows(
    rows: list[dict[str, Any]],
    filters: dict[str, str],
    include_dataflow_filters: bool,
) -> list[dict[str, Any]]:
    return [row for row in rows if _matches_log_filters(row, filters, include_dataflow_filters)]


def _normalize_monitoring_filters_for_timezone(
    filters: dict[str, str],
    timezone_info: tzinfo | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    normalized = dict(filters)
    range_value = (normalized.get("range") or "").strip().lower()
    active_timezone = timezone_info or timezone.utc
    now_value = now or datetime.now(active_timezone)
    if now_value.tzinfo is None:
        now_value = now_value.replace(tzinfo=active_timezone)
    else:
        now_value = now_value.astimezone(active_timezone)
    if range_value in {"24h", "3d", "7d", "30d", "90d"}:
        days = {"24h": 1, "3d": 3, "7d": 7, "30d": 30, "90d": 90}[range_value]
        boundary = (
            now_value.replace(minute=0, second=0, microsecond=0)
            if range_value == "24h"
            else now_value.replace(hour=0, minute=0, second=0, microsecond=0)
        )
        normalized["_relativeStartTime"] = (
            boundary - timedelta(days=days)
        ).astimezone(timezone.utc).isoformat()
        return normalized
    if range_value != "today":
        return normalized

    start_local = now_value.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1) - timedelta(microseconds=1)

    normalized["range"] = "custom"
    normalized["startTime"] = start_local.astimezone(timezone.utc).isoformat()
    normalized["endTime"] = end_local.astimezone(timezone.utc).isoformat()
    return normalized


def _matches_log_filters(row: dict[str, Any], filters: dict[str, str], include_dataflow_filters: bool) -> bool:
    range_value = filters.get("range")
    if range_value in {"24h", "3d", "7d", "30d", "90d"}:
        days = {"24h": 1, "3d": 3, "7d": 7, "30d": 30, "90d": 90}[range_value]
        timestamp = _row_timestamp(row)
        if timestamp is None:
            return False
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        relative_start = parse_utc_datetime(filters.get("_relativeStartTime"))
        cutoff = relative_start or datetime.now(timezone.utc) - timedelta(days=days)
        if timestamp.astimezone(timezone.utc) < cutoff:
            return False
    elif range_value == "custom":
        timestamp = _row_timestamp(row)
        if timestamp is None:
            return False
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        timestamp = timestamp.astimezone(timezone.utc)
        start_time = parse_utc_datetime(filters.get("startTime"))
        end_time = parse_utc_datetime(filters.get("endTime"))
        if start_time is not None and timestamp < start_time:
            return False
        if end_time is not None and timestamp > end_time:
            return False

    common_filters = {
        "status": "status",
        "engine": "engine_name",
        "provider": "metadata_provider_name",
    }
    for filter_key, row_key in common_filters.items():
        if not _matches_filter_value(row.get(row_key), filters.get(filter_key)):
            return False

    if include_dataflow_filters:
        dataflow_filters = {
            "stage": "stage",
            "sourceType": "source_connection_type",
            "destinationType": "destination_connection_type",
            "loadType": "destination_load_type",
            "operationType": "operation_type",
        }
        for filter_key, row_key in dataflow_filters.items():
            if not _matches_filter_value(row.get(row_key), filters.get(filter_key)):
                return False
        if not _matches_connection(row, filters.get("connection")):
            return False

    search = (filters.get("search") or "").strip().lower()
    if search and not _row_matches_search(row, search, include_dataflow_filters):
        return False
    if not _matches_investigation(row, filters, include_dataflow_filters):
        return False
    return True


def _filter_jobs_for_dataflow_scope(
    jobs: list[dict[str, Any]],
    dataflow_rows: list[dict[str, Any]],
    filters: dict[str, str],
) -> list[dict[str, Any]]:
    if not _has_dataflow_scoped_filter(filters):
        return jobs
    job_ids = {str(row.get("job_id")) for row in dataflow_rows if row.get("job_id")}
    if not job_ids:
        return []
    return [job for job in jobs if str(job.get("job_id") or "") in job_ids]


def _row_matches_search(row: dict[str, Any], search: str, include_dataflow_filters: bool) -> bool:
    if any(search in str(value or "").lower() for value in row.values()):
        return True
    if not include_dataflow_filters:
        return False
    normalized = search.replace("`", "")
    candidates = [
        _norm(row.get("source_full_table")),
        _norm(row.get("destination_full_table")),
        _maintenance_target_identity(row).lower().replace("`", ""),
    ]
    return any(normalized in candidate for candidate in candidates)


def _has_dataflow_scoped_filter(filters: dict[str, str]) -> bool:
    if _split_filter_values(filters.get("connection")):
        return True
    kind = (filters.get("investigateKind") or "").strip()
    value = (filters.get("investigateValue") or "").strip()
    return bool(value and kind and kind != "job_id")


def _matches_investigation(row: dict[str, Any], filters: dict[str, str], include_dataflow_filters: bool) -> bool:
    kind = (filters.get("investigateKind") or "").strip()
    value = (filters.get("investigateValue") or "").strip()
    if not kind or not value:
        return True
    normalized = value.lower().replace("`", "")
    if kind == "job_id":
        return _norm(row.get("job_id")) == normalized
    if not include_dataflow_filters:
        return True
    if kind == "dataflow_run_id":
        return _norm(row.get("dataflow_run_id")) == normalized
    if kind == "dataflow":
        return _norm(row.get("dataflow_id")) == normalized or _norm(row.get("dataflow_name")) == normalized
    if kind == "destination_table":
        target_identity = _maintenance_target_identity(row).lower().replace("`", "")
        return normalized in {
            target_identity,
            _norm(row.get("destination_full_table")),
            _norm(row.get("destination_table")),
            _norm(row.get("destination_path")),
        }
    return True


def _norm(value: Any) -> str:
    return str(value or "").replace("`", "").lower()


def _matches_filter_value(value: Any, filter_value: str | None) -> bool:
    selected = _split_filter_values(filter_value)
    return not selected or str(value or "unknown") in selected


def _matches_connection(row: dict[str, Any], filter_value: str | None) -> bool:
    selected = _split_filter_values(filter_value)
    if not selected:
        return True
    return str(row.get("source_name") or "unknown") in selected or str(row.get("destination_name") or "unknown") in selected


def _split_filter_values(value: str | None) -> list[str]:
    if not value or value == "all":
        return []
    return [item.strip() for item in value.split("|") if item.strip()]


def _filter_options_from_rows(rows: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    sources = {
        "operation_type": rows,
        "status": rows,
        "stage": rows,
        "source_connection_type": rows,
        "source_format": rows,
        "source_table": rows,
        "destination_connection_type": rows,
        "destination_format": rows,
        "destination_table": rows,
        "destination_load_type": rows,
        "destination_operation_type": rows,
        "engine_name": jobs,
        "metadata_provider_name": jobs,
        "platform_name": jobs,
    }
    result: dict[str, list[dict[str, Any]]] = {}
    for field, items in sources.items():
        counts = Counter(str(item.get(field)) for item in items if item.get(field) not in {None, ""})
        if counts:
            result[field] = [
                {"value": value, "label": value, "count": count}
                for value, count in sorted(counts.items(), key=lambda item: item[0])
            ]
    connection_counts: Counter[str] = Counter()
    for item in rows:
        connection_counts.update({
            str(value)
            for value in (item.get("source_name"), item.get("destination_name"))
            if value not in {None, ""}
        })
    if connection_counts:
        result["connection"] = [
            {"value": value, "label": value, "count": count}
            for value, count in sorted(connection_counts.items(), key=lambda item: item[0])
        ]
    return result


def _sort_log_rows(rows: list[dict[str, Any]], sort_by: str, sort_dir: str) -> list[dict[str, Any]]:
    reverse = sort_dir.lower() != "asc"
    return sorted(rows, key=lambda row: _sort_value(row, sort_by), reverse=reverse)


def _sort_value(row: dict[str, Any], sort_by: str) -> tuple[int, Any]:
    value = row.get(sort_by)
    if sort_by in {"end_time", "start_time"}:
        key = _time_value(value or row.get("end_time") or row.get("start_time"))
        return (0, key)
    if isinstance(value, (int, float)):
        return (0, value)
    if value is None:
        return (1, "")
    return (0, str(value).lower())


def _enrich_job_runs_for_investigation(
    jobs: list[dict[str, Any]],
    dataflow_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    child_by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in dataflow_rows:
        job_id = row.get("job_id")
        if job_id not in (None, ""):
            child_by_job[str(job_id)].append(row)
    return [_enrich_job_run_for_investigation(job, child_by_job.get(str(job.get("job_id") or ""), [])) for job in jobs]


def _enrich_job_run_for_investigation(
    job: dict[str, Any],
    child_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    child_rows = child_rows or []
    enriched = dict(job)
    if child_rows:
        child_status = Counter(_status(row) for row in child_rows)
        child_durations = [_num(row, "duration_seconds") or 0 for row in child_rows if _num(row, "duration_seconds")]
        enriched.update({
            "child_dataflow_count": len(child_rows),
            "child_succeeded_count": child_status.get("succeeded", 0),
            "child_failed_count": child_status.get("failed", 0),
            "child_skipped_count": child_status.get("skipped", 0),
            "child_running_count": child_status.get("running", 0),
            "child_pending_count": child_status.get("pending", 0),
            "child_p95_duration_seconds": _percentile_clean(child_durations, 0.95),
            "child_total_rows_read": _sum(child_rows, "source_rows_read"),
            "child_total_rows_written": _sum(child_rows, "destination_rows_written"),
            "child_total_bytes_added": _sum(child_rows, "destination_bytes_added"),
            "child_total_bytes_removed": _sum(child_rows, "destination_bytes_removed"),
        })
    for key in (
        "child_dataflow_count",
        "child_succeeded_count",
        "child_failed_count",
        "child_skipped_count",
        "child_running_count",
        "child_pending_count",
        "child_p95_duration_seconds",
        "child_total_rows_read",
        "child_total_rows_written",
        "child_total_bytes_added",
        "child_total_bytes_removed",
    ):
        enriched[key] = _num(enriched, key) or 0
    enriched["error_preview"] = _error_preview(enriched)
    enriched["reconciliation_mismatch_count"] = _job_reconciliation_mismatch_count(enriched)
    enriched["reconciliation_status"] = _job_reconciliation_status(enriched)
    enriched["job_key"] = _job_key(enriched)
    enriched["job_shape_label"] = _job_shape_label(enriched)
    return enriched


def _job_reconciliation_mismatch_count(job: dict[str, Any]) -> int:
    expected = job.get("total_dataflows")
    if expected in (None, ""):
        return 0
    observed = _num(job, "child_dataflow_count") or 0
    return 0 if int(expected or 0) == observed else 1


def _job_reconciliation_status(job: dict[str, Any]) -> str:
    if job.get("total_dataflows") in (None, "") and not (_num(job, "child_dataflow_count") or 0):
        return "not_available"
    if _job_reconciliation_mismatch_count(job):
        return "mismatch"
    return "matched"


def _enrich_dataflow_run_for_investigation(row: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(row)
    if _status(enriched) == "failed":
        enriched = _failure_enriched_dataflow(enriched)
    enriched["source_display"] = _endpoint_display(enriched, "source")
    enriched["destination_display"] = _endpoint_display(enriched, "destination")
    enriched["phase_health"] = _phase_health(enriched)
    enriched["error_phase"] = _error_phase(enriched)
    enriched["error_preview"] = _error_preview(enriched)
    watermark = _watermark_classification(enriched)
    enriched["coverage_state"] = watermark["coverage_state"]
    enriched["movement_state"] = watermark["movement_state"]
    enriched["adjustment_state"] = watermark["adjustment_state"]
    enriched["linked_job_status"] = enriched.get("job_status") or "unknown"
    enriched["linked_job_duration_seconds"] = enriched.get("job_duration_seconds")
    return enriched


def _endpoint_display(row: dict[str, Any], direction: str) -> str:
    full_table = row.get(f"{direction}_full_table")
    if full_table:
        return str(full_table).replace("`", "")
    qualified = ".".join(
        str(part)
        for part in (
            row.get(f"{direction}_catalog"),
            row.get(f"{direction}_database"),
            row.get(f"{direction}_schema"),
            row.get(f"{direction}_table"),
        )
        if part not in (None, "")
    )
    if qualified:
        return qualified
    path = row.get(f"{direction}_path")
    if path:
        return _tail_path(str(path))
    python_function = row.get(f"{direction}_python_function")
    if python_function:
        return str(python_function)
    query = row.get(f"{direction}_query")
    if query:
        return _compact_text(str(query), 80)
    return str(row.get(f"{direction}_table") or row.get(f"{direction}_name") or "unknown")


def _tail_path(path: str) -> str:
    normalized = path.replace("\\", "/").rstrip("/")
    parts = [part for part in normalized.split("/") if part]
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[-1] if parts else path


def _phase_health(row: dict[str, Any]) -> str:
    failed_phases = dataflow_failed_phases(row)
    if failed_phases:
        return f"{failed_phases[0]}_failed"
    phases = ("source", "transform", "destination", "overhead")
    durations = {phase: _performance_phase_duration(row, phase) for phase in phases}
    if any(durations.values()):
        slowest = max(durations.items(), key=lambda item: item[1])
        return f"{slowest[0]}_bottleneck"
    return "unknown" if _status(row) == "unknown" else "ok"


def _error_phase(row: dict[str, Any]) -> str:
    failed_phases = dataflow_failed_phases(row)
    if failed_phases:
        return failed_phases[0]
    for phase in ("source", "transform", "destination"):
        if row.get(f"{phase}_error_message"):
            return phase
    return "job" if row.get("error_message") else ""


def _error_preview(row: dict[str, Any]) -> str:
    message = (
        row.get("error_message")
        or row.get("source_error_message")
        or row.get("transform_error_message")
        or row.get("destination_error_message")
        or row.get("last_error")
        or ""
    )
    return _compact_text(str(message), 140)


def _compact_text(value: str, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", value or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: max(0, limit - 3)]}..."


def _watermark_classification(row: dict[str, Any]) -> dict[str, str | None]:
    value_keys = (
        "source_watermark_before",
        "source_watermark_after",
        "source_watermark_effective",
        "source_watermark_columns",
    )
    values = [row.get(key) for key in value_keys]
    if not any(not _is_missing_value(value) for value in values):
        return {
            "coverage_state": "not_configured",
            "movement_state": "not_configured",
            "adjustment_state": "not_configured",
            "before": None,
            "after": None,
            "effective": None,
        }
    runtime_values = [
        row.get("source_watermark_before"),
        row.get("source_watermark_after"),
        row.get("source_watermark_effective"),
    ]
    if any(_looks_json(value) and not _is_valid_json(value) for value in runtime_values):
        return {
            "coverage_state": "invalid",
            "movement_state": "invalid",
            "adjustment_state": "invalid",
            "before": None,
            "after": None,
            "effective": None,
        }
    before = _normalized_json_string(row.get("source_watermark_before"))
    after = _normalized_json_string(row.get("source_watermark_after"))
    effective = _normalized_json_string(row.get("source_watermark_effective"))
    status = _status(row)
    if status in {"running", "pending"}:
        movement = "incomplete"
    elif status in {"failed", "skipped"}:
        movement = "unchanged"
    elif before and after:
        movement = "advanced" if before != after else "unchanged"
    elif not before and after:
        movement = "initialized"
    else:
        movement = "incomplete"

    if before and effective:
        adjustment = "adjusted" if effective != before else "not_adjusted"
    elif before and not effective:
        adjustment = "not_adjusted"
    else:
        adjustment = "unknown"

    return {
        "coverage_state": "configured",
        "movement_state": movement,
        "adjustment_state": adjustment,
        "before": before,
        "after": after,
        "effective": effective,
    }


def _is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    try:
        if value == "":
            return True
        return bool(value != value)
    except (TypeError, ValueError):
        return False


def _looks_json(value: Any) -> bool:
    if _is_missing_value(value):
        return False
    if not isinstance(value, str):
        return isinstance(value, (dict, list))
    stripped = value.strip()
    return (stripped.startswith("{") and stripped.endswith("}")) or (stripped.startswith("[") and stripped.endswith("]"))


def _is_valid_json(value: Any) -> bool:
    if isinstance(value, (dict, list)):
        return True
    if _is_missing_value(value):
        return True
    if not isinstance(value, str):
        return True
    try:
        json.loads(value)
    except (TypeError, ValueError):
        return False
    return True


def _is_later(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    candidate_time = candidate.get("end_time") or candidate.get("start_time")
    current_time = current.get("end_time") or current.get("start_time")
    return _time_value(candidate_time) > _time_value(current_time)


def _enabled_etl_paths(paths: list[EnvironmentSource]) -> list[str]:
    return [
        resolved.etl_logs_uri or path.uri
        for path in paths
        if path.enabled
        for resolved in [resolve_log_source_paths(path)]
    ]


def _analytics_unavailable(paths: list[EnvironmentSource]) -> AnalyticsRebuildRequired:
    source_ids = sorted(path.id for path in paths if path.enabled)
    return AnalyticsRebuildRequired(
        "Monitoring analytics are unavailable; sync the Log sources to rebuild them",
        source_ids=source_ids,
        missing_source_ids=source_ids,
        reason="not_ready",
    )


def _enrich_dataflow(row: dict[str, Any], job: dict[str, Any] | None) -> dict[str, Any]:
    if not job:
        return {**row, "engine_name": "unknown", "metadata_provider_name": "unknown", "platform_name": "unknown"}
    return {
        **row,
        "engine_name": job.get("engine_name") or "unknown",
        "metadata_provider_name": job.get("metadata_provider_name") or "unknown",
        "platform_name": job.get("platform_name") or "unknown",
        "job_status": job.get("status"),
        "job_duration_seconds": job.get("duration_seconds"),
    }


def _operations_page(
    rows: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    timezone_info: tzinfo = timezone.utc,
    trend_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trend_context = trend_context or _trend_context({}, [*rows, *jobs], timezone_info)
    job_status = Counter(_status(row) for row in jobs)
    dataflow_status = Counter(_status(row) for row in rows)
    executable_job_runs = [job for job in jobs if _status(job) in {"succeeded", "failed"}]
    executable_dataflow_runs = [row for row in rows if _status(row) in {"succeeded", "failed"}]
    job_durations = [_num(job, "duration_seconds") for job in executable_job_runs]
    dataflow_durations = [_num(row, "duration_seconds") for row in executable_dataflow_runs]
    total_jobs = len(jobs)
    total_dataflows = len(rows)
    executable_jobs = job_status.get("succeeded", 0) + job_status.get("failed", 0)
    executable_dataflows = dataflow_status.get("succeeded", 0) + dataflow_status.get("failed", 0)
    failed_jobs = [_enrich_job_run_for_investigation(job) for job in jobs if _status(job) == "failed"][:50]
    job_runs_by_dataflow_operation_type = _job_runs_by_dataflow_operation_type(rows, jobs)
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
            "total_rows_processed": _sum(jobs, "total_rows_read") + _sum(jobs, "total_rows_written"),
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
        "dataflow_duration_by_stage": _duration_by_group(rows, "stage", lambda row: str(row.get("stage") or "unknown"), limit=100),
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
            "success_rate": _rate(dataflow_status.get("succeeded", 0), executable_dataflows),
            "failure_rate": _rate(dataflow_status.get("failed", 0), executable_dataflows),
            "skip_rate": _rate(dataflow_status.get("skipped", 0), total_dataflows),
            "pending_rate": _rate(dataflow_status.get("pending", 0), total_dataflows),
            "running_rate": _rate(dataflow_status.get("running", 0), total_dataflows),
            "total_bytes_written": _sum(rows, "destination_bytes_added"),
            "avg_duration_seconds": _avg(dataflow_durations),
            "p95_duration_seconds": _percentile_clean(dataflow_durations, 0.95),
            "active_engines": len({row.get("engine_name") for row in rows if row.get("engine_name")}),
        },
        "dataflow_duration_stats": _duration_stats(executable_dataflow_runs),
        "dataflow_runs_by_operation_type": _operation_type_mix(rows, _dataflow_operation_type),
        "dataflow_runs_by_destination_operation_type": _operation_type_mix(rows, _destination_operation_type),
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
    failed = [row for row in rows if _status(row) == "failed" and _row_timestamp(row) is not None]
    if not failed:
        return None
    return max(failed, key=lambda row: _row_timestamp(row) or datetime.min)


def _slowest_jobs(jobs: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    completed = [job for job in jobs if _num(job, "duration_seconds") is not None]
    return sorted(completed, key=lambda job: _num(job, "duration_seconds") or 0, reverse=True)[:limit]


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
            _job_operation_types_value(item) if entity_kind == "job" else _dataflow_operation_type(item)
            for item in items
        )
        q1_duration = _percentile(clean, 0.25)
        q3_duration = _percentile(clean, 0.75)
        iqr = q3_duration - q1_duration
        lower_fence = q1_duration - 1.5 * iqr
        upper_fence = q3_duration + 1.5 * iqr
        non_outlier_durations = [
            duration
            for duration in clean
            if lower_fence <= duration <= upper_fence
        ] or clean
        outliers = [
            {
                "duration_seconds": round(duration, 3),
                "dataflow_name": (
                    item.get("job_id") if entity_kind == "job"
                    else item.get("dataflow_name") or item.get("dataflow_id")
                ) or "unknown",
                "dataflow_run_id": item.get("job_id") if entity_kind == "job" else item.get("dataflow_run_id"),
                "status": _status(item),
                "operation_type": (
                    _job_operation_types_value(item)
                    if entity_kind == "job"
                    else _dataflow_operation_type(item)
                ),
            }
            for item in items
            for duration in [_num(item, "duration_seconds")]
            if duration is not None and (duration < lower_fence or duration > upper_fence)
        ]
        outlier_count = len(outliers)
        outliers = sorted(outliers, key=lambda item: float(item["duration_seconds"]), reverse=True)[:40]
        result.append({
            group_key: group_value,
            "group": group_value,
            "count": len(clean),
            "min_duration_seconds": round(min(clean), 3) if clean else 0,
            "whisker_min_duration_seconds": round(min(non_outlier_durations), 3) if non_outlier_durations else 0,
            "q1_duration_seconds": q1_duration,
            "p50_duration_seconds": _percentile(clean, 0.50),
            "q3_duration_seconds": q3_duration,
            "whisker_max_duration_seconds": round(max(non_outlier_durations), 3) if non_outlier_durations else 0,
            "p95_duration_seconds": _percentile(clean, 0.95),
            "max_duration_seconds": round(max(clean), 3) if clean else 0,
            "avg_duration_seconds": _avg(durations),
            "succeeded": statuses.get("succeeded", 0),
            "failed": statuses.get("failed", 0),
            "skipped": statuses.get("skipped", 0),
            "success_rate": _rate(statuses.get("succeeded", 0), executable),
            "operation_mix": ", ".join(f"{name}: {count}" for name, count in sorted(operation_mix.items())),
            "outlier_count": outlier_count,
            "outliers": outliers,
        })
    return sorted(result, key=lambda row: (-float(row["p95_duration_seconds"]), -int(row["count"]), str(row["group"])))[:limit]


def _job_workload_efficiency(jobs: list[dict[str, Any]], rows: list[dict[str, Any]], limit: int = 500) -> list[dict[str, Any]]:
    jobs_by_id = {str(job.get("job_id") or "").strip(): job for job in jobs if str(job.get("job_id") or "").strip()}
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
        rows_written = sum(_num(item, "destination_rows_written") or 0 for item in items)
        bytes_added = sum(_num(item, "destination_bytes_added") or 0 for item in items)
        throughput = rows_read / duration if rows_read > 0 and duration > 0 else 0
        statuses = Counter(_status(item) for item in items)
        points.append({
            "job_id": job_id,
            "job_key": _job_key(job) if job else "unknown",
            "job_shape_label": _job_shape_label(job) if job else "unknown",
            "status": _status(job) if job else _dominant_status(statuses),
            "operation_type": operation_type,
            "engine_name": job.get("engine_name") or "unknown",
            "metadata_provider_name": job.get("metadata_provider_name") or "unknown",
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
        })
    return sorted(points, key=lambda item: (float(item["duration_seconds"]), float(item["child_dataflow_count"])), reverse=True)[:limit]


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
        for total_dataflows, values in sorted(buckets.items(), key=lambda item: int(item[0]))
    ]


def _jobs_by_engine_provider(jobs: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
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
        durations = [_num(item, "duration_seconds") for item in items if _status(item) in {"succeeded", "failed"}]
        rows.append({
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
        })
    return sorted(rows, key=lambda row: (row["failed"], row["jobs"]), reverse=True)[:limit]


def _jobs_by_child_impact(jobs: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    candidates = []
    for job in jobs:
        total = _num(job, "child_dataflow_count") or _num(job, "total_dataflows") or 0
        failed = _num(job, "child_failed_count") or 0
        skipped = _num(job, "child_skipped_count") or 0
        p95 = _num(job, "child_p95_duration_seconds") or 0
        if total <= 0 and failed <= 0 and skipped <= 0 and p95 <= 0:
            continue
        candidates.append({
            "job_id": job.get("job_id"),
            "status": _status(job),
            "child_dataflow_count": total,
            "child_failed_count": failed,
            "child_skipped_count": skipped,
            "child_p95_duration_seconds": p95,
            "duration_seconds": _num(job, "duration_seconds") or 0,
            "reconciliation_status": job.get("reconciliation_status"),
            "reconciliation_mismatch_count": _num(job, "reconciliation_mismatch_count") or 0,
        })
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


def _job_attention_items(jobs: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    duration_stats = _duration_stats([job for job in jobs if _status(job) in {"succeeded", "failed"}])
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
            items.append({
                "severity": "bad",
                "code": "job_failed",
                "title": "Failed job",
                "detail": _error_preview(job) or job_id,
                "job_id": job_id,
                "sort_score": 5000 + sort_time,
            })
        if child_failed:
            items.append({
                "severity": "bad",
                "code": "child_dataflow_failed",
                "title": "Child dataflows failed",
                "detail": f"{int(child_failed)} failed child dataflows in {job_id}",
                "job_id": job_id,
                "sort_score": 4000 + child_failed,
            })
        if mismatches:
            items.append({
                "severity": "warning",
                "code": "job_reconciliation_mismatch",
                "title": "Reconciliation mismatch",
                "detail": f"{int(mismatches)} job totals differ from child rollup in {job_id}",
                "job_id": job_id,
                "sort_score": 3000 + mismatches,
            })
        if p99 and duration >= p99:
            items.append({
                "severity": "warning",
                "code": "job_duration_p99",
                "title": "P99 duration job",
                "detail": f"{_format_seconds(duration)} duration in {job_id}",
                "job_id": job_id,
                "sort_score": 2000 + duration,
            })
        elif p95 and duration >= p95:
            items.append({
                "severity": "info",
                "code": "job_duration_p95",
                "title": "P95 duration job",
                "detail": f"{_format_seconds(duration)} duration in {job_id}",
                "job_id": job_id,
                "sort_score": 1000 + duration,
            })

    return sorted(items, key=lambda item: float(item.get("sort_score") or 0), reverse=True)[:limit]


def _failures_page(rows: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [_failure_enriched_dataflow(row) for row in rows if _status(row) == "failed"]
    failed_jobs = [_failure_enriched_job(job) for job in jobs if _status(job) == "failed"]
    return {
        "kpis": _failure_kpis(failed, failed_jobs),
        "latest_queue": _latest_failure_queue(failed),
        "repeated_signatures": _repeated_failure_signatures(failed),
        "failure_by_phase": _top_counter(failed, "failure_phase", limit=12),
        "failure_category_phase_matrix": _failure_category_phase_matrix(failed),
        "endpoint_impact": _failure_endpoint_impact(failed),
        "failed_by_stage": _failure_phase_breakdown(failed, "stage", label_key="name", count_key="count", limit=30),
        "failed_by_source_connection_type": _top_counter(failed, "source_connection_type", limit=20),
        "top_failing_dataflows": _top_failing_dataflows(failed),
        "error_categories": _error_categories(failed),
        "failure_trend_by_date": _failure_trend([*failed, *failed_jobs]),
        "failed_records": failed[:100],
    }


def _failure_kpis(failed: list[dict[str, Any]], failed_jobs: list[dict[str, Any]]) -> dict[str, Any]:
    latest = _latest_failure(failed)
    failed_job_ids = {
        str(row.get("job_id"))
        for row in failed_jobs
        if row.get("job_id") not in (None, "", "unknown")
    }
    affected_dataflow_jobs = {
        str(row.get("job_id"))
        for row in failed
        if row.get("job_id") not in (None, "", "unknown")
    }
    affected_job_shapes = {_job_key(job) for job in failed_jobs}
    affected_dataflows = {_dataflow_id(row) for row in failed if _dataflow_id(row)}
    affected_routes = {
        (
            str(row.get("source_name") or row.get("source_connection_name") or "unknown"),
            str(row.get("destination_name") or row.get("destination_connection_name") or "unknown"),
        )
        for row in failed
    }
    repeated = _repeated_failure_signatures(failed, limit=1000)
    total_failed_records = len(failed)
    repeated_runs = sum(int(row.get("failed_runs") or 0) for row in repeated if int(row.get("failed_runs") or 0) >= 2)
    top_signature = max(repeated, key=lambda row: int(row.get("failed_runs") or 0), default=None)
    top_cause_runs = int(top_signature.get("failed_runs") or 0) if top_signature else 0
    return {
        "failed_jobs": len(failed_jobs),
        "failed_dataflows": len(failed),
        "affected_jobs": len(failed_job_ids),
        "affected_dataflow_jobs": len(affected_dataflow_jobs),
        "affected_job_shapes": len(affected_job_shapes),
        "affected_job_contexts": len(affected_job_shapes),
        "affected_stages": len(affected_job_shapes),
        "affected_dataflows": len(affected_dataflows),
        "affected_routes": len(affected_routes),
        "repeated_signatures": sum(1 for row in repeated if int(row.get("failed_runs") or 0) >= 2),
        "unique_signatures": len(repeated),
        "repeated_failure_runs": repeated_runs,
        "repeated_failure_share": _rate(repeated_runs, total_failed_records),
        "total_failed_records": total_failed_records,
        "top_cause_runs": top_cause_runs,
        "top_cause_share": _rate(top_cause_runs, total_failed_records),
        "top_cause_category": top_signature.get("failure_category") if top_signature else None,
        "top_cause_phase": top_signature.get("failure_phase") if top_signature else None,
        "top_cause_signature": top_signature.get("failure_signature") if top_signature else None,
        "latest_failure_at": latest.get("failure_time") if latest else None,
        "latest_failure_name": latest.get("dataflow_name") or latest.get("job_id") if latest else None,
    }


def _failure_enriched_dataflow(row: dict[str, Any]) -> dict[str, Any]:
    phase, message = dataflow_failure_phase_and_message(row)
    all_evidence = "\n".join(
        str(row.get(key) or "").strip()
        for key in (
            "source_error_message", "transform_error_message",
            "destination_error_message", "error_message",
        )
        if _has_value(row.get(key))
    )
    classification = classify_failure(message, all_evidence=all_evidence)
    category = classification.category
    signature = _failure_signature(category, phase, message)
    target = _failure_target(row)
    return {
        **row,
        "failure_kind": "dataflow",
        "failure_phase": phase,
        "failure_message": message,
        "failure_category": category,
        "failure_tags": list(classification.tags),
        "failure_rule_id": classification.rule_id,
        "failure_signature": signature,
        "failure_target": target,
        "failure_time": row.get("end_time") or row.get("start_time"),
    }


def _failure_enriched_job(job: dict[str, Any]) -> dict[str, Any]:
    message = str(job.get("error_message") or "")
    classification = classify_failure(message)
    category = classification.category
    phase = "job"
    return {
        **job,
        "failure_kind": "job",
        "failure_phase": phase,
        "failure_message": message,
        "failure_category": category,
        "failure_tags": list(classification.tags),
        "failure_rule_id": classification.rule_id,
        "failure_signature": _failure_signature(category, phase, message),
        "failure_target": job.get("job_id") or "unknown",
        "failure_time": job.get("end_time") or job.get("start_time"),
        "job_key": _job_key(job),
        "job_shape_label": _job_shape_label(job),
    }


def _failure_signature(category: str, phase: str, message: str) -> str:
    normalized = _normalize_error_message(message)
    return f"{category}|{phase}|{normalized or 'unknown'}"


def _normalize_error_message(message: str) -> str:
    text = str(message or "").strip().lower()
    text = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<uuid>", text)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}(?:[t ][0-9:.+\-z]+)?\b", "<timestamp>", text)
    text = re.sub(r"\b\d+\b", "<number>", text)
    text = re.sub(r"\s+", " ", text)
    return text[:360]


def _failure_target(row: dict[str, Any]) -> str:
    destination = (
        row.get("destination_full_table")
        or row.get("destination_table")
        or row.get("destination_path")
        or row.get("destination_name")
    )
    source = (
        row.get("source_full_table")
        or row.get("source_table")
        or row.get("source_path")
        or row.get("source_name")
    )
    return str(destination or source or row.get("dataflow_name") or row.get("dataflow_id") or "unknown")


def _latest_failure(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(rows, key=lambda row: _time_value(row.get("failure_time") or row.get("end_time") or row.get("start_time")))


def _latest_failure_queue(failed: list[dict[str, Any]], limit: int = 60) -> list[dict[str, Any]]:
    rows = [*failed]
    rows.sort(key=lambda row: _time_value(row.get("failure_time") or row.get("end_time") or row.get("start_time")), reverse=True)
    return rows[:limit]


def _repeated_failure_signatures(
    failed: list[dict[str, Any]],
    limit: int = 30,
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in failed:
        buckets[str(row.get("failure_signature") or "unknown")].append(row)

    result: list[dict[str, Any]] = []
    for signature, items in buckets.items():
        latest = _latest_failure(items) or {}
        affected_jobs = {
            str(item.get("job_id"))
            for item in items
            if item.get("job_id") not in (None, "", "unknown")
        }
        affected_dataflows = {
            str(item.get("dataflow_id") or item.get("dataflow_name"))
            for item in items
            if item.get("dataflow_id") or item.get("dataflow_name")
        }
        result.append({
            "failure_signature": signature,
            "failure_category": latest.get("failure_category") or "Unspecified",
            "failure_phase": latest.get("failure_phase") or "unknown",
            "failed_runs": len(items),
            "affected_jobs": len(affected_jobs),
            "affected_dataflows": len(affected_dataflows),
            "latest_time": latest.get("failure_time"),
            "latest_error": latest.get("failure_message"),
            "sample_dataflow": latest.get("dataflow_name"),
            "sample_job_id": latest.get("job_id"),
            "failure_target": latest.get("failure_target"),
        })
    result.sort(key=lambda item: str(item.get("latest_time") or ""), reverse=True)
    result.sort(key=lambda item: int(item.get("affected_jobs") or 0), reverse=True)
    result.sort(key=lambda item: int(item.get("failed_runs") or 0), reverse=True)
    return result[:limit]


def _failure_endpoint_impact(failed: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in failed:
        source = str(row.get("source_name") or row.get("source_connection_name") or "unknown")
        destination = str(row.get("destination_name") or row.get("destination_connection_name") or "unknown")
        buckets[(source, destination)].append(row)

    result: list[dict[str, Any]] = []
    for (source, destination), items in buckets.items():
        latest = _latest_failure(items) or {}
        affected_jobs = {
            str(item.get("job_id"))
            for item in items
            if item.get("job_id") not in (None, "", "unknown")
        }
        result.append({
            "source_name": source,
            "destination_name": destination,
            "source_format": _dominant_value(items, "source_format"),
            "destination_format": _dominant_value(items, "destination_format"),
            "source_connection_type": _dominant_value(items, "source_connection_type"),
            "destination_connection_type": _dominant_value(items, "destination_connection_type"),
            "failed_runs": len(items),
            "affected_jobs": len(affected_jobs),
            "failure_category": _dominant_value(items, "failure_category"),
            "failure_phase": _dominant_value(items, "failure_phase"),
            "latest_time": latest.get("failure_time"),
            "latest_error": latest.get("failure_message"),
        })
    result.sort(key=lambda item: str(item.get("latest_time") or ""), reverse=True)
    result.sort(key=lambda item: int(item.get("affected_jobs") or 0), reverse=True)
    result.sort(key=lambda item: int(item.get("failed_runs") or 0), reverse=True)
    return result[:limit]


def _failure_category_phase_matrix(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    phases = ("source", "transform", "destination", "overhead", "unknown")
    buckets: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        category = str(row.get("failure_category") or "Unspecified")
        phase = _failure_phase_value(row, phases)
        buckets[category][phase] += 1

    result = []
    for category, counts in buckets.items():
        item = {"category": category, **{phase: int(counts.get(phase, 0)) for phase in phases}}
        item["total"] = sum(int(item[phase]) for phase in phases)
        result.append(item)
    result.sort(key=lambda item: str(item.get("category") or ""))
    result.sort(key=lambda item: int(item.get("total") or 0), reverse=True)
    return result


def _failure_phase_breakdown(
    rows: list[dict[str, Any]],
    dimension_key: str,
    *,
    label_key: str,
    count_key: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    phases = ("source", "transform", "destination", "overhead", "unknown")
    buckets: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        label = _dimension_value(row.get(dimension_key))
        buckets[label][_failure_phase_value(row, phases)] += 1

    result: list[dict[str, Any]] = []
    for label, counts in buckets.items():
        item = {label_key: label, **{phase: int(counts.get(phase, 0)) for phase in phases}}
        item[count_key] = sum(int(item[phase]) for phase in phases)
        result.append(item)
    result.sort(key=lambda item: str(item.get(label_key) or "unknown"))
    result.sort(key=lambda item: int(item.get(count_key) or 0), reverse=True)
    return result[:limit]


def _failure_phase_value(row: dict[str, Any], phases: tuple[str, ...]) -> str:
    phase = str(row.get("failure_phase") or "unknown")
    return phase if phase in phases else "unknown"


def _performance_page(
    rows: list[dict[str, Any]],
    trend_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    phase_runs = [row for row in rows if _status(row) in {"succeeded", "failed", "skipped"}]
    executable = [
        row
        for row in rows
        if _status(row) in {"succeeded", "failed"} and _num(row, "duration_seconds") is not None
    ]
    durations = [_num(row, "duration_seconds") or 0 for row in executable]
    duration_stats = _duration_stats(executable)
    thresholds_by_operation = _performance_thresholds_by_operation(executable)
    executable = [
        _performance_enriched_run(
            row,
            thresholds_by_operation.get(_dataflow_operation_type(row), thresholds_by_operation["__all__"]),
        )
        for row in executable
    ]
    candidate_counts: Counter[str] = Counter()
    for row in executable:
        for code in row.get("performance_candidate_codes") or []:
            candidate_counts[str(code)] += 1
    phase_totals = _performance_phase_totals(executable)
    phase_total_duration = sum(phase_totals.values())
    bottleneck_phase = max(phase_totals.items(), key=lambda item: item[1])[0] if phase_total_duration > 0 else "unknown"
    throughput_duration = sum(_num(row, "duration_seconds") or 0 for row in executable)
    total_rows_read = _sum(executable, "source_rows_read")
    total_rows_written = _sum(executable, "destination_rows_written")
    return {
        "kpis": {
            "run_count": len(executable),
            "avg_duration_seconds": duration_stats.get("avg_duration_seconds", 0),
            "p50_duration_seconds": duration_stats.get("p50_duration_seconds", 0),
            "p75_duration_seconds": duration_stats.get("q3_duration_seconds", 0),
            "p95_duration_seconds": duration_stats.get("p95_duration_seconds", 0),
            "p99_duration_seconds": duration_stats.get("p99_duration_seconds", 0),
            "max_duration_seconds": duration_stats.get("max_duration_seconds", 0),
            "duration_pressure_ratio": _safe_ratio(
                duration_stats.get("p95_duration_seconds", 0) or 0,
                duration_stats.get("p50_duration_seconds", 0) or 0,
            ),
            "duration_outlier_count": sum(int(row.get("outlier_count") or 0) for row in _performance_duration_distribution_by_stage(executable, limit=None)),
            "slowest_run_duration_seconds": round(max(durations), 3) if durations else 0,
            "slowest_run_dataflow_name": (max(executable, key=lambda row: _num(row, "duration_seconds") or 0).get("dataflow_name") if executable else None),
            "slowest_run_stage": (max(executable, key=lambda row: _num(row, "duration_seconds") or 0).get("stage") if executable else None),
            "bottleneck_phase": bottleneck_phase,
            "source_duration_percent": _rate(phase_totals["source"], phase_total_duration),
            "transform_duration_percent": _rate(phase_totals["transform"], phase_total_duration),
            "destination_duration_percent": _rate(phase_totals["destination"], phase_total_duration),
            "overhead_duration_percent": _rate(phase_totals["overhead"], phase_total_duration),
            "rows_read_per_second": _safe_ratio(total_rows_read, throughput_duration),
            "total_rows_read": total_rows_read,
            "total_rows_written": total_rows_written,
            "optimization_candidate_count": sum(1 for row in executable if row.get("performance_candidate_code")),
            "slow_small_workload_count": candidate_counts.get("slow_small_workload", 0),
            "slow_small_maintenance_count": candidate_counts.get("slow_small_maintenance", 0),
            "high_overhead_count": candidate_counts.get("high_overhead", 0),
            "phase_skew_count": candidate_counts.get("phase_skew", 0),
        },
        "duration_distribution_by_stage": _performance_duration_distribution_by_stage(executable),
        "phase_contribution_by_stage_operation": _performance_phase_contribution_by_stage_operation(phase_runs),
        "workload_efficiency_points": _performance_workload_efficiency_points(executable),
        "slowest_dataflow_profiles": _performance_slowest_dataflow_profiles(executable),
        "runtime_context_profiles": _performance_runtime_context_profiles(executable),
        "performance_trend": _performance_trend(executable, trend_context=trend_context),
        "investigation_queue": [_compact_performance_evidence(row) for row in _performance_investigation_queue(executable)],
        "duration_breakdown": _duration_breakdown(executable),
        "duration_vs_rows": _duration_vs_rows(executable),
        "slowest_dataflows": [
            _compact_performance_evidence(row)
            for row in sorted(executable, key=lambda row: _num(row, "duration_seconds") or 0, reverse=True)[:25]
        ],
        "slowest_dataflows_by_p95": _slowest_dataflows_by_p95(executable),
        "overview_p95_duration_seconds": _percentile(durations, 0.95),
        "duration_by_stage": _duration_by_stage(executable),
        "engine_stage_matrix": _engine_stage_matrix(executable),
    }


def _volume_page(rows: list[dict[str, Any]], jobs: list[dict[str, Any]], trend_context: dict[str, Any] | None = None) -> dict[str, Any]:
    trend_context = trend_context or _trend_context({}, rows, timezone.utc)
    total_runs = len(rows)
    skip_count = sum(1 for row in rows if _status(row) == "skipped")
    rows_read_total = _sum(rows, "source_rows_read")
    lakehouse_rows_written_total = _sum(rows, "destination_rows_written")
    est_rows_written_total = round(sum(_estimated_rows_written(row) for row in rows), 3)
    files_added = _sum(rows, "destination_files_added")
    files_removed = _sum(rows, "destination_files_removed")
    bytes_added = _sum(rows, "destination_bytes_added")
    bytes_removed = _sum(rows, "destination_bytes_removed")
    lakehouse_runs = sum(1 for row in rows if _is_lakehouse_destination(row))
    high_volume_queue = _volume_investigation_queue(rows)
    dataflow_registry = _volume_dataflow_registry(rows, high_volume_queue)
    candidate_dataflows = [row for row in dataflow_registry if row.get("volume_candidate_priority", 0) > 0]
    return {
        "kpis": {
            "total_rows_read": rows_read_total,
            "total_rows_written": lakehouse_rows_written_total,
            "total_est_rows_written": est_rows_written_total,
            "total_est_rows_written_non_lakehouse": round(est_rows_written_total - lakehouse_rows_written_total, 3),
            "total_rows_inserted": _sum(rows, "destination_rows_inserted"),
            "total_rows_updated": _sum(rows, "destination_rows_updated"),
            "total_rows_deleted": _sum(rows, "destination_rows_deleted"),
            "lakehouse_destination_run_count": lakehouse_runs,
            "lakehouse_destination_share": _rate(lakehouse_runs, total_runs),
            "files_added": files_added,
            "files_removed": files_removed,
            "total_bytes_added": bytes_added,
            "total_bytes_removed": bytes_removed,
            "total_bytes_saved": _sum(rows, "destination_bytes_saved"),
            "net_bytes_change": bytes_added - bytes_removed,
            "avg_bytes_per_file_added": round(bytes_added / files_added, 3) if files_added else 0,
            "high_volume_run_count": len(high_volume_queue),
            "high_volume_dataflow_count": len(candidate_dataflows),
            "high_volume_candidate_run_count": len(high_volume_queue),
            "high_volume_rows_count": sum(1 for row in high_volume_queue if row.get("volume_candidate_kind") == "read"),
            "high_volume_est_rows_count": sum(1 for row in high_volume_queue if row.get("volume_candidate_kind") == "est_rows"),
            "high_volume_lakehouse_rows_count": sum(1 for row in high_volume_queue if row.get("volume_candidate_kind") == "lakehouse_rows"),
            "high_volume_bytes_count": sum(1 for row in high_volume_queue if row.get("volume_candidate_kind") == "bytes"),
            "high_volume_files_count": sum(1 for row in high_volume_queue if row.get("volume_candidate_kind") == "files"),
            "skip_count": skip_count,
            "skip_rate": _rate(skip_count, total_runs),
        },
        "rows_by_date": _rows_by_date(rows, trend_context=trend_context),
        "bytes_by_date": _bytes_by_date(rows, trend_context=trend_context),
        "volume_by_load_type": _volume_by_load_type(rows),
        "volume_by_workload_type": _volume_by_workload_type(rows),
        "route_volume": _route_volume(rows),
        "top_dataflows_by_rows_read": _top_dataflow_sum(rows, "source_rows_read", limit=20),
        "top_dataflows_by_est_rows_written": _top_dataflow_est_rows_written(rows, limit=20),
        "top_dataflows_by_rows_written": _top_dataflow_sum(rows, "destination_rows_written", limit=20),
        "top_dataflows_by_bytes_added": _top_dataflow_sum(rows, "destination_bytes_added", limit=20),
        "top_dataflows_by_net_bytes": _top_dataflow_net_bytes(rows, limit=20),
        "dataflow_registry": [_compact_volume_registry_row(row) for row in dataflow_registry],
    }


def _maintenance_page(rows: list[dict[str, Any]], trend_context: dict[str, Any] | None = None) -> dict[str, Any]:
    trend_context = trend_context or _trend_context({}, rows, timezone.utc)
    maintenance = [row for row in rows if _is_maintenance_row(row)]
    table_registry = _maintenance_table_registry(rows, maintenance)
    coverage = _maintenance_coverage_from_registry(table_registry)
    no_op_table_count = sum(1 for row in table_registry if int(row.get("no_op_runs") or 0) > 0)
    no_op_count = sum(1 for row in maintenance if _is_no_op_maintenance(row))
    no_op_duration = sum(_num(row, "duration_seconds") or 0 for row in maintenance if _is_no_op_maintenance(row))
    succeeded_duration = sum(_num(row, "duration_seconds") or 0 for row in maintenance if _status(row) == "succeeded")
    high_duration_count = sum(1 for row in maintenance if _maintenance_candidate(row, maintenance)[0] == "high_duration")
    duration_total = sum(_num(row, "duration_seconds") or 0 for row in maintenance)
    bytes_reclaimed = _sum(maintenance, "destination_bytes_removed")
    files_removed = _sum(maintenance, "destination_files_removed")
    failed_ops = sum(1 for row in maintenance if _status(row) == "failed")
    skipped_ops = sum(1 for row in maintenance if _status(row) == "skipped")
    latest_failed_tables = sum(1 for row in table_registry if row.get("latest_status") == "failed")
    latest_skipped_tables = sum(1 for row in table_registry if row.get("latest_status") == "skipped")
    latest_active_tables = sum(1 for row in table_registry if row.get("latest_status") in {"running", "pending"})
    lagged_tables = sum(1 for row in table_registry if int(row.get("maintenance_lag_warning") or 0))
    health_status = _maintenance_health_status(
        maintenance,
        latest_failed_tables=latest_failed_tables,
        coverage=coverage,
        latest_skipped_tables=latest_skipped_tables,
        latest_active_tables=latest_active_tables,
        lagged_tables=lagged_tables,
    )
    table_attention = _maintenance_table_attention(table_registry)
    return {
        "kpis": {
            "total_maintenance_runs": len(maintenance),
            "health_status": health_status,
            "succeeded_ops": sum(1 for row in maintenance if _status(row) == "succeeded"),
            "failed_ops": failed_ops,
            "skipped_ops": skipped_ops,
            "running_ops": sum(1 for row in maintenance if _status(row) == "running"),
            "pending_ops": sum(1 for row in maintenance if _status(row) == "pending"),
            "files_removed": files_removed,
            "bytes_reclaimed": bytes_reclaimed,
            "bytes_saved": _sum(maintenance, "destination_bytes_saved"),
            "duration_seconds": round(duration_total, 3),
            "bytes_reclaimed_per_second": round(bytes_reclaimed / duration_total, 3) if duration_total else 0,
            "avg_bytes_per_file_removed": round(bytes_reclaimed / files_removed, 3) if files_removed else 0,
            "no_op_runs": no_op_count,
            "no_op_tables": no_op_table_count,
            "no_op_duration_seconds": round(no_op_duration, 3),
            "no_op_runtime_share": _rate(no_op_duration, succeeded_duration),
            "high_duration_runs": high_duration_count,
            "tables_with_reclaim": sum(1 for row in table_registry if (_num(row, "bytes_reclaimed") or 0) > 0 or (_num(row, "files_removed") or 0) > 0),
            "tables_with_issues": sum(1 for row in table_registry if row.get("table_health") == "has_issues"),
            "tables_with_warnings": sum(1 for row in table_registry if row.get("table_health") == "warning"),
            "latest_failed_tables": latest_failed_tables,
            "latest_skipped_tables": latest_skipped_tables,
            "latest_active_tables": latest_active_tables,
            "lagged_tables": lagged_tables,
            "maintenance_lag_warning_days": _MAINTENANCE_LAG_WARNING_DAYS,
            **coverage,
        },
        "bytes_reclaimed_by_table": _top_sum(maintenance, "destination_table", "destination_bytes_removed", limit=20),
        "status_by_date": _maintenance_status_by_date(maintenance, trend_context=trend_context),
        "reclaim_by_date": _maintenance_reclaim_by_date(maintenance, trend_context=trend_context),
        "table_registry": table_registry,
        "table_attention": table_attention,
        "table_efficiency_points": _maintenance_table_efficiency_points(table_registry),
        "format_comparison": _maintenance_format_comparison(maintenance),
        "bytes_reclaimed_by_date": _maintenance_reclaim_by_date(maintenance, trend_context=trend_context),
    }


def _freshness_page(rows: list[dict[str, Any]], trend_context: dict[str, Any] | None = None) -> dict[str, Any]:
    etl_rows = [
        row
        for row in rows
        if _dataflow_operation_type(row) == "etl"
    ]
    successful = [row for row in etl_rows if _status(row) == "succeeded"]
    failed = [row for row in etl_rows if _status(row) == "failed"]
    skipped = [row for row in etl_rows if _status(row) == "skipped"]
    freshness_rows = [row for row in etl_rows if _status(row) in {"succeeded", "skipped"}]
    watermark_rows = [row for row in etl_rows if _has_watermark(row)]
    movement = [_watermark_movement_row(row) for row in watermark_rows if _dataflow_id(row)]
    advanced = [row for row in movement if row["movement"] == "advanced"]
    initialized = [row for row in movement if row["movement"] == "initialized"]
    unchanged = [row for row in movement if row["movement"] == "unchanged"]
    incomplete = [row for row in movement if row["movement"] == "incomplete"]
    invalid = [row for row in movement if row["movement"] == "invalid"]
    unknown = [row for row in movement if row["movement"] == "unknown"]
    adjusted = [row for row in movement if row.get("adjustment_state") == "adjusted"]
    latest_freshness = _latest_freshness_by_dataflow(freshness_rows)
    stale_candidates = _stale_freshness_candidates_from_latest(latest_freshness, days=_FRESHNESS_STALE_DAYS)
    skipped_patterns = _consecutive_skipped_patterns(etl_rows, threshold=_SKIPPED_STREAK_RUNS)
    skipped_by_dataflow = _latest_skipped_streaks_by_dataflow(etl_rows)
    observed_dataflows = len({dataflow_id for row in etl_rows if (dataflow_id := _dataflow_id(row))})
    watermark_dataflows = len({dataflow_id for row in watermark_rows if (dataflow_id := _dataflow_id(row))})
    missing_dataflow_id_runs = sum(1 for row in etl_rows if not _dataflow_id(row))
    age_values = [float(item["age_days"]) for item in latest_freshness if item.get("age_days") is not None]
    age_seconds_values = [float(item["age_seconds"]) for item in latest_freshness if item.get("age_seconds") is not None]
    dataflow_registry = _freshness_dataflow_registry(latest_freshness, movement, skipped_by_dataflow, etl_rows)
    stale_dataflow_count = len(stale_candidates)
    latest_by_dataflow = _latest_rows_by_dataflow(etl_rows)
    latest_watermark_states = [_watermark_classification(row) for row in latest_by_dataflow.values()]
    latest_invalid_watermarks = [row for row in latest_watermark_states if row["movement_state"] == "invalid"]
    latest_incomplete_watermarks = [row for row in latest_watermark_states if row["movement_state"] == "incomplete"]
    latest_status_issues = [
        row
        for row in latest_by_dataflow.values()
        if _status(row) not in {"succeeded", "skipped"}
    ]
    return {
        "kpis": {
            "latest_successful_runs": len(successful),
            "successful_runs": len(successful),
            "freshness_runs": len(freshness_rows),
            "failed_runs": len(failed),
            "skipped_runs": len(skipped),
            "observed_dataflows": observed_dataflows,
            "missing_dataflow_id_runs": missing_dataflow_id_runs,
            "dataflows_with_freshness_evidence": len(latest_freshness),
            "latest_status_issue_dataflows": len(latest_status_issues),
            "latest_watermark_invalid_dataflows": len(latest_invalid_watermarks),
            "latest_watermark_incomplete_dataflows": len(latest_incomplete_watermarks),
            "latest_watermark_issue_dataflows": len(latest_invalid_watermarks) + len(latest_incomplete_watermarks),
            "watermark_enabled_dataflows": len({dataflow_id for row in watermark_rows if (dataflow_id := _dataflow_id(row))}),
            "watermark_coverage_rate": _rate(watermark_dataflows, observed_dataflows),
            "watermark_advanced_runs": len(advanced),
            "watermark_initialized_runs": len(initialized),
            "watermark_unchanged_runs": len(unchanged),
            "watermark_incomplete_runs": len(incomplete),
            "watermark_adjusted_runs": len(adjusted),
            "watermark_invalid_runs": len(invalid),
            "watermark_unknown_runs": len(unknown),
            "watermark_advanced_rate": _rate(len(advanced), len(advanced) + len(unchanged)),
            "skipped_no_new_data": len(skipped),
            "skipped_streak_threshold": _SKIPPED_STREAK_RUNS,
            "skipped_streak_dataflows": len(skipped_patterns),
            "stale_candidates": stale_dataflow_count,
            "stale_dataflows": stale_dataflow_count,
            "stale_threshold_days": _FRESHNESS_STALE_DAYS,
            "stale_dataflow_rate": _rate(stale_dataflow_count, observed_dataflows),
            "min_age_days": min(age_values) if age_values else 0,
            "p50_age_days": _percentile(age_values, 0.50),
            "p95_age_days": _percentile(age_values, 0.95),
            "max_age_days": max(age_values) if age_values else 0,
            "min_age_seconds": min(age_seconds_values) if age_seconds_values else 0,
            "p50_age_seconds": _percentile(age_seconds_values, 0.50),
            "p95_age_seconds": _percentile(age_seconds_values, 0.95),
            "max_age_seconds": max(age_seconds_values) if age_seconds_values else 0,
        },
        "latest_freshness_by_dataflow": latest_freshness,
        "watermark_movement": movement[:100],
        "watermark_movement_by_date": _watermark_movement_by_date(watermark_rows, trend_context=trend_context),
        "age_distribution": _freshness_age_distribution(latest_freshness),
        "watermark_coverage_by_stage": _watermark_coverage_by_stage(etl_rows),
        "skipped_streak_distribution": _skipped_streak_distribution(skipped_patterns),
        "dataflow_registry": dataflow_registry,
        "stale_candidates": stale_candidates,
        "skipped_patterns": skipped_patterns,
    }


def _coverage_page(
    paths: list[EnvironmentSource],
    rows: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    enabled_paths = [path for path in paths if path.enabled]
    dataflow_job_ids = {row.get("job_id") for row in rows if row.get("job_id")}
    job_ids = {job.get("job_id") for job in jobs if job.get("job_id")}
    return {
        "enabled_log_paths": len(enabled_paths),
        "dataflow_records": len(rows),
        "job_records": len(jobs),
        "linked_job_ids": len(dataflow_job_ids & job_ids),
        "dataflow_job_ids": len(dataflow_job_ids),
        "job_ids": len(job_ids),
        "orphan_dataflow_job_ids": len(dataflow_job_ids - job_ids),
        "jobs_without_dataflow_records": len(job_ids - dataflow_job_ids),
        "read_errors": len(errors),
        "status": _coverage_status(enabled_paths, rows, jobs, errors),
    }


def _coverage_status(
    enabled_paths: list[EnvironmentSource],
    rows: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    errors: list[dict[str, str]],
) -> str:
    if errors:
        return "error"
    if not enabled_paths:
        return "missing_sources"
    if not rows and not jobs:
        return "no_records"
    if not rows or not jobs:
        return "partial"
    return "ok"


def _reconciliation_page(rows: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> dict[str, Any]:
    rows_by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("job_id"):
            rows_by_job[str(row["job_id"])].append(row)
    checks = []
    for job in jobs:
        job_id = str(job.get("job_id") or "")
        if not job_id:
            continue
        items = rows_by_job.get(job_id, [])
        for key, observed in {
            "total_dataflows": len(items),
            "total_failed": sum(1 for item in items if _status(item) == "failed"),
            "total_skipped": sum(1 for item in items if _status(item) == "skipped"),
            "total_succeeded": sum(1 for item in items if _status(item) == "succeeded"),
        }.items():
            expected = _num(job, key)
            if expected is not None and int(expected) != int(observed):
                checks.append({
                    "severity": "warning",
                    "job_id": job_id,
                    "metric": key,
                    "expected": int(expected),
                    "observed": int(observed),
                    "difference": int(observed - expected),
                })
    return {
        "status": "warning" if checks else "ok",
        "mismatch_count": len(checks),
        "checks": checks[:50],
    }


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
    record_evidence = _diagnostics_record_evidence_by_date(rows, jobs, trend_context=trend_context)
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
        and float(row.get("completeness_rate") if row.get("completeness_rate") is not None else 100) < 95
    )
    conditional_evidence_groups = sum(1 for row in field_completeness if row.get("applicability") == "conditional")
    cache_partial_sources = sum(1 for row in source_coverage if row.get("warning_count"))
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
            "affected_reconciliation_jobs": len({str(check.get("job_id") or "") for check in reconciliation.get("checks", []) if check.get("job_id")}),
            "read_errors": read_errors,
            "cache_warning_count": cache_partial_sources,
            "field_readiness_rate": _diagnostics_field_readiness_rate(field_completeness),
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


def _diagnostics_context(rows: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> dict[str, Any]:
    rows_by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        job_id = _diagnostics_job_id(row)
        if job_id:
            rows_by_job[job_id].append(row)
    jobs_by_id = {_diagnostics_job_id(job): job for job in jobs if _diagnostics_job_id(job)}
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
    return "" if not value or value.lower() in {"none", "null", "nan", "unknown"} else value


def _diagnostics_job_id_evidence(context: dict[str, Any], limit_per_category: int = 50) -> list[dict[str, Any]]:
    rows_by_job: dict[str, list[dict[str, Any]]] = context["rows_by_job"]
    jobs_by_id: dict[str, dict[str, Any]] = context["jobs_by_id"]
    evidence: list[dict[str, Any]] = []
    for job_id in sorted(context["matched_ids"])[:limit_per_category]:
        job = jobs_by_id[job_id]
        items = rows_by_job[job_id]
        evidence.append(_diagnostics_job_linkage_row("matched", job_id, job, items))
    for job_id in sorted(context["orphan_job_ids"])[:limit_per_category]:
        items = rows_by_job[job_id]
        latest = max(items, key=lambda item: _time_value(item.get("end_time") or item.get("start_time")))
        evidence.append(_diagnostics_job_linkage_row("orphan_dataflow_job_id", job_id, None, items, latest=latest))
    for job_id in sorted(context["job_only_ids"])[:limit_per_category]:
        job = jobs_by_id[job_id]
        evidence.append(_diagnostics_job_linkage_row("job_without_dataflow_records", job_id, job, []))
    return evidence


def _diagnostics_job_linkage_row(
    category: str,
    job_id: str,
    job: dict[str, Any] | None,
    items: list[dict[str, Any]],
    latest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    latest_row = latest or job or (max(items, key=lambda item: _time_value(item.get("end_time") or item.get("start_time"))) if items else {})
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
    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "bucket": "",
        "date": "",
        "job_records": 0,
        "dataflow_records": 0,
        "matched_job_ids": 0,
        "orphan_dataflow_job_ids": 0,
        "jobs_without_dataflow_records": 0,
    })
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
        item["linkage_rate"] = _rate(item["matched_job_ids"], len(dataflow_ids | job_ids))
    for bucket in _diagnostics_expected_trend_buckets(trend_context):
        item = buckets[bucket]
        item["bucket"] = bucket
        item["date"] = bucket
        item.setdefault("linkage_rate", 0)
    return sorted(buckets.values(), key=lambda item: str(item["bucket"]))


def _diagnostics_time_bucket(row: dict[str, Any], trend_context: dict[str, Any] | None) -> str:
    if trend_context:
        bucket = _date_bucket(row, trend_context).get("bucket")
        if bucket:
            return str(bucket)
    return _run_date(row)


def _diagnostics_expected_trend_buckets(trend_context: dict[str, Any] | None) -> list[str]:
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
        current = (start - timedelta(days=start.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
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
            current = current.replace(year=current.year + 1, month=1) if current.month == 12 else current.replace(month=current.month + 1)
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


def _diagnostics_reconciliation_by_metric(reconciliation: dict[str, Any]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "metric": "",
        "mismatch_count": 0,
        "affected_jobs": 0,
        "absolute_difference": 0,
        "severity": "warning",
    })
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
    return sorted(buckets.values(), key=lambda item: (-int(item["mismatch_count"]), str(item["metric"])))


def _diagnostics_field_completeness(rows: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = [
        ("identity/linkage", "dataflow", rows, ("job_id", "dataflow_id", "dataflow_run_id", "dataflow_name"), "universal"),
        ("time/status", "dataflow", rows, ("status", "start_time", "end_time"), "universal"),
        ("runtime duration", "dataflow", rows, ("duration_seconds", "source_duration_seconds", "transform_duration_seconds", "destination_duration_seconds"), "universal"),
        ("source evidence", "dataflow", rows, ("source_name", "source_connection_type", "source_rows_read"), "universal"),
        ("destination evidence", "dataflow", rows, ("destination_name", "destination_connection_type", "destination_load_type"), "universal"),
        ("watermark evidence", "dataflow", rows, ("source_watermark_columns", "source_watermark_before", "source_watermark_after"), "conditional"),
        ("maintenance evidence", "dataflow", rows, ("destination_operation_type", "destination_files_removed", "destination_bytes_removed"), "conditional"),
        ("identity/linkage", "job", jobs, ("job_id",), "universal"),
        ("time/status", "job", jobs, ("status", "start_time", "end_time"), "universal"),
        ("runtime duration", "job", jobs, ("duration_seconds",), "universal"),
        ("job totals", "job", jobs, ("total_dataflows", "total_succeeded", "total_failed", "total_skipped"), "universal"),
        ("runtime context", "job", jobs, ("engine_name", "metadata_provider_name", "platform_name"), "universal"),
    ]
    result = []
    for group, record_type, items, fields, applicability in groups:
        total_slots = len(items) * len(fields)
        present = sum(1 for item in items for field in fields if _has_value(item.get(field)))
        missing = max(0, total_slots - present)
        completeness = _rate(present, total_slots)
        result.append({
            "group": group,
            "record_type": record_type,
            "fields": ", ".join(fields),
            "records": len(items),
            "required_fields": len(fields),
            "present_values": present,
            "missing_values": missing,
            "completeness_rate": completeness,
            "severity": _diagnostics_completeness_severity(completeness, len(items)),
            "applicability": applicability,
            "actionable": applicability == "universal",
        })
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
    weighted_total = sum(float(row.get("records") or 0) * float(row.get("required_fields") or 0) for row in rows)
    weighted_present = sum(float(row.get("present_values") or 0) for row in rows)
    return _rate(weighted_present, weighted_total)


def _diagnostics_source_coverage(
    rows: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {
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
    })
    files_by_source: dict[str, set[str]] = defaultdict(set)
    for record_type, items in (("dataflow", rows), ("job", jobs)):
        for row in items:
            key = _diagnostics_source_key(row)
            bucket = buckets[key]
            bucket["source"] = key
            bucket["source_id"] = row.get("_source_id")
            bucket["file_kind"] = row.get("_file_kind") or bucket["file_kind"] or "unknown"
            file_uri = str(row.get("_file_uri") or "")
            if file_uri:
                files_by_source[key].add(file_uri)
            bucket["records"] += 1
            bucket[f"{record_type}_records"] += 1
            bucket["latest_log_at"] = _latest_timestamp_value(bucket["latest_log_at"], row.get("end_time") or row.get("start_time"))
            bucket["latest_ingested_at"] = _latest_timestamp_value(bucket["latest_ingested_at"], row.get("_ingested_at"))
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
    return candidate_time.isoformat() if candidate_time > current_time else (str(current) if current else None)


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
        queue.append(_diagnostics_queue_row(
            severity="bad",
            category="read/cache warning",
            issue=str(error.get("message") or error.get("error") or "Read/cache warning"),
            target=str(error.get("uri") or error.get("path") or "log source"),
            evidence=error,
            action_hint="Check source path, credentials, file format, then sync logs again.",
        ))
    for job_id in sorted(context["orphan_job_ids"]):
        items = rows_by_job[job_id]
        latest = max(items, key=lambda item: _time_value(item.get("end_time") or item.get("start_time")))
        queue.append(_diagnostics_queue_row(
            severity="bad",
            category="orphan dataflow job id",
            issue="Dataflow records reference a job_id with no matching job log.",
            target=job_id,
            latest_time=latest.get("end_time") or latest.get("start_time"),
            evidence={"job_id": job_id, "dataflow_records": len(items)},
            action_hint="Check job_run_log coverage for the same run window.",
        ))
    for job_id in sorted(context["job_only_ids"]):
        job = jobs_by_id[job_id]
        queue.append(_diagnostics_queue_row(
            severity="warning",
            category="job without dataflows",
            issue="Job log exists but no child dataflow records were found.",
            target=job_id,
            latest_time=job.get("end_time") or job.get("start_time"),
            evidence={"job_id": job_id, "job_total_dataflows": int(_num(job, "total_dataflows") or 0)},
            action_hint="Check dataflow_run_log coverage and cache sync for this job.",
        ))
    for check in reconciliation.get("checks", []):
        queue.append(_diagnostics_queue_row(
            severity=str(check.get("severity") or "warning"),
            category="reconciliation mismatch",
            issue=f"{check.get('metric') or 'metric'} expected {check.get('expected')} but observed {check.get('observed')}.",
            target=str(check.get("job_id") or "job"),
            evidence=check,
            action_hint="Inspect the job drawer and child dataflow records.",
        ))
    for row in field_completeness:
        if not row.get("actionable"):
            continue
        severity = str(row.get("severity") or "good")
        if severity not in {"bad", "warning"}:
            continue
        queue.append(_diagnostics_queue_row(
            severity=severity,
            category="field completeness",
            issue=f"{row.get('record_type')} {row.get('group')} completeness is {row.get('completeness_rate')}%.",
            target=f"{row.get('record_type')} · {row.get('group')}",
            evidence=row,
            action_hint="Confirm the log version emits the fields used by Monitoring pages.",
        ))
    for row in source_coverage:
        if not row.get("warning_count"):
            continue
        queue.append(_diagnostics_queue_row(
            severity="warning",
            category="source coverage",
            issue=f"{row.get('warning_count')} warning(s) for this log source.",
            target=str(row.get("source") or "source"),
            latest_time=row.get("latest_log_at"),
            evidence=row,
            action_hint="Open Sources and sync or validate this ETL log path.",
        ))
    queue.sort(key=lambda row: (-_diagnostics_severity_rank(str(row.get("severity"))), -_diagnostics_sort_timestamp(row.get("latest_time")), str(row.get("category"))))
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
    return {"bad": 4, "error": 4, "warning": 3, "info": 2, "good": 1}.get(value.lower(), 0)


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
    if read_errors or orphan_job_ids or jobs_without_dataflow_records or reconciliation_mismatches:
        return "has_issues"
    if bool(rows) != bool(jobs) or cache_partial_source_count:
        return "warning"
    return "healthy"


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
        items.append(_attention("bad", "log_read_errors", "Review log read errors", f"{coverage['read_errors']} read errors were found.", "diagnostics", {"impact": coverage["read_errors"]}))
    failed_jobs_3 = int(health.get("failed_jobs_last_3_days") or 0)
    failed_jobs_7 = int(health.get("failed_jobs_last_7_days") or 0)
    failed_dataflows_3 = int(health.get("failed_dataflows_last_3_days") or 0)
    failed_dataflows_7 = int(health.get("failed_dataflows_last_7_days") or 0)
    if failed_jobs_3:
        items.append(_attention("bad", "recent_failed_jobs", "Review recent failed jobs", f"{failed_jobs_3} jobs failed in the last 3 days.", "jobs", {"impact": failed_jobs_3}))
    elif failed_jobs_7:
        items.append(_attention("warning", "recent_failed_jobs", "Review recent failed jobs", f"{failed_jobs_7} jobs failed in the last 7 days.", "jobs", {"impact": failed_jobs_7}))
    if failed_dataflows_3:
        items.append(_attention("bad", "recent_failed_dataflows", "Review recent failed dataflows", f"{failed_dataflows_3} dataflow runs failed in the last 3 days.", "failures", {"impact": failed_dataflows_3}))
    elif failed_dataflows_7:
        items.append(_attention("warning", "recent_failed_dataflows", "Review recent failed dataflows", f"{failed_dataflows_7} dataflow runs failed in the last 7 days.", "failures", {"impact": failed_dataflows_7}))
    top_failure = _first(failures.get("top_failing_dataflows"))
    if top_failure and int(top_failure.get("error_count") or 0) >= repeated_failure_min_errors:
        items.append(_attention("bad", "repeated_failure", "Repeated dataflow failure", f"{top_failure.get('dataflow_name')} failed {top_failure.get('error_count')} times.", "failures", {**top_failure, "impact": top_failure.get("error_count")}))
    if health.get("status") == "no_log_evidence":
        items.append(_attention("warning", "no_log_evidence", "No log evidence", "No monitoring logs were found in current filters.", "overview"))
    if health.get("latest_log_age_days") and health["latest_log_age_days"] > stale_log_warning_days:
        items.append(_attention("warning", "stale_logs", "Check log freshness", f"Latest log is {health['latest_log_age_days']} days old.", "overview"))
    maintenance_failed_7 = int(health.get("maintenance_failed_last_7_days") or 0)
    maintenance_failed_14 = int(health.get("maintenance_failed_last_14_days") or 0)
    maintenance_skipped_7 = int(health.get("maintenance_skipped_last_7_days") or 0)
    if maintenance_failed_7:
        items.append(_attention("bad", "maintenance_failed", "Review failed maintenance", f"{maintenance_failed_7} maintenance operations failed in the last 7 days.", "maintenance"))
    elif maintenance_failed_14:
        items.append(_attention("warning", "maintenance_failed", "Review failed maintenance", f"{maintenance_failed_14} maintenance operations failed in the last 14 days.", "maintenance"))
    if maintenance_skipped_7:
        items.append(_attention("warning", "maintenance_skipped", "Review skipped maintenance", f"{maintenance_skipped_7} maintenance operations were skipped in the last 7 days.", "maintenance"))
    maintenance_kpis = maintenance.get("kpis", {})
    maintenance_missing = int(maintenance_kpis.get("coverage_missing_tables") or 0)
    maintenance_lagged = int(maintenance_kpis.get("lagged_tables") or 0)
    maintenance_active = int(maintenance_kpis.get("latest_active_tables") or 0)
    if maintenance_missing:
        items.append(_attention("warning", "maintenance_coverage", "Review maintenance coverage", f"{maintenance_missing} active lakehouse tables have no maintenance evidence.", "maintenance", {"impact": maintenance_missing}))
    if maintenance_lagged:
        items.append(_attention("warning", "maintenance_lag", "Review maintenance lag", f"{maintenance_lagged} tables exceed the maintenance lag threshold.", "maintenance", {"impact": maintenance_lagged}))
    if maintenance_active:
        items.append(_attention("info", "maintenance_active", "Inspect active maintenance", f"{maintenance_active} table maintenance targets are running or pending.", "maintenance", {"impact": maintenance_active}))
    freshness_kpis = freshness.get("kpis", {})
    if freshness_kpis.get("stale_candidates"):
        items.append(_attention("warning", "stale_dataflows", "Review stale dataflows", f"{freshness_kpis['stale_candidates']} stale dataflow candidates were detected.", "freshness"))
    if freshness_kpis.get("watermark_unchanged_runs"):
        items.append(_attention("warning", "watermark_not_advanced", "Review unchanged watermarks", f"{freshness_kpis['watermark_unchanged_runs']} runs did not advance watermark values.", "freshness"))
    performance_kpis = performance.get("kpis", {})
    pressure_ratio = _num(performance_kpis, "duration_pressure_ratio") or 0
    pressure_p95 = _num(performance_kpis, "p95_duration_seconds") or 0
    pressure_severity = "bad" if pressure_ratio >= 10 and pressure_p95 >= 60 else "warning" if pressure_ratio >= 5 and pressure_p95 >= 30 else None
    if pressure_severity:
        items.append(_attention(pressure_severity, "performance_pressure", "Review performance pressure", f"P95 is {round(pressure_ratio, 1)}x P50 at {_format_seconds(pressure_p95)}.", "performance", {"impact": pressure_ratio, "p95_duration_seconds": pressure_p95}))
    optimization_candidates = int(performance_kpis.get("optimization_candidate_count") or 0)
    if optimization_candidates:
        items.append(_attention("warning", "optimization_candidates", "Review optimization candidates", f"{optimization_candidates} dataflow runs match performance optimization rules.", "performance", {"impact": optimization_candidates}))
    slowest_stage = _first(performance.get("duration_by_stage"))
    if not pressure_severity and slowest_stage and (_num(slowest_stage, "p95_duration_seconds") or 0) >= slow_stage_info_min_p95_seconds:
        items.append(_attention("info", "slowest_stage", "Inspect slowest stage", f"{slowest_stage.get('stage')} has p95 duration {_format_seconds(_num(slowest_stage, 'p95_duration_seconds') or 0)}.", "performance", slowest_stage))
    if reconciliation.get("mismatch_count"):
        items.append(_attention("warning", "log_reconciliation", "Review log consistency", f"{reconciliation['mismatch_count']} job totals differ from dataflow rollups.", "diagnostics"))
    diagnostics_kpis = diagnostics.get("kpis", {})
    linkage_gaps = int(diagnostics_kpis.get("orphan_dataflow_job_ids") or 0) + int(diagnostics_kpis.get("jobs_without_dataflow_records") or 0)
    if linkage_gaps:
        items.append(_attention("bad", "job_linkage_gaps", "Review job linkage gaps", f"{linkage_gaps} job IDs are not linked across job and dataflow logs.", "diagnostics", {"impact": linkage_gaps}))
    cache_warnings = int(diagnostics_kpis.get("cache_warning_count") or 0)
    if cache_warnings:
        items.append(_attention("warning", "log_cache_warnings", "Review log cache warnings", f"{cache_warnings} log sources have partial cache or read coverage.", "diagnostics", {"impact": cache_warnings}))
    dataflow_kpis = operations.get("dataflow_kpis", {})
    active_dataflows = int(dataflow_kpis.get("running") or 0) + int(dataflow_kpis.get("pending") or 0)
    if active_dataflows:
        items.append(_attention("info", "active_dataflows", "Inspect active dataflows", f"{active_dataflows} dataflow runs are running or pending in the current filters.", "dataflows", {"impact": active_dataflows}))
    runtime_contexts = operations.get("jobs_by_engine_provider", [])
    unhealthy_contexts = [context for context in runtime_contexts if int(context.get("failed") or 0) > 0 and (_num(context, "success_rate") or 0) < 95]
    if unhealthy_contexts:
        context = min(unhealthy_contexts, key=lambda item: (_num(item, "success_rate") or 0, -int(item.get("failed") or 0)))
        context_name = " / ".join(str(context.get(key) or "unknown") for key in ("engine_name", "metadata_provider_name"))
        context_success_rate = round(_num(context, "success_rate") or 0, 1)
        items.append(_attention("warning", "runtime_context_health", "Review runtime context health", f"{context_name} is at {context_success_rate}% success.", "jobs", {**context, "impact": context.get("failed")}))
    return _prioritize_attention(items, limit=max_attention_items)


def _prioritize_attention(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    severity_rank = {"bad": 0, "warning": 1, "info": 2, "good": 3}
    by_code: dict[str, dict[str, Any]] = {}
    for item in items:
        code = str(item.get("code") or "")
        current = by_code.get(code)
        if current is None or severity_rank.get(str(item.get("severity")), 4) < severity_rank.get(str(current.get("severity")), 4):
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
        "job_success_rate": {"label": "Job execution success rate", "formula": "succeeded jobs / (succeeded jobs + failed jobs)"},
        "job_failure_rate": {"label": "Job failure rate", "formula": "failed jobs / (succeeded jobs + failed jobs)"},
        "job_skip_rate": {"label": "Job skip rate", "formula": "skipped jobs / all job runs"},
        "job_pending_rate": {"label": "Job pending rate", "formula": "pending jobs / all job runs"},
        "job_running_rate": {"label": "Job running rate", "formula": "running jobs / all job runs"},
        "dataflow_success_rate": {"label": "Dataflow execution success rate", "formula": "succeeded dataflow runs / (succeeded dataflow runs + failed dataflow runs)"},
        "dataflow_failure_rate": {"label": "Dataflow failure rate", "formula": "failed dataflow runs / (succeeded dataflow runs + failed dataflow runs)"},
        "dataflow_skip_rate": {"label": "Dataflow skip rate", "formula": "skipped dataflow runs / all dataflow runs"},
        "dataflow_pending_rate": {"label": "Dataflow pending rate", "formula": "pending dataflow runs / all dataflow runs"},
        "dataflow_running_rate": {"label": "Dataflow running rate", "formula": "running dataflow runs / all dataflow runs"},
        "health_status": {"label": "Environment health", "formula": "highest severity matching rule: no evidence, stale logs, recent failures, maintenance issues, or reconciliation mismatch"},
        "today_window": {"label": "Today", "formula": "runs whose end_time/start_time falls on the current date in the configured Studio timezone"},
        "last_7_days_window": {"label": "Last 7 days", "formula": "runs whose end_time/start_time is within the last 7 * 24 hours"},
        "avg_duration": {"label": "Average duration", "formula": "average duration_seconds for executable runs (succeeded + failed) with duration present"},
        "duration_quartiles": {"label": "Duration percentiles", "formula": "P50/P75/P95/P99 duration_seconds for executable runs (succeeded + failed) with duration present"},
        "net_bytes_change": {"label": "Net bytes change", "formula": "destination bytes added - destination bytes removed"},
        "maintenance_efficiency": {"label": "Maintenance efficiency", "formula": "bytes removed / duration seconds"},
        "p95_duration": {"label": "P95 duration", "formula": "95th percentile of duration_seconds for executable runs (succeeded + failed) with duration present"},
        "log_coverage": {"label": "Log coverage", "formula": "presence and joinability of job logs and dataflow logs"},
    }


def _performance_enriched_run(row: dict[str, Any], thresholds: dict[str, float]) -> dict[str, Any]:
    rows_processed = _performance_rows_processed(row)
    maintenance_bytes, maintenance_files = _performance_maintenance_workload(row)
    source_duration = _performance_phase_duration(row, "source")
    transform_duration = _performance_phase_duration(row, "transform")
    destination_duration = _performance_phase_duration(row, "destination")
    overhead_duration = _performance_phase_duration(row, "overhead")
    phase_totals = {
        "source": source_duration,
        "transform": transform_duration,
        "destination": destination_duration,
        "overhead": overhead_duration,
    }
    bottleneck_phase = max(phase_totals.items(), key=lambda item: item[1])[0] if any(phase_totals.values()) else "unknown"
    duration = _num(row, "duration_seconds") or 0
    matched_reasons = _performance_candidate_reasons(
        duration=duration,
        rows_processed=rows_processed,
        operation_type=_dataflow_operation_type(row),
        maintenance_bytes=maintenance_bytes,
        maintenance_files=maintenance_files,
        phase_totals=phase_totals,
        thresholds=thresholds,
    )
    primary_reason = matched_reasons[0] if matched_reasons else (None, None, 0)
    return {
        **row,
        "rows_processed": rows_processed,
        "maintenance_bytes_processed": maintenance_bytes,
        "maintenance_files_processed": maintenance_files,
        "rows_read_per_second": _safe_ratio(_num(row, "source_rows_read") or 0, duration),
        "lakehouse_bytes_moved": (_num(row, "destination_bytes_added") or 0) + (_num(row, "destination_bytes_removed") or 0),
        "source_duration_seconds": source_duration,
        "transform_duration_seconds": transform_duration,
        "destination_duration_seconds": destination_duration,
        "overhead_duration_seconds": overhead_duration,
        "performance_bottleneck_phase": bottleneck_phase,
        "performance_candidate_codes": [reason[0] for reason in matched_reasons],
        "performance_candidate_reasons": [reason[1] for reason in matched_reasons],
        "performance_candidate_code": primary_reason[0],
        "performance_candidate_reason": primary_reason[1],
        "performance_candidate_priority": primary_reason[2],
    }


def _performance_candidate_reasons(
    *,
    duration: float,
    rows_processed: float,
    operation_type: str,
    maintenance_bytes: float,
    maintenance_files: float,
    phase_totals: dict[str, float],
    thresholds: dict[str, float],
) -> list[tuple[str, str, int]]:
    duration_p75 = thresholds.get("duration_p75_seconds", 0) or 0
    duration_p95 = thresholds.get("duration_p95_seconds", 0) or 0
    rows_p50 = thresholds.get("rows_processed_p50", 0) or 0
    maintenance_bytes_p50 = thresholds.get("maintenance_bytes_p50", 0) or 0
    maintenance_files_p50 = thresholds.get("maintenance_files_p50", 0) or 0
    total_phase_duration = sum(phase_totals.values())
    overhead_share = _safe_ratio(phase_totals.get("overhead", 0), total_phase_duration)
    largest_phase = max(phase_totals.items(), key=lambda item: item[1]) if total_phase_duration > 0 else ("unknown", 0)
    largest_phase_share = _safe_ratio(largest_phase[1], total_phase_duration)

    reasons: list[tuple[str, str, int]] = []
    is_maintenance = operation_type == "maintenance"
    if not is_maintenance and rows_p50 > 0 and 0 < rows_processed <= rows_p50 and duration_p95 > 0 and duration >= duration_p95:
        reasons.append(("slow_small_workload", "Slow small workload", 300))
    maintenance_is_small = (
        maintenance_bytes_p50 > 0 and 0 < maintenance_bytes <= maintenance_bytes_p50
    ) or (
        maintenance_bytes <= 0
        and maintenance_files_p50 > 0
        and 0 < maintenance_files <= maintenance_files_p50
    )
    if is_maintenance and maintenance_is_small and duration_p95 > 0 and duration >= duration_p95:
        reasons.append(("slow_small_maintenance", "Slow small maintenance workload", 300))
    if duration_p75 > 0 and duration >= duration_p75 and overhead_share >= 0.20:
        reasons.append(("high_overhead", "High overhead", 200))
    if not is_maintenance and duration_p75 > 0 and duration >= duration_p75 and largest_phase_share >= 0.90:
        reasons.append(("phase_skew", f"{_title_word(largest_phase[0])} phase skew", 100))
    return sorted(reasons, key=lambda reason: reason[2], reverse=True)


def _performance_thresholds_by_operation(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[_dataflow_operation_type(row)].append(row)

    def thresholds(items: list[dict[str, Any]]) -> dict[str, float]:
        durations = [_num(item, "duration_seconds") for item in items if _num(item, "duration_seconds") is not None]
        rows_processed = [_performance_rows_processed(item) for item in items]
        maintenance_workload = [_performance_maintenance_workload(item) for item in items]
        return {
            "duration_p75_seconds": _percentile_clean(durations, 0.75),
            "duration_p95_seconds": _percentile_clean(durations, 0.95),
            "rows_processed_p50": _percentile_clean([value for value in rows_processed if value > 0], 0.50),
            "maintenance_bytes_p50": _percentile_clean([value[0] for value in maintenance_workload if value[0] > 0], 0.50),
            "maintenance_files_p50": _percentile_clean([value[1] for value in maintenance_workload if value[1] > 0], 0.50),
        }

    return {
        "__all__": thresholds(rows),
        **{operation_type: thresholds(items) for operation_type, items in buckets.items()},
    }


def _performance_maintenance_workload(row: dict[str, Any]) -> tuple[float, float]:
    bytes_processed = sum(
        _num(row, field) or 0
        for field in ("destination_bytes_added", "destination_bytes_removed", "destination_bytes_saved")
    )
    files_processed = sum(
        _num(row, field) or 0
        for field in ("destination_files_added", "destination_files_removed")
    )
    return max(0.0, bytes_processed), max(0.0, files_processed)


def _performance_rows_processed(row: dict[str, Any]) -> float:
    row_candidates = [
        _num(row, "source_rows_read") or 0,
        _num(row, "destination_rows_written") or 0,
        (_num(row, "destination_rows_inserted") or 0)
        + (_num(row, "destination_rows_updated") or 0)
        + (_num(row, "destination_rows_deleted") or 0),
    ]
    return max(row_candidates)


def _performance_phase_duration(row: dict[str, Any], phase: str) -> float:
    if phase != "overhead":
        return max(0.0, _num(row, f"{phase}_duration_seconds") or 0)
    overhead = _num(row, "overhead_duration_seconds")
    if overhead is not None:
        return max(0.0, overhead)
    duration = _num(row, "duration_seconds") or 0
    known_duration = sum(
        max(0.0, _num(row, f"{known_phase}_duration_seconds") or 0)
        for known_phase in ("source", "transform", "destination")
    )
    return max(0.0, duration - known_duration)


def _performance_phase_totals(rows: list[dict[str, Any]]) -> dict[str, float]:
    return {
        phase: sum(_performance_phase_duration(row, phase) for row in rows)
        for phase in ("source", "transform", "destination", "overhead")
    }


def _performance_duration_distribution_by_stage(
    rows: list[dict[str, Any]],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    return _performance_duration_distribution(
        rows,
        group_key="stage",
        resolve_group=lambda row: _dimension_value(row.get("stage")),
        limit=limit,
    )


def _performance_duration_distribution(
    rows: list[dict[str, Any]],
    *,
    group_key: str,
    resolve_group,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        duration = _num(row, "duration_seconds")
        if duration is None:
            continue
        buckets[resolve_group(row)].append(row)

    result: list[dict[str, Any]] = []
    for group_value, items in buckets.items():
        clean = [_num(item, "duration_seconds") for item in items if _num(item, "duration_seconds") is not None]
        if not clean:
            continue
        q1_duration = _percentile(clean, 0.25)
        q3_duration = _percentile(clean, 0.75)
        iqr = q3_duration - q1_duration
        lower_fence = q1_duration - 1.5 * iqr
        upper_fence = q3_duration + 1.5 * iqr
        non_outlier_durations = [
            duration
            for duration in clean
            if lower_fence <= duration <= upper_fence
        ] or clean
        outliers = [
            {
                "duration_seconds": round(duration, 3),
                "dataflow_name": item.get("dataflow_name") or item.get("dataflow_id") or "unknown",
                "dataflow_id": item.get("dataflow_id"),
                "dataflow_run_id": item.get("dataflow_run_id"),
                "status": _status(item),
                "operation_type": _dataflow_operation_type(item),
            }
            for item in items
            for duration in [_num(item, "duration_seconds")]
            if duration is not None and (duration < lower_fence or duration > upper_fence)
        ]
        statuses = Counter(_status(item) for item in items)
        operation_mix = Counter(_dataflow_operation_type(item) for item in items)
        result.append({
            group_key: group_value,
            "group": group_value,
            "count": len(clean),
            "min_duration_seconds": round(min(clean), 3),
            "whisker_min_duration_seconds": round(min(non_outlier_durations), 3),
            "q1_duration_seconds": q1_duration,
            "p50_duration_seconds": _percentile(clean, 0.50),
            "q3_duration_seconds": q3_duration,
            "p95_duration_seconds": _percentile(clean, 0.95),
            "p99_duration_seconds": _percentile(clean, 0.99),
            "whisker_max_duration_seconds": round(max(non_outlier_durations), 3),
            "max_duration_seconds": round(max(clean), 3),
            "avg_duration_seconds": _avg(clean),
            "succeeded": statuses.get("succeeded", 0),
            "failed": statuses.get("failed", 0),
            "skipped": statuses.get("skipped", 0),
            "running": statuses.get("running", 0),
            "pending": statuses.get("pending", 0),
            "unknown": statuses.get("unknown", 0),
            "operation_mix": ", ".join(f"{name}: {count}" for name, count in sorted(operation_mix.items())),
            "outlier_count": len(outliers),
            "outliers": sorted(outliers, key=lambda item: float(item["duration_seconds"]), reverse=True)[:8],
        })
    sorted_result = sorted(
        result,
        key=lambda row: (-float(row["p95_duration_seconds"]), -int(row["count"]), str(row["group"])),
    )
    return sorted_result[:limit] if limit is not None else sorted_result


def _performance_phase_contribution_by_stage_operation(
    rows: list[dict[str, Any]],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    return _phase_duration_summary(
        rows,
        group_key="context",
        resolve_group=lambda row: f"{_dataflow_operation_type(row)} · {_dimension_value(row.get('stage'))}",
        limit=limit,
    )


def _performance_workload_efficiency_points(rows: list[dict[str, Any]], limit: int = 200) -> list[dict[str, Any]]:
    def sort_key(row: dict[str, Any]) -> tuple[int, float, float]:
        return (
            int(row.get("performance_candidate_priority") or 0),
            _num(row, "duration_seconds") or 0,
            _performance_rows_processed(row),
        )

    ranked = sorted(rows, key=sort_key, reverse=True)
    maintenance = [row for row in ranked if _dataflow_operation_type(row) == "maintenance"]
    pipeline = [row for row in ranked if _dataflow_operation_type(row) != "maintenance"]
    if maintenance and pipeline:
        maintenance_limit = max(1, limit // 4)
        selected = [*pipeline[: limit - maintenance_limit], *maintenance[:maintenance_limit]]
        selected_ids = {id(row) for row in selected}
        selected.extend(row for row in ranked if id(row) not in selected_ids and len(selected) < limit)
        selected.sort(key=sort_key, reverse=True)
    else:
        selected = ranked[:limit]

    points = []
    for row in selected:
        duration = _num(row, "duration_seconds") or 0
        rows_processed = _performance_rows_processed(row)
        points.append({
            "job_id": row.get("job_id"),
            "dataflow_id": row.get("dataflow_id"),
            "dataflow_run_id": row.get("dataflow_run_id"),
            "dataflow_name": row.get("dataflow_name") or row.get("dataflow_id") or "unknown",
            "stage": row.get("stage") or "unknown",
            "operation_type": _dataflow_operation_type(row),
            "status": _status(row),
            "rows_processed": rows_processed,
            "rows_read_per_second": _safe_ratio(_num(row, "source_rows_read") or 0, duration),
            "duration_seconds": duration,
            "lakehouse_bytes_moved": (_num(row, "destination_bytes_added") or 0) + (_num(row, "destination_bytes_removed") or 0),
            "destination_bytes_added": _num(row, "destination_bytes_added") or 0,
            "destination_bytes_removed": _num(row, "destination_bytes_removed") or 0,
            "performance_bottleneck_phase": row.get("performance_bottleneck_phase") or "unknown",
            "performance_candidate_reason": row.get("performance_candidate_reason"),
            "performance_candidate_reasons": row.get("performance_candidate_reasons") or [],
            "performance_candidate_priority": row.get("performance_candidate_priority") or 0,
            "maintenance_bytes_processed": row.get("maintenance_bytes_processed") or 0,
            "maintenance_files_processed": row.get("maintenance_files_processed") or 0,
        })
    return points


def _performance_slowest_dataflow_profiles(rows: list[dict[str, Any]], limit: int | None = None) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if dataflow_id:
            buckets[dataflow_id].append(row)

    result: list[dict[str, Any]] = []
    for dataflow_id, items in buckets.items():
        durations = [_num(item, "duration_seconds") for item in items if _num(item, "duration_seconds") is not None]
        if not durations:
            continue
        latest = max(items, key=lambda item: _row_timestamp(item) or datetime.min)
        phase_totals = _performance_phase_totals(items)
        bottleneck_phase = max(phase_totals.items(), key=lambda item: item[1])[0] if any(phase_totals.values()) else "unknown"
        result.append({
            "dataflow_id": dataflow_id,
            "dataflow_name": latest.get("dataflow_name") or dataflow_id,
            "stage": latest.get("stage") or "unknown",
            "operation_type": _dominant_dataflow_operation_type(items),
            "run_count": len(items),
            "avg_duration_seconds": _avg(durations),
            "p50_duration_seconds": _percentile(durations, 0.50),
            "p95_duration_seconds": _percentile(durations, 0.95),
            "p99_duration_seconds": _percentile(durations, 0.99),
            "max_duration_seconds": round(max(durations), 3),
            "performance_bottleneck_phase": bottleneck_phase,
            "source_name": latest.get("source_name") or latest.get("source_connection_name"),
            "destination_name": latest.get("destination_name") or latest.get("destination_connection_name"),
            "source_format": latest.get("source_format"),
            "destination_format": latest.get("destination_format"),
        })
    sorted_result = sorted(
        result,
        key=lambda row: (-float(row["p95_duration_seconds"]), -float(row["max_duration_seconds"]), -int(row["run_count"])),
    )
    return sorted_result[:limit] if limit is not None else sorted_result


def _performance_runtime_context_profiles(rows: list[dict[str, Any]], limit: int | None = None) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[(
            _dimension_value(row.get("platform_name")),
            _dimension_value(row.get("engine_name")),
            _dimension_value(row.get("metadata_provider_name")),
        )].append(row)

    result: list[dict[str, Any]] = []
    for (platform, engine, provider), items in buckets.items():
        statuses = Counter(_status(item) for item in items)
        executable = statuses.get("succeeded", 0) + statuses.get("failed", 0)
        durations = [_num(item, "duration_seconds") for item in items if _num(item, "duration_seconds") is not None]
        total_duration = sum(durations)
        rows_read = _sum(items, "source_rows_read")
        result.append({
            "platform_name": platform,
            "engine_name": engine,
            "metadata_provider_name": provider,
            "context": f"{platform} · {engine} · {provider}",
            "runs": len(items),
            "success_rate": _rate(statuses.get("succeeded", 0), executable),
            "failed": statuses.get("failed", 0),
            "avg_duration_seconds": _avg(durations),
            "p50_duration_seconds": _percentile_clean(durations, 0.50),
            "p95_duration_seconds": _percentile_clean(durations, 0.95),
            "rows_read_per_second": _safe_ratio(rows_read, total_duration),
            "slow_candidate_count": sum(1 for item in items if item.get("performance_candidate_code")),
        })
    sorted_result = sorted(
        result,
        key=lambda row: (-int(row["slow_candidate_count"]), -float(row["p95_duration_seconds"]), -int(row["runs"]), str(row["context"])),
    )
    return sorted_result[:limit] if limit is not None else sorted_result


def _performance_trend(
    rows: list[dict[str, Any]],
    trend_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    trend_context = trend_context or _trend_context({}, rows, timezone.utc)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    bucket_metadata: dict[str, dict[str, str | None]] = {}
    for row in rows:
        bucket = _date_bucket(row, trend_context)
        buckets[str(bucket["bucket"])].append(row)
        bucket_metadata[str(bucket["bucket"])] = bucket

    result: list[dict[str, Any]] = []
    for bucket_key, items in buckets.items():
        durations = [_num(item, "duration_seconds") for item in items if _num(item, "duration_seconds") is not None]
        bucket_info = bucket_metadata.get(bucket_key, {"bucket_start": None, "bucket_end": None})
        result.append({
            "date": bucket_key,
            "bucket": bucket_key,
            "bucket_start": bucket_info["bucket_start"],
            "bucket_end": bucket_info["bucket_end"],
            "grain": trend_context["effective_grain"],
            "run_count": len(items),
            "p50_duration_seconds": _percentile_clean(durations, 0.50),
            "p95_duration_seconds": _percentile_clean(durations, 0.95),
            "candidate_count": sum(1 for item in items if item.get("performance_candidate_code")),
        })
    return sorted(result, key=lambda item: str(item["bucket_start"] or item["bucket"]))


def _performance_investigation_queue(rows: list[dict[str, Any]], limit: int = 500) -> list[dict[str, Any]]:
    def sort_key(row: dict[str, Any]) -> tuple[int, float, datetime]:
        return (
            int(row.get("performance_candidate_priority") or 0),
            _num(row, "duration_seconds") or 0,
            _time_value(row.get("end_time") or row.get("start_time")),
        )

    return sorted(rows, key=sort_key, reverse=True)[:limit]


_PERFORMANCE_EVIDENCE_FIELDS = {
    "job_id", "dataflow_id", "dataflow_run_id", "dataflow_name", "stage",
    "status", "start_time", "end_time", "duration_seconds", "operation_type",
    "engine_name", "metadata_provider_name", "platform_name",
    "source_name", "source_connection_type", "source_format", "source_full_table",
    "source_table", "source_path", "source_status", "source_duration_seconds",
    "source_rows_read", "source_error_message",
    "transform_status", "transform_duration_seconds", "transform_error_message",
    "destination_name", "destination_connection_type", "destination_format",
    "destination_full_table", "destination_table", "destination_path",
    "destination_load_type", "destination_status", "destination_duration_seconds",
    "destination_rows_written", "destination_bytes_added", "destination_bytes_removed",
    "destination_error_message", "overhead_duration_seconds", "error_message",
    "error_preview", "failure_phase", "failure_message", "phase_health",
    "performance_bottleneck_phase", "performance_candidate_code",
    "performance_candidate_codes", "performance_candidate_reason",
    "performance_candidate_reasons", "performance_candidate_priority",
    "performance_rows_processed", "performance_rows_per_second",
    "performance_overhead_ratio", "performance_dominant_phase_ratio",
}


def _compact_performance_evidence(row: dict[str, Any]) -> dict[str, Any]:
    evidence = _failure_enriched_dataflow(row) if _status(row) == "failed" else row
    compact = {key: value for key, value in evidence.items() if key in _PERFORMANCE_EVIDENCE_FIELDS}
    compact["error_preview"] = _error_preview(evidence)
    compact["phase_health"] = _phase_health(evidence)
    return compact


_VOLUME_REGISTRY_BASE_FIELDS = {
    "job_id", "dataflow_id", "dataflow_run_id", "dataflow_name", "stage",
    "status", "start_time", "end_time", "duration_seconds", "operation_type",
    "source_name", "source_connection_type", "source_format", "source_full_table",
    "source_table", "source_path", "destination_name", "destination_connection_type",
    "destination_format", "destination_full_table", "destination_table",
    "destination_path", "destination_load_type", "latest_run_at", "latest_run_status",
    "run_count", "candidate_run_count", "candidate_run_reasons",
}


def _compact_volume_registry_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key in _VOLUME_REGISTRY_BASE_FIELDS
        or key.startswith("volume_")
        or key.startswith("peak_")
        or key.startswith("p95_")
    }


def _safe_ratio(numerator: float | int | None, denominator: float | int | None) -> float:
    numerator_value = float(numerator or 0)
    denominator_value = float(denominator or 0)
    if denominator_value <= 0:
        return 0
    return round(numerator_value / denominator_value, 3)


def _title_word(value: Any) -> str:
    return str(value or "unknown").replace("_", " ").strip().title() or "Unknown"


def _duration_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    top_rows = sorted(rows, key=lambda row: _num(row, "duration_seconds") or 0, reverse=True)[:30]
    return [
        {
            "dataflow_name": row.get("dataflow_name") or row.get("dataflow_id") or "unknown",
            "stage": row.get("stage") or "unknown",
            "engine_name": row.get("engine_name") or "unknown",
            "source_duration_seconds": _num(row, "source_duration_seconds") or 0,
            "transform_duration_seconds": _num(row, "transform_duration_seconds") or 0,
            "destination_duration_seconds": _num(row, "destination_duration_seconds") or 0,
            "overhead_duration_seconds": _performance_phase_duration(row, "overhead"),
            "duration_seconds": _num(row, "duration_seconds") or 0,
        }
        for row in top_rows
    ]


def _duration_vs_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points = []
    for row in rows:
        rows_processed = _performance_rows_processed(row)
        points.append({
            "dataflow_id": row.get("dataflow_id"),
            "dataflow_run_id": row.get("dataflow_run_id"),
            "dataflow_name": row.get("dataflow_name") or row.get("dataflow_id") or "unknown",
            "stage": row.get("stage") or "unknown",
            "engine_name": row.get("engine_name") or "unknown",
            "rows_processed": rows_processed,
            "duration_seconds": _num(row, "duration_seconds") or 0,
            "status": _status(row),
            "performance_bottleneck_phase": row.get("performance_bottleneck_phase") or "unknown",
        })
    return sorted(points, key=lambda item: item["duration_seconds"], reverse=True)[:200]


def _duration_by_stage(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = _num(row, "duration_seconds")
        if value is not None:
            buckets[row.get("stage") or "unknown"].append(value)
    result = []
    for stage, values in buckets.items():
        result.append({
            "stage": stage,
            "count": len(values),
            "avg_duration_seconds": round(sum(values) / len(values), 3),
            "p50_duration_seconds": _percentile(values, 0.50),
            "p95_duration_seconds": _percentile(values, 0.95),
            "max_duration_seconds": round(max(values), 3),
        })
    return sorted(result, key=lambda item: item["p95_duration_seconds"], reverse=True)[:40]


def _slowest_dataflows_by_p95(rows: list[dict[str, Any]], limit: int = 25) -> list[dict[str, Any]]:
    buckets: dict[str, list[float]] = defaultdict(list)
    labels: dict[str, dict[str, Any]] = {}
    latest_runs: dict[str, dict[str, Any]] = {}
    for row in rows:
        duration = _num(row, "duration_seconds")
        if duration is None:
            continue
        key = _dataflow_id(row)
        if not key:
            continue
        buckets[key].append(duration)
        if key not in labels:
            labels[key] = {
                "dataflow_id": key,
                "dataflow_name": row.get("dataflow_name") or row.get("dataflow_id") or "unknown",
            }
        latest = latest_runs.get(key)
        if latest is None or _time_value(row.get("end_time") or row.get("start_time")) > _time_value(
            latest.get("end_time") or latest.get("start_time")
        ):
            latest_runs[key] = row
    result = []
    for key, values in buckets.items():
        latest = latest_runs.get(key, {})
        label = labels.get(key, {"dataflow_id": None, "dataflow_name": key})
        result.append(
            {
                "dataflow_id": label["dataflow_id"],
                "dataflow_name": label["dataflow_name"],
                "run_count": len(values),
                "avg_duration_seconds": round(sum(values) / len(values), 3),
                "p95_duration_seconds": _percentile(values, 0.95),
                "max_duration_seconds": round(max(values), 3),
                "stage": latest.get("stage") or "unknown",
            }
        )
    return sorted(
        result,
        key=lambda item: (item["p95_duration_seconds"], item["max_duration_seconds"], item["run_count"]),
        reverse=True,
    )[:limit]


def _engine_stage_matrix(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        duration = _num(row, "duration_seconds")
        if duration is not None:
            buckets[(row.get("stage") or "unknown", row.get("engine_name") or "unknown")].append(duration)
    result = []
    for (stage, engine), values in buckets.items():
        result.append({
            "stage": stage,
            "engine_name": engine,
            "count": len(values),
            "p50_duration_seconds": _percentile(values, 0.50),
            "avg_duration_seconds": round(sum(values) / len(values), 3),
        })
    return sorted(result, key=lambda item: (item["stage"], item["engine_name"]))


def _rows_by_date(rows: list[dict[str, Any]], trend_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    trend_context = trend_context or _trend_context({}, rows, timezone.utc)
    buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "rows_read": 0,
            "rows_written": 0,
            "est_rows_written": 0,
            "rows_output": 0,
            "rows_output_estimated": 0,
            "rows_inserted": 0,
            "rows_updated": 0,
            "rows_deleted": 0,
            "dataflow_runs": 0,
        }
    )
    bucket_metadata: dict[str, dict[str, str | None]] = {}
    for row in rows:
        bucket = _date_bucket(row, trend_context)
        key = bucket["bucket"]
        bucket_metadata[key] = bucket
        rows_read = _num(row, "source_rows_read") or 0
        rows_written = _num(row, "destination_rows_written") or 0
        est_rows_written = _estimated_rows_written(row)
        rows_output, estimated = _output_rows(row)
        buckets[key]["rows_read"] += rows_read
        buckets[key]["rows_written"] += rows_written
        buckets[key]["est_rows_written"] += est_rows_written
        buckets[key]["rows_output"] += rows_output
        buckets[key]["rows_output_estimated"] += estimated
        buckets[key]["rows_inserted"] += _num(row, "destination_rows_inserted") or 0
        buckets[key]["rows_updated"] += _num(row, "destination_rows_updated") or 0
        buckets[key]["rows_deleted"] += _num(row, "destination_rows_deleted") or 0
        buckets[key]["dataflow_runs"] += 1
    return [
        {
            "date": key,
            "bucket": key,
            "bucket_start": bucket_metadata.get(key, {}).get("bucket_start"),
            "bucket_end": bucket_metadata.get(key, {}).get("bucket_end"),
            "grain": trend_context["effective_grain"],
            **values,
        }
        for key, values in sorted(buckets.items(), key=lambda item: str(bucket_metadata.get(item[0], {}).get("bucket_start") or item[0]))
    ]


def _output_rows(row: dict[str, Any]) -> tuple[float, float]:
    rows_written = _num(row, "destination_rows_written")
    if rows_written is not None and rows_written > 0:
        return rows_written, 0
    rows_read = _num(row, "source_rows_read") or 0
    if _status(row) == "succeeded" and rows_read > 0 and not _is_lakehouse_destination(row):
        return rows_read, rows_read
    return rows_written or 0, 0


def _estimated_rows_written(row: dict[str, Any]) -> float:
    rows_written = _num(row, "destination_rows_written") or 0
    if _is_lakehouse_destination(row):
        return rows_written
    if _status(row) == "succeeded":
        return _num(row, "source_rows_read") or rows_written
    return rows_written


def _is_lakehouse_destination(row: dict[str, Any]) -> bool:
    metadata_candidates = [
        row.get("destination_connection_type"),
        row.get("destination_format"),
    ]
    metadata = [
        str(value).strip().lower()
        for value in metadata_candidates
        if str(value or "").strip().lower() not in {"", "unknown", "none", "null", "n/a"}
    ]
    if metadata:
        text = " ".join(metadata)
        return any(token in text for token in ("lakehouse", "delta", "iceberg", "onelake", "deltalake"))

    fallback_candidates = [
        row.get("destination_name"),
        row.get("destination_path"),
    ]
    text = " ".join(str(value or "").lower() for value in fallback_candidates)
    return any(token in text for token in ("lakehouse", "delta", "iceberg", "onelake", "deltalake"))


def _bytes_by_date(rows: list[dict[str, Any]], trend_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    trend_context = trend_context or _trend_context({}, rows, timezone.utc)
    buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {"bytes_added": 0, "bytes_removed": 0, "bytes_saved": 0, "net_bytes": 0, "files_added": 0, "files_removed": 0}
    )
    bucket_metadata: dict[str, dict[str, str | None]] = {}
    for row in rows:
        bucket = _date_bucket(row, trend_context)
        key = bucket["bucket"]
        bucket_metadata[key] = bucket
        bytes_added = _num(row, "destination_bytes_added") or 0
        bytes_removed = _num(row, "destination_bytes_removed") or 0
        buckets[key]["bytes_added"] += bytes_added
        buckets[key]["bytes_removed"] += bytes_removed
        buckets[key]["bytes_saved"] += _num(row, "destination_bytes_saved") or 0
        buckets[key]["net_bytes"] += bytes_added - bytes_removed
        buckets[key]["files_added"] += _num(row, "destination_files_added") or 0
        buckets[key]["files_removed"] += _num(row, "destination_files_removed") or 0
    return [
        {
            "date": key,
            "bucket": key,
            "bucket_start": bucket_metadata.get(key, {}).get("bucket_start"),
            "bucket_end": bucket_metadata.get(key, {}).get("bucket_end"),
            "grain": trend_context["effective_grain"],
            **values,
        }
        for key, values in sorted(buckets.items(), key=lambda item: str(bucket_metadata.get(item[0], {}).get("bucket_start") or item[0]))
    ]


def _volume_by_load_type(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, float]] = defaultdict(lambda: {"rows_written": 0, "est_rows_written": 0, "bytes_added": 0, "count": 0})
    for row in rows:
        key = row.get("destination_load_type") or row.get("destination_operation_type") or "unknown"
        buckets[key]["rows_written"] += _num(row, "destination_rows_written") or 0
        buckets[key]["est_rows_written"] += _estimated_rows_written(row)
        buckets[key]["bytes_added"] += _num(row, "destination_bytes_added") or 0
        buckets[key]["count"] += 1
    return sorted(({"load_type": key, **values} for key, values in buckets.items()), key=lambda item: item["est_rows_written"], reverse=True)


def _volume_by_workload_type(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "rows_read": 0,
            "rows_written": 0,
            "est_rows_written": 0,
            "rows_inserted": 0,
            "rows_updated": 0,
            "rows_deleted": 0,
            "bytes_added": 0,
            "bytes_removed": 0,
            "runs": 0,
            "skipped": 0,
        }
    )
    for row in rows:
        operation = _dataflow_operation_type(row)
        load_type = _dimension_value(row.get("destination_load_type") or row.get("destination_operation_type"))
        key = f"{operation} · {load_type}"
        bucket = buckets[key]
        bucket["rows_read"] += _num(row, "source_rows_read") or 0
        bucket["rows_written"] += _num(row, "destination_rows_written") or 0
        bucket["est_rows_written"] += _estimated_rows_written(row)
        bucket["rows_inserted"] += _num(row, "destination_rows_inserted") or 0
        bucket["rows_updated"] += _num(row, "destination_rows_updated") or 0
        bucket["rows_deleted"] += _num(row, "destination_rows_deleted") or 0
        bucket["bytes_added"] += _num(row, "destination_bytes_added") or 0
        bucket["bytes_removed"] += _num(row, "destination_bytes_removed") or 0
        bucket["runs"] += 1
        bucket["skipped"] += 1 if _status(row) == "skipped" else 0
    return sorted(
        ({"workload_type": key, **values} for key, values in buckets.items()),
        key=lambda item: (item["rows_read"], item["est_rows_written"], item["bytes_added"] + item["bytes_removed"], item["runs"]),
        reverse=True,
    )


def _route_volume(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        source = str(row.get("source_name") or row.get("source_connection_name") or "unknown")
        destination = str(row.get("destination_name") or row.get("destination_connection_name") or "unknown")
        buckets[(source, destination)].append(row)

    result: list[dict[str, Any]] = []
    for (source, destination), items in buckets.items():
        result.append({
            "source_name": source,
            "destination_name": destination,
            "source_format": _dominant_value(items, "source_format"),
            "destination_format": _dominant_value(items, "destination_format"),
            "source_connection_type": _dominant_value(items, "source_connection_type"),
            "destination_connection_type": _dominant_value(items, "destination_connection_type"),
            "runs": len(items),
            "skipped": sum(1 for item in items if _status(item) == "skipped"),
            "rows_read": _sum(items, "source_rows_read"),
            "rows_written": _sum(items, "destination_rows_written"),
            "est_rows_written": round(sum(_estimated_rows_written(item) for item in items), 3),
            "rows_inserted": _sum(items, "destination_rows_inserted"),
            "rows_updated": _sum(items, "destination_rows_updated"),
            "rows_deleted": _sum(items, "destination_rows_deleted"),
            "bytes_added": _sum(items, "destination_bytes_added"),
            "bytes_removed": _sum(items, "destination_bytes_removed"),
            "files_added": _sum(items, "destination_files_added"),
            "files_removed": _sum(items, "destination_files_removed"),
        })
    return sorted(
        result,
        key=lambda row: (
            -float(row["rows_read"]),
            -float(row["est_rows_written"]),
            -float(row["rows_written"]),
            -float(row["bytes_added"]) - float(row["bytes_removed"]),
            -int(row["runs"]),
            str(row["source_name"]),
            str(row["destination_name"]),
        ),
    )


def _top_dataflow_net_bytes(rows: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    buckets: dict[str, float] = defaultdict(float)
    counts: Counter[str] = Counter()
    labels: dict[str, str] = {}
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if not dataflow_id:
            continue
        labels.setdefault(dataflow_id, str(row.get("dataflow_name") or dataflow_id))
        buckets[dataflow_id] += (_num(row, "destination_bytes_added") or 0) - (_num(row, "destination_bytes_removed") or 0)
        counts[dataflow_id] += 1
    return [
        {"dataflow_id": dataflow_id, "name": labels.get(dataflow_id, dataflow_id), "value": round(value, 3), "count": counts[dataflow_id]}
        for dataflow_id, value in sorted(buckets.items(), key=lambda item: abs(item[1]), reverse=True)[:limit]
    ]


def _top_dataflow_est_rows_written(rows: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    buckets: dict[str, float] = defaultdict(float)
    counts: Counter[str] = Counter()
    labels: dict[str, str] = {}
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if not dataflow_id:
            continue
        labels.setdefault(dataflow_id, str(row.get("dataflow_name") or dataflow_id))
        buckets[dataflow_id] += _estimated_rows_written(row)
        counts[dataflow_id] += 1
    return [
        {"dataflow_id": dataflow_id, "name": labels.get(dataflow_id, dataflow_id), "value": round(value, 3), "count": counts[dataflow_id]}
        for dataflow_id, value in sorted(buckets.items(), key=lambda item: item[1], reverse=True)[:limit]
    ]


def _volume_investigation_queue(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows_read_values = [_num(row, "source_rows_read") or 0 for row in rows if (_num(row, "source_rows_read") or 0) > 0]
    est_rows_values = [_estimated_rows_written(row) for row in rows if _estimated_rows_written(row) > 0]
    lakehouse_rows_values = [_num(row, "destination_rows_written") or 0 for row in rows if (_num(row, "destination_rows_written") or 0) > 0]
    net_byte_values = [
        abs((_num(row, "destination_bytes_added") or 0) - (_num(row, "destination_bytes_removed") or 0))
        for row in rows
        if abs((_num(row, "destination_bytes_added") or 0) - (_num(row, "destination_bytes_removed") or 0)) > 0
    ]
    file_change_values = [
        (_num(row, "destination_files_added") or 0) + (_num(row, "destination_files_removed") or 0)
        for row in rows
        if ((_num(row, "destination_files_added") or 0) + (_num(row, "destination_files_removed") or 0)) > 0
    ]
    thresholds = {
        "read": _percentile(rows_read_values, 0.95),
        "est_rows": _percentile(est_rows_values, 0.95),
        "lakehouse_rows": _percentile(lakehouse_rows_values, 0.95),
        "bytes": _percentile(net_byte_values, 0.95),
        "files": _percentile(file_change_values, 0.95),
    }
    candidates = [_enrich_volume_candidate(row, thresholds) for row in rows]
    candidates = [row for row in candidates if row["volume_candidate_priority"] > 0]
    return sorted(
        candidates,
        key=lambda row: (
            -float(row["volume_candidate_priority"]),
            -float(row["volume_rows_read"]),
            -float(row["volume_est_rows_written"]),
            -float(row["volume_lakehouse_rows_written"]),
            -abs(float(row["volume_net_bytes"])),
            -_time_value(row.get("end_time") or row.get("start_time")).timestamp(),
        ),
    )


def _enrich_volume_candidate(row: dict[str, Any], thresholds: dict[str, float]) -> dict[str, Any]:
    rows_read = _num(row, "source_rows_read") or 0
    est_rows_written = _estimated_rows_written(row)
    lakehouse_rows_written = _num(row, "destination_rows_written") or 0
    bytes_added = _num(row, "destination_bytes_added") or 0
    bytes_removed = _num(row, "destination_bytes_removed") or 0
    files_added = _num(row, "destination_files_added") or 0
    files_removed = _num(row, "destination_files_removed") or 0
    net_bytes = bytes_added - bytes_removed
    files_changed = files_added + files_removed
    checks = [
        ("read", rows_read, thresholds["read"], "High rows read"),
        ("est_rows", est_rows_written, thresholds["est_rows"], "High estimated rows written"),
        ("lakehouse_rows", lakehouse_rows_written, thresholds["lakehouse_rows"], "High lakehouse rows written"),
        ("bytes", abs(net_bytes), thresholds["bytes"], "High lakehouse net bytes"),
        ("files", files_changed, thresholds["files"], "High lakehouse file churn"),
    ]
    matched = [(kind, value, threshold, reason) for kind, value, threshold, reason in checks if threshold > 0 and value >= threshold and value > 0]
    if matched:
        kind, value, threshold, reason = max(matched, key=lambda item: item[1] / item[2] if item[2] else 0)
        priority = round(value / threshold, 3) if threshold else 0
    else:
        kind, reason, priority = "none", "", 0
    return {
        **row,
        "volume_rows_read": rows_read,
        "volume_est_rows_written": est_rows_written,
        "volume_lakehouse_rows_written": lakehouse_rows_written,
        "volume_rows_inserted": _num(row, "destination_rows_inserted") or 0,
        "volume_rows_updated": _num(row, "destination_rows_updated") or 0,
        "volume_rows_deleted": _num(row, "destination_rows_deleted") or 0,
        "volume_bytes_added": bytes_added,
        "volume_bytes_removed": bytes_removed,
        "volume_net_bytes": net_bytes,
        "volume_files_added": files_added,
        "volume_files_removed": files_removed,
        "volume_files_changed": files_changed,
        "volume_candidate_kind": kind,
        "volume_candidate_reason": reason,
        "volume_candidate_priority": priority,
    }


def _volume_dataflow_registry(
    rows: list[dict[str, Any]],
    run_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if dataflow_id:
            grouped[dataflow_id].append(row)

    candidate_runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in run_candidates:
        dataflow_id = _dataflow_id(row)
        if dataflow_id:
            candidate_runs[dataflow_id].append(row)

    registry: list[dict[str, Any]] = []
    for dataflow_id, items in grouped.items():
        ordered = sorted(items, key=lambda item: _time_value(item.get("end_time") or item.get("start_time")), reverse=True)
        latest = ordered[0]
        durations = [value for item in items if (value := _num(item, "duration_seconds")) is not None]
        rows_read_values = [(_num(item, "source_rows_read") or 0) for item in items]
        est_rows_values = [_estimated_rows_written(item) for item in items]
        lakehouse_rows_values = [(_num(item, "destination_rows_written") or 0) for item in items]
        bytes_added = _sum(items, "destination_bytes_added")
        bytes_removed = _sum(items, "destination_bytes_removed")
        files_added = _sum(items, "destination_files_added")
        files_removed = _sum(items, "destination_files_removed")
        matched_run_reasons = sorted({str(item.get("volume_candidate_reason")) for item in candidate_runs[dataflow_id] if item.get("volume_candidate_reason")})
        registry.append({
            **latest,
            "dataflow_id": dataflow_id,
            "latest_run_at": latest.get("end_time") or latest.get("start_time"),
            "latest_run_status": _status(latest),
            "run_count": len(items),
            "candidate_run_count": len(candidate_runs[dataflow_id]),
            "candidate_run_reasons": matched_run_reasons,
            "volume_rows_read": sum(rows_read_values),
            "volume_est_rows_written": round(sum(est_rows_values), 3),
            "volume_lakehouse_rows_written": sum(lakehouse_rows_values),
            "volume_rows_inserted": _sum(items, "destination_rows_inserted"),
            "volume_rows_updated": _sum(items, "destination_rows_updated"),
            "volume_rows_deleted": _sum(items, "destination_rows_deleted"),
            "volume_bytes_added": bytes_added,
            "volume_bytes_removed": bytes_removed,
            "volume_net_bytes": bytes_added - bytes_removed,
            "volume_files_added": files_added,
            "volume_files_removed": files_removed,
            "volume_files_changed": files_added + files_removed,
            "avg_rows_read": round(sum(rows_read_values) / len(items), 3),
            "avg_est_rows_written": round(sum(est_rows_values) / len(items), 3),
            "peak_rows_read": max(rows_read_values, default=0),
            "peak_est_rows_written": max(est_rows_values, default=0),
            "peak_lakehouse_rows_written": max(lakehouse_rows_values, default=0),
            "p95_rows_read": _percentile_clean(rows_read_values, 0.95),
            "p95_est_rows_written": _percentile_clean(est_rows_values, 0.95),
            "p95_lakehouse_rows_written": _percentile_clean(lakehouse_rows_values, 0.95),
            "avg_duration_seconds": round(sum(durations) / len(durations), 3) if durations else 0,
            "p95_duration_seconds": _percentile_clean(durations, 0.95),
        })

    threshold_fields = {
        "read": "volume_rows_read",
        "est_rows": "volume_est_rows_written",
        "lakehouse_rows": "volume_lakehouse_rows_written",
        "bytes": "volume_net_bytes",
        "files": "volume_files_changed",
    }
    thresholds = {
        kind: _percentile([abs(float(row.get(field) or 0)) for row in registry if abs(float(row.get(field) or 0)) > 0], 0.95)
        for kind, field in threshold_fields.items()
    }
    labels = {
        "read": "High rows read",
        "est_rows": "High estimated rows written",
        "lakehouse_rows": "High lakehouse rows written",
        "bytes": "High lakehouse net bytes",
        "files": "High lakehouse file churn",
    }
    result: list[dict[str, Any]] = []
    for row in registry:
        matched = []
        for kind, field in threshold_fields.items():
            value = abs(float(row.get(field) or 0))
            threshold = float(thresholds.get(kind) or 0)
            if threshold > 0 and value > 0 and value >= threshold:
                matched.append({
                    "kind": kind,
                    "label": labels[kind],
                    "value": value,
                    "threshold": threshold,
                    "ratio": round(value / threshold, 3),
                })
        primary = max(matched, key=lambda item: float(item["ratio"]), default=None)
        result.append({
            **row,
            "volume_candidate_kind": primary["kind"] if primary else "none",
            "volume_candidate_reason": primary["label"] if primary else "",
            "volume_candidate_priority": primary["ratio"] if primary else 0,
            "volume_candidate_signals": matched,
        })
    return sorted(
        result,
        key=lambda row: (
            -float(row.get("volume_candidate_priority") or 0),
            -float(row.get("volume_rows_read") or 0),
            -float(row.get("volume_est_rows_written") or 0),
            str(row.get("dataflow_name") or row.get("dataflow_id") or ""),
        ),
    )


def _maintenance_health_status(
    maintenance: list[dict[str, Any]],
    *,
    latest_failed_tables: int,
    coverage: dict[str, Any],
    latest_skipped_tables: int,
    latest_active_tables: int,
    lagged_tables: int,
) -> str:
    if not maintenance:
        return "no_evidence"
    if latest_failed_tables:
        return "has_issues"
    if int(coverage.get("coverage_missing_tables") or 0) or latest_skipped_tables or latest_active_tables or lagged_tables:
        return "warning"
    return "healthy"


def _maintenance_status_by_date(rows: list[dict[str, Any]], trend_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return _status_by_date(rows, trend_context=trend_context)


def _maintenance_reclaim_by_date(rows: list[dict[str, Any]], trend_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    trend_context = trend_context or _trend_context({}, rows, timezone.utc)
    buckets: dict[str, dict[str, float]] = defaultdict(lambda: {
        "bytes_reclaimed": 0,
        "bytes_saved": 0,
        "files_removed": 0,
        "runs": 0,
    })
    bucket_metadata: dict[str, dict[str, str | None]] = {}
    for row in rows:
        bucket = _date_bucket(row, trend_context)
        key = bucket["bucket"]
        bucket_metadata[key] = bucket
        buckets[key]["bytes_reclaimed"] += _num(row, "destination_bytes_removed") or 0
        buckets[key]["bytes_saved"] += _num(row, "destination_bytes_saved") or 0
        buckets[key]["files_removed"] += _num(row, "destination_files_removed") or 0
        buckets[key]["runs"] += 1
    return [
        {
            "date": key,
            "bucket": key,
            "bucket_start": bucket_metadata.get(key, {}).get("bucket_start"),
            "bucket_end": bucket_metadata.get(key, {}).get("bucket_end"),
            "grain": trend_context["effective_grain"],
            **values,
        }
        for key, values in sorted(buckets.items(), key=lambda item: str(bucket_metadata.get(item[0], {}).get("bucket_start") or item[0]))
    ]


def _maintenance_operation_type(row: dict[str, Any]) -> str:
    operation = row.get("destination_operation_type") or row.get("operation_type") or "unknown"
    return _dimension_value(operation)


def _maintenance_operation_health(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "operation_type": "",
        "count": 0,
        "bytes_reclaimed": 0.0,
        "files_removed": 0.0,
        "duration_seconds": 0.0,
        "no_op_runs": 0,
        "no_op_duration_seconds": 0.0,
        **{status: 0 for status in _STATUS_KEYS},
    })
    for row in rows:
        operation = _maintenance_operation_type(row)
        bucket = buckets[operation]
        bucket["operation_type"] = operation
        bucket["count"] += 1
        status = _status(row)
        bucket[status if status in _STATUS_KEYS else "unknown"] += 1
        bucket["bytes_reclaimed"] += _num(row, "destination_bytes_removed") or 0
        bucket["files_removed"] += _num(row, "destination_files_removed") or 0
        bucket["duration_seconds"] += _num(row, "duration_seconds") or 0
        if _is_no_op_maintenance(row):
            bucket["no_op_runs"] += 1
            bucket["no_op_duration_seconds"] += _num(row, "duration_seconds") or 0
    result = []
    for item in buckets.values():
        duration = float(item["duration_seconds"] or 0)
        bytes_reclaimed = float(item["bytes_reclaimed"] or 0)
        item["bytes_reclaimed_per_second"] = round(bytes_reclaimed / duration, 3) if duration else 0
        item["success_rate"] = _rate(int(item["succeeded"]), int(item["succeeded"]) + int(item["failed"]))
        result.append(item)
    return sorted(result, key=lambda item: (-int(item["count"]), str(item["operation_type"])))


def _maintenance_table_outcome(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[_maintenance_target_identity(row)].append(row)
    result = []
    for target, items in buckets.items():
        latest = max(items, key=lambda item: _time_value(item.get("end_time") or item.get("start_time")))
        duration = round(sum(_num(item, "duration_seconds") or 0 for item in items), 3)
        bytes_removed = _sum(items, "destination_bytes_removed")
        files_removed = _sum(items, "destination_files_removed")
        no_op_items = [item for item in items if _is_no_op_maintenance(item)]
        result.append({
            "target": target,
            "target_display": _maintenance_target_display(target),
            "table": target,
            "destination_table": latest.get("destination_table"),
            "destination_path": latest.get("destination_path"),
            "destination_name": latest.get("destination_name") or latest.get("destination_connection_name"),
            "format": latest.get("destination_format") or "unknown",
            "operation_type": _dominant_value(items, "destination_operation_type") or _dominant_value(items, "operation_type"),
            "run_count": len(items),
            "last_time": latest.get("end_time") or latest.get("start_time"),
            "files_removed": files_removed,
            "bytes_removed": bytes_removed,
            "bytes_reclaimed": bytes_removed,
            "bytes_saved": _sum(items, "destination_bytes_saved"),
            "duration_seconds": duration,
            "bytes_reclaimed_per_second": round(bytes_removed / duration, 3) if duration else 0,
            "status": latest.get("status") or "unknown",
            "no_op_runs": len(no_op_items),
            "no_op_duration_seconds": round(sum(_num(item, "duration_seconds") or 0 for item in no_op_items), 3),
        })
    return sorted(
        result,
        key=lambda item: (
            -float(item["bytes_reclaimed"]),
            -float(item["files_removed"]),
            -_time_value(item.get("last_time")).timestamp(),
            str(item["target"]),
        ),
    )


def _maintenance_table_registry(all_rows: list[dict[str, Any]], maintenance_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    maintenance_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_rows:
        if _is_active_lakehouse_destination(row):
            active_by_target[_maintenance_target_identity(row)].append(row)
    for row in maintenance_rows:
        maintenance_by_target[_maintenance_target_identity(row)].append(row)

    all_targets = set(active_by_target) | set(maintenance_by_target)
    result = []
    for target in all_targets:
        etl_items = active_by_target.get(target, [])
        maintenance_items = maintenance_by_target.get(target, [])
        evidence = [*maintenance_items, *etl_items]
        latest_evidence = max(evidence, key=lambda item: _time_value(item.get("end_time") or item.get("start_time"))) if evidence else {}
        latest_etl = max(etl_items, key=lambda item: _time_value(item.get("end_time") or item.get("start_time"))) if etl_items else None
        latest_maintenance = max(maintenance_items, key=lambda item: _time_value(item.get("end_time") or item.get("start_time"))) if maintenance_items else None
        latest_maintenance_time = (latest_maintenance or {}).get("end_time") or (latest_maintenance or {}).get("start_time")
        latest_etl_time = (latest_etl or {}).get("end_time") or (latest_etl or {}).get("start_time")
        status_counts = Counter(_status(row) for row in maintenance_items)
        duration = round(sum(_num(item, "duration_seconds") or 0 for item in maintenance_items), 3)
        bytes_removed = _sum(maintenance_items, "destination_bytes_removed")
        files_removed = _sum(maintenance_items, "destination_files_removed")
        no_op_items = [item for item in maintenance_items if _is_no_op_maintenance(item)]
        maintenance_lag_seconds = _maintenance_lag_seconds(latest_etl_time, latest_maintenance_time)
        health, reason, priority = _maintenance_table_health(
            active=bool(etl_items),
            latest_maintenance=latest_maintenance,
            maintenance_lag_seconds=maintenance_lag_seconds,
        )
        upstream_dataflows = _maintenance_upstream_dataflows(etl_items)
        result.append({
            "target": target,
            "target_display": _maintenance_target_display(target),
            "table": target,
            "destination_table": latest_evidence.get("destination_table"),
            "destination_full_table": latest_evidence.get("destination_full_table"),
            "destination_path": latest_evidence.get("destination_path"),
            "destination_name": latest_evidence.get("destination_name") or latest_evidence.get("destination_connection_name"),
            "destination_connection_name": latest_evidence.get("destination_connection_name") or latest_evidence.get("destination_name"),
            "destination_connection_type": latest_evidence.get("destination_connection_type"),
            "format": latest_evidence.get("destination_format") or "unknown",
            "destination_format": latest_evidence.get("destination_format") or "unknown",
            "active_lakehouse_table": bool(etl_items),
            "maintained_table": bool(maintenance_items),
            "run_count": len(maintenance_items),
            "succeeded": status_counts.get("succeeded", 0),
            "failed": status_counts.get("failed", 0),
            "skipped": status_counts.get("skipped", 0),
            "running": status_counts.get("running", 0),
            "pending": status_counts.get("pending", 0),
            "unknown": status_counts.get("unknown", 0),
            "latest_status": _status(latest_maintenance) if latest_maintenance else "missing",
            "status": _status(latest_maintenance) if latest_maintenance else "missing",
            "latest_maintenance_time": latest_maintenance_time,
            "latest_etl_write_time": latest_etl_time,
            "maintenance_lag_seconds": maintenance_lag_seconds,
            "maintenance_lag_warning": maintenance_lag_seconds > _MAINTENANCE_LAG_WARNING_DAYS * 86400,
            "maintenance_lag_warning_days": _MAINTENANCE_LAG_WARNING_DAYS,
            "files_removed": files_removed,
            "bytes_removed": bytes_removed,
            "bytes_reclaimed": bytes_removed,
            "bytes_saved": _sum(maintenance_items, "destination_bytes_saved"),
            "duration_seconds": duration,
            "bytes_reclaimed_per_second": round(bytes_removed / duration, 3) if duration else 0,
            "no_op_runs": len(no_op_items),
            "no_op_duration_seconds": round(sum(_num(item, "duration_seconds") or 0 for item in no_op_items), 3),
            "table_health": health,
            "attention_reason": reason,
            "attention_priority": priority,
            "upstream_dataflows": upstream_dataflows,
            "upstream_run_count": len(etl_items),
        })
    return sorted(
        result,
        key=lambda item: (
            -int(item["attention_priority"]),
            -float(item["bytes_reclaimed"]),
            -float(item["files_removed"]),
            -_time_value(item.get("latest_maintenance_time") or item.get("latest_etl_write_time")).timestamp(),
            str(item["target"]),
        ),
    )


def _maintenance_upstream_dataflows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = _dataflow_id(row) or str(row.get("dataflow_name") or "unknown")
        buckets[key].append(row)
    result = []
    for dataflow_id, items in buckets.items():
        latest = max(items, key=lambda item: _time_value(item.get("end_time") or item.get("start_time")))
        source_connection = latest.get("source_name") or latest.get("source_connection_name") or "unknown"
        source_object = (
            latest.get("source_full_table")
            or latest.get("source_table")
            or latest.get("source_path")
            or latest.get("source_python_function")
            or latest.get("source_query")
            or "-"
        )
        result.append({
            "dataflow_id": dataflow_id,
            "dataflow_name": latest.get("dataflow_name") or dataflow_id,
            "stage": latest.get("stage") or "unknown",
            "operation_type": latest.get("operation_type") or "unknown",
            "source": f"{source_connection} · {source_object}",
            "load_type": latest.get("destination_load_type") or latest.get("destination_operation_type") or "-",
            "latest_status": _status(latest),
            "latest_time": latest.get("end_time") or latest.get("start_time"),
            "run_count": len(items),
            "rows_read": _sum(items, "source_rows_read"),
        })
    return sorted(result, key=lambda item: str(item["dataflow_name"]))


def _maintenance_table_health(
    *,
    active: bool,
    latest_maintenance: dict[str, Any] | None,
    maintenance_lag_seconds: int,
) -> tuple[str, str, int]:
    if not latest_maintenance:
        if active:
            return "warning", "Missing maintenance coverage", 80
        return "no_evidence", "No maintenance evidence", 10
    latest_status = _status(latest_maintenance)
    if latest_status == "failed":
        return "has_issues", "Latest maintenance failed", 100
    if latest_status in {"running", "pending"}:
        return "warning", f"Latest maintenance is {latest_status}", 90
    if latest_status == "skipped":
        return "warning", "Latest maintenance skipped", 70
    if maintenance_lag_seconds > _MAINTENANCE_LAG_WARNING_DAYS * 86400:
        return "warning", f"Maintenance lag exceeds {_MAINTENANCE_LAG_WARNING_DAYS} days", 60
    return "healthy", "Maintained table", 0


def _maintenance_lag_seconds(latest_etl_time: object, latest_maintenance_time: object) -> int:
    latest_etl = _time_value(latest_etl_time)
    latest_maintenance = _time_value(latest_maintenance_time)
    if latest_etl == datetime.min.replace(tzinfo=timezone.utc) or latest_maintenance == datetime.min.replace(tzinfo=timezone.utc):
        return 0
    return max(0, int((latest_etl - latest_maintenance).total_seconds()))


def _maintenance_target_display(target: str) -> str:
    return target.split("::", 1)[1] if "::" in target else target


def _maintenance_coverage_from_registry(registry: list[dict[str, Any]]) -> dict[str, Any]:
    active_tables = [row for row in registry if row.get("active_lakehouse_table")]
    maintained_active_tables = [row for row in active_tables if row.get("maintained_table")]
    missing_tables = [row for row in active_tables if not row.get("maintained_table")]
    return {
        "active_lakehouse_tables": len(active_tables),
        "maintained_tables": len(maintained_active_tables),
        "coverage_missing_tables": len(missing_tables),
        "coverage_rate": _rate(len(maintained_active_tables), len(active_tables)),
    }


def _maintenance_table_attention(registry: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    return [
        row
        for row in sorted(
            registry,
            key=lambda item: (
                -int(item.get("attention_priority") or 0),
                -float(item.get("bytes_reclaimed") or 0),
                str(item.get("target") or ""),
            ),
        )
        if int(row.get("attention_priority") or 0) > 0
    ][:limit]


def _maintenance_table_efficiency_points(registry: list[dict[str, Any]], limit: int = 1000) -> list[dict[str, Any]]:
    return sorted(
        registry,
        key=lambda item: (
            -int(item.get("attention_priority") or 0),
            -float(item.get("duration_seconds") or 0),
            str(item.get("target") or ""),
        ),
    )[:limit]


def _maintenance_efficiency_points(rows: list[dict[str, Any]], limit: int = 1000) -> list[dict[str, Any]]:
    return [
        _enrich_maintenance_run(row, rows)
        for row in sorted(rows, key=lambda item: _num(item, "duration_seconds") or 0, reverse=True)[:limit]
    ]


def _maintenance_investigation_queue(rows: list[dict[str, Any]], limit: int = 200) -> list[dict[str, Any]]:
    enriched = [_enrich_maintenance_run(row, rows) for row in rows]
    return sorted(
        enriched,
        key=lambda row: (
            -float(row.get("maintenance_candidate_priority") or 0),
            -_time_value(row.get("end_time") or row.get("start_time")).timestamp(),
            str(row.get("dataflow_name") or ""),
        ),
    )[:limit]


def _enrich_maintenance_run(row: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    bytes_removed = _num(row, "destination_bytes_removed") or 0
    files_removed = _num(row, "destination_files_removed") or 0
    duration = _num(row, "duration_seconds") or 0
    kind, reason, priority = _maintenance_candidate(row, rows)
    return {
        **row,
        "maintenance_operation_type": _maintenance_operation_type(row),
        "maintenance_target": _maintenance_target_identity(row),
        "maintenance_bytes_reclaimed": bytes_removed,
        "maintenance_files_removed": files_removed,
        "maintenance_bytes_saved": _num(row, "destination_bytes_saved") or 0,
        "maintenance_bytes_per_second": round(bytes_removed / duration, 3) if duration else 0,
        "maintenance_candidate_kind": kind,
        "maintenance_candidate_reason": reason,
        "maintenance_candidate_priority": priority,
    }


def _maintenance_candidate(row: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[str, str, int]:
    status = _status(row)
    if status == "failed":
        return "failed", "Failed maintenance run", 6
    if status == "skipped":
        return "skipped", "Skipped maintenance run", 5
    if _is_no_op_maintenance(row):
        return "no_op", "No-op maintenance run", 4
    duration = _num(row, "duration_seconds") or 0
    if duration >= _maintenance_duration_percentile(rows, 0.95) and duration > 0:
        return "high_duration", "High maintenance duration", 3
    bytes_removed = _num(row, "destination_bytes_removed") or 0
    if bytes_removed >= _maintenance_bytes_percentile(rows, 0.95) and bytes_removed > 0:
        return "high_reclaim", "High bytes reclaimed", 2
    return "latest", "Latest maintenance evidence", 1


def _is_no_op_maintenance(row: dict[str, Any]) -> bool:
    return (
        _status(row) == "succeeded"
        and (_num(row, "destination_bytes_removed") or 0) == 0
        and (_num(row, "destination_files_removed") or 0) == 0
    )


def _maintenance_duration_percentile(rows: list[dict[str, Any]], percentile: float) -> float:
    values = [_num(row, "duration_seconds") or 0 for row in rows if (_num(row, "duration_seconds") or 0) > 0]
    return _percentile(values, percentile) if values else 0


def _maintenance_bytes_percentile(rows: list[dict[str, Any]], percentile: float) -> float:
    values = [_num(row, "destination_bytes_removed") or 0 for row in rows if (_num(row, "destination_bytes_removed") or 0) > 0]
    return _percentile(values, percentile) if values else 0


def _maintenance_coverage(all_rows: list[dict[str, Any]], maintenance_rows: list[dict[str, Any]]) -> dict[str, Any]:
    active_tables = {
        _maintenance_target_identity(row)
        for row in all_rows
        if _is_active_lakehouse_destination(row)
    }
    maintained_tables = {_maintenance_target_identity(row) for row in maintenance_rows}
    missing_tables = sorted(active_tables - maintained_tables)
    return {
        "active_lakehouse_tables": len(active_tables),
        "maintained_tables": len(active_tables & maintained_tables),
        "coverage_missing_tables": len(missing_tables),
        "coverage_rate": _rate(len(active_tables & maintained_tables), len(active_tables)),
    }


def _is_active_lakehouse_destination(row: dict[str, Any]) -> bool:
    if _dataflow_operation_type(row) != "etl":
        return False
    if not _is_lakehouse_destination(row):
        return False
    return any((_num(row, key) or 0) > 0 for key in (
        "destination_files_added",
        "destination_bytes_added",
        "destination_rows_written",
        "destination_rows_inserted",
        "destination_rows_updated",
        "destination_rows_deleted",
    ))


def _maintenance_target_identity(row: dict[str, Any]) -> str:
    connection = str(row.get("destination_name") or row.get("destination_connection_name") or "unknown").strip()
    table = _qualified_table_name(
        row.get("destination_catalog"),
        row.get("destination_database"),
        row.get("destination_schema"),
        row.get("destination_table"),
    )
    if table:
        return f"{connection}::{table}"
    for key in ("destination_full_table", "destination_table", "destination_path"):
        value = row.get(key)
        if value:
            return f"{connection}::{str(value)}"
    return f"{connection}:unknown"


def _qualified_table_name(*parts: object) -> str:
    values = [str(part).strip("` ") for part in parts if part not in (None, "")]
    return ".".join(values)


def _maintenance_format_comparison(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, float]] = defaultdict(lambda: {"files_removed": 0, "bytes_removed": 0, "count": 0})
    for row in rows:
        key = row.get("destination_format") or "unknown"
        buckets[key]["files_removed"] += _num(row, "destination_files_removed") or 0
        buckets[key]["bytes_removed"] += _num(row, "destination_bytes_removed") or 0
        buckets[key]["count"] += 1
    return [{"format": key, **values} for key, values in sorted(buckets.items())]


def _maintenance_per_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[row.get("destination_table") or row.get("destination_path") or "unknown"].append(row)
    result = []
    for table, items in buckets.items():
        latest = max(items, key=lambda item: _time_value(item.get("end_time") or item.get("start_time")))
        result.append({
            "table": table,
            "last_time": latest.get("end_time") or latest.get("start_time"),
            "files_removed": _sum(items, "destination_files_removed"),
            "bytes_removed": _sum(items, "destination_bytes_removed"),
            "duration_seconds": round(sum(_num(item, "duration_seconds") or 0 for item in items), 3),
            "status": latest.get("status") or "unknown",
        })
    return sorted(result, key=lambda item: item["bytes_removed"], reverse=True)[:50]


def _maintenance_scatter(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "table": row.get("destination_table") or row.get("destination_path") or "unknown",
            "format": row.get("destination_format") or "unknown",
            "duration_seconds": _num(row, "duration_seconds") or 0,
            "files_removed": _num(row, "destination_files_removed") or 0,
            "bytes_removed": _num(row, "destination_bytes_removed") or 0,
            "status": _status(row),
        }
        for row in sorted(rows, key=lambda item: _num(item, "duration_seconds") or 0, reverse=True)[:500]
    ]


def _maintenance_bytes_by_date(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, float] = defaultdict(float)
    for row in rows:
        buckets[_run_date(row)] += _num(row, "destination_bytes_removed") or 0
    return [{"date": key, "bytes_reclaimed": value} for key, value in sorted(buckets.items())]


def _latest_freshness_by_dataflow(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    now = datetime.now(timezone.utc)
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if dataflow_id:
            buckets[dataflow_id].append(row)
    result = []
    for dataflow_id, items in buckets.items():
        latest = max(items, key=lambda item: _time_value(item.get("end_time") or item.get("start_time")))
        latest_at = latest.get("end_time") or latest.get("start_time")
        timestamp = parse_utc_datetime(latest_at)
        age_days = None
        age_seconds = None
        if timestamp is not None:
            age_seconds = max(0, int((now - timestamp.astimezone(timezone.utc)).total_seconds()))
            age_days = age_seconds / 86400
        result.append({
            "dataflow_name": latest.get("dataflow_name") or latest.get("dataflow_id") or "unknown",
            "dataflow_id": dataflow_id,
            "stage": latest.get("stage") or "unknown",
            "target": _target_identity(latest),
            "latest_freshness_at": latest_at,
            "latest_freshness_status": _status(latest),
            "age_days": age_days,
            "age_seconds": age_seconds,
            "source_name": latest.get("source_name") or latest.get("source_id") or "unknown",
            "destination_name": latest.get("destination_name") or latest.get("destination_id") or "unknown",
            "source_format": latest.get("source_format") or latest.get("source_connection_type") or "unknown",
            "destination_format": latest.get("destination_format") or latest.get("destination_connection_type") or "unknown",
            "source_watermark_effective": latest.get("source_watermark_effective"),
            "destination_load_type": latest.get("destination_load_type") or "unknown",
            "status": _status(latest),
        })
    return sorted(result, key=lambda item: _time_value(item["latest_freshness_at"]), reverse=True)


def _latest_rows_by_dataflow(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if not dataflow_id:
            continue
        current = result.get(dataflow_id)
        if current is None or _time_value(row.get("end_time") or row.get("start_time")) > _time_value(current.get("end_time") or current.get("start_time")):
            result[dataflow_id] = row
    return result


def _watermark_movement_row(row: dict[str, Any]) -> dict[str, Any]:
    watermark = _watermark_classification(row)
    movement = watermark["movement_state"]
    return {
        "dataflow_name": row.get("dataflow_name") or row.get("dataflow_id") or "unknown",
        "dataflow_id": _dataflow_id(row),
        "target": _target_identity(row),
        "end_time": row.get("end_time") or row.get("start_time"),
        "coverage_state": watermark["coverage_state"],
        "movement_state": movement,
        "adjustment_state": watermark["adjustment_state"],
        "movement": movement,
        "before": watermark["before"],
        "after": watermark["after"],
        "effective": watermark["effective"],
        "status": _status(row),
    }


def _stale_freshness_candidates(rows: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    return _stale_freshness_candidates_from_latest(_latest_freshness_by_dataflow(rows), days=days)


def _stale_freshness_candidates_from_latest(rows: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    result = []
    for item in rows:
        age_days = item.get("age_days")
        if age_days is None:
            continue
        age_value = max(0, float(age_days))
        if age_value > days:
            result.append({**item, "age_days": age_value})
    return sorted(result, key=lambda item: item["age_days"], reverse=True)[:50]


def _watermark_movement_by_date(
    rows: list[dict[str, Any]],
    trend_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    trend_context = trend_context or _trend_context({}, rows, timezone.utc)
    buckets: dict[str, Counter] = defaultdict(Counter)
    bucket_metadata: dict[str, dict[str, str | None]] = {}
    for row in rows:
        bucket = _date_bucket(row, trend_context)
        movement_row = _watermark_movement_row(row)
        buckets[bucket["bucket"]][movement_row["movement"]] += 1
        if movement_row.get("adjustment_state") == "adjusted":
            buckets[bucket["bucket"]]["adjusted"] += 1
        bucket_metadata[bucket["bucket"]] = bucket
    result = []
    for bucket_key, counts in buckets.items():
        advanced = int(counts.get("advanced", 0))
        initialized = int(counts.get("initialized", 0))
        unchanged = int(counts.get("unchanged", 0))
        incomplete = int(counts.get("incomplete", 0))
        invalid = int(counts.get("invalid", 0))
        unknown = int(counts.get("unknown", 0))
        adjusted = int(counts.get("adjusted", 0))
        comparable = advanced + unchanged
        bucket_info = bucket_metadata.get(bucket_key, {"bucket_start": None, "bucket_end": None})
        result.append({
            "date": bucket_key,
            "bucket": bucket_key,
            "bucket_start": bucket_info["bucket_start"],
            "bucket_end": bucket_info["bucket_end"],
            "grain": trend_context["effective_grain"],
            "advanced": advanced,
            "initialized": initialized,
            "unchanged": unchanged,
            "incomplete": incomplete,
            "invalid": invalid,
            "unknown": unknown,
            "adjusted": adjusted,
            "total": advanced + initialized + unchanged + incomplete + invalid + unknown,
            "advanced_rate": _rate(advanced, comparable) if comparable else None,
        })
    return sorted(result, key=lambda item: str(item["bucket_start"] or item["bucket"]))


def _freshness_age_distribution(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bucket_defs = [
        ("<24h", 0, 0),
        ("1-3d", 1, 3),
        ("3-7d", 4, 7),
        ("7-30d", 8, 30),
        (">30d", 31, None),
    ]
    counts = Counter({label: 0 for label, _, _ in bucket_defs})
    counts["unknown"] = 0
    for row in rows:
        age_days = row.get("age_days")
        if age_days is None:
            counts["unknown"] += 1
            continue
        age = int(age_days)
        for label, lower, upper in bucket_defs:
            if age >= lower and (upper is None or age <= upper):
                counts[label] += 1
                break
    order = [label for label, _, _ in bucket_defs] + ["unknown"]
    return [{"bucket": label, "dataflows": int(counts[label]), "targets": int(counts[label])} for label in order if counts[label] > 0]


def _watermark_coverage_by_stage(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"enabled": set(), "missing": set()})
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if not dataflow_id:
            continue
        stage = _dimension_value(row.get("stage"))
        key = "enabled" if _has_watermark(row) else "missing"
        buckets[stage][key].add(dataflow_id)
    result = []
    for stage, values in buckets.items():
        enabled = len(values["enabled"])
        missing = len(values["missing"] - values["enabled"])
        total = enabled + missing
        result.append({
            "stage": stage,
            "enabled": enabled,
            "missing": missing,
            "not_configured": missing,
            "total": total,
            "coverage_rate": _rate(enabled, total),
        })
    return sorted(result, key=lambda item: (-int(item["missing"]), -int(item["total"]), str(item["stage"])))[:50]


def _skipped_streak_distribution(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets = Counter()
    for row in rows:
        streak = int(row.get("consecutive_skipped") or 0)
        if streak <= 0:
            continue
        if streak == 1:
            bucket = "1"
        elif streak <= 3:
            bucket = "2-3"
        elif streak <= 7:
            bucket = "4-7"
        else:
            bucket = ">7"
        buckets[bucket] += 1
    order = ["1", "2-3", "4-7", ">7"]
    return [{"bucket": bucket, "dataflows": int(buckets[bucket]), "targets": int(buckets[bucket])} for bucket in order if buckets[bucket] > 0]


def _freshness_dataflow_registry(
    latest_freshness_rows: list[dict[str, Any]],
    movement_rows: list[dict[str, Any]],
    skipped_by_dataflow: dict[str, dict[str, Any]],
    etl_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    latest_movement: dict[str, dict[str, Any]] = {}
    for row in movement_rows:
        dataflow_id = str(row.get("dataflow_id") or "")
        if not dataflow_id:
            continue
        current = latest_movement.get(dataflow_id)
        if current is None or _time_value(row.get("end_time")) > _time_value(current.get("end_time")):
            latest_movement[dataflow_id] = row

    latest_any: dict[str, dict[str, Any]] = {}
    latest_success: dict[str, dict[str, Any]] = {}
    aggregates: dict[str, Counter] = defaultdict(Counter)
    latest_status_time: dict[str, dict[str, Any]] = defaultdict(dict)
    rows_by_dataflow: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in etl_rows:
        dataflow_id = _dataflow_id(row)
        if not dataflow_id:
            continue
        rows_by_dataflow[dataflow_id].append(row)
        status = _status(row)
        aggregates[dataflow_id]["runs"] += 1
        aggregates[dataflow_id][status] += 1
        row_time = row.get("end_time") or row.get("start_time")
        if status == "succeeded":
            current_success = latest_status_time[dataflow_id].get("last_success_at")
            if current_success is None or _time_value(row_time) > _time_value(current_success):
                latest_status_time[dataflow_id]["last_success_at"] = row_time
            current_success_row = latest_success.get(dataflow_id)
            if current_success_row is None or _time_value(row_time) > _time_value(current_success_row.get("end_time") or current_success_row.get("start_time")):
                latest_success[dataflow_id] = row
        if status == "failed":
            current_failed = latest_status_time[dataflow_id].get("last_failed_at")
            if current_failed is None or _time_value(row_time) > _time_value(current_failed):
                latest_status_time[dataflow_id]["last_failed_at"] = row_time
        current = latest_any.get(dataflow_id)
        if current is None or _time_value(row_time) > _time_value(current.get("end_time") or current.get("start_time")):
            latest_any[dataflow_id] = row

    result = []
    latest_freshness_by_dataflow = {str(row.get("dataflow_id") or ""): row for row in latest_freshness_rows if row.get("dataflow_id")}
    all_dataflows = set(latest_freshness_by_dataflow) | set(latest_movement) | set(skipped_by_dataflow) | set(latest_any)
    for dataflow_id in all_dataflows:
        fallback = latest_any.get(dataflow_id, {})
        row = latest_freshness_by_dataflow.get(dataflow_id) or {
            "dataflow_name": fallback.get("dataflow_name") or fallback.get("dataflow_id") or "unknown",
            "dataflow_id": dataflow_id,
            "stage": fallback.get("stage") or "unknown",
            "target": _target_identity(fallback) if fallback else "unknown",
            "latest_freshness_at": None,
            "latest_freshness_status": None,
            "age_days": None,
            "source_name": fallback.get("source_name") or fallback.get("source_id") or "unknown",
            "destination_name": fallback.get("destination_name") or fallback.get("destination_id") or "unknown",
            "source_format": fallback.get("source_format") or fallback.get("source_connection_type") or "unknown",
            "destination_format": fallback.get("destination_format") or fallback.get("destination_connection_type") or "unknown",
            "source_watermark_effective": fallback.get("source_watermark_effective"),
            "age_seconds": None,
            "destination_load_type": fallback.get("destination_load_type") or "unknown",
            "status": _status(fallback) if fallback else "unknown",
        }
        movement = latest_movement.get(dataflow_id, {})
        skipped = skipped_by_dataflow.get(dataflow_id, {})
        movement_state = movement.get("movement_state") or movement.get("movement")
        adjustment_state = movement.get("adjustment_state")
        counts = aggregates.get(dataflow_id, Counter())
        latest = latest_any.get(dataflow_id, {})
        latest_success_row = latest_success.get(dataflow_id, {})
        result.append({
            **_freshness_master_data(latest),
            **row,
            "coverage_state": movement.get("coverage_state") or ("configured" if row.get("source_watermark_effective") else "not_configured"),
            "movement_state": movement_state or ("not_configured" if not row.get("source_watermark_effective") else "incomplete"),
            "adjustment_state": adjustment_state or ("unknown" if row.get("source_watermark_effective") else "not_configured"),
            "watermark_time": movement.get("end_time"),
            "source_watermark_before": movement.get("before"),
            "source_watermark_after": movement.get("after"),
            "source_watermark_effective": movement.get("effective") or row.get("source_watermark_effective"),
            "latest_success_watermark": _latest_success_watermark_value(latest_success_row),
            "skipped_streak": int(skipped.get("consecutive_skipped") or 0),
            "latest_skip_time": skipped.get("latest_time"),
            "source_action": skipped.get("source_action"),
            "latest_run_at": latest.get("end_time") or latest.get("start_time"),
            "latest_run_status": _status(latest) if latest else "unknown",
            "latest_error_message": (latest.get("error_message") or latest.get("error_messages")) if latest else None,
            "run_count": int(counts.get("runs", 0)),
            "succeeded_count": int(counts.get("succeeded", 0)),
            "failed_count": int(counts.get("failed", 0)),
            "skipped_count": int(counts.get("skipped", 0)),
            "running_count": int(counts.get("running", 0)),
            "pending_count": int(counts.get("pending", 0)),
            "last_statuses": _last_dataflow_statuses(rows_by_dataflow.get(dataflow_id, []), limit=5),
            **latest_status_time.get(dataflow_id, {}),
        })
    return sorted(
        result,
        key=lambda item: _time_value(item.get("latest_freshness_at")),
        reverse=True,
    )


def _latest_success_watermark_value(row: dict[str, Any]) -> Any:
    if not row:
        return None
    for field in ("source_watermark_after", "source_watermark_effective", "source_watermark_before"):
        value = row.get(field)
        if not _is_missing_value(value):
            return value
    return None


def _last_dataflow_statuses(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda item: _time_value(item.get("end_time") or item.get("start_time")), reverse=True)
    return [
        {
            "status": _status(row),
            "time": row.get("end_time") or row.get("start_time"),
            "dataflow_run_id": row.get("dataflow_run_id"),
        }
        for row in ordered[:limit]
    ]


def _freshness_master_data(row: dict[str, Any]) -> dict[str, Any]:
    if not row:
        return {}
    fields = [
        "workspace_id",
        "dataflow_id",
        "dataflow_name",
        "dataflow_description",
        "stage",
        "group_number",
        "execution_order",
        "processing_mode",
        "is_active",
        "configure",
        "operation_type",
        "source_id",
        "source_name",
        "source_connection_type",
        "source_format",
        "source_catalog",
        "source_database",
        "source_schema",
        "source_table",
        "source_full_table",
        "source_path",
        "source_query",
        "source_python_function",
        "source_watermark_columns",
        "source_filter_expression",
        "source_configure",
        "source_action",
        "transform_deduplicate_columns",
        "transform_latest_data_columns",
        "transform_filter_expression",
        "transform_additional_columns",
        "transform_schema_hints",
        "transform_configure",
        "destination_id",
        "destination_name",
        "destination_connection_type",
        "destination_format",
        "destination_catalog",
        "destination_database",
        "destination_schema",
        "destination_table",
        "destination_full_table",
        "destination_path",
        "destination_load_type",
        "destination_merge_keys",
        "destination_partition_columns",
        "destination_configure",
    ]
    return {field: row.get(field) for field in fields if field in row}


def _consecutive_skipped_patterns(rows: list[dict[str, Any]], threshold: int = _SKIPPED_STREAK_RUNS) -> list[dict[str, Any]]:
    result = [
        row
        for row in _latest_skipped_streaks_by_dataflow(rows).values()
        if int(row.get("consecutive_skipped") or 0) >= threshold
    ]
    return sorted(result, key=lambda item: item["consecutive_skipped"], reverse=True)[:50]


def _latest_skipped_streaks_by_dataflow(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if dataflow_id:
            buckets[dataflow_id].append(row)
    result: dict[str, dict[str, Any]] = {}
    for dataflow_id, items in buckets.items():
        ordered = sorted(items, key=lambda item: _time_value(item.get("end_time") or item.get("start_time")), reverse=True)
        consecutive = 0
        for item in ordered:
            if _status(item) != "skipped":
                break
            consecutive += 1
        latest = ordered[0]
        result[dataflow_id] = {
            "dataflow_id": dataflow_id,
            "dataflow_name": latest.get("dataflow_name") or latest.get("dataflow_id") or dataflow_id,
            "target": _target_identity(latest),
            "consecutive_skipped": consecutive,
            "threshold": _SKIPPED_STREAK_RUNS,
            "latest_time": latest.get("end_time") or latest.get("start_time"),
            "source_action": latest.get("source_action") or "unknown",
        }
    return result


def _has_watermark(row: dict[str, Any]) -> bool:
    return any(
        row.get(key)
        for key in (
            "source_watermark_columns",
            "source_watermark_before",
            "source_watermark_after",
            "source_watermark_effective",
        )
    )


def _target_identity(row: dict[str, Any]) -> str:
    schema = row.get("destination_schema") or row.get("destination_schema_name")
    return str(
        row.get("destination_full_table")
        or ".".join(
            part
            for part in (
                row.get("destination_catalog"),
                row.get("destination_database"),
                schema,
                row.get("destination_table"),
            )
            if part
        )
        or row.get("destination_path")
        or row.get("destination_table")
        or row.get("dataflow_name")
        or row.get("dataflow_id")
        or "unknown"
    )


def _normalized_json_string(value: Any) -> str | None:
    if _is_missing_value(value):
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    text = str(value)
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return text
    return json.dumps(parsed, sort_keys=True, ensure_ascii=False)


def _top_failing_dataflows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    phases = ("source", "transform", "destination", "overhead", "unknown")
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if dataflow_id:
            buckets[dataflow_id].append(row)
    result = []
    for dataflow_id, items in buckets.items():
        latest = max(items, key=lambda item: _time_value(item.get("end_time") or item.get("start_time")))
        affected_job_ids = {
            str(item.get("job_id"))
            for item in items
            if item.get("job_id") not in (None, "", "unknown")
        }
        phase_counts = Counter(_failure_phase_value(item, phases) for item in items)
        result.append({
            "dataflow_id": dataflow_id,
            "dataflow_name": latest.get("dataflow_name") or dataflow_id,
            "error_count": len(items),
            **{phase: int(phase_counts.get(phase, 0)) for phase in phases},
            "affected_job_count": len(affected_job_ids),
            "last_error": latest.get("error_message") or latest.get("destination_error_message") or latest.get("source_error_message"),
            "last_time": latest.get("end_time") or latest.get("start_time"),
            "stage": latest.get("stage") or "unknown",
            "engine_name": latest.get("engine_name") or "unknown",
        })
    result.sort(key=lambda item: str(item.get("dataflow_name") or "unknown"))
    result.sort(key=lambda item: int(item.get("affected_job_count") or 0), reverse=True)
    result.sort(key=lambda item: _time_value(item.get("last_time")), reverse=True)
    result.sort(key=lambda item: int(item.get("error_count") or 0), reverse=True)
    return result[:30]


def _error_categories(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for row in rows:
        category = row.get("failure_category")
        if category:
            counts[str(category)] += 1
            continue
        message = (
            row.get("source_error_message")
            or row.get("transform_error_message")
            or row.get("destination_error_message")
            or row.get("error_messages")
            or row.get("error_message")
            or ""
        )
        counts[_error_category(str(message))] += 1
    return _counter_rows(counts, "category")


def _failure_trend(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        if _status(row) != "failed":
            continue
        date_key = _run_date(row)
        kind = str(row.get("failure_kind") or "dataflow")
        if kind == "job":
            buckets[date_key]["failed_jobs"] += 1
        else:
            buckets[date_key]["failed_dataflows"] += 1
    return [
        {
            "date": key,
            "failed": values.get("failed_jobs", 0) + values.get("failed_dataflows", 0),
            "failed_jobs": values.get("failed_jobs", 0),
            "failed_dataflows": values.get("failed_dataflows", 0),
        }
        for key, values in sorted(buckets.items())
    ]


def _status_by_date(rows: list[dict[str, Any]], trend_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
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
        bucket_info = bucket_metadata.get(bucket_key, {"bucket_start": None, "bucket_end": None})
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
            "success_rate": _rate(succeeded, executable_total) if executable_total else None,
            "failure_rate": _rate(failed, executable_total) if executable_total else None,
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
            -sum(int(item.get(status, 0)) for status in ("succeeded", "failed", "skipped", "running", "pending", "unknown")),
            str(item["stage"]),
        ),
    )


def _job_status_by_stage(rows: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_job_by_id: dict[str, dict[str, Any]] = {}
    for job in jobs:
        job_id = str(job.get("job_id") or "")
        if not job_id:
            continue
        existing = latest_job_by_id.get(job_id)
        if existing is None or _time_value(job.get("end_time") or job.get("start_time")) > _time_value(
            existing.get("end_time") or existing.get("start_time")
        ):
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
            -sum(int(item.get(status, 0)) for status in ("succeeded", "failed", "skipped", "running", "pending", "unknown")),
            str(item["stage"]),
        ),
    )


def _top_counter(rows: list[dict[str, Any]], key: str, limit: int = 20) -> list[dict[str, Any]]:
    counts = Counter(str(row.get(key) or "unknown") for row in rows)
    return [{"name": name, "count": count} for name, count in counts.most_common(limit)]


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
        ({"operation_type": operation_type, **dict(values)} for operation_type, values in buckets.items()),
        key=lambda item: (-int(item["count"]), str(item["operation_type"])),
    )


def _job_runs_by_dataflow_operation_type(_rows: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, Counter] = defaultdict(Counter)
    for job in jobs:
        operation_types = _listish_values(job.get("operation_types")) or ["unknown"]
        for operation_type in operation_types:
            operation_type = _dimension_value(operation_type)
            bucket = buckets[operation_type]
            bucket["count"] += 1
            status = _status(job)
            bucket[status] += 1
            if status == "failed":
                bucket["failed_count"] += 1

    return sorted(
        ({"operation_type": operation_type, **dict(values)} for operation_type, values in buckets.items()),
        key=lambda item: (-int(item["count"]), str(item["operation_type"])),
    )

def _dataflow_operation_type(row: dict[str, Any]) -> str:
    return _dimension_value(row.get("operation_type"))


def _dataflow_id(row: dict[str, Any]) -> str:
    value = row.get("dataflow_id")
    if value in (None, ""):
        return ""
    text = str(value).strip()
    return "" if not text or text.lower() in {"none", "null", "nan", "unknown"} else text


def _normalized_job_shape(job: dict[str, Any]) -> dict[str, list[str]]:
    stages = [_dimension_value(value) for value in _listish_values(job.get("stages"))]
    operation_types = [_dimension_value(value) for value in _listish_values(job.get("operation_types"))]
    return {
        "operation_types": sorted(set(operation_types or ["unknown"])),
        "stages": stages or ["unknown"],
    }


def _job_key(job: dict[str, Any]) -> str:
    payload = json.dumps(_normalized_job_shape(job), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _job_shape_label(job: dict[str, Any]) -> str:
    shape = _normalized_job_shape(job)
    return f"{', '.join(shape['operation_types'])} | {', '.join(shape['stages'])}"


def _dimension_value(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    if not text or text.lower() in {"none", "null", "nan", "not_available", "not available"}:
        return "unknown"
    return text


def _has_value(value: Any) -> bool:
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return value not in (None, "")


def _listish_values(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if not _has_value(value):
        return []
    text_value = str(value).strip()
    if not text_value:
        return []
    if text_value.startswith("[") and text_value.endswith("]"):
        try:
            parsed = json.loads(text_value)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [item.strip() for item in text_value.split(",") if item.strip()]


def _job_operation_types_value(job: dict[str, Any]) -> str:
    return ", ".join(_listish_values(job.get("operation_types"))) or "unknown"


def _destination_operation_type(row: dict[str, Any]) -> str:
    return _dimension_value(row.get("destination_operation_type"))


def _phase_health_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _phase_duration_summary(
        rows,
        group_key="operation_type",
        resolve_group=_dataflow_operation_type,
    )


def _phase_health_by_stage(rows: list[dict[str, Any]], limit: int = 100) -> list[dict[str, Any]]:
    return _phase_duration_summary(
        rows,
        group_key="stage",
        resolve_group=lambda row: str(row.get("stage") or "unknown"),
        limit=limit,
    )


def _phase_duration_summary(
    rows: list[dict[str, Any]],
    group_key: str,
    resolve_group,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    phase_definitions = [
        ("source", "source_status", "source_duration_seconds"),
        ("transform", "transform_status", "transform_duration_seconds"),
        ("destination", "destination_status", "destination_duration_seconds"),
        ("overhead", None, "overhead_duration_seconds"),
    ]
    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "statuses": {phase: Counter() for phase, _, _ in phase_definitions},
        "durations": {phase: [] for phase, _, _ in phase_definitions},
    })
    total_bucket = {
        "statuses": {phase: Counter() for phase, _, _ in phase_definitions},
        "durations": {phase: [] for phase, _, _ in phase_definitions},
    }

    def add_run(bucket: dict[str, Any], row: dict[str, Any]) -> None:
        failed_phases = set(dataflow_failed_phases(row))
        for phase, status_key, duration_key in phase_definitions:
            if status_key and row.get(status_key):
                phase_status = _status({**row, "status": row.get(status_key)})
                bucket["statuses"][phase][phase_status] += 1
            duration = _phase_duration(row, phase, duration_key)
            if duration is not None:
                bucket["durations"][phase].append(duration)
        if "overhead" in failed_phases:
            bucket["statuses"]["overhead"]["failed"] += 1

    for row in rows:
        if _status(row) not in {"succeeded", "failed", "skipped"}:
            continue
        group_value = resolve_group(row)
        add_run(buckets[group_value], row)
        add_run(total_bucket, row)

    def summary_row(group_value: str, bucket: dict[str, Any], is_total: bool = False) -> dict[str, Any] | None:
        phase_durations = bucket["durations"]
        total_duration = sum(sum(values) for values in phase_durations.values())
        if total_duration <= 0:
            return None
        row: dict[str, Any] = {
            group_key: group_value,
            "group": group_value,
            "total_duration_seconds": round(total_duration, 3),
        }
        if is_total:
            row["is_total"] = 1
        for phase, _, _ in phase_definitions:
            statuses = bucket["statuses"][phase]
            durations = phase_durations[phase]
            duration = sum(durations)
            row[f"{phase}_duration_seconds"] = round(duration, 3)
            row[f"{phase}_duration_percent"] = _rate(duration, total_duration)
            row[f"{phase}_run_count"] = len(durations)
            row[f"{phase}_avg_duration_seconds"] = _avg(durations)
            row[f"{phase}_p95_duration_seconds"] = _percentile_clean(durations, 0.95)
            row[f"{phase}_succeeded"] = statuses.get("succeeded", 0)
            row[f"{phase}_failed"] = statuses.get("failed", 0)
            row[f"{phase}_skipped"] = statuses.get("skipped", 0)
            row[f"{phase}_running"] = statuses.get("running", 0)
            row[f"{phase}_pending"] = statuses.get("pending", 0)
            row[f"{phase}_unknown"] = statuses.get("unknown", 0)
        return row

    result = [
        row
        for group_value, bucket in buckets.items()
        if (row := summary_row(group_value, bucket)) is not None
    ]
    sorted_result = sorted(result, key=lambda item: (-float(item["total_duration_seconds"]), str(item["group"])))
    if limit is not None:
        sorted_result = sorted_result[:limit]
    total = summary_row("Total", total_bucket, is_total=True)
    return ([total] if total is not None else []) + sorted_result


def _phase_duration(row: dict[str, Any], phase: str, duration_key: str) -> float | None:
    if phase != "overhead":
        return _num(row, duration_key)
    total_duration = _num(row, "duration_seconds")
    if total_duration is None:
        return None
    known_duration = sum(
        _num(row, known_key) or 0
        for known_key in ("source_duration_seconds", "transform_duration_seconds", "destination_duration_seconds")
    )
    return max(0.0, total_duration - known_duration)


def _dataflow_name_status_health(rows: list[dict[str, Any]], limit: int = 40) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if dataflow_id:
            buckets[dataflow_id].append(row)

    result: list[dict[str, Any]] = []
    for dataflow_id, items in buckets.items():
        statuses = Counter(_status(item) for item in items)
        executable = statuses.get("succeeded", 0) + statuses.get("failed", 0)
        durations = [_num(item, "duration_seconds") for item in items if _num(item, "duration_seconds") is not None]
        latest = max(items, key=lambda item: _row_timestamp(item) or datetime.min)
        rows_read = _sum(items, "source_rows_read")
        rows_written = _sum(items, "destination_rows_written")
        result.append({
            "dataflow_name": latest.get("dataflow_name") or dataflow_id,
            "dataflow_id": dataflow_id,
            "stage": latest.get("stage") or "unknown",
            "operation_type": _dominant_value(items, "operation_type") if any(item.get("operation_type") for item in items) else _dominant_dataflow_operation_type(items),
            "source_name": latest.get("source_name") or latest.get("source_connection_name"),
            "destination_name": latest.get("destination_name") or latest.get("destination_connection_name"),
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
        })
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


def _dominant_dataflow_operation_type(rows: list[dict[str, Any]]) -> str:
    values = Counter(_dataflow_operation_type(row) for row in rows)
    return values.most_common(1)[0][0] if values else "unknown"


def _dataflow_endpoint_health(rows: list[dict[str, Any]], limit: int = 18) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        source = str(row.get("source_name") or row.get("source_connection_name") or "unknown")
        destination = str(row.get("destination_name") or row.get("destination_connection_name") or "unknown")
        buckets[(source, destination)].append(row)

    result: list[dict[str, Any]] = []
    for (source, destination), items in buckets.items():
        statuses = Counter(_status(item) for item in items)
        executable = statuses.get("succeeded", 0) + statuses.get("failed", 0)
        durations = [_num(item, "duration_seconds") for item in items if _num(item, "duration_seconds") is not None]
        result.append({
            "source_name": source,
            "destination_name": destination,
            "source_format": _dominant_value(items, "source_format"),
            "destination_format": _dominant_value(items, "destination_format"),
            "source_connection_type": _dominant_value(items, "source_connection_type"),
            "destination_connection_type": _dominant_value(items, "destination_connection_type"),
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
        })
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


def _dataflow_watermark_summary(rows: list[dict[str, Any]], limit: int = 18) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if dataflow_id:
            buckets[dataflow_id].append(row)

    result: list[dict[str, Any]] = []
    for dataflow_id, items in buckets.items():
        latest = max(items, key=lambda item: _time_value(item.get("end_time") or item.get("start_time")))
        signals = [_watermark_classification(item) for item in items]
        statuses = Counter(signal["movement_state"] for signal in signals)
        adjustments = Counter(signal["adjustment_state"] for signal in signals)
        run_statuses = Counter(_status(item) for item in items)
        not_configured = statuses.get("not_configured", 0) + statuses.get("missing", 0)
        result.append({
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
        })
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


def _dominant_value(rows: list[dict[str, Any]], key: str) -> str:
    values = Counter(_dimension_value(row.get(key)) for row in rows)
    return values.most_common(1)[0][0] if values else "unknown"


def _top_sum(rows: list[dict[str, Any]], group_key: str, value_key: str, limit: int = 20) -> list[dict[str, Any]]:
    buckets: dict[str, float] = defaultdict(float)
    counts: Counter[str] = Counter()
    for row in rows:
        key = str(row.get(group_key) or "unknown")
        buckets[key] += _num(row, value_key) or 0
        counts[key] += 1
    return [
        {"name": key, "value": round(value, 3), "count": counts[key]}
        for key, value in sorted(buckets.items(), key=lambda item: item[1], reverse=True)[:limit]
    ]


def _top_dataflow_sum(rows: list[dict[str, Any]], value_key: str, limit: int = 20) -> list[dict[str, Any]]:
    buckets: dict[str, float] = defaultdict(float)
    counts: Counter[str] = Counter()
    labels: dict[str, str] = {}
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if not dataflow_id:
            continue
        labels.setdefault(dataflow_id, str(row.get("dataflow_name") or dataflow_id))
        buckets[dataflow_id] += _num(row, value_key) or 0
        counts[dataflow_id] += 1
    return [
        {
            "dataflow_id": dataflow_id,
            "name": labels.get(dataflow_id, dataflow_id),
            "value": round(value, 3),
            "count": counts[dataflow_id],
        }
        for dataflow_id, value in sorted(buckets.items(), key=lambda item: item[1], reverse=True)[:limit]
    ]


def _counter_rows(counts: Counter, key_name: str) -> list[dict[str, Any]]:
    return [{key_name: key, "count": value} for key, value in counts.most_common()]


def _date_range(rows: list[dict[str, Any]]) -> dict[str, str | None]:
    dates = sorted({_run_date(row) for row in rows if _run_date(row) != "unknown"})
    return {"min": dates[0] if dates else None, "max": dates[-1] if dates else None}


def _trend_context(filters: dict[str, str], rows: list[dict[str, Any]], timezone_info: tzinfo) -> dict[str, Any]:
    requested_grain = str(filters.get("grain") or "auto").strip().lower()
    if requested_grain not in {"auto", *_DATE_GRAINS}:
        requested_grain = "auto"
    start, end = _trend_bounds(filters, rows, timezone_info)
    effective_grain = _resolve_effective_grain(requested_grain, start, end)
    return {
        "requested_grain": requested_grain,
        "effective_grain": effective_grain,
        "timezone_info": timezone_info,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
    }


def _trend_bounds(filters: dict[str, str], rows: list[dict[str, Any]], timezone_info: tzinfo) -> tuple[datetime | None, datetime | None]:
    now = datetime.now(timezone.utc).astimezone(timezone_info)
    range_value = str(filters.get("range") or "30d").strip().lower()
    if range_value in {"24h", "3d", "7d", "30d", "90d"}:
        days = {"24h": 1, "3d": 3, "7d": 7, "30d": 30, "90d": 90}[range_value]
        return now - timedelta(days=days), now
    if range_value == "custom":
        start = parse_utc_datetime(filters.get("startTime"))
        end = parse_utc_datetime(filters.get("endTime"))
        if start is not None:
            start = start.astimezone(timezone_info)
        if end is not None:
            end = end.astimezone(timezone_info)
        if start is not None or end is not None:
            return start, end

    timestamps = [_ensure_aware(timestamp).astimezone(timezone_info) for row in rows if (timestamp := _row_timestamp(row)) is not None]
    if timestamps:
        return min(timestamps), max(timestamps)
    return now - timedelta(days=30), now


def _resolve_effective_grain(requested_grain: str, start: datetime | None, end: datetime | None) -> str:
    if start is None or end is None:
        return "day" if requested_grain == "auto" else requested_grain
    if end < start:
        start, end = end, start
    minimum_grain = _minimum_allowed_grain(start, end)
    if requested_grain == "auto":
        return minimum_grain
    if requested_grain not in _DATE_GRAINS:
        return minimum_grain
    requested_index = _DATE_GRAINS.index(requested_grain)
    minimum_index = _DATE_GRAINS.index(minimum_grain)
    return _DATE_GRAINS[max(requested_index, minimum_index)]


def _minimum_allowed_grain(start: datetime, end: datetime) -> str:
    if end < start:
        start, end = end, start
    span_seconds = max(0, (end - start).total_seconds())
    if span_seconds <= 3 * 24 * 3600:
        return "hour"
    if span_seconds <= 90 * 86400:
        return "day"
    if span_seconds <= 365 * 86400:
        return "week"
    return "month"


def _estimated_bucket_count(start: datetime, end: datetime, grain: str) -> int:
    if end < start:
        start, end = end, start
    if grain == "hour":
        return int((end - start).total_seconds() // 3600) + 1
    if grain == "day":
        return (end.date() - start.date()).days + 1
    if grain == "week":
        return int((end.date() - start.date()).days // 7) + 1
    if grain == "month":
        return (end.year - start.year) * 12 + end.month - start.month + 1
    return 1


def _coarser_grain(grain: str) -> str:
    return {"hour": "day", "day": "week", "week": "month"}.get(grain, "month")


def _finer_grain(grain: str) -> str:
    return {"month": "week", "week": "day", "day": "hour"}.get(grain, "hour")


def _date_bucket(row: dict[str, Any], trend_context: dict[str, Any]) -> dict[str, str | None]:
    timestamp = _row_timestamp(row)
    if timestamp is None:
        return {"bucket": "unknown", "bucket_start": None, "bucket_end": None}
    timezone_info = trend_context.get("timezone_info") or timezone.utc
    local_time = _ensure_aware(timestamp).astimezone(timezone_info)
    grain = str(trend_context.get("effective_grain") or "day")
    if grain == "hour":
        start = local_time.replace(minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)
        bucket = start.strftime("%Y-%m-%d %H:00")
    elif grain == "week":
        start = (local_time - timedelta(days=local_time.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
        iso_year, iso_week, _ = start.isocalendar()
        bucket = f"{iso_year}-W{iso_week:02d}"
    elif grain == "month":
        start = local_time.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
        bucket = start.strftime("%Y-%m")
    else:
        start = local_time.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        bucket = start.strftime("%Y-%m-%d")
    return {"bucket": bucket, "bucket_start": start.isoformat(), "bucket_end": end.isoformat()}


def _ensure_aware(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp


def _run_date(row: dict[str, Any]) -> str:
    value = row.get("__run_date")
    if value:
        return str(value)[:10]
    timestamp = _row_timestamp(row)
    if timestamp:
        return timestamp.date().isoformat()
    return "unknown"


def _latest_log_at(rows: list[dict[str, Any]]) -> str | None:
    timestamps = [timestamp for row in rows if (timestamp := _row_timestamp(row)) is not None]
    return max(timestamps).isoformat() if timestamps else None


def _row_timestamp(row: dict[str, Any]) -> datetime | None:
    for key in ("end_time", "start_time"):
        timestamp = parse_utc_datetime(row.get(key))
        if timestamp is not None:
            return timestamp
    return None


def _error_category(message: str) -> str:
    return categorize_failure(message)


def _status(row: dict[str, Any]) -> str:
    return str(row.get("status") or "unknown")


def _dominant_status(statuses: Counter) -> str:
    for status in ("failed", "running", "pending", "succeeded", "skipped"):
        if statuses.get(status, 0):
            return status
    return "unknown"


def _sum(rows: list[dict[str, Any]], key: str) -> float:
    return round(sum(_num(row, key) or 0 for row in rows), 3)


def _avg(values: list[float | None]) -> float:
    clean = [value for value in values if value is not None]
    return round(sum(clean) / len(clean), 3) if clean else 0


def _rate(part: int | float, whole: int | float) -> float:
    return round((part / whole) * 100, 2) if whole else 0


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


def _operation_window_summary(jobs: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    job_status = Counter(_status(row) for row in jobs)
    dataflow_status = Counter(_status(row) for row in rows)
    executable_jobs = job_status.get("succeeded", 0) + job_status.get("failed", 0)
    executable_dataflows = dataflow_status.get("succeeded", 0) + dataflow_status.get("failed", 0)
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
        "dataflow_success_rate": _rate(dataflow_status.get("succeeded", 0), executable_dataflows),
        "dataflow_failure_rate": _rate(dataflow_status.get("failed", 0), executable_dataflows),
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


def _duration_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [_num(row, "duration_seconds") for row in rows]
    clean = [value for value in values if value is not None]
    return {
        "count": len(clean),
        "avg_duration_seconds": _avg(values),
        "q1_duration_seconds": _percentile(clean, 0.25),
        "p50_duration_seconds": _percentile(clean, 0.50),
        "q3_duration_seconds": _percentile(clean, 0.75),
        "p95_duration_seconds": _percentile(clean, 0.95),
        "p99_duration_seconds": _percentile(clean, 0.99),
        "max_duration_seconds": round(max(clean), 3) if clean else 0,
    }


def _percentile_clean(values: list[float | None], percentile: float) -> float:
    return _percentile([value for value in values if value is not None], percentile)


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


def _maintenance_count_in_window(rows: list[dict[str, Any]], days: int, statuses: set[str]) -> int:
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
    destination_operation_type = str(row.get("destination_operation_type") or "").strip().lower()
    return operation_type == "maintenance" or destination_operation_type in {"compact", "cleanup", "maintenance"}


def _age_days(value: str | None) -> int | None:
    timestamp = parse_utc_datetime(value)
    if timestamp is None:
        return None
    return max(0, int((datetime.now(timezone.utc) - timestamp).total_seconds() // 86400))


def _max_status(current: str, candidate: str) -> str:
    order = {"healthy": 0, "no_log_evidence": 1, "warning": 2, "has_issues": 3}
    return candidate if order.get(candidate, 0) > order.get(current, 0) else current


def _first(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None


def _format_seconds(value: float) -> str:
    if value < 60:
        return f"{value:.2f}s"
    return f"{value / 60:.2f}m"


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return round(ordered[index], 3)


def _num(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _time_value(value: object) -> datetime:
    return parse_utc_datetime(value) or datetime.min.replace(tzinfo=timezone.utc)
