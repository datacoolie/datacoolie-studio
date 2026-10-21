from __future__ import annotations

from datetime import tzinfo
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.metrics.time_buckets import trend_context as metrics_trend_context
from datacoolie_studio.domains.monitoring.read_models.volume import volume_read_model
from datacoolie_studio.domains.monitoring.page_builders.common import empty_internal_page


def build_volume_page(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_info: tzinfo,
    timezone_label: str,
    timezone_source: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    trend_context = metrics_trend_context(filters, [], timezone_info)
    aggregate = volume_read_model(
        paths, filters,
        grain=str(trend_context["effective_grain"]),
        timezone_name=str(getattr(timezone_info, "key", "UTC")),
        analytics_context=analytics_context,
    )
    values = aggregate.get("summary") or {}
    kpis = {
        key: values.get(key)
        for key in (
            "total_rows_read", "total_rows_written", "total_lakehouse_rows_written",
            "total_non_lakehouse_rows_written", "total_rows_inserted",
            "total_rows_updated", "total_rows_deleted", "lakehouse_destination_run_count",
            "lakehouse_destination_share", "files_added", "files_removed",
            "total_bytes_added", "total_bytes_removed", "total_bytes_saved",
            "net_bytes_change", "avg_bytes_per_file_added", "skip_count", "skip_rate",
            "observed_rows_read", "rows_read_measured", "rows_read_eligible",
            "observed_rows_written", "rows_written_measured", "rows_written_eligible",
            "observed_lakehouse_rows_written", "lakehouse_rows_written_measured",
            "lakehouse_rows_written_eligible",
            "observed_rows_inserted", "rows_inserted_measured", "rows_inserted_eligible",
            "observed_rows_updated", "rows_updated_measured", "rows_updated_eligible",
            "observed_rows_deleted", "rows_deleted_measured", "rows_deleted_eligible",
            "observed_files_added", "files_added_measured", "files_added_eligible",
            "observed_files_removed", "files_removed_measured", "files_removed_eligible",
            "observed_bytes_added", "bytes_added_measured", "bytes_added_eligible",
            "observed_bytes_removed", "bytes_removed_measured", "bytes_removed_eligible",
            "observed_net_bytes_change", "net_bytes_change_measured", "net_bytes_change_eligible",
            "bytes_per_file_measured", "bytes_per_file_eligible",
            "high_volume_run_count", "high_volume_candidate_run_count",
            "high_volume_rows_count", "high_volume_rows_written_count",
            "high_volume_lakehouse_rows_count", "high_volume_bytes_count",
            "high_volume_files_count", "high_volume_dataflow_count",
        )
    }
    volume = {
        "kpis": kpis,
        "rows_by_date": aggregate["rows_by_date"],
        "bytes_by_date": aggregate["bytes_by_date"],
        "volume_by_load_type": aggregate["volume_by_load_type"],
        "volume_by_workload_type": aggregate["volume_by_workload_type"],
        "route_volume": aggregate["route_volume"],
        "top_dataflows_by_rows_read": aggregate["top_dataflows_by_rows_read"],
        "top_dataflows_by_rows_written": aggregate["top_dataflows_by_rows_written"],
        "top_dataflows_by_lakehouse_rows_written": aggregate["top_dataflows_by_lakehouse_rows_written"],
        "top_dataflows_by_bytes_added": aggregate["top_dataflows_by_bytes_added"],
        "top_dataflows_by_net_bytes": aggregate["top_dataflows_by_net_bytes"],
    }
    return empty_internal_page(
        paths, values, trend_context, timezone_label, timezone_source,
        volume=volume,
    )
