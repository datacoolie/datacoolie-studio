from __future__ import annotations

from datetime import tzinfo
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.metrics import page_defaults as metrics_defaults
import datacoolie_studio.domains.monitoring.metrics.overview_projections as metrics_overview
from datacoolie_studio.domains.monitoring.metrics.aggregates import rate as metrics_rate
from datacoolie_studio.domains.monitoring.metrics.time_buckets import trend_context as metrics_trend_context
from datacoolie_studio.domains.monitoring.repository import environment_overview_read_model


def build_environment_overview_page(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    trend_context = metrics_trend_context(filters, [], timezone_info)
    aggregate = environment_overview_read_model(
        paths,
        filters,
        grain=str(trend_context["effective_grain"]),
        timezone_name=str(getattr(timezone_info, "key", "UTC")),
        analytics_context=analytics_context,
    )
    operations = metrics_defaults.empty_operations_page()
    executable_dataflows = int(aggregate["dataflow_succeeded"] or 0) + int(
        aggregate["dataflow_failed"] or 0
    )
    operations["kpis"] = {"total_failures": int(aggregate["job_failed"] or 0)}
    operations["dataflow_kpis"] = {
        "success_rate": metrics_rate(
            int(aggregate["dataflow_succeeded"] or 0),
            executable_dataflows,
        )
    }
    operations["jobs_by_date_status"] = metrics_overview._status_by_date_counts(
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
            "log_base_paths": len([path for path in paths if path.enabled]),
        },
        "health": metrics_defaults.empty_health_page(),
        "attention": [],
        "coverage": {},
        "reconciliation": {},
        "diagnostics": metrics_defaults.empty_diagnostics_page(),
        "operations": operations,
        "failures": metrics_defaults.empty_failures_page(),
        "performance": metrics_defaults.empty_performance_page(),
        "volume": metrics_defaults.empty_volume_page(),
        "maintenance": metrics_defaults.empty_maintenance_page(),
        "freshness": metrics_defaults.empty_freshness_page(),
        "errors": [],
    }
