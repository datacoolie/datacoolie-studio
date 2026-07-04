from __future__ import annotations

import os
from pathlib import Path


def test_modules_catalog_and_toggle(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        modules = client.get("/api/v1/studio/modules").json()
        by_key = {module["key"]: module for module in modules}

        assert by_key["metadata"]["enabled"] is True
        assert by_key["metadata"]["togglable"] is True
        assert by_key["master-data"]["enabled"] is False
        assert by_key["master-data"]["status"] == "coming_soon"

        # Disable the metadata module.
        disabled = client.patch("/api/v1/studio/modules/metadata", json={"enabled": False}).json()
        assert disabled["enabled"] is False

        refreshed = client.get("/api/v1/studio/modules").json()
        assert {m["key"]: m["enabled"] for m in refreshed}["metadata"] is False

        # Re-enable it.
        enabled = client.patch("/api/v1/studio/modules/metadata", json={"enabled": True}).json()
        assert enabled["enabled"] is True

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_modules_toggle_rejects_unknown_and_non_togglable(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        assert client.patch("/api/v1/studio/modules/does-not-exist", json={"enabled": True}).status_code == 404
        # master-data is not togglable (coming soon).
        assert client.patch("/api/v1/studio/modules/master-data", json={"enabled": True}).status_code == 409

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)
