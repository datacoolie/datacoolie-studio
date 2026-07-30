from __future__ import annotations

import hashlib
import io
import os
import threading
from dataclasses import replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from time import perf_counter
from typing import TYPE_CHECKING, BinaryIO
from uuid import uuid4

from datacoolie_studio.domains.storage.adapters import (
    StorageObject,
    StorageRevision,
)
from datacoolie_studio.domains.storage.concurrency import (
    map_storage_io,
    storage_io_context_active,
    storage_io_limit,
)
from datacoolie_studio.domains.storage.errors import (
    StorageAccessError,
    StorageConflictError,
    StorageNotFoundError,
)
from datacoolie_studio.domains.storage.uri import canonical_cloud_uri

if TYPE_CHECKING:
    from datacoolie_studio.domains.storage.inventory import (
        StorageInventory,
        StorageInventoryRequest,
    )


class DbfsStorageAdapter:
    """Databricks SDK adapter for Unity Catalog Volumes and legacy DBFS paths."""

    def __init__(self, workspace_client) -> None:
        self._client = workspace_client
        self.provider = "dbfs"
        self.transport = str(
            getattr(workspace_client, "transport", "databricks_sdk")
        )
        self._metrics_lock = threading.Lock()
        self._provider_requests = 0
        self._bytes_read = 0
        self._objects_inspected = 0

    def storage_diagnostics(self) -> dict[str, int | str]:
        with self._metrics_lock:
            return {
                "transport": self.transport,
                "provider_requests": self._provider_requests,
                "bytes_read": self._bytes_read,
                "objects_inspected": self._objects_inspected,
            }

    def inventory(
        self,
        request: StorageInventoryRequest,
    ) -> StorageInventory:
        from datacoolie_studio.domains.storage.inventory import StorageInventory

        started = perf_counter()
        root = _dbfs_path(request.uri)
        pending = [root]
        excluded = {name.lower() for name in request.exclude_directories}
        result: list[StorageObject] = []
        requests = 0
        pages = 0
        directories_visited = 0
        objects_inspected = 0
        matching_objects = 0
        partial = False
        try:
            while pending and not partial:
                nested_io = storage_io_context_active(self)
                batch_size = 1 if nested_io else storage_io_limit(self)
                frontier = pending[:batch_size]
                pending = pending[batch_size:]
                listings = (
                    [self._list_directory(frontier[0])]
                    if nested_io
                    else map_storage_io(
                        self,
                        self._list_directory,
                        frontier,
                    )
                )
                requests += len(frontier)
                pages += len(frontier)
                directories_visited += len(frontier)
                discovered_directories: list[str] = []
                for path, entries in zip(frontier, listings, strict=True):
                    ordered = sorted(
                        entries,
                        key=lambda entry: str(
                            _field(entry, "path", "name") or ""
                        ),
                    )
                    for entry in ordered:
                        objects_inspected += 1
                        item = self._object(entry, fallback_path=path)
                        if (
                            item.object_type == "directory"
                            and item.name.lower() in excluded
                        ):
                            continue
                        if request.recursive and item.object_type == "directory":
                            discovered_directories.append(
                                _dbfs_path(item.canonical_uri)
                            )
                        if item.object_type not in request.object_types or (
                            item.object_type == "file"
                            and request.suffixes
                            and not any(
                                item.name.lower().endswith(suffix)
                                for suffix in request.suffixes
                            )
                        ) or (
                            item.object_type == "file"
                            and request.name_prefix is not None
                            and not item.name.startswith(request.name_prefix)
                        ):
                            continue
                        matching_objects += 1
                        if (
                            request.object_limit is not None
                            and matching_objects > request.object_limit
                        ):
                            partial = True
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
                    if partial:
                        break
                if request.recursive and not partial:
                    pending.extend(discovered_directories)
                if not request.recursive:
                    break
        except Exception as exc:
            if _is_not_found(exc):
                raise StorageNotFoundError(
                    "DBFS path was not found", provider="dbfs"
                ) from exc
            raise StorageAccessError(
                "DBFS list failed", provider="dbfs"
            ) from exc
        self._record_io(
            provider_requests=requests,
            objects_inspected=objects_inspected,
        )
        result.sort(key=lambda item: item.canonical_uri)
        return StorageInventory(
            objects=tuple(result),
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
        path = _dbfs_path(uri)
        try:
            entry = (
                self._volume_metadata(path)
                if _is_volume_path(path)
                else self._client.dbfs.get_status(path)
            )
        except Exception as exc:
            if _is_not_found(exc):
                raise StorageNotFoundError(
                    "DBFS object was not found", provider="dbfs"
                ) from exc
            raise StorageAccessError(
                "DBFS stat failed", provider="dbfs"
            ) from exc
        item = self._object(entry, fallback_path=path)
        self._record_io(provider_requests=1, objects_inspected=1)
        return StorageRevision(
            canonical_uri=item.canonical_uri,
            size=int(item.size or 0),
            last_modified=item.last_modified or _epoch(),
            provider_revision=item.provider_revision,
        )

    def canonical_uri(self, uri: str) -> str:
        return canonical_cloud_uri(uri, "dbfs")

    def open_read(self, uri: str) -> BinaryIO:
        response, _revision = self._download(uri)
        return _binary_stream(response)

    def open_read_with_revision(
        self, uri: str
    ) -> tuple[BinaryIO, StorageRevision]:
        """Open a DBFS object and use the download response as its revision."""
        response, revision = self._download(uri)
        return _binary_stream(response), revision

    def _download(self, uri: str):
        path = _dbfs_path(uri)
        try:
            response = (
                self._client.files.download(path)
                if _is_volume_path(path)
                else self._client.dbfs.download(path)
            )
            revision = _download_revision(
                response,
                canonical_uri=self.canonical_uri(uri),
            )
            if revision is None:
                revision = self.stat(uri)
            self._record_io(
                provider_requests=1,
                bytes_read=revision.size,
                objects_inspected=1,
            )
            return response, revision
        except Exception as exc:
            raise StorageAccessError(
                "DBFS open failed", provider="dbfs"
            ) from exc

    def materialize(
        self,
        uri: str,
        target: Path,
        expected_revision: StorageRevision | None = None,
    ) -> StorageRevision:
        source, observed = self.open_read_with_revision(uri)
        if expected_revision and not observed.same_object_state_as(
            expected_revision
        ):
            _safe_close(source)
            raise StorageConflictError(
                uri, "Source revision changed before materialization"
            )
        if expected_revision is not None:
            observed = replace(
                observed,
                provider_revision=expected_revision.provider_revision,
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        digest = hashlib.sha256()
        try:
            with source, temporary.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return StorageRevision(
            canonical_uri=observed.canonical_uri,
            size=observed.size,
            last_modified=observed.last_modified,
            provider_revision=observed.provider_revision,
            content_hash=digest.hexdigest(),
        )

    def _list_directory(self, path: str):
        entries = (
            self._client.files.list_directory_contents(path)
            if _is_volume_path(path)
            else self._client.dbfs.list(path)
        )
        return list(entries)

    def _volume_metadata(self, path: str):
        getter = getattr(self._client.files, "get_metadata", None)
        if callable(getter):
            return getter(path)
        parent = str(PurePosixPath(path).parent)
        name = PurePosixPath(path).name
        for entry in self._client.files.list_directory_contents(parent):
            entry_path = str(_field(entry, "path") or "")
            entry_name = str(_field(entry, "name") or PurePosixPath(entry_path).name)
            if entry_name == name or entry_path.rstrip("/") == path.rstrip("/"):
                return entry
        raise FileNotFoundError(path)

    def _object(self, entry, *, fallback_path: str) -> StorageObject:
        raw_path = str(_field(entry, "path") or fallback_path)
        if raw_path.startswith("dbfs:"):
            raw_path = raw_path.removeprefix("dbfs:")
        path = "/" + raw_path.lstrip("/")
        is_directory = bool(
            _field(entry, "is_directory")
            or _field(entry, "is_dir")
            or str(_field(entry, "type") or "").lower()
            in {"directory", "dir"}
        )
        size = _integer(
            _field(entry, "file_size", "size", "content_length")
        )
        modified = _modified(
            _field(entry, "last_modified", "modification_time", "mtime")
        )
        revision = _revision(entry, size=size, modified=modified)
        return StorageObject(
            canonical_uri=self.canonical_uri(f"dbfs:{path}"),
            name=str(_field(entry, "name") or PurePosixPath(path).name),
            object_type="directory" if is_directory else "file",
            size=size,
            last_modified=modified,
            provider_revision=revision,
        )

    def _record_io(
        self,
        *,
        provider_requests: int = 0,
        bytes_read: int = 0,
        objects_inspected: int = 0,
    ) -> None:
        with self._metrics_lock:
            self._provider_requests += provider_requests
            self._bytes_read += bytes_read
            self._objects_inspected += objects_inspected


def _field(value, *names: str):
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return None


def _binary_stream(response) -> BinaryIO:
    contents = _field(response, "contents")
    value = contents if contents is not None else response
    if isinstance(value, (bytes, bytearray)):
        return io.BytesIO(bytes(value))
    if hasattr(value, "read"):
        return value
    raise TypeError("Databricks download response does not contain a byte stream")


def _safe_close(stream: BinaryIO) -> None:
    try:
        stream.close()
    except Exception:
        # databricks-sdk 0.122 may raise AttributeError when closing an
        # unread resilient streaming response.
        pass


def _dbfs_path(uri: str) -> str:
    canonical = canonical_cloud_uri(uri, "dbfs")
    return "/" + canonical.removeprefix("dbfs:").lstrip("/")


def _is_volume_path(path: str) -> bool:
    return path == "/Volumes" or path.startswith("/Volumes/")


def _integer(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _modified(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc)
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(str(value))
            return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
        except (TypeError, ValueError):
            return None


def _download_revision(
    response, *, canonical_uri: str
) -> StorageRevision | None:
    size = _integer(_field(response, "content_length"))
    modified = _modified(_field(response, "last_modified"))
    if size is None or modified is None:
        return None
    return StorageRevision(
        canonical_uri=canonical_uri,
        size=size,
        last_modified=modified,
        # The Files download response has size and mtime but no ETag. Keep
        # the token absent so it can be reconciled with a listing revision.
        provider_revision=None,
    )


def _revision(entry, *, size: int | None, modified: datetime | None) -> str | None:
    etag = _field(entry, "etag", "e_tag")
    if etag:
        return str(etag).strip('"')
    if modified is None and size is None:
        return None
    return f"{int(modified.timestamp() * 1000) if modified else 0}:{size or 0}"
def _epoch() -> datetime:
    return datetime.fromtimestamp(0, tz=timezone.utc)


def _is_not_found(exc: Exception) -> bool:
    if isinstance(exc, FileNotFoundError):
        return True
    error_code = str(
        getattr(exc, "error_code", None)
        or getattr(exc, "code", None)
        or ""
    ).lower()
    status_code = getattr(exc, "status_code", None)
    return status_code == 404 or error_code in {
        "404",
        "not_found",
        "resource_does_not_exist",
    }
