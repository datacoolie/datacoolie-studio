import json
from pathlib import Path


def test_metadata_write_operation_api_prepares_then_applies(
    studio_client,
    studio_project_factory,
    tmp_path: Path,
    monkeypatch,
):
    environment = studio_project_factory(project_name="operation-api-project")
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "connections": [{"name": "lake"}],
                "dataflows": [],
                "schema_hints": [],
            }
        ),
        encoding="utf-8",
    )
    source = studio_client.post(
        f"/api/v1/environments/{environment.environment_id}/metadata-sources",
        json={"uri": str(metadata_path), "label": "metadata"},
    ).json()
    workspace = studio_client.get(
        f"/api/v1/environments/{environment.environment_id}/metadata-editor-workspace"
    ).json()
    document = workspace["document"]
    document["sheets"]["connections"]["rows"][0]["name"] = "warehouse"

    prepared_response = studio_client.post(
        f"/api/v1/environments/{environment.environment_id}/metadata-write-operations/prepare",
        json={
            "expected_revision": document["source"]["revision"],
            "editor_document": document,
        },
    )
    assert prepared_response.status_code == 200, prepared_response.text
    prepared = prepared_response.json()
    assert prepared["state"] == "prepared"
    assert prepared["summary"]["total"] == 1
    assert json.loads(metadata_path.read_text(encoding="utf-8"))["connections"][0]["name"] == "lake"

    applied_response = studio_client.post(
        f"/api/v1/environments/{environment.environment_id}/metadata-write-operations/"
        f"{prepared['operation_key']}/apply",
        json={"confirm_apply": True},
    )
    assert applied_response.status_code == 200, applied_response.text
    assert applied_response.json()["state"] == "succeeded"
    assert json.loads(metadata_path.read_text(encoding="utf-8"))["connections"][0]["name"] == "warehouse"
    assert source["id"] == applied_response.json()["items"][0]["source_id"]

    backups = studio_client.get(
        f"/api/v1/environments/{environment.environment_id}/metadata-backups"
    ).json()
    assert len(backups) == 1
    assert backups[0]["backup_path"] is None
    assert backups[0]["backup_ref"]
    preview = studio_client.get(
        f"/api/v1/environments/{environment.environment_id}/metadata-backups/"
        f"{backups[0]['id']}/editor-document"
    )
    assert preview.status_code == 200, preview.text

    current_workspace = studio_client.get(
        f"/api/v1/environments/{environment.environment_id}/metadata-editor-workspace"
    ).json()
    source_revision = next(
        item
        for item in current_workspace["document"]["source"]["revision"]["sources"]
        if item["source_id"] == source["id"]
    )
    # Response composition must not invoke any materialization refresh after
    # the saga has verified and committed the restored bytes.
    monkeypatch.setattr(
        "datacoolie_studio.domains.metadata.service.storage_for_source",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("restore response refreshed source")),
    )
    restored = studio_client.post(
        f"/api/v1/environments/{environment.environment_id}/metadata-backups/"
        f"{backups[0]['id']}/restore",
        json={
            "expected_revision": source_revision.get("revision", source_revision),
            "confirm_restore": True,
        },
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["document"]["sheets"]["connections"]["rows"][0]["name"] == "lake"


def test_metadata_write_operation_blocks_source_lifecycle_mutation(
    studio_client,
    studio_project_factory,
    tmp_path: Path,
):
    environment = studio_project_factory(project_name="operation-guard-project")
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps({"connections": [{"name": "lake"}], "dataflows": [], "schema_hints": []}),
        encoding="utf-8",
    )
    source = studio_client.post(
        f"/api/v1/environments/{environment.environment_id}/metadata-sources",
        json={"uri": str(metadata_path), "label": "metadata"},
    ).json()
    document = studio_client.get(
        f"/api/v1/environments/{environment.environment_id}/metadata-editor-workspace"
    ).json()["document"]
    document["sheets"]["connections"]["rows"][0]["name"] = "warehouse"
    prepared = studio_client.post(
        f"/api/v1/environments/{environment.environment_id}/metadata-write-operations/prepare",
        json={"expected_revision": document["source"]["revision"], "editor_document": document},
    )
    assert prepared.status_code == 200, prepared.text
    blocked = studio_client.patch(
        f"/api/v1/environments/{environment.environment_id}/metadata-sources/{source['id']}",
        json={"label": "renamed while pending"},
    )
    assert blocked.status_code == 409, blocked.text


def test_metadata_write_operation_can_be_abandoned_without_source_mutation(
    studio_client,
    studio_project_factory,
    tmp_path: Path,
):
    environment = studio_project_factory(project_name="operation-abandon-project")
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps({"connections": [{"name": "lake"}], "dataflows": [], "schema_hints": []}),
        encoding="utf-8",
    )
    studio_client.post(
        f"/api/v1/environments/{environment.environment_id}/metadata-sources",
        json={"uri": str(metadata_path), "label": "metadata"},
    )
    document = studio_client.get(
        f"/api/v1/environments/{environment.environment_id}/metadata-editor-workspace"
    ).json()["document"]
    document["sheets"]["connections"]["rows"][0]["name"] = "warehouse"
    prepared = studio_client.post(
        f"/api/v1/environments/{environment.environment_id}/metadata-write-operations/prepare",
        json={"expected_revision": document["source"]["revision"], "editor_document": document},
    )
    assert prepared.status_code == 200, prepared.text
    operation_key = prepared.json()["operation_key"]
    abandoned = studio_client.post(
        f"/api/v1/environments/{environment.environment_id}/metadata-write-operations/"
        f"{operation_key}/abandon",
        json={"confirm_abandon": True},
    )
    assert abandoned.status_code == 200, abandoned.text
    assert abandoned.json()["state"] == "abandoned"
    repeated = studio_client.post(
        f"/api/v1/environments/{environment.environment_id}/metadata-write-operations/{operation_key}/abandon",
        json={"confirm_abandon": True},
    )
    assert repeated.status_code == 200, repeated.text
    first_result, repeated_result = abandoned.json(), repeated.json()
    # SQLite reloads timestamps without timezone metadata; the instant is unchanged.
    for field in ["completed_at", "updated_at"]:
        assert repeated_result.pop(field).removesuffix("Z") == first_result.pop(field).removesuffix("Z")
    assert repeated_result == first_result
    assert json.loads(metadata_path.read_text(encoding="utf-8"))["connections"][0]["name"] == "lake"

    retry = studio_client.post(
        f"/api/v1/environments/{environment.environment_id}/metadata-write-operations/"
        f"{operation_key}/apply",
        json={"confirm_apply": True},
    )
    assert retry.status_code == 409, retry.text


def test_failed_operation_api_keeps_source_guard_and_conflicts_with_new_plan(studio_client, studio_project_factory, tmp_path, monkeypatch):
    from datacoolie_studio.domains.metadata import write_executor

    environment = studio_project_factory(project_name="failed-operation-api")
    path = tmp_path / "metadata.json"
    path.write_text('{"connections":[{"name":"lake"}],"dataflows":[],"schema_hints":[]}', encoding="utf-8")
    prefix = f"/api/v1/environments/{environment.environment_id}"
    source = studio_client.post(f"{prefix}/metadata-sources", json={"uri": str(path), "label": "metadata"}).json()
    document = studio_client.get(f"{prefix}/metadata-editor-workspace").json()["document"]
    document["sheets"]["connections"]["rows"][0]["description"] = "new"
    prepared = studio_client.post(f"{prefix}/metadata-write-operations/prepare", json={"editor_document": document, "expected_revision": document["source"]["revision"]}).json()
    monkeypatch.setattr(write_executor, "conditional_replace_uri", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")))
    applied = studio_client.post(f"{prefix}/metadata-write-operations/{prepared['operation_key']}/apply", json={"confirm_apply": True})
    assert applied.status_code == 200
    assert applied.json()["state"] == "failed"
    assert studio_client.patch(f"{prefix}/metadata-sources/{source['id']}", json={"label": "blocked"}).status_code == 409
    assert studio_client.delete(f"{prefix}/metadata-sources/{source['id']}").status_code == 409
    document["sheets"]["connections"]["rows"][0]["description"] = "another"
    conflict = studio_client.post(f"{prefix}/metadata-write-operations/prepare", json={"editor_document": document, "expected_revision": document["source"]["revision"]})
    assert conflict.status_code == 409, conflict.text


def test_profile_only_sql_scope_returns_http_conflict(studio_client, studio_project_factory, tmp_path):
    from datacoolie_studio.db.models import CredentialProfile
    from datacoolie_studio.db.session import create_session

    environment = studio_project_factory(project_name="sql-profile-conflict")
    path = tmp_path / "metadata.json"
    path.write_text('{"connections":[{"name":"lake"}],"dataflows":[],"schema_hints":[]}', encoding="utf-8")
    prefix = f"/api/v1/environments/{environment.environment_id}"
    source = studio_client.post(f"{prefix}/metadata-sources", json={"uri": str(path), "label": "metadata"}).json()
    document = studio_client.get(f"{prefix}/metadata-editor-workspace").json()["document"]
    session = create_session()
    try:
        profile = CredentialProfile(id="profile-only", name="profile-only", provider="dbfs", auth_type="databricks_profile",
                                    config_json=json.dumps({"profile": "test-profile"}), secret_state="present")
        session.add(profile)
        session.flush()
        from datacoolie_studio.db.models import EnvironmentSource
        # Existing persisted profile-only source remains readable, but publication
        # cannot manufacture its unknown workspace identity from current config.
        session.add(EnvironmentSource(environment_id=environment.environment_id, source_kind="sql", uri="dbfs:/queries",
            enabled=True, storage_provider="dbfs", storage_auth_mode="credential_profile", credential_profile_id=profile.id,
            source_config_json=json.dumps({"sql_root_uri": "dbfs:/queries", "discovery_mode": "manual_sql_path"})))
        session.commit()
    finally:
        session.close()
    document["sql_files"] = [{"source_id": source["id"], "query": "orders.sql", "content": "SELECT 2;"}]
    response = studio_client.post(f"{prefix}/metadata-write-operations/prepare", json={"editor_document": document, "expected_revision": document["source"]["revision"]})
    assert response.status_code == 409, response.text
    assert "scope cannot be proven" in response.json()["detail"]
