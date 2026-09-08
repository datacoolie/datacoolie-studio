from __future__ import annotations

import os
from pathlib import Path


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


def cache_dir() -> Path:
    return data_dir() / "cache"


def source_materialization_cache_dir() -> Path:
    return cache_dir() / "source-materializations"


def analytics_database_path() -> Path:
    return cache_dir() / "analytics.duckdb"


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
        return configured_url
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path.as_posix()}"
