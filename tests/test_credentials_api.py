from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.requests import Request

from datacoolie_studio.api.v1.routes.credentials import (
    get_credential_secret_store,
    require_loopback_client,
)
from datacoolie_studio.main import app

from test_credentials_service import MemorySecretStore


def test_credential_api_roundtrip_is_write_only_for_secrets(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))
    store = MemorySecretStore()
    app.dependency_overrides[get_credential_secret_store] = lambda: store
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/credential-profiles",
                json={
                    "name": "S3",
                    "provider": "s3",
                    "auth_type": "access_key",
                    "config": {"access_key_id": "key-id"},
                    "secret": {"secret_access_key": "never-return-this"},
                },
            )
            assert created.status_code == 201, created.text
            profile = created.json()
            assert "never-return-this" not in created.text
            assert profile["secret_state"] == "present"

            listed = client.get("/api/v1/credential-profiles")
            fetched = client.get(
                f"/api/v1/credential-profiles/{profile['id']}"
            )
            assert listed.status_code == 200
            assert fetched.status_code == 200
            assert "never-return-this" not in listed.text
            assert "never-return-this" not in fetched.text
            assert "config" not in listed.json()[0]
            assert fetched.json()["config"] == {"access_key_id": "key-id"}

            edited = client.patch(
                f"/api/v1/credential-profiles/{profile['id']}",
                json={
                    "name": "S3 edited",
                    "config": {"access_key_id": "edited-key-id"},
                },
            )
            assert edited.status_code == 200, edited.text
            assert edited.json()["name"] == "S3 edited"
            assert store.values[profile["id"]]["secret_access_key"] == "never-return-this"

            replaced = client.patch(
                f"/api/v1/credential-profiles/{profile['id']}",
                json={"secret": {"secret_access_key": "rotated-value"}},
            )
            assert replaced.status_code == 200, replaced.text
            assert replaced.json()["version"] == 3
            assert "rotated-value" not in replaced.text

            deleted = client.delete(
                f"/api/v1/credential-profiles/{profile['id']}"
            )
            assert deleted.status_code == 204
            assert client.get(
                f"/api/v1/credential-profiles/{profile['id']}"
            ).status_code == 404
    finally:
        app.dependency_overrides.pop(get_credential_secret_store, None)


def test_capabilities_fail_closed_when_keyring_is_unavailable(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))
    store = MemorySecretStore(available=False)
    app.dependency_overrides[get_credential_secret_store] = lambda: store
    try:
        with TestClient(app) as client:
            capabilities = client.get(
                "/api/v1/credential-profiles/capabilities"
            )
            assert capabilities.status_code == 200
            assert capabilities.json()["secret_store_available"] is False

            response = client.post(
                "/api/v1/credential-profiles",
                json={
                    "name": "S3",
                    "provider": "s3",
                    "auth_type": "access_key",
                    "config": {"access_key_id": "key-id"},
                    "secret": {"secret_access_key": "sensitive"},
                },
            )
            assert response.status_code == 503
            assert "sensitive" not in response.text
    finally:
        app.dependency_overrides.pop(get_credential_secret_store, None)


def test_loopback_guard_ignores_forwarded_headers():
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/credential-profiles",
            "headers": [(b"x-forwarded-for", b"127.0.0.1")],
            "client": ("203.0.113.8", 443),
            "server": ("studio.example", 443),
            "scheme": "https",
            "query_string": b"",
        }
    )

    try:
        require_loopback_client(request)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 403
        assert "203.0.113.8" not in json.dumps(getattr(exc, "detail", ""))
    else:
        raise AssertionError("Non-loopback client must be rejected")
