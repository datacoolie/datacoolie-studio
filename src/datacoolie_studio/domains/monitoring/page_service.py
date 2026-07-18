from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring import service as metrics
from datacoolie_studio.domains.read_models.cache import fingerprint
from datacoolie_studio.domains.read_models.contracts import ResultCacheKey, get_or_compute
from datacoolie_studio.domains.read_models.keys import monitoring_page as monitoring_page_key
from datacoolie_studio.domains.read_models.provider import result_cache_provider


MONITORING_PAGES = {
    "environment-overview", "overview", "jobs", "dataflows", "failures",
    "diagnostics", "performance", "volume", "maintenance", "freshness",
}

_PRODUCER_VERSION = "monitoring-page-v10"
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
        provider = result_cache_provider(session)
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
) -> ResultCacheKey:
    if page not in MONITORING_PAGES:
        raise ValueError(f"Unknown Monitoring page: {page}")
    parameters = _canonical_parameters(page, filters, timezone_label, now=now)
    return ResultCacheKey(
        environment_id=environment_id,
        namespace=monitoring_page_key(page),
        parameters_fingerprint=fingerprint(parameters),
        input_fingerprint=metrics.monitoring_input_fingerprint(session, paths),
        producer_version=_PRODUCER_VERSION,
    )


def monitoring_page_etag(key: ResultCacheKey) -> str:
    return f'"{fingerprint(key.identity)}"'


def public_monitoring_page(page: str, payload: dict[str, Any]) -> dict[str, Any]:
    if page != "performance":
        return payload
    performance = dict(payload.get("performance") or {})
    performance.pop("investigation_queue", None)
    return {**payload, "performance": performance}


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
    if page != "performance":
        raise ValueError(f"Monitoring evidence is not defined for page: {page}")
    payload = monitoring_page(
        paths,
        page,
        filters,
        session,
        timezone_info,
        timezone_label,
        timezone_source,
        environment_id=environment_id,
    )
    rows = list((payload.get("performance") or {}).get("investigation_queue") or [])
    safe_sort_by = sort_by if sort_by in _PERFORMANCE_EVIDENCE_SORT_FIELDS else "performance_candidate_priority"
    safe_sort_dir = sort_dir if sort_dir in {"asc", "desc"} else "desc"
    rows = metrics._sort_log_rows(rows, safe_sort_by, safe_sort_dir)
    records = rows[offset:offset + limit]
    return {
        "records": records,
        "errors": payload.get("errors") or [],
        "summary": {
            "records": len(records),
            "total_records": len(rows),
            "limit": limit,
            "offset": offset,
            "cache": "result",
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
    if range_value in {"24h", "3d", "7d", "30d", "90d", "today"}:
        try:
            anchor_timezone = ZoneInfo(timezone_label)
        except ZoneInfoNotFoundError:
            anchor_timezone = timezone.utc
        current = (now or datetime.now(timezone.utc)).astimezone(anchor_timezone)
        anchor = (
            current.replace(minute=0, second=0, microsecond=0).isoformat()
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
) -> dict[str, Any]:
    """Build only the read model required by one Monitoring page."""
    if page not in MONITORING_PAGES:
        raise ValueError(f"Unknown Monitoring page: {page}")
    active_timezone = timezone_info or timezone.utc
    active_timezone_label = timezone_label or "UTC"
    normalized_filters = metrics._normalize_monitoring_filters_for_timezone(
        filters or {}, timezone_info=active_timezone,
    )
    rows, jobs, errors = metrics._monitoring_rows(
        paths,
        session=session,
        enrich_for_investigation=page == "failures",
        filters=normalized_filters,
        report_columns=page not in {"dataflows", "environment-overview", "overview"},
        page=page,
    )
    jobs = metrics._filter_jobs_for_dataflow_scope(jobs, rows, normalized_filters)
    trend_context = metrics._trend_context(normalized_filters, [*rows, *jobs], active_timezone)

    if page == "environment-overview":
        operations = metrics._environment_overview_operations_page(rows, jobs, trend_context)
    elif page == "dataflows":
        operations = metrics._dataflows_operations_page(rows, jobs, active_timezone, trend_context)
    elif page in {"overview", "jobs", "failures"}:
        operations = metrics._operations_page(rows, jobs, timezone_info=active_timezone, trend_context=trend_context)
    else:
        operations = metrics._empty_operations_page()

    failures = metrics._failures_page(rows, jobs) if page in {"overview", "failures"} else metrics._empty_failures_page()
    performance = (
        metrics._overview_performance_page(rows) if page == "overview"
        else metrics._performance_page(rows, trend_context=trend_context) if page == "performance"
        else metrics._empty_performance_page()
    )
    volume = (
        metrics._overview_volume_page(rows, trend_context) if page == "overview"
        else metrics._dataflows_volume_page(rows) if page == "dataflows"
        else metrics._volume_page(rows, jobs, trend_context=trend_context) if page == "volume"
        else metrics._empty_volume_page()
    )
    maintenance = metrics._maintenance_page(rows, trend_context=trend_context) if page in {"overview", "maintenance"} else metrics._empty_maintenance_page()
    freshness = (
        metrics._overview_freshness_page(rows) if page == "overview"
        else metrics._freshness_page(rows, trend_context=trend_context) if page == "freshness"
        else metrics._empty_freshness_page()
    )
    coverage = metrics._coverage_page(paths, rows, jobs, errors) if page in {"overview", "diagnostics"} else {}
    reconciliation = metrics._reconciliation_page(rows, jobs) if page in {"overview", "jobs", "diagnostics"} else {}
    diagnostics = (
        metrics._overview_diagnostics_page(rows, jobs, errors) if page == "overview"
        else metrics._diagnostics_page(rows, jobs, errors, reconciliation, trend_context=trend_context) if page == "diagnostics"
        else metrics._empty_diagnostics_page()
    )
    health = metrics._health_page(rows, jobs, operations, maintenance, coverage, reconciliation) if page == "overview" else metrics._empty_health_page()
    attention = metrics._attention_queue(
        rows, jobs, failures, performance, maintenance, coverage, reconciliation,
        freshness, health, operations=operations, diagnostics=diagnostics,
    ) if page == "overview" else []

    if page == "overview":
        failures = {**metrics._empty_failures_page(), "error_categories": failures["error_categories"]}
        performance = metrics._empty_performance_page()
        maintenance = metrics._empty_maintenance_page()
        freshness = metrics._empty_freshness_page()
        coverage = {}
        reconciliation = {}
        diagnostics = metrics._empty_diagnostics_page()

    return {
        "summary": {
            "dataflow_records": len(rows),
            "job_records": len(jobs),
            "date_range": metrics._date_range([*rows, *jobs]),
            "latest_log_at": metrics._latest_log_at([*rows, *jobs]),
            "latest_job_log_at": metrics._latest_log_at(jobs),
            "latest_dataflow_log_at": metrics._latest_log_at(rows),
            "timezone": active_timezone_label,
            "timezone_source": timezone_source,
            "requested_grain": trend_context["requested_grain"],
            "effective_grain": trend_context["effective_grain"],
            "active_engines": len({job.get("engine_name") for job in jobs if job.get("engine_name")}),
            "active_metadata_providers": len({job.get("metadata_provider_name") for job in jobs if job.get("metadata_provider_name")}),
            "log_paths": len([path for path in paths if path.enabled]),
        },
        "health": health, "attention": attention, "coverage": coverage,
        "reconciliation": reconciliation, "diagnostics": diagnostics,
        "metric_definitions": metrics._metric_definitions(), "operations": operations,
        "failures": failures, "performance": performance, "volume": volume,
        "maintenance": maintenance, "freshness": freshness, "errors": errors,
    }
