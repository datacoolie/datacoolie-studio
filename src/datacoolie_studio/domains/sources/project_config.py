"""Resolve DataCoolie component roots from one supplied source root.

The caller supplies either a working-project root or an already selected
environment artifact root.  ``datacoolie.yml`` and ``manifest.json`` are
optional hints for component locations; when a component is not declared,
the caller's fallback input (or Studio's default) is used.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

import yaml

from datacoolie_studio.domains.storage.adapters import StorageAdapter
from datacoolie_studio.domains.storage.errors import StorageNotFoundError
from datacoolie_studio.domains.storage.uri import (
    StorageProviderNotEnabled,
    join_uri,
    require_local_path,
)


CONFIG_FILENAME = "datacoolie.yml"
MANIFEST_FILENAME = "manifest.json"
CONFIG_SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1
BUILD_ARTIFACT_TYPE = "datacoolie_build"
ENVIRONMENT_ARTIFACT_TYPE = "datacoolie_environment"
DEFAULT_COMPONENT_PATHS = {
    "metadata": "metadata",
    "sql": "sql",
    "functions": "functions",
}
_COMPONENT_NAMES = frozenset(DEFAULT_COMPONENT_PATHS)
_RESERVED_COMPONENT_ROOTS = frozenset({".builds", "runners"})


class ProjectConfigError(ValueError):
    pass


@dataclass(frozen=True)
class _SourceDirective:
    kind: str
    uri: str
    found: bool
    revision: dict[str, Any] | None
    components: dict[str, tuple[dict[str, Any], ...]] = field(default_factory=dict)
    diagnostics: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ProjectComponentPaths:
    project_uri: str
    metadata_paths: tuple[str, ...]
    sql_paths: tuple[str, ...]
    functions_paths: tuple[str, ...]
    config_uri: str
    config_found: bool
    config_revision: dict[str, Any] | None
    provenance: dict[str, str]
    manifest_uri: str | None = None
    manifest_found: bool = False
    directive_kind: str = "default_input"
    directive_uri: str | None = None
    component_descriptors: dict[str, tuple[dict[str, Any], ...]] = field(
        default_factory=dict
    )
    diagnostics: tuple[dict[str, Any], ...] = ()

    # Scalar aliases preserve the existing Studio call sites while the
    # plural properties carry the DataCoolie multi-root contract.
    @property
    def metadata_path(self) -> str:
        return self.metadata_paths[0]

    @property
    def sql_path(self) -> str:
        return self.sql_paths[0]

    @property
    def functions_path(self) -> str:
        return self.functions_paths[0]

    @property
    def metadata_uri(self) -> str:
        return join_uri(self.project_uri, self.metadata_path)

    @property
    def sql_uri(self) -> str:
        return join_uri(self.project_uri, self.sql_path)

    @property
    def functions_uri(self) -> str:
        return join_uri(self.project_uri, self.functions_path)

    @property
    def metadata_uris(self) -> tuple[str, ...]:
        return tuple(join_uri(self.project_uri, path) for path in self.metadata_paths)

    @property
    def sql_uris(self) -> tuple[str, ...]:
        return tuple(join_uri(self.project_uri, path) for path in self.sql_paths)

    @property
    def functions_uris(self) -> tuple[str, ...]:
        return tuple(join_uri(self.project_uri, path) for path in self.functions_paths)

    def as_config(self) -> dict[str, Any]:
        component_paths = {
            "metadata": self.metadata_path,
            "sql": self.sql_path,
            "functions": self.functions_path,
        }
        component_paths_all = {
            "metadata": list(self.metadata_paths),
            "sql": list(self.sql_paths),
            "functions": list(self.functions_paths),
        }
        return {
            "project_uri": self.project_uri,
            "config_uri": self.config_uri,
            "config_found": self.config_found,
            "config_revision": self.config_revision,
            "manifest_uri": self.manifest_uri,
            "manifest_found": self.manifest_found,
            "directive_kind": self.directive_kind,
            "directive_uri": self.directive_uri,
            "directive_revision": self.config_revision,
            "component_paths": component_paths,
            "component_paths_all": component_paths_all,
            "component_descriptors": {
                name: [dict(item) for item in entries]
                for name, entries in self.component_descriptors.items()
            },
            "component_path_provenance": dict(self.provenance),
            "component_resolved_from": dict(self.provenance),
            "metadata_root_uri": self.metadata_uri,
            "sql_root_uri": self.sql_uri,
            "functions_root_uri": self.functions_uri,
            "metadata_root_uris": list(self.metadata_uris),
            "sql_root_uris": list(self.sql_uris),
            "functions_root_uris": list(self.functions_uris),
            "component_path_diagnostics": [dict(item) for item in self.diagnostics],
        }


def resolve_project_component_paths(
    project_uri: str,
    *,
    adapter: StorageAdapter | None = None,
    metadata_override: str | Sequence[str] | None = None,
    sql_override: str | Sequence[str] | None = None,
    functions_override: str | Sequence[str] | None = None,
) -> ProjectComponentPaths:
    """Resolve effective component roots below ``project_uri``.

    Marker precedence is ``datacoolie.yml`` then ``manifest.json``.  A marker
    wins for the components it declares; caller-supplied overrides are
    fallback inputs for undeclared components.  Marker read/parse errors are
    returned as diagnostics so the existing scan can continue with safe
    fallback paths instead of failing silently.
    """

    config_uri = join_uri(project_uri, CONFIG_FILENAME)
    manifest_uri = join_uri(project_uri, MANIFEST_FILENAME)
    directive = _read_source_directive(
        adapter=adapter,
        config_uri=config_uri,
        manifest_uri=manifest_uri,
    )

    fallback_values = {
        "metadata": _fallback_values(metadata_override, "metadata"),
        "sql": _fallback_values(sql_override, "sql"),
        "functions": _fallback_values(functions_override, "functions"),
    }
    fallback_provenance = {
        name: "default_input" if _has_value(override) else "default"
        for name, override in (
            ("metadata", metadata_override),
            ("sql", sql_override),
            ("functions", functions_override),
        )
    }

    resolved: dict[str, tuple[str, ...]] = {}
    provenance: dict[str, str] = {}
    descriptors: dict[str, tuple[dict[str, Any], ...]] = {}
    for name in DEFAULT_COMPONENT_PATHS:
        declared = directive.components.get(name) if directive else None
        if declared:
            try:
                entries = tuple(
                    {
                        **entry,
                        "path": _normalise_component_path(
                            project_uri,
                            entry["path"],
                            label=f"{directive.kind}.components.{name}.path",
                        ),
                    }
                    for entry in declared
                )
            except ProjectConfigError as exc:
                # A bad declaration must never redirect a scan outside the
                # supplied root.  Keep the safe fallback for this component,
                # while preserving an actionable diagnostic for the caller.
                fallback_paths = _normalise_fallback_paths(
                    project_uri,
                    fallback_values[name],
                    name=name,
                )
                resolved[name] = fallback_paths
                descriptors[name] = tuple({"path": path} for path in fallback_paths)
                provenance[name] = fallback_provenance[name]
                diagnostics = list(directive.diagnostics)
                diagnostics.append(
                    {
                        "uri": directive.uri,
                        "code": "component_path_invalid",
                        "message": str(exc),
                        "component": name,
                        "fallback": fallback_provenance[name],
                    }
                )
                directive = _with_diagnostics(directive, diagnostics)
                continue
            resolved[name] = tuple(str(entry["path"]) for entry in entries)
            descriptors[name] = entries
            provenance[name] = directive.kind
            continue

        fallback_paths = _normalise_fallback_paths(
            project_uri,
            fallback_values[name],
            name=name,
        )
        resolved[name] = fallback_paths
        descriptors[name] = tuple({"path": path} for path in fallback_paths)
        provenance[name] = fallback_provenance[name]

    try:
        _validate_component_overlap(resolved)
    except ProjectConfigError as exc:
        if not _directive_selected(directive):
            raise
        resolved, provenance, descriptors = _fallback_all_components(
            project_uri,
            fallback_values,
            fallback_provenance,
        )
        directive = _with_diagnostic(
            directive,
            code="component_paths_overlap",
            message=str(exc),
            component=None,
            fallback="all",
        )
    try:
        _validate_sql_prefixes(resolved["sql"])
    except ProjectConfigError as exc:
        if not _directive_selected(directive):
            raise
        fallback_sql = _normalise_fallback_paths(
            project_uri,
            fallback_values["sql"],
            name="sql",
        )
        _validate_sql_prefixes(fallback_sql)
        resolved["sql"] = fallback_sql
        descriptors["sql"] = tuple({"path": path} for path in fallback_sql)
        provenance["sql"] = fallback_provenance["sql"]
        directive = _with_diagnostic(
            directive,
            code="sql_component_paths_ambiguous",
            message=str(exc),
            component="sql",
            fallback=fallback_provenance["sql"],
        )

    return ProjectComponentPaths(
        project_uri=project_uri,
        metadata_paths=resolved["metadata"],
        sql_paths=resolved["sql"],
        functions_paths=resolved["functions"],
        config_uri=config_uri,
        config_found=bool(directive and directive.kind == CONFIG_FILENAME),
        config_revision=directive.revision if directive else None,
        provenance=provenance,
        manifest_uri=(
            manifest_uri
            if directive and directive.kind == MANIFEST_FILENAME
            else None
        ),
        manifest_found=bool(directive and directive.kind == MANIFEST_FILENAME),
        directive_kind=directive.kind if directive else "default_input",
        directive_uri=directive.uri if directive else None,
        component_descriptors=descriptors,
        diagnostics=directive.diagnostics if directive else (),
    )


def _read_source_directive(
    *,
    adapter: StorageAdapter | None,
    config_uri: str,
    manifest_uri: str,
) -> _SourceDirective | None:
    for kind, uri, parser in (
        (CONFIG_FILENAME, config_uri, _parse_yaml_directive),
        (MANIFEST_FILENAME, manifest_uri, _parse_manifest_directive),
    ):
        try:
            raw, revision = _read_directive_bytes(uri, adapter=adapter)
        except Exception as exc:
            if _is_missing_directive_error(exc):
                continue
            return _invalid_directive(kind, uri, f"Cannot read {kind}: {exc}")
        try:
            value = parser(raw)
            components = _parse_components(value, kind=kind)
        except (
            ProjectConfigError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            yaml.YAMLError,
            TypeError,
        ) as exc:
            return _invalid_directive(kind, uri, str(exc), revision=revision)
        return _SourceDirective(
            kind=kind,
            uri=uri,
            found=True,
            revision=revision,
            components=components,
        )
    return None


def _invalid_directive(
    kind: str,
    uri: str,
    message: str,
    *,
    revision: dict[str, Any] | None = None,
) -> _SourceDirective:
    code = "project_config_invalid" if kind == CONFIG_FILENAME else "manifest_invalid"
    return _SourceDirective(
        kind=kind,
        uri=uri,
        found=True,
        revision=revision,
        diagnostics=(
            {
                "uri": uri,
                "code": code,
                "message": message,
                "fallback": "default_input",
            },
        ),
    )


def _read_directive_bytes(
    uri: str,
    *,
    adapter: StorageAdapter | None,
) -> tuple[bytes, dict[str, Any]]:
    if adapter is None:
        path = require_local_path(uri)
        if not path.is_file():
            raise FileNotFoundError(uri)
        _assert_local_marker_containment(path)
        raw = path.read_bytes()
        return raw, _local_revision(path, raw)
    with adapter.open_read(uri) as handle:
        raw = handle.read()
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return bytes(raw), _adapter_revision(adapter, uri, bytes(raw))


def _parse_yaml_directive(raw: bytes) -> Mapping[str, Any]:
    parsed = yaml.safe_load(raw.decode("utf-8"))
    if not isinstance(parsed, Mapping):
        raise ProjectConfigError(f"{CONFIG_FILENAME} must contain a mapping")
    schema_version = parsed.get("schema_version", CONFIG_SCHEMA_VERSION)
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != CONFIG_SCHEMA_VERSION
    ):
        raise ProjectConfigError(
            f"Unsupported {CONFIG_FILENAME} schema_version {schema_version!r}; "
            f"expected {CONFIG_SCHEMA_VERSION}"
        )
    return parsed


def _parse_manifest_directive(raw: bytes) -> Mapping[str, Any]:
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, Mapping):
        raise ProjectConfigError(f"{MANIFEST_FILENAME} must contain an object")
    schema_version = parsed.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != MANIFEST_SCHEMA_VERSION
    ):
        raise ProjectConfigError(
            f"Unsupported {MANIFEST_FILENAME} schema_version "
            f"{parsed.get('schema_version')!r}; expected {MANIFEST_SCHEMA_VERSION}"
        )
    artifact_type = parsed.get("artifact_type")
    if not isinstance(artifact_type, str) or not artifact_type.strip():
        raise ProjectConfigError(
            f"{MANIFEST_FILENAME} artifact_type must be a non-empty string"
        )
    if parsed.get("artifact_type") == BUILD_ARTIFACT_TYPE:
        raise ProjectConfigError(
            "Build manifest was supplied as the scan root; provide the "
            "environment artifact root <artifact>/<environment>/"
        )
    if artifact_type != ENVIRONMENT_ARTIFACT_TYPE:
        raise ProjectConfigError(
            f"Unsupported {MANIFEST_FILENAME} artifact_type {artifact_type!r}; "
            f"expected {ENVIRONMENT_ARTIFACT_TYPE!r}"
        )
    return parsed


def _parse_components(
    value: Mapping[str, Any],
    *,
    kind: str,
) -> dict[str, tuple[dict[str, Any], ...]]:
    components = value.get("components", {})
    if components is None:
        return {}
    if not isinstance(components, Mapping):
        raise ProjectConfigError(f"{kind} components must be a mapping")
    unknown = sorted(str(key) for key in set(components) - _COMPONENT_NAMES - {"runners"})
    if unknown:
        raise ProjectConfigError(
            f"Unsupported {kind} components field(s): {', '.join(unknown)}"
        )

    result: dict[str, tuple[dict[str, Any], ...]] = {}
    for name in DEFAULT_COMPONENT_PATHS:
        if name not in components or components[name] is None:
            continue
        entries = _component_entries(components[name], name=name, kind=kind)
        if name == "metadata" and not entries:
            raise ProjectConfigError(
                f"{kind} components.metadata must define exactly one path"
            )
        if entries:
            if name == "metadata" and len(entries) != 1:
                raise ProjectConfigError(
                    f"{kind} components.metadata must define exactly one path"
                )
            result[name] = entries
    return result


def _component_entries(
    value: Any,
    *,
    name: str,
    kind: str,
) -> tuple[dict[str, Any], ...]:
    values = value if isinstance(value, list) else [value]
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(values):
        if isinstance(item, str):
            data: dict[str, Any] = {"path": item}
        elif isinstance(item, Mapping):
            data = dict(item)
        else:
            raise ProjectConfigError(
                f"{kind} components.{name}[{index}] must be a mapping or string"
            )
        path = data.get("path")
        if path is None or (isinstance(path, str) and not path.strip()):
            # A missing component path is an undeclared component and will use
            # the caller's fallback.  An explicit non-string remains invalid.
            if path is None:
                continue
            raise ProjectConfigError(
                f"{kind} components.{name}[{index}].path must be a non-empty path"
            )
        descriptor: dict[str, Any] = {
            "path": path,
        }
        if name == "functions" and "packaging" in data:
            descriptor["packaging"] = data["packaging"]
        if "files" in data and isinstance(data["files"], list):
            descriptor["files"] = list(data["files"])
        for key in ("root_name", "wrapped", "output"):
            if key in data:
                descriptor[key] = data[key]
        entries.append(descriptor)
    return tuple(entries)


def _validate_component_overlap(paths: Mapping[str, tuple[str, ...]]) -> None:
    roots: list[tuple[str, str, tuple[str, ...]]] = []
    for name, values in paths.items():
        for value in values:
            parts = tuple(value.casefold().split("/"))
            if parts and parts[0] in _RESERVED_COMPONENT_ROOTS:
                reason = (
                    "reserved for build output"
                    if parts[0] == ".builds"
                    else "reserved for environment runners"
                )
                raise ProjectConfigError(
                    f"components.{name}.path {value!r} is {reason}"
                )
            roots.append((name, value, parts))
    for index, (left_name, left_path, left_parts) in enumerate(roots):
        for right_name, right_path, right_parts in roots[index + 1 :]:
            if left_parts[: len(right_parts)] == right_parts or right_parts[: len(left_parts)] == left_parts:
                raise ProjectConfigError(
                    f"components.{left_name}.path {left_path!r} and "
                    f"components.{right_name}.path {right_path!r} overlap"
                )


def _validate_sql_prefixes(paths: tuple[str, ...]) -> None:
    prefixes: dict[str, str] = {}
    for path in paths:
        prefix = path.rsplit("/", 1)[-1].casefold()
        if prefix in prefixes:
            raise ProjectConfigError(
                "SQL paths must have unique folder names for query resolution: "
                f"{prefixes[prefix]!r} and {path!r}"
            )
        prefixes[prefix] = path


def _normalise_relative_path(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectConfigError(f"{label} must be a non-empty relative path")
    raw = value.strip().replace("\\", "/")
    if any(ord(character) < 32 or ord(character) == 127 for character in raw):
        raise ProjectConfigError(f"{label} contains a control character")
    lowered = raw.lower()
    if (
        "?" in raw
        or "#" in raw
        or "%2f" in lowered
        or "%5c" in lowered
        or "%2e" in lowered
        or raw.startswith("~")
    ):
        raise ProjectConfigError(
            f"{label} contains an unsupported query, fragment, or encoded separator"
        )
    if PurePosixPath(raw).is_absolute() or PureWindowsPath(raw).is_absolute():
        raise ProjectConfigError(f"{label} must be project-relative: {value!r}")
    if PureWindowsPath(raw).drive or ":" in raw.split("/", 1)[0]:
        raise ProjectConfigError(f"{label} must not contain a drive or URI scheme")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise ProjectConfigError(f"{label} must not escape the project: {value!r}")
    return "/".join(parts)


def _normalise_component_path(
    project_uri: str,
    value: object,
    *,
    label: str,
) -> str:
    normalized = _normalise_relative_path(value, label=label)
    _assert_local_component_containment(project_uri, normalized, label=label)
    return normalized


def _assert_local_marker_containment(path: Path) -> None:
    """Do not follow a marker symlink outside its supplied project root."""

    try:
        parent = path.parent.resolve(strict=False)
        resolved = path.resolve(strict=False)
        resolved.relative_to(parent)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProjectConfigError(
            f"Project marker must remain below its supplied root: {path}"
        ) from exc


def _assert_local_component_containment(
    project_uri: str,
    relative_path: str,
    *,
    label: str,
) -> None:
    """Ensure a local component root cannot escape via a symlink."""

    try:
        project_path = require_local_path(project_uri)
    except StorageProviderNotEnabled:
        # Cloud paths are checked lexically above and joined through the
        # provider adapter. Only local paths have symlink targets to resolve.
        return
    try:
        root = project_path.resolve(strict=False)
        candidate = root.joinpath(*relative_path.split("/")).resolve(strict=False)
        candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProjectConfigError(
            f"{label} must remain below the supplied project root: {relative_path!r}"
        ) from exc


def _has_value(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return any(isinstance(item, str) and bool(item.strip()) for item in value)
    return False


def _fallback_values(
    value: str | Sequence[str] | None,
    name: str,
) -> tuple[str, ...]:
    if value is None:
        return (DEFAULT_COMPONENT_PATHS[name],)
    if isinstance(value, str):
        candidates: Sequence[object] = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        candidates = value
    else:
        raise ProjectConfigError(
            f"default {name} path must be a string or a list of strings"
        )

    values: list[str] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, str):
            raise ProjectConfigError(
                f"default {name} path[{index}] must be a string"
            )
        normalized = candidate.strip()
        if normalized:
            values.append(normalized)
    if not values:
        return (DEFAULT_COMPONENT_PATHS[name],)
    if name == "metadata" and len(values) != 1:
        raise ProjectConfigError(
            "default metadata path must define exactly one path"
        )
    return tuple(values)


def _normalise_fallback_paths(
    project_uri: str,
    values: Sequence[str],
    *,
    name: str,
) -> tuple[str, ...]:
    label = f"default {name} path"
    return tuple(
        _normalise_component_path(
            project_uri,
            value,
            label=label if len(values) == 1 else f"{label}[{index}]",
        )
        for index, value in enumerate(values)
    )


def _with_diagnostics(
    directive: _SourceDirective,
    diagnostics: list[dict[str, Any]],
) -> _SourceDirective:
    return _SourceDirective(
        kind=directive.kind,
        uri=directive.uri,
        found=directive.found,
        revision=directive.revision,
        components=directive.components,
        diagnostics=tuple(diagnostics),
    )


def _directive_selected(directive: _SourceDirective | None) -> bool:
    return bool(
        directive
        and directive.found
        and directive.kind in {CONFIG_FILENAME, MANIFEST_FILENAME}
    )


def _with_diagnostic(
    directive: _SourceDirective | None,
    *,
    code: str,
    message: str,
    component: str | None,
    fallback: str,
) -> _SourceDirective:
    if directive is None:
        raise ProjectConfigError(message)
    item: dict[str, Any] = {
        "uri": directive.uri,
        "code": code,
        "message": message,
        "fallback": fallback,
    }
    if component is not None:
        item["component"] = component
    return _with_diagnostics(directive, [*directive.diagnostics, item])


def _fallback_all_components(
    project_uri: str,
    fallback_values: Mapping[str, tuple[str, ...]],
    fallback_provenance: Mapping[str, str],
) -> tuple[
    dict[str, tuple[str, ...]],
    dict[str, str],
    dict[str, tuple[dict[str, Any], ...]],
]:
    resolved: dict[str, tuple[str, ...]] = {}
    provenance: dict[str, str] = {}
    descriptors: dict[str, tuple[dict[str, Any], ...]] = {}
    for name in DEFAULT_COMPONENT_PATHS:
        paths = _normalise_fallback_paths(
            project_uri,
            fallback_values[name],
            name=name,
        )
        resolved[name] = paths
        provenance[name] = fallback_provenance[name]
        descriptors[name] = tuple({"path": path} for path in paths)
    _validate_component_overlap(resolved)
    return resolved, provenance, descriptors


def _is_missing_directive_error(exc: BaseException) -> bool:
    """Recognise provider-specific wrappers around a missing marker."""

    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, (FileNotFoundError, StorageNotFoundError)):
            return True
        status_code = getattr(current, "status_code", None)
        error_code = str(
            getattr(current, "error_code", None)
            or getattr(current, "code", None)
            or ""
        ).lower()
        if status_code == 404 or error_code in {
            "404",
            "not_found",
            "resource_does_not_exist",
        }:
            return True
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return False


def _local_revision(path: Path, raw: bytes) -> dict[str, Any]:
    state = path.stat()
    return {
        "canonical_uri": path.resolve().as_posix(),
        "size": len(raw),
        "mtime_ns": state.st_mtime_ns,
        "last_modified": datetime.fromtimestamp(
            state.st_mtime_ns / 1_000_000_000,
            tz=timezone.utc,
        ).isoformat(),
        "content_hash": hashlib.sha256(raw).hexdigest(),
    }


def _adapter_revision(
    adapter: StorageAdapter,
    uri: str,
    raw: bytes,
) -> dict[str, Any]:
    digest = hashlib.sha256(raw).hexdigest()
    try:
        observed = adapter.stat(uri)
    except Exception:
        return {
            "canonical_uri": adapter.canonical_uri(uri),
            "size": len(raw),
            "content_hash": digest,
        }
    return {
        "canonical_uri": observed.canonical_uri,
        "size": observed.size,
        "last_modified": observed.last_modified.isoformat(),
        "provider_revision": observed.provider_revision,
        "content_hash": digest,
    }
