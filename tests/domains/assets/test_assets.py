from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from datacoolie_studio.domains.assets.service import build_asset_detail, build_assets_inventory
from datacoolie_studio.domains.assets.reference_source import build_reference_occurrence_source
from datacoolie_studio.domains.lineage.service import build_lineage
from datacoolie_studio.domains.metadata.normalizer import normalize_metadata_document


def test_reference_occurrence_source_preview_returns_python_and_evaluated_sql(tmp_path: Path):
    source_file = tmp_path / "functions.py"
    source_file.write_text(
        """
def read_orders(engine):
    query = \"SELECT * FROM silver.orders\"
    return engine.execute_sql(query)
""".strip(),
        encoding="utf-8",
    )
    from datacoolie_studio.db.models import EnvironmentSource

    artifact = EnvironmentSource(
        id=17,
        environment_id=1,
        source_kind="code",
        uri=str(tmp_path),
        enabled=True,
        source_config_json='{"artifact_type":"directory"}',
    )
    occurrence = {
        "id": "reference-occurrence:test",
        "consumer_asset_id": "asset:consumer",
        "provenance": "python_sql",
        "raw_value": "silver.orders",
        "source_location": {
            "function_path": "functions.read_orders",
            "path": "functions.py",
            "line": 3,
            "column": 11,
            "end_line": 3,
            "end_column": 36,
            "coordinate_space": "function_source",
        },
        "observations": [{
            "sql": "SELECT * FROM silver.orders",
            "details": {
                "code_artifact_source_id": 17,
                "resolved_sql_location": {"line": 1, "column": 14, "end_line": 1, "end_column": 27},
            },
        }],
    }

    preview = build_reference_occurrence_source(occurrence, None, [artifact])

    assert [item["id"] for item in preview["views"]] == ["consumer_source", "evaluated_sql"]
    assert preview["views"][0]["function_path"] == "functions.read_orders"
    assert preview["views"][0]["matches"][0]["precision"] == "exact_reference"
    assert preview["views"][1]["matches"][0]["precision"] == "exact_reference"


def test_build_assets_inventory_covers_canonical_assets_references_provenance_and_attention():
    first = normalize_metadata_document(1, "first.json", _metadata_source_one())
    second = normalize_metadata_document(2, "second.json", _metadata_source_two())

    lineage = build_lineage({"_documents": [first, second], "errors": []}, environment_id=42)
    payload = build_assets_inventory(lineage, {1: "first.json", 2: "second.json"})

    asset_rows = payload["assets"]
    reference_rows = payload["reference_groups"]
    occurrence_rows = payload["reference_occurrences"]
    assert payload["summary"]["assets"] == len(asset_rows)
    assert payload["summary"]["references"] == len(reference_rows)
    assert payload["summary"]["visible"] == len(payload["assets"]) + len(payload["reference_groups"])
    assert payload["summary"]["asset_attention"] >= 1
    assert all("record_type" not in item for item in asset_rows)

    unresolved_reference = next(
        item for item in reference_rows
        if item["resolution"] == {"state": "unresolved", "reason": "no_match"}
    )
    unresolved_occurrence = next(item for item in occurrence_rows if item["reference_id"] == unresolved_reference["id"])
    assert unresolved_reference["reference_type"] == "table_reference"
    assert unresolved_reference["normalized_value"] == "external.orders"
    assert unresolved_occurrence["consumer_asset"]
    external_orders_flow = next(item for item in lineage["dataflows"] if item["name"] == "query_external_orders")
    assert unresolved_occurrence["dataflow_ids"] == [external_orders_flow["dataflow_id"]]
    assert unresolved_reference["dataflow_ids"] == [external_orders_flow["dataflow_id"]]
    assert all(
        not (item.get("schema_name") == "external" and item.get("table") == "orders")
        for item in asset_rows
    )

    merged_sales = next(
        item for item in asset_rows
        if item.get("schema_name") == "sales" and item.get("table") == "orders"
    )
    assert merged_sales["metadata_source_ids"] == [1, 2]
    assert len(merged_sales["metadata_sources"]) == 2
    assert merged_sales["used_by_count"] == 1
    assert "destination" in merged_sales["roles"]
    assert any("source" in item["roles"] for item in asset_rows)

    python_asset = next(
        item for item in asset_rows
        if item.get("asset_type") == "python_function"
    )
    assert python_asset["friendly_name"] == "orders_reader_alias"
    assert python_asset["python_function"] == "functions.sources.read_orders"
    assert python_asset["full_identity"] == "lake · functions.sources.read_orders"

    ambiguous_reference = next(
        item for item in reference_rows
        if item["resolution"] == {"state": "unresolved", "reason": "multiple_matches"}
    )
    assert ambiguous_reference["attention_count"] == 1
    ambiguous_attention = ambiguous_reference["attention_items"][0]
    assert ambiguous_attention["source_type"] == "sql_reference"
    assert ambiguous_attention["reference_id"].startswith("reference:")
    assert ambiguous_attention["reference_occurrence_id"].startswith("reference-occurrence:")
    assert ambiguous_reference["reference_type"] == "table_reference"
    assert ambiguous_reference["candidate_assets"]
    candidate_identities = {item["full_identity"] for item in ambiguous_reference["candidate_assets"]}
    assert "lake · main.warehouse.archive.orders" in candidate_identities
    assert "lake · main.warehouse.sales.orders" in candidate_identities

    assert "unresolved" in payload["filter_options"]["reference_groups"]["resolution_states"]
    assert "table_reference" in payload["filter_options"]["reference_groups"]["reference_types"]
    assert "source" in payload["filter_options"]["assets"]["roles"]
    assert "destination" in payload["filter_options"]["assets"]["roles"]
    assert "with_attention" in payload["filter_options"]["assets"]["attention_states"]
    assert "with_attention" in payload["filter_options"]["reference_groups"]["attention_states"]

    table_order = [
        (item.get("schema_name"), item.get("table"))
        for item in asset_rows
        if item.get("connection_name") == "lake"
        and item.get("database") == "warehouse"
        and item.get("asset_type") == "table"
    ]
    assert table_order == sorted(table_order, key=lambda item: (item[0] or "", item[1] or ""))

    detail_rows = [
        build_asset_detail(item["id"], payload, lineage)
        for item in asset_rows
    ]
    dependency_inputs = [
        dependency
        for detail in detail_rows
        if detail is not None
        for dependency in detail["depends_on"]
    ]
    assert dependency_inputs
    assert all(
        dependency["reference_id"].startswith("reference:")
        for dependency in dependency_inputs
    )
    assert all(
        dependency["source_reference"]["id"] == dependency["reference_id"]
        for dependency in dependency_inputs
        if dependency.get("source_reference")
    )


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
        asset_rows = payload["items"]
        assert payload["summary"]["assets"] == len(asset_rows)
        assert "pagination" not in payload
        assert payload["summary"]["references"] >= 1
        assert "identifiers" not in asset_rows[0]
        assert "observations" not in asset_rows[0]
        assert "identifier_count" in asset_rows[0]
        assert listing.headers["etag"]
        assert listing.headers.get("content-encoding") == "gzip"
        assert client.get(
            f"/api/v1/environments/{environment['id']}/assets",
            headers={"If-None-Match": listing.headers["etag"]},
        ).status_code == 304

        references = client.get(f"/api/v1/environments/{environment['id']}/asset-references")
        assert references.status_code == 200
        reference_rows = references.json()["items"]
        assert len(reference_rows) == payload["summary"]["references"]
        assert "pagination" not in references.json()
        assert any(item["resolution"]["state"] == "unresolved" for item in reference_rows)
        assert all(item["occurrence_count"] >= 1 for item in reference_rows)
        assert all("occurrence_ids" not in item or item["occurrence_ids"] == [] for item in reference_rows)
        method_row = next(item for item in reference_rows if item["resolution_methods"])
        method_search = client.get(
            f"/api/v1/environments/{environment['id']}/asset-references",
            params={"q": method_row["resolution_methods"][0]},
        ).json()["items"]
        assert method_row["id"] in {item["id"] for item in method_search}
        resolved_row = next(item for item in reference_rows if item.get("resolved_asset"))
        resolved_search = client.get(
            f"/api/v1/environments/{environment['id']}/asset-references",
            params={"q": resolved_row["resolved_asset"]["display_name"]},
        ).json()["items"]
        assert resolved_row["id"] in {item["id"] for item in resolved_search}
        assert payload["summary"]["asset_attention"] >= 1
        assert asset_rows

        filtered = client.get(
            f"/api/v1/environments/{environment['id']}/assets",
            params={"asset_type": asset_rows[0]["asset_type"]},
        ).json()
        assert len(filtered["items"]) >= 1
        assert all(item["asset_type"] == asset_rows[0]["asset_type"] for item in filtered["items"])
        assert client.get(f"/api/v1/environments/{environment['id']}/assets?sort_by=invalid").status_code == 422

        reference_detail = client.get(
            f"/api/v1/environments/{environment['id']}/asset-references/{reference_rows[0]['id']}"
        )
        assert reference_detail.status_code == 200
        assert reference_detail.json()["reference"]["id"] == reference_rows[0]["id"]

        asset_id = asset_rows[0]["id"]
        detail = client.get(f"/api/v1/environments/{environment['id']}/assets/{asset_id}")
        assert detail.status_code == 200
        detail_payload = detail.json()
        assert detail_payload["asset"]["id"] == asset_id
        assert "attention_items" in detail_payload
        assert detail_payload["direct_relationships"]["upstream_assets"] >= 0
        assert detail_payload["direct_relationships"]["downstream_assets"] >= 0
        assert "position" in detail_payload["direct_relationships"]
        assert isinstance(detail_payload["upstream_assets"], list)
        assert isinstance(detail_payload["downstream_assets"], list)
        assert isinstance(detail_payload["input_flows"], list)
        assert isinstance(detail_payload["output_flows"], list)
        assert isinstance(detail_payload["depends_on"], list)
        assert isinstance(detail_payload["used_by"], list)

        sql_asset = next(item for item in asset_rows if item["asset_type"] == "sql_query")
        sql_detail = client.get(
            f"/api/v1/environments/{environment['id']}/assets/{sql_asset['id']}"
        ).json()
        assert "raw" not in sql_detail["definition"]
        source_response = client.get(
            f"/api/v1/environments/{environment['id']}/assets/{sql_asset['id']}/source"
        )
        assert source_response.status_code == 200
        assert source_response.headers["cache-control"] == "private, no-store"
        assert source_response.json()["definition"]["raw"]

        structural_before = client.get(
            f"/api/v1/environments/{environment['id']}/freshness"
        ).json()["structural_cache_version"]
        unresolved = next(item for item in reference_rows if item["resolution"]["state"] == "unresolved")
        target = next(item["mapping_target"] for item in asset_rows if item.get("mapping_target"))
        mapping = client.post(
            f"/api/v1/projects/{project['id']}/reference-mappings",
            json={
                "reference_type": unresolved["reference_type"],
                "reference_value": unresolved["normalized_value"],
                "target_identifier_kind": target["kind"],
                "target_value": target["value"],
                "target_display_value": target["display"],
            },
        )
        assert mapping.status_code == 200
        structural_after = client.get(
            f"/api/v1/environments/{environment['id']}/freshness"
        ).json()["structural_cache_version"]
        assert structural_after != structural_before
        assert client.get(f"/api/v1/environments/{environment['id']}/assets").json()["catalog_version"] != payload["catalog_version"]

        project_registry = client.get(f"/api/v1/projects/{project['id']}/reference-registry")
        assert project_registry.status_code == 200
        project_row = next(
            item for item in project_registry.json()["rows"]
            if item["normalized_value"] == unresolved["normalized_value"]
        )
        assert project_row["resolution"]["state"] == "manual"
        assert project_row["mapping"]["id"] == mapping.json()["id"]

        cleared = client.delete(
            f"/api/v1/projects/{project['id']}/reference-mappings/{mapping.json()['id']}"
        )
        assert cleared.status_code == 204
        cleared_row = next(
            item for item in client.get(f"/api/v1/projects/{project['id']}/reference-registry").json()["rows"]
            if item["normalized_value"] == unresolved["normalized_value"]
        )
        assert cleared_row["resolution"]["state"] == "unresolved"
        assert cleared_row["mapping"] is None

        with sqlite3.connect(tmp_path / "read-models.sqlite3") as connection:
            assert connection.execute(
                "select count(*) from result_cache_entries where namespace = 'assets.catalog'"
            ).fetchone()[0] == 1

        if detail_payload["upstream_assets"]:
            neighbor = detail_payload["upstream_assets"][0]
            assert "asset" in neighbor
            assert "relation_flow_count" in neighbor
            assert "relation_dependency_count" in neighbor

        missing = client.get(f"/api/v1/environments/{environment['id']}/assets/asset:missing")
        assert missing.status_code == 404
        assert client.get("/api/v1/environments/999999/assets").status_code == 404
        assert client.get("/api/v1/environments/999999/asset-references").status_code == 404

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)


def test_asset_detail_definition_formats_sql_and_resolves_python_source(tmp_path: Path):
    from datacoolie_studio.db.models import EnvironmentSource

    document = normalize_metadata_document(1, "source.json", _metadata_source_one())
    lineage = build_lineage({"_documents": [document], "errors": []}, environment_id=42)
    payload = build_assets_inventory(lineage, {1: "source.json"})

    sql_asset = next(
        item for item in payload["assets"]
        if item["asset_type"] == "sql_query" and item.get("query") == "SELECT * FROM sales.orders"
    )
    sql_detail = build_asset_detail(sql_asset["id"], payload, lineage)

    assert sql_detail is not None
    sql_definition = sql_detail["definition"]
    assert sql_definition["kind"] == "sql_query"
    assert sql_definition["status"] == "available"
    assert sql_definition["raw"] == "SELECT * FROM sales.orders"
    assert "\nFROM" in sql_definition["formatted"]

    package = tmp_path / "src" / "functions"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "sources.py").write_text(
        """
def helper():
    return "not included"


def read_orders(engine):
    query = "SELECT * FROM raw.orders"
    return engine.execute_sql(query)
""",
        encoding="utf-8",
    )
    artifact = EnvironmentSource(
        id=99,
        environment_id=42,
        source_kind="code",
        uri=str(tmp_path),
        enabled=True,
        source_config_json='{"artifact_type": "directory", "module_roots": ["src"]}',
    )
    python_asset = next(item for item in payload["assets"] if item["asset_type"] == "python_function")
    python_detail = build_asset_detail(python_asset["id"], payload, lineage, [artifact])

    assert python_detail is not None
    python_definition = python_detail["definition"]
    assert python_definition["kind"] == "python_function"
    assert python_definition["status"] == "available"
    assert python_definition["module_name"] == "functions.sources"
    assert python_definition["relative_path"] == "src/functions/sources.py"
    assert "def read_orders" in python_definition["source"]
    assert "def helper" not in python_definition["source"]


def test_mssql_sql_asset_formats_and_keeps_sql_references():
    query = """
    SELECT [Border].STAsBinary() AS [Border], [CountryID]
    FROM [Person].[StateProvince]
    """
    document = normalize_metadata_document(
        1,
        "mssql.json",
        {
            "connections": [
                {
                    "name": "mssql",
                    "connection_type": "database",
                    "format": "sql",
                    "configure": {"database_type": "mssql"},
                },
                {
                    "name": "lake",
                    "connection_type": "file",
                    "format": "parquet",
                    "configure": {"base_path": "./data"},
                },
            ],
            "dataflows": [
                {
                    "name": "read_state_province",
                    "source": {"connection_name": "mssql", "query": query},
                    "destination": {"connection_name": "lake", "table": "state_province"},
                },
            ],
        },
    )

    lineage = build_lineage({"_documents": [document], "errors": []}, environment_id=42)
    payload = build_assets_inventory(lineage, {1: "mssql.json"})
    sql_asset = next(item for item in payload["assets"] if item["asset_type"] == "sql_query")
    detail = build_asset_detail(sql_asset["id"], payload, lineage)

    assert sql_asset["database_type"] == "mssql"
    assert not any(item["code"] == "sql_parse_error" for item in lineage["diagnostics"])
    assert lineage["references"]
    assert detail is not None
    definition = detail["definition"]
    assert definition["diagnostics"] == []
    assert "\nFROM [Person].[StateProvince]" in definition["formatted"]


def test_asset_detail_definition_resolves_manually_selected_package_directory(tmp_path: Path):
    from datacoolie_studio.db.models import EnvironmentSource

    document = normalize_metadata_document(1, "source.json", _metadata_source_one())
    lineage = build_lineage({"_documents": [document], "errors": []}, environment_id=42)
    payload = build_assets_inventory(lineage, {1: "source.json"})
    package = tmp_path / "functions"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "sources.py").write_text(
        "def read_orders(engine):\n    return engine.execute_sql('SELECT 1')\n",
        encoding="utf-8",
    )
    artifact = EnvironmentSource(
        id=99,
        environment_id=42,
        source_kind="code",
        uri=str(package),
        enabled=True,
        source_config_json='{"artifact_type": "directory", "module_roots": []}',
    )
    python_asset = next(item for item in payload["assets"] if item["asset_type"] == "python_function")

    detail = build_asset_detail(python_asset["id"], payload, lineage, [artifact])

    assert detail is not None
    definition = detail["definition"]
    assert definition["status"] == "available"
    assert definition["module_name"] == "functions.sources"
    assert definition["relative_path"] == "sources.py"
    assert "def read_orders" in definition["source"]


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
                "name": "query_sales_orders",
                "source": {
                    "connection_name": "lake",
                    "query": "SELECT * FROM sales.orders",
                },
                "destination": {"connection_name": "lake", "schema_name": "curated", "table": "orders_from_sales"},
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
            {
                "name": "python_orders",
                "source": {
                    "connection_name": "lake",
                    "python_function": "functions.sources.read_orders",
                    "schema_name": "raw",
                    "table": "orders_reader_alias",
                },
                "destination": {"connection_name": "lake", "schema_name": "curated", "table": "orders_python"},
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
