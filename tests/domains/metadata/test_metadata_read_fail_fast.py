from __future__ import annotations

import json

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.db.session import create_session


def test_environment_metadata_read_routes_fail_fast_on_persisted_validation_error(
    studio_client,
):
    project = studio_client.post("/api/v1/projects", json={"name": "fail-fast"}).json()
    environment = studio_client.post(
        f"/api/v1/projects/{project['id']}/environments",
        json={"name": "dev"},
    ).json()

    session = create_session()
    session.add(
        EnvironmentSource(
            environment_id=environment["id"],
            source_kind="metadata",
            uri="abfs://container@account.dfs.core.windows.net/metadata.json",
            storage_provider="adls",
            storage_auth_mode="ambient",
            enabled=True,
            read_check_status="error",
            read_check_result_json=json.dumps(
                {
                    "status": "error",
                    "message": "AADSTS90072: account is not present in the configured tenant",
                    "detected_provider": "adls",
                }
            ),
        )
    )
    session.commit()
    session.close()

    for path in (
        f"/api/v1/environments/{environment['id']}/metadata",
        f"/api/v1/environments/{environment['id']}/metadata-editor-workspace",
        f"/api/v1/environments/{environment['id']}/overview",
    ):
        response = studio_client.get(path)
        assert response.status_code == 422, (path, response.text)
        detail = response.json()["detail"]
        assert detail["code"] == "metadata_sources_unavailable"
        assert "AADSTS90072" in detail["message"]

    code_environment = studio_client.post(
        f"/api/v1/projects/{project['id']}/environments",
        json={"name": "code-only"},
    ).json()
    session = create_session()
    session.add(
        EnvironmentSource(
            environment_id=code_environment["id"],
            source_kind="code",
            uri="abfs://container@account.dfs.core.windows.net/functions",
            storage_provider="adls",
            storage_auth_mode="ambient",
            source_config_json=json.dumps({"artifact_type": "directory"}),
            enabled=True,
            read_check_status="error",
            read_check_result_json=json.dumps(
                {
                    "status": "error",
                    "message": "AADSTS90072: account is not present in the configured tenant",
                    "detected_provider": "adls",
                }
            ),
        )
    )
    session.commit()
    session.close()

    response = studio_client.get(f"/api/v1/environments/{code_environment['id']}/overview")
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "source_validation_failed"
    assert "AADSTS90072" in detail["message"]
