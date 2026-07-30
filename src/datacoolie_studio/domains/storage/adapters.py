from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, BinaryIO, Literal, Protocol
from uuid import uuid4

from datacoolie_studio.domains.storage.errors import StorageConflictError
from datacoolie_studio.domains.storage.uri import require_local_path

if TYPE_CHECKING:
    from datacoolie_studio.domains.storage.inventory import (
        StorageInventory,
        StorageInventoryRequest,
    )


@dataclass(frozen=True)
class StorageObject:
    canonical_uri: str
    name: str
    object_type: Literal["file", "directory"]
    size: int | None = None
    last_modified: datetime | None = None
    provider_revision: str | None = None


@dataclass(frozen=True)
class StorageRevision:
    canonical_uri: str
    size: int
    last_modified: datetime
    provider_revision: str | None = None
    content_hash: str | None = None

    def __post_init__(self) -> None:
        if self.size < 0:
            raise ValueError("File size cannot be negative")
        if self.last_modified.tzinfo is None:
            raise ValueError("File last_modified must be timezone-aware")

    def same_content_as(self, other: StorageRevision) -> bool:
        return (
            self.canonical_uri == other.canonical_uri
            and self.size == other.size
            and self.last_modified == other.last_modified
            and self.provider_revision == other.provider_revision
        )

    def same_object_state_as(self, other: StorageRevision) -> bool:
        """Compare observable state when one response omits a version token."""
        return (
            self.canonical_uri == other.canonical_uri
            and self.size == other.size
            and self.last_modified == other.last_modified
            and (
                self.provider_revision is None
                or other.provider_revision is None
                or self.provider_revision == other.provider_revision
            )
        )


class StorageAdapter(Protocol):
    def inventory(self, request: StorageInventoryRequest) -> StorageInventory: ...

    def stat(self, uri: str) -> StorageRevision: ...

    def canonical_uri(self, uri: str) -> str: ...

    def open_read(self, uri: str) -> BinaryIO: ...

    def materialize(
        self,
        uri: str,
        target: Path,
        expected_revision: StorageRevision | None = None,
    ) -> StorageRevision: ...

class LocalStorageAdapter:
    provider = "local"

    def inventory(
        self,
        request: StorageInventoryRequest,
    ) -> StorageInventory:
        from datacoolie_studio.domains.storage.inventory import StorageInventory

        started = perf_counter()
        root = require_local_path(request.uri)
        if not root.is_dir():
            return StorageInventory(
                objects=(),
                completeness="complete",
                requests=0,
                pages=0,
                directories_visited=0,
                objects_inspected=0,
                matching_objects=0,
                retries=0,
                throttles=0,
                bytes_read=0,
                duration_ms=round((perf_counter() - started) * 1000),
            )
        excluded = {name.lower() for name in request.exclude_directories}
        result: list[StorageObject] = []
        pending = [root]
        directories_visited = 0
        objects_inspected = 0
        matching_objects = 0
        partial = False
        while pending:
            current = pending.pop()
            directories_visited += 1
            with os.scandir(current) as entries:
                children = sorted(entries, key=lambda item: item.name)
            for child in children:
                objects_inspected += 1
                if child.is_dir(follow_symlinks=False):
                    if child.name.lower() in excluded:
                        continue
                    if request.recursive:
                        pending.append(Path(child.path))
                    if "directory" not in request.object_types:
                        continue
                    item = StorageObject(
                        canonical_uri=self.canonical_uri(child.path),
                        name=child.name,
                        object_type="directory",
                    )
                elif child.is_file(follow_symlinks=False):
                    if "file" not in request.object_types or (
                        request.suffixes
                        and Path(child.name).suffix.lower() not in request.suffixes
                    ) or (
                        request.name_prefix is not None
                        and not child.name.startswith(request.name_prefix)
                    ):
                        continue
                    item = self._file_object(Path(child.path))
                else:
                    continue
                matching_objects += 1
                if (
                    request.object_limit is not None
                    and matching_objects > request.object_limit
                ):
                    partial = True
                    pending.clear()
                    break
                result.append(item)
                if (
                    request.stop_after_match
                    and request.object_limit is not None
                    and matching_objects >= request.object_limit
                ):
                    partial = True
                    pending.clear()
                    break
            if not request.recursive or partial:
                break
        result.sort(key=lambda item: item.canonical_uri)
        return StorageInventory(
            objects=tuple(result),
            completeness="partial" if partial else "complete",
            requests=0,
            pages=0,
            directories_visited=directories_visited,
            objects_inspected=objects_inspected,
            matching_objects=matching_objects,
            retries=0,
            throttles=0,
            bytes_read=0,
            duration_ms=round((perf_counter() - started) * 1000),
            early_stop_reason="object_limit" if partial else None,
        )

    def stat(self, uri: str) -> StorageRevision:
        path = require_local_path(uri)
        state = path.stat()
        return StorageRevision(
            canonical_uri=self.canonical_uri(uri),
            size=state.st_size,
            last_modified=datetime.fromtimestamp(
                state.st_mtime_ns / 1_000_000_000, tz=timezone.utc
            ),
            provider_revision=f"{state.st_mtime_ns}:{state.st_size}",
        )

    def canonical_uri(self, uri: str) -> str:
        return require_local_path(uri).resolve().as_posix()

    def open_read(self, uri: str) -> BinaryIO:
        return require_local_path(uri).open("rb")

    def materialize(
        self,
        uri: str,
        target: Path,
        expected_revision: StorageRevision | None = None,
    ) -> StorageRevision:
        before = expected_revision or self.stat(uri)
        if expected_revision and not before.same_content_as(expected_revision):
            raise StorageConflictError(uri, "Source revision changed before materialization")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        digest = hashlib.sha256()
        try:
            with self.open_read(uri) as source, temporary.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
            after = self.stat(uri)
            if not before.same_content_as(after):
                raise StorageConflictError(uri, "Source revision changed during materialization")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return StorageRevision(
            canonical_uri=after.canonical_uri,
            size=after.size,
            last_modified=after.last_modified,
            provider_revision=after.provider_revision,
            content_hash=digest.hexdigest(),
        )

    def _file_object(self, child: Path) -> StorageObject:
        state = child.stat()
        modified = datetime.fromtimestamp(
            state.st_mtime_ns / 1_000_000_000, tz=timezone.utc
        )
        return StorageObject(
            canonical_uri=self.canonical_uri(str(child)),
            name=child.name,
            object_type="file",
            size=state.st_size,
            last_modified=modified,
            provider_revision=f"{state.st_mtime_ns}:{state.st_size}",
        )
