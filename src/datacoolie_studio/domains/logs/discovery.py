from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from datacoolie_studio.domains.logs.partition import ParsedPartition
from datacoolie_studio.domains.storage.adapters import (
    StorageAdapter,
    StorageRevision,
)
from datacoolie_studio.domains.storage.errors import StorageNotFoundError
from datacoolie_studio.domains.storage.concurrency import map_storage_io
from datacoolie_studio.domains.storage.inventory import (
    StorageInventoryRequest,
    inventory,
)


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

    def contains(self, partition_value: date | datetime) -> bool:
        value = partition_value.date() if isinstance(partition_value, datetime) else partition_value
        return self.from_partition <= value <= self.to_partition


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
class DiscoveredPartition:
    uri: str
    partition: ParsedPartition


@dataclass(frozen=True)
class DiscoveredLogFile:
    partition: ParsedPartition
    revision: StorageRevision

    @property
    def canonical_uri(self) -> str:
        return self.revision.canonical_uri


def discover_partition_files(
    adapter: StorageAdapter,
    partitions: Sequence[DiscoveredPartition],
    *,
    suffix: str,
    diagnostics: dict[str, int] | None = None,
) -> list[DiscoveredLogFile]:
    def discover_one(
        discovered_partition: DiscoveredPartition,
    ) -> tuple[list[DiscoveredLogFile], dict[str, int]]:
        files: list[DiscoveredLogFile] = []
        local_diagnostics = {
            "inventory_requests": 0,
            "inventory_pages": 0,
            "objects_inspected": 0,
            "stat_requests": 0,
        }
        try:
            observed = inventory(
                adapter,
                StorageInventoryRequest(
                    uri=discovered_partition.uri,
                    purpose="logs",
                    object_types=frozenset({"file"}),
                    suffixes=frozenset({suffix}),
                ),
            )
            if observed.completeness != "complete":
                raise ValueError("Log refresh requires a complete file inventory")
            local_diagnostics["inventory_requests"] += observed.requests
            local_diagnostics["inventory_pages"] += observed.pages
            local_diagnostics["objects_inspected"] += observed.objects_inspected
            file_items = observed.files
        except (FileNotFoundError, StorageNotFoundError):
            file_items = []
        for file_item in file_items:
            file_uri = file_item.canonical_uri
            revision = StorageRevision(
                canonical_uri=file_item.canonical_uri,
                size=int(file_item.size or 0),
                last_modified=file_item.last_modified
                or _stat_with_diagnostics(adapter, file_uri, local_diagnostics).last_modified,
                provider_revision=file_item.provider_revision,
            )
            files.append(
                DiscoveredLogFile(
                    partition=discovered_partition.partition,
                    revision=revision,
                )
            )
        return files, local_diagnostics

    discovered = map_storage_io(adapter, discover_one, list(partitions))
    files = []
    for group, local_diagnostics in discovered:
        files.extend(group)
        if diagnostics is not None:
            for key, value in local_diagnostics.items():
                diagnostics[key] = diagnostics.get(key, 0) + value
    return sorted(files, key=lambda item: (item.partition.partition_value, item.canonical_uri))


def _stat_with_diagnostics(
    adapter: StorageAdapter,
    uri: str,
    diagnostics: dict[str, int],
) -> StorageRevision:
    diagnostics["stat_requests"] += 1
    return adapter.stat(uri)
