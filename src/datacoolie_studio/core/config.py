from __future__ import annotations

import os
import re
from pathlib import Path

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from datacoolie_studio.core.runtime_resources import available_cpu_count, available_memory_bytes


MAX_ANALYTICS_UPGRADE_BUILD_CONCURRENCY = 16
DEFAULT_ANALYTICS_UPGRADE_BATCH_MAX_FILES = 16
DEFAULT_ANALYTICS_UPGRADE_BATCH_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_ANALYTICS_UPGRADE_MAX_INFLIGHT_BYTES = 256 * 1024 * 1024
DEFAULT_ANALYTICS_UPGRADE_BUILD_CONCURRENCY = 8
_ANALYTICS_MEMORY_LIMIT_PATTERN = re.compile(
    r"^(?P<amount>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)$",
    re.IGNORECASE,
)


def data_dir() -> Path:
    configured = os.environ.get("DATACOOLIE_STUDIO_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".datacoolie" / "datacoolie-studio"


def default_db_dir() -> Path:
    return data_dir() / "db"


def default_database_path() -> Path:
    return default_db_dir() / "studio.db"


def backup_dir() -> Path:
    return data_dir() / "backups"


def metadata_write_artifact_dir() -> Path:
    return data_dir() / "metadata-write-artifacts"


def cache_dir() -> Path:
    return data_dir() / "cache"


def source_materialization_cache_dir() -> Path:
    return cache_dir() / "source-materializations"


def analytics_database_path() -> Path:
    return cache_dir() / "analytics.duckdb"


def analytics_duckdb_threads() -> int:
    name = "DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_THREADS"
    value = os.environ.get(name)
    if value is None or not value.strip() or value.strip().lower() == "auto":
        return min(available_cpu_count(), 4)
    return _bounded_positive_int(name, 1, maximum=None)


def analytics_duckdb_memory_limit() -> str:
    configured = os.environ.get("DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_MEMORY_LIMIT")
    if configured is None or not configured.strip() or configured.strip().lower() == "auto":
        return _default_analytics_duckdb_memory_limit()
    value = "".join(configured.split())
    match = _ANALYTICS_MEMORY_LIMIT_PATTERN.fullmatch(value)
    if match is None or float(match.group("amount")) <= 0:
        raise ValueError(
            "DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_MEMORY_LIMIT must be a positive "
            "DuckDB size such as 1GB or 512MiB"
        )
    return value


def analytics_duckdb_temp_directory() -> Path:
    configured = os.environ.get("DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_TEMP_DIRECTORY")
    if configured and configured.strip():
        return Path(configured).expanduser()
    return cache_dir() / "analytics-tmp"


def analytics_upgrade_batch_max_files() -> int:
    return _bounded_positive_int(
        "DATACOOLIE_STUDIO_ANALYTICS_UPGRADE_BATCH_MAX_FILES",
        DEFAULT_ANALYTICS_UPGRADE_BATCH_MAX_FILES,
        maximum=1024,
    )


def analytics_upgrade_batch_max_bytes() -> int:
    return _bounded_positive_int(
        "DATACOOLIE_STUDIO_ANALYTICS_UPGRADE_BATCH_MAX_BYTES",
        DEFAULT_ANALYTICS_UPGRADE_BATCH_MAX_BYTES,
        maximum=4 * 1024 * 1024 * 1024,
    )


def analytics_upgrade_max_inflight_bytes() -> int:
    return _bounded_positive_int(
        "DATACOOLIE_STUDIO_ANALYTICS_UPGRADE_MAX_INFLIGHT_BYTES",
        DEFAULT_ANALYTICS_UPGRADE_MAX_INFLIGHT_BYTES,
        maximum=16 * 1024 * 1024 * 1024,
    )


def analytics_upgrade_build_concurrency() -> int:
    return _bounded_positive_int(
        "DATACOOLIE_STUDIO_ANALYTICS_UPGRADE_BUILD_CONCURRENCY",
        DEFAULT_ANALYTICS_UPGRADE_BUILD_CONCURRENCY,
        maximum=MAX_ANALYTICS_UPGRADE_BUILD_CONCURRENCY,
    )


def _bounded_positive_int(name: str, default: int, *, maximum: int | None) -> int:
    configured = os.environ.get(name)
    if configured is None or not configured.strip():
        return default
    try:
        value = int(configured)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return min(value, maximum) if maximum is not None else value


def _default_analytics_duckdb_memory_limit() -> str:
    memory = available_memory_bytes()
    if memory is None:
        return "1GB"
    target = max(1, memory * 7 // 10)
    mebibyte = 1024 * 1024
    return f"{target // mebibyte}MiB" if target >= mebibyte else f"{target}B"


def result_cache_database_path() -> Path:
    return cache_dir() / "read-models.sqlite3"


def result_cache_url() -> str:
    configured = os.environ.get("DATACOOLIE_STUDIO_RESULT_CACHE_URL")
    if configured:
        if configured == "memory://":
            return "sqlite://"
        if not configured.startswith("sqlite:"):
            scheme = configured.split(":", 1)[0] or "unknown"
            raise ValueError(f"Unsupported result cache URL scheme: {scheme}")
        return configured
    path = result_cache_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path.as_posix()}"


def logs_dir() -> Path:
    return data_dir() / "logs"


def database_path() -> Path:
    return default_database_path()


def database_url() -> str:
    configured_url = os.environ.get("DATACOOLIE_STUDIO_DATABASE_URL")
    if configured_url:
        return validate_workspace_database_url(configured_url)
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path.as_posix()}"


def validate_workspace_database_url(value: str | URL) -> str:
    """Validate the supported SQLAlchemy URL for Studio-owned workspace state."""

    try:
        url = make_url(value) if isinstance(value, str) else value
    except ArgumentError as exc:
        raise ValueError("Invalid Studio workspace database URL") from exc
    if url.get_backend_name() != "sqlite" or url.get_driver_name() != "pysqlite":
        raise ValueError(
            "Unsupported Studio workspace database URL: use a synchronous SQLite "
            "URL such as sqlite:///path/to/studio.db"
        )
    return str(url)
