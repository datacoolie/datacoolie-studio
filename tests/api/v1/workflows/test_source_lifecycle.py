from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from datacoolie_studio.domains.analytics import access as analytics_access
from datacoolie_studio.domains.analytics import maintenance as analytics_maintenance
from tests.api.v1.api_support import (
    SAMPLE_LOGS,
    SAMPLE_METADATA,
    _normalize_duckdb_type,
    _source_status,
)
from tests.support.eventual import wait_for


def test_log_mutation_application_contract_through_http(studio_client, studio_project_factory, tmp_path):
    from datacoolie_studio.domains.sync.service import source_refresh_guard

    environment = studio_project_factory(project_name="log-mutation-http")
    prefix = f"/api/v1/environments/{environment.environment_id}/log-sources"
    source = studio_client.post(prefix, json={"uri": str(tmp_path / "logs"), "label": "keep"}).json()
    url = f"{prefix}/{source['id']}"
    response = studio_client.patch(url, json={"enabled": False})
    assert response.status_code == 200, response.text
    assert response.json()["label"] == "keep"
    assert studio_client.patch(url, json={"label": None}).json()["label"] is None
    with source_refresh_guard(source["id"]) as acquired:
        assert acquired
        blocked = studio_client.delete(url)
        assert blocked.status_code == 409, blocked.text
    assert any(item["id"] == source["id"] for item in studio_client.get(prefix).json())
    assert studio_client.delete(url).status_code == 204
    assert studio_client.delete(url).status_code == 404


def test_source_observation_pause_recovery_api(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    readable_metadata = tmp_path / "metadata.json"
    readable_metadata.write_text(
        '{"connections": [], "dataflows": [], "schema_hints": []}',
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "pause-recovery"}
        ).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        other_environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "test"},
        ).json()
        readable = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={"uri": str(readable_metadata), "label": "readable"},
        ).json()
        missing = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={"uri": str(tmp_path / 'missing.json'), "label": "missing"},
        ).json()

        def pause(source_id: int) -> None:
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    """
                    UPDATE source_observations
                    SET failure_streak = 3,
                        last_outcome = 'error',
                        error_json = '{"code":"not_found","message":"missing"}',
                        automatic_observation_paused_at = ?,
                        next_observation_at = NULL,
                        lease_owner = NULL,
                        lease_expires_at = NULL
                    WHERE source_id = ?
                    """,
                    ("2026-07-30 12:00:00", source_id),
                )

        pause(readable["id"])
        paused = _source_status(client, environment["id"], readable["id"])
        assert paused["observation"]["state"] == "paused"
        assert paused["observation"]["failure_count"] == 3
        assert paused["observation"]["next_check_at"] is None

        wrong_environment = client.post(
            f"/api/v1/environments/{other_environment['id']}/"
            f"metadata-sources/{readable['id']}/retry-observation"
        )
        assert wrong_environment.status_code == 404

        retried = client.post(
            f"/api/v1/environments/{environment['id']}/"
            f"metadata-sources/{readable['id']}/retry-observation"
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["observation"]["state"] == "unchanged"
        assert retried.json()["observation"]["failure_count"] == 0

        pause(missing["id"])
        failed_retry = client.post(
            f"/api/v1/environments/{environment['id']}/"
            f"metadata-sources/{missing['id']}/retry-observation"
        )
        assert failed_retry.status_code == 200, failed_retry.text
        assert failed_retry.json()["observation"]["state"] == "error"
        assert failed_retry.json()["observation"]["failure_count"] == 1

        pause(readable["id"])
        validated = client.post(
            f"/api/v1/environments/{environment['id']}/"
            f"metadata-sources/{readable['id']}/validate"
        )
        assert validated.status_code == 200
        assert validated.json()["status"] == "ok"
        assert (
            _source_status(client, environment["id"], readable["id"])[
                "observation"
            ]
            ["failure_count"]
            == 0
        )

        pause(missing["id"])
        failed_validation = client.post(
            f"/api/v1/environments/{environment['id']}/"
            f"metadata-sources/{missing['id']}/validate"
        )
        assert failed_validation.status_code == 200
        assert failed_validation.json()["status"] == "error"
        assert (
            _source_status(client, environment["id"], missing["id"])[
                "observation"
            ]
            ["state"]
            == "paused"
        )


def test_source_management_api_roundtrip(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(SAMPLE_METADATA), "label": "metadata"},
        ).json()

        validation = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources/{source['id']}/validate"
        ).json()
        assert validation["status"] == "ok"
        assert validation["message"] == "Metadata source path is readable"
        assert validation["detected_format"] == "json"
        assert validation["record_counts"]["files"] == 1
        assert validation["validated_at"].endswith(("Z", "+00:00"))

        observation = client.post(
            f"/api/v1/environments/{env['id']}/sources/observe-local"
        )
        assert observation.status_code == 200
        assert observation.json()["total"] == 1
        assert observation.json()["observed"] == 1
        assert observation.json()["failed"] == 0
        sync_status = _source_status(client, env["id"], source["id"])
        assert sync_status["observation"]["checked_at"] is not None
        assert sync_status["observation"]["next_check_at"] is None
        sources_workspace = client.get(
            f"/api/v1/environments/{env['id']}/sources/workspace"
        )
        assert sources_workspace.status_code == 200
        assert [item["id"] for item in sources_workspace.json()["metadata_sources"]] == [
            source["id"]
        ]
        assert sources_workspace.json()["log_sources"] == []
        assert sources_workspace.json()["code_artifacts"] == []
        assert sources_workspace.json()["statuses"][0]["source_id"] == source["id"]

        sources = client.get(
            f"/api/v1/environments/{env['id']}/metadata-sources"
        ).json()
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

        patched = client.patch(
            f"/api/v1/environments/{env['id']}/metadata-sources/{source['id']}", json={"enabled": False}
        ).json()
        assert patched["enabled"] is False

        impact = client.get(
            f"/api/v1/environments/{env['id']}/metadata-sources/{source['id']}/delete-impact"
        ).json()
        assert impact["mode"] == "hard_delete"
        assert impact["metadata_file_deleted"] is False
        assert impact["has_impact"] is True
        assert {item["kind"] for item in impact["impacts"]} == {
            "source_observation", "sync_job", "materialization",
        }

        response = client.delete(f"/api/v1/environments/{env['id']}/metadata-sources/{source['id']}")
        assert response.status_code == 204
        assert (
            client.get(f"/api/v1/environments/{env['id']}/metadata-sources").json()
            == []
        )


def test_new_log_source_queues_validation_and_sync(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.logs import ingestion as log_ingestion
    from datacoolie_studio.main import app

    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_access, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_maintenance, "analytics_database_path", lambda: analytics_path)

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        source = client.post(
            f"/api/v1/environments/{environment['id']}/log-sources",
            json={"uri": str(SAMPLE_LOGS), "label": "sample logs"},
        ).json()
        assert source["latest_validation"] is None
        def observe_source_state():
            persisted = client.get(
                f"/api/v1/environments/{environment['id']}/log-sources"
            ).json()[0]
            status = _source_status(client, environment["id"], source["id"])
            sync_execution = status.get("sync_execution") or {}
            return persisted, status, sync_execution

        persisted, status, _sync_execution = wait_for(
            observe_source_state,
            lambda observed: (
                observed[0].get("latest_validation") is not None
                and observed[1].get("status") == "ok"
                and observed[2].get("trigger") == "automatic"
                and observed[2].get("state") == "succeeded"
            ),
            description="automatic log source initialization",
        )
        assert persisted.get("latest_validation") is not None, persisted
        assert persisted["latest_validation"]["status"] == "ok"
        assert status["status"] == "ok"
        assert status["sync_execution"]["trigger"] == "automatic"
        assert status["sync_execution"]["state"] == "succeeded"
        with sqlite3.connect(db_path) as connection:
            assert connection.execute("select count(*) from sync_jobs").fetchone()[0] == 2


def test_source_detail_routes_require_matching_environment_scope(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    code_root = tmp_path / "code"
    code_root.mkdir()
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        environment_a = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        environment_b = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "prod"}
        ).json()
        metadata_source = client.post(
            f"/api/v1/environments/{environment_a['id']}/metadata-sources",
            json={"uri": str(SAMPLE_METADATA), "label": "metadata"},
        ).json()
        log_source = client.post(
            f"/api/v1/environments/{environment_a['id']}/log-sources",
            json={"uri": str(tmp_path / "logs"), "label": "logs"},
        ).json()
        code_artifact = client.post(
            f"/api/v1/environments/{environment_a['id']}/code-artifacts",
            json={"uri": str(code_root), "label": "code"},
        ).json()

        assert client.patch(
            f"/api/v1/environments/{environment_b['id']}/metadata-sources/{metadata_source['id']}",
            json={"label": "wrong environment"},
        ).status_code == 404
        assert client.delete(
            f"/api/v1/environments/{environment_b['id']}/log-sources/{log_source['id']}"
        ).status_code == 404
        assert client.post(
            f"/api/v1/environments/{environment_b['id']}/code-artifacts/{code_artifact['id']}/refresh"
        ).status_code == 404

        metadata_sources = client.get(
            f"/api/v1/environments/{environment_a['id']}/metadata-sources"
        ).json()
        log_sources = client.get(
            f"/api/v1/environments/{environment_a['id']}/log-sources"
        ).json()
        code_artifacts = client.get(
            f"/api/v1/environments/{environment_a['id']}/code-artifacts"
        ).json()
        assert metadata_sources[0]["label"] == "metadata"
        assert [item["id"] for item in log_sources] == [log_source["id"]]
        assert [item["id"] for item in code_artifacts] == [code_artifact["id"]]

        paths = client.get("/openapi.json").json()["paths"]
        assert not any(
            path.startswith(("/api/v1/metadata-sources/", "/api/v1/log-sources/", "/api/v1/code-artifacts/"))
            for path in paths
        )
        assert "/api/v1/environments/{environment_id}/metadata-sources/{source_id}" in paths
        assert "/api/v1/environments/{environment_id}/log-sources/{source_id}" in paths
        assert "/api/v1/environments/{environment_id}/code-artifacts/{source_id}" in paths

    monkeypatch.delenv("DATACOOLIE_STUDIO_DATABASE_URL", raising=False)

def test_source_refresh_records_revision_and_sync_job(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(SAMPLE_METADATA), "label": "metadata"},
        ).json()

        status = _source_status(client, env["id"], source["id"])
        assert status["status"] == "ok"
        assert status["sync_execution"]["trigger"] == "automatic"
        assert status["sync_execution"]["state"] == "succeeded"

        validation = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources/{source['id']}/validate"
        ).json()
        assert validation["status"] == "ok"
        refreshed = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources/{source['id']}/refresh"
        ).json()
        assert {
            "source_id",
            "source_kind",
            "status",
            "message",
            "validation",
            "observation",
            "sync_execution",
        } <= set(refreshed)
        assert not {
            "revision",
            "error",
            "checked_at",
            "last_observed_at",
            "next_check_at",
            "pending_changes",
            "observation_state",
            "observation_failure_count",
            "observation_paused_at",
            "active_operation",
            "latest_job",
        } & set(refreshed)
        assert refreshed["status"] == "ok"
        assert refreshed["message"] == "Metadata source materialization refreshed"
        assert refreshed["sync_execution"]["result"]["revision"]["object_type"] == "file"
        assert refreshed["sync_execution"]["result"]["revision"]["content_hash"]
        assert refreshed["sync_execution"]["state"] == "succeeded"
        assert refreshed["sync_execution"]["trigger"] == "manual"

        listed = client.get(
            f"/api/v1/environments/{env['id']}/metadata-sources"
        ).json()
        assert listed[0]["latest_validation"] == validation

        impact = client.get(
            f"/api/v1/environments/{env['id']}/metadata-sources/{source['id']}/delete-impact"
        ).json()
        assert impact["has_impact"] is True
        assert {item["kind"] for item in impact["impacts"]} == {
            "source_observation",
            "sync_job",
            "materialization",
        }

        response = client.delete(f"/api/v1/environments/{env['id']}/metadata-sources/{source['id']}")
        assert response.status_code == 204
        with sqlite3.connect(db_path) as connection:
            assert (
                connection.execute("select count(*) from source_observations").fetchone()[
                    0
                ]
                == 0
            )
            assert (
                connection.execute("select count(*) from sync_jobs").fetchone()[0] == 0
            )
            assert (
                connection.execute(
                    "select count(*) from metadata_materializations"
                ).fetchone()[0]
                == 0
            )


def test_source_fast_stat_omits_content_hash(tmp_path: Path):
    from datacoolie_studio.db.models import EnvironmentSource
    from datacoolie_studio.domains.sync.service import stat_source

    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        '{"connections": [], "dataflows": [], "schema_hints": []}', encoding="utf-8"
    )
    source = EnvironmentSource(
        environment_id=1, source_kind="metadata", uri=str(metadata_path), enabled=True
    )

    fast = stat_source(source, include_content_hash=False)
    full = stat_source(source)

    assert fast["object_type"] == "file"
    assert "content_hash" not in fast
    assert full["content_hash"]

def test_environment_freshness_reports_materialized_cache_state(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "studio.db"
    metadata_old = tmp_path / "metadata_old.json"
    metadata_new = tmp_path / "metadata_new.json"
    logs_dir = tmp_path / "logs"
    dataflow_dir = logs_dir / "execution_logs" / "dataflow_run_log" / "2023-11-14"
    job_dir = logs_dir / "execution_logs" / "job_run_log" / "2023-11-14"
    dataflow_dir.mkdir(parents=True)
    job_dir.mkdir(parents=True)
    log_old = job_dir / "old.job.json"
    log_new = dataflow_dir / "new.dataflow.json"
    for path in (metadata_old, metadata_new):
        path.write_text(
            '{"connections": [], "dataflows": [], "schema_hints": []}', encoding="utf-8"
        )
    log_old.write_text(
        json.dumps(
            {
                "log_schema_version": 3,
                "_type": "job_run_log",
                "job_id": "job-old",
                "status": "succeeded",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    log_new.write_text(
        json.dumps(
            {
                "log_schema_version": 3,
                "_type": "dataflow_run_log",
                "job_id": "job-new",
                "dataflow_id": "flow-new",
                "dataflow_run_id": "run-new",
                "status": "succeeded",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    os.utime(metadata_old, (1_700_000_000, 1_700_000_000))
    os.utime(metadata_new, (1_700_000_300, 1_700_000_300))
    os.utime(log_old, (1_700_000_100, 1_700_000_100))
    os.utime(log_new, (1_700_000_200, 1_700_000_200))
    # The log source revision is the configured root directory revision; keep
    # it deterministic after creating the v3 partition tree.
    os.utime(logs_dir, (1_700_000_300, 1_700_000_300))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        first_source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(metadata_old), "label": "old"},
        ).json()
        client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(metadata_new), "label": "new"},
        )
        log_source = client.post(
            f"/api/v1/environments/{env['id']}/log-sources",
            json={"uri": str(logs_dir), "label": "logs"},
        ).json()
        client.post(
            f"/api/v1/environments/{env['id']}/log-sources/{log_source['id']}/refresh",
            json={"mode": "incremental"},
        )

        freshness = client.get(f"/api/v1/environments/{env['id']}/freshness").json()
        assert freshness["metadata_source_count"] == 2
        assert freshness["log_source_count"] == 1
        assert freshness["status"] == "current"
        # Metadata and logs have both materialized before freshness is read.
        assert freshness["max_source_modified_at"] == "2023-11-14T22:18:20Z"
        initial_source_cache_version = freshness["source_cache_version"]

        metadata_old.write_text(
            '{"connections": [], "dataflows": [{"name": "changed"}], "schema_hints": []}',
            encoding="utf-8",
        )
        os.utime(metadata_old, (1_700_000_900, 1_700_000_900))
        client.post(f"/api/v1/environments/{env['id']}/metadata-sources/{first_source['id']}/refresh")
        source_freshness = client.get(
            f"/api/v1/environments/{env['id']}/freshness"
        ).json()
        first_item = next(
            item
            for item in source_freshness["items"]
            if item["source_id"] == first_source["id"]
        )
        assert first_item["status"] == "current"
        assert source_freshness["source_cache_version"] != initial_source_cache_version


def test_source_refresh_reports_missing_path_without_crashing(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(tmp_path / "missing.json"), "label": "missing"},
        ).json()

        refreshed = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources/{source['id']}/refresh"
        ).json()
        assert refreshed["status"] == "error"
        assert refreshed["observation"]["error"]["code"] == "not_found"
        assert refreshed["sync_execution"]["state"] == "failed"
        assert refreshed["sync_execution"]["result"]["error"]["code"] == "not_found"
        listed = client.get(
            f"/api/v1/environments/{env['id']}/metadata-sources"
        ).json()
        assert listed[0]["latest_validation"] is None


def test_failed_metadata_refresh_does_not_reuse_previous_success(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "studio.db"
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_bytes(SAMPLE_METADATA.read_bytes())
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv(
        "DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}"
    )

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "metadata-refresh"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        source = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={"uri": str(metadata_path), "label": "metadata"},
        ).json()

        first = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources/{source['id']}/refresh"
        ).json()
        assert first["status"] == "ok"
        assert first["sync_execution"]["state"] == "succeeded"
        previous_job_id = first["sync_execution"]["job_id"]

        metadata_path.unlink()
        second = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources/{source['id']}/refresh"
        ).json()

        assert second["status"] == "error"
        assert second["sync_execution"]["state"] == "failed"
        assert second["sync_execution"]["job_id"] != previous_job_id
        assert second["sync_execution"]["result"]["error"]["code"] == "not_found"
        assert second["observation"]["error"]["code"] == "not_found"

        cached = client.get(
            f"/api/v1/environments/{environment['id']}/metadata"
        ).json()
        assert cached["errors"][0]["cache_status"] == "stale"


def test_source_uri_update_clears_read_check_and_sync_status(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(SAMPLE_METADATA), "label": "metadata"},
        ).json()
        client.post(f"/api/v1/environments/{env['id']}/metadata-sources/{source['id']}/validate")
        client.post(f"/api/v1/environments/{env['id']}/metadata-sources/{source['id']}/refresh")

        updated = client.patch(
            f"/api/v1/environments/{env['id']}/metadata-sources/{source['id']}",
            json={"uri": str(tmp_path / "other.json")},
        ).json()
        assert updated["latest_validation"] is None
        status = _source_status(client, env["id"], source["id"])
        assert status["status"] == "unknown"
        assert status["sync_execution"]["state"] == "succeeded"
        assert status["sync_execution"]["job_id"] is None


def test_log_base_path_refresh_records_directory_revision(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.logs import ingestion as log_ingestion
    from datacoolie_studio.main import app

    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_access, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_maintenance, "analytics_database_path", lambda: analytics_path)

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        path = client.post(
            f"/api/v1/environments/{env['id']}/log-sources",
            json={"uri": str(SAMPLE_LOGS), "label": "sample logs"},
        ).json()
        validation_before_sync = client.get(
            f"/api/v1/environments/{env['id']}/log-sources"
        ).json()[0]["latest_validation"]

        refreshed = client.post(
            f"/api/v1/environments/{env['id']}/log-sources/{path['id']}/refresh",
            json={"mode": "incremental"},
        ).json()
        assert refreshed["status"] == "ok"
        assert refreshed["sync_execution"]["result"]["revision"]["object_type"] == "directory"
        assert refreshed["sync_execution"]["result"]["revision"]["file_count"] > 0
        assert refreshed["sync_execution"]["state"] == "succeeded"
        assert refreshed["sync_execution"]["summary"] == "Log source cache is current"
        assert analytics_path.exists()
        listed = client.get(f"/api/v1/environments/{env['id']}/log-sources").json()
        assert listed[0]["latest_validation"]["status"] == "ok"
        assert listed[0]["latest_validation"]["record_counts"]["job_json_files"] > 0
        assert listed[0]["latest_validation"] == validation_before_sync
        with sqlite3.connect(tmp_path / "studio.db") as connection:
            assert (
                connection.execute("select count(*) from log_file_manifest").fetchone()[
                    0
                ]
                > 0
            )
        import duckdb

        with duckdb.connect(str(analytics_path), read_only=True) as connection:
            tables = {row[0] for row in connection.execute("show tables").fetchall()}
            assert {
                "etl_job_runs",
                "etl_dataflow_runs",
                "etl_monitoring_filter_values",
            } <= tables
            job_columns = {
                str(row[1]): str(row[2]).upper()
                for row in connection.execute(
                    "PRAGMA table_info('etl_job_runs')"
                ).fetchall()
            }
            dataflow_columns = {
                str(row[1]): _normalize_duckdb_type(str(row[2]))
                for row in connection.execute(
                    "PRAGMA table_info('etl_dataflow_runs')"
                ).fetchall()
            }
            sample_dataflow_file = (
                str(next((SAMPLE_LOGS / "execution_logs" / "dataflow_run_log").rglob("*.json")))
                .replace("\\", "/")
                .replace("'", "''")
            )
            source_types = {
                str(row[0]): _normalize_duckdb_type(str(row[1]))
                for row in connection.execute(
                    f"DESCRIBE SELECT * FROM read_json_auto('{sample_dataflow_file}', format='newline_delimited', union_by_name=true)"
                ).fetchall()
            }
            assert {
                "job_id",
                "status",
                "duration_seconds",
                "_source_id",
                "_file_uri",
            } <= set(job_columns)
            assert {
                "dataflow_id",
                "stage",
                "source_id",
                "status",
                "_source_id",
                "_file_uri",
            } <= set(dataflow_columns)
            assert "_raw_json" not in job_columns
            assert "_raw_json" not in dataflow_columns
            assert job_columns["start_time"] == "VARCHAR"
            assert dataflow_columns["start_time"] == "VARCHAR"
            assert source_types["start_time"] in {
                "TIMESTAMP",
                "TIMESTAMP WITH TIME ZONE",
            }
            assert "source_end_time" not in dataflow_columns
            assert dataflow_columns["attempts"].startswith("STRUCT(")
            serving_types = {
                str(row[0]): _normalize_duckdb_type(str(row[1]))
                for row in connection.execute("DESCRIBE monitoring_dataflow_facts").fetchall()
            }
            assert serving_types["start_time"] == "TIMESTAMP WITH TIME ZONE"
            assert "source_id" in dataflow_columns
            assert "_source_id" in dataflow_columns
            assert (
                connection.execute("select count(*) from etl_job_runs").fetchone()[0]
                > 0
            )
            assert (
                connection.execute("select count(*) from etl_dataflow_runs").fetchone()[
                    0
                ]
                > 0
            )
            assert (
                connection.execute(
                    "select count(*) from etl_monitoring_filter_values where field = 'operation_type'"
                ).fetchone()[0]
                > 0
            )

        # The fixture is historical; use an explicit full-history window so
        # the assertions do not change as the current date moves forward.
        report = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/overview?range=all"
        ).json()
        assert report["schema_version"] == "monitoring-page.v12"
        assert report["page"] == "overview"
        assert set(report) == {
            "schema_version", "page", "summary", "health", "attention",
            "operations", "failures", "volume", "log_admission",
        }
        assert report["summary"]["dataflow_records"] != "0"
        assert report["summary"]["job_records"] != "0"
        assert report["summary"]["requested_grain"] == "auto"
        assert report["summary"]["effective_grain"] in {"hour", "day", "week", "month"}
        diagnostics_report = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/diagnostics?range=all"
        ).json()
        assert diagnostics_report["diagnostics"]["kpis"]["matched_job_ids"] > 0
        freshness_report = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/freshness?range=all"
        ).json()
        assert "freshness" in freshness_report
        assert "freshness" not in report
        filter_options = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/filter-options"
        ).json()
        assert filter_options["summary"]["source"] == "duckdb_filter_values"
        assert filter_options["options"]["operation_type"]
        filtered_report = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/overview?range=all&status=failed"
        ).json()
        assert (
            filtered_report["summary"]["dataflow_records"]
            < report["summary"]["dataflow_records"]
        )
        assert (
            filtered_report["operations"]["dataflow_kpis"]["failed"]
            == filtered_report["summary"]["dataflow_records"]
        )
        assert filtered_report["operations"]["dataflow_kpis"]["skipped"] == "0"
        last_24h = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/overview?range=24h"
        ).json()
        assert (
            last_24h["summary"]["dataflow_records"]
            <= report["summary"]["dataflow_records"]
        )
        last_3d = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/overview?range=3d"
        ).json()
        assert (
            last_3d["summary"]["dataflow_records"]
            <= report["summary"]["dataflow_records"]
        )
        custom = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/overview?range=custom&startTime=1900-01-01T00:00:00Z&endTime=1900-01-02T00:00:00Z"
        ).json()
        assert custom["summary"]["dataflow_records"] == "0"
        dataflows = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/dataflows?range=all&limit=5"
        ).json()
        assert dataflows["summary"]["cache"] == "duckdb"
        assert dataflows["summary"]["records"] == 5
        assert (
            dataflows["summary"]["total_records"]
            == int(report["summary"]["dataflow_records"])
        )
        assert {
            "source_display",
            "destination_display",
            "phase_health",
            "movement_state",
        } <= set(dataflows["records"][0])
        assert (
            dataflows["records"][0]["start_time"]
            >= dataflows["records"][-1]["start_time"]
        )
        last_3d_dataflows = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/dataflows?limit=5&range=3d"
        ).json()
        assert (
            last_3d_dataflows["summary"]["total_records"]
            <= dataflows["summary"]["total_records"]
        )
        failed_dataflows = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/dataflows?range=all&limit=5&status=failed"
        ).json()
        assert failed_dataflows["summary"]["cache"] == "duckdb"
        assert failed_dataflows["summary"]["total_records"] >= 0
        if failed_dataflows["records"]:
            assert {row["status"] for row in failed_dataflows["records"]} == {"failed"}
        slow_dataflows = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/dataflows?range=all&limit=5&sortBy=duration_seconds&sortDir=desc"
        ).json()
        fast_dataflows = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/dataflows?range=all&limit=5&sortBy=duration_seconds&sortDir=asc"
        ).json()
        assert (
            slow_dataflows["records"][0]["duration_seconds"]
            >= slow_dataflows["records"][-1]["duration_seconds"]
        )
        assert (
            fast_dataflows["records"][0]["duration_seconds"]
            <= fast_dataflows["records"][-1]["duration_seconds"]
        )
        jobs = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/jobs?range=all&limit=3&offset=0"
        ).json()
        assert jobs["summary"]["cache"] == "duckdb"
        assert jobs["summary"]["records"] == 1
        assert jobs["summary"]["offset"] == 0
        assert jobs["summary"]["total_records"] == int(report["summary"]["job_records"])
        assert {
            "child_dataflow_count",
            "child_failed_count",
            "reconciliation_status",
            "error_preview",
        } <= set(jobs["records"][0])
        assert jobs["records"][0]["start_time"] >= jobs["records"][-1]["start_time"]
        last_3d_jobs = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/jobs?limit=3&range=3d"
        ).json()
        assert (
            last_3d_jobs["summary"]["total_records"] <= jobs["summary"]["total_records"]
        )
        longest_jobs = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/jobs?range=all&limit=5&sortBy=duration_seconds&sortDir=desc"
        ).json()
        assert (
            longest_jobs["records"][0]["duration_seconds"]
            >= longest_jobs["records"][-1]["duration_seconds"]
        )

        second_refresh = client.post(
            f"/api/v1/environments/{env['id']}/log-sources/{path['id']}/refresh",
            json={"mode": "incremental"},
        ).json()
        assert second_refresh["sync_execution"]["summary"] == "Log source cache is current"
        assert (
            second_refresh["sync_execution"]["result"]["record_counts"]["parsed_files"] == 0
        )


def test_log_base_path_reads_only_v6_log_categories(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    base = tmp_path / "logs"
    dataflows = base / "execution_logs" / "dataflow_run_log"
    jobs = base / "execution_logs" / "job_run_log"
    debug_dataflows = base / "debug" / "dataflow_run_log"
    debug_jobs = base / "debug" / "job_run_log"
    system_logs = base / "system_logs"
    for folder in (dataflows, jobs, debug_dataflows, debug_jobs, system_logs):
        folder.mkdir(parents=True)

    dataflow_sample = next((SAMPLE_LOGS / "execution_logs" / "dataflow_run_log").rglob("*.json"))
    job_sample = next((SAMPLE_LOGS / "execution_logs" / "job_run_log").rglob("*.json"))
    system_sample = next((SAMPLE_LOGS / "system_logs").rglob("*.json"))
    shutil.copy2(dataflow_sample, dataflows / dataflow_sample.name)
    shutil.copy2(job_sample, jobs / job_sample.name)
    shutil.copy2(system_sample, system_logs / system_sample.name)
    for folder in (debug_dataflows, debug_jobs):
        folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dataflow_sample, debug_dataflows / dataflow_sample.name)
    for folder in (debug_jobs,):
        shutil.copy2(job_sample, folder / job_sample.name)

    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))

    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.logs import ingestion as log_ingestion
    from datacoolie_studio.domains.logs.reader import read_dataflow_logs
    from datacoolie_studio.main import app

    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_access, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_maintenance, "analytics_database_path", lambda: analytics_path)

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/log-sources",
            json={"uri": str(base), "label": "base logs"},
        ).json()

        expected_rows, expected_errors = read_dataflow_logs([str(dataflows)])
        assert not expected_errors
        available = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/dataflows?range=365d"
        )
        assert available.status_code == 200
        assert available.json()["summary"]["total_records"] == len(expected_rows)
        initial_status = _source_status(client, env["id"], source["id"])
        counts = initial_status["sync_execution"]["result"]["record_counts"]
        assert counts["dataflow_json_files"] == 1
        assert counts["job_json_files"] == 1
        assert counts["system_json_files"] == 1

        refreshed = client.post(
            f"/api/v1/environments/{env['id']}/log-sources/{source['id']}/refresh",
            json={"mode": "incremental"},
        ).json()
        assert refreshed["sync_execution"]["summary"] == "Log source cache is current"
        direct = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/dataflows?range=365d"
        ).json()
        assert direct["summary"]["total_records"] == len(expected_rows)

        with sqlite3.connect(db_path) as connection:
            manifest_paths = [
                row[0]
                for row in connection.execute(
                    "select file_uri from log_file_manifest where source_id = ?",
                    (source["id"],),
                ).fetchall()
            ]
        assert manifest_paths
        normalized_manifest_paths = [path.replace("\\", "/") for path in manifest_paths]
        assert all(
            "/execution_logs/" in path or "/system_logs/" in path
            for path in normalized_manifest_paths
        )
        assert all("/debug/" not in path for path in normalized_manifest_paths)

        freshness = client.get(f"/api/v1/environments/{env['id']}/freshness").json()
        assert freshness["logs"]["status"] == "current"

        # Freshness reads are DB-only. Logs are observed periodically, while
        # their actual sync remains manual or schedule-driven.
        from datacoolie_studio.db.models import EnvironmentSource
        from datacoolie_studio.db.session import create_session
        from datacoolie_studio.domains.source_observation.repository import (
            claim_due_observation_ids,
            reset_observation,
        )
        from datacoolie_studio.domains.logs import pending as log_pending
        from datacoolie_studio.domains.sync.scheduler import _observe_source

        def observe_log_change() -> bool:
            session = create_session()
            try:
                observed_at = datetime.now(timezone.utc)
                reset_observation(
                    session,
                    int(source["id"]),
                    due_at=observed_at,
                )
                session.commit()
                owner, source_ids = claim_due_observation_ids(
                    session,
                    now=observed_at,
                    lease_owner="test-log-observation",
                )
                assert int(source["id"]) in source_ids
                observed_source = session.get(EnvironmentSource, int(source["id"]))
                return _observe_source(session, observed_source, owner)
            finally:
                session.close()

        # Changing a file outside the v3 categories does not affect the cache.
        debug_file = debug_jobs / job_sample.name
        debug_file.write_text(debug_file.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        log_pending.invalidate_pending_changes(int(source["id"]))
        assert observe_log_change() is False
        freshness_after_debug_change = client.get(f"/api/v1/environments/{env['id']}/freshness").json()
        assert freshness_after_debug_change["logs"]["status"] == "current"

        # Changing a synced v3 log file makes the cache out of date; the header
        # reflects it as not synced without any auto-sync.
        synced_file = jobs / job_sample.name
        synced_file.write_text(synced_file.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        log_pending.invalidate_pending_changes(int(source["id"]))
        assert observe_log_change() is True
        freshness_after_synced_change = client.get(f"/api/v1/environments/{env['id']}/freshness").json()
        assert freshness_after_synced_change["logs"]["status"] == "not_cached"


def test_delete_etl_log_path_purges_duckdb_cache(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    scheduled_logs = tmp_path / "scheduled-logs"
    shutil.copytree(SAMPLE_LOGS, scheduled_logs)
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.logs import ingestion as log_ingestion
    from datacoolie_studio.main import app

    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_access, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_maintenance, "analytics_database_path", lambda: analytics_path)

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        path = client.post(
            f"/api/v1/environments/{env['id']}/log-sources",
            json={"uri": str(scheduled_logs), "label": "sample logs"},
        ).json()
        source_id = int(path["id"])
        assert path["sync_schedule_enabled"] is False
        assert path["sync_interval_minutes"] == 1

        refreshed = client.post(
            f"/api/v1/environments/{env['id']}/log-sources/{source_id}/refresh",
            json={"mode": "incremental"},
        ).json()
        assert refreshed["status"] == "ok"

        scheduled = client.patch(
            f"/api/v1/environments/{env['id']}/log-sources/{source_id}",
            json={"sync_schedule_enabled": True},
        ).json()
        assert scheduled["sync_interval_minutes"] == 1
        from datacoolie_studio.domains.sync import scheduler as sync_scheduler

        with sqlite3.connect(db_path) as connection:
            running_job = connection.execute(
                """
                insert into sync_jobs (
                    environment_id, source_id, source_kind, job_type, status, started_at
                ) values (?, ?, 'logs', 'manual_refresh', 'running', ?)
                """,
                (env["id"], source_id, datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()),
            )
            running_job_id = int(running_job.lastrowid)
            connection.commit()

        assert sync_scheduler.run_due_schedules_once() == 0
        with sqlite3.connect(db_path) as connection:
            assert connection.execute(
                "select last_scheduled_sync_at from environment_sources where id = ?",
                (source_id,),
            ).fetchone()[0] is None
            assert connection.execute(
                "select count(*) from sync_jobs where source_id = ? and job_type = 'scheduled_refresh'",
                (source_id,),
            ).fetchone()[0] == 0
            connection.execute(
                """
                update sync_jobs
                set status = 'succeeded', completed_at = ?
                where id = ?
                """,
                (datetime.now(timezone.utc).isoformat(), running_job_id),
            )
            connection.commit()

        assert sync_scheduler.run_due_schedules_once() == 1
        unchanged_status = _source_status(client, env["id"], source_id)
        assert unchanged_status["sync_execution"]["trigger"] == "manual"
        with sqlite3.connect(db_path) as connection:
            assert connection.execute(
                "select last_scheduled_sync_at from environment_sources where id = ?",
                (source_id,),
            ).fetchone()[0] is not None
            assert connection.execute(
                "select count(*) from sync_jobs where source_id = ? and job_type = 'scheduled_refresh'",
                (source_id,),
            ).fetchone()[0] == 0

        monkeypatch.setattr(
            sync_scheduler,
            "log_source_has_pending_changes",
            lambda *_args, **_kwargs: True,
        )
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "update environment_sources set last_scheduled_sync_at = ? where id = ?",
                (datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat(), source_id),
            )
            connection.commit()

        assert sync_scheduler.run_due_schedules_once() == 1
        scheduled_status = _source_status(client, env["id"], source_id)
        assert scheduled_status["sync_execution"]["trigger"] == "scheduled"

        import duckdb

        with duckdb.connect(str(analytics_path), read_only=True) as connection:
            assert (
                connection.execute(
                    "select count(*) from etl_dataflow_runs where _source_id = ?",
                    [source_id],
                ).fetchone()[0]
                > 0
            )

        with sqlite3.connect(db_path) as connection:
            assert connection.execute(
                "select count(*) from log_file_manifest where source_id = ?", [source_id]
            ).fetchone()[0] > 0
            assert connection.execute(
                "select count(*) from source_observations where source_id = ?", [source_id]
            ).fetchone()[0] > 0
            assert connection.execute(
                "select count(*) from sync_jobs where source_id = ?", [source_id]
            ).fetchone()[0] > 0

        impact = client.get(f"/api/v1/environments/{env['id']}/log-sources/{source_id}/delete-impact")
        assert impact.status_code == 200
        impact_body = impact.json()
        assert impact_body["has_impact"] is True
        assert impact_body["metadata_file_deleted"] is False
        assert "Original log files will not be deleted" in impact_body["summary"]
        impact_counts = {item["kind"]: item["count"] for item in impact_body["impacts"]}
        assert impact_counts["manifest"] > 0
        assert impact_counts["dataflow_cache"] > 0
        assert impact_counts["source_observation"] > 0
        assert impact_counts["sync_job"] > 0
        assert impact_counts["schedule"] == 1

        deleted = client.delete(f"/api/v1/environments/{env['id']}/log-sources/{source_id}")
        assert deleted.status_code == 204

        with sqlite3.connect(db_path) as connection:
            for table in ("log_file_manifest", "source_observations", "sync_jobs"):
                assert connection.execute(
                    f"select count(*) from {table} where source_id = ?", [source_id]
                ).fetchone()[0] == 0

        with duckdb.connect(str(analytics_path), read_only=True) as connection:
            assert (
                connection.execute(
                    "select count(*) from etl_dataflow_runs where _source_id = ?",
                    [source_id],
                ).fetchone()[0]
                == 0
            )
            assert (
                connection.execute(
                    "select count(*) from etl_job_runs where _source_id = ?",
                    [source_id],
                ).fetchone()[0]
                == 0
            )
            assert (
                connection.execute(
                    "select count(*) from etl_monitoring_filter_values where _source_id = ?",
                    [source_id],
                ).fetchone()[0]
                == 0
            )
