from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from datacoolie_studio.core.config import backup_dir
from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.credentials.store import (
    CredentialSecretStore,
    KeyringCredentialSecretStore,
)
from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.domains.sources.storage_binding import binding_from_source
from datacoolie_studio.domains.storage.adapters import (
    StorageAdapter,
    StorageRevision,
)
from datacoolie_studio.domains.storage.errors import StorageConflictError
from datacoolie_studio.domains.storage.factory import (
    create_storage_adapter,
    create_storage_writer,
)
from datacoolie_studio.domains.storage.uri import uri_basename
from datacoolie_studio.domains.storage.writers import ConditionalStorageWriter


@dataclass
class MetadataStorage:
    adapter: StorageAdapter
    writer: ConditionalStorageWriter | None = None


def storage_for_source(
    session: Session,
    source: EnvironmentSource,
    *,
    secret_store: CredentialSecretStore | None = None,
    writable: bool = False,
) -> MetadataStorage:
    store = secret_store or KeyringCredentialSecretStore()
    binding = binding_from_source(source)
    return MetadataStorage(
        adapter=create_storage_adapter(
            binding, uri=source.uri, session=session, secret_store=store
        ),
        writer=(
            create_storage_writer(
                binding, uri=source.uri, session=session, secret_store=store
            )
            if writable
            else None
        ),
    )


def read_source_bytes(
    storage: MetadataStorage,
    source: EnvironmentSource,
    *,
    expected_revision: StorageRevision | None = None,
) -> tuple[bytes, dict[str, object]]:
    revisioned_read = getattr(
        storage.adapter, "open_read_with_revision", None
    )
    if callable(revisioned_read):
        try:
            handle, observed = revisioned_read(source.uri)
        except Exception as exc:
            raise MetadataReadError("Cannot read metadata object") from exc
        if (
            expected_revision is not None
            and not expected_revision.same_object_state_as(observed)
        ):
            try:
                handle.close()
            except Exception:
                pass
            raise MetadataReadError(
                "Metadata object changed before it was read"
            )
        if expected_revision is not None:
            observed = replace(
                observed,
                provider_revision=expected_revision.provider_revision,
            )
        return _read_revisioned_bytes(handle, observed)

    before = expected_revision or storage.adapter.stat(source.uri)
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    try:
        with storage.adapter.open_read(source.uri) as handle:
            while chunk := handle.read(1024 * 1024):
                chunks.append(chunk)
                digest.update(chunk)
    except Exception as exc:
        raise MetadataReadError("Cannot read metadata object") from exc
    after = storage.adapter.stat(source.uri)
    if not before.same_content_as(after):
        raise MetadataReadError("Metadata object changed while it was being read")
    return b"".join(chunks), revision_dict(after, content_hash=digest.hexdigest())


def _read_revisioned_bytes(
    handle, revision: StorageRevision
) -> tuple[bytes, dict[str, object]]:
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    try:
        with handle:
            while chunk := handle.read(1024 * 1024):
                chunks.append(chunk)
                digest.update(chunk)
    except Exception as exc:
        raise MetadataReadError("Cannot read metadata object") from exc
    return (
        b"".join(chunks),
        revision_dict(revision, content_hash=digest.hexdigest()),
    )


def current_revision(
    storage: MetadataStorage,
    source: EnvironmentSource,
    *,
    include_content_hash: bool = True,
) -> dict[str, object]:
    if include_content_hash:
        _, revision = read_source_bytes(storage, source)
        return revision
    return revision_dict(storage.adapter.stat(source.uri))


def conditional_replace(
    storage: MetadataStorage,
    source: EnvironmentSource,
    content: bytes,
    expected_revision: dict[str, object],
    *,
    verified_revision: dict[str, object] | None = None,
) -> dict[str, object]:
    if storage.writer is None:
        raise MetadataReadError("Metadata storage is not writable")
    actual = (
        verified_revision
        if verified_revision is not None
        else current_revision(storage, source, include_content_hash=True)
    )
    if not same_revision(actual, expected_revision):
        raise StorageConflictError(source.uri)
    token = storage_revision_from_dict(actual)
    intended_hash = hashlib.sha256(content).hexdigest()
    try:
        storage.writer.replace(source.uri, content, token)
    except StorageConflictError:
        raise
    except Exception as exc:
        # Reconcile timeout-after-success without replaying an unsafe write.
        try:
            reconciled = current_revision(
                storage, source, include_content_hash=True
            )
        except Exception:
            raise MetadataReadError("Metadata write outcome is unknown") from exc
        if reconciled.get("content_hash") == intended_hash:
            return reconciled
        raise MetadataReadError("Metadata write failed") from exc
    saved = current_revision(storage, source, include_content_hash=True)
    if saved.get("content_hash") != intended_hash:
        raise MetadataReadError("Metadata write could not be confirmed")
    return saved


def conditional_create(
    storage: MetadataStorage,
    source: EnvironmentSource,
    content: bytes,
) -> dict[str, object]:
    if storage.writer is None:
        raise MetadataReadError("Metadata storage is not writable")
    try:
        storage.writer.create(source.uri, content)
    except StorageConflictError:
        raise
    except Exception as exc:
        try:
            reconciled = current_revision(
                storage, source, include_content_hash=True
            )
        except Exception:
            raise MetadataReadError("Metadata create outcome is unknown") from exc
        if reconciled.get("content_hash") == hashlib.sha256(content).hexdigest():
            return reconciled
        raise MetadataReadError("Metadata create failed") from exc
    return current_revision(storage, source, include_content_hash=True)


def create_local_backup(
    source: EnvironmentSource,
    content: bytes,
    revision: dict[str, object],
) -> Path:
    project_id = source.environment.project_id if source.environment else 0
    target_dir = (
        backup_dir()
        / f"project-{project_id}"
        / f"env-{source.environment_id}"
        / f"source-{source.id}"
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    name = uri_basename(source.uri) or "metadata.json"
    revision_token = str(
        revision.get("provider_revision")
        or revision.get("content_hash")
        or "unknown"
    )
    safe_token = hashlib.sha256(revision_token.encode("utf-8")).hexdigest()[:16]
    target = target_dir / f"{safe_token}-{name}"
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_bytes(content)
    temporary.replace(target)
    return target


def revision_dict(
    revision: StorageRevision, *, content_hash: str | None = None
) -> dict[str, object]:
    mtime_ns = int(revision.last_modified.timestamp() * 1_000_000_000)
    return {
        "provider": _provider_from_uri(revision.canonical_uri),
        "canonical_uri": revision.canonical_uri,
        "exists": True,
        "object_type": "file",
        "size": revision.size,
        "mtime_ns": mtime_ns,
        "last_modified": revision.last_modified.isoformat(),
        "provider_revision": revision.provider_revision,
        "content_hash": content_hash or revision.content_hash,
    }


def same_revision(
    left: dict[str, object], right: dict[str, object]
) -> bool:
    if left.get("content_hash") and right.get("content_hash"):
        return (
            str(left["content_hash"]) == str(right["content_hash"])
            and int(left.get("size", -1)) == int(right.get("size", -2))
        )
    return (
        str(left.get("provider_revision")) == str(right.get("provider_revision"))
        and int(left.get("size", -1)) == int(right.get("size", -2))
    )


def storage_revision_from_dict(
    value: dict[str, object],
) -> StorageRevision:
    modified = value.get("last_modified")
    if isinstance(modified, str):
        try:
            parsed = datetime.fromisoformat(modified.replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.fromtimestamp(0, tz=timezone.utc)
    else:
        parsed = datetime.fromtimestamp(0, tz=timezone.utc)
    return StorageRevision(
        canonical_uri=str(value.get("canonical_uri") or ""),
        size=int(value.get("size") or 0),
        last_modified=parsed.replace(tzinfo=parsed.tzinfo or timezone.utc),
        provider_revision=(
            str(value["provider_revision"])
            if value.get("provider_revision") is not None
            else None
        ),
        content_hash=(
            str(value["content_hash"])
            if value.get("content_hash") is not None
            else None
        ),
    )


def _provider_from_uri(uri: str) -> str:
    if uri.startswith("dbfs:/"):
        return "dbfs"
    if uri.startswith("s3://"):
        return "s3"
    if uri.startswith(("abfs://", "abfss://")) and (
        "@onelake.dfs.fabric.microsoft.com/" in uri.lower()
    ):
        return "onelake"
    if uri.startswith(("abfs://", "abfss://")):
        return "adls"
    if uri.startswith(("gs://", "gcs://")):
        return "gcs"
    return "local"
