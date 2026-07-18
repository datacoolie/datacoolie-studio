from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path


def test_lineage_graph_cache_avoids_repeat_code_analysis(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    metadata_path = tmp_path / "metadata.json"
    artifact_root = tmp_path / "artifact"
    package = artifact_root / "src" / "functions"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "sources.py").write_text(
        """
def read_orders(engine, source):
    return engine.execute_sql("SELECT * FROM raw.orders")
""",
        encoding="utf-8",
    )
    metadata_path.write_text(
        json.dumps({
            "connections": [{
                "name": "lake",
                "connection_type": "lakehouse",
                "format": "delta",
                "catalog": "main",
                "database": "warehouse",
            }],
            "dataflows": [
                {
                    "name": "produce_orders",
                    "source": {"connection_name": "lake", "schema_name": "raw", "table": "seed"},
                    "destination": {"connection_name": "lake", "schema_name": "raw", "table": "orders"},
                },
                {
                    "name": "read_orders",
                    "source": {
                        "connection_name": "lake",
                        "python_function": "functions.sources.read_orders",
                    },
                    "destination": {"connection_name": "lake", "schema_name": "curated", "table": "orders"},
                },
            ],
            "schema_hints": [],
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.lineage import service as lineage_service
    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={"uri": str(metadata_path), "label": "metadata"},
        )
        artifact = client.post(
            f"/api/v1/environments/{environment['id']}/code-artifacts",
            json={
                "uri": str(artifact_root),
                "label": "functions",
                "source_config": {"artifact_type": "directory", "module_roots": ["src"]},
            },
        ).json()
        client.post(f"/api/v1/environments/{environment['id']}/metadata-sources/1/refresh")
        client.post(f"/api/v1/environments/{environment['id']}/code-artifacts/{artifact['id']}/refresh")

        first_response = client.get(f"/api/v1/environments/{environment['id']}/lineage")
        first = first_response.json()
        assert first["summary"]["resolved_dependencies"] == 1
        assert first_response.headers["etag"]
        assert client.get(
            f"/api/v1/environments/{environment['id']}/lineage",
            headers={"If-None-Match": first_response.headers["etag"]},
        ).status_code == 304
        with sqlite3.connect(db_path) as connection:
            assert connection.execute("select count(*) from environment_read_model_cache_entries where model_key = 'lineage.graph'").fetchone()[0] == 1

        def fail_if_analyzed(*_args, **_kwargs):
            raise AssertionError("unchanged lineage should use the cached graph")

        monkeypatch.setattr(lineage_service, "analyze_code_artifact_function", fail_if_analyzed)
        second = client.get(f"/api/v1/environments/{environment['id']}/lineage").json()
        assert second == first

        client.delete(f"/api/v1/environments/{environment['id']}/code-artifacts/{artifact['id']}")
        with sqlite3.connect(db_path) as connection:
            assert connection.execute("select count(*) from environment_read_model_cache_entries where model_key = 'lineage.graph'").fetchone()[0] == 0

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_lineage_graph_cache_fingerprint_includes_reference_mappings(tmp_path: Path, monkeypatch):
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
            "dataflows": [
                {
                    "name": "seed_sales",
                    "source": {"connection_name": "lake", "schema_name": "raw", "table": "seed"},
                    "destination": {"connection_name": "lake", "schema_name": "sales", "table": "orders"},
                },
                {
                    "name": "seed_archive",
                    "source": {"connection_name": "lake", "schema_name": "raw", "table": "seed"},
                    "destination": {"connection_name": "lake", "schema_name": "archive", "table": "orders"},
                },
                {
                    "name": "query_orders",
                    "source": {"connection_name": "lake", "query": "SELECT * FROM orders"},
                    "destination": {"connection_name": "lake", "schema_name": "curated", "table": "orders"},
                },
            ],
            "schema_hints": [],
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={"uri": str(metadata_path), "label": "metadata"},
        )
        client.post(f"/api/v1/environments/{environment['id']}/metadata-sources/1/refresh")

        first = client.get(f"/api/v1/environments/{environment['id']}/lineage").json()
        assert first["summary"]["ambiguous_dependencies"] == 1
        with sqlite3.connect(db_path) as connection:
            assert connection.execute("select count(*) from environment_read_model_cache_entries where model_key = 'lineage.graph'").fetchone()[0] == 1

        mapping = client.post(
            f"/api/v1/projects/{project['id']}/reference-mappings",
            json={
                "reference_type": "table_reference",
                "reference_value": "orders",
                "target_identifier_kind": "logical_table",
                "target_value": "catalog:main:warehouse|main.warehouse.sales.orders",
            },
        )
        assert mapping.status_code == 200

        second = client.get(f"/api/v1/environments/{environment['id']}/lineage").json()
        assert second["summary"]["resolved_manual_dependencies"] == 1
        with sqlite3.connect(db_path) as connection:
            assert connection.execute("select count(*) from environment_read_model_cache_entries where model_key = 'lineage.graph'").fetchone()[0] == 1

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)
