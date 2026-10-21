from __future__ import annotations

from datetime import tzinfo
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.metrics.time_buckets import trend_context as metrics_trend_context
from datacoolie_studio.domains.monitoring.read_models.performance import performance_read_model
from datacoolie_studio.domains.monitoring.page_builders.common import empty_internal_page, nullable_float


def build_performance_page(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    trend_context = metrics_trend_context(filters, [], timezone_info)
    aggregate = performance_read_model(
        paths, filters,
        grain=str(trend_context["effective_grain"]),
        timezone_name=str(getattr(timezone_info, "key", "UTC")),
        analytics_context=analytics_context,
    )
    values = aggregate.get("summary") or {}
    p50 = nullable_float(values.get("p50_duration_seconds"))
    p95 = nullable_float(values.get("p95_duration_seconds"))
    kpis = {
        key: values.get(key)
        for key in (
            "run_count", "total_run_time_seconds", "duration_runs_measured",
            "duration_runs_eligible", "avg_duration_seconds", "p50_duration_seconds",
            "workload_efficiency_pipeline_eligible", "workload_efficiency_pipeline_measured",
            "workload_efficiency_maintenance_eligible", "workload_efficiency_maintenance_measured",
            "p75_duration_seconds", "p95_duration_seconds", "p99_duration_seconds",
            "max_duration_seconds", "slowest_run_duration_seconds",
            "slowest_run_dataflow_name", "slowest_run_dataflow_id",
            "slowest_run_dataflow_run_id", "slowest_run_job_id",
            "slowest_run_start_time", "slowest_run_end_time",
            "slowest_run_stage", "slowest_run_operation_type",
            "slowest_run_status", "bottleneck_phase",
            "source_duration_percent", "transform_duration_percent",
            "destination_duration_percent", "overhead_duration_percent",
            "phase_runs_eligible", "phase_runs_measured", "phase_coverage_percent",
            "rows_read_per_second", "total_rows_read", "observed_rows_read",
            "rows_read_measured", "rows_read_eligible",
            "total_rows_written", "observed_rows_written",
            "rows_written_measured", "rows_written_eligible",
            "optimization_candidate_count", "slow_small_workload_count",
            "slow_small_maintenance_count", "high_overhead_count", "phase_skew_count",
        )
    }
    kpis["duration_pressure_ratio"] = round(p95 / p50, 3) if p50 and p95 is not None else None
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
    return empty_internal_page(
        paths, values, trend_context, timezone_label, timezone_source,
        performance=performance,
    )
