from __future__ import annotations

from datetime import tzinfo
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.metrics.time_buckets import trend_context as metrics_trend_context
from datacoolie_studio.domains.monitoring.read_models.freshness import freshness_read_model
from datacoolie_studio.domains.monitoring.page_builders.common import empty_internal_page


def build_freshness_page(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    trend_context = metrics_trend_context(filters, [], timezone_info)
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
        "latest_status_issue_dataflows", "latest_watermark_unknown_dataflows",
        "latest_watermark_issue_dataflows", "latest_check_missing_dataflows",
        "latest_watermark_changed_dataflows", "latest_watermark_invalid_dataflows",
        "latest_watermark_incomplete_dataflows",
        "watermark_enabled_dataflows", "watermark_coverage_rate", "watermark_advanced_runs",
        "watermark_enabled_runs", "watermark_rate_runs",
        "watermark_initialized_runs", "watermark_unchanged_runs", "watermark_changed_runs",
        "watermark_empty_runs",
        "watermark_incomplete_runs", "watermark_invalid_runs", "watermark_adjusted_runs",
        "watermark_unknown_runs", "watermark_not_requested_runs",
        "watermark_not_attempted_runs", "watermark_no_candidate_runs",
        "watermark_save_error_runs",
        "watermark_advanced_rate", "skipped_streak_dataflows", "skipped_streak_threshold",
        "stale_candidates", "stale_dataflows", "stale_threshold_days", "stale_dataflow_rate",
        "min_age_days", "p50_age_days", "p95_age_days", "max_age_days",
        "min_age_seconds", "p50_age_seconds", "p95_age_seconds", "max_age_seconds",
    }
    kpis = {key: values.get(key) for key in kpi_keys}
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
        "read_boundary_adjustment_by_dataflow": aggregate["read_boundary_adjustment_by_dataflow"],
    }
    return empty_internal_page(
        paths, values, trend_context, timezone_label, timezone_source,
        freshness=freshness,
    )
