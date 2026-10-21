from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from datacoolie_studio.domains.code_artifacts.discovery import discover_code_artifacts
from datacoolie_studio.domains.metadata.reader import (
    MetadataReadError,
    read_metadata_bytes,
    read_metadata_file,
)
from datacoolie_studio.domains.sources.scan_policy import (
    METADATA_SCAN_EXCLUDED_DIRECTORIES,
)
from datacoolie_studio.domains.storage.adapters import StorageAdapter, StorageObject
from datacoolie_studio.domains.storage.capabilities import (
    metadata_write_back_supported,
)
from datacoolie_studio.domains.storage.errors import StorageNotFoundError
from datacoolie_studio.domains.storage.inventory import (
    StorageInventoryRequest,
    inventory,
)
from datacoolie_studio.domains.storage.uri import (
    StorageProviderNotEnabled,
    parse_storage_uri,
    require_local_path,
    uri_basename,
)
from datacoolie_studio.domains.sources.project_config import (
    ProjectConfigError,
    resolve_project_component_paths,
)


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
    sql_sources: list[DiscoveredSource] = field(default_factory=list)


def discover_metadata_sources(
    uri: str,
    *,
    label: str | None = None,
    adapter: StorageAdapter | None = None,
    inspect_contents: bool = True,
    strict: bool = False,
) -> SourceDiscoveryResult:
    if adapter is not None:
        return _discover_remote_metadata_sources(
            uri,
            label=label,
            adapter=adapter,
            inspect_contents=inspect_contents or strict,
            strict=strict,
        )
    try:
        path = require_local_path(uri)
    except StorageProviderNotEnabled as exc:
        return SourceDiscoveryResult([], [], [{"uri": exc.uri, "message": str(exc), "provider": exc.provider}])

    if not path.exists():
        return SourceDiscoveryResult([], [], [{"uri": uri, "message": f"Metadata path not found: {uri}"}])
    if path.is_symlink():
        return SourceDiscoveryResult(
            [],
            [],
            [_metadata_discovery_error(
                path,
                "metadata_symlink",
                "Metadata scan does not follow symlinked files or directories",
            )],
        )

    if path.is_file():
        errors: list[dict[str, str]] = []
        source = _metadata_file_source(
            path,
            base_dir=path.parent,
            label=label,
            strict=strict,
            errors=errors,
        )
        if source is None:
            if not errors:
                errors.append({"uri": str(path), "message": "File does not contain datacoolie metadata sections"})
            return SourceDiscoveryResult([], [], errors)
        return SourceDiscoveryResult([source], [], errors)

    if not path.is_dir():
        return SourceDiscoveryResult([], [], [{"uri": uri, "message": f"Unsupported metadata path type: {uri}"}])

    sources: list[DiscoveredSource] = []
    errors: list[dict[str, str]] = []
    for candidate in sorted(path.rglob("*"), key=lambda item: _metadata_sort_key(path, item)):
        if candidate.is_symlink():
            if candidate.suffix.lower() in METADATA_SUFFIXES:
                errors.append(_metadata_discovery_error(
                    candidate,
                    "metadata_symlink",
                    "Metadata scan does not follow symlinked files or directories",
                    severity="error" if strict else "warning",
                ))
            continue
        if not candidate.is_file() or candidate.suffix.lower() not in METADATA_SUFFIXES:
            continue
        if _is_environments_file(path, candidate):
            continue
        source = _metadata_file_source(
            candidate,
            base_dir=path,
            strict=strict,
            errors=errors,
        )
        if source is None:
            continue
        sources.append(source)

    if not sources and not errors:
        errors.append({"uri": uri, "message": "No datacoolie metadata files found"})
    return SourceDiscoveryResult(sources, [], errors)


def discover_datacoolie_project_sources(
    project_uri: str,
    *,
    metadata_subpath: str | None = None,
    sql_subpaths: Sequence[str] | None = None,
    code_subpaths: Sequence[str] | None = None,
    metadata_uri: str | None = None,
    code_uri: str | None = None,
    include_metadata: bool = True,
    include_code: bool = True,
    code_discovery_mode: str = "auto",
    adapter: StorageAdapter | None = None,
) -> SourceDiscoveryResult:
    metadata_sources: list[DiscoveredSource] = []
    code_artifacts: list[DiscoveredSource] = []
    sql_sources: list[DiscoveredSource] = []
    errors: list[dict[str, str]] = []

    try:
        component_paths = resolve_project_component_paths(
            project_uri,
            adapter=adapter,
            metadata_override=metadata_subpath,
            sql_override=sql_subpaths,
            functions_override=code_subpaths,
        )
    except ProjectConfigError as exc:
        return SourceDiscoveryResult([], [], [{"uri": project_uri, "message": str(exc), "code": "project_config_invalid"}])

    errors.extend(
        {
            "uri": str(item.get("uri") or project_uri),
            "message": str(item.get("message") or "Component path directive diagnostic"),
            "code": str(item.get("code") or "component_path_diagnostic"),
            "severity": "warning",
        }
        for item in component_paths.diagnostics
    )

    selected_marker = bool(
        component_paths.directive_uri
        and component_paths.directive_kind in {"datacoolie.yml", "manifest.json"}
    )
    directive_declares = {
        name: selected_marker
        and component_paths.provenance.get(name) == component_paths.directive_kind
        for name in ("metadata", "sql", "functions")
    }
    directive_config = component_paths.as_config()
    resolved_metadata_uri = (
        component_paths.metadata_uri
        if directive_declares["metadata"]
        else metadata_uri or component_paths.metadata_uri
    )
    effective_metadata_uris = (resolved_metadata_uri,)
    code_roots = (
        list(component_paths.functions_uris)
        if directive_declares["functions"] or not code_uri
        else [code_uri]
    )
    effective_code_provenance = dict(component_paths.provenance)
    if code_uri and not directive_declares["functions"]:
        effective_code_provenance["functions"] = "explicit_uri"
    effective_metadata_provenance = dict(component_paths.provenance)
    if metadata_uri and not directive_declares["metadata"]:
        effective_metadata_provenance["metadata"] = "explicit_uri"

    if include_metadata and _component_path_available(resolved_metadata_uri, adapter=adapter):
        # A marker is authoritative for the component it declares.  Direct
        # URI inputs remain useful for marker-free legacy callers, and become
        # the fallback only when the selected marker leaves metadata
        # undeclared.
        metadata_result = discover_metadata_sources(
            resolved_metadata_uri,
            adapter=adapter,
            # A datacoolie project's metadata folder is already a typed
            # boundary. Remote parsing is deferred to the source checker so
            # project discovery does not download every object serially.
            inspect_contents=adapter is None or selected_marker,
            strict=selected_marker,
        )
        metadata_sources.extend(
            replace(
                candidate,
                source_config={
                    **candidate.source_config,
                    **directive_config,
                    "metadata_root_uri": resolved_metadata_uri,
                    "metadata_root_uris": list(effective_metadata_uris),
                    "effective_component_path_provenance": {
                        **effective_metadata_provenance,
                        "functions": component_paths.provenance.get("functions"),
                        "sql": component_paths.provenance.get("sql"),
                    },
                    "component_resolved_from": {
                        **effective_metadata_provenance,
                        "functions": component_paths.provenance.get("functions"),
                        "sql": component_paths.provenance.get("sql"),
                    },
                    "discovery_mode": "datacoolie_project",
                    "discovery_managed": True,
                    "project_uri": project_uri,
                },
            )
            for candidate in metadata_result.metadata_sources
        )
        errors.extend(
            {
                **error,
                "component": error.get("component", "metadata"),
            }
            for error in metadata_result.errors
        )

    if include_code:
        for code_root in code_roots:
            code_root_available = _component_path_available(code_root, adapter=adapter)
            code_result = discover_code_artifacts(
                project_uri,
                code_root,
                adapter=adapter,
                discovery_mode=code_discovery_mode,
            )
            errors.extend(
                {
                    **error,
                    "component": error.get("component", "functions"),
                }
                for error in code_result.errors
                if code_root_available or not _optional_code_absence_error(error)
            )
            for candidate in code_result.candidates:
                source_config: dict[str, Any] = {
                    "artifact_type": candidate.artifact_type,
                    "module_roots": list(candidate.module_roots),
                    "discovery_mode": "datacoolie_project",
                    "code_discovery_mode": code_discovery_mode,
                    "discovery_managed": True,
                    "discovery_origin": candidate.discovery_origin,
                    "discovery_record_counts": dict(candidate.record_counts),
                    "project_uri": project_uri,
                    "config_uri": component_paths.config_uri,
                    "config_found": component_paths.config_found,
                    "manifest_uri": component_paths.manifest_uri,
                    "manifest_found": component_paths.manifest_found,
                    "directive_kind": component_paths.directive_kind,
                    "directive_uri": component_paths.directive_uri,
                    "config_revision": component_paths.config_revision,
                    "metadata_root_uri": resolved_metadata_uri,
                    "metadata_root_uris": list(effective_metadata_uris),
                    "sql_root_uri": component_paths.sql_uri,
                    "sql_root_uris": list(component_paths.sql_uris),
                    "functions_root_uri": code_roots[0],
                    "functions_root_uris": list(code_roots),
                    "component_paths": directive_config["component_paths"],
                    "component_paths_all": directive_config["component_paths_all"],
                    "component_descriptors": {
                        name: [dict(item) for item in entries]
                        for name, entries in component_paths.component_descriptors.items()
                    },
                    "component_path_provenance": dict(component_paths.provenance),
                    "effective_component_path_provenance": {
                        **effective_metadata_provenance,
                        **effective_code_provenance,
                    },
                    "component_resolved_from": {
                        **effective_metadata_provenance,
                        **effective_code_provenance,
                    },
                    "component_path_diagnostics": [
                        dict(item) for item in component_paths.diagnostics
                    ],
                }
                if candidate.module_prefix:
                    source_config["module_prefix"] = candidate.module_prefix
                code_artifacts.append(
                    DiscoveredSource(
                        source_kind="code",
                        uri=candidate.uri,
                        label=candidate.label,
                        source_config=source_config,
                        record_counts=candidate.record_counts,
                    )
                )

    for sql_root in component_paths.sql_uris:
        if not _component_path_available(sql_root, adapter=adapter):
            continue
        sql_sources.append(
            DiscoveredSource(
                source_kind="sql",
                uri=sql_root,
                label=_relative_uri_label(sql_root, project_uri),
                source_config={
                    **directive_config,
                    "sql_root_uri": sql_root,
                    "sql_root_uris": list(component_paths.sql_uris),
                    "discovery_mode": "datacoolie_project",
                    "discovery_managed": True,
                    "project_uri": project_uri,
                },
            )
        )

    return SourceDiscoveryResult(metadata_sources, code_artifacts, errors, sql_sources)


def _optional_code_absence_error(error: dict[str, str]) -> bool:
    """Hide the low-level empty-root diagnostic for optional auto scans."""

    message = str(error.get("message") or "").lower()
    return (
        message == "no readable source code artifact found"
        or message.startswith("source code path not found:")
        or message.startswith("source code artifact not found:")
    )


def _component_path_available(
    uri: str,
    *,
    adapter: StorageAdapter | None,
) -> bool:
    """Return whether an optional project component can be scanned.

    Project components are optional.  Local paths can be checked without I/O
    beyond the filesystem stat.  Remote object stores often do not materialize
    directory prefixes, so a missing prefix is probed with a bounded inventory
    after a failed stat.  Unknown provider failures are treated as available so
    the real discovery operation can return the actionable error.
    """

    if adapter is None:
        try:
            return require_local_path(uri).exists()
        except StorageProviderNotEnabled:
            # Preserve the low-level discovery error for remote callers that
            # did not provide an adapter.
            return True

    stat = getattr(adapter, "stat", None)
    if callable(stat):
        try:
            stat(uri)
            return True
        except (StorageNotFoundError, FileNotFoundError):
            pass
        except Exception:
            return True

    try:
        observed = inventory(
            adapter,
            StorageInventoryRequest(
                uri=uri,
                purpose="probe",
                recursive=True,
                object_types=frozenset({"file", "directory"}),
                object_limit=1,
                stop_after_match=True,
            ),
        )
    except (StorageNotFoundError, FileNotFoundError):
        return False
    except Exception:
        return True
    return bool(observed.objects)


def _metadata_file_source(
    path: Path,
    *,
    base_dir: Path,
    label: str | None = None,
    strict: bool = False,
    errors: list[dict[str, str]] | None = None,
) -> DiscoveredSource | None:
    if path.suffix.lower() not in METADATA_SUFFIXES:
        return None
    if path.is_symlink():
        if errors is not None:
            errors.append(_metadata_discovery_error(
                path,
                "metadata_symlink",
                "Metadata scan does not follow symlinked files or directories",
                severity="error" if strict else "warning",
            ))
        return None
    try:
        raw = read_metadata_file(str(path), strict=strict)
    except MetadataReadError as exc:
        if errors is not None:
            code = "unsupported_metadata_extension" if path.suffix.lower() == ".xls" else "metadata_document_invalid"
            errors.append(_metadata_discovery_error(
                path,
                code if strict else "metadata_candidate_unreadable",
                str(exc),
                severity="error" if strict else "warning",
            ))
        return None
    counts = _metadata_counts(raw)
    if not any(counts.values()) and not strict:
        return None
    return DiscoveredSource(
        source_kind="metadata",
        uri=str(path),
        label=label or _relative_label(path, base_dir),
        source_config={
            "discovery_mode": "metadata_path",
            "metadata_root_uri": str(base_dir),
            "metadata_validation_mode": "read_only_checks",
            "datacoolie_compatible": path.suffix.lower() != ".xls",
            "metadata_write_back_supported": (
                metadata_write_back_supported("local")
                and path.suffix.lower() != ".xls"
            ),
        },
        record_counts=counts,
    )


def _discover_remote_metadata_sources(
    uri: str,
    *,
    label: str | None,
    adapter: StorageAdapter,
    inspect_contents: bool,
    strict: bool,
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
                        candidate.canonical_uri,
                        handle.read(),
                        strict=strict,
                    )
            except Exception as exc:
                code = "unsupported_metadata_extension" if Path(candidate.name).suffix.lower() == ".xls" else "metadata_document_invalid"
                errors.append({
                    "uri": candidate.canonical_uri,
                    "code": code if strict else "metadata_candidate_unreadable",
                    "severity": "error" if strict else "warning",
                    "message": str(exc),
                })
                continue
            counts = _metadata_counts(raw)
            if not any(counts.values()) and not strict:
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
                    "metadata_validation_mode": "read_only_checks",
                    "datacoolie_compatible": Path(candidate.name).suffix.lower() != ".xls",
                    "metadata_write_back_supported": (
                        metadata_write_back_supported(parse_storage_uri(uri).provider)
                        and Path(candidate.name).suffix.lower() != ".xls"
                    ),
                },
                record_counts=counts,
            )
        )
    if not sources and not errors:
        errors.append(
            {
                "uri": canonical_root,
                "message": "No datacoolie metadata files found",
            }
        )
    return SourceDiscoveryResult(sources, [], errors)


def _metadata_discovery_error(
    path: Path,
    code: str,
    message: str,
    *,
    severity: str = "error",
) -> dict[str, str]:
    return {
        "uri": str(path),
        "code": code,
        "severity": severity,
        "message": message,
    }


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
