from __future__ import annotations

from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.metrics import page_defaults as metrics_defaults


def prefixed_duration_stats(values: dict[str, Any], prefix: str) -> dict[str, float | int | None]:
    return {
        "count": int(values.get(f"{prefix}_duration_count") or 0),
        "avg_duration_seconds": nullable_float(values.get(f"{prefix}_avg_duration_seconds")),
        "q1_duration_seconds": nullable_float(values.get(f"{prefix}_q1_duration_seconds")),
        "p50_duration_seconds": nullable_float(values.get(f"{prefix}_p50_duration_seconds")),
        "q3_duration_seconds": nullable_float(values.get(f"{prefix}_q3_duration_seconds")),
        "p75_duration_seconds": nullable_float(values.get(f"{prefix}_q3_duration_seconds")),
        "p95_duration_seconds": nullable_float(values.get(f"{prefix}_p95_duration_seconds")),
        "p99_duration_seconds": nullable_float(values.get(f"{prefix}_p99_duration_seconds")),
        "max_duration_seconds": nullable_float(values.get(f"{prefix}_max_duration_seconds")),
    }


def nullable_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def nullable_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def empty_internal_page(
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
            "log_base_paths": len([path for path in paths if path.enabled]),
        },
        "health": metrics_defaults.empty_health_page(), "attention": [], "coverage": {},
        "reconciliation": {}, "diagnostics": metrics_defaults.empty_diagnostics_page(),
        "operations": metrics_defaults.empty_operations_page(),
        "failures": metrics_defaults.empty_failures_page(),
        "performance": metrics_defaults.empty_performance_page(),
        "volume": metrics_defaults.empty_volume_page(),
        "maintenance": metrics_defaults.empty_maintenance_page(),
        "freshness": metrics_defaults.empty_freshness_page(),
        "errors": [],
    }
    result.update(sections)
    return result
