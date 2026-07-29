"""Storage URI helpers and adapter boundaries."""

from datacoolie_studio.domains.storage.adapters import (
    LocalStorageAdapter,
    StorageAdapter,
    StorageObject,
    StorageRevision,
)
from datacoolie_studio.domains.storage.binding import StorageBinding
from datacoolie_studio.domains.storage.onelake_adapter import OneLakeStorageAdapter

__all__ = [
    "LocalStorageAdapter",
    "OneLakeStorageAdapter",
    "StorageAdapter",
    "StorageBinding",
    "StorageObject",
    "StorageRevision",
]
