"""Opt-in Databricks write-back contract for an operator-owned disposable root.

The test is excluded from the default suite. It requires both an explicit root and
an acknowledgement so a normal credentialed workspace cannot be mutated by an
ordinary live-test run.
"""

from __future__ import annotations

import hashlib
import os
from uuid import uuid4

import pytest

from datacoolie_studio.domains.metadata.storage_io import (
    MetadataStorage,
    conditional_create_uri,
    conditional_replace_uri,
    current_storage_revision,
)
from datacoolie_studio.domains.storage.binding import StorageBinding
from datacoolie_studio.domains.storage.errors import StorageConflictError
from datacoolie_studio.domains.storage.factory import (
    create_storage_adapter,
    create_storage_writer,
)
from datacoolie_studio.domains.storage.uri import (
    canonical_cloud_uri,
    join_uri,
    transport_storage_uri,
)


pytestmark = pytest.mark.live

_TEST_ROOT = os.getenv("DATACOOLIE_DATABRICKS_WRITEBACK_TEST_ROOT")
_LEGACY_ROOT = os.getenv("DATACOOLIE_DATABRICKS_WRITEBACK_TEST_LEGACY_ROOT")
_CONFIRMATION = os.getenv("DATACOOLIE_DATABRICKS_WRITEBACK_TEST_CONFIRM")


@pytest.mark.skipif(
    not _TEST_ROOT or _CONFIRMATION != "I_UNDERSTAND",
    reason=(
        "Set a disposable Databricks root and "
        "DATACOOLIE_DATABRICKS_WRITEBACK_TEST_CONFIRM=I_UNDERSTAND"
    ),
)
def test_disposable_databricks_root_supports_verified_writeback() -> None:
    roots = [("configured", _TEST_ROOT)]
    if _LEGACY_ROOT:
        roots.append(("legacy", _LEGACY_ROOT))

    failures: list[str] = []
    for family, raw_root in roots:
        if not raw_root:
            continue
        failure = _exercise_root(family, raw_root)
        if failure is not None:
            failures.append(failure)

    if failures:
        pytest.fail("; ".join(failures))


def _exercise_root(family: str, raw_root: str) -> str | None:
    try:
        root = _safe_test_root(raw_root)
    except Exception:
        return f"{family} root is not an eligible disposable DBFS root"

    target = join_uri(root, f".datacoolie-studio-writeback-{uuid4().hex}.json")
    binding = StorageBinding(provider="dbfs", auth_mode="ambient")
    adapter = None
    created = False
    failure: str | None = None
    replacement = b'{"writeback":"replacement"}'
    try:
        adapter = create_storage_adapter(binding, uri=target)
        writer = create_storage_writer(binding, uri=target)
        storage = MetadataStorage(adapter=adapter, writer=writer)
        initial = b'{"writeback":"initial"}'

        receipt = conditional_create_uri(storage, target, initial)
        created = True
        if receipt.content_hash != _sha256(initial):
            raise AssertionError("create readback hash mismatch")
        if _read_all(adapter, target) != initial:
            raise AssertionError("create readback content mismatch")

        expected = current_storage_revision(storage, target)
        replaced = conditional_replace_uri(storage, target, replacement, expected)
        if replaced.content_hash != _sha256(replacement):
            raise AssertionError("replace readback hash mismatch")
        if _read_all(adapter, target) != replacement:
            raise AssertionError("replace readback content mismatch")

        try:
            conditional_create_uri(storage, target, b"collision")
        except StorageConflictError:
            pass
        else:
            raise AssertionError("create collision was not rejected")
    except Exception as exc:
        failure = f"{family} contract failed with {type(exc).__name__}"
    finally:
        if created and adapter is not None:
            try:
                if _read_all(adapter, target) != replacement:
                    failure = failure or (
                        f"{family} cleanup skipped because the test target changed"
                    )
                else:
                    _delete_exact_target(adapter, target)
            except Exception as exc:
                failure = failure or (
                    f"{family} cleanup verification failed with {type(exc).__name__}"
                )
    return failure


def _safe_test_root(raw_root: str) -> str:
    root = canonical_cloud_uri(raw_root, provider="dbfs").rstrip("/")
    path = root.removeprefix("dbfs:").strip("/")
    parts = [part for part in path.split("/") if part]
    protected = {"", "Volumes", "mnt", "FileStore", "user", "users"}
    if path in protected or len(parts) < 2:
        raise ValueError("root is too broad")
    if parts[0].lower() == "volumes" and len(parts) < 4:
        raise ValueError("volume root is too broad")
    return root


def _read_all(adapter, uri: str) -> bytes:
    with adapter.open_read(uri) as stream:
        return stream.read()


def _delete_exact_target(adapter, uri: str) -> None:
    client = getattr(adapter, "_client")
    transport = transport_storage_uri(uri, "dbfs")
    path = "/" + transport.removeprefix("dbfs:").lstrip("/")
    if path == "/Volumes" or path.startswith("/Volumes/"):
        client.files.delete(path)
    else:
        client.dbfs.delete(path, recursive=False)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
