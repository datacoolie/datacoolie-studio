from __future__ import annotations

from datetime import tzinfo
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.metrics import page_defaults as metrics_defaults
import datacoolie_studio.domains.monitoring.metrics.overview_projections as metrics_overview
from datacoolie_studio.domains.monitoring.metrics.aggregates import rate as metrics_rate
from datacoolie_studio.domains.monitoring.metrics.time_buckets import trend_context as metrics_trend_context
from datacoolie_studio.domains.monitoring.read_models.jobs import jobs_read_model
from datacoolie_studio.domains.monitoring.page_builders.common import nullable_float, nullable_int


def build_jobs_page(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    trend_context = metrics_trend_context(filters, [], timezone_info)
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
        "avg_duration_seconds": nullable_float(values.get("avg_duration_seconds")),
        "q1_duration_seconds": nullable_float(values.get("q1_duration_seconds")),
        "p50_duration_seconds": nullable_float(values.get("p50_duration_seconds")),
        "q3_duration_seconds": nullable_float(values.get("q3_duration_seconds")),
        "p75_duration_seconds": nullable_float(values.get("q3_duration_seconds")),
        "p95_duration_seconds": nullable_float(values.get("p95_duration_seconds")),
        "p99_duration_seconds": nullable_float(values.get("p99_duration_seconds")),
        "max_duration_seconds": nullable_float(values.get("max_duration_seconds")),
    }
    operations = metrics_defaults.empty_operations_page()
    operations["kpis"] = {
        "total_jobs": int(values.get("total_jobs") or 0),
        "total_succeeded": int(values.get("total_succeeded") or 0),
        "job_success_rate": float(values.get("job_success_rate") or 0),
        "job_failure_rate": float(values.get("job_failure_rate") or 0),
        "job_skip_rate": float(values.get("job_skip_rate") or 0),
        "job_pending_rate": float(values.get("job_pending_rate") or 0),
        "job_running_rate": float(values.get("job_running_rate") or 0),
        "total_rows_processed": nullable_int(values.get("total_rows_processed")),
        "total_failures": int(values.get("total_failures") or 0),
        "total_skipped": nullable_int(values.get("total_skipped")),
        "total_pending": nullable_int(values.get("total_pending")),
        "total_running": nullable_int(values.get("total_running")),
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
        "success_rate": metrics_rate(succeeded_dataflows, succeeded_dataflows + failed_dataflows),
        "failure_rate": metrics_rate(failed_dataflows, succeeded_dataflows + failed_dataflows),
    }
    operations["windows"] = aggregate["windows"]
    operations["job_duration_stats"] = duration_stats
    operations["jobs_by_date_status"] = metrics_overview._status_by_date_counts(
        list(aggregate["status_trend"]),
        trend_context,
    )
    operations["job_duration_by_operation_types"] = aggregate["job_duration_by_operation"]
    operations["job_workload_efficiency"] = aggregate["workload_efficiency"]
    operations["job_child_fanout_distribution"] = aggregate["child_fanout"]
    operations["job_status_by_stage"] = aggregate["status_by_stage"]
    operations["latest_failed_job"] = aggregate["latest_failed_job"]
    checks = list(aggregate["reconciliation_checks"])
    mismatch_count = max((int(item.get("mismatch_count", item.get("severity") == "warning")) for item in checks), default=0)
    insufficient = any(item.get("severity") == "insufficient_coverage" for item in checks)
    reconciliation = {
        "status": "warning" if mismatch_count else "insufficient_coverage" if insufficient else "ok",
        "scope_coverage": "insufficient_coverage" if insufficient else "full",
        "mismatch_count": mismatch_count,
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
            "log_base_paths": len([path for path in paths if path.enabled]),
        },
        "health": metrics_defaults.empty_health_page(),
        "attention": [],
        "coverage": {},
        "reconciliation": reconciliation,
        "diagnostics": metrics_defaults.empty_diagnostics_page(),
        "operations": operations,
        "failures": metrics_defaults.empty_failures_page(),
        "performance": metrics_defaults.empty_performance_page(),
        "volume": metrics_defaults.empty_volume_page(),
        "maintenance": metrics_defaults.empty_maintenance_page(),
        "freshness": metrics_defaults.empty_freshness_page(),
        "errors": [],
    }
