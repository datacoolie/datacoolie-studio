from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from datacoolie_studio.api.v1.routes.credentials import (
    get_credential_secret_store,
)
from datacoolie_studio.main import app

from test_credentials_service import MemorySecretStore


def test_local_source_defaults_to_typed_local_binding(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))
    metadata = tmp_path / "metadata.json"
    metadata.write_text("{}", encoding="utf-8")
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        response = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={"uri": str(metadata), "label": "metadata"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["storage"] == {
            "provider": "local",
            "auth_mode": "none",
            "credential_profile_id": None,
            "options": {},
        }


def test_cloud_source_binding_validates_profile_and_rejects_secret_config(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))
    store = MemorySecretStore()
    app.dependency_overrides[get_credential_secret_store] = lambda: store
    try:
        with TestClient(app) as client:
            project = client.post(
                "/api/v1/projects", json={"name": "demo"}
            ).json()
            environment = client.post(
                f"/api/v1/projects/{project['id']}/environments",
                json={"name": "dev"},
            ).json()
            profile = client.post(
                "/api/v1/credential-profiles",
                json={
                    "name": "S3",
                    "provider": "s3",
                    "auth_type": "access_key",
                    "config": {"access_key_id": "identifier"},
                    "secret": {"secret_access_key": "external-only"},
                },
            ).json()
            response = client.post(
                f"/api/v1/environments/{environment['id']}/metadata-sources",
                json={
                    "uri": "s3://analytics/metadata.json",
                    "storage": {
                        "provider": "s3",
                        "auth_mode": "credential_profile",
                        "credential_profile_id": profile["id"],
                        "options": {"region": "ap-southeast-1"},
                    },
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["storage"]["credential_profile_id"] == profile["id"]
            assert "external-only" not in response.text

            bad_provider = client.post(
                f"/api/v1/environments/{environment['id']}/code-artifacts",
                json={
                    "uri": "gs://bucket/code.zip",
                    "storage": {
                        "provider": "gcs",
                        "auth_mode": "credential_profile",
                        "credential_profile_id": profile["id"],
                    },
                },
            )
            assert bad_provider.status_code == 422

            embedded_secret = client.post(
                f"/api/v1/environments/{environment['id']}/log-sources",
                json={
                    "uri": "s3://analytics/logs",
                    "storage": {
                        "provider": "s3",
                        "auth_mode": "ambient",
                    },
                    "source_config": {"secret_access_key": "must-be-rejected"},
                },
            )
            assert embedded_secret.status_code == 422
            assert "must-be-rejected" not in embedded_secret.text
    finally:
        app.dependency_overrides.pop(get_credential_secret_store, None)


def test_log_credential_change_preserves_learned_state_but_storage_change_resets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "studio.db"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))
    store = MemorySecretStore()
    app.dependency_overrides[get_credential_secret_store] = lambda: store
    try:
        with TestClient(app) as client:
            project = client.post(
                "/api/v1/projects",
                json={"name": "log-control-lifecycle"},
            ).json()
            environment = client.post(
                f"/api/v1/projects/{project['id']}/environments",
                json={"name": "dev"},
            ).json()
            profiles = [
                client.post(
                    "/api/v1/credential-profiles",
                    json={
                        "name": name,
                        "provider": "s3",
                        "auth_type": "access_key",
                        "config": {"access_key_id": f"{name}-id"},
                        "secret": {"secret_access_key": f"{name}-secret"},
                    },
                ).json()
                for name in ("S3 primary", "S3 rotated")
            ]
            source = client.post(
                f"/api/v1/environments/{environment['id']}/log-sources",
                json={
                    "uri": "s3://analytics/logs",
                    "enabled": False,
                    "storage": {
                        "provider": "s3",
                        "auth_mode": "credential_profile",
                        "credential_profile_id": profiles[0]["id"],
                        "options": {"region": "ap-southeast-1"},
                    },
                },
            ).json()
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    """
                    INSERT INTO log_stream_states (
                        source_id, stream_kind, root_uri, partition_format,
                        partition_granularity, checkpoint_partition_value,
                        boundary_last_modified, last_scanned_partition_value,
                        layout_status, created_at, updated_at
                    ) VALUES (?, 'job_jsonl', 's3://analytics/logs/job_run_log',
                              '%Y-%m-%d', 'day', '2026-07-22',
                              '2026-07-22T00:00:00+00:00', '2026-07-22',
                              'learned', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (source["id"],),
                )
                connection.execute(
                    """
                    INSERT INTO log_file_manifest (
                        source_id, file_uri, file_kind, revision_json,
                        row_count, status, first_seen_at, last_seen_at
                    ) VALUES (?, 's3://analytics/logs/job.jsonl', 'job_jsonl',
                              '{}', 0, 'ok', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (source["id"],),
                )

            rotated = client.patch(
                f"/api/v1/environments/{environment['id']}/log-sources/{source['id']}",
                json={
                    "storage": {
                        "provider": "s3",
                        "auth_mode": "credential_profile",
                        "credential_profile_id": profiles[1]["id"],
                        "options": {"region": "ap-southeast-1"},
                    },
                },
            )
            assert rotated.status_code == 200, rotated.text
            with sqlite3.connect(db_path) as connection:
                assert connection.execute(
                    "SELECT count(*) FROM log_stream_states WHERE source_id = ?",
                    (source["id"],),
                ).fetchone()[0] == 1
                assert connection.execute(
                    "SELECT count(*) FROM log_file_manifest WHERE source_id = ?",
                    (source["id"],),
                ).fetchone()[0] == 1

            changed_storage = client.patch(
                f"/api/v1/environments/{environment['id']}/log-sources/{source['id']}",
                json={
                    "storage": {
                        "provider": "s3",
                        "auth_mode": "credential_profile",
                        "credential_profile_id": profiles[1]["id"],
                        "options": {"region": "us-east-1"},
                    },
                },
            )
            assert changed_storage.status_code == 200, changed_storage.text
            with sqlite3.connect(db_path) as connection:
                assert connection.execute(
                    "SELECT count(*) FROM log_stream_states WHERE source_id = ?",
                    (source["id"],),
                ).fetchone()[0] == 0
                assert connection.execute(
                    "SELECT count(*) FROM log_file_manifest WHERE source_id = ?",
                    (source["id"],),
                ).fetchone()[0] == 0
    finally:
        app.dependency_overrides.pop(get_credential_secret_store, None)


def test_storage_connection_validation_is_read_only_and_bounded(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))
    root = tmp_path / "metadata"
    root.mkdir()
    (root / "one.json").write_text("{}", encoding="utf-8")
    (root / "two.json").write_text("{}", encoding="utf-8")

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/storage-connections/validate",
            json={
                "uri": str(root),
                "storage": {"provider": "local", "auth_mode": "none"},
            },
        )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["object_type"] == "directory"
    assert response.json()["objects_scanned"] == 2
    assert response.json()["metadata_write_back_supported"] is True


def test_onelake_connection_probe_stops_after_one_matching_object(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))
    from datacoolie_studio.domains.storage.adapters import StorageObject
    from datacoolie_studio.domains.storage.inventory import StorageInventory

    observed_requests = []

    class ProbeAdapter:
        def canonical_uri(self, uri: str) -> str:
            return uri

        def inventory(self, request):
            observed_requests.append(request)
            return StorageInventory(
                objects=(
                    StorageObject(
                        canonical_uri=f"{request.uri}/metadata",
                        name="metadata",
                        object_type="directory",
                    ),
                ),
                completeness="partial",
                requests=1,
                pages=1,
                directories_visited=1,
                objects_inspected=2,
                matching_objects=2,
                retries=0,
                throttles=0,
                bytes_read=0,
                duration_ms=1,
                early_stop_reason="object_limit",
            )

    monkeypatch.setattr(
        "datacoolie_studio.domains.sources.service.create_storage_adapter",
        lambda *_args, **_kwargs: ProbeAdapter(),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/storage-connections/validate",
            json={
                "uri": (
                    "https://onelake.dfs.fabric.microsoft.com/Analytics/"
                    "Telemetry.Lakehouse/Files/project"
                ),
                "storage": {
                    "provider": "onelake",
                    "auth_mode": "ambient",
                },
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"
    assert response.json()["objects_scanned"] == 1
    assert response.json()["metadata_write_back_supported"] is False
    assert observed_requests[0].object_limit == 1
    assert observed_requests[0].recursive is False


def test_adls_aliases_share_one_source_registration_and_keep_first_input(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))
    storage = {"provider": "adls", "auth_mode": "ambient"}
    abfss_uri = (
        "abfss://test@datateamtest01.dfs.core.windows.net/metadata/assets.json"
    )
    https_uri = (
        "https://datateamtest01.dfs.core.windows.net/test/metadata/assets.json"
    )
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "aliases"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        first = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={"uri": abfss_uri, "enabled": False, "storage": storage},
        )
        duplicate = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={"uri": https_uri, "enabled": False, "storage": storage},
        )
        sources = client.get(
            f"/api/v1/environments/{environment['id']}/metadata-sources"
        )

    assert first.status_code == 200, first.text
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["id"] == first.json()["id"]
    assert duplicate.json()["uri"] == (
        "abfs://test@datateamtest01.dfs.core.windows.net/metadata/assets.json"
    )
    assert duplicate.json()["configured_location"]["input_uri"] == abfss_uri
    assert len(sources.json()) == 1


def test_onelake_files_aliases_share_registration_and_tables_are_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))
    storage = {"provider": "onelake", "auth_mode": "ambient"}
    abfss_uri = (
        "abfss://Analytics%20Workspace@onelake.dfs.fabric.microsoft.com/"
        "Telemetry.Lakehouse/Files/metadata/assets.json"
    )
    https_uri = (
        "https://onelake.dfs.fabric.microsoft.com/Analytics%20Workspace/"
        "Telemetry.lakehouse/Files/metadata/assets.json"
    )
    with TestClient(app) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "onelake-files"}
        ).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        first = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={
                "uri": abfss_uri,
                "enabled": False,
                "storage": storage,
            },
        )
        duplicate = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={
                "uri": https_uri,
                "enabled": False,
                "storage": storage,
            },
        )
        tables = client.post(
            f"/api/v1/environments/{environment['id']}/code-artifacts",
            json={
                "uri": (
                    "abfss://Analytics%20Workspace@onelake.dfs.fabric.microsoft.com/"
                    "Telemetry.Lakehouse/Tables/table"
                ),
                "enabled": False,
                "storage": storage,
            },
        )

    assert first.status_code == 200, first.text
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["id"] == first.json()["id"]
    assert duplicate.json()["uri"] == abfss_uri
    assert duplicate.json()["configured_location"]["input_uri"] == abfss_uri
    assert tables.status_code == 422
    assert "Files" in tables.text


def test_editing_only_an_adls_alias_preserves_source_sync_state(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "studio.db"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))
    storage = {"provider": "adls", "auth_mode": "ambient"}
    canonical = "abfs://test@datateamtest01.dfs.core.windows.net/metadata/assets.json"
    alias = "https://datateamtest01.dfs.core.windows.net/test/metadata/assets.json"
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "alias-edit"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        source = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={"uri": canonical, "enabled": False, "storage": storage},
        ).json()
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                """
                    UPDATE source_observations
                    SET last_outcome = 'changed',
                        last_attempted_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE source_id = ?
                """,
                (source["id"],),
            )
        updated = client.patch(
            f"/api/v1/environments/{environment['id']}/metadata-sources/{source['id']}",
            json={"uri": alias},
        )

    assert updated.status_code == 200, updated.text
    assert updated.json()["uri"] == canonical
    assert updated.json()["configured_location"]["input_uri"] == alias
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM source_observations WHERE source_id = ?",
            (source["id"],),
        ).fetchone()[0] == 1


def test_log_secondary_locations_are_canonical_for_collection_and_keep_raw_input(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))
    etl_input = "abfss://test@account.dfs.core.windows.net/logs/etl"
    system_input = "https://account.dfs.core.windows.net/test/logs/system"
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "log-locations"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        response = client.post(
            f"/api/v1/environments/{environment['id']}/log-sources",
            json={
                "uri": etl_input,
                "enabled": False,
                "storage": {"provider": "adls", "auth_mode": "ambient"},
                "source_config": {
                    "mode": "separate_paths",
                    "etl_logs_uri": etl_input,
                    "system_logs_uri": system_input,
                },
            },
        )
        second = client.post(
            f"/api/v1/environments/{environment['id']}/log-sources",
            json={
                "uri": etl_input,
                "enabled": False,
                "storage": {"provider": "adls", "auth_mode": "ambient"},
                "source_config": {
                    "mode": "separate_paths",
                    "etl_logs_uri": etl_input,
                    "system_logs_uri": (
                        "https://account.dfs.core.windows.net/test/logs/system-2"
                    ),
                },
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["uri"] == "abfs://test@account.dfs.core.windows.net/logs/etl"
    assert body["source_config"]["etl_logs_uri"] == (
        "abfs://test@account.dfs.core.windows.net/logs/etl"
    )
    assert body["source_config"]["system_logs_uri"] == (
        "abfs://test@account.dfs.core.windows.net/logs/system"
    )
    assert body["configured_location"]["input_locations"] == {
        "etl_logs_uri": etl_input,
        "system_logs_uri": system_input,
    }
    assert second.status_code == 200, second.text
    assert second.json()["id"] != body["id"]


def test_equal_minio_uris_on_different_endpoints_are_distinct_sources(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "minio-scope"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        source_ids = []
        for endpoint in ("https://minio-a.example", "https://minio-b.example"):
            response = client.post(
                f"/api/v1/environments/{environment['id']}/log-sources",
                json={
                    "uri": "s3://analytics/logs",
                    "enabled": False,
                    "storage": {
                        "provider": "minio",
                        "auth_mode": "anonymous",
                        "options": {"endpoint_url": endpoint},
                    },
                },
            )
            assert response.status_code == 200, response.text
            source_ids.append(response.json()["id"])

    assert source_ids[0] != source_ids[1]


def test_dbfs_source_accepts_volume_aliases_and_persists_canonical_uri(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        response = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={
                "uri": "/Volumes/catalog/schema/volume/project/metadata",
                "enabled": False,
                "source_config": {
                    "metadata_root_uri": (
                        "Volumes/catalog/schema/volume/project/metadata"
                    ),
                },
                "storage": {
                    "provider": "dbfs",
                    "auth_mode": "ambient",
                },
            },
        )

    assert response.status_code == 200, response.text
    assert (
        response.json()["uri"]
        == "dbfs:/Volumes/catalog/schema/volume/project/metadata"
    )
    assert response.json()["storage"]["provider"] == "dbfs"


def test_remote_client_cannot_attach_stored_profile(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))
    with TestClient(app, client=("203.0.113.8", 50000)) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        response = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={
                "uri": "s3://bucket/metadata.json",
                "storage": {
                    "provider": "s3",
                    "auth_mode": "credential_profile",
                    "credential_profile_id": "00000000-0000-0000-0000-000000000000",
                },
            },
            headers={"X-Forwarded-For": "127.0.0.1"},
        )

    assert response.status_code == 403
