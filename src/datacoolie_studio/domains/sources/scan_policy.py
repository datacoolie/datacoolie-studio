COMMON_SCAN_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".eggs",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)

METADATA_SCAN_EXCLUDED_DIRECTORIES = (
    COMMON_SCAN_EXCLUDED_DIRECTORIES | {"watermarks"}
)

CODE_SCAN_EXCLUDED_DIRECTORIES = COMMON_SCAN_EXCLUDED_DIRECTORIES


def is_excluded_code_directory(name: str) -> bool:
    """Return whether a directory should be ignored by static code scans.

    ``*.egg-info`` is intentionally handled as a suffix because build tools
    commonly create names such as ``package-1.0.egg-info``.
    """

    normalized = str(name).strip().lower()
    return normalized in CODE_SCAN_EXCLUDED_DIRECTORIES or normalized.endswith(
        ".egg-info"
    )
