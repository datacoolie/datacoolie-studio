from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from datacoolie_studio.domains.metadata.reader import MetadataReadError, read_metadata_file
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


def discover_metadata_sources(uri: str, *, label: str | None = None) -> SourceDiscoveryResult:
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
) -> SourceDiscoveryResult:
    metadata_sources: list[DiscoveredSource] = []
    code_artifacts: list[DiscoveredSource] = []
    errors: list[dict[str, str]] = []

    if include_metadata:
        resolved_metadata_uri = metadata_uri or join_uri(project_uri, metadata_subpath or "metadata")
        metadata_result = discover_metadata_sources(resolved_metadata_uri)
        metadata_sources.extend(metadata_result.metadata_sources)
        errors.extend(metadata_result.errors)

    if include_code:
        resolved_code_uri = code_uri or join_uri(project_uri, code_subpath or "functions")
        code = _discover_code_artifact(resolved_code_uri, project_uri=project_uri)
        if code is None:
            errors.append({"uri": resolved_code_uri, "message": "No readable source code directory found"})
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


def _discover_code_artifact(uri: str, *, project_uri: str) -> DiscoveredSource | None:
    try:
        path = require_local_path(uri)
    except StorageProviderNotEnabled:
        return None
    if not path.exists() or not path.is_dir():
        return None
    python_files = [item for item in path.rglob("*.py") if item.is_file()]
    if not python_files:
        return None
    label = uri_basename(uri) or "source code"
    source_config: dict[str, Any] = {
        "artifact_type": "directory",
        "module_roots": [],
        "discovery_mode": "datacoolie_project",
        "project_uri": project_uri,
    }
    if label == "functions":
        source_config["module_prefix"] = "functions"
    return DiscoveredSource(
        source_kind="code",
        uri=str(path),
        label=label,
        source_config=source_config,
        record_counts={"python_files": len(python_files)},
    )


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
