from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.metrics import page_defaults as metrics_defaults
import datacoolie_studio.domains.monitoring.metrics.diagnostics_projections as metrics_diagnostics
from datacoolie_studio.domains.monitoring.metrics.time_buckets import trend_context as metrics_trend_context
from datacoolie_studio.domains.monitoring.diagnostics_repository import diagnostics_read_model


def build_diagnostics_page(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    trend_context = metrics_trend_context(filters, [], timezone_info)
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
    trend_context = metrics_trend_context(filters, trend_rows, timezone_info)
    trend_context["effective_grain"] = aggregate.get("effective_grain") or trend_context["effective_grain"]
    diagnostics = dict(aggregate["diagnostics"])
    findings_summary = dict(diagnostics.get("findings_summary") or {})
    findings_summary["as_of"] = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    diagnostics["findings_summary"] = findings_summary
    diagnostics["record_evidence_by_date"] = fill_diagnostics_trend_buckets(
        list(diagnostics.get("record_evidence_by_date") or []),
        trend_context,
    )
    coverage = dict(aggregate["coverage"])
    coverage["enabled_log_base_paths"] = len([path for path in paths if path.enabled])
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
            "log_base_paths": len([path for path in paths if path.enabled]),
        },
        "health": metrics_defaults.empty_health_page(),
        "attention": [],
        "coverage": coverage,
        "reconciliation": aggregate["reconciliation"],
        "diagnostics": diagnostics,
        "operations": metrics_defaults.empty_operations_page(),
        "failures": metrics_defaults.empty_failures_page(),
        "performance": metrics_defaults.empty_performance_page(),
        "volume": metrics_defaults.empty_volume_page(),
        "maintenance": metrics_defaults.empty_maintenance_page(),
        "freshness": metrics_defaults.empty_freshness_page(),
        "errors": [],
    }


def fill_diagnostics_trend_buckets(
    rows: list[dict[str, Any]],
    trend_context: dict[str, Any],
) -> list[dict[str, Any]]:
    buckets = {str(row.get("bucket") or "unknown"): row for row in rows}
    for bucket in metrics_diagnostics._diagnostics_expected_trend_buckets(trend_context):
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
