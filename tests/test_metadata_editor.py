from __future__ import annotations

from pathlib import Path
import sqlite3
import uuid
import json

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "datacoolie"
METADATA_DIR = FIXTURE_ROOT / "usecase-sim" / "metadata" / "file"
SAMPLE_JSON = METADATA_DIR / "local_use_cases.json"
SAMPLE_YAML = METADATA_DIR / "local_use_cases.yaml"
SAMPLE_XLSX = METADATA_DIR / "local_use_cases.xlsx"
DATACOOLIE_NS = uuid.UUID("da7ac001-e000-4000-8000-000000000000")


def _editor_workspace(client, environment_id: int) -> dict:
    response = client.get(f"/api/v1/environments/{environment_id}/metadata-editor-workspace")
    assert response.status_code == 200
    workspace = response.json()
    assert workspace["schema_version"] == "metadata-editor-workspace.v1"
    assert workspace["environment_id"] == environment_id
    assert workspace["metadata_catalog_version"]
    return workspace


def _editor_document(client, environment_id: int) -> dict:
    return _editor_workspace(client, environment_id)["document"]


def test_metadata_editor_document_loads_json_yaml_and_xlsx(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()

        for sample_path, expected_format in [(SAMPLE_JSON, "json"), (SAMPLE_YAML, "yaml"), (SAMPLE_XLSX, "xlsx")]:
            env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": f"dev-{expected_format}"}).json()
            source = client.post(
                f"/api/v1/environments/{env['id']}/metadata-sources",
                json={"uri": str(sample_path), "label": expected_format},
            ).json()
            workspace = _editor_workspace(client, env["id"])
            document = workspace["document"]
            assert workspace["draft"] is None
            context = client.get(f"/api/v1/environments/{env['id']}/context").json()
            assert workspace["metadata_catalog_version"] == context["versions"]["metadata_catalog"]
            assert client.get(f"/api/v1/environments/{env['id']}/metadata-editor-document").status_code != 200
            assert client.get(f"/api/v1/environments/{env['id']}/metadata-editor-document/draft").status_code != 200
            source_revision = next(item for item in document["source"]["revision"]["sources"] if item["source_id"] == source["id"])

            assert document["source"]["format"] == "merged"
            assert source_revision["format"] == expected_format
            assert source_revision["revision"]["content_hash"]
            assert document["sheets"]["connections"]["rows"]
            assert document["sheets"]["dataflows"]["rows"]
            assert document["sheets"]["schema_hints"]["rows"]
            assert _column_keys(document, "connections")[:3] == ["connection_id", "name", "description"]
            assert "is_active" in _column_keys(document, "connections")
            assert _column_keys(document, "dataflows")[1:9] == [
                "name",
                "description",
                "stage",
                "group_number",
                "execution_order",
                "processing_mode",
                "is_active",
                "configure",
            ]
            assert "source_python_function" in _column_keys(document, "dataflows")
            assert "source_filter_expression" in _column_keys(document, "dataflows")
            assert "transform_schema_hints" in _column_keys(document, "dataflows")
            assert "destination_partition_columns" in _column_keys(document, "dataflows")
            assert "schema_name" in _column_keys(document, "schema_hints")
            assert "default_value" in _column_keys(document, "schema_hints")
            assert "ordinal_position" in _column_keys(document, "schema_hints")
            assert "is_active" in _column_keys(document, "schema_hints")
            assert any(row["name"] == "read__csv" for row in document["sheets"]["dataflows"]["rows"])
            first_connection = document["sheets"]["connections"]["rows"][0]
            read_csv = next(row for row in document["sheets"]["dataflows"]["rows"] if row["name"] == "read__csv")
            assert first_connection["connection_id"] == str(uuid.uuid5(DATACOOLIE_NS, first_connection["name"]))
            assert read_csv["dataflow_id"] == str(uuid.uuid5(DATACOOLIE_NS, "read__csv"))

    monkeypatch.delenv("DATACOOLIE_STUDIO_DB", raising=False)


def _column_keys(document: dict, sheet: str) -> list[str]:
    return [column["key"] for column in document["sheets"][sheet]["columns"]]


def _source_revision(document: dict, source_id: int) -> dict:
    source = next(item for item in document["source"]["revision"]["sources"] if item["source_id"] == source_id)
    return source["revision"]


def test_metadata_editor_validation_catches_invalid_references(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(SAMPLE_JSON), "label": "json"},
        ).json()
        document = _editor_document(client, env["id"])
        document["sheets"]["dataflows"]["rows"][0]["source_connection_name"] = "missing_connection"

        validation = client.post(f"/api/v1/environments/{env['id']}/metadata-editor-document/validate", json=document).json()

        assert validation["status"] == "error"
        assert any(issue["column"] == "source_connection_name" and issue["severity"] == "error" for issue in validation["issues"])

    monkeypatch.delenv("DATACOOLIE_STUDIO_DB", raising=False)


def test_metadata_editor_expands_dynamic_nested_columns_and_stringifies_complex_values(tmp_path: Path, monkeypatch):
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "connections": [
                    {"name": "lake", "connection_type": "file", "format": "parquet", "configure": {"base_path": "./data"}}
                ],
                "dataflows": [
                    {
                        "name": "flow",
                        "source": {"connection_name": "lake", "table": "a", "custom_option": {"enabled": True}},
                        "transform": {"additional_columns": [{"column": "x", "expression": "1"}], "custom_rule": ["a", "b"]},
                        "destination": {"connection_name": "lake", "table": "b", "load_type": "append", "custom_sink": {"mode": "fast"}},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

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

    columns = _column_keys(document, "dataflows")
    row = document["sheets"]["dataflows"]["rows"][0]
    assert columns.index("source_custom_option") > columns.index("source_configure")
    assert columns.index("transform_custom_rule") > columns.index("transform_configure")
    assert columns.index("destination_custom_sink") > columns.index("destination_configure")
    assert row["source_custom_option"] == '{"enabled": true}'
    assert row["transform_additional_columns"].startswith("[")
    assert row["destination_custom_sink"] == '{"mode": "fast"}'

    monkeypatch.delenv("DATACOOLIE_STUDIO_DB", raising=False)


def test_metadata_editor_materializes_default_is_active_for_studio(tmp_path: Path, monkeypatch):
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "connections": [
                    {"name": "lake", "connection_type": "file"},
                    {"name": "disabled_lake", "connection_type": "file", "is_active": False},
                ],
                "dataflows": [
                    {
                        "name": "flow",
                        "source": {"connection_name": "lake", "table": "a"},
                        "destination": {"connection_name": "lake", "table": "b"},
                    },
                    {
                        "name": "disabled_flow",
                        "is_active": False,
                        "source": {"connection_name": "lake", "table": "c"},
                        "destination": {"connection_name": "lake", "table": "d"},
                    },
                ],
                "schema_hints": [
                    {
                        "connection_name": "lake",
                        "table_name": "orders",
                        "hints": [
                            {"column_name": "id", "data_type": "int"},
                            {"column_name": "old_id", "data_type": "int", "is_active": False},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

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

    connections = {row["name"]: row["is_active"] for row in document["sheets"]["connections"]["rows"]}
    dataflows = {row["name"]: row["is_active"] for row in document["sheets"]["dataflows"]["rows"]}
    schema_hints = {row["column_name"]: row["is_active"] for row in document["sheets"]["schema_hints"]["rows"]}
    assert connections == {"lake": True, "disabled_lake": False}
    assert dataflows == {"flow": True, "disabled_flow": False}
    assert schema_hints == {"id": True, "old_id": False}

    monkeypatch.delenv("DATACOOLIE_STUDIO_DB", raising=False)


def test_metadata_editor_draft_and_safe_save_create_backup(tmp_path: Path, monkeypatch):
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "connections": [{"name": "lake", "connection_type": "file", "format": "json"}],
                "dataflows": [
                    {
                        "name": "flow",
                        "source": {"connection_name": "lake", "table": "a"},
                        "destination": {"connection_name": "lake", "table": "b"},
                    }
                ],
                "schema_hints": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.metadata import editor as editor_service
    from datacoolie_studio.main import app

    monkeypatch.setattr(editor_service, "backup_dir", lambda: tmp_path / "backups")

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(metadata_path), "label": "json"},
        ).json()
        document = _editor_document(client, env["id"])
        original_text = metadata_path.read_text(encoding="utf-8")

        document["sheets"]["dataflows"]["rows"][0]["description"] = "draft only"
        draft = client.put(f"/api/v1/environments/{env['id']}/metadata-editor-document/draft", json=document).json()
        assert draft["sheets"]["dataflows"]["rows"][0]["description"] == "draft only"
        assert metadata_path.read_text(encoding="utf-8") == original_text

        document["sheets"]["dataflows"]["rows"][0]["description"] = "saved"
        saved = client.put(
            f"/api/v1/environments/{env['id']}/metadata-editor-document",
            json={
                "expected_revision": document["source"]["revision"],
                "editor_document": document,
                "confirm_overwrite": True,
            },
        ).json()["document"]
        assert saved["sheets"]["dataflows"]["rows"][0]["description"] == "saved"
        assert "saved" in metadata_path.read_text(encoding="utf-8")
        saved_file = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert "connection_id" not in saved_file["connections"][0]
        assert "dataflow_id" not in saved_file["dataflows"][0]
        backups = client.get(f"/api/v1/environments/{env['id']}/metadata-backups").json()
        assert len(backups) == 1
        assert Path(backups[0]["backup_path"]).exists()
        assert _editor_workspace(client, env["id"])["draft"] is None

        preview = client.get(f"/api/v1/metadata-backups/{backups[0]['id']}/editor-document").json()
        assert preview["source"]["backup_id"] == backups[0]["id"]
        assert preview["sheets"]["dataflows"]["rows"][0].get("description") is None

        saved["sheets"]["dataflows"]["rows"][0]["description"] = "stale draft"
        client.put(f"/api/v1/environments/{env['id']}/metadata-editor-document/draft", json=saved)
        restored = client.post(
            f"/api/v1/metadata-backups/{backups[0]['id']}/restore",
            json={
                "expected_revision": _source_revision(saved, source["id"]),
                "confirm_restore": True,
            },
        ).json()["document"]
        assert restored["sheets"]["dataflows"]["rows"][0].get("description") is None
        assert _editor_workspace(client, env["id"])["draft"] is None
        backups = client.get(f"/api/v1/environments/{env['id']}/metadata-backups").json()
        assert len(backups) == 2
        backup_paths = [Path(backup["backup_path"]) for backup in backups]
        assert all(path.exists() for path in backup_paths)

        response = client.delete(f"/api/v1/environments/{env['id']}/metadata-backups")
        assert response.status_code == 204
        assert client.get(f"/api/v1/environments/{env['id']}/metadata-backups").json() == []
        assert all(not path.exists() for path in backup_paths)
        assert metadata_path.exists()

        restored = _editor_document(client, env["id"])
        restored["sheets"]["dataflows"]["rows"][0]["description"] = "save before source delete"
        saved_for_delete = client.put(
            f"/api/v1/environments/{env['id']}/metadata-editor-document",
            json={
                "expected_revision": restored["source"]["revision"],
                "editor_document": restored,
                "confirm_overwrite": True,
            },
        ).json()["document"]
        saved_for_delete["sheets"]["dataflows"]["rows"][0]["description"] = "draft before source delete"
        client.put(f"/api/v1/environments/{env['id']}/metadata-editor-document/draft", json=saved_for_delete)
        with sqlite3.connect(tmp_path / "studio.db") as connection:
            connection.execute(
                """
                insert into metadata_validation_results (source_id, base_revision_json, status, result_json, created_at)
                values (?, '{}', 'ok', '{}', CURRENT_TIMESTAMP)
                """,
                (source["id"],),
            )
            connection.commit()
        source_backup = client.get(f"/api/v1/environments/{env['id']}/metadata-backups").json()[0]
        source_backup_path = Path(source_backup["backup_path"])
        assert source_backup_path.exists()

        impact = client.get(f"/api/v1/environments/{env['id']}/metadata-sources/{source['id']}/delete-impact").json()
        impact_counts = {item["kind"]: item["count"] for item in impact["impacts"]}
        assert impact["has_impact"] is True
        assert impact["metadata_file_deleted"] is False
        assert impact_counts["environment_draft"] == 1
        assert impact_counts["backup"] == 1
        assert impact_counts["validation_result"] == 1
        assert impact_counts["materialization"] == 1
        assert impact_counts["save_event"] >= 1
        assert impact_counts["source_observation"] == 1
        assert impact_counts["sync_job"] >= 1
        assert "metadata file will not be deleted" in impact["summary"]

        response = client.delete(f"/api/v1/environments/{env['id']}/metadata-sources/{source['id']}")
        assert response.status_code == 204
        assert not source_backup_path.exists()
        assert client.get(f"/api/v1/environments/{env['id']}/metadata-sources").json() == []
        assert metadata_path.exists()
        with sqlite3.connect(tmp_path / "studio.db") as connection:
            for table in [
                "metadata_editor_drafts",
                "metadata_backups",
                "metadata_validation_results",
                "metadata_save_events",
                "metadata_materializations",
                "source_observations",
                "sync_jobs",
            ]:
                count = connection.execute(f"select count(*) from {table} where source_id = ?", (source["id"],)).fetchone()[0]
                assert count == 0
            env_drafts = connection.execute(
                "select count(*) from environment_metadata_editor_drafts where environment_id = ?",
                (env["id"],),
            ).fetchone()[0]
            assert env_drafts == 0

    monkeypatch.delenv("DATACOOLIE_STUDIO_DB", raising=False)


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
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.metadata import editor as editor_service
    from datacoolie_studio.main import app

    monkeypatch.setattr(editor_service, "backup_dir", lambda: tmp_path / "backups")

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
        saved = client.put(
            f"/api/v1/environments/{env['id']}/metadata-editor-document",
            json={
                "expected_revision": document["source"]["revision"],
                "editor_document": document,
                "confirm_overwrite": True,
            },
        ).json()["document"]
        assert saved["source"]["scope"] == "environment"
        assert "saved from merge view" in first_path.read_text(encoding="utf-8")
        assert "saved from merge view" not in second_path.read_text(encoding="utf-8")
        assert _editor_workspace(client, env["id"])["draft"] is None
        backups = client.get(f"/api/v1/environments/{env['id']}/metadata-backups").json()
        assert len(backups) == 1
        assert backups[0]["source_id"] == first["id"]
        assert Path(backups[0]["backup_path"]).exists()

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
        saved_with_new_source = client.put(
            f"/api/v1/environments/{env['id']}/metadata-editor-document",
            json={
                "expected_revision": saved["source"]["revision"],
                "editor_document": saved,
                "confirm_overwrite": True,
            },
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

    monkeypatch.delenv("DATACOOLIE_STUDIO_DB", raising=False)


def test_file_serializers_omit_runtime_ids(tmp_path: Path):
    import openpyxl

    from datacoolie_studio.domains.metadata.editor import _serialize_editor_document, _write_xlsx_document

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

    serialized_json = json.loads(_serialize_editor_document(document, tmp_path / "metadata.json"))
    assert serialized_json["connections"] == [{"name": "lake"}, {"name": "disabled_lake", "is_active": False}]
    assert serialized_json["dataflows"] == [{"name": "flow"}, {"name": "disabled_flow", "is_active": False}]
    assert serialized_json["schema_hints"][0]["hints"] == [{"column_name": "id"}, {"column_name": "old_id", "is_active": False}]

    serialized_yaml = _serialize_editor_document(document, tmp_path / "metadata.yaml")
    assert "connection_id" not in serialized_yaml
    assert "dataflow_id" not in serialized_yaml
    assert "is_active: true" not in serialized_yaml
    assert "is_active: false" in serialized_yaml

    xlsx_path = tmp_path / "metadata.xlsx"
    _write_xlsx_document(document, xlsx_path)
    workbook = openpyxl.load_workbook(xlsx_path, read_only=True)
    assert list(workbook["connections"].values)[0] == ("name", "is_active")
    assert list(workbook["connections"].values)[1] == ("lake", None)
    assert list(workbook["connections"].values)[2] == ("disabled_lake", False)
    assert list(workbook["dataflows"].values)[0] == ("name", "is_active")
    assert list(workbook["dataflows"].values)[1] == ("flow", None)
    assert list(workbook["dataflows"].values)[2] == ("disabled_flow", False)


def test_file_serializers_follow_editor_column_order(tmp_path: Path):
    import yaml

    from datacoolie_studio.domains.metadata.editor import _serialize_editor_document

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

    serialized_json = json.loads(_serialize_editor_document(document, tmp_path / "metadata.json"))
    assert list(serialized_json["connections"][0]) == ["name", "description", "connection_type"]
    assert list(serialized_json["dataflows"][0]) == ["name", "description", "stage", "source", "destination"]
    assert list(serialized_json["dataflows"][0]["source"]) == ["connection_name", "schema_name", "table"]
    assert list(serialized_json["dataflows"][0]["destination"]) == ["connection_name", "table"]

    serialized_yaml = yaml.safe_load(_serialize_editor_document(document, tmp_path / "metadata.yaml"))
    assert list(serialized_yaml["connections"][0]) == ["name", "description", "connection_type"]
    assert list(serialized_yaml["dataflows"][0]) == ["name", "description", "stage", "source", "destination"]


def test_metadata_editor_save_rejects_stale_revision(tmp_path: Path, monkeypatch):
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps({"connections": [{"name": "lake"}], "dataflows": [], "schema_hints": []}), encoding="utf-8")
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

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

        response = client.put(
            f"/api/v1/environments/{env['id']}/metadata-editor-document",
            json={
                "expected_revision": document["source"]["revision"],
                "editor_document": document,
                "confirm_overwrite": True,
            },
        )
        assert response.status_code == 409

    monkeypatch.delenv("DATACOOLIE_STUDIO_DB", raising=False)


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
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.metadata import editor as editor_service
    from datacoolie_studio.main import app

    monkeypatch.setattr(editor_service, "backup_dir", lambda: tmp_path / "backups")

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(metadata_path), "label": "json"},
        ).json()
        document = _editor_document(client, env["id"])
        document["sheets"]["dataflows"]["rows"][0]["destination_table"] = "after"
        saved = client.put(
            f"/api/v1/environments/{env['id']}/metadata-editor-document",
            json={
                "expected_revision": document["source"]["revision"],
                "editor_document": document,
                "confirm_overwrite": True,
            },
        ).json()["document"]
        backup = client.get(f"/api/v1/environments/{env['id']}/metadata-backups").json()[0]
        metadata_path.write_text(
            json.dumps({"connections": [{"name": "external"}], "dataflows": [], "schema_hints": []}),
            encoding="utf-8",
        )

        response = client.post(
            f"/api/v1/metadata-backups/{backup['id']}/restore",
            json={
                "expected_revision": _source_revision(saved, source["id"]),
                "confirm_restore": True,
            },
        )
        assert response.status_code == 409
        assert json.loads(metadata_path.read_text(encoding="utf-8"))["connections"][0]["name"] == "external"

    monkeypatch.delenv("DATACOOLIE_STUDIO_DB", raising=False)
