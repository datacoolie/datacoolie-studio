from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from datacoolie_studio.domains.logs.partition import ParsedPartition, PartitionGranularity, parse_partition_path
from datacoolie_studio.domains.storage.adapters import FileRevision, StorageAdapter


class PartitionFormatError(ValueError):
    pass


class LogSyncMode(str, Enum):
    INCREMENTAL = "incremental"
    INCREMENTAL_WITH_LOOKBACK = "incremental_with_lookback"


@dataclass(frozen=True)
class LookbackRange:
    from_partition: date
    to_partition: date

    def __post_init__(self) -> None:
        if self.from_partition > self.to_partition:
            raise ValueError("Lookback from_partition must be on or before to_partition")

    def contains(self, partition_value: date) -> bool:
        return self.from_partition <= partition_value <= self.to_partition


@dataclass(frozen=True)
class LogSyncSpec:
    mode: LogSyncMode = LogSyncMode.INCREMENTAL
    lookback: LookbackRange | None = None

    def __post_init__(self) -> None:
        if self.mode is LogSyncMode.INCREMENTAL and self.lookback is not None:
            raise ValueError("Incremental sync cannot include a lookback range")
        if self.mode is LogSyncMode.INCREMENTAL_WITH_LOOKBACK and self.lookback is None:
            raise ValueError("Incremental with lookback requires a lookback range")


@dataclass(frozen=True)
class LogStreamCheckpoint:
    partition_value: date
    boundary_last_modified: datetime
    partition_format: str

    def __post_init__(self) -> None:
        if self.boundary_last_modified.tzinfo is None:
            raise ValueError("Checkpoint boundary_last_modified must be timezone-aware")


@dataclass(frozen=True)
class DiscoveredPartition:
    uri: str
    partition: ParsedPartition


@dataclass(frozen=True)
class DiscoveredLogFile:
    partition: ParsedPartition
    revision: FileRevision

    @property
    def canonical_uri(self) -> str:
        return self.revision.canonical_uri


def discover_partitions(
    adapter: StorageAdapter,
    root_uri: str,
    *,
    expected_format: str | None = None,
) -> list[DiscoveredPartition]:
    """Discover partition leaves with at most three shallow directory listings."""

    leaves: list[DiscoveredPartition] = []
    for child_uri in adapter.list_partition_children(root_uri):
        relative = _relative_child_name(root_uri, child_uri)
        leaves.extend(_discover_partition_branch(adapter, child_uri, relative, depth=1))
    formats = {item.partition.partition_format for item in leaves}
    if len(formats) > 1:
        raise PartitionFormatError(f"Mixed partition formats are not supported: {', '.join(sorted(formats))}")
    if expected_format is not None and formats and formats != {expected_format}:
        discovered_format = next(iter(formats))
        raise PartitionFormatError(
            f"Partition format changed from {expected_format!r} to {discovered_format!r}"
        )
    return sorted(leaves, key=lambda item: (item.partition.partition_value, item.partition.raw_partition_path))


def plan_incremental_partitions(
    partitions: Sequence[DiscoveredPartition],
    checkpoint: LogStreamCheckpoint | None,
) -> list[DiscoveredPartition]:
    if checkpoint is None:
        return list(partitions)
    return [item for item in partitions if item.partition.partition_value >= checkpoint.partition_value]


def plan_lookback_partitions(
    partitions: Sequence[DiscoveredPartition],
    lookback: LookbackRange,
) -> list[DiscoveredPartition]:
    return [item for item in partitions if lookback.contains(item.partition.partition_value)]


def discover_partition_files(
    adapter: StorageAdapter,
    partitions: Sequence[DiscoveredPartition],
    *,
    suffix: str,
) -> list[DiscoveredLogFile]:
    files: list[DiscoveredLogFile] = []
    for discovered_partition in partitions:
        for file_uri in adapter.list_files(discovered_partition.uri, suffix):
            files.append(
                DiscoveredLogFile(
                    partition=discovered_partition.partition,
                    revision=adapter.stat(file_uri),
                )
            )
    return sorted(files, key=lambda item: (item.partition.partition_value, item.canonical_uri))


def plan_incremental_candidates(
    files: Sequence[DiscoveredLogFile],
    checkpoint: LogStreamCheckpoint | None,
) -> list[DiscoveredLogFile]:
    if checkpoint is None:
        return list(files)
    return [item for item in files if item.revision.last_modified > checkpoint.boundary_last_modified]


def plan_lookback_candidates(
    files: Sequence[DiscoveredLogFile],
    lookback: LookbackRange,
    manifest_revisions: Mapping[str, FileRevision],
) -> list[DiscoveredLogFile]:
    candidates: list[DiscoveredLogFile] = []
    for item in files:
        if not lookback.contains(item.partition.partition_value):
            continue
        existing = manifest_revisions.get(item.canonical_uri)
        if existing is None or not item.revision.same_content_as(existing):
            candidates.append(item)
    return candidates


def discover_incremental_candidates(
    adapter: StorageAdapter,
    root_uri: str,
    *,
    suffix: str,
    checkpoint: LogStreamCheckpoint | None = None,
) -> list[DiscoveredLogFile]:
    expected_format = checkpoint.partition_format if checkpoint else None
    partitions = discover_partitions(adapter, root_uri, expected_format=expected_format)
    selected = plan_incremental_partitions(partitions, checkpoint)
    files = discover_partition_files(adapter, selected, suffix=suffix)
    return plan_incremental_candidates(files, checkpoint)


def discover_lookback_candidates(
    adapter: StorageAdapter,
    root_uri: str,
    *,
    suffix: str,
    lookback: LookbackRange,
    manifest_revisions: Mapping[str, FileRevision],
    expected_format: str | None = None,
) -> list[DiscoveredLogFile]:
    partitions = discover_partitions(adapter, root_uri, expected_format=expected_format)
    selected = plan_lookback_partitions(partitions, lookback)
    files = discover_partition_files(adapter, selected, suffix=suffix)
    return plan_lookback_candidates(files, lookback, manifest_revisions)


def deduplicate_candidates(*groups: Sequence[DiscoveredLogFile]) -> list[DiscoveredLogFile]:
    by_uri: dict[str, DiscoveredLogFile] = {}
    for group in groups:
        for candidate in group:
            by_uri[candidate.canonical_uri] = candidate
    return sorted(by_uri.values(), key=lambda item: (item.partition.partition_value, item.canonical_uri))


def _discover_partition_branch(
    adapter: StorageAdapter,
    uri: str,
    relative_path: str,
    *,
    depth: int,
) -> list[DiscoveredPartition]:
    parsed = parse_partition_path(relative_path)
    if parsed is not None and _is_partition_leaf(parsed):
        return [DiscoveredPartition(uri=adapter.canonical_uri(uri), partition=parsed)]

    descendants: list[DiscoveredPartition] = []
    if depth < 3 and (parsed is None or parsed.partition_granularity is not PartitionGranularity.DAY):
        for child_uri in adapter.list_partition_children(uri):
            child_name = _relative_child_name(uri, child_uri)
            descendants.extend(
                _discover_partition_branch(
                    adapter,
                    child_uri,
                    f"{relative_path}/{child_name}",
                    depth=depth + 1,
                )
            )
    if descendants:
        return descendants
    if parsed is not None:
        return [DiscoveredPartition(uri=adapter.canonical_uri(uri), partition=parsed)]
    return []


def _is_partition_leaf(partition: ParsedPartition) -> bool:
    if partition.partition_granularity is PartitionGranularity.DAY:
        return True
    return "/" not in partition.raw_partition_path and partition.partition_granularity is PartitionGranularity.MONTH


def _relative_child_name(parent_uri: str, child_uri: str) -> str:
    parent = parent_uri.rstrip("/\\")
    child = child_uri.rstrip("/\\")
    if child.startswith(parent):
        relative = child[len(parent) :].lstrip("/\\")
        if relative and "/" not in relative and "\\" not in relative:
            return relative
    normalized = child.replace("\\", "/")
    return normalized.rsplit("/", 1)[-1]
