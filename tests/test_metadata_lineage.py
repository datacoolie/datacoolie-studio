from __future__ import annotations

import json
from pathlib import Path

from datacoolie_studio.domains.lineage.service import build_lineage
from datacoolie_studio.domains.metadata.normalizer import (
    enrich_metadata_documents_with_connections,
    normalize_metadata_document,
)
from datacoolie_studio.domains.metadata.reader import read_metadata_file


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_METADATA = ROOT / "datacoolie" / "usecase-sim" / "metadata" / "file" / "local_use_cases.json"


def test_reads_datacoolie_usecase_metadata():
    data = read_metadata_file(str(SAMPLE_METADATA))
    document = normalize_metadata_document(1, str(SAMPLE_METADATA), data)
    assert document["connections"]
    assert document["dataflows"]
    assert any(flow["name"] == "read__csv" for flow in document["dataflows"])


def test_lineage_stitches_across_metadata_files(tmp_path: Path):
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
    assert lineage["schema_version"] == "lineage.v2"
    assert lineage["summary"]["assets"] == 3
    assert lineage["summary"]["dataflows"] == 2
    assert lineage["summary"]["stitched_assets"] == 1
    stitched = [node for node in lineage["assets"] if node["label"] == "./data/B"]
    assert stitched and stitched[0]["metadata_source_ids"] == [1, 2]
    assert stitched[0]["display_label"] == "data/B"
    assert stitched[0]["endpoint_locator"] == "data/B"
    assert stitched[0]["endpoint_kind"] == "file"
    assert stitched[0]["path"] == "./data/B"
    assert {item["kind"] for item in stitched[0]["identifiers"]} == {"physical_path"}


def test_lineage_stitches_same_path_across_different_connection_names():
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
    assert len(shared) == 1
    assert shared[0]["connection_names"] == ["reader", "writer"]
    assert shared[0]["metadata_source_ids"] == [1, 2]


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


def test_conflicting_logical_table_for_same_path_is_reported():
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

    assert any(item["code"] == "asset_identity_conflict" for item in lineage["diagnostics"])


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
    assert lineage["summary"]["resolved_dependencies"] == 1
    dependency = lineage["dependencies"][0]
    assert dependency["resolution_status"] == "resolved"
    assert dependency["observations"][0]["value"] == "raw.orders"
    query_asset = next(asset for asset in lineage["assets"] if asset["kind"] == "sql_query")
    raw_orders = next(
        asset for asset in lineage["assets"]
        if asset.get("schema_name") == "raw" and asset.get("table") == "orders"
    )
    query_flow = next(flow for flow in lineage["dataflows"] if flow["name"] == "query_orders")
    assert dependency["source"] == {"entity_type": "asset", "id": raw_orders["id"]}
    assert dependency["target_asset_id"] == query_asset["id"]
    assert query_flow["source_asset_id"] == query_asset["id"]


def test_query_asset_does_not_merge_with_same_table_endpoint():
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
                        "schema_name": "raw",
                        "table": "orders",
                        "query": "SELECT * FROM raw.orders",
                    },
                    "destination": {"connection_name": "lake", "schema_name": "curated", "table": "orders"},
                },
            ],
        },
    )

    lineage = build_lineage(
        {"_documents": [document], "dataflows": document["dataflows"], "errors": []},
        environment_id=11,
    )

    query_asset = next(asset for asset in lineage["assets"] if asset["kind"] == "sql_query")
    table_asset = next(
        asset for asset in lineage["assets"]
        if asset["kind"] == "table" and asset.get("schema_name") == "raw" and asset.get("table") == "orders"
    )
    dependency = lineage["dependencies"][0]
    assert query_asset["id"] != table_asset["id"]
    assert dependency["source"]["id"] == table_asset["id"]
    assert dependency["target_asset_id"] == query_asset["id"]


def test_sql_fan_in_is_dependencies_not_process_nodes():
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
                    "name": "seed_orders",
                    "source": {"connection_name": "lake", "schema_name": "raw", "table": "seed"},
                    "destination": {"connection_name": "lake", "schema_name": "raw", "table": "orders"},
                },
                {
                    "name": "seed_customers",
                    "source": {"connection_name": "lake", "schema_name": "raw", "table": "seed"},
                    "destination": {"connection_name": "lake", "schema_name": "raw", "table": "customers"},
                },
                {
                    "name": "build_mart",
                    "source": {
                        "connection_name": "lake",
                        "query": """
                            SELECT o.order_id, c.customer_name
                            FROM raw.orders o
                            JOIN raw.customers c ON c.id = o.customer_id
                        """,
                    },
                    "destination": {"connection_name": "lake", "schema_name": "mart", "table": "orders"},
                },
            ],
        },
    )

    lineage = build_lineage({"_documents": [document], "dataflows": document["dataflows"], "errors": []}, environment_id=6)

    query_asset = next(asset for asset in lineage["assets"] if asset["kind"] == "sql_query")
    build_mart = next(flow for flow in lineage["dataflows"] if flow["name"] == "build_mart")
    inputs = [
        dependency for dependency in lineage["dependencies"]
        if dependency["target_asset_id"] == query_asset["id"]
    ]

    assert build_mart["source_asset_id"] == query_asset["id"]
    assert len(inputs) == 2
    assert {dependency["source"]["entity_type"] for dependency in inputs} == {"asset"}
    assert {dependency["resolution_status"] for dependency in inputs} == {"resolved"}


def test_sql_input_with_new_exact_identity_becomes_discovered_asset():
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
            "dataflows": [{
                "name": "query_external_orders",
                "source": {
                    "connection_name": "lake",
                    "query": "SELECT * FROM external.orders",
                },
                "destination": {
                    "connection_name": "lake",
                    "schema_name": "curated",
                    "table": "orders",
                },
            }],
        },
    )

    lineage = build_lineage(
        {"_documents": [document], "dataflows": document["dataflows"], "errors": []},
        environment_id=8,
    )

    dependency = lineage["dependencies"][0]
    discovered = next(asset for asset in lineage["assets"] if asset["declaration_status"] == "discovered_only")
    assert lineage["summary"]["discovered_only_assets"] == 1
    assert lineage["summary"]["discovered_only_dependencies"] == 1
    assert dependency["source"] == {"entity_type": "asset", "id": discovered["id"]}
    assert discovered["schema_name"] == "external"
    assert discovered["table"] == "orders"


def test_ambiguous_sql_input_remains_reference_outside_asset_registry():
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
                    "name": "query_orders",
                    "source": {"connection_name": "lake", "query": "SELECT * FROM orders"},
                    "destination": {"connection_name": "lake", "schema_name": "curated", "table": "orders"},
                },
            ],
        },
    )

    lineage = build_lineage(
        {"_documents": [document], "dataflows": document["dataflows"], "errors": []},
        environment_id=9,
    )

    reference = lineage["references"][0]
    dependency = next(item for item in lineage["dependencies"] if item["source"]["entity_type"] == "reference")
    assert lineage["summary"]["ambiguous_dependencies"] == 1
    assert reference["resolution_status"] == "ambiguous"
    assert len(reference["candidate_asset_ids"]) >= 2
    assert dependency["source"]["id"] == reference["id"]
    assert all(asset["id"] != reference["id"] for asset in lineage["assets"])


def test_duplicate_dataflow_id_with_different_endpoints_is_diagnostic():
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
                    "name": "duplicate_name",
                    "source": {"connection_name": "lake", "table": "A"},
                    "destination": {"connection_name": "lake", "table": "B"},
                },
                {
                    "name": "duplicate_name",
                    "source": {"connection_name": "lake", "table": "C"},
                    "destination": {"connection_name": "lake", "table": "D"},
                },
            ],
        },
    )

    lineage = build_lineage({"_documents": [document], "errors": []}, environment_id=10)

    assert len(lineage["dataflows"]) == 2
    assert len({item["id"] for item in lineage["dataflows"]}) == 2
    assert any(item["code"] == "dataflow_identity_conflict" for item in lineage["diagnostics"])


def test_duplicate_dataflow_names_keep_their_own_analysis():
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
                    "name": "seed_a",
                    "source": {"connection_name": "lake", "schema_name": "raw", "table": "seed"},
                    "destination": {"connection_name": "lake", "schema_name": "raw", "table": "A"},
                },
                {
                    "name": "seed_c",
                    "source": {"connection_name": "lake", "schema_name": "raw", "table": "seed"},
                    "destination": {"connection_name": "lake", "schema_name": "raw", "table": "C"},
                },
                {
                    "name": "duplicate_query",
                    "source": {"connection_name": "lake", "query": "SELECT * FROM raw.A"},
                    "destination": {"connection_name": "lake", "schema_name": "curated", "table": "A"},
                },
                {
                    "name": "duplicate_query",
                    "source": {"connection_name": "lake", "query": "SELECT * FROM raw.C"},
                    "destination": {"connection_name": "lake", "schema_name": "curated", "table": "C"},
                },
            ],
        },
    )

    lineage = build_lineage(
        {"_documents": [document], "dataflows": document["dataflows"], "errors": []},
        environment_id=12,
    )

    query_flows = [item for item in lineage["dataflows"] if item["name"] == "duplicate_query"]
    dependency_by_target = {item["target_asset_id"]: item for item in lineage["dependencies"]}
    input_tables = {
        dependency_by_target[item["source_asset_id"]]["observations"][0]["table"]
        for item in query_flows
    }
    assert input_tables == {"A", "C"}


def _metadata(name: str, source_table: str, destination_table: str) -> dict:
    return {
        "connections": [
            {"name": "lake", "connection_type": "file", "format": "parquet", "configure": {"base_path": "./data"}}
        ],
        "dataflows": [
            {
                "name": name,
                "stage": "demo",
                "source": {"connection_name": "lake", "table": source_table},
                "destination": {"connection_name": "lake", "table": destination_table, "load_type": "append"},
                "transform": {},
            }
        ],
    }


def _metadata_with_connection(name: str, connection_name: str, base_path: str, source_table: str, destination_table: str) -> dict:
    return {
        "connections": [{
            "name": connection_name,
            "connection_type": "file",
            "format": "parquet",
            "configure": {"base_path": base_path},
        }],
        "dataflows": [{
            "name": name,
            "source": {"connection_name": connection_name, "table": source_table},
            "destination": {"connection_name": connection_name, "table": destination_table},
        }],
    }


def _catalog_metadata(name: str, catalog: str, base_path: str, table: str) -> dict:
    return {
        "connections": [{
            "name": catalog,
            "connection_type": "lakehouse",
            "format": "iceberg",
            "catalog": catalog,
            "database": "default",
            "configure": {"base_path": base_path},
        }],
        "dataflows": [{
            "name": name,
            "source": {"connection_name": catalog, "table": f"{catalog}_{table}_input"},
            "destination": {"connection_name": catalog, "table": table},
        }],
    }
