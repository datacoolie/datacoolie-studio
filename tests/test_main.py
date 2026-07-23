from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from datacoolie_studio.main import app, spa_fallback


def test_spa_root_deep_link_and_root_asset_are_served_from_static_package():
    client = TestClient(app)

    root = client.get("/")
    deep_link = client.get("/projects/1/environments/2/monitoring")
    favicon = client.get("/favicon.png")

    assert root.status_code == 200
    assert root.headers["content-type"].startswith("text/html")
    assert deep_link.status_code == 200
    assert deep_link.content == root.content
    assert favicon.status_code == 200
    assert favicon.headers["content-type"] == "image/png"
    assert favicon.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_unknown_api_path_does_not_fall_through_to_spa():
    response = TestClient(app).get("/api/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


@pytest.mark.parametrize(
    "path",
    [
        "/%2e%2e/%2e%2e/%2e%2e/pyproject.toml",
        "/%2E%2E/%2E%2E/%2E%2E/pyproject.toml",
        "/..%2F..%2F..%2Fpyproject.toml",
        "/%2e%2e%2f%2e%2e%2f%2e%2e%2fpyproject.toml",
        "/%2e%2e%5c%2e%2e%5c%2e%2e%5cpyproject.toml",
        "/C:%5CWindows%5Cwin.ini",
        "/%5C%5Clocalhost%5Cc$%5CWindows%5Cwin.ini",
    ],
)
def test_encoded_static_traversal_is_rejected(path: str):
    response = TestClient(app).get(path)

    assert response.status_code == 404
    assert b"[build-system]" not in response.content


@pytest.mark.parametrize(
    "path",
    [
        "../../../pyproject.toml",
        "../main.py",
        r"..\..\..\pyproject.toml",
        r"C:\Windows\win.ini",
        r"\\localhost\c$\Windows\win.ini",
    ],
)
def test_spa_fallback_rejects_non_http_normalized_traversal(path: str):
    with pytest.raises(HTTPException) as error:
        spa_fallback(path)

    assert error.value.status_code == 404


def test_spa_fallback_never_serves_an_existing_file_outside_static_root():
    repository_config = Path(__file__).parents[1] / "pyproject.toml"

    assert repository_config.is_file()
    with pytest.raises(HTTPException) as error:
        spa_fallback("../../../pyproject.toml")

    assert error.value.status_code == 404
