from __future__ import annotations

import hashlib
import io
import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, BinaryIO, Iterable
from uuid import uuid4

from datacoolie_studio.domains.storage.adapters import (
    StorageObject,
    StorageRevision,
)
from datacoolie_studio.domains.storage.errors import (
    StorageAccessError,
    StorageAuthenticationError,
    StorageConflictError,
    StorageNotFoundError,
)
from datacoolie_studio.domains.storage.redaction import redact_storage_error
from datacoolie_studio.domains.storage.concurrency import (
    map_storage_io,
    storage_io_context_active,
    storage_io_limit,
)
from datacoolie_studio.domains.storage.uri import (
    canonical_cloud_uri,
    parse_onelake_location,
)

if TYPE_CHECKING:
    from datacoolie_studio.domains.storage.inventory import (
        StorageInventory,
        StorageInventoryRequest,
    )


class OneLakeStorageAdapter:
    provider = "onelake"
    _download_concurrency = 4

    def __init__(self, service_client) -> None:
        self._service_client = service_client
        self._filesystems: dict[str, object] = {}

    def inventory(
        self,
        request: StorageInventoryRequest,
    ) -> StorageInventory:
        from datacoolie_studio.domains.storage.inventory import StorageInventory

        started = perf_counter()
        root = parse_onelake_location(request.uri)
        filesystem = self._filesystem(root.workspace)
        pending = deque([root.sdk_path])
        excluded = {value.lower() for value in request.exclude_directories}
        objects: list[StorageObject] = []
        requests = 0
        pages = 0
        directories_visited = 0
        objects_inspected = 0
        matching_objects = 0
        partial = False
        try:
            while pending:
                batch_size = (
                    1
                    if request.object_limit is not None
                    or storage_io_context_active(self)
                    else min(storage_io_limit(self), len(pending))
                )
                current_batch = [
                    pending.popleft() for _index in range(batch_size)
                ]
                def list_directory(current: str) -> _DirectoryListing:
                    return self._list_directory(
                        filesystem,
                        current,
                        object_limit=request.object_limit,
                    )

                listings = (
                    [list_directory(current_batch[0])]
                    if storage_io_context_active(self)
                    else map_storage_io(self, list_directory, current_batch)
                )
                directories_visited += len(current_batch)
                for listing in listings:
                    requests += listing.requests
                    pages += listing.pages
                    for properties in listing.objects:
                        raw_path = str(getattr(properties, "name", "") or "")
                        if not raw_path:
                            continue
                        objects_inspected += 1
                        object_type = (
                            "directory"
                            if bool(getattr(properties, "is_directory", False))
                            else "file"
                        )
                        name = raw_path.rstrip("/").rsplit("/", 1)[-1]
                        if object_type == "directory":
                            if name.lower() in excluded:
                                continue
                            if request.recursive:
                                pending.append(raw_path)
                        if object_type not in request.object_types or (
                            object_type == "file"
                            and request.suffixes
                            and not any(
                                name.lower().endswith(suffix)
                                for suffix in request.suffixes
                            )
                        ) or (
                            object_type == "file"
                            and request.name_prefix is not None
                            and not name.startswith(request.name_prefix)
                        ):
                            continue
                        matching_objects += 1
                        if (
                            request.object_limit is not None
                            and matching_objects > request.object_limit
                        ):
                            partial = True
                            pending.clear()
                            break
                        objects.append(
                            self._object(
                                workspace=root.workspace,
                                raw_path=raw_path,
                                properties=properties,
                                object_type=object_type,
                            )
                        )
                        if (
                            request.stop_after_match
                            and request.object_limit is not None
                            and matching_objects >= request.object_limit
                        ):
                            partial = True
                            pending.clear()
                            break
                    if partial:
                        break
                if not request.recursive or partial:
                    pending.clear()
                    break
        except Exception as exc:
            self._raise_access_error(exc, "list")
        objects.sort(key=lambda item: item.canonical_uri)
        return StorageInventory(
            objects=tuple(objects),
            completeness="partial" if partial else "complete",
            requests=requests,
            pages=pages,
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
        location = parse_onelake_location(uri)
        try:
            properties = self._filesystem(location.workspace).get_file_client(
                location.sdk_path
            ).get_file_properties()
        except Exception as exc:
            self._raise_access_error(exc, "stat")
        return self._revision(location.workspace, location.sdk_path, properties)

    def canonical_uri(self, uri: str) -> str:
        return canonical_cloud_uri(uri, "onelake")

    def open_read(self, uri: str) -> BinaryIO:
        handle, _revision = self.open_read_with_revision(uri)
        return handle

    def open_read_with_revision(
        self, uri: str
    ) -> tuple[BinaryIO, StorageRevision]:
        location = parse_onelake_location(uri)
        try:
            file_client = self._filesystem(location.workspace).get_file_client(
                location.sdk_path
            )
            downloader = file_client.download_file(
                max_concurrency=self._download_concurrency
            )
            properties = getattr(downloader, "properties", None)
            if properties is None:
                properties = file_client.get_file_properties()
        except Exception as exc:
            self._raise_access_error(exc, "open")
        return (
            io.BufferedReader(_ChunkStream(downloader.chunks())),
            self._revision(location.workspace, location.sdk_path, properties),
        )

    def materialize(
        self,
        uri: str,
        target: Path,
        expected_revision: StorageRevision | None = None,
    ) -> StorageRevision:
        before = expected_revision or self.stat(uri)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        digest = hashlib.sha256()
        try:
            handle, downloaded = self.open_read_with_revision(uri)
            if not before.same_object_state_as(downloaded):
                raise StorageConflictError(
                    uri, "Source revision changed before materialization"
                )
            with handle, temporary.open("wb") as output:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
            after = self.stat(uri)
            if not downloaded.same_object_state_as(after):
                raise StorageConflictError(
                    uri, "Source revision changed during materialization"
                )
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

    @staticmethod
    def _list_directory(
        filesystem,
        path: str,
        *,
        object_limit: int | None,
    ) -> _DirectoryListing:
        pager = filesystem.get_paths(
            path=path,
            recursive=False,
            max_results=(
                min(object_limit + 1, 5000)
                if object_limit is not None
                else None
            ),
        ).by_page()
        values: list[object] = []
        pages = 0
        for page in pager:
            pages += 1
            values.extend(page)
        values.sort(key=lambda value: str(getattr(value, "name", "")))
        return _DirectoryListing(
            objects=tuple(values),
            requests=pages,
            pages=pages,
        )

    def _filesystem(self, workspace: str):
        filesystem = self._filesystems.get(workspace)
        if filesystem is None:
            filesystem = self._service_client.get_file_system_client(workspace)
            self._filesystems[workspace] = filesystem
        return filesystem

    def _object(
        self,
        *,
        workspace: str,
        raw_path: str,
        properties,
        object_type: str,
    ) -> StorageObject:
        canonical = canonical_cloud_uri(
            f"abfss://{workspace}@onelake.dfs.fabric.microsoft.com/{raw_path}",
            "onelake",
        )
        return StorageObject(
            canonical_uri=canonical,
            name=raw_path.rstrip("/").rsplit("/", 1)[-1],
            object_type=object_type,
            size=(
                None
                if object_type == "directory"
                else _integer(getattr(properties, "content_length", None))
            ),
            last_modified=_modified(getattr(properties, "last_modified", None)),
            provider_revision=_etag(getattr(properties, "etag", None)),
        )

    def _revision(
        self, workspace: str, raw_path: str, properties
    ) -> StorageRevision:
        return StorageRevision(
            canonical_uri=canonical_cloud_uri(
                f"abfss://{workspace}@onelake.dfs.fabric.microsoft.com/{raw_path}",
                "onelake",
            ),
            size=_integer(getattr(properties, "size", None))
            or _integer(getattr(properties, "content_length", None))
            or 0,
            last_modified=_modified(
                getattr(properties, "last_modified", None)
            )
            or datetime.fromtimestamp(0, tz=timezone.utc),
            provider_revision=_etag(getattr(properties, "etag", None)),
        )

    def _raise_access_error(self, exc: Exception, operation: str) -> None:
        status = (
            getattr(exc, "status_code", None)
            or getattr(exc, "status", None)
            or getattr(getattr(exc, "response", None), "status_code", None)
        )
        detail = redact_storage_error(str(exc))
        if status == 401 or exc.__class__.__name__ == "ClientAuthenticationError":
            raise StorageAuthenticationError(
                f"OneLake {operation} authentication failed: {detail}",
                provider=self.provider,
            ) from exc
        if status == 404 or exc.__class__.__name__ == "ResourceNotFoundError":
            raise StorageNotFoundError(
                f"OneLake {operation} path was not found: {detail}",
                provider=self.provider,
            ) from exc
        raise StorageAccessError(
            f"OneLake {operation} failed: {detail}",
            provider=self.provider,
        ) from exc


class _ChunkStream(io.RawIOBase):
    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._chunks = iter(chunks)
        self._buffer = bytearray()
        self._finished = False

    def readable(self) -> bool:
        return True

    def readinto(self, target) -> int:
        if self.closed:
            return 0
        view = memoryview(target)
        while len(self._buffer) < len(view) and not self._finished:
            try:
                self._buffer.extend(next(self._chunks))
            except StopIteration:
                self._finished = True
        count = min(len(view), len(self._buffer))
        view[:count] = self._buffer[:count]
        del self._buffer[:count]
        return count


@dataclass(frozen=True)
class _DirectoryListing:
    objects: tuple[object, ...]
    requests: int
    pages: int


def _integer(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _modified(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc)
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)


def _etag(value: object) -> str | None:
    return str(value).strip('"') if value else None
