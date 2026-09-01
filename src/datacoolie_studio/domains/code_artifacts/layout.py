from __future__ import annotations

from dataclasses import dataclass
import tomllib


@dataclass(frozen=True)
class LayoutRoot:
    """A statically declared Python package root from ``pyproject.toml``."""

    relative_path: str
    module_prefix: str | None
    origin: str


def parse_pyproject_layout(
    content: bytes,
    *,
    source_uri: str,
) -> tuple[list[LayoutRoot], list[dict[str, str]]]:
    """Read supported packaging layout hints without importing project code."""

    try:
        document = tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return [], [
            {
                "uri": source_uri,
                "code": "pyproject_parse_error",
                "message": f"Could not parse pyproject.toml: {exc}",
            }
        ]
    if not isinstance(document, dict):
        return [], [
            {
                "uri": source_uri,
                "code": "pyproject_parse_error",
                "message": "pyproject.toml must contain a TOML table",
            }
        ]

    tool = document.get("tool")
    if not isinstance(tool, dict):
        return [], []

    roots: list[LayoutRoot] = []
    errors: list[dict[str, str]] = []
    setuptools = tool.get("setuptools")
    if isinstance(setuptools, dict):
        package_dir = setuptools.get("package-dir")
        if package_dir is not None:
            if not isinstance(package_dir, dict):
                errors.append(_diagnostic(source_uri, "setuptools.package-dir must be a table"))
            else:
                for prefix, value in package_dir.items():
                    if not isinstance(prefix, str) or not isinstance(value, str):
                        errors.append(_diagnostic(source_uri, "setuptools.package-dir entries must be strings"))
                        continue
                    relative = _relative_path(value)
                    if relative is None:
                        errors.append(_diagnostic(source_uri, f"Invalid setuptools package directory: {value}"))
                        continue
                    roots.append(
                        LayoutRoot(
                            relative,
                            _module_prefix(prefix),
                            "pyproject.setuptools.package-dir",
                        )
                    )
        packages = setuptools.get("packages")
        package_find = packages.get("find") if isinstance(packages, dict) else None
        if package_find is not None:
            if not isinstance(package_find, dict):
                errors.append(_diagnostic(source_uri, "setuptools.packages.find must be a table"))
            else:
                where = package_find.get("where", ["."])
                values = where if isinstance(where, list) else [where]
                if not all(isinstance(value, str) for value in values):
                    errors.append(_diagnostic(source_uri, "setuptools.packages.find.where must contain strings"))
                else:
                    for value in values:
                        relative = _relative_path(value)
                        if relative is None:
                            errors.append(_diagnostic(source_uri, f"Invalid setuptools package search root: {value}"))
                            continue
                        roots.append(LayoutRoot(relative, None, "pyproject.setuptools.packages.find"))

    poetry = tool.get("poetry")
    if isinstance(poetry, dict) and "packages" in poetry:
        packages = poetry.get("packages")
        if not isinstance(packages, list):
            errors.append(_diagnostic(source_uri, "poetry.packages must be an array"))
        else:
            for item in packages:
                if not isinstance(item, dict) or not isinstance(item.get("include"), str):
                    errors.append(_diagnostic(source_uri, "poetry.packages entries must define a string include"))
                    continue
                include = str(item["include"])
                base = item.get("from", ".")
                if not isinstance(base, str):
                    errors.append(_diagnostic(source_uri, "poetry package from must be a string"))
                    continue
                relative = _relative_path(f"{base}/{include}")
                if relative is None:
                    errors.append(_diagnostic(source_uri, f"Invalid Poetry package path: {base}/{include}"))
                    continue
                roots.append(
                    LayoutRoot(
                        relative,
                        _module_prefix(include),
                        "pyproject.poetry.packages",
                    )
                )

    hatch = tool.get("hatch")
    hatch_build = hatch.get("build") if isinstance(hatch, dict) else None
    hatch_targets = hatch_build.get("targets") if isinstance(hatch_build, dict) else None
    hatch_wheel = hatch_targets.get("wheel") if isinstance(hatch_targets, dict) else {}
    if isinstance(hatch_wheel, dict) and "packages" in hatch_wheel:
        packages = hatch_wheel.get("packages")
        values = packages if isinstance(packages, list) else [packages]
        if not all(isinstance(value, str) for value in values):
            errors.append(_diagnostic(source_uri, "hatch wheel packages must contain strings"))
        else:
            for value in values:
                relative = _relative_path(value)
                if relative is None:
                    errors.append(_diagnostic(source_uri, f"Invalid Hatch package path: {value}"))
                    continue
                roots.append(
                    LayoutRoot(
                        relative,
                        _module_prefix(relative.rsplit("/", 1)[-1]),
                        "pyproject.hatch.build.targets.wheel.packages",
                    )
                )

    pdm = tool.get("pdm")
    if isinstance(pdm, dict):
        pdm_build = pdm.get("build")
        if isinstance(pdm_build, dict):
            package_dir = pdm_build.get("package-dir")
            if package_dir is not None:
                if not isinstance(package_dir, str):
                    errors.append(_diagnostic(source_uri, "pdm.build.package-dir must be a string"))
                else:
                    relative = _relative_path(package_dir)
                    if relative is None:
                        errors.append(_diagnostic(source_uri, f"Invalid PDM package directory: {package_dir}"))
                    else:
                        roots.append(LayoutRoot(relative, None, "pyproject.pdm.build.package-dir"))
            includes = pdm_build.get("includes")
            if includes is not None:
                values = includes if isinstance(includes, list) else [includes]
                if not all(isinstance(value, str) for value in values):
                    errors.append(_diagnostic(source_uri, "pdm.build.includes must contain strings"))
                else:
                    for value in values:
                        relative = _relative_path(value)
                        if relative is None:
                            errors.append(_diagnostic(source_uri, f"Invalid PDM include path: {value}"))
                            continue
                        roots.append(
                            LayoutRoot(
                                relative,
                                _module_prefix(relative.rsplit("/", 1)[-1]),
                                "pyproject.pdm.build.includes",
                            )
                        )

    return _dedupe_roots(roots), errors


def convention_src_root() -> LayoutRoot:
    return LayoutRoot("src", None, "pyproject.src_convention")


def _dedupe_roots(roots: list[LayoutRoot]) -> list[LayoutRoot]:
    seen: set[tuple[str, str | None]] = set()
    result: list[LayoutRoot] = []
    for root in roots:
        key = (root.relative_path, root.module_prefix)
        if key in seen:
            continue
        seen.add(key)
        result.append(root)
    return result


def _relative_path(value: str) -> str | None:
    normalized = str(value).strip().replace("\\", "/")
    if not normalized or normalized.startswith("/") or ":" in normalized.split("/", 1)[0]:
        return None
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if not parts:
        return "." if normalized in {".", "./"} else None
    if any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _module_prefix(value: str) -> str | None:
    normalized = str(value).strip().replace("/", ".").replace("\\", ".")
    parts = [part for part in normalized.strip(".").split(".") if part]
    return ".".join(parts) or None


def _diagnostic(source_uri: str, message: str) -> dict[str, str]:
    return {"uri": source_uri, "code": "pyproject_layout_error", "message": message}
