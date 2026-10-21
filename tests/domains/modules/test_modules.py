from __future__ import annotations


def test_modules_catalog_and_toggle(studio_client):
    modules = studio_client.get("/api/v1/studio/modules").json()
    by_key = {module["key"]: module for module in modules}

    assert by_key["metadata"]["enabled"] is True
    assert by_key["metadata"]["togglable"] is True
    assert by_key["master-data"]["enabled"] is False
    assert by_key["master-data"]["status"] == "coming_soon"

    # Disable the metadata module.
    disabled = studio_client.patch(
        "/api/v1/studio/modules/metadata",
        json={"enabled": False},
    ).json()
    assert disabled["enabled"] is False

    refreshed = studio_client.get("/api/v1/studio/modules").json()
    assert {module["key"]: module["enabled"] for module in refreshed}["metadata"] is False

    # Re-enable it.
    enabled = studio_client.patch(
        "/api/v1/studio/modules/metadata",
        json={"enabled": True},
    ).json()
    assert enabled["enabled"] is True


def test_modules_toggle_rejects_unknown_and_non_togglable(studio_client):
    assert (
        studio_client.patch(
            "/api/v1/studio/modules/does-not-exist",
            json={"enabled": True},
        ).status_code
        == 404
    )
    # master-data is not togglable (coming soon).
    assert (
        studio_client.patch(
            "/api/v1/studio/modules/master-data",
            json={"enabled": True},
        ).status_code
        == 409
    )
