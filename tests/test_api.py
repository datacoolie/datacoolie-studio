from __future__ import annotations

import os
import io
import sqlite3
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from datacoolie_studio.domains.analytics import access as analytics_access
from datacoolie_studio.domains.analytics import maintenance as analytics_maintenance

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "datacoolie"
SAMPLE_LOGS = FIXTURE_ROOT / "usecase-sim" / "logs" / "etl_logs" / "analyst"
SAMPLE_METADATA = (
    FIXTURE_ROOT / "usecase-sim" / "metadata" / "file" / "local_use_cases.json"
)


def _normalize_duckdb_type(value: str) -> str:
    return value.upper().replace("TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE")


def _source_status(client, environment_id: int, source_id: int) -> dict:
    workspace = client.get(
        f"/api/v1/environments/{environment_id}/sources/workspace"
    )
    assert workspace.status_code == 200, workspace.text
    return next(
        status
        for status in workspace.json()["statuses"]
        if status["source_id"] == source_id
    )


def test_workspace_api_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        assert project["name"] == "demo"
        env = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        assert env["name"] == "dev"
        source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": "metadata.json", "label": "metadata"},
        ).json()
        assert Path(source["uri"]).is_absolute()
        assert Path(source["uri"]).name == "metadata.json"
        assert source["configured_location"]["input_uri"] == "metadata.json"
        assert client.get("/api/projects").status_code == 404

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_source_observation_pause_recovery_api(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    readable_metadata = tmp_path / "metadata.json"
    readable_metadata.write_text(
        '{"connections": [], "dataflows": [], "schema_hints": []}',
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

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
        assert paused["observation_state"] == "paused"
        assert paused["observation_failure_count"] == 3
        assert paused["next_check_at"] is None

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
        assert retried.json()["observation_state"] == "active"
        assert retried.json()["observation_failure_count"] == 0

        pause(missing["id"])
        failed_retry = client.post(
            f"/api/v1/environments/{environment['id']}/"
            f"metadata-sources/{missing['id']}/retry-observation"
        )
        assert failed_retry.status_code == 200, failed_retry.text
        assert failed_retry.json()["observation_state"] == "retrying"
        assert failed_retry.json()["observation_failure_count"] == 1

        pause(readable["id"])
        validated = client.post(
            f"/api/v1/environments/{environment['id']}/"
            f"metadata-sources/{readable['id']}/validate"
        )
        assert validated.status_code == 200
        assert validated.json()["status"] == "ok"
        assert (
            _source_status(client, environment["id"], readable["id"])[
                "observation_state"
            ]
            == "active"
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
                "observation_state"
            ]
            == "paused"
        )

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_monitoring_bypasses_validated_empty_log_sources(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    empty_logs = tmp_path / "empty-logs"
    empty_logs.mkdir()
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.logs import ingestion as log_ingestion
    from datacoolie_studio.main import app

    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_access, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_maintenance, "analytics_database_path", lambda: analytics_path)
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "empty-monitoring"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        source = client.post(
            f"/api/v1/environments/{environment['id']}/log-sources",
            json={"uri": str(empty_logs), "label": "empty logs"},
        ).json()
        source = client.get(
            f"/api/v1/environments/{environment['id']}/log-sources"
        ).json()[0]

        assert source["latest_validation"]["status"] == "error"
        assert source["latest_validation"]["message"] == "No ETL or system log files found"

        overview = client.get(f"/api/v1/environments/{environment['id']}/overview")
        filter_options = client.get(
            f"/api/v1/environments/{environment['id']}/monitoring/filter-options"
        )
        assert overview.status_code == 200, overview.text
        assert overview.json()["monitoring"]["job_records"] == 0
        assert filter_options.status_code == 200, filter_options.text
        assert filter_options.json()["options"] == {}
        for page in (
            "environment-overview",
            "overview",
            "jobs",
            "dataflows",
            "failures",
            "diagnostics",
            "performance",
            "volume",
            "maintenance",
            "freshness",
        ):
            monitoring_page = client.get(
                f"/api/v1/environments/{environment['id']}/monitoring/pages/{page}"
            )
            assert monitoring_page.status_code == 200, monitoring_page.text
            assert monitoring_page.json()["summary"]["job_records"] == 0

        for endpoint in ("dataflows", "jobs"):
            records = client.get(
                f"/api/v1/environments/{environment['id']}/monitoring/{endpoint}"
            )
            assert records.status_code == 200, records.text
            assert records.json()["summary"]["total_records"] == 0
        for page in ("performance", "freshness", "volume", "maintenance"):
            evidence = client.get(
                f"/api/v1/environments/{environment['id']}/monitoring/pages/{page}/evidence"
            )
            assert evidence.status_code == 200, evidence.text
            assert evidence.json()["summary"]["total_records"] == 0


def test_environment_overview_is_available_when_monitoring_cache_is_missing(
    tmp_path: Path,
    monkeypatch,
):
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.logs import ingestion as log_ingestion
    from datacoolie_studio.main import app

    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_access, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_maintenance, "analytics_database_path", lambda: analytics_path)
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "overview-fail-soft"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        source = client.post(
            f"/api/v1/environments/{environment['id']}/log-sources",
            json={"uri": str(SAMPLE_LOGS), "label": "logs"},
        ).json()
        source = client.get(
            f"/api/v1/environments/{environment['id']}/log-sources"
        ).json()[0]
        assert source["latest_validation"]["status"] == "ok"
        # Add performs the required initial sync. Remove that cache explicitly
        # so this test still exercises the fail-soft read contract.
        analytics_path.unlink()

        overview = client.get(f"/api/v1/environments/{environment['id']}/overview")
        filter_options = client.get(
            f"/api/v1/environments/{environment['id']}/monitoring/filter-options"
        )
        dataflows = client.get(
            f"/api/v1/environments/{environment['id']}/monitoring/dataflows"
        )
        jobs = client.get(f"/api/v1/environments/{environment['id']}/monitoring/jobs")
        latest_status = client.get(
            f"/api/v1/environments/{environment['id']}/monitoring/latest-status"
        )
        evidence = client.get(
            f"/api/v1/environments/{environment['id']}/monitoring/pages/performance/evidence"
        )

        assert overview.status_code == 200, overview.text
        assert overview.json()["monitoring"] == {
            "job_records": 0,
            "total_failures": 0,
            "dataflow_success_rate": 0.0,
            "failed_job_windows": {"last7": 0, "last30": 0, "last365": 0},
            "active_engines": 0,
            "latest_log_at": None,
            "date_range": {"min": None, "max": None},
            "errors": [],
        }
        assert filter_options.status_code == 200, filter_options.text
        assert filter_options.json()["options"] == {}
        assert dataflows.status_code == 200, dataflows.text
        assert dataflows.json()["summary"]["total_records"] == 0
        assert jobs.status_code == 200, jobs.text
        assert jobs.json()["summary"]["total_records"] == 0
        assert latest_status.status_code == 200, latest_status.text
        assert latest_status.json()["latest_by_id"] == {}
        assert evidence.status_code == 200, evidence.text
        assert evidence.json()["records"] == []

        for page in (
            "environment-overview",
            "overview",
            "jobs",
            "dataflows",
            "failures",
            "diagnostics",
            "performance",
            "volume",
            "maintenance",
            "freshness",
        ):
            monitoring_page = client.get(
                f"/api/v1/environments/{environment['id']}/monitoring/pages/{page}"
            )
            assert monitoring_page.status_code == 200, monitoring_page.text
            assert monitoring_page.json()["summary"]["job_records"] == 0


def test_environment_context_is_narrow_and_versions_dependencies(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "context-demo"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()

        initial = client.get(
            f"/api/v1/environments/{environment['id']}/context"
        )
        assert initial.status_code == 200
        initial_context = initial.json()
        assert initial_context["schema_version"] == "environment-context.v1"
        assert initial_context["project"] == {"id": project["id"], "name": "context-demo"}
        assert initial_context["environment"] == {
            "id": environment["id"],
            "project_id": project["id"],
            "name": "dev",
        }
        assert initial_context["source_counts"] == {"metadata": 0, "logs": 0, "code": 0}
        assert set(initial_context["versions"]) == {
            "source_registry",
            "metadata_catalog",
            "code_catalog",
            "operations",
            "reference_mappings",
        }
        assert "items" not in initial_context["freshness"]

        source_path = tmp_path / "metadata.json"
        source_path.write_text(
            '{"connections": [], "dataflows": [], "schema_hints": []}',
            encoding="utf-8",
        )
        client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={"uri": str(source_path), "label": "metadata"},
        )
        after_source = client.get(
            f"/api/v1/environments/{environment['id']}/context"
        ).json()
        assert after_source["source_counts"]["metadata"] == 1
        assert (
            after_source["versions"]["source_registry"]
            != initial_context["versions"]["source_registry"]
        )
        assert (
            after_source["versions"]["reference_mappings"]
            == initial_context["versions"]["reference_mappings"]
        )

        missing = client.get("/api/v1/environments/999999/context")
        assert missing.status_code == 404

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_project_reference_mapping_crud_and_project_cascade(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "studio.db"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        create_response = client.post(
            f"/api/v1/projects/{project['id']}/reference-mappings",
            json={
                "reference_type": "table_reference",
                "reference_value": "Orders",
                "target_identifier_kind": "logical_table",
                "target_value": "catalog:main:warehouse|main.warehouse.sales.orders",
                "target_display_value": "main.warehouse.sales.orders",
                "note": "manual resolution",
            },
        )
        assert create_response.status_code == 200
        mapping = create_response.json()
        assert mapping["reference_type"] == "table_reference"
        assert mapping["reference_normalized_value"] == "orders"
        assert mapping["target_identifier_kind"] == "logical_table"
        assert (
            mapping["target_normalized_value"]
            == "catalog:main:warehouse|main.warehouse.sales.orders"
        )

        listing = client.get(f"/api/v1/projects/{project['id']}/reference-mappings")
        assert listing.status_code == 200
        payload = listing.json()
        assert len(payload) == 1
        assert payload[0]["id"] == mapping["id"]

        duplicate = client.post(
            f"/api/v1/projects/{project['id']}/reference-mappings",
            json={
                "reference_type": "table_reference",
                "reference_value": "orders",
                "target_identifier_kind": "logical_table",
                "target_value": "catalog:main:warehouse|main.warehouse.sales.orders",
            },
        )
        assert duplicate.status_code == 409

        patched = client.patch(
            f"/api/v1/projects/{project['id']}/reference-mappings/{mapping['id']}",
            json={"note": "updated note", "target_display_value": "sales.orders"},
        )
        assert patched.status_code == 200
        patched_payload = patched.json()
        assert patched_payload["note"] == "updated note"
        assert patched_payload["target_display_value"] == "sales.orders"

        deleted = client.delete(
            f"/api/v1/projects/{project['id']}/reference-mappings/{mapping['id']}"
        )
        assert deleted.status_code == 204
        assert (
            client.get(f"/api/v1/projects/{project['id']}/reference-mappings").json()
            == []
        )

        recreated = client.post(
            f"/api/v1/projects/{project['id']}/reference-mappings",
            json={
                "reference_type": "table_reference",
                "reference_value": "orders",
                "target_identifier_kind": "logical_table",
                "target_value": "catalog:main:warehouse|main.warehouse.sales.orders",
            },
        )
        assert recreated.status_code == 200
        deleted_project = client.delete(f"/api/v1/projects/{project['id']}")
        assert deleted_project.status_code == 204

        with sqlite3.connect(db_path) as connection:
            count = connection.execute(
                "select count(*) from project_reference_mappings"
            ).fetchone()[0]
            assert count == 0

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_project_reference_mapping_migrates_legacy_reference_kind_column(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "studio.db"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        mapping = client.post(
            f"/api/v1/projects/{project['id']}/reference-mappings",
            json={
                "reference_type": "path_reference",
                "reference_value": "abfss://lake/raw/orders",
                "target_identifier_kind": "logical_table",
                "target_value": "bronze.saleslt_salesorderheader",
            },
        ).json()

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "ALTER TABLE project_reference_mappings RENAME TO project_asset_mappings"
        )
        connection.execute(
            "ALTER TABLE project_asset_mappings ADD COLUMN reference_kind VARCHAR(50)"
        )
        connection.execute(
            "UPDATE project_asset_mappings SET reference_kind = reference_type"
        )

    with TestClient(app) as client:
        listing = client.get(f"/api/v1/projects/{project['id']}/reference-mappings")
        assert listing.status_code == 200
        assert listing.json()[0]["id"] == mapping["id"]

    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(project_reference_mappings)"
            )
        }
        assert "reference_kind" not in columns
        assert "reference_type" in columns
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "project_asset_mappings" not in tables

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_project_reference_mapping_accepts_unknown_reference_type(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        response = client.post(
            f"/api/v1/projects/{project['id']}/reference-mappings",
            json={
                "reference_type": "unknown",
                "reference_value": "SELECT * FROM orders",
                "target_identifier_kind": "logical_table",
                "target_value": "catalog:main:warehouse|main.warehouse.sales.orders",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["reference_type"] == "unknown"
        assert payload["reference_signature"]["reference_type"] == "unknown"

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_source_management_api_roundtrip(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

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
        assert sync_status["last_observed_at"] is not None
        assert sync_status["next_check_at"] is None
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

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_project_scan_queues_metadata_and_code_initialization(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    project_root = tmp_path / "project"
    metadata_dir = project_root / "metadata"
    functions_dir = project_root / "functions"
    metadata_dir.mkdir(parents=True)
    functions_dir.mkdir()
    (metadata_dir / "metadata.json").write_text(
        json.dumps({"connections": [{"name": "lake"}], "dataflows": [], "schema_hints": []}),
        encoding="utf-8",
    )
    (functions_dir / "transform.py").write_text("def transform(value):\n    return value\n", encoding="utf-8")
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()

        first = client.post(
            f"/api/v1/environments/{environment['id']}/datacoolie-project-sources",
            json={"project_uri": str(project_root)},
        ).json()
        assert first["summary"] == {
            "created": 2,
            "existing": 0,
            "errors": 0,
            "metadata_sources": 1,
            "code_artifacts": 1,
            "initialization_queued": 2,
        }
        assert first["errors"] == []
        registration_ids = {
            item["configured_location"]["registration_id"]
            for item in first["created"]
        }
        assert len(registration_ids) == 1
        assert all(
            item["configured_location"]["purpose"] == "project"
            and item["configured_location"]["input_uri"] == str(project_root)
            for item in first["created"]
        )
        assert client.get(f"/api/v1/environments/{environment['id']}/log-sources").json() == []

        with sqlite3.connect(db_path) as connection:
            assert connection.execute("select count(*) from metadata_materializations").fetchone()[0] == 1
            assert connection.execute("select count(*) from code_artifact_materializations").fetchone()[0] == 1
            assert connection.execute(
                "select count(*) from sync_jobs where job_type = 'initial_refresh'"
            ).fetchone()[0] == 2

        second = client.post(
            f"/api/v1/environments/{environment['id']}/datacoolie-project-sources",
            json={"project_uri": str(project_root)},
        ).json()
        assert second["summary"]["created"] == 0
        assert second["summary"]["existing"] == 2
        assert second["summary"]["initialization_queued"] == 2
        assert {
            item["configured_location"]["registration_id"]
            for item in second["existing"]
        } == registration_ids
        with sqlite3.connect(db_path) as connection:
            assert connection.execute(
                "select count(*) from sync_jobs where job_type = 'initial_refresh'"
            ).fetchone()[0] == 4

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_project_scan_preserves_remote_storage_binding(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.storage.adapters import StorageObject
    from datacoolie_studio.domains.storage.inventory import StorageInventory
    from datacoolie_studio.main import app

    class FakeRemoteProjectAdapter:
        def __init__(self):
            self.read_count = 0

        def canonical_uri(self, uri: str) -> str:
            return uri.rstrip("/")

        def inventory(self, request):
            uri = request.uri
            if uri.endswith("/metadata"):
                objects = [
                    StorageObject(
                        canonical_uri=f"{uri}/metadata.json",
                        name="metadata.json",
                        object_type="file",
                    )
                ]
            elif uri.endswith("/functions"):
                objects = [
                    StorageObject(
                        canonical_uri=f"{uri}/transform.py",
                        name="transform.py",
                        object_type="file",
                        size=32,
                    )
                ]
            else:
                objects = []
            return StorageInventory(
                objects=tuple(objects),
                completeness="complete",
                requests=1,
                pages=1,
                directories_visited=1,
                objects_inspected=len(objects),
                matching_objects=len(objects),
                retries=0,
                throttles=0,
                bytes_read=0,
                duration_ms=1,
            )

        def open_read(self, uri: str):
            self.read_count += 1
            assert uri.endswith("/metadata.json")
            return io.BytesIO(
                b'{"connections":[{"name":"lake"}],"dataflows":[],"schema_hints":[]}'
            )

    remote_adapter = FakeRemoteProjectAdapter()
    monkeypatch.setattr(
        "datacoolie_studio.domains.workspace.service.create_storage_adapter",
        lambda *_args, **_kwargs: remote_adapter,
    )

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        response = client.post(
            f"/api/v1/environments/{environment['id']}/datacoolie-project-sources",
            json={
                "project_uri": "/Volumes/catalog/schema/volume/project",
                "enabled": False,
                "storage": {
                    "provider": "dbfs",
                    "auth_mode": "ambient",
                    "options": {
                        "host": "https://workspace.cloud.databricks.com"
                    },
                },
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["summary"]["created"] == 2
        sources = [
            *client.get(
                f"/api/v1/environments/{environment['id']}/metadata-sources"
            ).json(),
            *client.get(
                f"/api/v1/environments/{environment['id']}/code-artifacts"
            ).json(),
        ]
        assert {source["storage"]["provider"] for source in sources} == {"dbfs"}
        assert all(
            source["uri"].startswith(
                "dbfs:/Volumes/catalog/schema/volume/project/"
            )
            for source in sources
        )
        assert all(
            source["storage"]["options"]["host"]
            == "https://workspace.cloud.databricks.com"
            for source in sources
        )

        onelake = client.post(
            f"/api/v1/environments/{environment['id']}/datacoolie-project-sources",
            json={
                "project_uri": (
                    "https://onelake.dfs.fabric.microsoft.com/Analytics/"
                    "Telemetry.Lakehouse/Files/project"
                ),
                "enabled": False,
                "storage": {
                    "provider": "onelake",
                    "auth_mode": "ambient",
                },
            },
        )
        assert onelake.status_code == 200, onelake.text
        assert onelake.json()["summary"]["created"] == 2
        onelake_sources = [
            source
            for source in [
                *client.get(
                    f"/api/v1/environments/{environment['id']}/metadata-sources"
                ).json(),
                *client.get(
                    f"/api/v1/environments/{environment['id']}/code-artifacts"
                ).json(),
            ]
            if source["storage"]["provider"] == "onelake"
        ]
        assert len(onelake_sources) == 2
        assert all(
            source["uri"].startswith(
                "abfss://Analytics@onelake.dfs.fabric.microsoft.com/"
                "Telemetry.Lakehouse/Files/project/"
            )
            for source in onelake_sources
        )
        assert remote_adapter.read_count == 0


def test_new_log_source_queues_validation_and_sync(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

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
        persisted = client.get(
            f"/api/v1/environments/{environment['id']}/log-sources"
        ).json()[0]
        assert persisted["latest_validation"]["status"] == "ok"
        status = _source_status(client, environment["id"], source["id"])
        assert status["status"] == "ok"
        assert status["latest_job"]["job_type"] == "auto_refresh"
        assert status["latest_job"]["status"] == "succeeded"
        with sqlite3.connect(db_path) as connection:
            assert connection.execute("select count(*) from sync_jobs").fetchone()[0] == 2

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_source_detail_routes_require_matching_environment_scope(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

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

    monkeypatch.delenv("DATACOOLIE_STUDIO_DB", raising=False)


def test_project_summary_api(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(SAMPLE_METADATA), "label": "metadata"},
        )
        client.post(
            f"/api/v1/environments/{env['id']}/log-sources",
            json={"uri": str(SAMPLE_LOGS), "label": "sample logs"},
        )

        summaries = client.get("/api/v1/projects/summary").json()
        assert summaries[0]["name"] == "demo"
        assert summaries[0]["environment_count"] == 1
        assert summaries[0]["metadata_source_count"] == 1
        assert summaries[0]["etl_log_path_count"] == 1
        assert summaries[0]["environments"][0]["name"] == "dev"

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_source_refresh_records_revision_and_sync_job(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

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
        assert status["latest_job"]["job_type"] == "auto_refresh"

        validation = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources/{source['id']}/validate"
        ).json()
        assert validation["status"] == "ok"
        refreshed = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources/{source['id']}/refresh"
        ).json()
        assert refreshed["status"] == "ok"
        assert refreshed["message"] == "Metadata source materialization refreshed"
        assert refreshed["revision"]["object_type"] == "file"
        assert refreshed["revision"]["content_hash"]
        assert refreshed["latest_job"]["status"] == "succeeded"
        assert refreshed["latest_job"]["job_type"] == "force_refresh"

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

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


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
    logs_dir = tmp_path / "etl_logs"
    logs_dir.mkdir()
    log_old = logs_dir / "old.job.jsonl"
    log_new = logs_dir / "new.job.jsonl"
    for path in (metadata_old, metadata_new):
        path.write_text(
            '{"connections": [], "dataflows": [], "schema_hints": []}', encoding="utf-8"
        )
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
        assert freshness["etl_log_path_count"] == 1
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

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_metadata_api_uses_current_materialization_and_auto_refreshes_stale_source(
    tmp_path: Path, monkeypatch
):
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
        env = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(metadata_path), "label": "metadata"},
        ).json()

        first = client.get(f"/api/v1/environments/{env['id']}/metadata").json()
        assert first["summary"]["dataflows"] == 1
        assert first["dataflows"][0]["name"] == "flow_v1"
        with sqlite3.connect(db_path) as connection:
            assert (
                connection.execute(
                    "select count(*) from metadata_materializations"
                ).fetchone()[0]
                == 1
            )
            initial_sync_job_count = connection.execute(
                "select count(*) from sync_jobs"
            ).fetchone()[0]

        original_read_metadata_file = metadata_service.read_metadata_file

        def fail_if_parsed(_uri: str):
            raise AssertionError("metadata file should not be parsed on cache hit")

        monkeypatch.setattr(metadata_service, "read_metadata_file", fail_if_parsed)
        second = client.get(f"/api/v1/environments/{env['id']}/metadata").json()
        assert second["dataflows"][0]["name"] == "flow_v1"
        with sqlite3.connect(db_path) as connection:
            assert (
                connection.execute(
                    "select count(*) from metadata_materializations"
                ).fetchone()[0]
                == 1
            )
            assert connection.execute(
                "select count(*) from sync_jobs"
            ).fetchone()[0] == initial_sync_job_count

        monkeypatch.setattr(
            metadata_service, "read_metadata_file", original_read_metadata_file
        )

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
            assert (
                connection.execute(
                    "select count(*) from metadata_materializations"
                ).fetchone()[0]
                == 1
            )
            assert connection.execute(
                "select count(*) from sync_jobs"
            ).fetchone()[0] == initial_sync_job_count + 1

        metadata_path.write_text("{ invalid json", encoding="utf-8")
        invalid = client.get(f"/api/v1/environments/{env['id']}/metadata").json()
        assert invalid["dataflows"][0]["name"] == "flow_v2"
        assert invalid["summary"]["errors"] == 1
        listed = client.get(
            f"/api/v1/environments/{env['id']}/metadata-sources"
        ).json()
        assert listed[0]["latest_validation"] is None

        metadata_path.unlink()
        last_good = client.get(f"/api/v1/environments/{env['id']}/metadata").json()
        assert last_good["dataflows"][0]["name"] == "flow_v2"
        assert last_good["summary"]["errors"] == 1
        assert last_good["errors"][0]["cache_status"] == "stale"
        status = _source_status(client, env["id"], source["id"])
        assert status["status"] == "error"

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_source_refresh_reports_missing_path_without_crashing(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

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
        assert refreshed["error"]["code"] == "not_found"
        assert refreshed["latest_job"]["status"] == "failed"
        listed = client.get(
            f"/api/v1/environments/{env['id']}/metadata-sources"
        ).json()
        assert listed[0]["latest_validation"] is None

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_source_uri_update_clears_read_check_and_sync_status(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

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
        assert status["latest_job"] is None

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_automatic_metadata_refresh_has_no_per_source_schedule(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {"connections": [{"name": "lake"}], "dataflows": [], "schema_hints": []}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.sync.scheduler import run_due_schedules_once
    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(metadata_path), "label": "metadata"},
        ).json()

        rejected = client.patch(
            f"/api/v1/environments/{env['id']}/metadata-sources/{source['id']}",
            json={"sync_schedule_enabled": True, "sync_interval_minutes": 15},
        )
        assert rejected.status_code == 422

        assert run_due_schedules_once() == 0
        assert client.post(
            f"/api/v1/environments/{env['id']}/sources/observe-local"
        ).json()["failed"] == 0
        status = _source_status(client, env["id"], source["id"])
        assert status["status"] == "ok"
        assert status["latest_job"]["job_type"] == "auto_refresh"
        with sqlite3.connect(db_path) as connection:
            assert (
                connection.execute(
                    "select count(*) from metadata_materializations"
                ).fetchone()[0]
                == 1
            )
            row = connection.execute("select last_scheduled_sync_at from environment_sources where id = ?", (source["id"],)).fetchone()
            assert row[0] is None

        assert client.post(
            f"/api/v1/environments/{env['id']}/sources/observe-local"
        ).json()["changed"] == 0

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_automatic_code_refresh_rebuilds_only_after_source_change(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    code_path = tmp_path / "functions"
    code_path.mkdir()
    module_path = code_path / "transform.py"
    module_path.write_text("def transform(value):\n    return value\n", encoding="utf-8")
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/code-artifacts",
            json={"uri": str(code_path), "label": "functions"},
        ).json()

        assert client.post(
            f"/api/v1/environments/{env['id']}/sources/observe-local"
        ).json()["failed"] == 0
        status = _source_status(client, env["id"], source["id"])
        assert status["status"] == "ok"
        assert status["latest_job"]["job_type"] == "auto_refresh"
        assert client.post(
            f"/api/v1/environments/{env['id']}/sources/observe-local"
        ).json()["changed"] == 0

        module_path.write_text("def transform(value):\n    return value * 2\n", encoding="utf-8")
        assert client.post(
            f"/api/v1/environments/{env['id']}/sources/observe-local"
        ).json()["changed"] == 1

        with sqlite3.connect(db_path) as connection:
            assert connection.execute("select count(*) from code_artifact_materializations").fetchone()[0] == 1

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_etl_log_path_refresh_records_directory_revision(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

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
        assert refreshed["revision"]["object_type"] == "directory"
        assert refreshed["revision"]["file_count"] > 0
        assert refreshed["latest_job"]["status"] == "succeeded"
        assert refreshed["latest_job"]["message"] == "Log source cache is current"
        assert analytics_path.exists()
        listed = client.get(f"/api/v1/environments/{env['id']}/log-sources").json()
        assert listed[0]["latest_validation"]["status"] == "ok"
        assert listed[0]["latest_validation"]["record_counts"]["job_jsonl_files"] > 0
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
                str(next(SAMPLE_LOGS.rglob("*.parquet")))
                .replace("\\", "/")
                .replace("'", "''")
            )
            source_types = {
                str(row[0]): _normalize_duckdb_type(str(row[1]))
                for row in connection.execute(
                    f"DESCRIBE SELECT * FROM read_parquet('{sample_dataflow_file}', union_by_name=true)"
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
            assert dataflow_columns["start_time"] == source_types["start_time"]
            assert dataflow_columns["source_end_time"] in {
                source_types["source_end_time"],
                "TIMESTAMP WITH TIME ZONE",
            }
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

        report = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/overview"
        ).json()
        assert report["schema_version"] == "monitoring-page.v9"
        assert report["page"] == "overview"
        assert set(report) == {
            "schema_version", "page", "summary", "health", "attention",
            "operations", "failures", "volume",
        }
        assert report["summary"]["dataflow_records"] > 0
        assert report["summary"]["job_records"] > 0
        assert report["summary"]["requested_grain"] == "auto"
        assert report["summary"]["effective_grain"] in {"hour", "day", "week", "month"}
        diagnostics_report = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/diagnostics"
        ).json()
        assert diagnostics_report["diagnostics"]["kpis"]["matched_job_ids"] > 0
        freshness_report = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/freshness"
        ).json()
        assert "freshness" in freshness_report
        assert "freshness" not in report
        filter_options = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/filter-options"
        ).json()
        assert filter_options["summary"]["source"] == "duckdb_filter_values"
        assert filter_options["options"]["operation_type"]
        filtered_report = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/overview?status=failed"
        ).json()
        assert (
            filtered_report["summary"]["dataflow_records"]
            < report["summary"]["dataflow_records"]
        )
        assert (
            filtered_report["operations"]["dataflow_kpis"]["failed"]
            == filtered_report["summary"]["dataflow_records"]
        )
        assert filtered_report["operations"]["dataflow_kpis"]["skipped"] == 0
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
        assert custom["summary"]["dataflow_records"] == 0
        dataflows = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/dataflows?limit=5"
        ).json()
        assert dataflows["summary"]["cache"] == "duckdb"
        assert dataflows["summary"]["records"] == 5
        assert (
            dataflows["summary"]["total_records"]
            == report["summary"]["dataflow_records"]
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
        assert (
            slow_dataflows["records"][0]["duration_seconds"]
            >= slow_dataflows["records"][-1]["duration_seconds"]
        )
        assert (
            fast_dataflows["records"][0]["duration_seconds"]
            <= fast_dataflows["records"][-1]["duration_seconds"]
        )
        jobs = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/jobs?limit=3&offset=2"
        ).json()
        assert jobs["summary"]["cache"] == "duckdb"
        assert jobs["summary"]["records"] == 3
        assert jobs["summary"]["offset"] == 2
        assert jobs["summary"]["total_records"] == report["summary"]["job_records"]
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
            f"/api/v1/environments/{env['id']}/monitoring/jobs?limit=5&sortBy=duration_seconds&sortDir=desc"
        ).json()
        assert (
            longest_jobs["records"][0]["duration_seconds"]
            >= longest_jobs["records"][-1]["duration_seconds"]
        )

        second_refresh = client.post(
            f"/api/v1/environments/{env['id']}/log-sources/{path['id']}/refresh",
            json={"mode": "incremental"},
        ).json()
        assert second_refresh["latest_job"]["message"] == "Log source cache is current"
        assert (
            second_refresh["latest_job"]["result"]["record_counts"]["parsed_files"] == 0
        )

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_base_log_path_reads_only_analyst_etl_folder(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    base = tmp_path / "logs"
    analyst_dataflows = base / "etl_logs" / "analyst" / "dataflow_run_log"
    analyst_jobs = base / "etl_logs" / "analyst" / "job_run_log"
    debug_dataflows = base / "etl_logs" / "debug_json" / "dataflow_run_log"
    debug_jobs = base / "etl_logs" / "debug_json" / "job_run_log"
    for folder in (analyst_dataflows, analyst_jobs, debug_dataflows, debug_jobs, base / "system_logs"):
        folder.mkdir(parents=True)

    parquet_sample = next(SAMPLE_LOGS.rglob("*.parquet"))
    job_sample = next((SAMPLE_LOGS / "job_run_log").rglob("*.jsonl"))
    for folder in (analyst_dataflows, debug_dataflows):
        shutil.copy2(parquet_sample, folder / parquet_sample.name)
    for folder in (analyst_jobs, debug_jobs):
        shutil.copy2(job_sample, folder / job_sample.name)

    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))
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

        expected_rows, expected_errors = read_dataflow_logs([str(base / "etl_logs" / "analyst")])
        assert not expected_errors
        available = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/dataflows?range=365d"
        )
        assert available.status_code == 200
        assert available.json()["summary"]["total_records"] == len(expected_rows)
        initial_status = _source_status(client, env["id"], source["id"])
        counts = initial_status["latest_job"]["result"]["record_counts"]
        assert counts["dataflow_parquet_files"] == 1
        assert counts["job_jsonl_files"] == 1

        refreshed = client.post(
            f"/api/v1/environments/{env['id']}/log-sources/{source['id']}/refresh",
            json={"mode": "incremental"},
        ).json()
        assert refreshed["latest_job"]["message"] == "Log source cache is current"
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
        assert all("/etl_logs/analyst/" in path for path in manifest_paths)
        assert all("debug_json" not in path for path in manifest_paths)

        freshness = client.get(f"/api/v1/environments/{env['id']}/freshness").json()
        assert freshness["etl_logs"]["status"] == "current"

        # Freshness reads are DB-only. Logs are observed periodically, while
        # their actual sync remains manual or schedule-driven.
        from datacoolie_studio.db.models import EnvironmentSource
        from datacoolie_studio.db.session import create_session
        from datacoolie_studio.domains.source_observation.repository import (
            claim_due_observation_ids,
            reset_observation,
        )
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

        # Changing an ignored debug_json file does not affect the synced analyst
        # cache, so freshness stays current.
        debug_file = debug_jobs / job_sample.name
        debug_file.write_text(debug_file.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        log_ingestion.invalidate_pending_changes(int(source["id"]))
        assert observe_log_change() is False
        freshness_after_debug_change = client.get(f"/api/v1/environments/{env['id']}/freshness").json()
        assert freshness_after_debug_change["etl_logs"]["status"] == "current"

        # Changing a synced analyst log file makes the cache out of date; the header
        # reflects it as not synced without any auto-sync.
        analyst_file = analyst_jobs / job_sample.name
        analyst_file.write_text(analyst_file.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        log_ingestion.invalidate_pending_changes(int(source["id"]))
        assert observe_log_change() is True
        freshness_after_analyst_change = client.get(f"/api/v1/environments/{env['id']}/freshness").json()
        assert freshness_after_analyst_change["etl_logs"]["status"] == "not_cached"

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_delete_etl_log_path_purges_duckdb_cache(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    scheduled_logs = tmp_path / "scheduled-logs"
    shutil.copytree(SAMPLE_LOGS, scheduled_logs)
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

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
        assert unchanged_status["latest_job"]["job_type"] == "manual_refresh"
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
        assert scheduled_status["latest_job"]["job_type"] == "scheduled_refresh"

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

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_delete_project_purges_disposable_caches(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.logs import ingestion as log_ingestion
    from datacoolie_studio.domains.read_models.contracts import ResultCacheKey
    from datacoolie_studio.domains.read_models.sqlite_store import SqliteResultCacheStore
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
            json={"uri": str(SAMPLE_LOGS), "label": "sample logs"},
        ).json()
        source_id = int(path["id"])

        refreshed = client.post(
            f"/api/v1/environments/{env['id']}/log-sources/{source_id}/refresh",
            json={"mode": "incremental"},
        ).json()
        assert refreshed["status"] == "ok"

        result_cache = SqliteResultCacheStore()
        result_cache.put(
            ResultCacheKey(
                environment_id=int(env["id"]),
                namespace="overview",
                parameters_fingerprint="parameters",
                input_fingerprint="input",
                producer_version="v1",
            ),
            {"cached": True},
        )
        assert result_cache.entry_count(int(env["id"]), "overview") == 1

        import duckdb

        with duckdb.connect(str(analytics_path), read_only=True) as connection:
            assert (
                connection.execute(
                    "select count(*) from etl_dataflow_runs where _source_id = ?",
                    [source_id],
                ).fetchone()[0]
                > 0
            )

        deleted = client.delete(f"/api/v1/projects/{project['id']}")
        assert deleted.status_code == 204
        assert result_cache.entry_count(int(env["id"]), "overview") == 0

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

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_environment_api_accepts_custom_name_and_rejects_invalid_name(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        custom = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "UAT-1"}
        ).json()
        assert custom["name"] == "UAT-1"
        duplicate = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "uat-1"}
        )
        assert duplicate.status_code == 409
        invalid = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "uat env"}
        )
        assert invalid.status_code == 422

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_monitoring_page_api_roundtrip(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.analytics import access as analytics_access
    from datacoolie_studio.domains.logs import ingestion as log_ingestion
    from datacoolie_studio.main import app

    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_maintenance, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_access, "analytics_database_path", lambda: analytics_path)

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/log-sources",
            json={"uri": str(SAMPLE_LOGS), "label": "sample logs"},
        ).json()
        paths = client.get(f"/api/v1/environments/{env['id']}/log-sources").json()
        validation = client.post(
            f"/api/v1/environments/{env['id']}/log-sources/{paths[0]['id']}/validate"
        ).json()
        assert validation["status"] == "ok"
        assert validation["record_counts"]["job_jsonl_files"] > 0
        paths = client.get(f"/api/v1/environments/{env['id']}/log-sources").json()
        assert paths[0]["latest_validation"]["record_counts"]["job_jsonl_files"] > 0
        refreshed = client.post(
            f"/api/v1/environments/{env['id']}/log-sources/{source['id']}/refresh",
            json={"mode": "incremental"},
        )
        assert refreshed.status_code == 200, refreshed.text

        original_connect = analytics_access.connect
        connection_calls = 0

        def counted_connect(*args, **kwargs):
            nonlocal connection_calls
            connection_calls += 1
            return original_connect(*args, **kwargs)

        monkeypatch.setattr(analytics_access, "connect", counted_connect)
        for endpoint in ("filter-options", "dataflows?limit=1", "jobs?limit=1"):
            before = connection_calls
            response = client.get(
                f"/api/v1/environments/{env['id']}/monitoring/{endpoint}"
            )
            assert response.status_code == 200, response.text
            assert connection_calls - before <= 1

        before = connection_calls
        report_response = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/overview"
        )
        # A result-cache hit opens no analytics connection; a miss uses one shared reader.
        assert connection_calls - before <= 1
        report = report_response.json()
        assert report["summary"]["dataflow_records"] > 0
        assert report["summary"]["job_records"] > 0
        assert report["summary"]["requested_grain"] == "auto"
        assert report["summary"]["effective_grain"] in {"hour", "day", "week", "month"}
        assert report["summary"]["latest_log_at"].endswith(("+00:00", "Z"))
        assert report["summary"]["latest_job_log_at"].endswith(("+00:00", "Z"))
        assert report["summary"]["latest_dataflow_log_at"].endswith(("+00:00", "Z"))
        assert report["operations"]["kpis"]["total_jobs"] > 0
        assert report["operations"]["dataflows_by_date_status"]
        assert {"bucket", "bucket_start", "bucket_end", "grain"} <= set(
            report["operations"]["dataflows_by_date_status"][0]
        )
        job_operation_types = {
            item["operation_type"]
            for item in report["operations"]["job_runs_by_dataflow_operation_type"]
        }
        assert "etl" in job_operation_types
        assert job_operation_types != {"unknown"}
        assert report["volume"]["rows_by_date"]
        assert report["volume"]["bytes_by_date"]
        weekly_report = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/overview?range=90d&grain=auto"
        ).json()
        assert weekly_report["summary"]["requested_grain"] == "auto"
        assert weekly_report["summary"]["effective_grain"] in {
            "hour",
            "day",
            "week",
            "month",
        }
        performance_response = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/performance"
        )
        performance_report = performance_response.json()
        maintenance_report = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/maintenance"
        ).json()
        volume_report = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/volume"
        ).json()
        failures_report = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/failures"
        ).json()
        assert performance_report["performance"]["kpis"]["p95_duration_seconds"] >= 0
        assert performance_report["schema_version"] == "monitoring-page.v9"
        assert performance_report["performance"]["kpis"]["slowest_run_start_time"]
        assert performance_report["performance"]["kpis"]["slowest_run_end_time"]
        assert performance_report["performance"]["slowest_dataflow_profiles"]
        assert "investigation_queue" not in performance_report["performance"]
        assert "slowest_dataflows_by_p95" not in performance_report["performance"]
        etag = performance_response.headers["etag"]
        unchanged = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/performance",
            headers={"If-None-Match": etag},
        )
        assert unchanged.status_code == 304
        assert unchanged.content == b""
        evidence = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/performance/evidence",
            params={"limit": 2, "offset": 0, "sortBy": "duration_seconds", "sortDir": "desc"},
        ).json()
        assert len(evidence["records"]) <= 2
        assert evidence["summary"]["total_records"] >= len(evidence["records"])
        assert maintenance_report["maintenance"]["kpis"]["total_maintenance_runs"] >= 0
        for bucket in maintenance_report["maintenance"]["status_by_date"]:
            assert {"succeeded", "failed", "skipped", "running", "pending", "unknown", "total"} <= set(bucket)
            assert bucket["total"] == sum(
                bucket[status] for status in ("succeeded", "failed", "skipped", "running", "pending", "unknown")
            )
        for bucket in maintenance_report["maintenance"]["reclaim_by_date"]:
            assert {"bytes_reclaimed", "bytes_saved", "files_removed", "runs"} <= set(bucket)
        assert sum(
            item["count"] for item in failures_report["failures"]["error_categories"]
        ) == failures_report["failures"]["kpis"]["failed_dataflows"]
        for failed_record in failures_report["failures"]["failed_records"]:
            assert isinstance(failed_record["failure_tags"], list)
            assert len(failed_record["failure_tags"]) <= 5
            assert "failure_rule_id" in failed_record
        maintenance_evidence = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/maintenance/evidence",
            params={"limit": 100, "offset": 0},
        ).json()
        for table in maintenance_evidence["records"]:
            assert isinstance(table["upstream_dataflows"], list)
            assert table["upstream_run_count"] >= len(table["upstream_dataflows"])
            if table["upstream_run_count"]:
                assert table["upstream_dataflows"]

        first_job = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/jobs?limit=1"
        ).json()["records"][0]
        canonical_job = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/jobs",
            params={
                "range": "all",
                "investigateKind": "job_id",
                "investigateValue": first_job["job_id"],
                "limit": 1,
                "offset": 0,
            },
        ).json()
        assert canonical_job["records"][0]["job_id"] == first_job["job_id"]
        child_page = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/dataflows",
            params={
                "limit": 2,
                "offset": 0,
                "sortBy": "duration_seconds",
                "sortDir": "desc",
                "investigateKind": "job_id",
                "investigateValue": first_job["job_id"],
            },
        ).json()
        assert child_page["summary"]["limit"] == 2
        assert child_page["summary"]["offset"] == 0
        assert len(child_page["records"]) <= 2
        assert child_page["summary"]["total_records"] >= len(child_page["records"])
        assert child_page["records"]
        first_child = child_page["records"][0]
        assert first_child["stage"]
        assert first_child["operation_type"]
        filtered_job = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/jobs",
            params={
                "range": "all",
                "stage": first_child["stage"],
                "operationType": first_child["operation_type"],
                "investigateKind": "job_id",
                "investigateValue": first_job["job_id"],
                "limit": 1,
                "offset": 0,
            },
        ).json()
        assert filtered_job["summary"]["total_records"] == 1
        assert filtered_job["records"][0]["job_id"] == first_job["job_id"]
        mismatched_job = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/jobs",
            params={
                "range": "all",
                "stage": first_child["stage"],
                "operationType": "__no_matching_operation__",
                "investigateKind": "job_id",
                "investigateValue": first_job["job_id"],
                "limit": 1,
                "offset": 0,
            },
        ).json()
        assert mismatched_job["summary"]["total_records"] == 0
        if child_page["summary"]["total_records"] > 2:
            next_child_page = client.get(
                f"/api/v1/environments/{env['id']}/monitoring/dataflows",
                params={
                    "limit": 2,
                    "offset": 2,
                    "sortBy": "duration_seconds",
                    "sortDir": "desc",
                    "investigateKind": "job_id",
                    "investigateValue": first_job["job_id"],
                },
            ).json()
            first_ids = {row["dataflow_run_id"] for row in child_page["records"]}
            next_ids = {row["dataflow_run_id"] for row in next_child_page["records"]}
            assert first_ids.isdisjoint(next_ids)

        from datacoolie_studio.domains.monitoring import query_service

        assert not hasattr(query_service, "_monitoring_rows")
        environment_overview = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/environment-overview"
        ).json()
        assert environment_overview["summary"]["job_records"] == report["summary"]["job_records"]
        assert environment_overview["operations"]["kpis"]["total_failures"] == report["operations"]["kpis"]["total_failures"]
        assert environment_overview["operations"]["dataflow_kpis"]["success_rate"] == report["operations"]["dataflow_kpis"]["success_rate"]
        assert environment_overview["operations"]["jobs_by_date_status"] == report["operations"]["jobs_by_date_status"]
        assert environment_overview["performance"]["slowest_dataflows"] == []
        assert environment_overview["maintenance"]["per_table"] == []

        overview_summary = client.get(f"/api/v1/environments/{env['id']}/overview")
        assert overview_summary.status_code == 200, overview_summary.text
        overview_payload = overview_summary.json()
        assert overview_payload["monitoring"]["job_records"] == environment_overview["summary"]["job_records"]
        assert overview_payload["monitoring"]["total_failures"] == environment_overview["operations"]["kpis"]["total_failures"]
        assert overview_payload["monitoring"]["dataflow_success_rate"] == environment_overview["operations"]["dataflow_kpis"]["success_rate"]
        assert overview_payload["monitoring"]["latest_log_at"] == environment_overview["summary"]["latest_log_at"]
        assert overview_payload["monitoring"]["date_range"] == environment_overview["summary"]["date_range"]
        assert overview_payload["monitoring"]["errors"] == environment_overview["errors"]

        assert report["health"]["status"]
        assert isinstance(report["attention"], list)
        assert report["failures"]["error_categories"]
        assert {
            item["category"] for item in report["failures"]["error_categories"]
        } != {"Unspecified"}
        assert volume_report["volume"]["rows_by_date"]
        assert volume_report["volume"]["bytes_by_date"]
        assert sum(
            item["est_rows_written"] for item in report["volume"]["rows_by_date"]
        ) == volume_report["volume"]["kpis"]["total_est_rows_written"]
        assert evidence["records"]
        assert "performance" not in report
        assert client.get(
            f"/api/v1/environments/{env['id']}/monitoring/overview"
        ).status_code == 404

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_environment_overview_summary_cache_and_lineage_projection(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps({
            "connections": [{
                "name": "lake",
                "connection_type": "lakehouse",
                "format": "delta",
                "catalog": "main",
                "database": "warehouse",
            }],
            "dataflows": [{
                "name": "load_orders",
                "stage": "bronze",
                "load_type": "append",
                "source": {"connection_name": "lake", "schema_name": "raw", "table": "orders"},
                "destination": {"connection_name": "lake", "schema_name": "bronze", "table": "orders"},
            }],
            "schema_hints": [],
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.lineage import service as lineage_service
    from datacoolie_studio.domains.overview import service as overview_service
    from datacoolie_studio.main import app

    calls = 0
    original_summary_builder = overview_service.build_lineage_overview_summary

    def count_summary_builds(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_summary_builder(*args, **kwargs)

    def reject_full_graph_builder(*_args, **_kwargs):
        raise AssertionError("Environment Overview must not call the full Lineage graph builder")

    monkeypatch.setattr(overview_service, "build_lineage_overview_summary", count_summary_builds)
    monkeypatch.setattr(lineage_service, "load_or_build_lineage_graph", reject_full_graph_builder)

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "overview-demo"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        source = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={"uri": str(metadata_path), "label": "metadata"},
        ).json()

        first = client.get(f"/api/v1/environments/{environment['id']}/overview")
        assert first.status_code == 200, first.text
        payload = first.json()
        assert payload["schema_version"] == "environment-overview.v2"
        assert payload["cache"]["state"] == "miss"
        assert payload["metadata"]["dataflows"] == 1
        assert payload["metadata"]["stages"] == [{"name": "bronze", "count": 1}]
        assert payload["lineage"]["dataflows"] == 1
        assert set(payload["lineage"]) >= {
            "automatic_references",
            "manual_references",
            "unresolved_references",
            "automatic_dependencies",
            "manual_dependencies",
            "unresolved_dependencies",
        }
        assert calls == 1

        second = client.get(f"/api/v1/environments/{environment['id']}/overview")
        assert second.status_code == 200, second.text
        assert second.json()["cache"]["state"] == "hit"
        assert calls == 1

        changed = client.patch(
            f"/api/v1/environments/{environment['id']}/metadata-sources/{source['id']}",
            json={"label": "renamed metadata"},
        )
        assert changed.status_code == 200, changed.text
        third = client.get(f"/api/v1/environments/{environment['id']}/overview")
        assert third.status_code == 200, third.text
        assert third.json()["cache"]["state"] == "miss"
        assert calls == 2

        mapping = client.post(
            f"/api/v1/projects/{project['id']}/reference-mappings",
            json={
                "reference_type": "table_reference",
                "reference_value": "orders",
                "target_identifier_kind": "logical_table",
                "target_value": "catalog:main:warehouse|main.warehouse.raw.orders",
            },
        )
        assert mapping.status_code == 200, mapping.text
        after_mapping = client.get(f"/api/v1/environments/{environment['id']}/overview")
        assert after_mapping.status_code == 200, after_mapping.text
        assert after_mapping.json()["cache"]["state"] == "miss"
        assert calls == 3

    with sqlite3.connect(tmp_path / "read-models.sqlite3") as connection:
        assert connection.execute("select count(*) from result_cache_entries where namespace = 'environment-overview'").fetchone()[0] == 1

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)
