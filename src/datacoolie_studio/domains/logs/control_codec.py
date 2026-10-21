from __future__ import annotations

from datetime import date, datetime
from typing import Any

from datacoolie_studio.core.time import parse_utc_datetime
from datacoolie_studio.domains.logs.control import StreamStateUpdate
from datacoolie_studio.domains.logs.partition import (
    PartitionGranularity,
    PartitionValue,
)
from datacoolie_studio.domains.logs.planner import StreamPlan


def deferred_control_payload(
    changed_files: list[dict[str, Any]],
    changed_system_files: list[dict[str, Any]],
    stream_plans: list[StreamPlan],
    revision: dict[str, Any],
    file_row_counts: dict[str, int],
    *,
    needs_publish: bool,
    replace_source_control: bool,
    removed_files: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "changed_files": [serialize_control_file_state(state) for state in changed_files],
        "changed_system_files": [
            serialize_control_file_state(state)
            for state in changed_system_files
        ],
        "removed_files": list(removed_files or []),
        "stream_states": [serialize_stream_state(plan) for plan in stream_plans],
        "revision": serialize_control_value(revision),
        "file_row_counts": {
            str(file_uri): int(row_count or 0)
            for file_uri, row_count in file_row_counts.items()
        },
        "needs_publish": bool(needs_publish),
        "replace_source_control": bool(replace_source_control),
    }


def serialize_control_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): serialize_control_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize_control_value(item) for item in value]
    return value


def serialize_control_file_state(state: dict[str, Any]) -> dict[str, Any]:
    return serialize_control_value(dict(state))


def deserialize_control_file_state(state: dict[str, Any]) -> dict[str, Any]:
    result = dict(state)
    for key in ("filename_job_id", "filename_job_id_alt"):
        value = result.get(key)
        if value is not None:
            result[key] = str(value)
    for key in ("partition_value", "run_date"):
        value = result.get(key)
        if isinstance(value, str):
            try:
                result[key] = date.fromisoformat(value[:10])
            except ValueError:
                result[key] = None
    for key in ("log_timestamp", "event_min_timestamp", "event_max_timestamp"):
        value = result.get(key)
        if isinstance(value, str):
            result[key] = parse_utc_datetime(value)
    return result


def serialize_stream_state(plan: StreamPlan) -> dict[str, Any]:
    state = plan.state
    return serialize_control_value(
        {
            "stream_kind": state.stream_kind,
            "root_uri": state.root_uri,
            "layout_status": state.layout_status,
            "partition_format": state.partition_format,
            "partition_granularity": (
                state.partition_granularity.value
                if state.partition_granularity is not None
                else None
            ),
            "last_scanned_partition_key": (
                state.last_scanned_partition_value.isoformat()
                if state.last_scanned_partition_value is not None
                else None
            ),
        }
    )


def deserialize_partition_value(
    value: Any,
    granularity: str | None,
) -> PartitionValue | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        if granularity == PartitionGranularity.HOUR.value and "T" in value:
            return datetime.fromisoformat(value)
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def deserialize_stream_state_update(state: dict[str, Any]) -> StreamStateUpdate:
    granularity = state.get("partition_granularity")
    granularity_value = str(granularity) if granularity is not None else None
    return StreamStateUpdate(
        stream_kind=str(state.get("stream_kind") or ""),
        root_uri=str(state.get("root_uri") or ""),
        layout_status=str(state.get("layout_status") or "pending"),
        partition_format=(
            str(state["partition_format"])
            if state.get("partition_format") is not None
            else None
        ),
        partition_granularity=granularity_value,
        last_scanned_partition_value=deserialize_partition_value(
            state.get("last_scanned_partition_key"),
            granularity_value,
        ),
    )
