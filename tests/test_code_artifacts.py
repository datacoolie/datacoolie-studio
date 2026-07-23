from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from zipfile import ZipFile

import pytest


def test_directory_index_is_static_and_respects_module_roots(tmp_path: Path):
    from datacoolie_studio.domains.code_artifacts.indexer import build_artifact_index

    package = tmp_path / "src" / "demo"
    package.mkdir(parents=True)
    marker = tmp_path / "executed.txt"
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "pipeline.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    (tmp_path / "outside.py").write_text("VALUE = 1\n", encoding="utf-8")

    indexed = build_artifact_index(str(tmp_path), "directory", ["src"])

    assert marker.exists() is False
    assert sorted(indexed["modules"]) == ["demo", "demo.pipeline"]
    assert indexed["manifest"]["python_files"] == 3
    assert indexed["manifest"]["modules"] == 2


def test_archive_index_rejects_unsafe_member_even_when_not_python(tmp_path: Path):
    from datacoolie_studio.domains.code_artifacts.indexer import ArtifactIndexError, build_artifact_index

    archive_path = tmp_path / "unsafe.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("package/module.py", "VALUE = 1\n")
        archive.writestr("../README.txt", "unsafe")

    with pytest.raises(ArtifactIndexError, match="Unsafe archive member path"):
        build_artifact_index(str(archive_path), "zip")


def test_code_artifact_api_lifecycle(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    artifact_root = tmp_path / "artifact"
    package = artifact_root / "src" / "demo"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "pipeline.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        artifact = client.post(
            f"/api/v1/environments/{environment['id']}/code-artifacts",
            json={
                "uri": str(artifact_root),
                "label": "pipeline code",
                "source_config": {"artifact_type": "directory", "module_roots": ["src"]},
            },
        ).json()

        assert artifact["source_config"]["artifact_type"] == "directory"
        assert artifact["source_config"]["module_roots"] == ["src"]

        refreshed = client.post(f"/api/v1/environments/{environment['id']}/code-artifacts/{artifact['id']}/refresh").json()
        assert refreshed["status"] == "ok"
        assert refreshed["revision"]["artifact_type"] == "directory"
        assert refreshed["revision"]["python_files"] == 2

        listed = client.get(f"/api/v1/environments/{environment['id']}/code-artifacts").json()
        assert listed[0]["latest_validation"]["status"] == "ok"
        assert listed[0]["latest_validation"]["record_counts"]["modules"] == 2

        validation = client.post(f"/api/v1/environments/{environment['id']}/code-artifacts/{artifact['id']}/validate").json()
        assert validation["status"] == "ok"
        assert validation["record_counts"] == {"python_files": 2, "modules": 2}

        with sqlite3.connect(db_path) as connection:
            assert connection.execute("select count(*) from code_artifact_materializations").fetchone()[0] == 1
            assert connection.execute("select count(*) from source_revisions").fetchone()[0] == 1
            # Initial auto-materialization plus the explicit manual refresh.
            assert connection.execute("select count(*) from sync_jobs").fetchone()[0] == 2

        impact = client.get(f"/api/v1/environments/{environment['id']}/code-artifacts/{artifact['id']}/delete-impact")
        assert impact.status_code == 200
        impact_body = impact.json()
        assert impact_body["metadata_file_deleted"] is False
        assert "original code artifact will not be deleted" in impact_body["summary"]
        impact_counts = {item["kind"]: item["count"] for item in impact_body["impacts"]}
        assert impact_counts["materialization"] == 1
        assert impact_counts["source_revision"] == 1
        assert impact_counts["sync_job"] == 2

        response = client.delete(f"/api/v1/environments/{environment['id']}/code-artifacts/{artifact['id']}")
        assert response.status_code == 204
        with sqlite3.connect(db_path) as connection:
            assert connection.execute("select count(*) from code_artifact_materializations").fetchone()[0] == 0
            assert connection.execute("select count(*) from source_revisions").fetchone()[0] == 0
            assert connection.execute("select count(*) from sync_jobs").fetchone()[0] == 0

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)
