from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from datacoolie_studio.domains.code_artifacts.indexer import ArtifactIndexError, build_artifact_index
from datacoolie_studio.domains.metadata.reader import MetadataReadError, read_metadata_file
from datacoolie_studio.domains.metadata.reader import read_metadata_bytes
from datacoolie_studio.domains.sources.scan_policy import (
    CODE_SCAN_EXCLUDED_DIRECTORIES,
    METADATA_SCAN_EXCLUDED_DIRECTORIES,
)
from datacoolie_studio.domains.storage.adapters import StorageAdapter, StorageObject
from datacoolie_studio.domains.storage.inventory import (
    StorageInventoryRequest,
    inventory,
)
from datacoolie_studio.domains.storage.uri import StorageProviderNotEnabled, join_uri, require_local_path, uri_basename


METADATA_SUFFIXES = {".json", ".yaml", ".yml", ".xlsx", ".xls"}


@dataclass(frozen=True)
class DiscoveredSource:
    source_kind: str
    uri: str
    label: str | None = None
    source_config: dict[str, Any] = field(default_factory=dict)
    record_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceDiscoveryResult:
    metadata_sources: list[DiscoveredSource]
    code_artifacts: list[DiscoveredSource]
    errors: list[dict[str, str]]


def discover_metadata_sources(
    uri: str,
    *,
    label: str | None = None,
    adapter: StorageAdapter | None = None,
    inspect_contents: bool = True,
) -> SourceDiscoveryResult:
    if adapter is not None:
        return _discover_remote_metadata_sources(
            uri,
            label=label,
            adapter=adapter,
            inspect_contents=inspect_contents,
        )
    try:
        path = require_local_path(uri)
    except StorageProviderNotEnabled as exc:
        return SourceDiscoveryResult([], [], [{"uri": exc.uri, "message": str(exc), "provider": exc.provider}])

    if not path.exists():
        return SourceDiscoveryResult([], [], [{"uri": uri, "message": f"Metadata path not found: {uri}"}])

    if path.is_file():
        source = _metadata_file_source(path, base_dir=path.parent, label=label)
        if source is None:
            return SourceDiscoveryResult([], [], [{"uri": str(path), "message": "File does not contain datacoolie metadata sections"}])
        return SourceDiscoveryResult([source], [], [])

    if not path.is_dir():
        return SourceDiscoveryResult([], [], [{"uri": uri, "message": f"Unsupported metadata path type: {uri}"}])

    sources: list[DiscoveredSource] = []
    errors: list[dict[str, str]] = []
    for candidate in sorted(path.rglob("*"), key=lambda item: _metadata_sort_key(path, item)):
        if not candidate.is_file() or candidate.suffix.lower() not in METADATA_SUFFIXES:
            continue
        if _is_environments_file(path, candidate):
            continue
        source = _metadata_file_source(candidate, base_dir=path)
        if source is None:
            continue
        sources.append(source)

    if not sources:
        errors.append({"uri": uri, "message": "No datacoolie metadata files found"})
    return SourceDiscoveryResult(sources, [], errors)


def discover_datacoolie_project_sources(
    project_uri: str,
    *,
    metadata_subpath: str = "metadata",
    code_subpath: str = "functions",
    metadata_uri: str | None = None,
    code_uri: str | None = None,
    include_metadata: bool = True,
    include_code: bool = True,
    adapter: StorageAdapter | None = None,
) -> SourceDiscoveryResult:
    metadata_sources: list[DiscoveredSource] = []
    code_artifacts: list[DiscoveredSource] = []
    errors: list[dict[str, str]] = []

    if include_metadata:
        resolved_metadata_uri = metadata_uri or join_uri(project_uri, metadata_subpath or "metadata")
        metadata_result = discover_metadata_sources(
            resolved_metadata_uri,
            adapter=adapter,
            # A datacoolie project's metadata folder is already a typed
            # boundary. Remote parsing is deferred to the source checker so
            # project discovery does not download every object serially.
            inspect_contents=adapter is None,
        )
        metadata_sources.extend(metadata_result.metadata_sources)
        errors.extend(metadata_result.errors)

    if include_code:
        resolved_code_uri = code_uri or join_uri(project_uri, code_subpath or "functions")
        code = _discover_code_artifact(
            resolved_code_uri, project_uri=project_uri, adapter=adapter
        )
        if code is None:
            errors.append({"uri": resolved_code_uri, "message": "No readable source code artifact found"})
        else:
            code_artifacts.append(code)

    return SourceDiscoveryResult(metadata_sources, code_artifacts, errors)


def _metadata_file_source(path: Path, *, base_dir: Path, label: str | None = None) -> DiscoveredSource | None:
    if path.suffix.lower() not in METADATA_SUFFIXES:
        return None
    try:
        raw = read_metadata_file(str(path))
    except MetadataReadError:
        return None
    counts = _metadata_counts(raw)
    if not any(counts.values()):
        return None
    return DiscoveredSource(
        source_kind="metadata",
        uri=str(path),
        label=label or _relative_label(path, base_dir),
        source_config={
            "discovery_mode": "metadata_path",
            "metadata_root_uri": str(base_dir),
        },
        record_counts=counts,
    )


def _discover_code_artifact(
    uri: str,
    *,
    project_uri: str,
    adapter: StorageAdapter | None = None,
) -> DiscoveredSource | None:
    if adapter is not None:
        return _discover_remote_code_artifact(
            uri, project_uri=project_uri, adapter=adapter
        )
    try:
        path = require_local_path(uri)
    except StorageProviderNotEnabled:
        return None
    if not path.exists():
        return None
    if path.is_file():
        artifact_types = {".py": "python_file", ".zip": "zip", ".whl": "wheel"}
        artifact_type = artifact_types.get(path.suffix.lower())
        if artifact_type is None:
            return None
        if artifact_type == "python_file":
            python_file_count = 1
        else:
            try:
                indexed = build_artifact_index(str(path), artifact_type)
            except ArtifactIndexError:
                return None
            python_file_count = int(indexed["manifest"]["python_files"])
    elif path.is_dir():
        python_file_count = sum(1 for item in path.rglob("*.py") if item.is_file())
        artifact_type = "directory"
    else:
        return None
    if not python_file_count:
        return None
    label = uri_basename(uri) or "source code"
    source_config: dict[str, Any] = {
        "artifact_type": artifact_type,
        "module_roots": [],
        "discovery_mode": "datacoolie_project",
        "project_uri": project_uri,
    }
    return DiscoveredSource(
        source_kind="code",
        uri=str(path),
        label=label,
        source_config=source_config,
        record_counts={"python_files": python_file_count},
    )


def _discover_remote_metadata_sources(
    uri: str,
    *,
    label: str | None,
    adapter: StorageAdapter,
    inspect_contents: bool,
) -> SourceDiscoveryResult:
    canonical_root = adapter.canonical_uri(uri)
    suffix = Path(uri.rstrip("/")).suffix.lower()
    if suffix in METADATA_SUFFIXES:
        candidates = [
            StorageObject(
                canonical_uri=canonical_root,
                name=uri_basename(canonical_root),
                object_type="file",
            )
        ]
        metadata_root = canonical_root.rsplit("/", 1)[0]
    else:
        try:
            observed = inventory(
                adapter,
                StorageInventoryRequest(
                    uri=canonical_root,
                    purpose="observe",
                    recursive=True,
                    object_types=frozenset({"file"}),
                    suffixes=METADATA_SUFFIXES,
                    exclude_directories=METADATA_SCAN_EXCLUDED_DIRECTORIES,
                ),
            )
            candidates = list(observed.files)
        except Exception as exc:
            return SourceDiscoveryResult(
                [],
                [],
                [{"uri": canonical_root, "message": str(exc)}],
            )
        metadata_root = canonical_root

    sources: list[DiscoveredSource] = []
    errors: list[dict[str, str]] = []
    for candidate in candidates:
        if Path(candidate.name).suffix.lower() not in METADATA_SUFFIXES:
            continue
        if "/environments/" in candidate.canonical_uri.lower():
            continue
        counts: dict[str, int] = {}
        if inspect_contents:
            try:
                with adapter.open_read(candidate.canonical_uri) as handle:
                    raw = read_metadata_bytes(
                        candidate.canonical_uri, handle.read()
                    )
            except Exception:
                continue
            counts = _metadata_counts(raw)
            if not any(counts.values()):
                continue
        sources.append(
            DiscoveredSource(
                source_kind="metadata",
                uri=candidate.canonical_uri,
                label=label
                or _relative_uri_label(
                    candidate.canonical_uri, metadata_root
                ),
                source_config={
                    "discovery_mode": "metadata_path",
                    "metadata_root_uri": metadata_root,
                },
                record_counts=counts,
            )
        )
    if not sources:
        errors.append(
            {
                "uri": canonical_root,
                "message": "No datacoolie metadata files found",
            }
        )
    return SourceDiscoveryResult(sources, [], errors)


def _discover_remote_code_artifact(
    uri: str,
    *,
    project_uri: str,
    adapter: StorageAdapter,
) -> DiscoveredSource | None:
    canonical = adapter.canonical_uri(uri)
    suffix = Path(canonical).suffix.lower()
    artifact_types = {".py": "python_file", ".zip": "zip", ".whl": "wheel"}
    artifact_type = artifact_types.get(suffix)
    if artifact_type is None:
        try:
            observed = inventory(
                adapter,
                StorageInventoryRequest(
                    uri=canonical,
                    purpose="observe",
                    recursive=True,
                    object_types=frozenset({"file"}),
                    suffixes=frozenset({".py"}),
                    exclude_directories=CODE_SCAN_EXCLUDED_DIRECTORIES,
                ),
            )
            python_files = list(observed.files)
        except Exception:
            return None
        if not python_files:
            return None
        artifact_type = "directory"
        count = len(python_files)
    else:
        try:
            adapter.stat(canonical)
        except Exception:
            return None
        count = 1 if artifact_type == "python_file" else 0
    return DiscoveredSource(
        source_kind="code",
        uri=canonical,
        label=uri_basename(canonical) or "source code",
        source_config={
            "artifact_type": artifact_type,
            "module_roots": [],
            "discovery_mode": "datacoolie_project",
            "project_uri": adapter.canonical_uri(project_uri),
        },
        record_counts={"python_files": count},
    )


def _relative_uri_label(uri: str, root_uri: str) -> str:
    prefix = root_uri.rstrip("/") + "/"
    return uri.removeprefix(prefix) if uri.startswith(prefix) else uri_basename(uri)


def _metadata_counts(raw: dict[str, Any]) -> dict[str, int]:
    return {
        "connections": _list_count(raw.get("connections")),
        "dataflows": _list_count(raw.get("dataflows")),
        "schema_hints": _list_count(raw.get("schema_hints")),
    }


def _list_count(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def _relative_label(path: Path, base_dir: Path) -> str:
    try:
        return path.relative_to(base_dir).as_posix()
    except ValueError:
        return path.name


def _is_environments_file(root: Path, file_path: Path) -> bool:
    try:
        relative_parts = file_path.relative_to(root).parts
    except ValueError:
        relative_parts = file_path.parts
    return any(part.lower() == "environments" for part in relative_parts)


def _metadata_sort_key(root: Path, path: Path) -> tuple[int, str]:
    name = path.name.lower()
    if name.startswith("connections"):
        priority = 0
    elif name.startswith("schema_hints"):
        priority = 1
    elif name.startswith("dataflows"):
        priority = 2
    elif name.startswith("metadata"):
        priority = 3
    else:
        priority = 4
    return priority, _relative_label(path, root).lower()
