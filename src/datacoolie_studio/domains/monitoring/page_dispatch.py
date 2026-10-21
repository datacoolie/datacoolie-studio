from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.metrics.filtering import (
    normalize_monitoring_filters_for_timezone,
)
from datacoolie_studio.domains.monitoring.page_builders.dataflows import build_dataflows_page
from datacoolie_studio.domains.monitoring.page_builders.diagnostics import build_diagnostics_page
from datacoolie_studio.domains.monitoring.page_builders.environment_overview import build_environment_overview_page
from datacoolie_studio.domains.monitoring.page_builders.failures import build_failures_page
from datacoolie_studio.domains.monitoring.page_builders.freshness import build_freshness_page
from datacoolie_studio.domains.monitoring.page_builders.jobs import build_jobs_page
from datacoolie_studio.domains.monitoring.page_builders.maintenance import build_maintenance_page
from datacoolie_studio.domains.monitoring.page_builders.overview import build_overview_page
from datacoolie_studio.domains.monitoring.page_builders.performance import build_performance_page
from datacoolie_studio.domains.monitoring.page_builders.volume import build_volume_page
from datacoolie_studio.domains.monitoring.page_contracts import MONITORING_PAGES


def build_monitoring_page(
    paths: list[EnvironmentSource],
    page: str,
    filters: dict[str, str] | None = None,
    timezone_info: tzinfo | None = None,
    timezone_label: str | None = None,
    timezone_source: str = "server_default",
    analytics_context: tuple[Any, list[int], str] | None = None,
    identity_request=None,
) -> dict[str, Any]:
    """Build only the read model required by one Monitoring page."""
    if page not in MONITORING_PAGES:
        raise ValueError(f"Unknown Monitoring page: {page}")
    active_timezone = timezone_info or timezone.utc
    active_timezone_label = timezone_label or "UTC"
    anchor = datetime.now(active_timezone)
    normalized_filters = normalize_monitoring_filters_for_timezone(
        filters or {}, timezone_info=active_timezone, now=anchor,
    )
    if page == "environment-overview":
        return build_environment_overview_page(
            paths,
            normalized_filters,
            active_timezone,
            active_timezone_label,
            timezone_source,
            analytics_context,
        )
    if page == "diagnostics":
        return build_diagnostics_page(
            paths,
            normalized_filters,
            active_timezone,
            active_timezone_label,
            timezone_source,
            analytics_context,
            as_of=anchor,
        )
    if page == "overview":
        return build_overview_page(
            paths,
            normalized_filters,
            active_timezone,
            active_timezone_label,
            timezone_source,
            analytics_context,
        )
    if page == "dataflows":
        return build_dataflows_page(
            paths,
            normalized_filters,
            active_timezone,
            active_timezone_label,
            timezone_source,
            analytics_context,
        )
    if page == "jobs":
        return build_jobs_page(
            paths,
            normalized_filters,
            active_timezone,
            active_timezone_label,
            timezone_source,
            analytics_context,
        )
    if page == "failures":
        return build_failures_page(
            paths,
            normalized_filters,
            active_timezone,
            active_timezone_label,
            timezone_source,
            analytics_context,
        )
    if page == "volume":
        return build_volume_page(
            paths,
            normalized_filters,
            active_timezone,
            active_timezone_label,
            timezone_source,
            analytics_context,
        )
    if page == "freshness":
        return build_freshness_page(
            paths,
            normalized_filters,
            active_timezone,
            active_timezone_label,
            timezone_source,
            analytics_context,
        )
    if page == "performance":
        return build_performance_page(
            paths,
            normalized_filters,
            active_timezone,
            active_timezone_label,
            timezone_source,
            analytics_context,
        )
    if page == "maintenance":
        return build_maintenance_page(
            paths,
            normalized_filters,
            active_timezone,
            active_timezone_label,
            timezone_source,
            analytics_context,
            identity_request,
        )
    raise AssertionError(f"Monitoring page is not routed to a SQL read model: {page}")
