from __future__ import annotations

from pathlib import Path
import json
import uuid

from tests.domains.metadata.metadata_editor_support import (
    DATACOOLIE_NS,
    SAMPLE_JSON,
    SAMPLE_XLSX,
    SAMPLE_YAML,
    _column_keys,
    _editor_document,
    _editor_workspace,
    _save_environment_document,
)


def test_metadata_editor_document_loads_json_yaml_and_xlsx(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

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

    monkeypatch.delenv("DATACOOLIE_STUDIO_DATABASE_URL", raising=False)

def test_environment_metadata_canonical_ordering_groups_sources_and_natural_keys():
    from datacoolie_studio.domains.metadata.environment_documents import (
        source_document_changed,
    )
    from datacoolie_studio.domains.metadata.ordering import (
        canonicalize_editor_document,
        same_canonical_document,
    )

    def row(source_id: int, source_uri: str, **values: object) -> dict:
        return {
            **values,
            "__metadata_source_id": source_id,
            "__metadata_source_uri": source_uri,
            "__metadata_source_name": Path(source_uri).name,
        }

    document = {
        "sheets": {
            "connections": {
                "rows": [
                    row(2, "/metadata/silver.json", name="silver-first"),
                    row(1, "/metadata/source.json", name="source"),
                    row(2, "/metadata/silver.json", name="silver-second"),
                ]
            },
            "dataflows": {
                "rows": [
                    row(1, "/metadata/source.json", stage="gold", name="gold"),
                    row(2, "/metadata/silver.json", stage="silver10", name="silver10"),
                    row(1, "/metadata/source.json", stage="source2", name="source2"),
                    row(2, "/metadata/silver.json", stage="silver2", name="silver2"),
                    row(1, "/metadata/source.json", stage="source", name="source"),
                ]
            },
            "schema_hints": {
                "rows": [
                    row(1, "/metadata/source.json", connection_name="z", schema_name="s", table_name="t", ordinal_position=1),
                    row(1, "/metadata/source.json", connection_name="a", schema_name="s", table_name="t", ordinal_position=2),
                    row(1, "/metadata/source.json", connection_name="a", schema_name="s", table_name="t", ordinal_position=10),
                ]
            },
        }
    }

    ordered = canonicalize_editor_document(document)
    assert [item["name"] for item in ordered["sheets"]["connections"]["rows"]] == [
        "source",
        "silver-first",
        "silver-second",
    ]
    assert [item["name"] for item in ordered["sheets"]["dataflows"]["rows"]] == [
        "source",
        "source2",
        "gold",
        "silver2",
        "silver10",
    ]
    assert [item["connection_name"] for item in ordered["sheets"]["schema_hints"]["rows"]] == ["a", "a", "z"]
    assert [item["ordinal_position"] for item in ordered["sheets"]["schema_hints"]["rows"][:2]] == [2, 10]

    reversed_source_groups = canonicalize_editor_document({
        **document,
        "sheets": {
            **document["sheets"],
            "connections": {
                "rows": [
                    document["sheets"]["connections"]["rows"][0],
                    document["sheets"]["connections"]["rows"][2],
                    document["sheets"]["connections"]["rows"][1],
                ]
            },
        },
    })
    assert same_canonical_document(ordered, reversed_source_groups)
    assert not source_document_changed(ordered, reversed_source_groups)

def test_connection_rename_rewrites_references_only_when_name_changes():
    from datacoolie_studio.domains.metadata.document_rewrites import (
        synchronize_connection_name_references,
    )

    baseline = {
        "sheets": {
            "connections": {"rows": [{"connection_id": "conn-1", "name": "silver"}]},
            "dataflows": {"rows": [{"source_connection_name": "silver", "destination_connection_name": "silver"}]},
            "schema_hints": {"rows": [{"connection_name": "silver"}]},
        }
    }
    renamed = {
        "sheets": {
            "connections": {"rows": [{"connection_id": "conn-1", "name": "silver_curated"}]},
            "dataflows": {"rows": [{"source_connection_name": "silver", "destination_connection_name": "silver"}]},
            "schema_hints": {"rows": [{"connection_name": "silver"}]},
        }
    }

    synchronized = synchronize_connection_name_references(renamed, baseline)

    assert synchronized["sheets"]["dataflows"]["rows"] == [
        {"source_connection_name": "silver_curated", "destination_connection_name": "silver_curated"}
    ]
    assert synchronized["sheets"]["schema_hints"]["rows"] == [{"connection_name": "silver_curated"}]
    unchanged = synchronize_connection_name_references(baseline, baseline)
    assert unchanged is baseline

def test_environment_metadata_save_applies_connection_rename_to_references(tmp_path: Path, monkeypatch):
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps({
            "connections": [{"name": "silver", "connection_type": "file", "format": "json"}],
            "dataflows": [{
                "name": "flow",
                "source": {"connection_name": "silver", "table": "orders"},
                "destination": {"connection_name": "silver", "table": "orders_curated"},
            }],
            "schema_hints": [{
                "connection_name": "silver",
                "table_name": "orders",
                "column_name": "id",
                "data_type": "integer",
            }],
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
        client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(metadata_path), "label": "metadata"},
        )
        document = _editor_document(client, env["id"])
        document["sheets"]["connections"]["rows"][0]["name"] = "silver_curated"

        response = _save_environment_document(client, env["id"], document)

        assert response.status_code == 200

    saved = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert saved["connections"][0]["name"] == "silver_curated"
    assert saved["dataflows"][0]["source"]["connection_name"] == "silver_curated"
    assert saved["dataflows"][0]["destination"]["connection_name"] == "silver_curated"
    assert saved["schema_hints"][0]["connection_name"] == "silver_curated"
    monkeypatch.delenv("DATACOOLIE_STUDIO_DATABASE_URL", raising=False)

def test_metadata_editor_preserves_invalid_runtime_references(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

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

        response = client.put(f"/api/v1/environments/{env['id']}/metadata-editor-document/draft", json=document)
        assert response.status_code == 200
        assert response.json()["issues"] == []
        assert response.json()["sheets"]["dataflows"]["rows"][0]["source_connection_name"] == "missing_connection"

    monkeypatch.delenv("DATACOOLIE_STUDIO_DATABASE_URL", raising=False)
