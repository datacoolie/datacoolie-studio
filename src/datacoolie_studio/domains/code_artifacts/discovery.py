from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from datacoolie_studio.domains.code_artifacts.indexer import (
    MAX_FILES,
    ArtifactIndexError,
    build_artifact_index,
)
from datacoolie_studio.domains.code_artifacts.layout import (
    LayoutRoot,
    convention_src_root,
    parse_pyproject_layout,
)
from datacoolie_studio.domains.sources.scan_policy import (
    CODE_SCAN_EXCLUDED_DIRECTORIES,
    is_excluded_code_directory,
)
from datacoolie_studio.domains.storage.adapters import StorageAdapter, StorageObject
from datacoolie_studio.domains.storage.inventory import StorageInventoryRequest, inventory
from datacoolie_studio.domains.storage.uri import (
    StorageProviderNotEnabled,
    join_uri,
    require_local_path,
    uri_basename,
)


CODE_ARTIFACT_TYPES = {".py": "python_file", ".zip": "zip", ".whl": "wheel"}
PYPROJECT_MAX_BYTES = 1024 * 1024


@dataclass(frozen=True)
class CodeArtifactCandidate:
    uri: str
    artifact_type: str
    label: str
    module_roots: tuple[str, ...] = ()
    module_prefix: str | None = None
    discovery_origin: str = "configured_root"
    record_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CodeDiscoveryResult:
    candidates: list[CodeArtifactCandidate]
    errors: list[dict[str, str]]


def discover_code_artifacts(
    project_uri: str,
    code_uri: str,
    *,
    adapter: StorageAdapter | None = None,
    discovery_mode: str = "auto",
) -> CodeDiscoveryResult:
    if discovery_mode not in {"auto", "explicit"}:
        return CodeDiscoveryResult(
            [],
            [{"uri": code_uri, "code": "invalid_discovery_mode", "message": "code discovery mode must be auto or explicit"}],
        )
    if adapter is not None:
        return _discover_remote(
            project_uri,
            code_uri,
            adapter=adapter,
            discovery_mode=discovery_mode,
        )
    return _discover_local(project_uri, code_uri, discovery_mode=discovery_mode)


def _discover_local(
    project_uri: str,
    code_uri: str,
    *,
    discovery_mode: str,
) -> CodeDiscoveryResult:
    errors: list[dict[str, str]] = []
    try:
        project_root = require_local_path(project_uri)
        code_root = require_local_path(code_uri)
    except StorageProviderNotEnabled as exc:
        return CodeDiscoveryResult(
            [],
            [{"uri": exc.uri, "provider": exc.provider, "message": str(exc)}],
        )

    if code_root.is_file() or (
        not code_root.exists() and code_root.suffix.lower() in CODE_ARTIFACT_TYPES
    ):
        if code_root.is_symlink():
            return CodeDiscoveryResult(
                [],
                [{
                    "uri": str(code_root),
                    "code": "code_symlink",
                    "message": "Source code discovery does not follow symlinked artifacts",
                }],
            )
        candidate = _local_file_candidate(code_root, project_root, errors)
        return CodeDiscoveryResult([candidate] if candidate else [], errors)
    if not code_root.exists() and discovery_mode == "explicit":
        return CodeDiscoveryResult([], [{"uri": code_uri, "message": f"Source code path not found: {code_uri}"}])
    if code_root.exists() and not code_root.is_dir():
        return CodeDiscoveryResult([], [{"uri": code_uri, "message": f"Unsupported source code path type: {code_uri}"}])
    if code_root.is_symlink():
        return CodeDiscoveryResult(
            [],
            [{
                "uri": str(code_root),
                "code": "code_symlink",
                "message": "Source code discovery does not follow symlinked directories",
            }],
        )

    layouts = _local_layout_roots(project_root, code_root, errors)
    candidates: list[CodeArtifactCandidate] = []
    if discovery_mode == "auto":
        candidates.extend(
            _local_package_candidates(project_root, layouts, errors)
        )

    code_files, archives = _local_code_entries(code_root) if code_root.is_dir() else ([], [])
    code_is_project_root = _same_path(code_root, project_root)
    package_under_code_root = any(
        _is_relative_path(code_root, Path(item.uri)) for item in candidates
    )
    layout_covers_code_root = any(
        _same_path(code_root, root) or _is_relative_path(root, code_root)
        for root, _layout in layouts
    )
    if code_files and (
        discovery_mode == "explicit"
        or (
            not layout_covers_code_root
            and (not candidates or not (code_is_project_root or package_under_code_root))
        )
    ):
        candidates.append(
            _local_directory_candidate(
                code_root,
                project_root,
                len(code_files),
                origin="configured_root",
            )
        )
    if discovery_mode == "auto":
        for archive in archives:
            candidate = _local_file_candidate(
                archive,
                project_root,
                errors,
                origin="nested_archive",
            )
            if candidate:
                candidates.append(candidate)

    candidates = _dedupe_candidates(candidates)
    if not candidates and not errors:
        errors.append({"uri": code_uri, "message": "No readable source code artifact found"})
    return CodeDiscoveryResult(candidates, errors)


def _local_layout_roots(
    project_root: Path,
    code_root: Path,
    errors: list[dict[str, str]],
) -> list[tuple[Path, LayoutRoot]]:
    roots: list[tuple[Path, LayoutRoot]] = []
    pyproject_paths: list[Path] = []
    for base in (project_root, code_root):
        if not base.is_dir():
            continue
        candidate = base / "pyproject.toml"
        if candidate not in pyproject_paths:
            pyproject_paths.append(candidate)
    for pyproject in pyproject_paths:
        if not pyproject.is_file():
            continue
        try:
            content = pyproject.read_bytes()
        except OSError as exc:
            errors.append({"uri": str(pyproject), "code": "pyproject_read_error", "message": str(exc)})
            continue
        if len(content) > PYPROJECT_MAX_BYTES:
            errors.append({"uri": str(pyproject), "code": "pyproject_too_large", "message": f"pyproject.toml exceeds {PYPROJECT_MAX_BYTES} bytes"})
            continue
        parsed, parse_errors = parse_pyproject_layout(content, source_uri=str(pyproject))
        errors.extend(parse_errors)
        if not parsed:
            convention = pyproject.parent / "src"
            if not parse_errors and not convention.is_dir():
                errors.append({
                    "uri": str(pyproject),
                    "code": "pyproject_layout_unrecognized",
                    "message": "No supported static package layout was found; configure the code root or module roots manually",
                })
            parsed = [convention_src_root()]
        for layout in parsed:
            root = (pyproject.parent / layout.relative_path).resolve()
            try:
                root.relative_to(pyproject.parent.resolve())
            except ValueError:
                errors.append({"uri": str(pyproject), "code": "package_root_escape", "message": f"Package root escapes project: {layout.relative_path}"})
                continue
            roots.append((root, layout))
    return _dedupe_layout_paths(roots)


def _local_package_candidates(
    project_root: Path,
    layouts: list[tuple[Path, LayoutRoot]],
    errors: list[dict[str, str]],
) -> list[CodeArtifactCandidate]:
    candidates: list[CodeArtifactCandidate] = []
    for root, layout in layouts:
        if not root.is_dir():
            continue
        python_files, _ = _local_code_entries(root)
        if not python_files:
            continue
        has_marker = any(path.name == "__init__.py" for path in python_files)
        if not has_marker and layout.module_prefix is None:
            if layout.relative_path == ".":
                continue
            errors.append({"uri": str(root), "code": "namespace_package_manual_mapping", "message": "Package root has Python files but no __init__.py; configure a module prefix manually"})
            continue
        candidates.append(
            _local_directory_candidate(
                root,
                project_root,
                len(python_files),
                module_prefix=layout.module_prefix,
                origin=layout.origin,
            )
        )
    return candidates


def _local_code_entries(root: Path) -> tuple[list[Path], list[Path]]:
    python_files: list[Path] = []
    archives: list[Path] = []
    if not root.is_dir():
        return python_files, archives
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        base = Path(current)
        directories[:] = sorted(
            name
            for name in directories
            if not is_excluded_code_directory(name)
            and not (base / name).is_symlink()
        )
        for name in sorted(files):
            path = base / name
            if path.is_symlink():
                continue
            suffix = path.suffix.lower()
            if suffix == ".py":
                python_files.append(path)
            elif suffix in {".zip", ".whl"}:
                archives.append(path)
    return python_files, archives


def _local_file_candidate(
    path: Path,
    project_root: Path,
    errors: list[dict[str, str]],
    *,
    origin: str = "direct",
) -> CodeArtifactCandidate | None:
    if path.is_symlink():
        errors.append({
            "uri": str(path),
            "code": "code_symlink",
            "message": "Source code discovery does not follow symlinked artifacts",
        })
        return None
    if not path.exists():
        errors.append({"uri": str(path), "message": f"Source code artifact not found: {path}"})
        return None
    if not path.is_file():
        errors.append({"uri": str(path), "message": f"Source code artifact is not a file: {path}"})
        return None
    artifact_type = CODE_ARTIFACT_TYPES.get(path.suffix.lower())
    if artifact_type is None:
        errors.append({"uri": str(path), "message": "Supported source artifacts are .py, .zip, and .whl"})
        return None
    if artifact_type == "python_file":
        count = 1
    else:
        try:
            indexed = build_artifact_index(str(path), artifact_type)
        except ArtifactIndexError as exc:
            errors.append({"uri": str(path), "code": "artifact_index_error", "message": str(exc)})
            return None
        count = int(indexed["manifest"]["python_files"])
        if count == 0:
            errors.append({"uri": str(path), "code": "empty_code_artifact", "message": "Archive does not contain Python files"})
            return None
    return CodeArtifactCandidate(
        uri=str(path),
        artifact_type=artifact_type,
        label=_local_label(path, project_root),
        discovery_origin=origin,
        record_counts={"python_files": count},
    )


def _local_directory_candidate(
    path: Path,
    project_root: Path,
    python_file_count: int,
    *,
    module_prefix: str | None = None,
    origin: str,
) -> CodeArtifactCandidate:
    return CodeArtifactCandidate(
        uri=str(path),
        artifact_type="directory",
        label=_local_label(path, project_root),
        module_prefix=module_prefix,
        discovery_origin=origin,
        record_counts={"python_files": python_file_count},
    )


def _discover_remote(
    project_uri: str,
    code_uri: str,
    *,
    adapter: StorageAdapter,
    discovery_mode: str,
) -> CodeDiscoveryResult:
    errors: list[dict[str, str]] = []
    project_root = adapter.canonical_uri(project_uri)
    code_root = adapter.canonical_uri(code_uri)
    if _uri_suffix(code_root) in CODE_ARTIFACT_TYPES:
        if not _remote_exists(adapter, code_root):
            return CodeDiscoveryResult([], [{"uri": code_root, "message": f"Source code artifact not found: {code_root}"}])
        return CodeDiscoveryResult(
            [_remote_file_candidate(code_root, project_root, CODE_ARTIFACT_TYPES[_uri_suffix(code_root)], "direct")],
            [],
        )
    if discovery_mode == "explicit":
        files = _remote_code_entries(adapter, code_root, errors)
        return _remote_explicit_result(code_root, project_root, files, errors)

    layouts = _remote_layout_roots(project_root, code_root, adapter, errors)
    candidates: list[CodeArtifactCandidate] = []
    package_roots: dict[str, list[StorageObject]] = {}
    for root, layout in layouts:
        objects = _remote_code_entries(adapter, root, errors, cache=package_roots)
        python_files = [item for item in objects if _uri_suffix(item.canonical_uri) == ".py"]
        if not python_files:
            continue
        has_marker = any(_relative_uri(root, item.canonical_uri).rsplit("/", 1)[-1] == "__init__.py" for item in python_files)
        if not has_marker and layout.module_prefix is None:
            if layout.relative_path == ".":
                continue
            errors.append({"uri": root, "code": "namespace_package_manual_mapping", "message": "Package root has Python files but no __init__.py; configure a module prefix manually"})
            continue
        candidates.append(
            _remote_directory_candidate(root, project_root, len(python_files), layout)
        )

    objects = _remote_code_entries(adapter, code_root, errors, cache=package_roots)
    python_files = [item for item in objects if _uri_suffix(item.canonical_uri) == ".py"]
    archives = [item for item in objects if _uri_suffix(item.canonical_uri) in {".zip", ".whl"}]
    code_is_project_root = _same_uri(code_root, project_root)
    package_under_code_root = any(_uri_is_under(code_root, item.uri) for item in candidates)
    layout_covers_code_root = any(
        _same_uri(code_root, root) or _uri_is_under(root, code_root)
        for root, _layout in layouts
    )
    if python_files and (
        not layout_covers_code_root
        and (not candidates or not (code_is_project_root or package_under_code_root))
    ):
        candidates.append(
            CodeArtifactCandidate(
                uri=code_root,
                artifact_type="directory",
                label=_remote_label(code_root, project_root),
                discovery_origin="configured_root",
                record_counts={"python_files": len(python_files)},
            )
        )
    for archive in archives:
        artifact_type = CODE_ARTIFACT_TYPES[_uri_suffix(archive.canonical_uri)]
        candidates.append(_remote_file_candidate(archive.canonical_uri, project_root, artifact_type, "nested_archive"))
    candidates = _dedupe_candidates(candidates)
    if not candidates and not errors:
        errors.append({"uri": code_root, "message": "No readable source code artifact found"})
    return CodeDiscoveryResult(candidates, errors)


def _remote_layout_roots(
    project_root: str,
    code_root: str,
    adapter: StorageAdapter,
    errors: list[dict[str, str]],
) -> list[tuple[str, LayoutRoot]]:
    roots: list[tuple[str, LayoutRoot]] = []
    pyprojects: list[str] = []
    for base in (project_root, code_root):
        candidate = join_uri(base, "pyproject.toml")
        if candidate not in pyprojects:
            pyprojects.append(candidate)
    for pyproject in pyprojects:
        content = _remote_pyproject_bytes(adapter, pyproject)
        if content is None:
            continue
        parsed, parse_errors = parse_pyproject_layout(content, source_uri=pyproject)
        errors.extend(parse_errors)
        if not parsed:
            parsed = [convention_src_root()]
        base = pyproject.rsplit("/", 1)[0]
        for layout in parsed:
            roots.append((join_uri(base, layout.relative_path), layout))
    return _dedupe_remote_layout_paths(roots)


def _remote_pyproject_bytes(adapter: StorageAdapter, uri: str) -> bytes | None:
    try:
        revision = adapter.stat(uri)
        if revision.size > PYPROJECT_MAX_BYTES:
            return None
        with adapter.open_read(uri) as handle:
            content = handle.read(PYPROJECT_MAX_BYTES + 1)
    except Exception:
        return None
    return content if len(content) <= PYPROJECT_MAX_BYTES else None


def _remote_code_entries(
    adapter: StorageAdapter,
    root: str,
    errors: list[dict[str, str]],
    *,
    cache: dict[str, list[StorageObject]] | None = None,
) -> list[StorageObject]:
    if cache is not None and root in cache:
        return cache[root]
    try:
        observed = inventory(
            adapter,
            StorageInventoryRequest(
                uri=root,
                purpose="observe",
                recursive=True,
                object_types=frozenset({"file"}),
                suffixes=frozenset({".py", ".zip", ".whl"}),
                exclude_directories=CODE_SCAN_EXCLUDED_DIRECTORIES,
                object_limit=MAX_FILES,
            ),
        )
    except Exception as exc:
        errors.append({"uri": root, "code": "code_inventory_error", "message": str(exc)})
        result: list[StorageObject] = []
    else:
        if observed.completeness != "complete":
            errors.append({"uri": root, "code": "code_inventory_incomplete", "message": f"Source code inventory exceeded {MAX_FILES} objects"})
            result = []
        else:
            result = [
                item
                for item in observed.files
                if _uri_is_under(root, item.canonical_uri)
                and not _remote_path_excluded(root, item.canonical_uri)
            ]
    if cache is not None:
        cache[root] = result
    return result


def _remote_explicit_result(
    code_root: str,
    project_root: str,
    objects: list[StorageObject],
    errors: list[dict[str, str]],
) -> CodeDiscoveryResult:
    python_files = [item for item in objects if _uri_suffix(item.canonical_uri) == ".py"]
    if not python_files:
        if not errors:
            errors.append({"uri": code_root, "message": "No readable source code artifact found"})
        return CodeDiscoveryResult([], errors)
    return CodeDiscoveryResult(
        [
            CodeArtifactCandidate(
                uri=code_root,
                artifact_type="directory",
                label=_remote_label(code_root, project_root),
                discovery_origin="configured_root",
                record_counts={"python_files": len(python_files)},
            )
        ],
        errors,
    )


def _remote_file_candidate(uri: str, project_root: str, artifact_type: str, origin: str) -> CodeArtifactCandidate:
    return CodeArtifactCandidate(
        uri=uri,
        artifact_type=artifact_type,
        label=_remote_label(uri, project_root),
        discovery_origin=origin,
        record_counts={"python_files": 1 if artifact_type == "python_file" else 0},
    )


def _remote_directory_candidate(uri: str, project_root: str, count: int, layout: LayoutRoot) -> CodeArtifactCandidate:
    return CodeArtifactCandidate(
        uri=uri,
        artifact_type="directory",
        label=_remote_label(uri, project_root),
        module_prefix=layout.module_prefix,
        discovery_origin=layout.origin,
        record_counts={"python_files": count},
    )


def _remote_exists(adapter: StorageAdapter, uri: str) -> bool:
    try:
        adapter.stat(uri)
    except Exception:
        return False
    return True


def _uri_suffix(uri: str) -> str:
    return Path(uri.rstrip("/")).suffix.lower()


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left == right


def _is_relative_path(root: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _same_uri(left: str, right: str) -> bool:
    return left.rstrip("/") == right.rstrip("/")


def _uri_is_under(root: str, child: str) -> bool:
    return child.rstrip("/").startswith(root.rstrip("/") + "/")


def _remote_path_excluded(root: str, child: str) -> bool:
    relative = _relative_uri(root, child)
    return any(is_excluded_code_directory(part) for part in relative.split("/")[:-1])


def _relative_uri(root: str, child: str) -> str:
    prefix = root.rstrip("/") + "/"
    return child.removeprefix(prefix) if child.startswith(prefix) else child


def _local_label(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix() or path.name
    except ValueError:
        return path.name


def _remote_label(uri: str, project_root: str) -> str:
    relative = _relative_uri(project_root.rstrip("/"), uri.rstrip("/"))
    return relative if relative != uri.rstrip("/") else (uri_basename(uri) or "source code")


def _dedupe_layout_paths(roots: list[tuple[Path, LayoutRoot]]) -> list[tuple[Path, LayoutRoot]]:
    seen: set[tuple[str, str | None]] = set()
    result: list[tuple[Path, LayoutRoot]] = []
    for path, layout in roots:
        key = (str(path), layout.module_prefix)
        if key not in seen:
            seen.add(key)
            result.append((path, layout))
    return result


def _dedupe_remote_layout_paths(roots: list[tuple[str, LayoutRoot]]) -> list[tuple[str, LayoutRoot]]:
    seen: set[tuple[str, str | None]] = set()
    result: list[tuple[str, LayoutRoot]] = []
    for path, layout in roots:
        key = (path.rstrip("/"), layout.module_prefix)
        if key not in seen:
            seen.add(key)
            result.append((path, layout))
    return result


def _dedupe_candidates(candidates: list[CodeArtifactCandidate]) -> list[CodeArtifactCandidate]:
    seen: set[tuple[str, str]] = set()
    result: list[CodeArtifactCandidate] = []
    for candidate in sorted(candidates, key=lambda item: (item.uri, item.artifact_type, item.discovery_origin)):
        key = (candidate.uri, candidate.artifact_type)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result
