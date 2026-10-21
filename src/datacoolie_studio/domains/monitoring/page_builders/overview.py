from __future__ import annotations

from datetime import tzinfo
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.metrics import page_defaults as metrics_defaults
import datacoolie_studio.domains.monitoring.metrics.overview_projections as metrics_overview
from datacoolie_studio.domains.monitoring.metrics.aggregates import rate as metrics_rate
from datacoolie_studio.domains.monitoring.metrics.time_buckets import trend_context as metrics_trend_context
from datacoolie_studio.domains.monitoring.metrics.health import environment_health
from datacoolie_studio.domains.monitoring.read_models.overview import overview_read_model
from datacoolie_studio.domains.monitoring.page_builders.common import nullable_float, nullable_int, prefixed_duration_stats


def build_overview_page(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    trend_context = metrics_trend_context(filters, [], timezone_info)
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

    operations = metrics_defaults.empty_operations_page()
    operations["windows"] = aggregate["windows"]
    operations["jobs_by_date_status"] = metrics_overview._status_by_date_counts(
        list(aggregate["job_status_trend"]), trend_context,
    )
    operations["dataflows_by_date_status"] = metrics_overview._status_by_date_counts(
        list(aggregate["dataflow_status_trend"]), trend_context,
    )
    operations["jobs_by_engine_provider"] = aggregate["runtime_contexts"]
    operations["job_runs_by_operation_type"] = aggregate["job_operation_health"]
    operations["job_runs_by_dataflow_operation_type"] = aggregate["job_operation_health"]
    operations["dataflow_runs_by_operation_type"] = aggregate["dataflow_operation_health"]
    operations["phase_health"] = aggregate["phase_health"]
    operations["job_duration_stats"] = prefixed_duration_stats(values, "job")
    operations["dataflow_duration_stats"] = prefixed_duration_stats(values, "dataflow")
    operations["kpis"] = {
        "total_jobs": job_records,
        "total_succeeded": job_succeeded,
        "job_success_rate": metrics_rate(job_succeeded, job_succeeded + job_failed),
        "job_failure_rate": metrics_rate(job_failed, job_succeeded + job_failed),
        "job_skip_rate": metrics_rate(int(values.get("job_skipped") or 0), job_records),
        "total_rows_processed": values.get("total_rows_processed"),
        "observed_job_rows_read": values.get("observed_job_rows_read"),
        "job_rows_read_measured": values.get("job_rows_read_measured"),
        "job_rows_read_eligible": values.get("job_rows_read_eligible"),
        "observed_job_rows_written": values.get("observed_job_rows_written"),
        "job_rows_written_measured": values.get("job_rows_written_measured"),
        "job_rows_written_eligible": values.get("job_rows_written_eligible"),
        "total_failures": job_failed,
        "total_skipped": nullable_int(values.get("job_child_skipped")),
        "total_pending": nullable_int(values.get("job_pending")),
        "total_running": nullable_int(values.get("job_running")),
        "avg_duration_seconds": nullable_float(values.get("job_avg_duration_seconds")),
        "p95_duration_seconds": nullable_float(values.get("job_p95_duration_seconds")),
    }
    operations["dataflow_kpis"] = {
        "total_dataflows": dataflow_records,
        "succeeded": dataflow_succeeded,
        "failed": dataflow_failed,
        "skipped": int(values.get("dataflow_skipped") or 0),
        "pending": int(values.get("dataflow_pending") or 0),
        "running": int(values.get("dataflow_running") or 0),
        "success_rate": metrics_rate(
            dataflow_succeeded, dataflow_succeeded + dataflow_failed,
        ),
        "failure_rate": metrics_rate(
            dataflow_failed, dataflow_succeeded + dataflow_failed,
        ),
        "skip_rate": metrics_rate(int(values.get("dataflow_skipped") or 0), dataflow_records),
        "pending_rate": metrics_rate(int(values.get("dataflow_pending") or 0), dataflow_records),
        "running_rate": metrics_rate(int(values.get("dataflow_running") or 0), dataflow_records),
        "total_bytes_written": values.get("total_bytes_written"),
        "observed_bytes_written": values.get("observed_bytes_written"),
        "bytes_written_measured": values.get("bytes_written_measured"),
        "bytes_written_eligible": values.get("bytes_written_eligible"),
        "total_rows_read": values.get("total_rows_read"),
        "observed_rows_read": values.get("observed_rows_read"),
        "rows_read_measured": values.get("rows_read_measured"),
        "rows_read_eligible": values.get("rows_read_eligible"),
        "total_rows_written": values.get("total_rows_written"),
        "observed_rows_written": values.get("observed_rows_written"),
        "rows_written_measured": values.get("rows_written_measured"),
        "rows_written_eligible": values.get("rows_written_eligible"),
        "avg_duration_seconds": nullable_float(values.get("dataflow_avg_duration_seconds")),
        "p95_duration_seconds": nullable_float(values.get("dataflow_p95_duration_seconds")),
        "active_engines": int(values.get("dataflow_active_engines") or 0),
    }

    health_values = aggregate.get("health") or {}
    enabled_log_base_paths = len([path for path in paths if path.enabled])
    coverage_status = (
        "missing_sources" if not enabled_log_base_paths
        else "no_records" if not dataflow_records and not job_records
        else "partial" if not dataflow_records or not job_records
        else "ok"
    )
    coverage = {
        "enabled_log_base_paths": enabled_log_base_paths,
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
        **metrics_defaults.empty_failures_page(),
        "error_categories": aggregate["error_categories"],
        "top_failing_dataflows": aggregate["top_failing_dataflows"],
    }
    dataflow_duration = operations["dataflow_duration_stats"]
    p50 = nullable_float(dataflow_duration.get("p50_duration_seconds"))
    p95 = nullable_float(dataflow_duration.get("p95_duration_seconds"))
    performance = metrics_defaults.empty_performance_page()
    performance["kpis"] = {
        "p50_duration_seconds": p50,
        "p95_duration_seconds": p95,
        "duration_pressure_ratio": p95 / p50 if p50 and p95 is not None else None,
        "optimization_candidate_count": int(performance_attention.get("optimization_candidate_count") or 0),
    }
    maintenance = metrics_defaults.empty_maintenance_page()
    maintenance["kpis"] = {
        "coverage_missing_tables": int(maintenance_attention.get("coverage_missing_tables") or 0),
        "lagged_tables": int(maintenance_attention.get("lagged_tables") or 0),
        "latest_active_tables": int(maintenance_attention.get("latest_active_tables") or 0),
    }
    freshness = metrics_defaults.empty_freshness_page()
    freshness["kpis"] = {
        "stale_candidates": int(freshness_attention.get("stale_candidates") or 0),
        "watermark_unchanged_runs": int(freshness_attention.get("watermark_unchanged_runs") or 0),
    }
    diagnostics = metrics_defaults.empty_diagnostics_page()
    diagnostics["kpis"] = {
        "orphan_dataflow_job_ids": int(health_values.get("orphan_dataflow_job_ids") or 0),
        "jobs_without_dataflow_records": int(health_values.get("jobs_without_dataflow_records") or 0),
        "cache_warning_count": 0,
    }
    attention = metrics_overview._attention_queue(
        failures, performance, maintenance, coverage, reconciliation,
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
            "log_base_paths": enabled_log_base_paths,
        },
        "health": health,
        "attention": attention,
        "coverage": {},
        "reconciliation": {},
        "diagnostics": metrics_defaults.empty_diagnostics_page(),
        "operations": operations,
        "failures": {**metrics_defaults.empty_failures_page(), "error_categories": aggregate["error_categories"]},
        "performance": metrics_defaults.empty_performance_page(),
        "volume": {
            **metrics_defaults.empty_volume_page(),
            "rows_by_date": aggregate["rows_by_date"],
            "bytes_by_date": aggregate["bytes_by_date"],
        },
        "maintenance": metrics_defaults.empty_maintenance_page(),
        "freshness": metrics_defaults.empty_freshness_page(),
        "errors": [],
    }
