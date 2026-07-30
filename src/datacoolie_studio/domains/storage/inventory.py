from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from datacoolie_studio.domains.storage.adapters import StorageObject

InventoryPurpose = Literal["probe", "observe", "validate", "materialize", "logs"]


@dataclass(frozen=True)
class StorageInventoryRequest:
    uri: str
    purpose: InventoryPurpose
    recursive: bool = False
    object_types: frozenset[Literal["file", "directory"]] = frozenset(
        {"file", "directory"}
    )
    suffixes: frozenset[str] = frozenset()
    name_prefix: str | None = None
    exclude_directories: frozenset[str] = frozenset()
    object_limit: int | None = None
    stop_after_match: bool = False
    fresh: bool = True

    def __post_init__(self) -> None:
        if self.object_limit is not None and self.object_limit < 1:
            raise ValueError("object_limit must be positive")
        if self.stop_after_match and self.object_limit is None:
            raise ValueError("stop_after_match requires object_limit")
        if self.name_prefix is not None and not self.name_prefix:
            raise ValueError("name_prefix cannot be empty")
        normalized = frozenset(_normalize_suffix(value) for value in self.suffixes)
        object.__setattr__(self, "suffixes", normalized)


@dataclass(frozen=True)
class StorageInventory:
    objects: tuple[StorageObject, ...]
    completeness: Literal["partial", "complete"]
    requests: int
    pages: int
    directories_visited: int
    objects_inspected: int
    matching_objects: int
    retries: int
    throttles: int
    bytes_read: int
    duration_ms: int
    early_stop_reason: Literal["object_limit"] | None = None

    @property
    def files(self) -> tuple[StorageObject, ...]:
        return tuple(item for item in self.objects if item.object_type == "file")

    @property
    def directories(self) -> tuple[StorageObject, ...]:
        return tuple(item for item in self.objects if item.object_type == "directory")


def inventory(adapter: object, request: StorageInventoryRequest) -> StorageInventory:
    """Run the adapter-owned bounded inventory contract."""

    scan = getattr(adapter, "inventory", None)
    if not callable(scan):
        raise TypeError(
            f"{type(adapter).__name__} does not implement the storage inventory contract"
        )
    return scan(request)


def storage_diagnostics(adapter: object) -> dict[str, int | str]:
    """Return allowlisted provider I/O metrics without client or auth state."""

    diagnostics = getattr(adapter, "storage_diagnostics", None)
    if not callable(diagnostics):
        return {}
    value = diagnostics()
    if not isinstance(value, dict):
        return {}
    allowed = {
        "transport",
        "provider_requests",
        "bytes_read",
        "objects_inspected",
    }
    return {
        key: item
        for key, item in value.items()
        if key in allowed and isinstance(item, (int, str))
    }


def _normalize_suffix(value: str) -> str:
    lowered = value.lower().strip()
    return lowered if lowered.startswith(".") else f".{lowered}"
