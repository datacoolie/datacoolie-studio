from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from zipfile import ZipFile



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
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

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


def test_project_scan_auto_accepts_only_existing_component_roots(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    project_root = tmp_path / "project"
    sql_root = project_root / "sql"
    sql_root.mkdir(parents=True)
    (sql_root / "orders.sql").write_text("SELECT 1;\n", encoding="utf-8")
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "sql-only"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        endpoint = f"/api/v1/environments/{environment['id']}/datacoolie-project-sources"

        first = client.post(
            endpoint,
            json={"project_uri": str(project_root), "enabled": False},
        )

        assert first.status_code == 200, first.text
        payload = first.json()
        assert payload["summary"]["created"] == 1
        assert payload["summary"]["sql_sources"] == 1
        assert payload["errors"] == []
        assert [item["source_kind"] for item in payload["created"]] == ["sql"]
        assert client.get(
            f"/api/v1/environments/{environment['id']}/metadata-sources"
        ).json() == []
        assert client.get(
            f"/api/v1/environments/{environment['id']}/code-artifacts"
        ).json() == []
        assert client.get(
            f"/api/v1/environments/{environment['id']}/sql-sources"
        ).json()[0]["source_config"]["discovery_mode"] == "datacoolie_project"


def test_project_scan_accepts_multiple_sql_and_code_fallback_roots(
    tmp_path: Path,
    monkeypatch,
):
    db_path = tmp_path / "studio.db"
    project_root = tmp_path / "project"
    sql_roots = [project_root / "queries" / "core", project_root / "queries" / "finance"]
    code_roots = [project_root / "functions" / "core", project_root / "functions" / "finance"]
    legacy_root = project_root / "legacy"
    legacy_sql_root = legacy_root / "custom-sql"
    legacy_code_root = legacy_root / "custom-functions"
    for index, root in enumerate(sql_roots):
        root.mkdir(parents=True)
        (root / f"query_{index}.sql").write_text("SELECT 1;\n", encoding="utf-8")
    for index, root in enumerate(code_roots):
        root.mkdir(parents=True)
        (root / f"transform_{index}.py").write_text(
            f"def transform_{index}(value):\n    return value\n",
            encoding="utf-8",
        )
    legacy_sql_root.mkdir(parents=True)
    (legacy_sql_root / "legacy.sql").write_text("SELECT 2;\n", encoding="utf-8")
    legacy_code_root.mkdir(parents=True)
    (legacy_code_root / "legacy.py").write_text(
        "def legacy(value):\n    return value\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "multi-root"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        endpoint = f"/api/v1/environments/{environment['id']}/datacoolie-project-sources"

        response = client.post(
            endpoint,
            json={
                "project_uri": str(project_root),
                "sql_subpaths": ["queries/core", "queries/finance"],
                "code_subpaths": ["functions/core", "functions/finance"],
                "enabled": False,
            },
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["summary"]["created"] == 4
        assert payload["summary"]["sql_sources"] == 2
        assert payload["summary"]["code_artifacts"] == 2
        assert payload["errors"] == []
        assert [item["source_kind"] for item in payload["created"]].count("sql") == 2
        assert [item["source_kind"] for item in payload["created"]].count("code") == 2

        sql_sources = client.get(
            f"/api/v1/environments/{environment['id']}/sql-sources"
        ).json()
        assert {item["uri"] for item in sql_sources} == {
            str(root) for root in sql_roots
        }
        assert all(
            item["source_config"]["sql_root_uris"] == [str(root) for root in sql_roots]
            for item in sql_sources
        )

        code_artifacts = client.get(
            f"/api/v1/environments/{environment['id']}/code-artifacts"
        ).json()
        assert {item["uri"] for item in code_artifacts} == {
            str(root) for root in code_roots
        }
        assert all(
            item["source_config"]["functions_root_uris"] == [str(root) for root in code_roots]
            for item in code_artifacts
        )
        sql_location = sql_sources[0]["configured_location"]
        code_location = code_artifacts[0]["configured_location"]
        assert sql_location["input_locations"]["sql_root_uri"] == [
            str(root) for root in sql_roots
        ]
        assert sql_location["canonical_locations"]["sql_root_uri"] == sorted(
            str(root) for root in sql_roots
        )
        assert code_location["input_locations"]["code_uri"] == [
            str(root) for root in code_roots
        ]
        assert code_location["canonical_locations"]["code_uri"] == sorted(
            str(root) for root in code_roots
        )

        scalar_response = client.post(
            endpoint,
            json={
                "project_uri": str(legacy_root),
                "sql_subpath": "custom-sql",
                "code_subpath": "custom-functions",
                "enabled": False,
            },
        )
        assert scalar_response.status_code == 422, scalar_response.text
        assert {error["loc"][-1] for error in scalar_response.json()["detail"]} == {
            "sql_subpath",
            "code_subpath",
        }

def test_project_scan_reconciles_removed_code_artifacts_without_hard_delete(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    project_root = tmp_path / "project"
    functions_dir = project_root / "functions"
    functions_dir.mkdir(parents=True)
    helper = functions_dir / "helper.py"
    helper.write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        endpoint = f"/api/v1/environments/{environment['id']}/datacoolie-project-sources"

        first = client.post(endpoint, json={"project_uri": str(project_root), "enabled": False}).json()
        assert first["summary"]["created"] == 1
        source_id = first["created"][0]["id"]

        helper.unlink()
        second = client.post(endpoint, json={"project_uri": str(project_root), "enabled": False}).json()
        assert second["summary"]["stale"] == 1
        assert second["stale"][0]["id"] == source_id
        assert second["stale"][0]["status"] == "stale"

        listed = client.get(
            f"/api/v1/environments/{environment['id']}/code-artifacts"
        ).json()
        assert len(listed) == 1
        assert listed[0]["id"] == source_id
        assert listed[0]["enabled"] is False
        assert listed[0]["source_config"]["discovery_state"] == "missing"

        helper.write_text("VALUE = 2\n", encoding="utf-8")
        third = client.post(endpoint, json={"project_uri": str(project_root), "enabled": False}).json()
        assert third["summary"].get("stale", 0) == 0
        assert third["summary"]["existing"] == 1
        restored = client.get(
            f"/api/v1/environments/{environment['id']}/code-artifacts"
        ).json()[0]
        assert restored["id"] == source_id
        assert restored["enabled"] is False
        assert "discovery_state" not in restored["source_config"]


def test_project_scan_reconciles_managed_metadata_when_marker_changes_root(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "studio.db"
    project_root = tmp_path / "project"
    default_metadata = project_root / "metadata"
    authored_metadata = project_root / "authored" / "metadata"
    default_metadata.mkdir(parents=True)
    authored_metadata.mkdir(parents=True)
    metadata_payload = {
        "connections": [{"name": "lake"}],
        "dataflows": [],
        "schema_hints": [],
    }
    (default_metadata / "metadata.json").write_text(
        json.dumps(metadata_payload), encoding="utf-8"
    )
    (authored_metadata / "metadata.json").write_text(
        json.dumps(metadata_payload), encoding="utf-8"
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv(
        "DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}"
    )

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        endpoint = (
            f"/api/v1/environments/{environment['id']}"
            "/datacoolie-project-sources"
        )

        first = client.post(
            endpoint,
            json={"project_uri": str(project_root), "enabled": False},
        ).json()
        old_id = first["created"][0]["id"]

        (project_root / "datacoolie.yml").write_text(
            "components:\n  metadata:\n    path: authored/metadata\n",
            encoding="utf-8",
        )
        second = client.post(
            endpoint,
            json={"project_uri": str(project_root), "enabled": False},
        ).json()

        assert second["summary"]["created"] == 1
        assert second["summary"]["stale"] == 1
        assert second["stale"][0]["id"] == old_id
        assert second["stale"][0]["source_kind"] == "metadata"

        listed = client.get(
            f"/api/v1/environments/{environment['id']}/metadata-sources"
        ).json()
        by_id = {item["id"]: item for item in listed}
        assert by_id[old_id]["enabled"] is False
        assert by_id[old_id]["source_config"]["discovery_state"] == "missing"
        new_items = [item for item in listed if item["id"] != old_id]
        assert len(new_items) == 1
        assert new_items[0]["source_config"]["metadata_root_uri"] == str(
            authored_metadata
        )


def test_project_scan_preserves_manual_code_override_on_rescan(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    project_root = tmp_path / "project"
    functions_dir = project_root / "functions"
    functions_dir.mkdir(parents=True)
    helper = functions_dir / "helper.py"
    helper.write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        endpoint = f"/api/v1/environments/{environment['id']}/datacoolie-project-sources"
        first = client.post(endpoint, json={"project_uri": str(project_root), "enabled": False}).json()
        source_id = first["created"][0]["id"]

        updated = client.patch(
            f"/api/v1/environments/{environment['id']}/code-artifacts/{source_id}",
            json={"label": "manual helper"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["source_config"]["discovery_managed"] is False
        helper.unlink()

        rescanned = client.post(endpoint, json={"project_uri": str(project_root), "enabled": False}).json()
        assert rescanned["summary"].get("stale", 0) == 0
        assert rescanned["created"] == []
        assert rescanned["existing"] == []
        listed = client.get(
            f"/api/v1/environments/{environment['id']}/code-artifacts"
        ).json()
        assert listed[0]["label"] == "manual helper"
        assert "discovery_state" not in listed[0]["source_config"]


def test_project_scan_does_not_mark_invalid_archive_as_missing(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "studio.db"
    project_root = tmp_path / "project"
    functions_dir = project_root / "functions"
    functions_dir.mkdir(parents=True)
    bundle = functions_dir / "bundle.zip"
    with ZipFile(bundle, "w") as archive:
        archive.writestr("pkg/__init__.py", "")
        archive.writestr("pkg/loader.py", "VALUE = 1\n")
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        endpoint = (
            f"/api/v1/environments/{environment['id']}"
            "/datacoolie-project-sources"
        )
        first = client.post(
            endpoint, json={"project_uri": str(project_root), "enabled": False}
        ).json()
        source_id = first["created"][0]["id"]

        bundle.write_bytes(b"not a zip")
        second = client.post(
            endpoint, json={"project_uri": str(project_root), "enabled": False}
        ).json()

        assert second["created"] == []
        assert second["existing"] == []
        assert second["stale"] == []
        assert any(
            error.get("code") == "artifact_index_error"
            for error in second["errors"]
        )
        listed = client.get(
            f"/api/v1/environments/{environment['id']}/code-artifacts"
        ).json()
        assert listed[0]["id"] == source_id
        assert "discovery_state" not in listed[0]["source_config"]


def test_project_scan_preserves_remote_storage_binding(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

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
        "datacoolie_studio.domains.sources.project_import.create_storage_adapter",
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
        # Project import queues initialization for the discovered metadata and
        # code sources; the remote adapter is therefore read after discovery.
        assert remote_adapter.read_count > 0
