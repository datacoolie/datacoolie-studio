from __future__ import annotations

from datetime import tzinfo
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.metrics.time_buckets import trend_context as metrics_trend_context
from datacoolie_studio.domains.monitoring.read_models.destinations import destination_read_model
from datacoolie_studio.domains.monitoring.page_builders.common import empty_internal_page


def build_maintenance_page(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
    identity_request=None,
) -> dict[str, Any]:
    trend_context = metrics_trend_context(filters, [], timezone_info)
    aggregate = destination_read_model(
        paths, filters,
        grain=str(trend_context["effective_grain"]),
        timezone_name=str(getattr(timezone_info, "key", "UTC")),
        analytics_context=analytics_context,
        identity_request=identity_request,
    )
    values = aggregate.get("summary") or {}
    values["dataflow_records"] = values.get("operation_count", 0)
    kpis = dict(values)
    maintenance = {
        "kpis": kpis,
        **{key: aggregate[key] for key in (
            "status_by_date", "reclaim_by_date", "table_outcome", "table_efficiency_points", "operation_type_counts", "maintenance_status_counts",
            "format_comparison", "bytes_reclaimed_by_date",
        )},
    }
    return empty_internal_page(
        paths, values, trend_context, timezone_label, timezone_source,
        maintenance=maintenance,
    )
