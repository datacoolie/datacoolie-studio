from __future__ import annotations

import json
import os
from pathlib import Path

from datacoolie_studio.domains.assets.service import build_assets_inventory
from datacoolie_studio.domains.lineage.service import build_lineage
from datacoolie_studio.domains.metadata.normalizer import normalize_metadata_document


def test_build_assets_inventory_covers_declared_discovered_stitched_and_issues():
    first = normalize_metadata_document(1, "first.json", _metadata_source_one())
    second = normalize_metadata_document(2, "second.json", _metadata_source_two())

    lineage = build_lineage({"_documents": [first, second], "errors": []}, environment_id=42)
    payload = build_assets_inventory(lineage, {1: "first.json", 2: "second.json"})

    assert payload["summary"]["assets"] == len(payload["assets"])
    assert payload["summary"]["declared"] > 0
    assert payload["summary"]["discovered_only"] >= 1
    assert payload["summary"]["stitched"] >= 1
    assert payload["summary"]["with_issues"] >= 1

    discovered_external = next(
        item for item in payload["assets"]
        if item.get("schema_name") == "external" and item.get("table") == "orders"
    )
    assert discovered_external["declaration_status"] == "discovered_only"
    assert discovered_external["metadata_source_ids"] == []

    stitched_sales = next(
        item for item in payload["assets"]
        if item.get("schema_name") == "sales" and item.get("table") == "orders"
    )
    assert stitched_sales["metadata_source_ids"] == [1, 2]
    assert len(stitched_sales["metadata_sources"]) == 2

    ambiguous_query = next(
        item for item in payload["assets"]
        if "SELECT * FROM orders" in str(item.get("query") or "")
    )
    assert ambiguous_query["issue_count"] >= 1
    assert any(issue["code"] in {"dependency_ambiguous", "reference_ambiguous"} for issue in ambiguous_query["issues"])

    assert "declared" in payload["filter_options"]["declaration_statuses"]
    assert "discovered_only" in payload["filter_options"]["declaration_statuses"]
    assert "with_issues" in payload["filter_options"]["issue_states"]
    assert "clean" in payload["filter_options"]["issue_states"]


def test_assets_api_list_and_detail(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    metadata_one = tmp_path / "metadata_one.json"
    metadata_two = tmp_path / "metadata_two.json"
    metadata_one.write_text(json.dumps(_metadata_source_one()), encoding="utf-8")
    metadata_two.write_text(json.dumps(_metadata_source_two()), encoding="utf-8")

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={"uri": str(metadata_one), "label": "primary"},
        )
        client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={"uri": str(metadata_two), "label": "secondary"},
        )

        listing = client.get(f"/api/v1/environments/{environment['id']}/assets")
        assert listing.status_code == 200
        payload = listing.json()
        assert payload["summary"]["assets"] == len(payload["assets"])
        assert payload["summary"]["stitched"] >= 1
        assert payload["summary"]["discovered_only"] >= 1
        assert payload["summary"]["with_issues"] >= 1
        assert payload["assets"]

        asset_id = payload["assets"][0]["id"]
        detail = client.get(f"/api/v1/environments/{environment['id']}/assets/{asset_id}")
        assert detail.status_code == 200
        detail_payload = detail.json()
        assert detail_payload["asset"]["id"] == asset_id
        assert "diagnostics" in detail_payload

        missing = client.get(f"/api/v1/environments/{environment['id']}/assets/asset:missing")
        assert missing.status_code == 404

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def _metadata_source_one() -> dict:
    return {
        "connections": [{
            "name": "lake",
            "connection_type": "lakehouse",
            "format": "delta",
            "catalog": "main",
            "database": "warehouse",
        }],
        "dataflows": [
            {
                "name": "seed_sales",
                "source": {"connection_name": "lake", "schema_name": "raw", "table": "seed"},
                "destination": {"connection_name": "lake", "schema_name": "sales", "table": "orders"},
            },
            {
                "name": "seed_archive",
                "source": {"connection_name": "lake", "schema_name": "raw", "table": "seed"},
                "destination": {"connection_name": "lake", "schema_name": "archive", "table": "orders"},
            },
            {
                "name": "query_external_orders",
                "source": {
                    "connection_name": "lake",
                    "query": "SELECT * FROM external.orders",
                },
                "destination": {"connection_name": "lake", "schema_name": "curated", "table": "orders_external"},
            },
            {
                "name": "query_ambiguous_orders",
                "source": {
                    "connection_name": "lake",
                    "query": "SELECT * FROM orders",
                },
                "destination": {"connection_name": "lake", "schema_name": "curated", "table": "orders_any"},
            },
        ],
    }


def _metadata_source_two() -> dict:
    return {
        "connections": [{
            "name": "lake",
            "connection_type": "lakehouse",
            "format": "delta",
            "catalog": "main",
            "database": "warehouse",
        }],
        "dataflows": [{
            "name": "refresh_sales",
            "source": {"connection_name": "lake", "schema_name": "raw", "table": "seed_2"},
            "destination": {"connection_name": "lake", "schema_name": "sales", "table": "orders"},
        }],
    }
