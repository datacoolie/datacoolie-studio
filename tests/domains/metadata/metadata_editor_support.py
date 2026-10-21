from __future__ import annotations

import uuid

from tests.support.paths import FIXTURE_ROOT


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

def _column_keys(document: dict, sheet: str) -> list[str]:
    return [column["key"] for column in document["sheets"][sheet]["columns"]]

def _source_revision(document: dict, source_id: int) -> dict:
    source = next(item for item in document["source"]["revision"]["sources"] if item["source_id"] == source_id)
    return source["revision"]


class _EnvironmentSaveResult:
    def __init__(self, response, document: dict | None = None) -> None:
        self._response = response
        self._document = document

    @property
    def status_code(self):
        return self._response.status_code

    @property
    def text(self):
        return self._response.text

    def json(self):
        payload = self._response.json()
        if self._document is not None and isinstance(payload, dict):
            payload = {**payload, "document": self._document}
        return payload


def _save_environment_document(client, environment_id: int, document: dict):
    prepared = client.post(
        f"/api/v1/environments/{environment_id}/metadata-write-operations/prepare",
        json={
            "expected_revision": document["source"]["revision"],
            "editor_document": document,
        },
    )
    if prepared.status_code != 200:
        return prepared
    prepared_payload = prepared.json()
    if prepared_payload["state"] == "prepared":
        applied = client.post(
            f"/api/v1/environments/{environment_id}/metadata-write-operations/"
            f"{prepared_payload['operation_key']}/apply",
            json={"confirm_apply": True},
        )
    else:
        applied = prepared
    if applied.status_code < 200 or applied.status_code >= 300:
        return applied
    workspace = client.get(
        f"/api/v1/environments/{environment_id}/metadata-editor-workspace"
    )
    assert workspace.status_code == 200, workspace.text
    return _EnvironmentSaveResult(applied, workspace.json()["document"])
