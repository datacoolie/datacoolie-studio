from __future__ import annotations

import json
from pathlib import Path

from tests.domains.metadata.metadata_editor_support import (
    _editor_document,
    _editor_workspace,
    _save_environment_document,
    _source_revision,
)


def test_environment_save_keeps_independent_opaque_metadata(
    tmp_path: Path,
    monkeypatch,
):
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    base_document = {
        "connections": [],
        "dataflows": [],
        "schema_hints": [],
    }
    first_path.write_text(
        json.dumps({**base_document, "$schema": "https://example.test/first.json"}),
        encoding="utf-8",
    )
    second_path.write_text(
        json.dumps({**base_document, "$schema": "https://example.test/second.json"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "DATACOOLIE_STUDIO_DATABASE_URL",
        f"sqlite:///{(tmp_path / 'studio.db').as_posix()}",
    )

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        first = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={"uri": str(first_path), "label": "first"},
        ).json()
        second = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={"uri": str(second_path), "label": "second"},
        ).json()
        document = _editor_document(client, environment["id"])

        first_extra = document["metadata_documents"][str(first["id"])]
        second_extra = document["metadata_documents"][str(second["id"])]
        assert first_extra["extras"]["$schema"] == "https://example.test/first.json"
        assert second_extra["extras"]["$schema"] == "https://example.test/second.json"
        first_before = first_path.read_bytes()
        second_before = second_path.read_bytes()

        response = _save_environment_document(client, environment["id"], document)

        assert response.status_code == 200, response.text
        assert first_path.read_bytes() == first_before
        assert second_path.read_bytes() == second_before

    monkeypatch.delenv("DATACOOLIE_STUDIO_DATABASE_URL", raising=False)

def test_environment_metadata_editor_draft_and_save_split_sources(tmp_path: Path, monkeypatch):
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_text(
        json.dumps(
            {
                "connections": [{"name": "lake_a", "connection_type": "file", "format": "json"}],
                "dataflows": [
                    {
                        "name": "flow_a",
                        "source": {"connection_name": "lake_a", "table": "a"},
                        "destination": {"connection_name": "lake_a", "table": "b"},
                    }
                ],
                "schema_hints": [],
            }
        ),
        encoding="utf-8",
    )
    second_path.write_text(
        json.dumps(
            {
                "connections": [{"name": "lake_b", "connection_type": "file", "format": "json"}],
                "dataflows": [
                    {
                        "name": "flow_b",
                        "source": {"connection_name": "lake_b", "table": "c"},
                        "destination": {"connection_name": "lake_b", "table": "d"},
                    }
                ],
                "schema_hints": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.metadata import write_executor
    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        first = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(first_path), "label": "first"},
        ).json()
        second = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(second_path), "label": "second"},
        ).json()
        document = _editor_document(client, env["id"])
        assert document["source"]["scope"] == "environment"
        assert document["source"]["read_only"] is False
        assert {row["__metadata_source_id"] for row in document["sheets"]["dataflows"]["rows"]} == {first["id"], second["id"]}

        flow_a = next(row for row in document["sheets"]["dataflows"]["rows"] if row["name"] == "flow_a")
        flow_a["description"] = "draft in merge view"
        draft = client.put(f"/api/v1/environments/{env['id']}/metadata-editor-document/draft", json=document).json()
        assert draft["sheets"]["dataflows"]["rows"][0]["description"] == "draft in merge view"
        assert _editor_workspace(client, env["id"])["draft"]["source"]["scope"] == "environment"
        assert "draft in merge view" not in first_path.read_text(encoding="utf-8")

        flow_a["description"] = "saved from merge view"
        opened_for_write: list[str] = []
        original_storage_for_source = write_executor.storage_for_source
        with monkeypatch.context() as save_patch:
            def tracked_storage_for_source(*args, **kwargs):
                source = args[1]
                opened_for_write.append(source.uri)
                return original_storage_for_source(*args, **kwargs)

            save_patch.setattr(
                    write_executor,
                    "storage_for_source",
                tracked_storage_for_source,
            )
            saved = _save_environment_document(client, env["id"], document).json()["document"]
        # Prepare checks both revisions; only the changed source gets a writer.
        assert opened_for_write == [str(first_path), str(second_path), str(first_path)]
        assert saved["source"]["scope"] == "environment"
        assert "saved from merge view" in first_path.read_text(encoding="utf-8")
        assert "saved from merge view" not in second_path.read_text(encoding="utf-8")
        assert _editor_workspace(client, env["id"])["draft"] is None
        backups = client.get(f"/api/v1/environments/{env['id']}/metadata-backups").json()
        assert len(backups) == 1
        assert backups[0]["source_id"] == first["id"]
        assert backups[0]["backup_path"] is None
        assert backups[0]["backup_ref"]

        saved["sheets"]["connections"]["rows"].append(
            {
                "name": "lake_c",
                "connection_type": "file",
                "format": "json",
                "is_active": True,
                "__metadata_source_id": None,
                "__metadata_source_name": "third",
                "__metadata_source_uri": "",
                "__metadata_source_kind": "metadata",
            }
        )
        saved["sheets"]["dataflows"]["rows"].append(
            {
                "name": "flow_c",
                "stage": "bronze",
                "source_connection_name": "lake_c",
                "destination_connection_name": "lake_c",
                "destination_table": "target_c",
                "is_active": True,
                "__metadata_source_id": None,
                "__metadata_source_name": "dataflows/source2bronze_v2.json",
                "__metadata_source_uri": "",
                "__metadata_source_kind": "metadata",
            }
        )
        saved_with_new_source = _save_environment_document(
            client,
            env["id"],
            saved,
        ).json()["document"]
        third_path = tmp_path / "third.json"
        assert third_path.exists()
        third_file = json.loads(third_path.read_text(encoding="utf-8"))
        assert third_file["connections"][0]["name"] == "lake_c"
        sources = client.get(f"/api/v1/environments/{env['id']}/metadata-sources").json()
        third_source = next(item for item in sources if item["label"] == "third")
        third_row = next(row for row in saved_with_new_source["sheets"]["connections"]["rows"] if row["name"] == "lake_c")
        assert third_row["__metadata_source_id"] == third_source["id"]
        assert third_row["__metadata_source_name"] == "third"
        dataflow_path = tmp_path / "dataflows" / "source2bronze_v2.json"
        assert dataflow_path.exists()
        dataflow_file = json.loads(dataflow_path.read_text(encoding="utf-8"))
        assert dataflow_file["dataflows"][0]["name"] == "flow_c"
        dataflow_source = next(item for item in sources if item["label"] == "dataflows/source2bronze_v2.json")
        dataflow_row = next(row for row in saved_with_new_source["sheets"]["dataflows"]["rows"] if row["name"] == "flow_c")
        assert dataflow_row["__metadata_source_id"] == dataflow_source["id"]
        assert dataflow_row["__metadata_source_name"] == "dataflows/source2bronze_v2.json"

    monkeypatch.delenv("DATACOOLIE_STUDIO_DATABASE_URL", raising=False)

def test_environment_save_preserves_duplicate_and_unknown_runtime_references(
    tmp_path: Path,
    monkeypatch,
):
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    duplicated_metadata = {
        "connections": [
            {"name": "shared_lake", "connection_type": "file", "format": "json"}
        ],
        "dataflows": [
            {
                "name": "shared_flow",
                "source": {"connection_name": "shared_lake", "table": "source"},
                "destination": {
                    "connection_name": "shared_lake",
                    "table": "target",
                },
            }
        ],
        "schema_hints": [],
    }
    first_path.write_text(json.dumps(duplicated_metadata), encoding="utf-8")
    second_path.write_text(json.dumps(duplicated_metadata), encoding="utf-8")
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        first = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(first_path), "label": "first"},
        ).json()
        client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(second_path), "label": "second"},
        )

        document = _editor_document(client, env["id"])
        assert document["issues"] == []
        first_flow = next(
            row
            for row in document["sheets"]["dataflows"]["rows"]
            if row["__metadata_source_id"] == first["id"]
        )
        first_flow["description"] = "safe edit"

        response = _save_environment_document(client, env["id"], document)
        assert response.status_code == 200, response.text
        saved = response.json()["document"]
        assert "safe edit" in first_path.read_text(encoding="utf-8")

        saved_first_flow = next(
            row
            for row in saved["sheets"]["dataflows"]["rows"]
            if row["__metadata_source_id"] == first["id"]
        )
        saved_first_flow["source_connection_name"] = "missing_connection"
        invalid_response = _save_environment_document(client, env["id"], saved)
        assert invalid_response.status_code == 200
        assert "missing_connection" in first_path.read_text(encoding="utf-8")

    monkeypatch.delenv("DATACOOLIE_STUDIO_DATABASE_URL", raising=False)

def test_file_serializers_omit_runtime_ids(tmp_path: Path):
    import openpyxl

    from datacoolie_studio.domains.metadata.document_serialization import (
        serialize_editor_document,
        write_xlsx_document,
    )

    document = {
        "sheets": {
            "connections": {
                "columns": [{"key": "connection_id"}, {"key": "name"}, {"key": "is_active"}],
                "rows": [
                    {"connection_id": "runtime-connection-id", "name": "lake", "is_active": True},
                    {"connection_id": "runtime-connection-id-2", "name": "disabled_lake", "is_active": False},
                ],
            },
            "dataflows": {
                "columns": [{"key": "dataflow_id"}, {"key": "name"}, {"key": "is_active"}],
                "rows": [
                    {"dataflow_id": "runtime-dataflow-id", "name": "flow", "is_active": True},
                    {"dataflow_id": "runtime-dataflow-id-2", "name": "disabled_flow", "is_active": False},
                ],
            },
            "schema_hints": {
                "columns": [{"key": "connection_name"}, {"key": "table_name"}, {"key": "column_name"}, {"key": "is_active"}],
                "rows": [
                    {"connection_name": "lake", "table_name": "orders", "column_name": "id", "is_active": True},
                    {"connection_name": "lake", "table_name": "orders", "column_name": "old_id", "is_active": False},
                ],
            },
        }
    }

    serialized_json = json.loads(serialize_editor_document(document, tmp_path / "metadata.json"))
    assert serialized_json["connections"] == [{"name": "lake"}, {"name": "disabled_lake", "is_active": False}]
    assert serialized_json["dataflows"] == [{"name": "flow"}, {"name": "disabled_flow", "is_active": False}]
    assert serialized_json["schema_hints"][0]["hints"] == [{"column_name": "id"}, {"column_name": "old_id", "is_active": False}]

    serialized_yaml = serialize_editor_document(document, tmp_path / "metadata.yaml")
    assert "connection_id" not in serialized_yaml
    assert "dataflow_id" not in serialized_yaml
    assert "is_active: true" not in serialized_yaml
    assert "is_active: false" in serialized_yaml

    xlsx_path = tmp_path / "metadata.xlsx"
    write_xlsx_document(document, xlsx_path)
    workbook = openpyxl.load_workbook(xlsx_path, read_only=True)
    assert list(workbook["connections"].values)[0] == ("name", "is_active")
    assert list(workbook["connections"].values)[1] == ("lake", None)
    assert list(workbook["connections"].values)[2] == ("disabled_lake", False)
    assert list(workbook["dataflows"].values)[0] == ("name", "is_active")
    assert list(workbook["dataflows"].values)[1] == ("flow", None)
    assert list(workbook["dataflows"].values)[2] == ("disabled_flow", False)

def test_file_serializers_follow_editor_column_order(tmp_path: Path):
    import yaml

    from datacoolie_studio.domains.metadata.document_serialization import (
        serialize_editor_document,
    )

    document = {
        "sheets": {
            "connections": {
                "columns": [
                    {"key": "connection_id"},
                    {"key": "name"},
                    {"key": "description"},
                    {"key": "connection_type"},
                ],
                "rows": [{
                    "name": "lake",
                    "connection_type": "file",
                    "description": "Primary lake",
                    "connection_id": "runtime-id",
                }],
            },
            "dataflows": {
                "columns": [
                    {"key": "dataflow_id"},
                    {"key": "name"},
                    {"key": "description"},
                    {"key": "stage"},
                    {"key": "source_connection_name"},
                    {"key": "source_schema_name"},
                    {"key": "source_table"},
                    {"key": "destination_connection_name"},
                    {"key": "destination_table"},
                ],
                "rows": [{
                    "name": "flow",
                    "stage": "raw",
                    "source_table": "input",
                    "destination_table": "output",
                    "description": "Load input",
                    "destination_connection_name": "lake",
                    "source_schema_name": "landing",
                    "source_connection_name": "lake",
                    "dataflow_id": "runtime-id",
                }],
            },
            "schema_hints": {"columns": [], "rows": []},
        }
    }

    serialized_json = json.loads(serialize_editor_document(document, tmp_path / "metadata.json"))
    assert list(serialized_json["connections"][0]) == ["name", "description", "connection_type"]
    assert list(serialized_json["dataflows"][0]) == ["name", "description", "stage", "source", "destination"]
    assert list(serialized_json["dataflows"][0]["source"]) == ["connection_name", "schema_name", "table"]
    assert list(serialized_json["dataflows"][0]["destination"]) == ["connection_name", "table"]

    serialized_yaml = yaml.safe_load(serialize_editor_document(document, tmp_path / "metadata.yaml"))
    assert list(serialized_yaml["connections"][0]) == ["name", "description", "connection_type"]
    assert list(serialized_yaml["dataflows"][0]) == ["name", "description", "stage", "source", "destination"]

def test_metadata_editor_save_rejects_stale_revision(tmp_path: Path, monkeypatch):
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps({"connections": [{"name": "lake"}], "dataflows": [], "schema_hints": []}), encoding="utf-8")
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(metadata_path), "label": "json"},
        ).json()
        document = _editor_document(client, env["id"])
        metadata_path.write_text(json.dumps({"connections": [{"name": "changed"}], "dataflows": [], "schema_hints": []}), encoding="utf-8")

        response = _save_environment_document(client, env["id"], document)
        assert response.status_code == 409
        assert "changed before save" in response.json()["detail"]

    monkeypatch.delenv("DATACOOLIE_STUDIO_DATABASE_URL", raising=False)

def test_metadata_backup_restore_rejects_stale_current_revision(tmp_path: Path, monkeypatch):
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps({
            "connections": [{"name": "lake"}],
            "dataflows": [{"name": "flow", "destination": {"table": "before"}}],
            "schema_hints": [],
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(metadata_path), "label": "json"},
        ).json()
        document = _editor_document(client, env["id"])
        document["sheets"]["dataflows"]["rows"][0]["destination_table"] = "after"
        saved = _save_environment_document(client, env["id"], document).json()["document"]
        backup = client.get(f"/api/v1/environments/{env['id']}/metadata-backups").json()[0]
        metadata_path.write_text(
            json.dumps({"connections": [{"name": "external"}], "dataflows": [], "schema_hints": []}),
            encoding="utf-8",
        )

        response = client.post(
            f"/api/v1/environments/{env['id']}/metadata-backups/{backup['id']}/restore",
            json={
                "expected_revision": _source_revision(saved, source["id"]),
                "confirm_restore": True,
            },
        )
        assert response.status_code == 409
        assert json.loads(metadata_path.read_text(encoding="utf-8"))["connections"][0]["name"] == "external"

    monkeypatch.delenv("DATACOOLIE_STUDIO_DATABASE_URL", raising=False)
