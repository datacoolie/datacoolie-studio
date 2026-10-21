from __future__ import annotations

from pathlib import Path

from tests.support.monitoring_fixture import build_analytics_fixture

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.analytics import access as analytics_access
from datacoolie_studio.domains.logs import ingestion as log_ingestion


class PathRecord:
    def __init__(self, uri: str, enabled: bool = True) -> None:
        self.uri = uri
        self.enabled = enabled


def _published_monitoring_paths(tmp_path: Path, monkeypatch) -> list[EnvironmentSource]:
    from datacoolie_studio.db.session import _dispose_engine, init_db
    _dispose_engine()
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path / "studio-data"))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")
    init_db()
    analytics_path = tmp_path / "monitoring.duckdb"
    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_access, "analytics_database_path", lambda: analytics_path)
    build_analytics_fixture(analytics_path, source_ids=[1], dataflow_rows=50)
    return [EnvironmentSource(
        id=1,
        environment_id=1,
        source_kind="logs",
        uri="benchmark://monitoring-tests/1",
        enabled=True,
    )]


def _normalize_duckdb_type(value: str) -> str:
    return value.upper().replace("TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE")
