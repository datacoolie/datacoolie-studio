from __future__ import annotations

from pathlib import Path

from tests.support.workspace import StudioTestPaths


def test_isolated_client_covers_workspace_metadata_and_source_boundaries(
    studio_client,
    studio_paths: StudioTestPaths,
):
    project = studio_client.post("/api/v1/projects", json={"name": "isolated"}).json()
    environment = studio_client.post(
        f"/api/v1/projects/{project['id']}/environments",
        json={"name": "dev"},
    ).json()
    metadata_path = studio_paths.data_dir / "metadata.json"
    metadata_path.write_text(
        '{"connections": [], "dataflows": [], "schema_hints": []}',
        encoding="utf-8",
    )
    metadata = studio_client.post(
        f"/api/v1/environments/{environment['id']}/metadata-sources",
        json={"uri": str(metadata_path), "label": "metadata"},
    )

    assert metadata.status_code == 200, metadata.text
    assert Path(metadata.json()["uri"]).is_relative_to(studio_paths.data_dir)
    assert studio_client.get("/api/v1/projects").json()[0]["name"] == "isolated"
