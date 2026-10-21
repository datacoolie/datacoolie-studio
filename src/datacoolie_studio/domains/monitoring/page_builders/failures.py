from __future__ import annotations

from datetime import tzinfo
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.metrics import page_defaults as metrics_defaults
from datacoolie_studio.domains.monitoring.metrics.aggregates import rate as metrics_rate
from datacoolie_studio.domains.monitoring.metrics.time_buckets import trend_context as metrics_trend_context
from datacoolie_studio.domains.monitoring.read_models.failures import failures_read_model


def build_failures_page(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    trend_context = metrics_trend_context(filters, [], timezone_info)
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
    operations = metrics_defaults.empty_operations_page()
    operations["windows"] = aggregate["windows"]
    operations["kpis"] = {
        "total_jobs": int(values.get("job_records") or 0),
        "total_succeeded": succeeded_jobs,
        "total_failures": failed_jobs,
        "job_success_rate": metrics_rate(succeeded_jobs, succeeded_jobs + failed_jobs),
        "job_failure_rate": metrics_rate(failed_jobs, succeeded_jobs + failed_jobs),
    }
    operations["dataflow_kpis"] = {
        "total_dataflows": int(values.get("dataflow_records") or 0),
        "succeeded": succeeded_dataflows,
        "failed": failed_dataflows,
        "success_rate": metrics_rate(
            succeeded_dataflows,
            succeeded_dataflows + failed_dataflows,
        ),
        "failure_rate": metrics_rate(
            failed_dataflows,
            succeeded_dataflows + failed_dataflows,
        ),
    }
    failures = metrics_defaults.empty_failures_page()
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
        "recovered_attempts": aggregate["recovered_attempts"],
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
            "log_base_paths": len([path for path in paths if path.enabled]),
        },
        "health": metrics_defaults.empty_health_page(),
        "attention": [],
        "coverage": {},
        "reconciliation": {},
        "diagnostics": metrics_defaults.empty_diagnostics_page(),
        "operations": operations,
        "failures": failures,
        "performance": metrics_defaults.empty_performance_page(),
        "volume": metrics_defaults.empty_volume_page(),
        "maintenance": metrics_defaults.empty_maintenance_page(),
        "freshness": metrics_defaults.empty_freshness_page(),
        "errors": [],
    }
