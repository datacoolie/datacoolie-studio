from __future__ import annotations

import hashlib
import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.domains.metadata.storage_io import (
    MetadataStorage,
    conditional_create_uri,
    conditional_replace_uri,
    current_storage_revision,
)
from datacoolie_studio.domains.storage.adapters import StorageRevision
from datacoolie_studio.domains.storage.errors import StorageConflictError
from datacoolie_studio.domains.storage.writers import (
    DatabricksVerifiedStorageWriter,
)


class _DatabricksState:
    def __init__(self, content: bytes | None) -> None:
        self.content = content
        self.version = 0
        self.uploads: list[tuple[str, bytes, bool]] = []
        self.failure: str | None = None

    def open_read(self):
        if self.content is None:
            raise FileNotFoundError("DBFS object is missing")
        return io.BytesIO(self.content)

    def revision(self, uri: str) -> StorageRevision:
        if self.content is None:
            raise FileNotFoundError(uri)
        return StorageRevision(
            canonical_uri=uri,
            size=len(self.content),
            last_modified=(
                datetime(2026, 9, 18, tzinfo=timezone.utc)
                + timedelta(seconds=self.version)
            ),
        )


class _DatabricksEndpoint:
    def __init__(self, name: str, state: _DatabricksState) -> None:
        self.name = name
        self.state = state

    def upload(self, path: str, stream, *, overwrite: bool) -> None:
        content = stream.read()
        self.state.uploads.append((self.name, content, overwrite))
        if not overwrite and self.state.content is not None:
            raise _ResourceAlreadyExists()
        if self.state.failure == "before":
            raise TimeoutError("upload timed out before publication")
        self.state.version += 1
        self.state.content = (
            b"unexpected external content"
            if self.state.failure == "mismatch"
            else content
        )
        if self.state.failure == "after":
            raise TimeoutError("upload timed out after publication")


class _ResourceAlreadyExists(RuntimeError):
    error_code = "RESOURCE_ALREADY_EXISTS"


class _DatabricksAdapter:
    provider = "dbfs"

    def __init__(self, state: _DatabricksState) -> None:
        self.state = state

    def stat(self, uri: str) -> StorageRevision:
        return self.state.revision(uri)

    def open_read(self, _uri: str):
        return self.state.open_read()


def _storage(
    uri: str,
    content: bytes | None,
) -> tuple[MetadataStorage, _DatabricksState]:
    state = _DatabricksState(content)
    client = SimpleNamespace(
        files=_DatabricksEndpoint("files", state),
        dbfs=_DatabricksEndpoint("dbfs", state),
    )
    writer = DatabricksVerifiedStorageWriter(
        client,
        lambda _uri: state.open_read(),
    )
    return MetadataStorage(adapter=_DatabricksAdapter(state), writer=writer), state


@pytest.mark.parametrize(
    ("uri", "endpoint"),
    [
        ("dbfs:/Volumes/catalog/schema/volume/metadata.json", "files"),
        ("dbfs:/metadata/metadata.json", "dbfs"),
    ],
)
def test_dbfs_replace_returns_content_verified_receipt(
    uri: str,
    endpoint: str,
):
    storage, state = _storage(uri, b'{"version":1}')
    expected = current_storage_revision(storage, uri)
    replacement = b'{"version":2}'

    receipt = conditional_replace_uri(storage, uri, replacement, expected)

    assert state.content == replacement
    assert state.uploads == [(endpoint, replacement, True)]
    assert receipt.content_hash == hashlib.sha256(replacement).hexdigest()
    assert receipt.provider_revision is None
    assert receipt.verification == "content_readback"


def test_dbfs_stale_replace_rejects_before_upload():
    uri = "dbfs:/Volumes/catalog/schema/volume/metadata.json"
    storage, state = _storage(uri, b"original")
    expected = current_storage_revision(storage, uri)
    state.content = b"external"
    state.version += 1

    with pytest.raises(StorageConflictError):
        conditional_replace_uri(storage, uri, b"studio", expected)

    assert state.content == b"external"
    assert state.uploads == []


def test_dbfs_post_write_mismatch_is_not_reported_as_success():
    uri = "dbfs:/Volumes/catalog/schema/volume/metadata.json"
    storage, state = _storage(uri, b"original")
    expected = current_storage_revision(storage, uri)
    state.failure = "mismatch"

    with pytest.raises(MetadataReadError, match="could not be confirmed"):
        conditional_replace_uri(storage, uri, b"studio", expected)

    assert state.uploads == [("files", b"studio", True)]
    assert state.content == b"unexpected external content"


@pytest.mark.parametrize("failure", ["before", "after"])
def test_dbfs_timeout_is_reconciled_without_replaying_upload(failure: str):
    uri = "dbfs:/Volumes/catalog/schema/volume/metadata.json"
    storage, state = _storage(uri, b"original")
    expected = current_storage_revision(storage, uri)
    state.failure = failure

    if failure == "before":
        with pytest.raises(MetadataReadError, match="Metadata write failed"):
            conditional_replace_uri(storage, uri, b"studio", expected)
        assert state.content == b"original"
    else:
        receipt = conditional_replace_uri(storage, uri, b"studio", expected)
        assert receipt.content_hash == hashlib.sha256(b"studio").hexdigest()
        assert state.content == b"studio"

    assert len(state.uploads) == 1


def test_dbfs_create_uses_create_if_absent_and_readback():
    uri = "dbfs:/Volumes/catalog/schema/volume/new.json"
    storage, state = _storage(uri, None)

    receipt = conditional_create_uri(storage, uri, b"new")

    assert state.uploads == [("files", b"new", False)]
    assert receipt.content_hash == hashlib.sha256(b"new").hexdigest()
    assert receipt.provider_revision is None


def test_dbfs_create_collision_preserves_existing_content():
    uri = "dbfs:/metadata/existing.json"
    storage, state = _storage(uri, b"existing")

    with pytest.raises(StorageConflictError, match="already exists"):
        conditional_create_uri(storage, uri, b"new")

    assert state.content == b"existing"
    assert state.uploads == [("dbfs", b"new", False)]
