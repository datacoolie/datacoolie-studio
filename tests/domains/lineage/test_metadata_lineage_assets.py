from __future__ import annotations

import json
from pathlib import Path

from datacoolie_studio.domains.lineage.service import build_lineage
from datacoolie_studio.domains.metadata.normalizer import (
    enrich_metadata_documents_with_connections,
    normalize_metadata_document,
)
from datacoolie_studio.domains.metadata.reader import read_metadata_file
from tests.support.paths import FIXTURE_ROOT


from tests.domains.lineage.lineage_test_support import (
    _api_metadata,
    _catalog_metadata,
    _metadata,
    _metadata_with_connection,
)

SAMPLE_METADATA = FIXTURE_ROOT / "usecase-sim" / "metadata" / "file" / "local_use_cases.json"

def test_reads_datacoolie_usecase_metadata():
    data = read_metadata_file(str(SAMPLE_METADATA))
    document = normalize_metadata_document(1, str(SAMPLE_METADATA), data)
    assert document["connections"]
    assert document["dataflows"]
    assert any(flow["name"] == "read__csv" for flow in document["dataflows"])

def test_lineage_keeps_weak_relative_declarations_scoped_across_metadata_files(tmp_path: Path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps(_metadata("a_to_b", "A", "B")), encoding="utf-8")
    second.write_text(json.dumps(_metadata("b_to_c", "B", "C")), encoding="utf-8")

    docs = [
        normalize_metadata_document(1, str(first), read_metadata_file(str(first))),
        normalize_metadata_document(2, str(second), read_metadata_file(str(second))),
    ]
    lineage = build_lineage({"_documents": docs, "errors": []})

    labels = {node["label"] for node in lineage["assets"]}
    assert {"./data/A", "./data/B", "./data/C"} <= labels
    assert lineage["schema_version"] == "lineage.v3"
    # Relative paths without a physical authority are weak declarations.  The
    # source scope prevents two files from silently merging unrelated objects.
    assert lineage["summary"]["assets"] == 4
    assert lineage["summary"]["dataflows"] == 2
    assert lineage["summary"]["stitched_assets"] == 0
    scoped = [node for node in lineage["assets"] if node["label"] == "./data/B"]
    assert len(scoped) == 2
    assert {tuple(node["metadata_source_ids"]) for node in scoped} == {(1,), (2,)}
    assert all(node["display_label"] == "data/B" for node in scoped)
    assert all(node["endpoint_locator"] == "data/B" for node in scoped)
    assert all(node["endpoint_kind"] == "file" for node in scoped)
    assert all(node["path"] == "./data/B" for node in scoped)
    assert all({item["kind"] for item in node["identifiers"]} == {"physical_path"} for node in scoped)
    assert len({node["identifiers"][0]["normalized_value"] for node in scoped}) == 2


def test_lineage_resolves_qualified_lakehouse_tables_across_metadata_files():
    first = normalize_metadata_document(
        1,
        "first.json",
        {
            "connections": [{
                "name": "lake",
                "connection_type": "lakehouse",
                "format": "delta",
                "catalog": "main",
                "database": "warehouse",
            }],
            "dataflows": [{
                "name": "seed_orders",
                "source": {"connection_name": "lake", "schema_name": "raw", "table": "seed"},
                "destination": {"connection_name": "lake", "schema_name": "sales", "table": "orders"},
            }],
        },
    )
    second = normalize_metadata_document(
        2,
        "second.json",
        {
            "connections": [{
                "name": "lake",
                "connection_type": "lakehouse",
                "format": "delta",
                "catalog": "main",
                "database": "warehouse",
            }],
            "dataflows": [{
                "name": "query_orders",
                "source": {"connection_name": "lake", "query": "SELECT * FROM sales.orders"},
                "destination": {"connection_name": "lake", "schema_name": "curated", "table": "orders"},
            }],
        },
    )

    lineage = build_lineage({"_documents": [first, second], "errors": []}, environment_id=23)

    orders = [
        asset
        for asset in lineage["assets"]
        if asset.get("schema_name") == "sales" and asset.get("table") == "orders"
    ]
    assert len(orders) == 1
    assert orders[0]["metadata_source_ids"] == [1]
    dependency = lineage["dependencies"][0]
    assert dependency["resolution"] == {"state": "automatic", "reason": None}
    assert dependency["resolution_method"] == "connection_scoped_table"
    assert dependency["resolved_asset_id"] == orders[0]["id"]

def test_lineage_keeps_weak_paths_scoped_across_different_connection_names():
    first = normalize_metadata_document(
        1,
        "first.json",
        _metadata_with_connection("a_to_shared", "writer", "./shared", "A", "orders"),
    )
    second = normalize_metadata_document(
        2,
        "second.json",
        _metadata_with_connection("shared_to_c", "reader", "./shared", "orders", "C"),
    )

    lineage = build_lineage({"_documents": [first, second], "errors": []}, environment_id=7)

    shared = [node for node in lineage["assets"] if node["path"] == "./shared/orders"]
    assert len(shared) == 2
    assert {tuple(item["connection_names"]) for item in shared} == {("reader",), ("writer",)}
    assert {tuple(item["metadata_source_ids"]) for item in shared} == {(1,), (2,)}

def test_lineage_stitches_same_api_endpoint_across_different_connection_names():
    first = normalize_metadata_document(
        1,
        "first.json",
        _api_metadata("api_to_a", "orders_api", "https://api.example.com/v1", "/orders", "orders_a"),
    )
    second = normalize_metadata_document(
        2,
        "second.json",
        _api_metadata(
            "api_to_b",
            "orders_reader",
            "https://api.example.com/v1/",
            "https://api.example.com/v1/orders",
            "orders_b",
        ),
    )

    lineage = build_lineage({"_documents": [first, second], "errors": []}, environment_id=17)

    api_assets = [node for node in lineage["assets"] if node["asset_type"] == "api"]
    assert len(api_assets) == 1
    assert api_assets[0]["connection_names"] == ["orders_api", "orders_reader"]
    assert {item["kind"] for item in api_assets[0]["identifiers"]} == {"api_endpoint"}
    assert api_assets[0]["identifiers"][0]["normalized_value"] == (
        "https://api.example.com|GET https://api.example.com/v1/orders"
    )

def test_lineage_distinguishes_api_endpoint_methods():
    document = normalize_metadata_document(
        1,
        "api.json",
        {
            "connections": [{
                "name": "orders_api",
                "connection_type": "api",
                "format": "api",
                "configure": {"base_url": "https://api.example.com/v1"},
            }, {
                "name": "lake",
                "connection_type": "file",
                "format": "parquet",
                "configure": {"base_path": "./data"},
            }],
            "dataflows": [
                {
                    "name": "get_orders",
                    "source": {"connection_name": "orders_api", "table": "orders", "configure": {"endpoint": "/orders"}},
                    "destination": {"connection_name": "lake", "table": "orders_get"},
                },
                {
                    "name": "post_orders",
                    "source": {
                        "connection_name": "orders_api",
                        "table": "orders",
                        "configure": {"endpoint": "/orders", "method": "POST"},
                    },
                    "destination": {"connection_name": "lake", "table": "orders_post"},
                },
            ],
        },
    )

    lineage = build_lineage({"_documents": [document], "errors": []}, environment_id=18)

    values = sorted(
        identifier["normalized_value"]
        for node in lineage["assets"]
        if node["asset_type"] == "api"
        for identifier in node["identifiers"]
        if identifier["kind"] == "api_endpoint"
    )
    assert values == [
        "https://api.example.com|GET https://api.example.com/v1/orders",
        "https://api.example.com|POST https://api.example.com/v1/orders",
    ]

def test_api_identity_ignores_auth_pagination_params_and_body():
    first = normalize_metadata_document(
        1,
        "first.json",
        _api_metadata(
            "orders_open",
            "orders_api",
            "https://api.example.com/v1",
            "/orders",
            "orders_open",
            connection_config={"auth_type": "bearer", "auth_token": "SECRET_TOKEN", "timeout": 3},
            source_config={"pagination_type": "offset", "params": {"status": "open"}},
        ),
    )
    second = normalize_metadata_document(
        2,
        "second.json",
        _api_metadata(
            "orders_closed",
            "orders_reader",
            "https://api.example.com/v1",
            "/orders",
            "orders_closed",
            connection_config={"auth_type": "api_key", "api_key_value": "SECRET_KEY"},
            source_config={"body": {"status": "closed"}, "watermark_to_param": "updated_before"},
        ),
    )

    lineage = build_lineage({"_documents": [first, second], "errors": []}, environment_id=19)

    api_assets = [node for node in lineage["assets"] if node["asset_type"] == "api"]
    assert len(api_assets) == 1
    identifier = api_assets[0]["identifiers"][0]
    assert identifier["normalized_value"] == "https://api.example.com|GET https://api.example.com/v1/orders"
    serialized = json.dumps(identifier, sort_keys=True)
    assert "SECRET" not in serialized
    assert "status" not in serialized

def test_lineage_resolves_connections_across_split_metadata_sources():
    connections_doc = normalize_metadata_document(
        1,
        "connections.json",
        {
            "connections": [{
                "name": "lake",
                "connection_type": "lakehouse",
                "format": "delta",
                "configure": {"base_path": "Tables"},
            }],
        },
    )
    dataflows_doc = normalize_metadata_document(
        2,
        "dataflows/orders.json",
        {
            "dataflows": [{
                "name": "bronze_to_silver",
                "source": {"connection_name": "lake", "schema_name": "bronze", "table": "orders"},
                "destination": {
                    "connection_name": "lake",
                    "schema_name": "silver",
                    "table": "orders_clean",
                    "load_type": "merge_upsert",
                },
            }],
        },
    )

    documents = enrich_metadata_documents_with_connections([connections_doc, dataflows_doc])
    lineage = build_lineage({"_documents": documents, "errors": []}, environment_id=12)

    orders = next(node for node in lineage["assets"] if node.get("table") == "orders")
    orders_clean = next(node for node in lineage["assets"] if node.get("table") == "orders_clean")
    dataflow = lineage["dataflows"][0]

    assert orders["connection_name"] == "lake"
    assert orders["connection_type"] == "lakehouse"
    assert orders["format"] == "delta"
    assert orders["path"] == "Tables/bronze/orders"
    assert orders_clean["format"] == "delta"
    assert dataflow["source_asset_id"] == orders["id"]
    assert dataflow["destination_asset_id"] == orders_clean["id"]

def test_lineage_ignores_inactive_dataflows():
    document = normalize_metadata_document(
        1,
        "metadata.json",
        {
            "connections": [{
                "name": "lake",
                "connection_type": "file",
                "format": "parquet",
                "configure": {"base_path": "./data"},
            }],
            "dataflows": [
                {
                    "name": "active_flow",
                    "source": {"connection_name": "lake", "table": "A"},
                    "destination": {"connection_name": "lake", "table": "B"},
                },
                {
                    "name": "disabled_flow",
                    "is_active": False,
                    "source": {"connection_name": "lake", "table": "B"},
                    "destination": {"connection_name": "lake", "table": "C"},
                },
                {
                    "name": "disabled_string_flow",
                    "is_active": "false",
                    "source": {"connection_name": "lake", "table": "C"},
                    "destination": {"connection_name": "lake", "table": "D"},
                },
            ],
        },
    )

    lineage = build_lineage({"_documents": [document], "errors": []}, environment_id=8)

    assert [flow["name"] for flow in lineage["dataflows"]] == ["active_flow"]
    assert {asset["path"] for asset in lineage["assets"]} == {"./data/A", "./data/B"}

def test_logical_table_and_path_are_aliases_for_same_metadata_asset():
    document = normalize_metadata_document(
        1,
        "iceberg.json",
        {
            "connections": [{
                "name": "iceberg",
                "connection_type": "lakehouse",
                "format": "iceberg",
                "catalog": "local_catalog",
                "database": "default",
                "configure": {"base_path": "./warehouse"},
            }],
            "dataflows": [{
                "name": "write_orders",
                "source": {"connection_name": "iceberg", "table": "incoming"},
                "destination": {"connection_name": "iceberg", "schema_name": "sales", "table": "orders"},
            }],
        },
    )

    lineage = build_lineage({"_documents": [document], "errors": []}, environment_id=2)
    orders = next(node for node in lineage["assets"] if node.get("table") == "orders")

    assert {item["kind"] for item in orders["identifiers"]} == {"logical_table", "physical_path"}
    assert any(item["display_value"] == "local_catalog.default.sales.orders" for item in orders["identifiers"])
    assert any(item["display_value"] == "./warehouse/sales/orders" for item in orders["identifiers"])

def test_path_object_case_is_not_collapsed():
    document = normalize_metadata_document(
        1,
        "case.json",
        {
            "connections": [{
                "name": "lake",
                "connection_type": "file",
                "format": "parquet",
                "configure": {"base_path": "s3://bucket/data"},
            }],
            "dataflows": [{
                "name": "case_sensitive",
                "source": {"connection_name": "lake", "table": "Orders"},
                "destination": {"connection_name": "lake", "table": "orders"},
            }],
        },
    )

    lineage = build_lineage({"_documents": [document], "errors": []}, environment_id=3)

    assert lineage["summary"]["assets"] == 2
    assert {node["path"] for node in lineage["assets"]} == {
        "s3://bucket/data/Orders",
        "s3://bucket/data/orders",
    }

def test_weak_logical_tables_with_different_catalogs_remain_distinct():
    first = normalize_metadata_document(
        1,
        "first.json",
        _catalog_metadata("first", "catalog_a", "shared", "orders"),
    )
    second = normalize_metadata_document(
        2,
        "second.json",
        _catalog_metadata("second", "catalog_b", "shared", "orders"),
    )

    lineage = build_lineage({"_documents": [first, second], "errors": []}, environment_id=4)

    assert lineage["summary"]["assets"] == 4
    assert not any(item["code"] == "asset_identity_conflict" for item in lineage["diagnostics"])
    orders = [
        asset
        for asset in lineage["assets"]
        if asset.get("table") == "orders"
    ]
    assert len(orders) == 2
    assert {tuple(asset["metadata_source_ids"]) for asset in orders} == {(1,), (2,)}
    assert len({asset["identifiers"][0]["normalized_value"] for asset in orders}) == 2

def test_sql_discovered_input_resolves_to_existing_metadata_asset():
    document = normalize_metadata_document(
        1,
        "metadata.json",
        {
            "connections": [{
                "name": "lake",
                "connection_type": "lakehouse",
                "format": "delta",
                "catalog": "main",
                "database": "warehouse",
            }],
            "dataflows": [
                {
                    "name": "produce_orders",
                    "source": {"connection_name": "lake", "schema_name": "raw", "table": "seed"},
                    "destination": {"connection_name": "lake", "schema_name": "raw", "table": "orders"},
                },
                {
                    "name": "query_orders",
                    "source": {
                        "connection_name": "lake",
                        "table": "placeholder",
                        "query": "SELECT * FROM raw.orders",
                    },
                    "destination": {"connection_name": "lake", "schema_name": "curated", "table": "orders"},
                },
            ],
        },
    )

    lineage = build_lineage({"_documents": [document], "dataflows": document["dataflows"], "errors": []}, environment_id=5)

    assert lineage["summary"]["dependencies"] == 1
    assert lineage["summary"]["automatic_dependencies"] == 1
    dependency = lineage["dependencies"][0]
    assert dependency["resolution"]["state"] == "automatic"
    assert dependency["observations"][0]["value"] == "raw.orders"
    query_asset = next(asset for asset in lineage["assets"] if asset["asset_type"] == "sql_query")
    raw_orders = next(
        asset for asset in lineage["assets"]
        if asset.get("schema_name") == "raw" and asset.get("table") == "orders"
    )
    query_flow = next(flow for flow in lineage["dataflows"] if flow["name"] == "query_orders")
    assert dependency["resolved_asset_id"] == raw_orders["id"]
    occurrence = next(item for item in lineage["reference_occurrences"] if item["consumer_asset_id"] == query_asset["id"])
    reference = next(item for item in lineage["references"] if item["id"] == occurrence["reference_id"])
    assert occurrence["resolution"]["state"] == "automatic"
    assert reference["resolution"]["state"] == "automatic"
    assert reference["resolved_asset_id"] == raw_orders["id"]
    assert dependency["reference_id"] == reference["id"]
    assert dependency["reference_occurrence_id"] == occurrence["id"]
    assert dependency["consumer_asset_id"] == query_asset["id"]
    assert dependency["target_asset_id"] == query_asset["id"]
    assert query_flow["source_asset_id"] == query_asset["id"]
