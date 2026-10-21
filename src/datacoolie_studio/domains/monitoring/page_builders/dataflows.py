from __future__ import annotations

from datetime import tzinfo
from typing import Any
from datacoolie_studio.domains.monitoring.page_builders.common import nullable_float

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.metrics import page_defaults as metrics_defaults
import datacoolie_studio.domains.monitoring.metrics.overview_projections as metrics_overview
from datacoolie_studio.domains.monitoring.metrics.time_buckets import trend_context as metrics_trend_context
from datacoolie_studio.domains.monitoring.read_models.dataflows import dataflows_read_model


def _nullable_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def build_dataflows_page(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    trend_context = metrics_trend_context(filters, [], timezone_info)
    aggregate = dataflows_read_model(
        paths,
        filters,
        grain=str(trend_context["effective_grain"]),
        timezone_name=str(getattr(timezone_info, "key", "UTC")),
        analytics_context=analytics_context,
    )
    summary_values = aggregate.get("summary") or {}
    operations = metrics_defaults.empty_operations_page()
    operations["windows"] = aggregate["windows"]
    operations["dataflows_by_date_status"] = metrics_overview._status_by_date_counts(
        list(aggregate["status_trend"]),
        trend_context,
    )
    operations["dataflow_duration_by_stage"] = aggregate["duration_by_stage"]
    operations["phase_health_by_stage"] = aggregate["phase_health_by_stage"]
    operations["dataflow_endpoint_health"] = aggregate["endpoint_health"]
    operations["dataflow_name_status_health"] = aggregate["name_status_health"]
    duration_stats = {
        "count": int(summary_values.get("duration_count") or 0),
        "avg_duration_seconds": nullable_float(summary_values.get("avg_duration_seconds")),
        "q1_duration_seconds": nullable_float(summary_values.get("q1_duration_seconds")),
        "p50_duration_seconds": nullable_float(summary_values.get("p50_duration_seconds")),
        "q3_duration_seconds": nullable_float(summary_values.get("q3_duration_seconds")),
        "p95_duration_seconds": nullable_float(summary_values.get("p95_duration_seconds")),
        "p99_duration_seconds": nullable_float(summary_values.get("p99_duration_seconds")),
        "max_duration_seconds": nullable_float(summary_values.get("max_duration_seconds")),
    }
    operations["dataflow_duration_stats"] = duration_stats
    operations["dataflow_kpis"] = {
        "total_dataflows": int(summary_values.get("total_dataflows") or 0),
        "succeeded": int(summary_values.get("succeeded") or 0),
        "failed": int(summary_values.get("failed") or 0),
        "skipped": int(summary_values.get("skipped") or 0),
        "pending": int(summary_values.get("pending") or 0),
        "running": int(summary_values.get("running") or 0),
        "success_rate": _nullable_float(summary_values.get("success_rate")),
        "failure_rate": _nullable_float(summary_values.get("failure_rate")),
        "skip_rate": _nullable_float(summary_values.get("skip_rate")),
        "pending_rate": _nullable_float(summary_values.get("pending_rate")),
        "running_rate": _nullable_float(summary_values.get("running_rate")),
        "total_bytes_written": summary_values.get("total_bytes_written"),
        "observed_bytes_written": summary_values.get("observed_bytes_written"),
        "bytes_written_measured": summary_values.get("bytes_written_measured"),
        "bytes_written_eligible": summary_values.get("bytes_written_eligible"),
        "avg_duration_seconds": duration_stats["avg_duration_seconds"],
        "p95_duration_seconds": duration_stats["p95_duration_seconds"],
        "active_engines": int(summary_values.get("dataflow_active_engines") or 0),
    }
    volume = metrics_defaults.empty_volume_page()
    volume["kpis"] = {
        "total_rows_read": summary_values.get("total_rows_read"),
        "observed_rows_read": summary_values.get("observed_rows_read"),
        "rows_read_measured": summary_values.get("rows_read_measured"),
        "rows_read_eligible": summary_values.get("rows_read_eligible"),
        "total_rows_written": summary_values.get("total_rows_written"),
        "observed_rows_written": summary_values.get("observed_rows_written"),
        "rows_written_measured": summary_values.get("rows_written_measured"),
        "rows_written_eligible": summary_values.get("rows_written_eligible"),
        "net_bytes_change": summary_values.get("net_bytes_change"),
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
        "volume": volume,
        "maintenance": metrics_defaults.empty_maintenance_page(),
        "freshness": metrics_defaults.empty_freshness_page(),
        "errors": [],
    }
