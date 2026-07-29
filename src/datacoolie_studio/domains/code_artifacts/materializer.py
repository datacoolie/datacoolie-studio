from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from sqlalchemy.orm import Session

from datacoolie_studio.core.config import source_materialization_cache_dir
from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.code_artifacts.indexer import (
    MAX_FILES,
    MAX_FILE_SIZE,
    MAX_TOTAL_SIZE,
    ArtifactIndexError,
)
from datacoolie_studio.domains.credentials.store import (
    CredentialSecretStore,
    KeyringCredentialSecretStore,
)
from datacoolie_studio.domains.sources.scan_policy import (
    CODE_SCAN_EXCLUDED_DIRECTORIES,
)
from datacoolie_studio.domains.sources.storage_binding import binding_from_source
from datacoolie_studio.domains.storage.adapters import (
    StorageObject,
    StorageRevision,
)
from datacoolie_studio.domains.storage.factory import create_storage_adapter
from datacoolie_studio.domains.storage.inventory import (
    StorageInventoryRequest,
    inventory,
)
from datacoolie_studio.domains.storage.uri import uri_basename


def materialize_remote_artifact(
    session: Session,
    source: EnvironmentSource,
    artifact_type: str,
    *,
    secret_store: CredentialSecretStore | None = None,
) -> tuple[str, dict[str, object]]:
    adapter = create_storage_adapter(
        binding_from_source(source),
        uri=source.uri,
        session=session,
        secret_store=secret_store or KeyringCredentialSecretStore(),
    )
    if artifact_type == "directory":
        observed = inventory(
            adapter,
            StorageInventoryRequest(
                uri=source.uri,
                purpose="materialize",
                recursive=True,
                object_types=frozenset({"file"}),
                suffixes=frozenset({".py"}),
                exclude_directories=CODE_SCAN_EXCLUDED_DIRECTORIES,
                object_limit=MAX_FILES,
            ),
        )
        objects = list(observed.files)
        if observed.completeness != "complete":
            raise ArtifactIndexError(
                f"Code artifact exceeds {MAX_FILES} Python files"
            )
        if not objects:
            raise ArtifactIndexError(
                f"No Python files found in code artifact: {source.uri}"
            )
        entries = _validated_directory_entries(source.uri, objects)
        fingerprint = _fingerprint(
            [
                (
                    relative,
                    item.canonical_uri,
                    int(item.size or 0),
                    item.provider_revision,
                )
                for relative, item in entries
            ]
        )
        source_stat = _directory_revision(source, entries, fingerprint)
        object_states = _directory_object_states(entries)
        root_name = _safe_name(uri_basename(source.uri) or "artifact")
        snapshot = _snapshot_root(source.id) / fingerprint / root_name
        if not snapshot.is_dir():
            previous_snapshot, previous_objects = _current_snapshot_state(source.id)
            _publish_directory_snapshot(
                adapter,
                entries,
                snapshot,
                previous_snapshot=previous_snapshot,
                previous_objects=previous_objects,
            )
        source_stat["objects"] = object_states
    else:
        revision = adapter.stat(source.uri)
        if revision.size > MAX_TOTAL_SIZE:
            raise ArtifactIndexError(
                f"Code artifact exceeds {MAX_TOTAL_SIZE} bytes: {source.uri}"
            )
        fingerprint = _fingerprint(
            [
                (
                    revision.canonical_uri,
                    revision.size,
                    revision.last_modified.isoformat(),
                    revision.provider_revision,
                )
            ]
        )
        snapshot = (
            _snapshot_root(source.id)
            / fingerprint
            / _safe_name(uri_basename(source.uri) or f"artifact.{_extension(artifact_type)}")
        )
        if not snapshot.is_file():
            _publish_file_snapshot(adapter, source.uri, revision, snapshot)
        source_stat = {
            "provider": source.storage_provider,
            "uri": source.uri,
            "exists": True,
            "object_type": "file",
            "size": revision.size,
            "mtime_ns": int(revision.last_modified.timestamp() * 1_000_000_000),
            "provider_revision": revision.provider_revision,
        }
    _publish_current_marker(
        source.id,
        snapshot,
        artifact_type,
        fingerprint,
        objects=object_states if artifact_type == "directory" else None,
    )
    _cleanup_stale_snapshots(source.id, fingerprint)
    return snapshot.as_posix(), source_stat


def current_remote_snapshot(
    source: EnvironmentSource, artifact_type: str
) -> str:
    marker = _snapshot_root(source.id) / "current.json"
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        path = Path(str(payload["path"])).resolve()
        path.relative_to(_snapshot_root(source.id).resolve())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ArtifactIndexError(
            "Remote code snapshot is unavailable; refresh the code artifact"
        ) from exc
    if payload.get("artifact_type") != artifact_type or not path.exists():
        raise ArtifactIndexError(
            "Remote code snapshot is unavailable; refresh the code artifact"
        )
    return path.as_posix()


def clear_remote_artifact_snapshot(source_id: int) -> None:
    shutil.rmtree(_snapshot_root(source_id), ignore_errors=True)


def _validated_directory_entries(
    root_uri: str, objects: list[StorageObject]
) -> list[tuple[str, StorageObject]]:
    entries: list[tuple[str, StorageObject]] = []
    total_size = 0
    for item in sorted(objects, key=lambda value: value.canonical_uri):
        relative = _relative_key(root_uri, item.canonical_uri)
        size = int(item.size or 0)
        if size > MAX_FILE_SIZE:
            raise ArtifactIndexError(
                f"Python source file exceeds {MAX_FILE_SIZE} bytes: {relative}"
            )
        total_size += size
        if len(entries) + 1 > MAX_FILES:
            raise ArtifactIndexError(
                f"Code artifact exceeds {MAX_FILES} Python files"
            )
        if total_size > MAX_TOTAL_SIZE:
            raise ArtifactIndexError(
                f"Code artifact exceeds {MAX_TOTAL_SIZE} bytes"
            )
        entries.append((relative, item))
    return entries


def _publish_directory_snapshot(
    adapter,
    entries,
    snapshot: Path,
    *,
    previous_snapshot: Path | None,
    previous_objects: dict[str, dict[str, object]],
) -> None:
    revision_dir = snapshot.parent
    staging = revision_dir.with_name(f".{revision_dir.name}.{uuid4().hex}.tmp")
    staging_root = staging / snapshot.name
    try:
        for relative, item in entries:
            target = staging_root.joinpath(*PurePosixPath(relative).parts)
            previous = (
                previous_snapshot.joinpath(*PurePosixPath(relative).parts)
                if previous_snapshot is not None
                else None
            )
            if (
                previous is not None
                and previous.is_file()
                and previous_objects.get(relative) == _object_state(item)
            ):
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(previous, target)
                except OSError:
                    shutil.copy2(previous, target)
            else:
                adapter.materialize(
                    item.canonical_uri,
                    target,
                    expected_revision=_object_revision(adapter, item),
                )
        revision_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(staging, revision_dir)
        except FileExistsError:
            pass
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _object_revision(adapter, item: StorageObject) -> StorageRevision:
    if item.last_modified is None:
        return adapter.stat(item.canonical_uri)
    return StorageRevision(
        canonical_uri=item.canonical_uri,
        size=int(item.size or 0),
        last_modified=item.last_modified,
        provider_revision=item.provider_revision,
    )


def _publish_file_snapshot(adapter, uri: str, revision, snapshot: Path) -> None:
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    temporary = snapshot.with_name(f".{snapshot.name}.{uuid4().hex}.tmp")
    try:
        adapter.materialize(uri, temporary, expected_revision=revision)
        os.replace(temporary, snapshot)
    finally:
        temporary.unlink(missing_ok=True)


def _publish_current_marker(
    source_id: int,
    snapshot: Path,
    artifact_type: str,
    fingerprint: str,
    *,
    objects: dict[str, dict[str, object]] | None = None,
) -> None:
    root = _snapshot_root(source_id)
    root.mkdir(parents=True, exist_ok=True)
    marker = root / "current.json"
    temporary = root / f".current.{uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(
                {
                    "path": snapshot.resolve().as_posix(),
                    "artifact_type": artifact_type,
                    "fingerprint": fingerprint,
                    "objects": objects or {},
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, marker)
    finally:
        temporary.unlink(missing_ok=True)


def _cleanup_stale_snapshots(source_id: int, current: str) -> None:
    root = _snapshot_root(source_id)
    for child in root.iterdir():
        if child.is_dir() and child.name != current:
            shutil.rmtree(child, ignore_errors=True)


def _directory_revision(
    source: EnvironmentSource,
    entries: list[tuple[str, StorageObject]],
    fingerprint: str,
) -> dict[str, object]:
    modified = [
        item.last_modified for _, item in entries if item.last_modified is not None
    ]
    return {
        "provider": source.storage_provider,
        "uri": source.uri,
        "exists": True,
        "object_type": "directory",
        "file_count": len(entries),
        "total_size": sum(int(item.size or 0) for _, item in entries),
        "max_mtime_ns": (
            max(int(value.timestamp() * 1_000_000_000) for value in modified)
            if modified
            else None
        ),
        "provider_revision": fingerprint,
    }


def _directory_object_states(
    entries: list[tuple[str, StorageObject]],
) -> dict[str, dict[str, object]]:
    return {relative: _object_state(item) for relative, item in entries}


def _object_state(item: StorageObject) -> dict[str, object]:
    return {
        "canonical_uri": item.canonical_uri,
        "size": int(item.size or 0),
        "last_modified": (
            item.last_modified.isoformat() if item.last_modified is not None else None
        ),
        "provider_revision": item.provider_revision,
    }


def _current_snapshot_state(
    source_id: int,
) -> tuple[Path | None, dict[str, dict[str, object]]]:
    marker = _snapshot_root(source_id) / "current.json"
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        path = Path(str(payload["path"])).resolve()
        path.relative_to(_snapshot_root(source_id).resolve())
        raw_objects = payload.get("objects")
        objects = (
            {
                str(key): value
                for key, value in raw_objects.items()
                if isinstance(value, dict)
            }
            if isinstance(raw_objects, dict)
            else {}
        )
        return (path if path.is_dir() else None), objects
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None, {}


def _relative_key(root_uri: str, object_uri: str) -> str:
    root = urlsplit(root_uri)
    child = urlsplit(object_uri)
    if (root.scheme.lower(), root.netloc.lower()) != (
        child.scheme.lower(),
        child.netloc.lower(),
    ):
        raise ArtifactIndexError("Code object escapes the configured prefix")
    root_path = PurePosixPath(unquote(root.path).strip("/"))
    child_path = PurePosixPath(unquote(child.path).strip("/"))
    try:
        relative = child_path.relative_to(root_path)
    except ValueError as exc:
        raise ArtifactIndexError(
            "Code object escapes the configured prefix"
        ) from exc
    if not relative.parts or ".." in relative.parts:
        raise ArtifactIndexError("Unsafe code object key")
    return relative.as_posix()


def _snapshot_root(source_id: int) -> Path:
    return source_materialization_cache_dir() / f"source-{source_id}"


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _safe_name(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in value
    )
    return normalized.strip(".") or "artifact"


def _extension(artifact_type: str) -> str:
    return {"python_file": "py", "wheel": "whl", "zip": "zip"}.get(
        artifact_type, "bin"
    )
