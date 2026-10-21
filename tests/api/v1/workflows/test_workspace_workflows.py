from __future__ import annotations

import sqlite3
from pathlib import Path

from datacoolie_studio.domains.analytics import access as analytics_access
from datacoolie_studio.domains.analytics import maintenance as analytics_maintenance
from tests.api.v1.api_support import (
    SAMPLE_LOGS,
    SAMPLE_METADATA,
)


def test_workspace_api_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

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


def test_environment_context_is_narrow_and_versions_dependencies(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

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


def test_project_reference_mapping_crud_and_project_cascade(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "studio.db"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

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


def test_project_reference_mapping_migrates_legacy_reference_kind_column(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "studio.db"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

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
        # This fixture represents a pre-Alembic workspace. Legacy shape
        # transformations are intentionally only run on the unversioned bridge path.
        connection.execute("DROP TABLE alembic_version")

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


def test_project_reference_mapping_accepts_unknown_reference_type(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

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


def test_project_summary_api(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

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
        assert summaries[0]["log_source_count"] == 1
        assert summaries[0]["environments"][0]["name"] == "dev"


def test_delete_project_purges_disposable_caches(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

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


def test_environment_api_accepts_custom_name_and_rejects_invalid_name(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

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
