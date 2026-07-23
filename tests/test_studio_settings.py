from __future__ import annotations

import os
from types import SimpleNamespace
from pathlib import Path


def _empty_analytics_stats() -> dict:
    return {
        "path": "analytics.duckdb",
        "exists": False,
        "size_bytes": None,
        "scope": "studio",
        "dataflow_row_count": 0,
        "job_row_count": 0,
        "filter_value_count": 0,
        "cached_source_ids": [],
    }


def test_studio_settings_and_diagnostics_have_separate_read_costs(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.studio_settings import service as settings_service
    from datacoolie_studio.main import app

    analytics_calls = 0

    def analytics_stats():
        nonlocal analytics_calls
        analytics_calls += 1
        return _empty_analytics_stats()

    monkeypatch.setattr(
        settings_service.analytics_maintenance,
        "cache_stats",
        analytics_stats,
    )

    with TestClient(app) as client:
        current = client.get("/api/v1/studio/settings")
        assert current.status_code == 200
        assert current.json()["source_check_interval_seconds"] == 30
        assert isinstance(current.json()["timezone_offset_minutes"], int)
        assert "storage" not in current.json()
        assert analytics_calls == 0

        updated = client.patch(
            "/api/v1/studio/settings",
            json={"timezone": "Asia/Ho_Chi_Minh", "source_check_interval_seconds": 45},
        )
        assert updated.status_code == 200
        assert updated.json()["timezone"] == "Asia/Ho_Chi_Minh"
        assert updated.json()["source_check_interval_seconds"] == 45
        assert analytics_calls == 0

        diagnostics = client.get("/api/v1/studio/diagnostics")
        assert diagnostics.status_code == 200
        assert diagnostics.json()["workspace_database"]["path"]
        assert diagnostics.json()["workspace_database"]["backend"] == "sqlite"
        assert diagnostics.json()["workspace_database"]["maintenance_supported"] is True
        assert diagnostics.json()["analytics_cache"]["scope"] == "studio"
        assert analytics_calls == 1

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_workspace_database_compaction_is_confirmed_and_preserves_core_rows(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "preserved"})
        assert project.status_code == 200

        rejected = client.post("/api/v1/studio/workspace-database/compact", json={"confirm": False})
        assert rejected.status_code == 422

        compacted = client.post("/api/v1/studio/workspace-database/compact", json={"confirm": True})
        assert compacted.status_code == 200
        assert compacted.json()["backend"] == "sqlite"
        assert compacted.json()["maintenance_supported"] is True
        assert compacted.json()["path"] == str(db_path)

        projects = client.get("/api/v1/projects")
        assert projects.status_code == 200
        assert [item["name"] for item in projects.json()] == ["preserved"]

    monkeypatch.delenv("DATACOOLIE_STUDIO_DB", raising=False)


def test_workspace_database_compaction_rejects_non_sqlite(monkeypatch):
    from datacoolie_studio.domains.studio_settings import service as settings_service

    fake_engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    monkeypatch.setattr(settings_service, "get_engine", lambda: fake_engine)

    try:
        settings_service.compact_workspace_database()
    except ValueError as exc:
        assert str(exc) == "Workspace database compaction is available only for SQLite"
    else:
        raise AssertionError("Expected non-SQLite workspace compaction to be rejected")


def test_studio_settings_update_is_atomic_and_avoids_noop_commits(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        commit_count = 0
        original_commit = Session.commit

        def counted_commit(session):
            nonlocal commit_count
            commit_count += 1
            return original_commit(session)

        monkeypatch.setattr(Session, "commit", counted_commit)

        updated = client.patch(
            "/api/v1/studio/settings",
            json={"timezone": "Asia/Ho_Chi_Minh", "source_check_interval_seconds": 45},
        )
        assert updated.status_code == 200
        assert commit_count == 1

        unchanged = client.patch(
            "/api/v1/studio/settings",
            json={"timezone": "Asia/Ho_Chi_Minh", "source_check_interval_seconds": 45},
        )
        assert unchanged.status_code == 200
        assert commit_count == 1

        invalid_interval = client.patch(
            "/api/v1/studio/settings",
            json={"timezone": "UTC", "source_check_interval_seconds": 4},
        )
        assert invalid_interval.status_code == 422
        invalid_timezone = client.patch(
            "/api/v1/studio/settings",
            json={"timezone": "Invalid/Timezone", "source_check_interval_seconds": 60},
        )
        assert invalid_timezone.status_code == 422
        assert commit_count == 1

        persisted = client.get("/api/v1/studio/settings").json()
        assert persisted["timezone"] == "Asia/Ho_Chi_Minh"
        assert persisted["source_check_interval_seconds"] == 45

        reset = client.patch("/api/v1/studio/settings", json={"timezone": None})
        assert reset.status_code == 200
        assert reset.json()["timezone_source"] == "server_default"
        assert reset.json()["source_check_interval_seconds"] == 45
        assert commit_count == 2

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)
