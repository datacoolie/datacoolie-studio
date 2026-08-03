from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, BinaryIO
from urllib.parse import urlsplit
from uuid import uuid4

from datacoolie_studio.domains.storage.adapters import (
    StorageObject,
    StorageRevision,
)
from datacoolie_studio.domains.storage.errors import (
    StorageAccessError,
    StorageConflictError,
    StorageNotFoundError,
)
from datacoolie_studio.domains.storage.redaction import redact_storage_error
from datacoolie_studio.domains.storage.uri import canonical_cloud_uri

if TYPE_CHECKING:
    from datacoolie_studio.domains.storage.inventory import (
        StorageInventory,
        StorageInventoryRequest,
    )


class FsspecStorageAdapter:
    """Provider-neutral adapter around a configured fsspec filesystem."""

    def __init__(self, filesystem, *, provider: str) -> None:
        self._filesystem = filesystem
        self.provider = provider

    def inventory(
        self,
        request: StorageInventoryRequest,
    ) -> StorageInventory:
        from datacoolie_studio.domains.storage.inventory import StorageInventory

        started = perf_counter()
        root = self._path(request.uri)
        pending = [root]
        excluded = {name.lower() for name in request.exclude_directories}
        objects: list[StorageObject] = []
        requests = 0
        pages = 0
        directories_visited = 0
        objects_inspected = 0
        matching_objects = 0
        partial = False
        invalidate_cache = getattr(self._filesystem, "invalidate_cache", None)
        try:
            while pending:
                current = pending.pop(0)
                if request.fresh and callable(invalidate_cache):
                    invalidate_cache(current)
                listing = self._filesystem.ls(current, detail=True)
                requests += 1
                pages += 1
                directories_visited += 1
                values = listing.values() if isinstance(listing, dict) else listing
                normalized = sorted(
                    (info for info in values if isinstance(info, dict)),
                    key=lambda info: str(info.get("name") or info.get("Key") or ""),
                )
                for info in normalized:
                    raw_path = str(info.get("name") or info.get("Key") or "")
                    if not raw_path:
                        continue
                    objects_inspected += 1
                    object_type = _object_type(info)
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
                        self._object_from_info(
                            raw_path,
                            info,
                            source_uri=request.uri,
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
                if not request.recursive or partial:
                    break
        except FileNotFoundError as exc:
            raise StorageNotFoundError(
                f"{self.provider.upper()} path was not found",
                provider=self.provider,
            ) from exc
        except Exception as exc:
            raise StorageAccessError(
                f"{self.provider.upper()} list failed: {redact_storage_error(str(exc))}",
                provider=self.provider,
            ) from exc
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
        try:
            info = self._filesystem.info(self._path(uri))
        except FileNotFoundError as exc:
            raise StorageNotFoundError(
                f"{self.provider.upper()} object was not found",
                provider=self.provider,
            ) from exc
        except Exception as exc:
            raise StorageAccessError(
                f"{self.provider.upper()} stat failed: {redact_storage_error(str(exc))}",
                provider=self.provider,
            ) from exc
        item = self._object_from_info(self._path(uri), info, source_uri=uri)
        return StorageRevision(
            canonical_uri=item.canonical_uri,
            size=int(item.size or 0),
            last_modified=item.last_modified or _epoch(),
            provider_revision=item.provider_revision,
        )

    def canonical_uri(self, uri: str) -> str:
        return canonical_cloud_uri(uri, self.provider)

    def open_read(self, uri: str) -> BinaryIO:
        try:
            return self._filesystem.open(self._path(uri), "rb")
        except Exception as exc:
            raise StorageAccessError(
                f"{self.provider.upper()} open failed: {redact_storage_error(str(exc))}",
                provider=self.provider,
            ) from exc

    def materialize(
        self,
        uri: str,
        target: Path,
        expected_revision: StorageRevision | None = None,
    ) -> StorageRevision:
        before = self.stat(uri)
        if expected_revision and not before.same_object_state_as(expected_revision):
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
            if not before.same_object_state_as(after):
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

    def _object_from_info(
        self, path: str, info: dict, *, source_uri: str | None = None
    ) -> StorageObject:
        canonical = self._canonical_from_path(
            str(info.get("name") or info.get("Key") or path),
            source_uri=source_uri,
        )
        modified = _modified(info)
        return StorageObject(
            canonical_uri=canonical,
            name=canonical.rstrip("/").rsplit("/", 1)[-1],
            object_type=_object_type(info),
            size=_size(info),
            last_modified=modified,
            provider_revision=_provider_revision(self.provider, info),
        )

    def _path(self, uri: str) -> str:
        strip = getattr(self._filesystem, "_strip_protocol", None)
        return str(strip(uri) if callable(strip) else uri)

    def _canonical_from_path(self, path: str, *, source_uri: str | None = None) -> str:
        if "://" in path:
            return canonical_cloud_uri(path, self.provider)
        if self.provider == "adls" and source_uri:
            return self._canonical_adls_path(path, source_uri)
        scheme = {"gcs": "gs", "adls": "abfs"}.get(self.provider, "s3")
        return canonical_cloud_uri(f"{scheme}://{path.lstrip('/')}", self.provider)

    def _canonical_adls_path(self, path: str, source_uri: str) -> str:
        source = urlsplit(canonical_cloud_uri(source_uri, "adls"))
        raw_path = path.lstrip("/")
        container = source.netloc.split("@", 1)[0]
        if raw_path == container:
            raw_path = ""
        elif raw_path.startswith(f"{container}/"):
            raw_path = raw_path[len(container) + 1 :]
        elif "@" in raw_path.split("/", 1)[0]:
            return canonical_cloud_uri(f"abfs://{raw_path}", "adls")
        return canonical_cloud_uri(
            f"abfs://{source.netloc}/{raw_path}" if raw_path else f"abfs://{source.netloc}",
            "adls",
        )


def _object_type(info: dict) -> str:
    value = str(info.get("type") or info.get("StorageClass") or "file").lower()
    return "directory" if value in {"directory", "dir"} else "file"


def _size(info: dict) -> int | None:
    value = info.get("size", info.get("Size"))
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _modified(info: dict) -> datetime | None:
    value = (
        info.get("LastModified")
        or info.get("last_modified")
        or info.get("mtime")
        or info.get("updated")
        or info.get("creation_time")
    )
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
    except ValueError:
        return None


def _provider_revision(provider: str, info: dict) -> str | None:
    if provider in {"s3", "minio"}:
        version = info.get("VersionId") or info.get("version_id")
        etag = info.get("ETag") or info.get("etag")
        return str(version or etag).strip('"') if version or etag else None
    if provider == "adls":
        version = info.get("version_id")
        etag = info.get("etag") or info.get("ETag")
        return str(version or etag).strip('"') if version or etag else None
    if provider == "gcs":
        generation = info.get("generation")
        metageneration = info.get("metageneration")
        if generation is not None:
            return (
                f"{generation}:{metageneration}"
                if metageneration is not None
                else str(generation)
            )
    return None
def _epoch() -> datetime:
    return datetime.fromtimestamp(0, tz=timezone.utc)
