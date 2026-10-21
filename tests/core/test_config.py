from __future__ import annotations

from pathlib import Path

import pytest

from datacoolie_studio.core.config import (
    analytics_database_path,
    analytics_duckdb_memory_limit,
    analytics_duckdb_temp_directory,
    analytics_duckdb_threads,
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


def test_database_url_rejects_non_sqlite_workspace_url(monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", "postgresql+psycopg://user:pass@example/db")

    with pytest.raises(ValueError, match="Unsupported Studio workspace database URL"):
        database_url()


def test_database_url_accepts_sqlite_driver_variants(monkeypatch, tmp_path: Path):
    url = f"sqlite+pysqlite:///{(tmp_path / 'studio.db').as_posix()}"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", url)

    assert database_url() == url


def test_database_url_rejects_async_sqlite_driver(monkeypatch, tmp_path: Path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'studio.db').as_posix()}"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", url)

    with pytest.raises(ValueError, match="Unsupported Studio workspace database URL"):
        database_url()


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


def test_analytics_resource_defaults_are_bounded_and_follow_data_dir(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_THREADS", raising=False)
    monkeypatch.delenv(
        "DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_MEMORY_LIMIT",
        raising=False,
    )
    monkeypatch.delenv(
        "DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_TEMP_DIRECTORY",
        raising=False,
    )

    assert analytics_duckdb_threads() >= 1
    assert analytics_duckdb_memory_limit().endswith(("MiB", "B", "GB"))
    assert analytics_duckdb_temp_directory() == tmp_path / "cache" / "analytics-tmp"


def test_analytics_resource_environment_values_are_validated(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_THREADS", "3")
    monkeypatch.setenv("DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_MEMORY_LIMIT", "512 MiB")
    monkeypatch.setenv(
        "DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_TEMP_DIRECTORY",
        str(tmp_path / "spill"),
    )

    assert analytics_duckdb_threads() == 3
    assert analytics_duckdb_memory_limit() == "512MiB"
    assert analytics_duckdb_temp_directory() == tmp_path / "spill"

    monkeypatch.setenv("DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_MEMORY_LIMIT", "75%")
    with pytest.raises(ValueError, match="DUCKDB_MEMORY_LIMIT"):
        analytics_duckdb_memory_limit()

    monkeypatch.setenv("DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_THREADS", "0")
    with pytest.raises(ValueError, match="DUCKDB_THREADS"):
        analytics_duckdb_threads()


@pytest.mark.parametrize('value', [None, '', 'auto', ' AUTO '])
def test_duckdb_auto_uses_current_allocation(monkeypatch, value):
    from datacoolie_studio.core import config

    if value is None:
        monkeypatch.delenv('DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_THREADS', raising=False)
        monkeypatch.delenv('DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_MEMORY_LIMIT', raising=False)
    else:
        monkeypatch.setenv('DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_THREADS', value)
        monkeypatch.setenv('DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_MEMORY_LIMIT', value)
    monkeypatch.setattr(config, 'available_cpu_count', lambda: 32)
    monkeypatch.setattr(config, 'available_memory_bytes', lambda: 64 * 1024**3)
    assert analytics_duckdb_threads() == 4
    assert analytics_duckdb_memory_limit() == '45875MiB'


def test_explicit_budget_has_no_old_caps_and_upgrade_workers_keep_separate_cap(monkeypatch):
    from datacoolie_studio.core import config

    monkeypatch.setenv('DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_THREADS', '32')
    monkeypatch.setenv('DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_MEMORY_LIMIT', '24GiB')
    monkeypatch.setenv('DATACOOLIE_STUDIO_ANALYTICS_UPGRADE_BUILD_CONCURRENCY', '32')
    assert analytics_duckdb_threads() == 32
    assert analytics_duckdb_memory_limit() == '24GiB'
    assert config.analytics_upgrade_build_concurrency() == 16


def test_small_memory_allocation_has_no_fixed_floor(monkeypatch):
    from datacoolie_studio.core import config

    monkeypatch.setattr(config, 'available_memory_bytes', lambda: 512 * 1024**2)
    assert config._default_analytics_duckdb_memory_limit() == '358MiB'
    monkeypatch.setattr(config, 'available_memory_bytes', lambda: 1024)
    assert config._default_analytics_duckdb_memory_limit() == '716B'
