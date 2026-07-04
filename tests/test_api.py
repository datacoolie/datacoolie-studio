from __future__ import annotations

import os
import sqlite3
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_LOGS = ROOT / "datacoolie" / "usecase-sim" / "logs" / "etl_logs" / "analyst"
SAMPLE_METADATA = ROOT / "datacoolie" / "usecase-sim" / "metadata" / "file" / "local_use_cases.json"


def _normalize_duckdb_type(value: str) -> str:
    return value.upper().replace("TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE")


def test_workspace_api_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        assert project["name"] == "demo"
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        assert env["name"] == "dev"
        source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": "metadata.json", "label": "metadata"},
        ).json()
        assert source["uri"] == "metadata.json"
        assert client.get("/api/projects").status_code == 404

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_source_management_api_roundtrip(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(SAMPLE_METADATA), "label": "metadata"},
        ).json()

        validation = client.post(f"/api/v1/metadata-sources/{source['id']}/validate").json()
        assert validation["status"] == "ok"
        assert validation["message"] == "Metadata source path is readable"
        assert validation["detected_format"] == "json"
        assert validation["record_counts"]["files"] == 1
        assert validation["validated_at"].endswith(("Z", "+00:00"))

        sources = client.get(f"/api/v1/environments/{env['id']}/metadata-sources").json()
        assert sources[0]["latest_validation"]["status"] == "ok"
        assert sources[0]["latest_validation"]["record_counts"]["files"] == 1
        assert sources[0]["latest_validation"]["validated_at"].endswith(("Z", "+00:00"))
        with sqlite3.connect(db_path) as connection:
            source_row = connection.execute(
                "select source_kind, read_check_status, read_check_result_json from environment_sources where id = ?",
                (source["id"],),
            ).fetchone()
            assert source_row[0] == "metadata"
            assert source_row[1] == "ok"
            assert "Metadata source path is readable" in source_row[2]
            tables = {
                row[0]
                for row in connection.execute(
                    "select name from sqlite_master where type = 'table'"
                ).fetchall()
            }
            assert "scan_runs" not in tables

        patched = client.patch(f"/api/v1/metadata-sources/{source['id']}", json={"enabled": False}).json()
        assert patched["enabled"] is False

        impact = client.get(f"/api/v1/metadata-sources/{source['id']}/delete-impact").json()
        assert impact["mode"] == "hard_delete"
        assert impact["metadata_file_deleted"] is False
        assert impact["has_impact"] is False
        assert impact["impacts"] == []

        response = client.delete(f"/api/v1/metadata-sources/{source['id']}")
        assert response.status_code == 204
        assert client.get(f"/api/v1/environments/{env['id']}/metadata-sources").json() == []

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_project_summary_api(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(SAMPLE_METADATA), "label": "metadata"},
        )
        client.post(
            f"/api/v1/environments/{env['id']}/etl-log-paths",
            json={"uri": str(SAMPLE_LOGS), "label": "sample logs"},
        )

        summaries = client.get("/api/v1/projects/summary").json()
        assert summaries[0]["name"] == "demo"
        assert summaries[0]["environment_count"] == 1
        assert summaries[0]["metadata_source_count"] == 1
        assert summaries[0]["etl_log_path_count"] == 1
        assert summaries[0]["environments"][0]["name"] == "dev"

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_studio_settings_timezone_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        current = client.get("/api/v1/studio/settings")
        assert current.status_code == 200
        current_payload = current.json()
        assert current_payload["timezone"]
        assert current_payload["timezone_source"] == "server_default"
        assert current_payload["storage"]["workspace_database"]["path"]
        assert current_payload["storage"]["analytics_cache"]["scope"] == "studio"
        assert "cached_source_count" in current_payload["storage"]["analytics_cache"]

        configured = client.patch("/api/v1/studio/settings", json={"timezone": "Asia/Ho_Chi_Minh"})
        assert configured.status_code == 200
        configured_payload = configured.json()
        assert configured_payload["timezone"] == "Asia/Ho_Chi_Minh"
        assert configured_payload["timezone_source"] == "configured"
        assert configured_payload["storage"]["analytics_cache"]["scope"] == "studio"

        invalid = client.patch("/api/v1/studio/settings", json={"timezone": "Invalid/Timezone"})
        assert invalid.status_code == 422

        reset = client.patch("/api/v1/studio/settings", json={"timezone": None})
        assert reset.status_code == 200
        reset_payload = reset.json()
        assert reset_payload["timezone_source"] == "server_default"
        assert reset_payload["timezone"]
        assert reset_payload["storage"]["workspace_database"]["path"]

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_source_refresh_records_revision_and_sync_job(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(SAMPLE_METADATA), "label": "metadata"},
        ).json()

        status = client.get(f"/api/v1/metadata-sources/{source['id']}/sync-status").json()
        assert status["status"] == "unknown"
        assert status["latest_job"] is None

        refreshed = client.post(f"/api/v1/metadata-sources/{source['id']}/refresh").json()
        assert refreshed["status"] == "ok"
        assert refreshed["message"] == "Metadata source cache refreshed"
        assert refreshed["revision"]["object_type"] == "file"
        assert refreshed["revision"]["content_hash"]
        assert refreshed["latest_job"]["status"] == "succeeded"
        assert refreshed["latest_job"]["job_type"] == "force_refresh"

        impact = client.get(f"/api/v1/metadata-sources/{source['id']}/delete-impact").json()
        assert impact["has_impact"] is True
        assert {item["kind"] for item in impact["impacts"]} == {"source_revision", "sync_job", "snapshot"}

        response = client.delete(f"/api/v1/metadata-sources/{source['id']}")
        assert response.status_code == 204
        with sqlite3.connect(db_path) as connection:
            assert connection.execute("select count(*) from source_revisions").fetchone()[0] == 0
            assert connection.execute("select count(*) from sync_jobs").fetchone()[0] == 0
            assert connection.execute("select count(*) from metadata_source_snapshots").fetchone()[0] == 0

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_source_fast_stat_omits_content_hash(tmp_path: Path):
    from datacoolie_studio.db.models import EnvironmentSource
    from datacoolie_studio.domains.sync.service import stat_source

    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text('{"connections": [], "dataflows": [], "schema_hints": []}', encoding="utf-8")
    source = EnvironmentSource(environment_id=1, source_kind="metadata", uri=str(metadata_path), enabled=True)

    fast = stat_source(source, include_content_hash=False)
    full = stat_source(source)

    assert fast["object_type"] == "file"
    assert "content_hash" not in fast
    assert full["content_hash"]


def test_environment_freshness_reports_max_source_modified_and_cache_status(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    metadata_old = tmp_path / "metadata_old.json"
    metadata_new = tmp_path / "metadata_new.json"
    logs_dir = tmp_path / "etl_logs"
    logs_dir.mkdir()
    log_old = logs_dir / "old.job.jsonl"
    log_new = logs_dir / "new.job.jsonl"
    for path in (metadata_old, metadata_new):
        path.write_text('{"connections": [], "dataflows": [], "schema_hints": []}', encoding="utf-8")
    log_old.write_text("{}", encoding="utf-8")
    log_new.write_text("{}", encoding="utf-8")
    os.utime(metadata_old, (1_700_000_000, 1_700_000_000))
    os.utime(metadata_new, (1_700_000_300, 1_700_000_300))
    os.utime(log_old, (1_700_000_100, 1_700_000_100))
    os.utime(log_new, (1_700_000_600, 1_700_000_600))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        first_source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(metadata_old), "label": "old"},
        ).json()
        client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(metadata_new), "label": "new"},
        )
        client.post(
            f"/api/v1/environments/{env['id']}/etl-log-paths",
            json={"uri": str(logs_dir), "label": "logs"},
        )

        freshness = client.get(f"/api/v1/environments/{env['id']}/freshness").json()
        assert freshness["metadata_source_count"] == 2
        assert freshness["etl_log_path_count"] == 1
        assert freshness["status"] == "not_cached"
        assert freshness["metadata"]["max_source_modified_at"].startswith("2023-11-14T22:18:20")
        assert freshness["metadata"]["max_source_modified_at"].endswith(("+00:00", "Z"))
        assert freshness["etl_logs"]["max_source_modified_at"].startswith("2023-11-14T22:23:20")
        assert freshness["etl_logs"]["max_source_modified_at"].endswith(("+00:00", "Z"))
        assert freshness["max_source_modified_at"].startswith("2023-11-14T22:23:20")

        client.post(f"/api/v1/metadata-sources/{first_source['id']}/refresh")
        source_freshness = client.get(f"/api/v1/environments/{env['id']}/freshness").json()
        first_item = next(item for item in source_freshness["items"] if item["source_id"] == first_source["id"])
        assert first_item["status"] == "current"

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_metadata_api_uses_snapshot_cache_and_auto_refreshes_stale_source(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        """
        {
          "connections": [{"name": "lake"}],
          "dataflows": [{"name": "flow_v1", "destination": {"table": "target_v1"}}],
          "schema_hints": []
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.metadata import service as metadata_service
    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(metadata_path), "label": "metadata"},
        ).json()

        first = client.get(f"/api/v1/environments/{env['id']}/metadata").json()
        assert first["summary"]["dataflows"] == 1
        assert first["dataflows"][0]["name"] == "flow_v1"
        with sqlite3.connect(db_path) as connection:
            assert connection.execute("select count(*) from metadata_source_snapshots").fetchone()[0] == 1
            assert connection.execute("select count(*) from sync_jobs").fetchone()[0] == 1

        original_read_metadata_file = metadata_service.read_metadata_file

        def fail_if_parsed(_uri: str):
            raise AssertionError("metadata file should not be parsed on cache hit")

        monkeypatch.setattr(metadata_service, "read_metadata_file", fail_if_parsed)
        second = client.get(f"/api/v1/environments/{env['id']}/metadata").json()
        assert second["dataflows"][0]["name"] == "flow_v1"
        with sqlite3.connect(db_path) as connection:
            assert connection.execute("select count(*) from metadata_source_snapshots").fetchone()[0] == 1
            assert connection.execute("select count(*) from sync_jobs").fetchone()[0] == 1

        monkeypatch.setattr(metadata_service, "read_metadata_file", original_read_metadata_file)

        metadata_path.write_text(
            """
            {
              "connections": [{"name": "lake"}],
              "dataflows": [{"name": "flow_v2", "destination": {"table": "target_v2"}}],
              "schema_hints": []
            }
            """,
            encoding="utf-8",
        )
        stale = client.get(f"/api/v1/environments/{env['id']}/metadata").json()
        assert stale["dataflows"][0]["name"] == "flow_v2"
        with sqlite3.connect(db_path) as connection:
            assert connection.execute("select count(*) from metadata_source_snapshots").fetchone()[0] == 2
            assert connection.execute("select count(*) from sync_jobs").fetchone()[0] == 2

        metadata_path.unlink()
        last_good = client.get(f"/api/v1/environments/{env['id']}/metadata").json()
        assert last_good["dataflows"][0]["name"] == "flow_v2"
        assert last_good["summary"]["errors"] == 1
        assert last_good["errors"][0]["cache_status"] == "stale"
        status = client.get(f"/api/v1/metadata-sources/{source['id']}/sync-status").json()
        assert status["status"] == "error"

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_source_refresh_reports_missing_path_without_crashing(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(tmp_path / "missing.json"), "label": "missing"},
        ).json()

        refreshed = client.post(f"/api/v1/metadata-sources/{source['id']}/refresh").json()
        assert refreshed["status"] == "error"
        assert refreshed["error"]["code"] == "not_found"
        assert refreshed["latest_job"]["status"] == "failed"

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_source_uri_update_clears_read_check_and_sync_status(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(SAMPLE_METADATA), "label": "metadata"},
        ).json()
        client.post(f"/api/v1/metadata-sources/{source['id']}/validate")
        client.post(f"/api/v1/metadata-sources/{source['id']}/refresh")

        updated = client.patch(
            f"/api/v1/metadata-sources/{source['id']}",
            json={"uri": str(tmp_path / "other.json")},
        ).json()
        assert updated["latest_validation"] is None
        status = client.get(f"/api/v1/metadata-sources/{source['id']}/sync-status").json()
        assert status["status"] == "unknown"
        assert status["latest_job"] is None

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_source_schedule_config_and_due_scheduler_refresh(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps({"connections": [{"name": "lake"}], "dataflows": [], "schema_hints": []}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.sync.scheduler import run_due_schedules_once
    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(metadata_path), "label": "metadata"},
        ).json()

        patched = client.patch(
            f"/api/v1/metadata-sources/{source['id']}",
            json={"sync_schedule_enabled": True, "sync_interval_minutes": 15},
        ).json()
        assert patched["sync_schedule_enabled"] is True
        assert patched["sync_interval_minutes"] == 15

        assert run_due_schedules_once() == 1
        status = client.get(f"/api/v1/metadata-sources/{source['id']}/sync-status").json()
        assert status["status"] == "ok"
        with sqlite3.connect(db_path) as connection:
            assert connection.execute("select count(*) from metadata_source_snapshots").fetchone()[0] == 1
            row = connection.execute(
                "select last_scheduled_sync_at from environment_sources where id = ?",
                (source["id"],),
            ).fetchone()
            assert row[0] is not None

        assert run_due_schedules_once() == 0

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_etl_log_path_refresh_records_directory_revision(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.logs import cache as logs_cache
    from datacoolie_studio.main import app

    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setattr(logs_cache, "analytics_database_path", lambda: analytics_path)

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        path = client.post(
            f"/api/v1/environments/{env['id']}/etl-log-paths",
            json={"uri": str(SAMPLE_LOGS), "label": "sample logs"},
        ).json()

        refreshed = client.post(f"/api/v1/etl-log-paths/{path['id']}/refresh").json()
        assert refreshed["status"] == "ok"
        assert refreshed["revision"]["object_type"] == "directory"
        assert refreshed["revision"]["file_count"] > 0
        assert refreshed["latest_job"]["status"] == "succeeded"
        assert refreshed["latest_job"]["message"] == "ETL log cache refreshed"
        assert analytics_path.exists()
        with sqlite3.connect(tmp_path / "studio.db") as connection:
            assert connection.execute("select count(*) from etl_log_file_manifest").fetchone()[0] > 0
        import duckdb

        with duckdb.connect(str(analytics_path), read_only=True) as connection:
            tables = {row[0] for row in connection.execute("show tables").fetchall()}
            assert {"etl_job_runs", "etl_dataflow_runs", "etl_monitoring_filter_values"} <= tables
            job_columns = {
                str(row[1]): str(row[2]).upper()
                for row in connection.execute("PRAGMA table_info('etl_job_runs')").fetchall()
            }
            dataflow_columns = {
                str(row[1]): _normalize_duckdb_type(str(row[2]))
                for row in connection.execute("PRAGMA table_info('etl_dataflow_runs')").fetchall()
            }
            sample_dataflow_file = str(next(SAMPLE_LOGS.rglob("*.parquet"))).replace("\\", "/").replace("'", "''")
            source_types = {
                str(row[0]): _normalize_duckdb_type(str(row[1]))
                for row in connection.execute(f"DESCRIBE SELECT * FROM read_parquet('{sample_dataflow_file}', union_by_name=true)").fetchall()
            }
            assert {"job_id", "status", "duration_seconds", "_source_id", "_file_uri"} <= set(job_columns)
            assert {"dataflow_id", "stage", "source_id", "status", "_source_id", "_file_uri"} <= set(dataflow_columns)
            assert "_raw_json" not in job_columns
            assert "_raw_json" not in dataflow_columns
            assert job_columns["start_time"] == "VARCHAR"
            assert dataflow_columns["start_time"] == source_types["start_time"]
            assert dataflow_columns["source_end_time"] == source_types["source_end_time"]
            assert "source_id" in dataflow_columns
            assert "_source_id" in dataflow_columns
            assert connection.execute("select count(*) from etl_job_runs").fetchone()[0] > 0
            assert connection.execute("select count(*) from etl_dataflow_runs").fetchone()[0] > 0
            assert connection.execute(
                "select count(*) from etl_monitoring_filter_values where field = 'operation_type'"
            ).fetchone()[0] > 0

        report = client.get(f"/api/v1/environments/{env['id']}/monitoring/overview").json()
        assert report["summary"]["dataflow_records"] > 0
        assert report["summary"]["job_records"] > 0
        assert report["summary"]["requested_grain"] == "auto"
        assert report["summary"]["effective_grain"] in {"hour", "day", "week", "month"}
        assert report["diagnostics"]["kpis"]["matched_job_ids"] > 0
        assert "freshness" in report
        filter_options = client.get(f"/api/v1/environments/{env['id']}/monitoring/filter-options").json()
        assert filter_options["summary"]["source"] == "duckdb_filter_values"
        assert filter_options["options"]["operation_type"]
        filtered_report = client.get(f"/api/v1/environments/{env['id']}/monitoring/overview?status=failed").json()
        assert filtered_report["summary"]["dataflow_records"] < report["summary"]["dataflow_records"]
        assert filtered_report["operations"]["dataflow_kpis"]["failed"] == filtered_report["summary"]["dataflow_records"]
        assert filtered_report["operations"]["dataflow_kpis"]["skipped"] == 0
        last_24h = client.get(f"/api/v1/environments/{env['id']}/monitoring/overview?range=24h").json()
        assert last_24h["summary"]["dataflow_records"] <= report["summary"]["dataflow_records"]
        last_3d = client.get(f"/api/v1/environments/{env['id']}/monitoring/overview?range=3d").json()
        assert last_3d["summary"]["dataflow_records"] <= report["summary"]["dataflow_records"]
        custom = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/overview?range=custom&startTime=1900-01-01T00:00:00Z&endTime=1900-01-02T00:00:00Z"
        ).json()
        assert custom["summary"]["dataflow_records"] == 0
        dataflows = client.get(f"/api/v1/environments/{env['id']}/monitoring/dataflows?limit=5").json()
        assert dataflows["summary"]["cache"] == "duckdb"
        assert dataflows["summary"]["records"] == 5
        assert dataflows["summary"]["total_records"] == report["summary"]["dataflow_records"]
        assert {"source_display", "destination_display", "phase_health", "movement_state"} <= set(dataflows["records"][0])
        assert dataflows["records"][0]["start_time"] >= dataflows["records"][-1]["start_time"]
        last_3d_dataflows = client.get(f"/api/v1/environments/{env['id']}/monitoring/dataflows?limit=5&range=3d").json()
        assert last_3d_dataflows["summary"]["total_records"] <= dataflows["summary"]["total_records"]
        failed_dataflows = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/dataflows?limit=5&status=failed"
        ).json()
        assert failed_dataflows["summary"]["cache"] == "duckdb"
        assert failed_dataflows["summary"]["total_records"] >= 0
        if failed_dataflows["records"]:
            assert {row["status"] for row in failed_dataflows["records"]} == {"failed"}
        slow_dataflows = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/dataflows?limit=5&sortBy=duration_seconds&sortDir=desc"
        ).json()
        fast_dataflows = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/dataflows?limit=5&sortBy=duration_seconds&sortDir=asc"
        ).json()
        assert slow_dataflows["records"][0]["duration_seconds"] >= slow_dataflows["records"][-1]["duration_seconds"]
        assert fast_dataflows["records"][0]["duration_seconds"] <= fast_dataflows["records"][-1]["duration_seconds"]
        jobs = client.get(f"/api/v1/environments/{env['id']}/monitoring/jobs?limit=3&offset=2").json()
        assert jobs["summary"]["cache"] == "duckdb"
        assert jobs["summary"]["records"] == 3
        assert jobs["summary"]["offset"] == 2
        assert jobs["summary"]["total_records"] == report["summary"]["job_records"]
        assert {"child_dataflow_count", "child_failed_count", "reconciliation_status", "error_preview"} <= set(jobs["records"][0])
        assert jobs["records"][0]["start_time"] >= jobs["records"][-1]["start_time"]
        last_3d_jobs = client.get(f"/api/v1/environments/{env['id']}/monitoring/jobs?limit=3&range=3d").json()
        assert last_3d_jobs["summary"]["total_records"] <= jobs["summary"]["total_records"]
        longest_jobs = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/jobs?limit=5&sortBy=duration_seconds&sortDir=desc"
        ).json()
        assert longest_jobs["records"][0]["duration_seconds"] >= longest_jobs["records"][-1]["duration_seconds"]

        second_refresh = client.post(f"/api/v1/etl-log-paths/{path['id']}/refresh").json()
        assert second_refresh["latest_job"]["message"] == "ETL log cache is current"
        assert second_refresh["latest_job"]["result"]["record_counts"]["parsed_files"] == 0

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_delete_etl_log_path_purges_duckdb_cache(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.logs import cache as logs_cache
    from datacoolie_studio.main import app

    monkeypatch.setattr(logs_cache, "analytics_database_path", lambda: analytics_path)

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        path = client.post(
            f"/api/v1/environments/{env['id']}/etl-log-paths",
            json={"uri": str(SAMPLE_LOGS), "label": "sample logs"},
        ).json()
        source_id = int(path["id"])

        refreshed = client.post(f"/api/v1/etl-log-paths/{source_id}/refresh").json()
        assert refreshed["status"] == "ok"

        import duckdb

        with duckdb.connect(str(analytics_path), read_only=True) as connection:
            assert connection.execute(
                "select count(*) from etl_dataflow_runs where _source_id = ?",
                [source_id],
            ).fetchone()[0] > 0

        deleted = client.delete(f"/api/v1/etl-log-paths/{source_id}")
        assert deleted.status_code == 204

        with duckdb.connect(str(analytics_path), read_only=True) as connection:
            assert connection.execute(
                "select count(*) from etl_dataflow_runs where _source_id = ?",
                [source_id],
            ).fetchone()[0] == 0
            assert connection.execute(
                "select count(*) from etl_job_runs where _source_id = ?",
                [source_id],
            ).fetchone()[0] == 0
            assert connection.execute(
                "select count(*) from etl_monitoring_filter_values where _source_id = ?",
                [source_id],
            ).fetchone()[0] == 0

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_delete_project_purges_duckdb_cache(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.logs import cache as logs_cache
    from datacoolie_studio.main import app

    monkeypatch.setattr(logs_cache, "analytics_database_path", lambda: analytics_path)

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        path = client.post(
            f"/api/v1/environments/{env['id']}/etl-log-paths",
            json={"uri": str(SAMPLE_LOGS), "label": "sample logs"},
        ).json()
        source_id = int(path["id"])

        refreshed = client.post(f"/api/v1/etl-log-paths/{source_id}/refresh").json()
        assert refreshed["status"] == "ok"

        import duckdb

        with duckdb.connect(str(analytics_path), read_only=True) as connection:
            assert connection.execute(
                "select count(*) from etl_dataflow_runs where _source_id = ?",
                [source_id],
            ).fetchone()[0] > 0

        deleted = client.delete(f"/api/v1/projects/{project['id']}")
        assert deleted.status_code == 204

        with duckdb.connect(str(analytics_path), read_only=True) as connection:
            assert connection.execute(
                "select count(*) from etl_dataflow_runs where _source_id = ?",
                [source_id],
            ).fetchone()[0] == 0
            assert connection.execute(
                "select count(*) from etl_job_runs where _source_id = ?",
                [source_id],
            ).fetchone()[0] == 0
            assert connection.execute(
                "select count(*) from etl_monitoring_filter_values where _source_id = ?",
                [source_id],
            ).fetchone()[0] == 0

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_environment_api_accepts_custom_name_and_rejects_invalid_name(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        custom = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "UAT-1"}).json()
        assert custom["name"] == "uat-1"
        duplicate = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "uat-1"})
        assert duplicate.status_code == 409
        invalid = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "uat env"})
        assert invalid.status_code == 422

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_monitoring_report_api_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        client.post(
            f"/api/v1/environments/{env['id']}/etl-log-paths",
            json={"uri": str(SAMPLE_LOGS), "label": "sample logs"},
        )
        paths = client.get(f"/api/v1/environments/{env['id']}/etl-log-paths").json()
        validation = client.post(f"/api/v1/etl-log-paths/{paths[0]['id']}/validate").json()
        assert validation["status"] == "ok"
        assert validation["record_counts"]["job_jsonl_files"] > 0
        paths = client.get(f"/api/v1/environments/{env['id']}/etl-log-paths").json()
        assert paths[0]["latest_validation"]["record_counts"]["job_jsonl_files"] > 0

        report = client.get(f"/api/v1/environments/{env['id']}/monitoring/overview").json()
        assert report["summary"]["dataflow_records"] > 0
        assert report["summary"]["job_records"] > 0
        assert report["summary"]["requested_grain"] == "auto"
        assert report["summary"]["effective_grain"] in {"hour", "day", "week", "month"}
        assert report["summary"]["latest_log_at"].endswith(("+00:00", "Z"))
        assert report["summary"]["latest_job_log_at"].endswith(("+00:00", "Z"))
        assert report["summary"]["latest_dataflow_log_at"].endswith(("+00:00", "Z"))
        assert report["operations"]["kpis"]["total_jobs"] > 0
        assert report["operations"]["dataflows_by_date_status"]
        assert {"bucket", "bucket_start", "bucket_end", "grain"} <= set(report["operations"]["dataflows_by_date_status"][0])
        weekly_report = client.get(f"/api/v1/environments/{env['id']}/monitoring/overview?range=90d&grain=auto").json()
        assert weekly_report["summary"]["requested_grain"] == "auto"
        assert weekly_report["summary"]["effective_grain"] in {"hour", "day", "week", "month"}
        assert report["performance"]["overview_p95_duration_seconds"] >= 0
        assert report["performance"]["slowest_dataflows_by_p95"]
        assert report["maintenance"]["kpis"]["total_maintenance_runs"] >= 0

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)
