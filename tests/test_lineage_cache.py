from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path


def test_lineage_snapshot_avoids_repeat_code_analysis(tmp_path: Path, monkeypatch):
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

        first = client.get(f"/api/v1/environments/{environment['id']}/lineage").json()
        assert first["summary"]["resolved_dependencies"] == 1
        with sqlite3.connect(db_path) as connection:
            assert connection.execute("select count(*) from lineage_snapshots").fetchone()[0] == 1

        def fail_if_analyzed(*_args, **_kwargs):
            raise AssertionError("unchanged lineage should use the cached snapshot")

        monkeypatch.setattr(lineage_service, "analyze_code_artifact_function", fail_if_analyzed)
        second = client.get(f"/api/v1/environments/{environment['id']}/lineage").json()
        assert second == first

        client.delete(f"/api/v1/code-artifacts/{artifact['id']}")
        with sqlite3.connect(db_path) as connection:
            assert connection.execute("select count(*) from lineage_snapshots").fetchone()[0] == 0

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)
