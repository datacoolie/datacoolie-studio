from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring import service as metrics
from datacoolie_studio.domains.monitoring.diagnostics_repository import diagnostics_read_model
from datacoolie_studio.domains.monitoring.read_models.dataflows import dataflows_read_model
from datacoolie_studio.domains.monitoring.read_models.failures import failures_read_model
from datacoolie_studio.domains.monitoring.read_models.jobs import jobs_read_model
from datacoolie_studio.domains.monitoring.read_models.overview import overview_read_model
from datacoolie_studio.domains.monitoring.read_models.volume import (
    volume_evidence_read_model,
    volume_read_model,
)
from datacoolie_studio.domains.monitoring.read_models.freshness import (
    freshness_evidence_read_model,
    freshness_read_model,
)
from datacoolie_studio.domains.monitoring.read_models.performance import (
    performance_evidence_read_model,
    performance_read_model,
)
from datacoolie_studio.domains.monitoring.read_models.maintenance import (
    maintenance_evidence_read_model,
    maintenance_read_model,
)
from datacoolie_studio.domains.monitoring.metrics.health import environment_health
from datacoolie_studio.domains.monitoring.repository import environment_overview_read_model
from datacoolie_studio.domains.read_models.cache import fingerprint
from datacoolie_studio.domains.read_models.contracts import ResultCacheKey, get_or_compute
from datacoolie_studio.domains.read_models.keys import monitoring_page as monitoring_page_key
from datacoolie_studio.domains.read_models.provider import result_cache_provider


MONITORING_PAGES = {
    "environment-overview", "overview", "jobs", "dataflows", "failures",
    "diagnostics", "performance", "volume", "maintenance", "freshness",
}

_PRODUCER_VERSION = "monitoring-page-sql-v9"
MONITORING_PAGE_RESPONSE_VERSION = "monitoring-page.v9"
_PUBLIC_PAGE_SECTIONS = {
    "overview": ("health", "attention", "operations", "failures", "volume"),
    "jobs": ("operations", "reconciliation"),
    "dataflows": ("operations", "volume"),
    "failures": ("operations", "failures"),
    "freshness": ("freshness",),
    "performance": ("performance",),
    "volume": ("volume",),
    "maintenance": ("maintenance",),
    "diagnostics": ("coverage", "reconciliation", "diagnostics"),
}
_MULTI_VALUE_FILTERS = {
    "status", "stage", "connection", "engine", "provider", "sourceType",
    "destinationType", "loadType", "operationType",
}
_PERFORMANCE_EVIDENCE_SORT_FIELDS = {
    "job_id", "dataflow_name", "stage", "performance_bottleneck_phase",
    "duration_seconds", "start_time", "end_time", "status",
    "performance_candidate_priority", "performance_candidate_reason",
}


def monitoring_page(
    paths: list[EnvironmentSource],
    page: str,
    filters: dict[str, str] | None = None,
    session: Session | None = None,
    timezone_info: tzinfo | None = None,
    timezone_label: str | None = None,
    timezone_source: str = "server_default",
    environment_id: int | None = None,
    cache_key: ResultCacheKey | None = None,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    resolved_environment_id = environment_id or (getattr(paths[0], "environment_id", None) if paths else None)
    if session is not None and resolved_environment_id is not None:
        key = cache_key or monitoring_page_cache_key(
            session,
            environment_id=resolved_environment_id,
            paths=paths,
            page=page,
            filters=filters or {},
            timezone_label=timezone_label or "UTC",
        )
        provider = result_cache_provider()
        result, _ = get_or_compute(
            provider.store,
            provider.coordinator,
            key,
            lambda: _build_monitoring_page(
                paths,
                page,
                filters,
                session,
                timezone_info,
                timezone_label,
                timezone_source,
                analytics_context,
            ),
        )
        return result.payload
    return _build_monitoring_page(
        paths,
        page,
        filters,
        session,
        timezone_info,
        timezone_label,
        timezone_source,
        analytics_context,
    )


def monitoring_page_cache_key(
    session: Session,
    *,
    environment_id: int,
    paths: list[EnvironmentSource],
    page: str,
    filters: dict[str, str],
    timezone_label: str,
    now: datetime | None = None,
    input_fingerprint: str | None = None,
) -> ResultCacheKey:
    if page not in MONITORING_PAGES:
        raise ValueError(f"Unknown Monitoring page: {page}")
    parameters = _canonical_parameters(page, filters, timezone_label, now=now)
    return ResultCacheKey(
        environment_id=environment_id,
        namespace=monitoring_page_key(page),
        parameters_fingerprint=fingerprint(parameters),
        input_fingerprint=input_fingerprint or metrics.monitoring_input_fingerprint(session, paths),
        producer_version=_PRODUCER_VERSION,
    )


def monitoring_page_etag(key: ResultCacheKey) -> str:
    return f'"{fingerprint([*key.identity, MONITORING_PAGE_RESPONSE_VERSION])}"'


def public_monitoring_page(page: str, payload: dict[str, Any]) -> dict[str, Any]:
    if page == "environment-overview":
        return {
            "schema_version": MONITORING_PAGE_RESPONSE_VERSION,
            "page": page,
            **{key: value for key, value in payload.items() if key != "metric_definitions"},
        }
    sections = _PUBLIC_PAGE_SECTIONS.get(page)
    if sections is None:
        raise ValueError(f"Unknown public Monitoring page: {page}")
    projected: dict[str, Any] = {
        "schema_version": MONITORING_PAGE_RESPONSE_VERSION,
        "page": page,
        "summary": payload.get("summary") or {},
    }
    for section in sections:
        value = payload.get(section)
        if section == "diagnostics":
            value = dict(value or {})
            value.pop("job_id_evidence", None)
        projected[section] = value
    if payload.get("errors"):
        projected["errors"] = payload["errors"]
    return projected


def monitoring_page_evidence(
    paths: list[EnvironmentSource],
    page: str,
    *,
    filters: dict[str, str],
    session: Session,
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    environment_id: int,
    limit: int,
    offset: int,
    sort_by: str,
    sort_dir: str,
) -> dict[str, Any]:
    safe_sort_dir = sort_dir if sort_dir in {"asc", "desc"} else "desc"
    normalized_filters = metrics._normalize_monitoring_filters_for_timezone(
        filters, timezone_info=timezone_info,
    )
    readers = {
        "performance": performance_evidence_read_model,
        "freshness": freshness_evidence_read_model,
        "volume": volume_evidence_read_model,
        "maintenance": maintenance_evidence_read_model,
    }
    reader = readers.get(page)
    if reader is None:
        raise ValueError(f"Monitoring evidence is not defined for page: {page}")
    safe_sort_by = (
        sort_by
        if page != "performance" or sort_by in _PERFORMANCE_EVIDENCE_SORT_FIELDS
        else "performance_candidate_priority"
    )
    result = reader(
        paths,
        normalized_filters,
        limit=limit,
        offset=offset,
        sort_by=safe_sort_by,
        sort_dir=safe_sort_dir,
    )
    records = result["records"]
    return {
        "records": records,
        "errors": [],
        "summary": {
            "records": len(records),
            "total_records": result["total_records"],
            "limit": limit,
            "offset": offset,
            "cache": "duckdb",
        },
    }


def _canonical_parameters(
    page: str,
    filters: dict[str, str],
    timezone_label: str,
    *,
    now: datetime | None,
) -> dict[str, Any]:
    canonical_filters: dict[str, str] = {}
    for name, raw_value in sorted(filters.items()):
        value = str(raw_value or "").strip()
        if name in _MULTI_VALUE_FILTERS and value:
            value = "|".join(sorted({item.strip() for item in value.split("|") if item.strip()}))
        canonical_filters[name] = value
    range_value = canonical_filters.get("range", "").lower()
    anchor = None
    if page == "freshness" or range_value in {"24h", "3d", "7d", "30d", "90d", "today"}:
        try:
            anchor_timezone = ZoneInfo(timezone_label)
        except ZoneInfoNotFoundError:
            anchor_timezone = timezone.utc
        current = (now or datetime.now(timezone.utc)).astimezone(anchor_timezone)
        anchor = (
            current.replace(second=0, microsecond=0).isoformat()
            if page == "freshness"
            else current.replace(minute=0, second=0, microsecond=0).isoformat()
            if range_value == "24h"
            else current.date().isoformat()
        )
    return {
        "page": page,
        "filters": canonical_filters,
        "timezone": timezone_label,
        "window_anchor": anchor,
    }


def _build_monitoring_page(
    paths: list[EnvironmentSource],
    page: str,
    filters: dict[str, str] | None = None,
    session: Session | None = None,
    timezone_info: tzinfo | None = None,
    timezone_label: str | None = None,
    timezone_source: str = "server_default",
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    """Build only the read model required by one Monitoring page."""
    if page not in MONITORING_PAGES:
        raise ValueError(f"Unknown Monitoring page: {page}")
    active_timezone = timezone_info or timezone.utc
    active_timezone_label = timezone_label or "UTC"
    normalized_filters = metrics._normalize_monitoring_filters_for_timezone(
        filters or {}, timezone_info=active_timezone,
    )
    if page == "environment-overview":
        return _build_environment_overview_page(
            paths,
            normalized_filters,
            active_timezone,
            active_timezone_label,
            timezone_source,
            analytics_context,
        )
    if page == "diagnostics":
        return _build_diagnostics_page(
            paths,
            normalized_filters,
            active_timezone,
            active_timezone_label,
            timezone_source,
            analytics_context,
        )
    if page == "overview":
        return _build_overview_page(
            paths,
            normalized_filters,
            active_timezone,
            active_timezone_label,
            timezone_source,
            analytics_context,
        )
    if page == "dataflows":
        return _build_dataflows_page(
            paths,
            normalized_filters,
            active_timezone,
            active_timezone_label,
            timezone_source,
            analytics_context,
        )
    if page == "jobs":
        return _build_jobs_page(
            paths,
            normalized_filters,
            active_timezone,
            active_timezone_label,
            timezone_source,
            analytics_context,
        )
    if page == "failures":
        return _build_failures_page(
            paths,
            normalized_filters,
            active_timezone,
            active_timezone_label,
            timezone_source,
            analytics_context,
        )
    if page == "volume":
        return _build_volume_page(
            paths,
            normalized_filters,
            active_timezone,
            active_timezone_label,
            timezone_source,
            analytics_context,
        )
    if page == "freshness":
        return _build_freshness_page(
            paths,
            normalized_filters,
            active_timezone,
            active_timezone_label,
            timezone_source,
            analytics_context,
        )
    if page == "performance":
        return _build_performance_page(
            paths,
            normalized_filters,
            active_timezone,
            active_timezone_label,
            timezone_source,
            analytics_context,
        )
    if page == "maintenance":
        return _build_maintenance_page(
            paths,
            normalized_filters,
            active_timezone,
            active_timezone_label,
            timezone_source,
            analytics_context,
        )
    raise AssertionError(f"Monitoring page is not routed to a SQL read model: {page}")


def _build_environment_overview_page(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    trend_context = metrics._trend_context(filters, [], timezone_info)
    aggregate = environment_overview_read_model(
        paths,
        filters,
        grain=str(trend_context["effective_grain"]),
        timezone_name=str(getattr(timezone_info, "key", "UTC")),
        analytics_context=analytics_context,
    )
    operations = metrics._empty_operations_page()
    executable_dataflows = int(aggregate["dataflow_succeeded"] or 0) + int(
        aggregate["dataflow_failed"] or 0
    )
    operations["kpis"] = {"total_failures": int(aggregate["job_failed"] or 0)}
    operations["dataflow_kpis"] = {
        "success_rate": metrics._rate(
            int(aggregate["dataflow_succeeded"] or 0),
            executable_dataflows,
        )
    }
    operations["jobs_by_date_status"] = metrics._status_by_date_counts(
        list(aggregate["jobs_by_date_status"]),
        trend_context,
    )
    latest_log_at = aggregate.get("latest_log_at")
    latest_job_log_at = aggregate.get("latest_job_log_at")
    latest_dataflow_log_at = aggregate.get("latest_dataflow_log_at")
    return {
        "summary": {
            "dataflow_records": int(aggregate["dataflow_records"] or 0),
            "job_records": int(aggregate["job_records"] or 0),
            "date_range": {"min": aggregate.get("date_min"), "max": aggregate.get("date_max")},
            "latest_log_at": latest_log_at,
            "latest_job_log_at": latest_job_log_at,
            "latest_dataflow_log_at": latest_dataflow_log_at,
            "timezone": timezone_label,
            "timezone_source": timezone_source,
            "requested_grain": trend_context["requested_grain"],
            "effective_grain": trend_context["effective_grain"],
            "active_engines": int(aggregate["active_engines"] or 0),
            "active_metadata_providers": int(aggregate["active_metadata_providers"] or 0),
            "log_paths": len([path for path in paths if path.enabled]),
        },
        "health": metrics._empty_health_page(),
        "attention": [],
        "coverage": {},
        "reconciliation": {},
        "diagnostics": metrics._empty_diagnostics_page(),
        "metric_definitions": metrics._metric_definitions(),
        "operations": operations,
        "failures": metrics._empty_failures_page(),
        "performance": metrics._empty_performance_page(),
        "volume": metrics._empty_volume_page(),
        "maintenance": metrics._empty_maintenance_page(),
        "freshness": metrics._empty_freshness_page(),
        "errors": [],
    }


def _build_overview_page(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    trend_context = metrics._trend_context(filters, [], timezone_info)
    aggregate = overview_read_model(
        paths,
        filters,
        grain=str(trend_context["effective_grain"]),
        timezone_name=str(getattr(timezone_info, "key", "UTC")),
        analytics_context=analytics_context,
    )
    attention = aggregate.get("attention") or {}
    performance_attention = attention.get("performance") or {}
    freshness_attention = attention.get("freshness") or {}
    maintenance_attention = attention.get("maintenance") or {}
    values = aggregate.get("summary") or {}
    dataflow_records = int(values.get("dataflow_records") or 0)
    job_records = int(values.get("job_records") or 0)
    job_succeeded = int(values.get("job_succeeded") or 0)
    job_failed = int(values.get("job_failed") or 0)
    dataflow_succeeded = int(values.get("dataflow_succeeded") or 0)
    dataflow_failed = int(values.get("dataflow_failed") or 0)

    operations = metrics._empty_operations_page()
    operations["windows"] = aggregate["windows"]
    operations["jobs_by_date_status"] = metrics._status_by_date_counts(
        list(aggregate["job_status_trend"]), trend_context,
    )
    operations["dataflows_by_date_status"] = metrics._status_by_date_counts(
        list(aggregate["dataflow_status_trend"]), trend_context,
    )
    operations["jobs_by_engine_provider"] = aggregate["runtime_contexts"]
    operations["job_runs_by_operation_type"] = aggregate["job_operation_health"]
    operations["job_runs_by_dataflow_operation_type"] = aggregate["job_operation_health"]
    operations["dataflow_runs_by_operation_type"] = aggregate["dataflow_operation_health"]
    operations["phase_health"] = aggregate["phase_health"]
    operations["job_duration_stats"] = _prefixed_duration_stats(values, "job")
    operations["dataflow_duration_stats"] = _prefixed_duration_stats(values, "dataflow")
    operations["kpis"] = {
        "total_jobs": job_records,
        "total_succeeded": job_succeeded,
        "job_success_rate": metrics._rate(job_succeeded, job_succeeded + job_failed),
        "job_failure_rate": metrics._rate(job_failed, job_succeeded + job_failed),
        "job_skip_rate": metrics._rate(int(values.get("job_skipped") or 0), job_records),
        "total_rows_processed": float(values.get("total_rows_processed") or 0),
        "total_failures": job_failed,
        "total_skipped": int(values.get("job_child_skipped") or 0),
        "total_pending": int(values.get("job_pending") or 0),
        "total_running": int(values.get("job_running") or 0),
        "avg_duration_seconds": float(values.get("job_avg_duration_seconds") or 0),
        "p95_duration_seconds": float(values.get("job_p95_duration_seconds") or 0),
    }
    operations["dataflow_kpis"] = {
        "total_dataflows": dataflow_records,
        "succeeded": dataflow_succeeded,
        "failed": dataflow_failed,
        "skipped": int(values.get("dataflow_skipped") or 0),
        "pending": int(values.get("dataflow_pending") or 0),
        "running": int(values.get("dataflow_running") or 0),
        "success_rate": metrics._rate(
            dataflow_succeeded, dataflow_succeeded + dataflow_failed,
        ),
        "failure_rate": metrics._rate(
            dataflow_failed, dataflow_succeeded + dataflow_failed,
        ),
        "skip_rate": metrics._rate(int(values.get("dataflow_skipped") or 0), dataflow_records),
        "pending_rate": metrics._rate(int(values.get("dataflow_pending") or 0), dataflow_records),
        "running_rate": metrics._rate(int(values.get("dataflow_running") or 0), dataflow_records),
        "total_bytes_written": float(values.get("total_bytes_written") or 0),
        "avg_duration_seconds": float(values.get("dataflow_avg_duration_seconds") or 0),
        "p95_duration_seconds": float(values.get("dataflow_p95_duration_seconds") or 0),
        "active_engines": int(values.get("dataflow_active_engines") or 0),
    }

    health_values = aggregate.get("health") or {}
    enabled_log_paths = len([path for path in paths if path.enabled])
    coverage_status = (
        "missing_sources" if not enabled_log_paths
        else "no_records" if not dataflow_records and not job_records
        else "partial" if not dataflow_records or not job_records
        else "ok"
    )
    coverage = {
        "enabled_log_paths": enabled_log_paths,
        "dataflow_records": dataflow_records,
        "job_records": job_records,
        "read_errors": 0,
        "status": coverage_status,
    }
    reconciliation = {
        "status": "warning" if int(health_values.get("mismatch_count") or 0) else "ok",
        "mismatch_count": int(health_values.get("mismatch_count") or 0),
        "checks": [],
    }
    health = environment_health(
        latest_log_at=values.get("latest_log_at"),
        latest_job_log_at=values.get("latest_job_log_at"),
        latest_dataflow_log_at=values.get("latest_dataflow_log_at"),
        coverage=coverage,
        reconciliation=reconciliation,
        failed_jobs_last_3_days=int(health_values.get("failed_jobs_last_3_days") or 0),
        failed_jobs_last_7_days=int(health_values.get("failed_jobs_last_7_days") or 0),
        failed_dataflows_last_3_days=int(health_values.get("failed_dataflows_last_3_days") or 0),
        failed_dataflows_last_7_days=int(health_values.get("failed_dataflows_last_7_days") or 0),
        maintenance_failed_last_7_days=int(health_values.get("maintenance_failed_last_7_days") or 0),
        maintenance_failed_last_14_days=int(health_values.get("maintenance_failed_last_14_days") or 0),
        maintenance_skipped_last_7_days=int(health_values.get("maintenance_skipped_last_7_days") or 0),
        has_jobs=job_records > 0,
    )
    failures = {
        **metrics._empty_failures_page(),
        "error_categories": aggregate["error_categories"],
        "top_failing_dataflows": aggregate["top_failing_dataflows"],
    }
    dataflow_duration = operations["dataflow_duration_stats"]
    p50 = float(dataflow_duration.get("p50_duration_seconds") or 0)
    p95 = float(dataflow_duration.get("p95_duration_seconds") or 0)
    performance = metrics._empty_performance_page()
    performance["kpis"] = {
        "p50_duration_seconds": p50,
        "p95_duration_seconds": p95,
        "duration_pressure_ratio": p95 / p50 if p50 else 0,
        "optimization_candidate_count": int(performance_attention.get("optimization_candidate_count") or 0),
    }
    maintenance = metrics._empty_maintenance_page()
    maintenance["kpis"] = {
        "coverage_missing_tables": int(maintenance_attention.get("coverage_missing_tables") or 0),
        "lagged_tables": int(maintenance_attention.get("lagged_tables") or 0),
        "latest_active_tables": int(maintenance_attention.get("latest_active_tables") or 0),
    }
    freshness = metrics._empty_freshness_page()
    freshness["kpis"] = {
        "stale_candidates": int(freshness_attention.get("stale_candidates") or 0),
        "watermark_unchanged_runs": int(freshness_attention.get("watermark_unchanged_runs") or 0),
    }
    diagnostics = metrics._empty_diagnostics_page()
    diagnostics["kpis"] = {
        "orphan_dataflow_job_ids": int(health_values.get("orphan_dataflow_job_ids") or 0),
        "jobs_without_dataflow_records": int(health_values.get("jobs_without_dataflow_records") or 0),
        "cache_warning_count": 0,
    }
    attention = metrics._attention_queue(
        [], [], failures, performance, maintenance, coverage, reconciliation,
        freshness, health, operations=operations, diagnostics=diagnostics,
    )
    return {
        "summary": {
            "dataflow_records": dataflow_records,
            "job_records": job_records,
            "date_range": {"min": values.get("date_min"), "max": values.get("date_max")},
            "latest_log_at": values.get("latest_log_at"),
            "latest_job_log_at": values.get("latest_job_log_at"),
            "latest_dataflow_log_at": values.get("latest_dataflow_log_at"),
            "timezone": timezone_label,
            "timezone_source": timezone_source,
            "requested_grain": trend_context["requested_grain"],
            "effective_grain": trend_context["effective_grain"],
            "active_engines": int(values.get("active_engines") or 0),
            "active_metadata_providers": int(values.get("active_metadata_providers") or 0),
            "log_paths": enabled_log_paths,
        },
        "health": health,
        "attention": attention,
        "coverage": {},
        "reconciliation": {},
        "diagnostics": metrics._empty_diagnostics_page(),
        "metric_definitions": metrics._metric_definitions(),
        "operations": operations,
        "failures": {**metrics._empty_failures_page(), "error_categories": aggregate["error_categories"]},
        "performance": metrics._empty_performance_page(),
        "volume": {
            **metrics._empty_volume_page(),
            "rows_by_date": aggregate["rows_by_date"],
            "bytes_by_date": aggregate["bytes_by_date"],
        },
        "maintenance": metrics._empty_maintenance_page(),
        "freshness": metrics._empty_freshness_page(),
        "errors": [],
    }


def _prefixed_duration_stats(values: dict[str, Any], prefix: str) -> dict[str, float | int]:
    return {
        "count": int(values.get(f"{prefix}_duration_count") or 0),
        "avg_duration_seconds": float(values.get(f"{prefix}_avg_duration_seconds") or 0),
        "q1_duration_seconds": float(values.get(f"{prefix}_q1_duration_seconds") or 0),
        "p50_duration_seconds": float(values.get(f"{prefix}_p50_duration_seconds") or 0),
        "q3_duration_seconds": float(values.get(f"{prefix}_q3_duration_seconds") or 0),
        "p75_duration_seconds": float(values.get(f"{prefix}_q3_duration_seconds") or 0),
        "p95_duration_seconds": float(values.get(f"{prefix}_p95_duration_seconds") or 0),
        "p99_duration_seconds": float(values.get(f"{prefix}_p99_duration_seconds") or 0),
        "max_duration_seconds": float(values.get(f"{prefix}_max_duration_seconds") or 0),
    }


def _build_volume_page(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    trend_context = metrics._trend_context(filters, [], timezone_info)
    aggregate = volume_read_model(
        paths, filters,
        grain=str(trend_context["effective_grain"]),
        timezone_name=str(getattr(timezone_info, "key", "UTC")),
        analytics_context=analytics_context,
    )
    values = aggregate.get("summary") or {}
    kpis = {
        key: values.get(key) or 0
        for key in (
            "total_rows_read", "total_rows_written", "total_est_rows_written",
            "total_est_rows_written_non_lakehouse", "total_rows_inserted",
            "total_rows_updated", "total_rows_deleted", "lakehouse_destination_run_count",
            "lakehouse_destination_share", "files_added", "files_removed",
            "total_bytes_added", "total_bytes_removed", "total_bytes_saved",
            "net_bytes_change", "avg_bytes_per_file_added", "skip_count", "skip_rate",
            "high_volume_run_count", "high_volume_candidate_run_count",
            "high_volume_rows_count", "high_volume_est_rows_count",
            "high_volume_lakehouse_rows_count", "high_volume_bytes_count",
            "high_volume_files_count", "high_volume_dataflow_count",
        )
    }
    volume = {
        "kpis": kpis,
        "rows_by_date": aggregate["rows_by_date"],
        "bytes_by_date": aggregate["bytes_by_date"],
        "volume_by_load_type": aggregate["volume_by_load_type"],
        "volume_by_workload_type": aggregate["volume_by_workload_type"],
        "route_volume": aggregate["route_volume"],
        "top_dataflows_by_rows_read": aggregate["top_dataflows_by_rows_read"],
        "top_dataflows_by_est_rows_written": aggregate["top_dataflows_by_est_rows_written"],
        "top_dataflows_by_rows_written": aggregate["top_dataflows_by_rows_written"],
        "top_dataflows_by_bytes_added": aggregate["top_dataflows_by_bytes_added"],
        "top_dataflows_by_net_bytes": aggregate["top_dataflows_by_net_bytes"],
    }
    return _empty_internal_page(
        paths, values, trend_context, timezone_label, timezone_source,
        volume=volume,
    )


def _build_freshness_page(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    trend_context = metrics._trend_context(filters, [], timezone_info)
    aggregate = freshness_read_model(
        paths, filters,
        grain=str(trend_context["effective_grain"]),
        timezone_name=str(getattr(timezone_info, "key", "UTC")),
        analytics_context=analytics_context,
    )
    values = aggregate.get("summary") or {}
    kpi_keys = {
        "successful_runs", "freshness_runs", "failed_runs", "skipped_runs",
        "observed_dataflows", "missing_dataflow_id_runs", "dataflows_with_freshness_evidence",
        "latest_status_issue_dataflows", "latest_watermark_invalid_dataflows",
        "latest_watermark_incomplete_dataflows", "latest_watermark_issue_dataflows",
        "watermark_enabled_dataflows", "watermark_coverage_rate", "watermark_advanced_runs",
        "watermark_initialized_runs", "watermark_unchanged_runs", "watermark_incomplete_runs",
        "watermark_adjusted_runs", "watermark_invalid_runs", "watermark_unknown_runs",
        "watermark_advanced_rate", "skipped_streak_dataflows", "skipped_streak_threshold",
        "stale_candidates", "stale_dataflows", "stale_threshold_days", "stale_dataflow_rate",
        "min_age_days", "p50_age_days", "p95_age_days", "max_age_days",
        "min_age_seconds", "p50_age_seconds", "p95_age_seconds", "max_age_seconds",
    }
    kpis = {key: values.get(key) or 0 for key in kpi_keys}
    kpis.update({
        "latest_successful_runs": kpis["successful_runs"],
        "skipped_no_new_data": kpis["skipped_runs"],
    })
    freshness = {
        "kpis": kpis,
        "age_by_dataflow": aggregate["age_by_dataflow"],
        "watermark_movement_by_date": aggregate["watermark_movement_by_date"],
        "age_distribution": aggregate["age_distribution"],
        "watermark_coverage_by_stage": aggregate["watermark_coverage_by_stage"],
        "skipped_streak_distribution": aggregate["skipped_streak_distribution"],
    }
    return _empty_internal_page(
        paths, values, trend_context, timezone_label, timezone_source,
        freshness=freshness,
    )


def _build_performance_page(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    trend_context = metrics._trend_context(filters, [], timezone_info)
    aggregate = performance_read_model(
        paths, filters,
        grain=str(trend_context["effective_grain"]),
        timezone_name=str(getattr(timezone_info, "key", "UTC")),
        analytics_context=analytics_context,
    )
    values = aggregate.get("summary") or {}
    p50 = float(values.get("p50_duration_seconds") or 0)
    p95 = float(values.get("p95_duration_seconds") or 0)
    kpis = {
        key: values.get(key) or 0
        for key in (
            "run_count", "avg_duration_seconds", "p50_duration_seconds",
            "p75_duration_seconds", "p95_duration_seconds", "p99_duration_seconds",
            "max_duration_seconds", "slowest_run_duration_seconds",
            "slowest_run_dataflow_name", "slowest_run_dataflow_id",
            "slowest_run_dataflow_run_id", "slowest_run_job_id",
            "slowest_run_start_time", "slowest_run_end_time",
            "slowest_run_stage", "slowest_run_operation_type",
            "slowest_run_status", "bottleneck_phase",
            "source_duration_percent", "transform_duration_percent",
            "destination_duration_percent", "overhead_duration_percent",
            "rows_read_per_second", "total_rows_read", "total_rows_written",
            "optimization_candidate_count", "slow_small_workload_count",
            "slow_small_maintenance_count", "high_overhead_count", "phase_skew_count",
        )
    }
    kpis["duration_pressure_ratio"] = round(p95 / p50, 3) if p50 else 0
    kpis["duration_outlier_count"] = sum(
        int(row.get("outlier_count") or 0)
        for row in aggregate["duration_distribution_by_stage"]
    )
    performance = {
        "kpis": kpis,
        **{key: aggregate[key] for key in (
            "duration_distribution_by_stage",
            "phase_contribution_by_stage_operation", "workload_efficiency_points",
            "slowest_dataflow_profiles", "runtime_context_profiles",
            "performance_trend",
        )},
    }
    return _empty_internal_page(
        paths, values, trend_context, timezone_label, timezone_source,
        performance=performance,
    )


def _build_maintenance_page(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    trend_context = metrics._trend_context(filters, [], timezone_info)
    aggregate = maintenance_read_model(
        paths, filters,
        grain=str(trend_context["effective_grain"]),
        timezone_name=str(getattr(timezone_info, "key", "UTC")),
        analytics_context=analytics_context,
    )
    values = aggregate.get("summary") or {}
    total_runs = int(values.get("total_maintenance_runs") or 0)
    failed_tables = int(values.get("latest_failed_tables") or 0)
    warnings = sum(int(values.get(key) or 0) for key in (
        "coverage_missing_tables", "latest_skipped_tables", "latest_active_tables", "lagged_tables",
    ))
    health_status = "no_evidence" if not total_runs else "has_issues" if failed_tables else "warning" if warnings else "healthy"
    duration = float(values.get("duration_seconds") or 0)
    reclaimed = float(values.get("bytes_reclaimed") or 0)
    files_removed = float(values.get("files_removed") or 0)
    kpis = {
        key: values.get(key) or 0
        for key in (
            "total_maintenance_runs", "succeeded_ops", "failed_ops", "skipped_ops",
            "running_ops", "pending_ops", "files_removed", "bytes_reclaimed", "bytes_saved",
            "duration_seconds", "no_op_runs", "no_op_tables", "no_op_duration_seconds",
            "succeeded_duration_seconds", "high_duration_runs",
            "tables_with_reclaim", "tables_with_issues", "tables_with_warnings",
            "latest_failed_tables", "latest_skipped_tables", "latest_active_tables",
            "lagged_tables", "active_lakehouse_tables", "maintained_tables",
            "coverage_missing_tables", "coverage_rate",
        )
    }
    kpis.update({
        "health_status": health_status,
        "bytes_reclaimed_per_second": round(reclaimed / duration, 3) if duration else 0,
        "avg_bytes_per_file_removed": round(reclaimed / files_removed, 3) if files_removed else 0,
        "no_op_runtime_share": metrics._rate(
            float(values.get("no_op_duration_seconds") or 0),
            float(values.get("succeeded_duration_seconds") or 0),
        ),
        "maintenance_lag_warning_days": 7,
    })
    maintenance = {
        "kpis": kpis,
        **{key: aggregate[key] for key in (
            "status_by_date", "reclaim_by_date", "table_outcome", "table_efficiency_points",
            "format_comparison", "bytes_reclaimed_by_date",
        )},
    }
    return _empty_internal_page(
        paths, values, trend_context, timezone_label, timezone_source,
        maintenance=maintenance,
    )


def _empty_internal_page(
    paths: list[EnvironmentSource],
    values: dict[str, Any],
    trend_context: dict[str, Any],
    timezone_label: str,
    timezone_source: str,
    **sections: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "summary": {
            "dataflow_records": int(values.get("dataflow_records") or 0),
            "job_records": int(values.get("job_records") or 0),
            "date_range": {"min": values.get("date_min"), "max": values.get("date_max")},
            "latest_log_at": values.get("latest_log_at"),
            "latest_job_log_at": values.get("latest_job_log_at"),
            "latest_dataflow_log_at": values.get("latest_dataflow_log_at"),
            "timezone": timezone_label,
            "timezone_source": timezone_source,
            "requested_grain": trend_context["requested_grain"],
            "effective_grain": trend_context["effective_grain"],
            "active_engines": int(values.get("active_engines") or 0),
            "active_metadata_providers": int(values.get("active_metadata_providers") or 0),
            "log_paths": len([path for path in paths if path.enabled]),
        },
        "health": metrics._empty_health_page(), "attention": [], "coverage": {},
        "reconciliation": {}, "diagnostics": metrics._empty_diagnostics_page(),
        "metric_definitions": metrics._metric_definitions(),
        "operations": metrics._empty_operations_page(),
        "failures": metrics._empty_failures_page(),
        "performance": metrics._empty_performance_page(),
        "volume": metrics._empty_volume_page(),
        "maintenance": metrics._empty_maintenance_page(),
        "freshness": metrics._empty_freshness_page(),
        "errors": [],
    }
    result.update(sections)
    return result


def _build_dataflows_page(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    trend_context = metrics._trend_context(filters, [], timezone_info)
    aggregate = dataflows_read_model(
        paths,
        filters,
        grain=str(trend_context["effective_grain"]),
        timezone_name=str(getattr(timezone_info, "key", "UTC")),
        analytics_context=analytics_context,
    )
    summary_values = aggregate.get("summary") or {}
    operations = metrics._empty_operations_page()
    operations["windows"] = aggregate["windows"]
    operations["dataflows_by_date_status"] = metrics._status_by_date_counts(
        list(aggregate["status_trend"]),
        trend_context,
    )
    operations["dataflow_duration_by_stage"] = aggregate["duration_by_stage"]
    operations["phase_health_by_stage"] = aggregate["phase_health_by_stage"]
    operations["dataflow_endpoint_health"] = aggregate["endpoint_health"]
    operations["dataflow_name_status_health"] = aggregate["name_status_health"]
    duration_stats = {
        "count": int(summary_values.get("duration_count") or 0),
        "avg_duration_seconds": float(summary_values.get("avg_duration_seconds") or 0),
        "q1_duration_seconds": float(summary_values.get("q1_duration_seconds") or 0),
        "p50_duration_seconds": float(summary_values.get("p50_duration_seconds") or 0),
        "q3_duration_seconds": float(summary_values.get("q3_duration_seconds") or 0),
        "p95_duration_seconds": float(summary_values.get("p95_duration_seconds") or 0),
        "p99_duration_seconds": float(summary_values.get("p99_duration_seconds") or 0),
        "max_duration_seconds": float(summary_values.get("max_duration_seconds") or 0),
    }
    operations["dataflow_duration_stats"] = duration_stats
    operations["dataflow_kpis"] = {
        "total_dataflows": int(summary_values.get("total_dataflows") or 0),
        "succeeded": int(summary_values.get("succeeded") or 0),
        "failed": int(summary_values.get("failed") or 0),
        "skipped": int(summary_values.get("skipped") or 0),
        "pending": int(summary_values.get("pending") or 0),
        "running": int(summary_values.get("running") or 0),
        "success_rate": float(summary_values.get("success_rate") or 0),
        "failure_rate": float(summary_values.get("failure_rate") or 0),
        "skip_rate": float(summary_values.get("skip_rate") or 0),
        "pending_rate": float(summary_values.get("pending_rate") or 0),
        "running_rate": float(summary_values.get("running_rate") or 0),
        "total_bytes_written": float(summary_values.get("total_bytes_written") or 0),
        "avg_duration_seconds": duration_stats["avg_duration_seconds"],
        "p95_duration_seconds": duration_stats["p95_duration_seconds"],
        "active_engines": int(summary_values.get("dataflow_active_engines") or 0),
    }
    volume = metrics._empty_volume_page()
    volume["kpis"] = {
        "total_rows_read": float(summary_values.get("total_rows_read") or 0),
        "total_rows_written": float(summary_values.get("total_rows_written") or 0),
        "net_bytes_change": float(summary_values.get("net_bytes_change") or 0),
    }
    return {
        "summary": {
            "dataflow_records": int(summary_values.get("dataflow_records") or 0),
            "job_records": int(summary_values.get("job_records") or 0),
            "date_range": {
                "min": summary_values.get("date_min"),
                "max": summary_values.get("date_max"),
            },
            "latest_log_at": summary_values.get("latest_log_at"),
            "latest_job_log_at": summary_values.get("latest_job_log_at"),
            "latest_dataflow_log_at": summary_values.get("latest_dataflow_log_at"),
            "timezone": timezone_label,
            "timezone_source": timezone_source,
            "requested_grain": trend_context["requested_grain"],
            "effective_grain": trend_context["effective_grain"],
            "active_engines": int(summary_values.get("active_engines") or 0),
            "active_metadata_providers": int(summary_values.get("active_metadata_providers") or 0),
            "log_paths": len([path for path in paths if path.enabled]),
        },
        "health": metrics._empty_health_page(),
        "attention": [],
        "coverage": {},
        "reconciliation": {},
        "diagnostics": metrics._empty_diagnostics_page(),
        "metric_definitions": metrics._metric_definitions(),
        "operations": operations,
        "failures": metrics._empty_failures_page(),
        "performance": metrics._empty_performance_page(),
        "volume": volume,
        "maintenance": metrics._empty_maintenance_page(),
        "freshness": metrics._empty_freshness_page(),
        "errors": [],
    }
def _build_jobs_page(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    trend_context = metrics._trend_context(filters, [], timezone_info)
    aggregate = jobs_read_model(
        paths,
        filters,
        grain=str(trend_context["effective_grain"]),
        timezone_name=str(getattr(timezone_info, "key", "UTC")),
        analytics_context=analytics_context,
    )
    values = aggregate.get("summary") or {}
    duration_stats = {
        "count": int(values.get("duration_count") or 0),
        "avg_duration_seconds": float(values.get("avg_duration_seconds") or 0),
        "q1_duration_seconds": float(values.get("q1_duration_seconds") or 0),
        "p50_duration_seconds": float(values.get("p50_duration_seconds") or 0),
        "q3_duration_seconds": float(values.get("q3_duration_seconds") or 0),
        "p75_duration_seconds": float(values.get("q3_duration_seconds") or 0),
        "p95_duration_seconds": float(values.get("p95_duration_seconds") or 0),
        "p99_duration_seconds": float(values.get("p99_duration_seconds") or 0),
        "max_duration_seconds": float(values.get("max_duration_seconds") or 0),
    }
    operations = metrics._empty_operations_page()
    operations["kpis"] = {
        "total_jobs": int(values.get("total_jobs") or 0),
        "total_succeeded": int(values.get("total_succeeded") or 0),
        "job_success_rate": float(values.get("job_success_rate") or 0),
        "job_failure_rate": float(values.get("job_failure_rate") or 0),
        "job_skip_rate": float(values.get("job_skip_rate") or 0),
        "job_pending_rate": float(values.get("job_pending_rate") or 0),
        "job_running_rate": float(values.get("job_running_rate") or 0),
        "total_rows_processed": float(values.get("total_rows_processed") or 0),
        "total_failures": int(values.get("total_failures") or 0),
        "total_skipped": int(values.get("total_skipped") or 0),
        "total_pending": int(values.get("total_pending") or 0),
        "total_running": int(values.get("total_running") or 0),
        "avg_duration_seconds": duration_stats["avg_duration_seconds"],
        "p95_duration_seconds": duration_stats["p95_duration_seconds"],
    }
    total_dataflows = int(values.get("total_dataflows") or 0)
    succeeded_dataflows = int(values.get("succeeded_dataflows") or 0)
    failed_dataflows = int(values.get("failed_dataflows") or 0)
    operations["dataflow_kpis"] = {
        "total_dataflows": total_dataflows,
        "succeeded": succeeded_dataflows,
        "failed": failed_dataflows,
        "skipped": int(values.get("skipped_dataflows") or 0),
        "running": int(values.get("running_dataflows") or 0),
        "pending": int(values.get("pending_dataflows") or 0),
        "success_rate": metrics._rate(succeeded_dataflows, succeeded_dataflows + failed_dataflows),
        "failure_rate": metrics._rate(failed_dataflows, succeeded_dataflows + failed_dataflows),
    }
    operations["windows"] = aggregate["windows"]
    operations["job_duration_stats"] = duration_stats
    operations["jobs_by_date_status"] = metrics._status_by_date_counts(
        list(aggregate["status_trend"]),
        trend_context,
    )
    operations["job_duration_by_operation_types"] = aggregate["job_duration_by_operation"]
    operations["job_workload_efficiency"] = aggregate["workload_efficiency"]
    operations["job_child_fanout_distribution"] = aggregate["child_fanout"]
    operations["job_status_by_stage"] = aggregate["status_by_stage"]
    operations["latest_failed_job"] = aggregate["latest_failed_job"]
    checks = list(aggregate["reconciliation_checks"])
    reconciliation = {
        "status": "warning" if checks else "ok",
        "mismatch_count": len(checks),
        "checks": checks,
    }
    return {
        "summary": {
            "dataflow_records": int(values.get("dataflow_records") or 0),
            "job_records": int(values.get("job_records") or 0),
            "date_range": {"min": values.get("date_min"), "max": values.get("date_max")},
            "latest_log_at": values.get("latest_log_at"),
            "latest_job_log_at": values.get("latest_job_log_at"),
            "latest_dataflow_log_at": values.get("latest_dataflow_log_at"),
            "timezone": timezone_label,
            "timezone_source": timezone_source,
            "requested_grain": trend_context["requested_grain"],
            "effective_grain": trend_context["effective_grain"],
            "active_engines": int(values.get("active_engines") or 0),
            "active_metadata_providers": int(values.get("active_metadata_providers") or 0),
            "log_paths": len([path for path in paths if path.enabled]),
        },
        "health": metrics._empty_health_page(),
        "attention": [],
        "coverage": {},
        "reconciliation": reconciliation,
        "diagnostics": metrics._empty_diagnostics_page(),
        "metric_definitions": metrics._metric_definitions(),
        "operations": operations,
        "failures": metrics._empty_failures_page(),
        "performance": metrics._empty_performance_page(),
        "volume": metrics._empty_volume_page(),
        "maintenance": metrics._empty_maintenance_page(),
        "freshness": metrics._empty_freshness_page(),
        "errors": [],
    }


def _build_failures_page(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    trend_context = metrics._trend_context(filters, [], timezone_info)
    aggregate = failures_read_model(
        paths,
        filters,
        timezone_name=str(getattr(timezone_info, "key", "UTC")),
        analytics_context=analytics_context,
    )
    values = aggregate.get("summary") or {}
    succeeded_jobs = int(values.get("succeeded_jobs") or 0)
    failed_jobs = int(values.get("failed_job_runs") or 0)
    succeeded_dataflows = int(values.get("succeeded_dataflows") or 0)
    failed_dataflows = int(values.get("failed_dataflows") or 0)
    operations = metrics._empty_operations_page()
    operations["windows"] = aggregate["windows"]
    operations["kpis"] = {
        "total_jobs": int(values.get("job_records") or 0),
        "total_succeeded": succeeded_jobs,
        "total_failures": failed_jobs,
        "job_success_rate": metrics._rate(succeeded_jobs, succeeded_jobs + failed_jobs),
        "job_failure_rate": metrics._rate(failed_jobs, succeeded_jobs + failed_jobs),
    }
    operations["dataflow_kpis"] = {
        "total_dataflows": int(values.get("dataflow_records") or 0),
        "succeeded": succeeded_dataflows,
        "failed": failed_dataflows,
        "success_rate": metrics._rate(
            succeeded_dataflows,
            succeeded_dataflows + failed_dataflows,
        ),
        "failure_rate": metrics._rate(
            failed_dataflows,
            succeeded_dataflows + failed_dataflows,
        ),
    }
    failures = metrics._empty_failures_page()
    failures.update({
        "kpis": {
            key: value
            for key, value in values.items()
            if key not in {
                "dataflow_records", "job_records", "date_min", "date_max",
                "latest_log_at", "latest_job_log_at", "latest_dataflow_log_at",
                "active_engines", "active_metadata_providers", "succeeded_jobs",
                "failed_job_runs", "succeeded_dataflows",
            }
        },
        "latest_queue": aggregate["latest_queue"],
        "repeated_signatures": aggregate["repeated_signatures"],
        "failure_by_phase": aggregate["failure_by_phase"],
        "failure_category_phase_matrix": aggregate["category_phase"],
        "endpoint_impact": aggregate["endpoint_impact"],
        "failed_by_stage": aggregate["failed_by_stage"],
        "failed_by_source_connection_type": aggregate["source_types"],
        "top_failing_dataflows": aggregate["top_dataflows"],
        "error_categories": aggregate["error_categories"],
        "failure_trend_by_date": aggregate["trend"],
        "failed_records": aggregate["failed_records"],
    })
    return {
        "summary": {
            "dataflow_records": int(values.get("dataflow_records") or 0),
            "job_records": int(values.get("job_records") or 0),
            "date_range": {"min": values.get("date_min"), "max": values.get("date_max")},
            "latest_log_at": values.get("latest_log_at"),
            "latest_job_log_at": values.get("latest_job_log_at"),
            "latest_dataflow_log_at": values.get("latest_dataflow_log_at"),
            "timezone": timezone_label,
            "timezone_source": timezone_source,
            "requested_grain": trend_context["requested_grain"],
            "effective_grain": trend_context["effective_grain"],
            "active_engines": int(values.get("active_engines") or 0),
            "active_metadata_providers": int(values.get("active_metadata_providers") or 0),
            "log_paths": len([path for path in paths if path.enabled]),
        },
        "health": metrics._empty_health_page(),
        "attention": [],
        "coverage": {},
        "reconciliation": {},
        "diagnostics": metrics._empty_diagnostics_page(),
        "metric_definitions": metrics._metric_definitions(),
        "operations": operations,
        "failures": failures,
        "performance": metrics._empty_performance_page(),
        "volume": metrics._empty_volume_page(),
        "maintenance": metrics._empty_maintenance_page(),
        "freshness": metrics._empty_freshness_page(),
        "errors": [],
    }


def _build_diagnostics_page(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    trend_context = metrics._trend_context(filters, [], timezone_info)
    aggregate = diagnostics_read_model(
        paths,
        filters,
        grain=str(trend_context["effective_grain"]),
        timezone_name=str(getattr(timezone_info, "key", "UTC")),
        analytics_context=analytics_context,
    )
    summary = aggregate["summary"]
    trend_rows = [
        {"end_time": value}
        for value in (summary.get("earliest_log_at"), summary.get("latest_log_at"))
        if value
    ]
    trend_context = metrics._trend_context(filters, trend_rows, timezone_info)
    trend_context["effective_grain"] = aggregate.get("effective_grain") or trend_context["effective_grain"]
    diagnostics = dict(aggregate["diagnostics"])
    diagnostics["record_evidence_by_date"] = _fill_diagnostics_trend_buckets(
        list(diagnostics.get("record_evidence_by_date") or []),
        trend_context,
    )
    coverage = dict(aggregate["coverage"])
    coverage["enabled_log_paths"] = len([path for path in paths if path.enabled])
    return {
        "summary": {
            "dataflow_records": int(summary.get("dataflow_records") or 0),
            "job_records": int(summary.get("job_records") or 0),
            "date_range": {"min": summary.get("date_min"), "max": summary.get("date_max")},
            "latest_log_at": summary.get("latest_log_at"),
            "latest_job_log_at": summary.get("latest_job_log_at"),
            "latest_dataflow_log_at": summary.get("latest_dataflow_log_at"),
            "timezone": timezone_label,
            "timezone_source": timezone_source,
            "requested_grain": trend_context["requested_grain"],
            "effective_grain": trend_context["effective_grain"],
            "active_engines": int(summary.get("active_engines") or 0),
            "active_metadata_providers": int(summary.get("active_metadata_providers") or 0),
            "log_paths": len([path for path in paths if path.enabled]),
        },
        "health": metrics._empty_health_page(),
        "attention": [],
        "coverage": coverage,
        "reconciliation": aggregate["reconciliation"],
        "diagnostics": diagnostics,
        "metric_definitions": metrics._metric_definitions(),
        "operations": metrics._empty_operations_page(),
        "failures": metrics._empty_failures_page(),
        "performance": metrics._empty_performance_page(),
        "volume": metrics._empty_volume_page(),
        "maintenance": metrics._empty_maintenance_page(),
        "freshness": metrics._empty_freshness_page(),
        "errors": [],
    }


def _fill_diagnostics_trend_buckets(
    rows: list[dict[str, Any]],
    trend_context: dict[str, Any],
) -> list[dict[str, Any]]:
    buckets = {str(row.get("bucket") or "unknown"): row for row in rows}
    for bucket in metrics._diagnostics_expected_trend_buckets(trend_context):
        buckets.setdefault(bucket, {
            "bucket": bucket,
            "date": bucket,
            "dataflow_records": 0,
            "job_records": 0,
            "matched_job_ids": 0,
            "orphan_dataflow_job_ids": 0,
            "jobs_without_dataflow_records": 0,
            "linkage_rate": 0,
        })
    return sorted(buckets.values(), key=lambda row: str(row.get("bucket") or ""))
