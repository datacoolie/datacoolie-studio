from __future__ import annotations

import json

import pytest

from datacoolie_studio.domains.metadata.editor import load_editor_document_from_path
from datacoolie_studio.domains.metadata.document_normalization import editor_document_to_raw_metadata
from tests.domains.metadata.metadata_editor_support import _editor_document, _save_environment_document


def test_id_only_incomplete_and_duplicate_metadata_save_without_semantic_gate(studio_client, studio_project_factory, tmp_path):
    environment = studio_project_factory()
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps({"dataflows": [
        {"dataflow_id": "UPPER`-ID", "configure": {"future": True}, "unknown_null": None, "unknown_empty": "", "future_text": "true", "source": {}},
        {"dataflow_id": "UPPER`-ID", "source": {"connection_name": "missing"}},
    ]}), encoding="utf-8")
    source = studio_client.post(f"/api/v1/environments/{environment.environment_id}/metadata-sources",
                                json={"uri": str(path)}).json()
    document = _editor_document(studio_client, environment.environment_id)
    assert not document["issues"]
    rows = document["sheets"]["dataflows"]["rows"]
    assert all(not row.get("name") for row in rows)
    rows[0]["source_configure"] = json.dumps({"range_param_mapping": {"lower": {"operator": "BAD"}}})
    rows[0]["description"] = "edited"
    result = _save_environment_document(studio_client, environment.environment_id, document)
    assert result.status_code == 200, result.text
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [row["dataflow_id"] for row in saved["dataflows"]] == ["UPPER`-ID"] * 2
    assert all("name" not in row for row in saved["dataflows"])
    assert saved["dataflows"][0]["source"]["configure"]["range_param_mapping"]["lower"]["operator"] == "BAD"
    assert saved["dataflows"][0]["unknown_null"] is None
    assert saved["dataflows"][0]["unknown_empty"] == ""
    assert saved["dataflows"][0]["future_text"] == "true"
    assert source["id"] > 0
    assert not any("metadata-editor-document/validate" in route for route in studio_client.get("/openapi.json").json()["paths"])


def test_unprojectable_section_preserved_when_editing_other_sections(tmp_path):
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps({"connections": {"future": [1, 2]}, "dataflows": [
        {"dataflow_id": "ID", "source": {"custom": {"raw": True}}},
    ]}), encoding="utf-8")
    document = load_editor_document_from_path(path, source_id=1, environment_id=1,
                                             project_id=1, source_uri=str(path))
    document["sheets"]["dataflows"]["rows"][0]["description"] = "edited"
    saved = editor_document_to_raw_metadata(document)
    assert saved["connections"] == {"future": [1, 2]}
    assert saved["dataflows"][0]["source"]["custom"] == {"raw": True}
    assert saved["dataflows"][0]["description"] == "edited"


def test_real_workspace_materializes_and_saves_opaque_sections(studio_client, studio_project_factory, tmp_path):
    environment = studio_project_factory()
    path = tmp_path / "metadata.json"
    opaque = {"connections": {"future": [1, None]}, "schema_hints": [{"hints": [], "future": True}]}
    path.write_text(json.dumps({**opaque, "dataflows": [{"dataflow_id": "ID"}]}), encoding="utf-8")
    response = studio_client.post(f"/api/v1/environments/{environment.environment_id}/metadata-sources", json={"uri": str(path)})
    assert response.status_code == 200, response.text
    document = _editor_document(studio_client, environment.environment_id)
    document["sheets"]["dataflows"]["rows"][0]["description"] = "edited"
    saved = _save_environment_document(studio_client, environment.environment_id, document)
    assert saved.status_code == 200, saved.text
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["connections"] == opaque["connections"]
    assert payload["schema_hints"] == opaque["schema_hints"]


def test_envelope_and_different_source_extras_survive_save(studio_client, studio_project_factory, tmp_path):
    environment = studio_project_factory()
    paths = [tmp_path / "one.json", tmp_path / "two.json"]
    for index, path in enumerate(paths):
        path.write_text(json.dumps({"future_envelope": {"value": index}, "metadata": {
            "$schema": f"schema-{index}", "dataflows": [{"dataflow_id": f"ID-{index}", "source": {}}],
        }}), encoding="utf-8")
        studio_client.post(f"/api/v1/environments/{environment.environment_id}/metadata-sources", json={"uri": str(path)})
    document = _editor_document(studio_client, environment.environment_id)
    assert not document["issues"]
    for row in document["sheets"]["dataflows"]["rows"]:
        row["description"] = "edited"
    response = _save_environment_document(studio_client, environment.environment_id, document)
    assert response.status_code == 200, response.text
    for index, path in enumerate(paths):
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["future_envelope"] == {"value": index}
        assert saved["metadata"]["$schema"] == f"schema-{index}"
        assert saved["metadata"]["dataflows"][0]["source"] == {}


def test_malformed_editor_rows_cannot_silently_delete_source(studio_client, studio_project_factory, tmp_path):
    environment = studio_project_factory()
    path = tmp_path / "metadata.json"
    original = '{"dataflows": [{"dataflow_id": "ID"}]}'
    path.write_text(original, encoding="utf-8")
    studio_client.post(f"/api/v1/environments/{environment.environment_id}/metadata-sources", json={"uri": str(path)})
    document = _editor_document(studio_client, environment.environment_id)
    document["sheets"]["dataflows"]["rows"] = ["unsupported row"]
    response = studio_client.post(f"/api/v1/environments/{environment.environment_id}/metadata-write-operations/prepare",
                                  json={"expected_revision": document["source"]["revision"], "editor_document": document})
    assert response.status_code == 422
    assert path.read_text(encoding="utf-8") == original


def test_ambiguous_metadata_alias_is_rejected_before_write():
    from datacoolie_studio.db.models import EnvironmentSource
    from datacoolie_studio.domains.metadata.environment_documents import resolve_environment_document_sources
    from datacoolie_studio.domains.metadata.reader import MetadataReadError

    sources = [EnvironmentSource(id=index, environment_id=1, source_kind="metadata", uri=f"D:/{index}/dataflows.json")
               for index in (1, 2)]
    document = {"sheets": {"dataflows": {"rows": [{"__metadata_source_name": "dataflows.json"}]}}}
    with pytest.raises(MetadataReadError, match="Ambiguous"):
        resolve_environment_document_sources(None, 1, sources, document, persist_new=False)
