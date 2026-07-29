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
    exclude_directories: frozenset[str] = frozenset()
    object_limit: int | None = None
    fresh: bool = True

    def __post_init__(self) -> None:
        if self.object_limit is not None and self.object_limit < 1:
            raise ValueError("object_limit must be positive")
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


def _normalize_suffix(value: str) -> str:
    lowered = value.lower().strip()
    return lowered if lowered.startswith(".") else f".{lowered}"
