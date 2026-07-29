COMMON_SCAN_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
        "venv",
    }
)

METADATA_SCAN_EXCLUDED_DIRECTORIES = (
    COMMON_SCAN_EXCLUDED_DIRECTORIES | {"watermarks"}
)

CODE_SCAN_EXCLUDED_DIRECTORIES = COMMON_SCAN_EXCLUDED_DIRECTORIES
