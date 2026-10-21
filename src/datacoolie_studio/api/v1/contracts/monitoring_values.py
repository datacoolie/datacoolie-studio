"""Exact declared metric transport, separate from arbitrary raw JSON evidence."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BeforeValidator


def decimal_integer(value: Any) -> str:
    if isinstance(value, bool):
        raise ValueError("Boolean is not an integer metric")
    if isinstance(value, float):
        if not value.is_integer() or abs(value) > 2**53:
            raise ValueError("Integer metric was converted to an inexact float")
        value = int(value)
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise ValueError("Fractional integer metric")
        value = int(value)
    text = str(value)
    if not re.fullmatch(r"-?(0|[1-9][0-9]*)", text):
        raise ValueError("Expected a decimal integer metric")
    return text


IntegerMetric = Annotated[str, BeforeValidator(decimal_integer)]


_INTEGER_FIELD = re.compile(
    r"(^count$|^total$|_count$|_records$|_markers$|^records$|_rows_|^rows_|_bytes_|^bytes_|_files_|^files_|"
    r"^total_|^child_|^failed$|^succeeded$|^skipped$|^running$|^pending$|"
    r"^expected$|^observed$|^difference$|^admitted$|^excluded$|^eligible$|^measured$|"
    r"^active_engines$|^active_metadata_providers$|^log_base_paths$|"
    r"^performance_(observed_)?processed_(rows|bytes)$)"
)


def exact_metric_transport(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        return {name: exact_metric_transport(item, key=name) for name, item in value.items()}
    if isinstance(value, list):
        return [exact_metric_transport(item, key=key) for item in value]
    if value is None or isinstance(value, bool) or key in {"raw_record_json", "row_count", "records", "total_records", "limit", "offset"}:
        return value
    if (key.endswith(("_measured_runs", "_paired_runs")) or key in {
        "comparable_runs", "efficiency_bytes_removed", "efficiency_files_removed",
        "operation_count", "active_destinations", "destinations_with_failures", "unresolved_destinations",
        "destination_failed_runs", "upstream_failed_runs", "unqualified_effect_runs", "matching_run_reports",
        "maintenance_succeeded", "maintenance_failed", "maintenance_skipped",
        "maintenance_qualified_success_runs",
        "snapshot_rows", "snapshot_files", "snapshot_bytes", "net_bytes",
        "net_bytes_observed", "snapshot_version",
        "paired_rows_written", "paired_files_added", "paired_bytes_added",
        "paired_rows_runs", "paired_files_runs", "paired_bytes_runs", "unknown",
        "etl", "maintenance", "replay",
        "maintenance_efficiency_bytes_removed", "maintenance_efficiency_files_removed",
        "maintenance_no_op_runs", "maintenance_comparable_runs",
        "paired_files_removed", "paired_bytes_removed", "paired_files_removed_runs", "paired_bytes_removed_runs",
        "log_records_dropped_reporting_jobs", "log_bytes_dropped_reporting_jobs",
        "log_records_dropped_observed_subtotal", "log_bytes_dropped_observed_subtotal",
        "log_loss_unknown_jobs",
        "present_values", "missing_values", "unknown_presence_values",
        "available_values", "missing_available_values", "required_fields",
        "absolute_difference", "affected_jobs",
    }) and isinstance(value, (int, float, Decimal, str)):
        if key == "snapshot_version" and isinstance(value,str) and not re.fullmatch(r"-?(0|[1-9][0-9]*)",value):
            return value
        return decimal_integer(value)
    if any(token in key for token in ("duration", "percent", "rate", "throughput", "per_second", "efficiency", "ratio", "age", "time", "_id", "_priority", "avg_", "average_")) or key in {
        "performance_processed_rows_paired_seconds",
        "performance_processed_bytes_paired_seconds",
    }:
        return value
    if (_INTEGER_FIELD.search(key) or key == "volume_net_bytes") and isinstance(value, (int, float, Decimal, str)):
        return decimal_integer(value)
    return value
