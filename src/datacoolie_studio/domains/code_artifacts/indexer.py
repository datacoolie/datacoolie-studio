from __future__ import annotations

import hashlib
import importlib.metadata
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile


MAX_FILES = 10_000
MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_TOTAL_SIZE = 100 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
SUPPORTED_ARTIFACT_TYPES = {"directory", "python_file", "zip", "wheel", "installed_distribution"}


class ArtifactIndexError(RuntimeError):
    pass


def build_artifact_index(
    uri: str,
    artifact_type: str,
    module_roots: list[str] | None = None,
    module_prefix: str | None = None,
) -> dict[str, Any]:
    if artifact_type not in SUPPORTED_ARTIFACT_TYPES:
        raise ArtifactIndexError(f"Unsupported code artifact type: {artifact_type}")
    roots = [_normalize_root(value) for value in (module_roots or []) if str(value).strip()]
    prefix = _normalize_module_prefix(module_prefix)
    if artifact_type == "directory":
        return _index_directory(Path(uri).expanduser(), roots, prefix)
    if artifact_type == "python_file":
        return _index_python_file(Path(uri).expanduser(), prefix)
    if artifact_type in {"zip", "wheel"}:
        return _index_archive(Path(uri).expanduser(), roots, artifact_type, prefix)
    return _index_distribution(uri, roots, prefix)


def read_artifact_module(
    uri: str,
    artifact_type: str,
    module_name: str,
    module_roots: list[str] | None = None,
    module_prefix: str | None = None,
) -> tuple[str, str]:
    indexed = build_artifact_index(uri, artifact_type, module_roots, module_prefix)
    entry = indexed["modules"].get(module_name)
    if entry is None:
        raise ArtifactIndexError(f"Python module not found in code artifact: {module_name}")
    relative = str(entry["path"])
    if artifact_type == "directory":
        root = Path(uri).expanduser().resolve()
        path = (root / Path(relative)).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ArtifactIndexError(f"Python source escapes artifact root: {relative}") from exc
        content = path.read_bytes()
    elif artifact_type == "python_file":
        path = Path(uri).expanduser().resolve()
        if relative != path.name:
            raise ArtifactIndexError(f"Python source not found in file artifact: {relative}")
        content = path.read_bytes()
    elif artifact_type in {"zip", "wheel"}:
        with ZipFile(Path(uri).expanduser()) as archive:
            content = archive.read(_safe_member(relative))
    else:
        distribution = importlib.metadata.distribution(uri)
        matching = next(
            (item for item in distribution.files or [] if str(item).replace("\\", "/") == relative),
            None,
        )
        if matching is None:
            raise ArtifactIndexError(f"Python source not found in installed distribution: {relative}")
        content = Path(distribution.locate_file(matching)).read_bytes()
    if len(content) > MAX_FILE_SIZE:
        raise ArtifactIndexError(f"Python source file exceeds {MAX_FILE_SIZE} bytes: {relative}")
    try:
        return content.decode("utf-8"), relative
    except UnicodeDecodeError as exc:
        raise ArtifactIndexError(f"Python source is not UTF-8: {relative}") from exc


def _index_directory(root: Path, module_roots: list[str], module_prefix: str | None) -> dict[str, Any]:
    if not root.exists():
        raise ArtifactIndexError(f"Code artifact directory not found: {root}")
    if not root.is_dir():
        raise ArtifactIndexError(f"Code artifact must be a directory: {root}")
    resolved_root = root.resolve()
    files = []
    total_size = 0
    for path in sorted(root.rglob("*.py")):
        if not path.is_file():
            continue
        try:
            path.resolve().relative_to(resolved_root)
        except ValueError as exc:
            raise ArtifactIndexError(f"Python source escapes artifact root: {path}") from exc
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        total_size = _check_limits(len(files) + 1, size, total_size, relative)
        files.append(_file_entry(relative, size, None, path.read_bytes()))
    _assign_module_names(
        files,
        module_roots,
        module_prefix,
        root_package=root.name if (root / "__init__.py").is_file() else None,
    )
    return _result("directory", str(root), files, total_size)


def _index_python_file(path: Path, module_prefix: str | None) -> dict[str, Any]:
    if not path.exists():
        raise ArtifactIndexError(f"Python source file not found: {path}")
    if not path.is_file() or path.suffix.lower() != ".py":
        raise ArtifactIndexError(f"Code artifact must be a Python file: {path}")
    size = path.stat().st_size
    total_size = _check_limits(1, size, 0, path.name)
    module = _apply_module_prefix(path.stem, module_prefix)
    files = [_file_entry(path.name, size, module, path.read_bytes())]
    return _result("python_file", str(path), files, total_size)


def _index_archive(path: Path, module_roots: list[str], artifact_type: str, module_prefix: str | None) -> dict[str, Any]:
    if not path.exists():
        raise ArtifactIndexError(f"Code artifact file not found: {path}")
    if not path.is_file():
        raise ArtifactIndexError(f"Code artifact must be a file: {path}")
    files = []
    total_size = 0
    try:
        with ZipFile(path) as archive:
            for info in sorted(archive.infolist(), key=lambda item: item.filename):
                if info.is_dir():
                    continue
                member = _safe_member(info.filename)
                if not member.endswith(".py"):
                    continue
                ratio = info.file_size / max(info.compress_size, 1)
                if ratio > MAX_COMPRESSION_RATIO:
                    raise ArtifactIndexError(f"Archive member compression ratio is too high: {member}")
                total_size = _check_limits(len(files) + 1, info.file_size, total_size, member)
                content = archive.read(info)
                files.append(_file_entry(member, info.file_size, None, content))
    except BadZipFile as exc:
        raise ArtifactIndexError(f"Invalid ZIP or wheel file: {path}") from exc
    root_package = path.stem if artifact_type == "zip" and any(item["path"] == "__init__.py" for item in files) else None
    _assign_module_names(files, module_roots, module_prefix, root_package=root_package)
    return _result(artifact_type, str(path), files, total_size)


def _index_distribution(name: str, module_roots: list[str], module_prefix: str | None) -> dict[str, Any]:
    try:
        distribution = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError as exc:
        raise ArtifactIndexError(f"Installed distribution not found: {name}") from exc
    files = []
    total_size = 0
    for item in sorted(distribution.files or [], key=str):
        relative = str(item).replace("\\", "/")
        if not relative.endswith(".py"):
            continue
        path = Path(distribution.locate_file(item))
        if not path.is_file():
            continue
        size = path.stat().st_size
        total_size = _check_limits(len(files) + 1, size, total_size, relative)
        files.append(_file_entry(relative, size, None, path.read_bytes()))
    distribution_root = name.replace("-", "_") if any(item["path"] == "__init__.py" for item in files) else None
    _assign_module_names(files, module_roots, module_prefix, root_package=distribution_root)
    result = _result("installed_distribution", name, files, total_size)
    result["distribution_version"] = distribution.version
    return result


def _safe_member(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    first = path.parts[0] if path.parts else ""
    if "\x00" in normalized or path.is_absolute() or ".." in path.parts or first.endswith(":"):
        raise ArtifactIndexError(f"Unsafe archive member path: {value}")
    return path.as_posix()


def _file_entry(relative: str, size: int, module: str | None, content: bytes) -> dict[str, Any]:
    return {
        "path": relative,
        "module": module,
        "size": size,
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _assign_module_names(
    files: list[dict[str, Any]],
    module_roots: list[str],
    module_prefix: str | None,
    *,
    root_package: str | None,
) -> None:
    effective_paths = {
        str(item["path"]): effective
        for item in files
        if (effective := _effective_module_path(str(item["path"]), module_roots)) is not None
    }
    package_directories = {
        _parent_path(effective)
        for effective in effective_paths.values()
        if _filename(effective) == "__init__.py"
    }
    normalized_root_package = _normalize_module_prefix(root_package)
    for item in files:
        effective = effective_paths.get(str(item["path"]))
        if effective is None:
            item["module"] = None
            continue
        item["module"] = _module_name(
            effective,
            package_directories,
            module_prefix,
            root_package=normalized_root_package,
        )


def _effective_module_path(path: str, module_roots: list[str]) -> str | None:
    normalized = path.replace("\\", "/").strip("/")
    if module_roots:
        matching_root = next(
            (
                root
                for root in module_roots
                if normalized == root or normalized.startswith(f"{root}/")
            ),
            None,
        )
        if matching_root is None:
            return None
        normalized = normalized[len(matching_root):].lstrip("/")
    return normalized or None


def _module_name(
    path: str,
    package_directories: set[str],
    module_prefix: str | None,
    *,
    root_package: str | None,
) -> str | None:
    normalized = path.replace("\\", "/").strip("/")
    if not normalized.endswith(".py"):
        return None
    filename = _filename(normalized)
    stem = filename[:-3]
    parent = _parent_path(normalized)
    package_name = _containing_package_name(parent, package_directories, root_package)
    if filename == "__init__.py":
        module_name = package_name
    elif package_name:
        module_name = f"{package_name}.{stem}"
    else:
        module_name = stem
    return _apply_module_prefix(module_name, module_prefix)


def _containing_package_name(
    parent: str,
    package_directories: set[str],
    root_package: str | None,
) -> str | None:
    if parent not in package_directories:
        return None
    parts = [part for part in parent.split("/") if part]
    package_parts: list[str] = []
    current = parts
    while "/".join(current) in package_directories:
        if not current:
            if root_package:
                package_parts.insert(0, root_package)
            break
        package_parts.insert(0, current[-1])
        current = current[:-1]
    return ".".join(package_parts) or None


def _apply_module_prefix(module_name: str | None, module_prefix: str | None) -> str | None:
    if not module_name:
        return None
    if not module_prefix or module_name == module_prefix or module_name.startswith(f"{module_prefix}."):
        return module_name
    return f"{module_prefix}.{module_name}"


def _parent_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip("/")
    return normalized.rpartition("/")[0]


def _filename(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _normalize_root(value: str) -> str:
    return value.strip().replace("\\", "/").strip("/")


def _normalize_module_prefix(value: str | None) -> str | None:
    if not value:
        return None
    normalized = str(value).strip().strip(".")
    if not normalized:
        return None
    return ".".join(part for part in normalized.replace("/", ".").replace("\\", ".").split(".") if part)


def _check_limits(file_count: int, file_size: int, total_size: int, name: str) -> int:
    if file_count > MAX_FILES:
        raise ArtifactIndexError(f"Code artifact exceeds {MAX_FILES} Python files")
    if file_size > MAX_FILE_SIZE:
        raise ArtifactIndexError(f"Python source file exceeds {MAX_FILE_SIZE} bytes: {name}")
    total = total_size + file_size
    if total > MAX_TOTAL_SIZE:
        raise ArtifactIndexError(f"Code artifact exceeds {MAX_TOTAL_SIZE} decompressed bytes")
    return total


def _result(artifact_type: str, uri: str, files: list[dict[str, Any]], total_size: int) -> dict[str, Any]:
    module_entries: dict[str, list[dict[str, Any]]] = {}
    for entry in files:
        if entry["module"]:
            module_entries.setdefault(str(entry["module"]), []).append(entry)
    modules = {
        module_name: entries[0]
        for module_name, entries in module_entries.items()
        if len(entries) == 1
    }
    diagnostics = []
    if not files:
        diagnostics.append({
            "severity": "warning",
            "code": "no_python_sources",
            "message": "No Python source files were found in the artifact",
        })
    for module_name, entries in sorted(module_entries.items()):
        if len(entries) < 2:
            continue
        diagnostics.append({
            "severity": "warning",
            "code": "duplicate_python_module",
            "message": f"Multiple Python files resolve to module {module_name}",
            "details": {"module": module_name, "paths": [str(entry["path"]) for entry in entries]},
        })
    return {
        "artifact_type": artifact_type,
        "uri": uri,
        "manifest": {"python_files": len(files), "modules": len(modules), "total_size": total_size},
        "files": files,
        "modules": modules,
        "diagnostics": diagnostics,
    }
