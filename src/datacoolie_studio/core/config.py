from __future__ import annotations

import os
import shutil
from pathlib import Path


def default_config_dir() -> Path:
    return Path.home() / ".datacoolie" / "datacoolie-studio"


def default_db_dir() -> Path:
    return default_config_dir() / "db"


def default_database_path() -> Path:
    return default_db_dir() / "studio.db"


def legacy_default_database_path() -> Path:
    return default_config_dir() / "studio.db"


def backup_dir() -> Path:
    return default_config_dir() / "backups"


def cache_dir() -> Path:
    return default_config_dir() / "cache"


def analytics_database_path() -> Path:
    return cache_dir() / "analytics.duckdb"


def result_cache_database_path() -> Path:
    configured_db = os.environ.get("DATACOOLIE_STUDIO_DB")
    if configured_db:
        return Path(configured_db).expanduser().parent / "read-models.sqlite3"
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
    return default_config_dir() / "logs"


def database_path() -> Path:
    configured = os.environ.get("DATACOOLIE_STUDIO_DB")
    if configured:
        return Path(configured).expanduser()
    _relocate_legacy_default_database()
    return default_database_path()


def database_url() -> str:
    configured_url = os.environ.get("DATACOOLIE_STUDIO_DATABASE_URL")
    if configured_url:
        return configured_url
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path.as_posix()}"


def _relocate_legacy_default_database() -> None:
    legacy_path = legacy_default_database_path()
    target_path = default_database_path()
    if target_path.exists() or not legacy_path.exists():
        return
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(legacy_path), str(target_path))
    for suffix in ("-wal", "-shm"):
        legacy_sidecar = Path(f"{legacy_path}{suffix}")
        if legacy_sidecar.exists():
            shutil.move(str(legacy_sidecar), str(f"{target_path}{suffix}"))
