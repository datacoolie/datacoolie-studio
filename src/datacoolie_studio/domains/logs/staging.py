from __future__ import annotations

import hashlib
import shutil
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from threading import Lock
from typing import Iterator

from datacoolie_studio.core.config import source_materialization_cache_dir
from datacoolie_studio.domains.logs.discovery import DiscoveredLogFile
from datacoolie_studio.domains.storage.adapters import StorageAdapter
from datacoolie_studio.domains.storage.uri import uri_basename


class _StagingScope:
    def __init__(self, source_id: int) -> None:
        self.source_id = source_id
        self.paths: list[Path] = []


_scope: ContextVar[_StagingScope | None] = ContextVar(
    "datacoolie_log_staging_scope",
    default=None,
)
_active_paths: set[Path] = set()
_active_paths_lock = Lock()


@contextmanager
def staging_scope(source_id: int) -> Iterator[None]:
    """Own every sync directory created by one refresh invocation."""

    value = _StagingScope(source_id)
    token = _scope.set(value)
    try:
        yield
    finally:
        for path in reversed(value.paths):
            remove_staging_dir(path)
        _scope.reset(token)


def create_staging_dir(source_id: int) -> Path:
    root = source_materialization_cache_dir() / "logs" / f"source-{source_id}"
    root.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="sync-", dir=root))
    resolved = path.resolve()
    with _active_paths_lock:
        _active_paths.add(resolved)
    current_scope = _scope.get()
    if current_scope is not None and current_scope.source_id == source_id:
        current_scope.paths.append(resolved)
    return path


def remove_staging_dir(path: Path) -> None:
    """Remove only an owned sync directory after validating its shape."""

    root = (source_materialization_cache_dir() / "logs").resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return
    if (
        len(relative.parts) != 2
        or not relative.parts[0].startswith("source-")
        or not relative.parts[1].startswith("sync-")
    ):
        return
    with _active_paths_lock:
        _active_paths.discard(resolved)
    shutil.rmtree(resolved, ignore_errors=True)


def cleanup_orphaned_staging() -> dict[str, int]:
    """Remove stale owned sync directories left by a terminated process."""

    root = source_materialization_cache_dir() / "logs"
    cleaned = 0
    errors = 0
    if not root.is_dir():
        return {"cleaned": 0, "errors": 0}
    with _active_paths_lock:
        active = set(_active_paths)
    for source_root in root.iterdir():
        if not source_root.is_dir() or not source_root.name.startswith("source-"):
            continue
        for child in source_root.iterdir():
            if (
                not child.is_dir()
                or not child.name.startswith("sync-")
                or child.resolve() in active
            ):
                continue
            try:
                shutil.rmtree(child)
                cleaned += 1
            except OSError:
                errors += 1
    return {"cleaned": cleaned, "errors": errors}


def materialize_log_uri(
    adapter: StorageAdapter,
    candidate: DiscoveredLogFile,
    staging_dir: Path,
    provider: str,
) -> str:
    if provider == "local":
        return candidate.canonical_uri
    target = materialized_log_path(candidate, staging_dir)
    adapter.materialize(
        candidate.canonical_uri,
        target,
        expected_revision=candidate.revision,
    )
    return target.as_posix()


def materialized_log_path(candidate: DiscoveredLogFile, staging_dir: Path) -> Path:
    digest = hashlib.sha256(candidate.canonical_uri.encode("utf-8")).hexdigest()[:16]
    return staging_dir / f"{digest}-{uri_basename(candidate.canonical_uri)}"


def cleanup_materialized_log(
    candidate: DiscoveredLogFile,
    staging_dir: Path,
    provider: str,
) -> int:
    """Remove only the owned remote target and its interrupted-download temp."""

    if provider == "local":
        return 0
    target = materialized_log_path(candidate, staging_dir)
    failures = 0
    for path in (target, Path(f"{target}.tmp")):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            failures += 1
    return failures


def cleanup_materialized_path(path: str, staging_dir: Path) -> int:
    """Remove a single file only when it is a direct child of owned staging."""

    resolved = Path(path).resolve()
    root = staging_dir.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return 0
    if len(relative.parts) != 1:
        return 0
    try:
        resolved.unlink()
    except FileNotFoundError:
        return 0
    except OSError:
        return 1
    return 0
