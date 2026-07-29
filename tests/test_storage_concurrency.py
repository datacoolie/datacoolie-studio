from __future__ import annotations

import threading
import time
from datetime import date, datetime, timezone

from datacoolie_studio.domains.logs.discovery import (
    DiscoveredPartition,
    discover_partition_files,
)
from datacoolie_studio.domains.logs.partition import (
    ParsedPartition,
    PartitionGranularity,
)
from datacoolie_studio.domains.storage.adapters import StorageObject
from datacoolie_studio.domains.storage.inventory import StorageInventory
from datacoolie_studio.domains.storage.concurrency import (
    map_storage_io,
    storage_io_limit,
)


class _ConcurrentDbfsAdapter:
    provider = "dbfs"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def inventory(self, request) -> StorageInventory:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.01)
            index = int(request.uri.rsplit("-", 1)[-1])
            objects = [
                StorageObject(
                    canonical_uri=f"{request.uri}/file-{index:02d}.jsonl",
                    name=f"file-{index:02d}.jsonl",
                    object_type="file",
                    size=index,
                    last_modified=datetime(2026, 7, 28, tzinfo=timezone.utc),
                    provider_revision=f"revision-{index}",
                )
            ]
            return StorageInventory(
                objects=tuple(objects),
                completeness="complete",
                requests=1,
                pages=1,
                directories_visited=1,
                objects_inspected=1,
                matching_objects=1,
                retries=0,
                throttles=0,
                bytes_read=0,
                duration_ms=10,
            )
        finally:
            with self._lock:
                self.active -= 1

    @staticmethod
    def canonical_uri(uri: str) -> str:
        return uri


def test_storage_io_map_preserves_input_order() -> None:
    adapter = _ConcurrentDbfsAdapter()

    result = map_storage_io(adapter, lambda value: value * value, list(range(32)))

    assert result == [value * value for value in range(32)]


def test_partition_file_discovery_uses_bounded_concurrency_and_sorts_results() -> None:
    adapter = _ConcurrentDbfsAdapter()
    partitions = [
        DiscoveredPartition(
            uri=f"dbfs:/partition-{index}",
            partition=ParsedPartition(
                partition_value=date(2026, 7, 28),
                raw_partition_path=f"partition-{index}",
                partition_granularity=PartitionGranularity.DAY,
                partition_format="%Y-%m-%d",
            ),
        )
        for index in reversed(range(24))
    ]

    files = discover_partition_files(adapter, partitions, suffix=".jsonl")

    assert adapter.max_active > 1
    assert adapter.max_active <= storage_io_limit(adapter)
    assert [item.canonical_uri for item in files] == sorted(
        item.canonical_uri for item in files
    )
