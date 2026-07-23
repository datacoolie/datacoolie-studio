from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import BinaryIO, Protocol

from datacoolie_studio.domains.storage.uri import require_local_path


@dataclass(frozen=True)
class FileRevision:
    canonical_uri: str
    size: int
    last_modified: datetime
    provider_revision: str | None = None

    def __post_init__(self) -> None:
        if self.size < 0:
            raise ValueError("File size cannot be negative")
        if self.last_modified.tzinfo is None:
            raise ValueError("File last_modified must be timezone-aware")

    def same_content_as(self, other: FileRevision) -> bool:
        return (
            self.canonical_uri == other.canonical_uri
            and self.size == other.size
            and self.last_modified == other.last_modified
            and self.provider_revision == other.provider_revision
        )


class StorageAdapter(Protocol):
    def list_partition_children(self, root_uri: str) -> list[str]: ...

    def list_files(self, partition_uri: str, suffix: str) -> list[str]: ...

    def stat(self, file_uri: str) -> FileRevision: ...

    def canonical_uri(self, uri: str) -> str: ...

    def open(self, file_uri: str) -> BinaryIO: ...


class LocalStorageAdapter:
    """Shallow local storage operations used by partition-aware discovery."""

    def list_partition_children(self, root_uri: str) -> list[str]:
        root = require_local_path(root_uri)
        if not root.is_dir():
            return []
        return [
            self.canonical_uri(str(child))
            for child in sorted(root.iterdir(), key=lambda item: item.name)
            if child.is_dir()
        ]

    def list_files(self, partition_uri: str, suffix: str) -> list[str]:
        partition = require_local_path(partition_uri)
        normalized_suffix = suffix.lower()
        if not normalized_suffix.startswith("."):
            normalized_suffix = f".{normalized_suffix}"
        if not partition.is_dir():
            return []
        return [
            self.canonical_uri(str(child))
            for child in sorted(partition.iterdir(), key=lambda item: item.name)
            if child.is_file() and child.suffix.lower() == normalized_suffix
        ]

    def stat(self, file_uri: str) -> FileRevision:
        path = require_local_path(file_uri)
        state = path.stat()
        return FileRevision(
            canonical_uri=self.canonical_uri(file_uri),
            size=state.st_size,
            last_modified=datetime.fromtimestamp(state.st_mtime_ns / 1_000_000_000, tz=timezone.utc),
            provider_revision=str(state.st_mtime_ns),
        )

    def canonical_uri(self, uri: str) -> str:
        return require_local_path(uri).resolve().as_posix()

    def open(self, file_uri: str) -> BinaryIO:
        return require_local_path(file_uri).open("rb")
