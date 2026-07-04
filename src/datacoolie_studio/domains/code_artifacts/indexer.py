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
SUPPORTED_ARTIFACT_TYPES = {"directory", "zip", "wheel", "installed_distribution"}


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
        files.append(_file_entry(relative, size, _module_name(relative, module_roots, module_prefix), path.read_bytes()))
    return _result("directory", str(root), files, total_size)


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
                files.append(_file_entry(member, info.file_size, _module_name(member, module_roots, module_prefix), content))
    except BadZipFile as exc:
        raise ArtifactIndexError(f"Invalid ZIP or wheel file: {path}") from exc
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
        files.append(_file_entry(relative, size, _module_name(relative, module_roots, module_prefix), path.read_bytes()))
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


def _module_name(path: str, module_roots: list[str], module_prefix: str | None) -> str | None:
    normalized = path.replace("\\", "/")
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
    if not normalized.endswith(".py"):
        return None
    module_path = normalized[:-3]
    if module_path.endswith("/__init__"):
        module_path = module_path[:-9]
    module_name = module_path.strip("/").replace("/", ".") or None
    if module_name and module_prefix:
        return f"{module_prefix}.{module_name}"
    return module_name


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
    modules = {entry["module"]: entry for entry in files if entry["module"]}
    diagnostics = []
    if not files:
        diagnostics.append({
            "severity": "warning",
            "code": "no_python_sources",
            "message": "No Python source files were found in the artifact",
        })
    return {
        "artifact_type": artifact_type,
        "uri": uri,
        "manifest": {"python_files": len(files), "modules": len(modules), "total_size": total_size},
        "files": files,
        "modules": modules,
        "diagnostics": diagnostics,
    }
