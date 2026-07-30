from __future__ import annotations

import argparse
import json
import statistics
import time

from databricks.sdk import WorkspaceClient

from datacoolie_studio.domains.storage.dbfs_adapter import DbfsStorageAdapter
from datacoolie_studio.domains.storage.inventory import (
    StorageInventoryRequest,
    inventory,
    storage_diagnostics,
)


def main() -> None:
    arguments = _arguments()
    adapter = _adapter(arguments.profile)
    operations: dict[str, list[float]] = {
        "list_ms": [],
        "stat_ms": [],
        "read_ms": [],
    }
    for _index in range(arguments.runs):
        operations["list_ms"].append(
            _measure(
                lambda: inventory(
                    adapter,
                    StorageInventoryRequest(
                        uri=arguments.directory_uri,
                        purpose="probe",
                        object_limit=1,
                        stop_after_match=True,
                    ),
                )
            )
        )
        operations["stat_ms"].append(
            _measure(lambda: adapter.stat(arguments.file_uri))
        )
        operations["read_ms"].append(
            _measure(lambda: _read_all(adapter, arguments.file_uri))
        )
    print(
        json.dumps(
            {
                "transport": "databricks_sdk",
                "runs": arguments.runs,
                "cold_ms": {
                    name: round(values[0], 3)
                    for name, values in operations.items()
                },
                "warm_median_ms": {
                    name: round(statistics.median(values[1:] or values), 3)
                    for name, values in operations.items()
                },
                "storage_io": storage_diagnostics(adapter),
            },
            indent=2,
            sort_keys=True,
        )
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only Databricks Volume storage benchmark."
    )
    parser.add_argument("--directory-uri", required=True)
    parser.add_argument("--file-uri", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--runs", type=int, default=5)
    arguments = parser.parse_args()
    if arguments.runs < 2:
        parser.error("--runs must be at least 2")
    return arguments


def _adapter(profile: str | None) -> DbfsStorageAdapter:
    options = {"profile": profile} if profile else {}
    return DbfsStorageAdapter(WorkspaceClient(**options))


def _measure(operation) -> float:
    started = time.perf_counter()
    operation()
    return (time.perf_counter() - started) * 1000


def _read_all(adapter: DbfsStorageAdapter, uri: str) -> None:
    with adapter.open_read(uri) as stream:
        while stream.read(1024 * 1024):
            pass


if __name__ == "__main__":
    main()
