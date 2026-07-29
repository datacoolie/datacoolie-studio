from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from datacoolie_studio.domains.storage.adapters import StorageRevision
from datacoolie_studio.domains.storage.errors import StorageConflictError
from datacoolie_studio.domains.storage.uri import require_local_path, validate_storage_uri


class ConditionalStorageWriter(Protocol):
    def replace(
        self, uri: str, content: bytes, expected_revision: StorageRevision
    ) -> str | None: ...

    def create(self, uri: str, content: bytes) -> str | None: ...


class LocalConditionalStorageWriter:
    def replace(
        self, uri: str, content: bytes, expected_revision: StorageRevision
    ) -> str | None:
        path = require_local_path(uri)
        state = path.stat()
        current = f"{state.st_mtime_ns}:{state.st_size}"
        if current != expected_revision.provider_revision:
            raise StorageConflictError(uri)
        _atomic_write(path, content, must_not_exist=False)
        updated = path.stat()
        return f"{updated.st_mtime_ns}:{updated.st_size}"

    def create(self, uri: str, content: bytes) -> str | None:
        path = require_local_path(uri)
        if path.exists():
            raise StorageConflictError(uri, "Storage object already exists")
        _atomic_write(path, content, must_not_exist=True)
        state = path.stat()
        return f"{state.st_mtime_ns}:{state.st_size}"


class S3ConditionalStorageWriter:
    def __init__(self, client) -> None:
        self._client = client

    def replace(
        self, uri: str, content: bytes, expected_revision: StorageRevision
    ) -> str | None:
        bucket, key = _bucket_key(uri, "s3")
        try:
            response = self._client.put_object(
                Bucket=bucket,
                Key=key,
                Body=content,
                IfMatch=expected_revision.provider_revision,
            )
        except Exception as exc:
            if _is_precondition_failure(exc):
                raise StorageConflictError(uri) from exc
            raise
        return _s3_revision(response)

    def create(self, uri: str, content: bytes) -> str | None:
        bucket, key = _bucket_key(uri, "s3")
        try:
            response = self._client.put_object(
                Bucket=bucket, Key=key, Body=content, IfNoneMatch="*"
            )
        except Exception as exc:
            if _is_precondition_failure(exc):
                raise StorageConflictError(uri, "Storage object already exists") from exc
            raise
        return _s3_revision(response)


class AdlsConditionalStorageWriter:
    def __init__(self, blob_client_factory, *, if_not_modified) -> None:
        self._blob_client_factory = blob_client_factory
        self._if_not_modified = if_not_modified

    def replace(
        self, uri: str, content: bytes, expected_revision: StorageRevision
    ) -> str | None:
        client = self._blob_client_factory(uri)
        try:
            response = client.upload_blob(
                content,
                overwrite=True,
                etag=expected_revision.provider_revision,
                match_condition=self._if_not_modified,
            )
        except Exception as exc:
            if _is_precondition_failure(exc):
                raise StorageConflictError(uri) from exc
            raise
        return _response_value(response, "etag")

    def create(self, uri: str, content: bytes) -> str | None:
        client = self._blob_client_factory(uri)
        try:
            response = client.upload_blob(content, overwrite=False)
        except Exception as exc:
            if _is_precondition_failure(exc):
                raise StorageConflictError(uri, "Storage object already exists") from exc
            raise
        return _response_value(response, "etag")


class GcsConditionalStorageWriter:
    def __init__(self, bucket_factory) -> None:
        self._bucket_factory = bucket_factory

    def replace(
        self, uri: str, content: bytes, expected_revision: StorageRevision
    ) -> str | None:
        bucket_name, key = _bucket_key(uri, "gcs")
        blob = self._bucket_factory(bucket_name).blob(key)
        generation = _gcs_generation(expected_revision.provider_revision)
        try:
            blob.upload_from_string(content, if_generation_match=generation)
        except Exception as exc:
            if _is_precondition_failure(exc):
                raise StorageConflictError(uri) from exc
            raise
        return _gcs_blob_revision(blob)

    def create(self, uri: str, content: bytes) -> str | None:
        bucket_name, key = _bucket_key(uri, "gcs")
        blob = self._bucket_factory(bucket_name).blob(key)
        try:
            blob.upload_from_string(content, if_generation_match=0)
        except Exception as exc:
            if _is_precondition_failure(exc):
                raise StorageConflictError(uri, "Storage object already exists") from exc
            raise
        return _gcs_blob_revision(blob)


def _atomic_write(path: Path, content: bytes, *, must_not_exist: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        if must_not_exist and path.exists():
            raise StorageConflictError(str(path), "Storage object already exists")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _bucket_key(uri: str, provider: str) -> tuple[str, str]:
    validate_storage_uri(uri, provider)
    parsed = urlsplit(uri)
    return parsed.netloc, parsed.path.lstrip("/")


def _s3_revision(response: object) -> str | None:
    if not isinstance(response, dict):
        return None
    value = response.get("VersionId") or response.get("ETag")
    return str(value).strip('"') if value else None


def _gcs_generation(value: str | None) -> int:
    if not value:
        raise ValueError("GCS conditional writes require an object generation")
    return int(value.split(":", 1)[0])


def _gcs_blob_revision(blob) -> str | None:
    generation = getattr(blob, "generation", None)
    metageneration = getattr(blob, "metageneration", None)
    if generation is None:
        return None
    return (
        f"{generation}:{metageneration}"
        if metageneration is not None
        else str(generation)
    )


def _response_value(response: object, key: str) -> str | None:
    if isinstance(response, dict):
        value = response.get(key) or response.get(key.upper())
    else:
        value = getattr(response, key, None)
    return str(value).strip('"') if value else None


def _is_precondition_failure(exc: Exception) -> bool:
    status = (
        getattr(exc, "status_code", None)
        or getattr(exc, "status", None)
        or getattr(getattr(exc, "response", None), "status_code", None)
    )
    if status in {409, 412}:
        return True
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return code in {409, 412}
    return exc.__class__.__name__ in {
        "PreconditionFailed",
        "ResourceExistsError",
    }
