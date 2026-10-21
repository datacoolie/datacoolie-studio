from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from datacoolie_studio.domains.logs.discovery import (
    DiscoveredLogFile,
    DiscoveredPartition,
    LogSyncMode,
    LogSyncSpec,
    PartitionFormatError,
    discover_partition_files,
)
from datacoolie_studio.domains.logs.partition import (
    ParsedPartition,
    PartitionGranularity,
    PartitionLayout,
    PartitionValue,
    parse_partition_path,
    partition_datetime,
)
from datacoolie_studio.domains.storage.adapters import (
    StorageRevision,
    StorageAdapter,
)
from datacoolie_studio.domains.storage.uri import join_uri
from datacoolie_studio.domains.storage.inventory import (
    StorageInventoryRequest,
    inventory,
)

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
    inventory_requests: int = 0
    inventory_pages: int = 0
    objects_inspected: int = 0
    stat_requests: int = 0
    removed_files: tuple[str, ...] = ()

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
    discovery_diagnostics: dict[str, int] = {}
    incremental_files = _files_for_values(
        adapter,
        definition,
        layout,
        incremental_values,
        diagnostics=discovery_diagnostics,
    )
    lookback_files = _files_for_values(
        adapter,
        definition,
        layout,
        lookback_values,
        diagnostics=discovery_diagnostics,
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
        inventory_requests=discovery_diagnostics.get("inventory_requests", 0),
        inventory_pages=discovery_diagnostics.get("inventory_pages", 0),
        objects_inspected=discovery_diagnostics.get("objects_inspected", 0),
        stat_requests=discovery_diagnostics.get("stat_requests", 0),
        removed_files=_removed_files(definition, all_files, manifest, layout=layout, scanned_values=(*incremental_values, *lookback_values)),
    )


def _initial_plan(
    adapter: StorageAdapter,
    definition: StreamDefinition,
    *,
    manifest: dict[str, StorageRevision],
    current_partition: PartitionValue,
) -> StreamPlan:
    discovery_diagnostics: dict[str, int] = {}
    files, partitions = _initial_inventory(
        adapter,
        definition,
        current_partition,
        diagnostics=discovery_diagnostics,
    )
    if partitions:
        first = partitions[0].partition
        layout = PartitionLayout(first.partition_format, first.partition_granularity)
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
            diagnostics=discovery_diagnostics,
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
        inventory_requests=discovery_diagnostics.get("inventory_requests", 0),
        inventory_pages=discovery_diagnostics.get("inventory_pages", 0),
        objects_inspected=discovery_diagnostics.get("objects_inspected", 0),
        stat_requests=discovery_diagnostics.get("stat_requests", 0),
        removed_files=_removed_files(definition, all_files, manifest),
    )


def _initial_inventory(
    adapter: StorageAdapter,
    definition: StreamDefinition,
    current_partition: PartitionValue,
    diagnostics: dict[str, int],
) -> tuple[list[DiscoveredLogFile], list[DiscoveredPartition]]:
    """Build the initial file/partition map from one recursive inventory.

    Initial discovery has no watermark, so a provider-side recursive file
    inventory is the only useful scan. It returns file revisions and lets the
    planner infer partition folders from each relative file URI. This avoids
    enumerating every directory first and then issuing one listing per folder.
    Incremental scans use the learned layout and do not call this path.
    """

    observed = inventory(
        adapter,
        StorageInventoryRequest(
            uri=definition.root_uri,
            purpose="logs",
            recursive=True,
            object_types=frozenset({"file"}),
            suffixes=frozenset({definition.suffix}),
            name_prefix=definition.name_prefix,
        ),
    )
    if observed.completeness != "complete":
        raise ValueError("Log refresh requires a complete file inventory")
    diagnostics.update(
        {
            "inventory_requests": diagnostics.get("inventory_requests", 0)
            + observed.requests,
            "inventory_pages": diagnostics.get("inventory_pages", 0)
            + observed.pages,
            "objects_inspected": diagnostics.get("objects_inspected", 0)
            + observed.objects_inspected,
        }
    )
    files: list[DiscoveredLogFile] = []
    partitions_by_uri: dict[str, DiscoveredPartition] = {}
    formats: set[str] = set()
    unpartitioned = False
    for item in observed.files:
        relative = _relative_uri(definition.root_uri, item.canonical_uri)
        directory = relative.rsplit("/", 1)[0] if "/" in relative else ""
        parsed = parse_partition_path(directory)
        if parsed is None:
            if directory:
                # Files below an unknown nested folder are outside the supported
                # partition contract; keep the old behavior of not ingesting
                # them instead of guessing a layout.
                continue
            unpartitioned = True
            parsed = ParsedPartition(
                partition_value=partition_datetime(current_partition),
                raw_partition_path="",
                partition_granularity=PartitionGranularity.UNPARTITIONED,
                partition_format="",
            )
            revision = StorageRevision(
                canonical_uri=item.canonical_uri,
                size=int(item.size or 0),
                last_modified=item.last_modified
                or _stat_with_diagnostics(
                    adapter,
                    item.canonical_uri,
                    diagnostics,
                ).last_modified,
                provider_revision=item.provider_revision,
            )
            files.append(DiscoveredLogFile(partition=parsed, revision=revision))
            continue
        else:
            formats.add(parsed.partition_format)
        partition_uri = (
            join_uri(definition.root_uri, parsed.raw_partition_path)
            if parsed.raw_partition_path
            else definition.root_uri
        )
        partition = partitions_by_uri.setdefault(
            partition_uri,
            DiscoveredPartition(
                uri=adapter.canonical_uri(partition_uri),
                partition=parsed,
            ),
        )
        revision = StorageRevision(
            canonical_uri=item.canonical_uri,
            size=int(item.size or 0),
            last_modified=item.last_modified
            or _stat_with_diagnostics(
                adapter,
                item.canonical_uri,
                diagnostics,
            ).last_modified,
            provider_revision=item.provider_revision,
        )
        files.append(DiscoveredLogFile(partition=partition.partition, revision=revision))

    if unpartitioned and formats:
        raise PartitionFormatError(
            "Partitioned and unpartitioned log files cannot share one stream"
        )
    if len(formats) > 1:
        raise PartitionFormatError(
            f"Mixed partition formats are not supported: {', '.join(sorted(formats))}"
        )
    partitions = sorted(
        partitions_by_uri.values(),
        key=lambda item: (item.partition.partition_value, item.partition.raw_partition_path),
    )
    files.sort(key=lambda item: (item.partition.partition_value, item.canonical_uri))
    return files, partitions


def _relative_uri(root_uri: str, child_uri: str) -> str:
    root = root_uri.rstrip("/\\").replace("\\", "/")
    child = child_uri.rstrip("/\\").replace("\\", "/")
    prefix = f"{root}/"
    if child.startswith(prefix):
        return child[len(prefix) :]
    return child.rsplit("/", 1)[-1]


def _stat_with_diagnostics(
    adapter: StorageAdapter,
    uri: str,
    diagnostics: dict[str, int],
) -> StorageRevision:
    diagnostics["stat_requests"] = diagnostics.get("stat_requests", 0) + 1
    return adapter.stat(uri)


def _incremental_values(
    layout: PartitionLayout,
    state: PlannerState,
    current_partition: PartitionValue,
) -> tuple[PartitionValue, ...]:
    if layout.granularity is PartitionGranularity.UNPARTITIONED:
        return (layout.normalize(current_partition),)
    # A scan has two deliberately separate responsibilities:
    #   * move the watermark forward over partitions that have never been seen;
    #   * revisit the bounded overwrite window for mutable snapshot files.
    # The union is inclusive at both ends.  Normalizing the 24-hour boundary to
    # the learned layout gives two daily partitions or 25 hourly partitions when
    # the source is caught up, while preserving the same rule for coarser layouts.
    forward_start = layout.normalize(
        state.last_scanned_partition_value or current_partition
    )
    overwrite_start = layout.normalize(
        partition_datetime(current_partition) - timedelta(days=1)
    )
    start = min(forward_start, overwrite_start)
    return layout.values(start, current_partition)


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
    *,
    diagnostics: dict[str, int],
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
            diagnostics=diagnostics,
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


def _removed_files(
    definition: StreamDefinition,
    files: list[DiscoveredLogFile],
    manifest: dict[str, StorageRevision],
    *,
    layout: PartitionLayout | None = None,
    scanned_values: tuple[PartitionValue, ...] = (),
) -> tuple[str, ...]:
    """Remove absent members only from the successfully inventoried scope."""
    current = {item.canonical_uri for item in files}
    scanned_paths = {layout.render(value) for value in scanned_values} if layout else None
    return tuple(sorted(
        uri for uri in manifest
        if uri not in current
        and (
            scanned_paths is None
            or _relative_uri(definition.root_uri, uri).rsplit("/", 1)[0] in scanned_paths
            or ("" in scanned_paths and "/" not in _relative_uri(definition.root_uri, uri))
        )
    ))


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
        last_scanned_partition_value=last_scanned,
    )
