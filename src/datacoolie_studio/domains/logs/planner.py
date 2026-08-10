from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from datacoolie_studio.domains.logs.discovery import (
    DiscoveredLogFile,
    DiscoveredPartition,
    LogSyncMode,
    LogSyncSpec,
    discover_partition_files,
    discover_partitions,
)
from datacoolie_studio.domains.logs.partition import (
    ParsedPartition,
    PartitionGranularity,
    PartitionLayout,
    PartitionValue,
    partition_datetime,
)
from datacoolie_studio.domains.storage.adapters import (
    StorageRevision,
    StorageAdapter,
)
from datacoolie_studio.domains.storage.uri import join_uri

LayoutStatus = Literal["learned", "unpartitioned", "pending"]


@dataclass(frozen=True)
class StreamDefinition:
    stream_kind: str
    root_uri: str
    suffix: str
    name_prefix: str | None = None
    manifest_only: bool = False


@dataclass(frozen=True)
class PlannerState:
    stream_kind: str
    root_uri: str
    layout_status: LayoutStatus
    partition_format: str | None = None
    partition_granularity: PartitionGranularity | None = None
    checkpoint_partition_value: PartitionValue | None = None
    boundary_last_modified: datetime | None = None
    last_scanned_partition_value: PartitionValue | None = None

    @property
    def layout(self) -> PartitionLayout | None:
        if self.layout_status == "pending":
            return None
        granularity = (
            PartitionGranularity.UNPARTITIONED
            if self.layout_status == "unpartitioned"
            else self.partition_granularity
        )
        if granularity is None:
            return None
        return PartitionLayout(self.partition_format, granularity)


@dataclass(frozen=True)
class StreamPlan:
    definition: StreamDefinition
    state: PlannerState
    files: tuple[DiscoveredLogFile, ...]
    candidates: tuple[DiscoveredLogFile, ...]
    incremental_partition_values: tuple[PartitionValue, ...]
    lookback_partition_values: tuple[PartitionValue, ...]

    @property
    def scanned_partition_count(self) -> int:
        return len(
            {
                *self.incremental_partition_values,
                *self.lookback_partition_values,
            }
        )


def plan_stream_sync(
    adapter: StorageAdapter,
    definition: StreamDefinition,
    *,
    state: PlannerState | None,
    manifest: dict[str, StorageRevision],
    spec: LogSyncSpec,
    today: date | datetime | None = None,
) -> StreamPlan:
    current_partition = partition_datetime(today or datetime.now(timezone.utc))
    if state is not None and state.root_uri != definition.root_uri:
        raise ValueError(
            f"Persisted root for {definition.stream_kind} differs from source configuration"
        )
    if state is None or state.layout_status == "pending":
        return _initial_plan(
            adapter,
            definition,
            manifest=manifest,
            current_partition=current_partition,
        )
    layout = state.layout
    if layout is None:
        raise ValueError(f"Invalid persisted layout for {definition.stream_kind}")

    incremental_values = _incremental_values(layout, state, current_partition)
    lookback_values = _lookback_values(layout, spec)
    incremental_files = _files_for_values(
        adapter,
        definition,
        layout,
        incremental_values,
    )
    lookback_files = _files_for_values(
        adapter,
        definition,
        layout,
        lookback_values,
    )
    all_files = _deduplicate_files(incremental_files, lookback_files)
    candidates = _changed_files(all_files, manifest)
    next_state = _next_state(
        state,
        definition,
        layout,
        incremental_files,
        incremental_values,
    )
    return StreamPlan(
        definition=definition,
        state=next_state,
        files=tuple(all_files),
        candidates=tuple(candidates),
        incremental_partition_values=incremental_values,
        lookback_partition_values=lookback_values,
    )


def _initial_plan(
    adapter: StorageAdapter,
    definition: StreamDefinition,
    *,
    manifest: dict[str, StorageRevision],
    current_partition: PartitionValue,
) -> StreamPlan:
    partitions = discover_partitions(adapter, definition.root_uri)
    if partitions:
        first = partitions[0].partition
        layout = PartitionLayout(
            first.partition_format,
            first.partition_granularity,
        )
        files = _filter_files(
            discover_partition_files(
                adapter,
                partitions,
                suffix=definition.suffix,
            ),
            definition,
        )
        incremental_values = tuple(
            sorted({item.partition.partition_value for item in partitions})
        )
        state = PlannerState(
            stream_kind=definition.stream_kind,
            root_uri=definition.root_uri,
            layout_status="learned",
            partition_format=layout.partition_format,
            partition_granularity=layout.granularity,
        )
    else:
        layout = PartitionLayout(None, PartitionGranularity.UNPARTITIONED)
        files = _files_for_values(
            adapter,
            definition,
            layout,
            (current_partition,),
        )
        incremental_values = (layout.normalize(current_partition),)
        state = PlannerState(
            stream_kind=definition.stream_kind,
            root_uri=definition.root_uri,
            layout_status="unpartitioned" if files else "pending",
            partition_granularity=(
                PartitionGranularity.UNPARTITIONED if files else None
            ),
        )
    if state.layout_status != "pending":
        scanned_values = tuple(
            sorted(
                {
                    *incremental_values,
                    layout.normalize(current_partition),
                }
            )
        )
        state = _next_state(
            state,
            definition,
            layout,
            files,
            scanned_values,
        )
    # The initial bounded learn already covers every discovered partition.
    # Exact lookback rendering starts after the learned state is committed.
    lookback_values: tuple[PartitionValue, ...] = ()
    all_files = _deduplicate_files(files)
    return StreamPlan(
        definition=definition,
        state=state,
        files=tuple(all_files),
        candidates=tuple(_changed_files(all_files, manifest)),
        incremental_partition_values=incremental_values,
        lookback_partition_values=lookback_values,
    )


def _incremental_values(
    layout: PartitionLayout,
    state: PlannerState,
    current_partition: PartitionValue,
) -> tuple[PartitionValue, ...]:
    if layout.granularity is PartitionGranularity.UNPARTITIONED:
        return (layout.normalize(current_partition),)
    # Always revisit the latest partition known to contain files so late writes
    # and in-place replacements remain visible. Scan forward only from the last
    # attempted partition to avoid repeatedly walking every empty partition
    # between the checkpoint and today.
    checkpoint = state.checkpoint_partition_value
    forward_start = state.last_scanned_partition_value or checkpoint or current_partition
    values = set(layout.values(forward_start, current_partition))
    if checkpoint is not None:
        values.add(layout.normalize(checkpoint))
    if layout.granularity is PartitionGranularity.HOUR:
        # A run can start near an hour boundary and close after the next hour has
        # already produced files. Revisit one completed hour so that immutable
        # late arrivals are discovered without turning every sync into a day scan.
        values.add(
            layout.normalize(
                partition_datetime(current_partition) - timedelta(hours=1)
            )
        )
    return tuple(sorted(values))


def _lookback_values(
    layout: PartitionLayout,
    spec: LogSyncSpec,
) -> tuple[PartitionValue, ...]:
    if spec.mode is not LogSyncMode.INCREMENTAL_WITH_LOOKBACK:
        return ()
    if spec.lookback is None:
        return ()
    from_partition = partition_datetime(spec.lookback.from_partition)
    to_partition = partition_datetime(spec.lookback.to_partition)
    if layout.granularity is PartitionGranularity.HOUR:
        to_partition += timedelta(hours=23)
    return layout.values(from_partition, to_partition)


def _files_for_values(
    adapter: StorageAdapter,
    definition: StreamDefinition,
    layout: PartitionLayout,
    values: tuple[PartitionValue, ...],
) -> list[DiscoveredLogFile]:
    partitions: list[DiscoveredPartition] = []
    for value in values:
        relative_path = layout.render(value)
        partition_uri = (
            join_uri(definition.root_uri, relative_path)
            if relative_path
            else definition.root_uri
        )
        partitions.append(
            DiscoveredPartition(
                uri=partition_uri,
                partition=ParsedPartition(
                    partition_value=layout.normalize(value),
                    raw_partition_path=relative_path,
                    partition_granularity=layout.granularity,
                    partition_format=layout.partition_format or "",
                ),
            )
        )
    return _filter_files(
        discover_partition_files(
            adapter,
            partitions,
            suffix=definition.suffix,
        ),
        definition,
    )


def _filter_files(
    files: list[DiscoveredLogFile],
    definition: StreamDefinition,
) -> list[DiscoveredLogFile]:
    return [
        item
        for item in files
        if _matches_name(item.canonical_uri.rsplit("/", 1)[-1], definition)
    ]


def _matches_name(name: str, definition: StreamDefinition) -> bool:
    return definition.name_prefix is None or name.startswith(definition.name_prefix)


def _changed_files(
    files: list[DiscoveredLogFile],
    manifest: dict[str, StorageRevision],
) -> list[DiscoveredLogFile]:
    return [
        item
        for item in files
        if (existing := manifest.get(item.canonical_uri)) is None
        or not item.revision.same_content_as(existing)
    ]


def _deduplicate_files(
    *groups: list[DiscoveredLogFile],
) -> list[DiscoveredLogFile]:
    by_uri = {
        item.canonical_uri: item
        for group in groups
        for item in group
    }
    return sorted(
        by_uri.values(),
        key=lambda item: (item.partition.partition_value, item.canonical_uri),
    )


def _next_state(
    previous: PlannerState,
    definition: StreamDefinition,
    layout: PartitionLayout,
    incremental_files: list[DiscoveredLogFile],
    incremental_values: tuple[PartitionValue, ...],
) -> PlannerState:
    checkpoint = (
        layout.normalize(previous.checkpoint_partition_value)
        if previous.checkpoint_partition_value is not None
        else None
    )
    boundary = previous.boundary_last_modified
    if incremental_files:
        checkpoint = max(
            [
                value
                for value in (
                    checkpoint,
                    max(
                        item.partition.partition_value
                        for item in incremental_files
                    ),
                )
                if value is not None
            ]
        )
        latest_revisions = [
            item.revision.last_modified
            for item in incremental_files
            if item.partition.partition_value == checkpoint
        ]
        if latest_revisions:
            candidate_boundary = max(latest_revisions)
            boundary = (
                max(boundary, candidate_boundary)
                if boundary is not None
                else candidate_boundary
            )
    last_scanned = (
        layout.normalize(previous.last_scanned_partition_value)
        if previous.last_scanned_partition_value is not None
        else None
    )
    if incremental_values:
        scanned = max(incremental_values)
        last_scanned = (
            max(last_scanned, scanned)
            if last_scanned is not None
            else scanned
        )
    return PlannerState(
        stream_kind=definition.stream_kind,
        root_uri=definition.root_uri,
        layout_status=previous.layout_status,
        partition_format=layout.partition_format,
        partition_granularity=layout.granularity,
        checkpoint_partition_value=checkpoint,
        boundary_last_modified=boundary,
        last_scanned_partition_value=last_scanned,
    )
