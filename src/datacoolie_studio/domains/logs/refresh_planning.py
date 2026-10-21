from __future__ import annotations

from datetime import date, datetime
from datacoolie_studio.db.models import LogStreamState
from datacoolie_studio.domains.logs.control import StreamStateUpdate
from datacoolie_studio.domains.logs.partition import PartitionGranularity, PartitionValue, partition_datetime
from datacoolie_studio.domains.logs.planner import PlannerState, StreamDefinition, StreamPlan
from datacoolie_studio.domains.storage.uri import join_uri, uri_basename


def stream_definitions(
    execution_uri: str,
    system_uri: str | None,
) -> tuple[StreamDefinition, ...]:
    definitions = [
        StreamDefinition(
            stream_kind="dataflow_json",
            root_uri=_log_stream_root(execution_uri, "dataflow_run_log"),
            suffix=".json",
        ),
        StreamDefinition(
            stream_kind="job_json",
            root_uri=_log_stream_root(execution_uri, "job_run_log"),
            suffix=".json",
        ),
    ]
    if system_uri:
        definitions.append(
            StreamDefinition(
                stream_kind="system_json",
                root_uri=system_uri,
                suffix=".json",
                manifest_only=True,
            )
        )
    return tuple(definitions)

def planner_state_from_row(row: LogStreamState | None) -> PlannerState | None:
    if row is None:
        return None
    granularity = (
        PartitionGranularity(row.partition_granularity)
        if row.partition_granularity
        else None
    )
    return PlannerState(
        stream_kind=row.stream_kind,
        root_uri=row.root_uri,
        layout_status=row.layout_status,  # type: ignore[arg-type]
        partition_format=row.partition_format,
        partition_granularity=granularity,
        last_scanned_partition_value=stored_partition_key(
            row.last_scanned_partition_key,
            None,
            granularity,
        ),
    )

def stream_state_update(plan: StreamPlan) -> StreamStateUpdate:
    state = plan.state
    return StreamStateUpdate(
        stream_kind=state.stream_kind,
        root_uri=state.root_uri,
        layout_status=state.layout_status,
        partition_format=state.partition_format,
        partition_granularity=(
            state.partition_granularity.value
            if state.partition_granularity is not None
            else None
        ),
        last_scanned_partition_value=state.last_scanned_partition_value,
    )

def stored_partition_key(
    key: str | None,
    fallback: date | None,
    granularity: PartitionGranularity | None,
) -> PartitionValue | None:
    if key:
        try:
            parsed = datetime.fromisoformat(key)
            if granularity is PartitionGranularity.HOUR:
                return partition_datetime(parsed)
            return fallback or parsed.date()
        except ValueError:
            pass
    return fallback

def partition_date(value: PartitionValue) -> date:
    return value.date() if isinstance(value, datetime) else value

def partition_key_text(value: PartitionValue) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return value.isoformat()

def _log_stream_root(root_uri: str, stream_name: str) -> str:
    if uri_basename(root_uri) == stream_name:
        return root_uri
    return join_uri(root_uri, stream_name)
