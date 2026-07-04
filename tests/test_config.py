from __future__ import annotations

from pathlib import Path

from datacoolie_studio.core.config import database_url, default_config_dir, default_database_path, default_db_dir


def test_default_database_path_uses_datacoolie_studio_db_dir():
    expected_dir = Path.home() / ".datacoolie" / "datacoolie-studio"

    assert default_config_dir() == expected_dir
    assert default_db_dir() == expected_dir / "db"
    assert default_database_path() == expected_dir / "db" / "studio.db"


def test_database_url_env_overrides_sqlite_path(monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", "postgresql+psycopg://user:pass@example/db")
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", "ignored.db")

    assert database_url() == "postgresql+psycopg://user:pass@example/db"
