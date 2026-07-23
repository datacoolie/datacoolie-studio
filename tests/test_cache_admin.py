from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

from datacoolie_studio.domains.read_models.contracts import ResultCacheKey
from datacoolie_studio.domains.read_models.database import reset_result_cache_engine
from datacoolie_studio.domains.read_models.sqlite_store import SqliteResultCacheStore


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_LOGS = ROOT / "datacoolie" / "usecase-sim" / "logs" / "etl_logs" / "analyst"


def _key(environment_id: int, namespace: str) -> ResultCacheKey:
    return ResultCacheKey(
        environment_id=environment_id,
        namespace=namespace,
        parameters_fingerprint=f"parameters-{namespace}",
        input_fingerprint="input-v1",
        producer_version="producer-v1",
    )


def test_cache_admin_is_scoped_and_preserves_core_state(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))
    monkeypatch.delenv("DATACOOLIE_STUDIO_RESULT_CACHE_URL", raising=False)
    reset_result_cache_engine()

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "cache-admin"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        store = SqliteResultCacheStore()
        store.put(_key(environment["id"], "environment-overview"), {"value": "overview"})
        store.put(_key(environment["id"], "assets.catalog"), {"value": "assets"})
        store.put(_key(environment["id"], "monitoring.page.health"), {"value": "monitoring"})

        status = client.get("/api/v1/studio/cache")
        assert status.status_code == 200
        assert status.json()["result_cache"]["entries"] == 3
        assert status.json()["result_cache"]["backend"] == "sqlite"

        assert client.post(
            "/api/v1/studio/cache/clear",
            json={"scope": "read_models"},
        ).status_code == 422

        cleared = client.post(
            "/api/v1/studio/cache/clear",
            json={
                "scope": "read_models",
                "environment_id": environment["id"],
                "features": ["assets"],
                "confirm": True,
            },
        )
        assert cleared.status_code == 200
        assert cleared.json()["read_models"]["deleted_entries"] == 1
        assert store.get(_key(environment["id"], "assets.catalog")) is None
        assert store.get(_key(environment["id"], "environment-overview")) is not None

        cleared_all = client.post(
            "/api/v1/studio/cache/clear",
            json={"scope": "read_models", "confirm": True},
        )
        assert cleared_all.status_code == 200
        assert client.get("/api/v1/studio/cache").json()["result_cache"]["entries"] == 0
        assert client.get("/api/v1/projects").json()[0]["id"] == project["id"]

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type = 'table'"
            )
        }
        assert "environment_read_model_cache_entries" not in tables
        assert "projects" in tables


def test_cache_admin_rejects_unknown_environment(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))
    monkeypatch.delenv("DATACOOLIE_STUDIO_RESULT_CACHE_URL", raising=False)
    reset_result_cache_engine()

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/studio/cache/clear",
            json={
                "scope": "read_models",
                "environment_id": 999,
                "confirm": True,
            },
        )
    assert response.status_code == 404


def test_cleared_analytics_rebuilds_from_unchanged_manifest(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))
    monkeypatch.delenv("DATACOOLIE_STUDIO_RESULT_CACHE_URL", raising=False)
    reset_result_cache_engine()

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.analytics import access as analytics_access
    from datacoolie_studio.domains.analytics import maintenance as analytics_maintenance
    from datacoolie_studio.domains.logs import cache as logs_cache
    from datacoolie_studio.main import app

    monkeypatch.setattr(logs_cache, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(
        analytics_access,
        "analytics_database_path",
        lambda: analytics_path,
    )
    monkeypatch.setattr(
        analytics_maintenance,
        "analytics_database_path",
        lambda: analytics_path,
    )
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "analytics-rebuild"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        source = client.post(
            f"/api/v1/environments/{environment['id']}/log-sources",
            json={"uri": str(SAMPLE_LOGS), "label": "sample"},
        ).json()
        first_refresh = client.post(
            f"/api/v1/environments/{environment['id']}/log-sources/{source['id']}/refresh",
            json={"mode": "incremental"},
        )
        assert first_refresh.status_code == 200
        first_rows = client.get("/api/v1/studio/cache").json()["analytics_cache"]["job_rows"]
        assert first_rows > 0
        import duckdb

        with duckdb.connect(str(analytics_path), read_only=True) as connection:
            first_generation = connection.execute(
                "select generation from etl_analytics_meta where singleton_id = 1"
            ).fetchone()[0]
        with duckdb.connect(str(analytics_path)) as connection:
            connection.execute("alter table etl_job_runs drop column __event_time")
            connection.execute("update etl_analytics_meta set schema_version = 1 where singleton_id = 1")

        overview = client.get(f"/api/v1/environments/{environment['id']}/overview")
        filter_options = client.get(
            f"/api/v1/environments/{environment['id']}/monitoring/filter-options"
        )
        assert overview.status_code == 200
        assert filter_options.status_code == 200
        assert overview.json()["monitoring"]["job_records"] == 0
        assert overview.json()["monitoring"]["errors"] == []
        assert filter_options.json()["options"] == {}

        schema_refresh = client.post(
            f"/api/v1/environments/{environment['id']}/log-sources/{source['id']}/refresh",
            json={"mode": "incremental"},
        )
        assert schema_refresh.status_code == 200
        assert client.get(f"/api/v1/environments/{environment['id']}/overview").status_code == 200
        assert client.get(
            f"/api/v1/environments/{environment['id']}/monitoring/filter-options"
        ).status_code == 200

        unchanged_refresh = client.post(
            f"/api/v1/environments/{environment['id']}/log-sources/{source['id']}/refresh",
            json={"mode": "incremental"},
        )
        assert unchanged_refresh.status_code == 200
        with duckdb.connect(str(analytics_path), read_only=True) as connection:
            assert connection.execute(
                "select generation from etl_analytics_meta where singleton_id = 1"
            ).fetchone()[0] == first_generation
        with sqlite3.connect(db_path) as connection:
            manifest_count = connection.execute(
                "select count(*) from log_file_manifest where source_id = ?",
                (source["id"],),
            ).fetchone()[0]

        cleared = client.post(
            "/api/v1/studio/cache/clear",
            json={"scope": "analytics", "confirm": True},
        )
        assert cleared.status_code == 200
        assert client.get("/api/v1/studio/cache").json()["analytics_cache"]["job_rows"] == 0
        with sqlite3.connect(db_path) as connection:
            assert connection.execute(
                "select count(*) from log_file_manifest where source_id = ?",
                (source["id"],),
            ).fetchone()[0] == manifest_count

        from datacoolie_studio.domains.monitoring import service as monitoring_service

        def reject_raw_reader(*_args, **_kwargs):
            raise AssertionError("Monitoring requests must not parse raw logs after Analytics Clear")

        monkeypatch.setattr(monitoring_service, "read_dataflow_logs", reject_raw_reader)
        monkeypatch.setattr(monitoring_service, "read_job_logs", reject_raw_reader)
        unavailable = client.get(
            f"/api/v1/environments/{environment['id']}/monitoring/pages/overview"
        )
        assert unavailable.status_code == 200
        assert unavailable.json()["summary"]["job_records"] == 0
        assert unavailable.json()["summary"]["dataflow_records"] == 0

        second_refresh = client.post(
            f"/api/v1/environments/{environment['id']}/log-sources/{source['id']}/refresh",
            json={"mode": "incremental"},
        )
        assert second_refresh.status_code == 200
        rebuilt_rows = client.get("/api/v1/studio/cache").json()["analytics_cache"]["job_rows"]
        assert rebuilt_rows == first_rows


def test_reenabled_log_source_requires_fresh_analytics_publish(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))
    monkeypatch.delenv("DATACOOLIE_STUDIO_RESULT_CACHE_URL", raising=False)
    reset_result_cache_engine()

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.logs import cache as logs_cache
    from datacoolie_studio.main import app

    monkeypatch.setattr(logs_cache, "analytics_database_path", lambda: analytics_path)
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "source-lifecycle"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        source = client.post(
            f"/api/v1/environments/{environment['id']}/log-sources",
            json={"uri": str(SAMPLE_LOGS), "label": "sample"},
        ).json()
        source_url = f"/api/v1/environments/{environment['id']}/log-sources/{source['id']}"
        refresh_url = f"{source_url}/refresh"
        overview_url = f"/api/v1/environments/{environment['id']}/monitoring/pages/overview"

        assert client.post(refresh_url, json={"mode": "incremental"}).status_code == 200
        assert client.get(overview_url).status_code == 200
        with sqlite3.connect(db_path) as connection:
            manifest_count = connection.execute(
                "select count(*) from log_file_manifest where source_id = ?", (source["id"],)
            ).fetchone()[0]
            sync_job_count = connection.execute(
                "select count(*) from sync_jobs where source_id = ?", (source["id"],)
            ).fetchone()[0]
        assert client.patch(source_url, json={"enabled": False}).status_code == 200
        assert client.patch(source_url, json={"enabled": True}).status_code == 200
        with sqlite3.connect(db_path) as connection:
            assert connection.execute(
                "select count(*) from log_file_manifest where source_id = ?", (source["id"],)
            ).fetchone()[0] == manifest_count
            assert connection.execute(
                "select count(*) from sync_jobs where source_id = ?", (source["id"],)
            ).fetchone()[0] == sync_job_count

        unavailable = client.get(overview_url)
        assert unavailable.status_code == 200
        assert unavailable.json()["summary"]["job_records"] == 0
        assert unavailable.json()["summary"]["dataflow_records"] == 0

        assert client.post(refresh_url, json={"mode": "incremental"}).status_code == 200
        assert client.get(overview_url).status_code == 200


def test_analytics_clear_waits_for_active_reader(tmp_path: Path, monkeypatch):
    analytics_path = tmp_path / "analytics.duckdb"

    from datacoolie_studio.db.models import EnvironmentSource
    from datacoolie_studio.domains.analytics import access as analytics_access
    from datacoolie_studio.domains.analytics import maintenance as analytics_maintenance
    from datacoolie_studio.domains.logs import cache as logs_cache
    from datacoolie_studio.domains.monitoring.context import reader as analytics_reader

    monkeypatch.setattr(logs_cache, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(
        analytics_access,
        "analytics_database_path",
        lambda: analytics_path,
    )
    monkeypatch.setattr(
        analytics_maintenance,
        "analytics_database_path",
        lambda: analytics_path,
    )
    published = logs_cache._upsert_duckdb_rows(7, [], [], [], [])
    assert published["published"] is True
    source = EnvironmentSource(
        id=7,
        environment_id=1,
        source_kind="logs",
        uri=str(tmp_path),
        enabled=True,
    )
    reader_started = Event()
    reader_release = Event()
    clear_started = Event()

    def hold_reader() -> None:
        with analytics_reader([source]):
            reader_started.set()
            assert reader_release.wait(timeout=2)

    def clear_cache() -> dict[str, int]:
        clear_started.set()
        return analytics_maintenance.clear_cache()

    with ThreadPoolExecutor(max_workers=2) as executor:
        reader = executor.submit(hold_reader)
        assert reader_started.wait(timeout=2)
        clear = executor.submit(clear_cache)
        assert clear_started.wait(timeout=2)
        assert clear.done() is False
        reader_release.set()
        reader.result(timeout=2)
        result = clear.result(timeout=2)

    assert result["deleted_files"] >= 1
    assert analytics_path.exists() is False


def test_analytics_clear_removes_orphan_rebuild_candidate(tmp_path: Path, monkeypatch):
    from datacoolie_studio.domains.analytics import maintenance as analytics_maintenance

    analytics_path = tmp_path / "analytics.duckdb"
    candidate_path = tmp_path / "analytics.candidate.duckdb"
    candidate_path.write_bytes(b"orphaned candidate")
    monkeypatch.setattr(
        analytics_maintenance,
        "analytics_database_path",
        lambda: analytics_path,
    )

    result = analytics_maintenance.clear_cache()

    assert result["deleted_files"] == 1
    assert result["deleted_file_bytes"] == len(b"orphaned candidate")
    assert candidate_path.exists() is False
