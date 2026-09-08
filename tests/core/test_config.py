from __future__ import annotations

from pathlib import Path

import pytest

from datacoolie_studio.core.config import (
    analytics_database_path,
    backup_dir,
    cache_dir,
    data_dir,
    database_url,
    default_database_path,
    default_db_dir,
    logs_dir,
    result_cache_database_path,
    result_cache_url,
    source_materialization_cache_dir,
)


def test_default_database_path_uses_datacoolie_studio_data_dir(monkeypatch):
    monkeypatch.delenv("DATACOOLIE_STUDIO_DATA_DIR", raising=False)
    expected_dir = Path.home() / ".datacoolie" / "datacoolie-studio"

    assert data_dir() == expected_dir
    assert default_db_dir() == expected_dir / "db"
    assert default_database_path() == expected_dir / "db" / "studio.db"
    assert cache_dir() == expected_dir / "cache"
    assert analytics_database_path() == expected_dir / "cache" / "analytics.duckdb"
    assert source_materialization_cache_dir() == expected_dir / "cache" / "source-materializations"
    assert backup_dir() == expected_dir / "backups"
    assert logs_dir() == expected_dir / "logs"


def test_custom_data_dir_controls_derived_paths(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))

    assert data_dir() == tmp_path
    assert default_database_path() == tmp_path / "db" / "studio.db"
    assert result_cache_database_path() == tmp_path / "cache" / "read-models.sqlite3"
    assert analytics_database_path() == tmp_path / "cache" / "analytics.duckdb"
    assert source_materialization_cache_dir() == tmp_path / "cache" / "source-materializations"
    assert backup_dir() == tmp_path / "backups"
    assert logs_dir() == tmp_path / "logs"


def test_database_url_defaults_to_data_dir(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATACOOLIE_STUDIO_DATABASE_URL", raising=False)

    assert database_url() == f"sqlite:///{(tmp_path / 'db' / 'studio.db').as_posix()}"


def test_database_url_env_overrides_sqlite_path(monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", "postgresql+psycopg://user:pass@example/db")

    assert database_url() == "postgresql+psycopg://user:pass@example/db"


def test_result_cache_defaults_under_data_dir(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATACOOLIE_STUDIO_RESULT_CACHE_URL", raising=False)

    expected = tmp_path / "cache" / "read-models.sqlite3"
    assert result_cache_database_path() == expected
    assert result_cache_url() == f"sqlite:///{expected.as_posix()}"


def test_result_cache_accepts_sqlite_and_memory_urls(monkeypatch, tmp_path: Path):
    sqlite_path = tmp_path / "result-cache.sqlite3"
    monkeypatch.setenv("DATACOOLIE_STUDIO_RESULT_CACHE_URL", f"sqlite:///{sqlite_path.as_posix()}")
    assert result_cache_url() == f"sqlite:///{sqlite_path.as_posix()}"

    monkeypatch.setenv("DATACOOLIE_STUDIO_RESULT_CACHE_URL", "memory://")
    assert result_cache_url() == "sqlite://"


def test_result_cache_rejects_unimplemented_redis_backend(monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_RESULT_CACHE_URL", "redis://localhost:6379/0")

    with pytest.raises(ValueError, match="Unsupported result cache URL scheme: redis"):
        result_cache_url()
