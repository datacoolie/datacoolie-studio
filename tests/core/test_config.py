from __future__ import annotations

from pathlib import Path

import pytest

from datacoolie_studio.core.config import (
    database_url,
    default_config_dir,
    default_database_path,
    default_db_dir,
    result_cache_database_path,
    result_cache_url,
)


def test_default_database_path_uses_datacoolie_studio_db_dir():
    expected_dir = Path.home() / ".datacoolie" / "datacoolie-studio"

    assert default_config_dir() == expected_dir
    assert default_db_dir() == expected_dir / "db"
    assert default_database_path() == expected_dir / "db" / "studio.db"


def test_database_url_env_overrides_sqlite_path(monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", "postgresql+psycopg://user:pass@example/db")
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", "ignored.db")

    assert database_url() == "postgresql+psycopg://user:pass@example/db"


def test_custom_workspace_db_keeps_default_result_cache_isolated(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))
    monkeypatch.delenv("DATACOOLIE_STUDIO_RESULT_CACHE_URL", raising=False)

    assert result_cache_database_path() == tmp_path / "read-models.sqlite3"
    assert result_cache_url() == f"sqlite:///{(tmp_path / 'read-models.sqlite3').as_posix()}"


def test_result_cache_rejects_unimplemented_redis_backend(monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_RESULT_CACHE_URL", "redis://localhost:6379/0")

    with pytest.raises(ValueError, match="Unsupported result cache URL scheme: redis"):
        result_cache_url()
