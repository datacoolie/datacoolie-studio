from __future__ import annotations

import json
from pathlib import Path

from datacoolie_studio.domains.lineage.service import build_lineage
from datacoolie_studio.domains.analysis.models import InputEvidence
from datacoolie_studio.domains.assets.reference_identity import build_reference_context_scope
from datacoolie_studio.domains.metadata.normalizer import (
    enrich_metadata_documents_with_connections,
    normalize_metadata_document,
)
from datacoolie_studio.domains.metadata.reader import read_metadata_file


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "datacoolie"
SAMPLE_METADATA = FIXTURE_ROOT / "usecase-sim" / "metadata" / "file" / "local_use_cases.json"


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
    assert lineage["schema_version"] == "lineage.v3"
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
        "https://api.example.com/v1|GET https://api.example.com/v1/orders"
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
        "https://api.example.com/v1|GET https://api.example.com/v1/orders",
        "https://api.example.com/v1|POST https://api.example.com/v1/orders",
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
    assert identifier["normalized_value"] == "https://api.example.com/v1|GET https://api.example.com/v1/orders"
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

    query_asset = next(asset for asset in lineage["assets"] if asset["asset_type"] == "sql_query")
    table_asset = next(
        asset for asset in lineage["assets"]
        if asset["asset_type"] == "table" and asset.get("schema_name") == "raw" and asset.get("table") == "orders"
    )
    dependency = lineage["dependencies"][0]
    assert query_asset["id"] != table_asset["id"]
    assert dependency["resolved_asset_id"] == table_asset["id"]
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

    query_asset = next(asset for asset in lineage["assets"] if asset["asset_type"] == "sql_query")
    build_mart = next(flow for flow in lineage["dataflows"] if flow["name"] == "build_mart")
    inputs = [
        dependency for dependency in lineage["dependencies"]
        if dependency["target_asset_id"] == query_asset["id"]
    ]

    assert build_mart["source_asset_id"] == query_asset["id"]
    assert len(inputs) == 2
    assert {dependency["resolution"]["state"] for dependency in inputs} == {"automatic"}
    assert all(dependency["resolved_asset_id"] for dependency in inputs)


def test_sql_input_with_new_exact_identity_remains_reference():
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
    occurrence = next(item for item in lineage["reference_occurrences"] if item["consumer_asset_id"] == dependency["target_asset_id"])
    reference = next(item for item in lineage["references"] if item["id"] == occurrence["reference_id"])
    assert lineage["summary"]["unresolved_dependencies"] == 1
    assert dependency["reference_id"] == reference["id"]
    assert dependency["resolved_asset_id"] is None
    assert reference["resolution"] == {"state": "unresolved", "reason": "no_match"}
    assert reference["resolved_asset_id"] is None
    assert all(asset["id"] != reference["id"] for asset in lineage["assets"])
    assert all(
        not (asset.get("schema_name") == "external" and asset.get("table") == "orders")
        for asset in lineage["assets"]
    )


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
    dependency = lineage["dependencies"][0]
    assert lineage["summary"]["unresolved_dependencies"] == 1
    assert reference["resolution"] == {"state": "unresolved", "reason": "multiple_matches"}
    assert len(reference["candidate_asset_ids"]) >= 2
    assert dependency["reference_id"] == reference["id"]
    assert dependency["resolved_asset_id"] is None
    assert all(asset["id"] != reference["id"] for asset in lineage["assets"])


def test_partial_schema_table_reference_resolves_only_when_unique():
    document = normalize_metadata_document(
        1,
        "metadata.json",
        _schema_table_suffix_metadata(include_second_lakehouse=False),
    )

    lineage = build_lineage({"_documents": [document], "dataflows": document["dataflows"], "errors": []}, environment_id=20)

    customer = next(
        asset for asset in lineage["assets"]
        if asset.get("database") == "lh1" and asset.get("schema_name") == "silver" and asset.get("table") == "customer"
    )
    dependency = lineage["dependencies"][0]
    reference = lineage["references"][0]
    occurrence = lineage["reference_occurrences"][0]
    assert occurrence["raw_value"] == "silver.customer"
    assert reference["resolution"]["state"] == "automatic"
    assert dependency["resolved_asset_id"] == customer["id"]


def test_same_reference_value_across_connections_has_one_canonical_reference_and_scoped_occurrences():
    document = normalize_metadata_document(
        1,
        "metadata.json",
        {
            "connections": [
                {"name": "lh1", "connection_type": "lakehouse", "format": "delta", "configure": {"connection_instance": "catalog:main:lh1"}},
                {"name": "lh2", "connection_type": "lakehouse", "format": "delta", "configure": {"connection_instance": "catalog:main:lh2"}},
            ],
            "dataflows": [
                {
                    "name": "query_lh1_customer",
                    "source": {"connection_name": "lh1", "query": "SELECT * FROM silver.customer"},
                    "destination": {"connection_name": "lh1", "schema_name": "gold", "table": "customer_lh1"},
                },
                {
                    "name": "query_lh2_customer",
                    "source": {"connection_name": "lh2", "query": "SELECT * FROM silver.customer"},
                    "destination": {"connection_name": "lh2", "schema_name": "gold", "table": "customer_lh2"},
                },
            ],
        },
    )

    lineage = build_lineage({"_documents": [document], "dataflows": document["dataflows"], "errors": []}, environment_id=23)

    assert len(lineage["references"]) == 1
    assert lineage["references"][0]["normalized_value"] == "silver.customer"
    assert {item["context_scope"] for item in lineage["reference_occurrences"]} == {
        "catalog:main:lh1",
        "catalog:main:lh2",
    }
    assert {item["context_scope_source"] for item in lineage["reference_occurrences"]} == {"metadata_context"}


def test_reference_context_scope_is_detected_for_paths_and_api_urls():
    path_scope = build_reference_context_scope(
        InputEvidence(kind="path", value="abfss://lake/container/data/customer", provenance="sql"),
        {},
    )
    api_scope = build_reference_context_scope(
        InputEvidence(kind="api", value="GET https://api.example.com/v1/customer", provenance="python"),
        {},
    )

    assert (path_scope.value, path_scope.source) == ("abfss://lake", "detected")
    assert (api_scope.value, api_scope.source) == ("https://api.example.com", "detected")


def test_schema_table_reference_resolves_to_path_backed_table_asset():
    document = normalize_metadata_document(
        1,
        "metadata.json",
        _path_backed_schema_table_metadata(),
    )

    lineage = build_lineage({"_documents": [document], "dataflows": document["dataflows"], "errors": []}, environment_id=22)

    table_asset = next(
        asset for asset in lineage["assets"]
        if asset.get("schema_name") == "silver" and asset.get("table") == "saleslt_salesorderheader"
    )
    identifier_kinds = {identifier["kind"] for identifier in table_asset["identifiers"]}
    dependency = lineage["dependencies"][0]
    reference = lineage["references"][0]
    occurrence = lineage["reference_occurrences"][0]

    assert table_asset["entity_type"] == "asset"
    assert table_asset["asset_type"] == "path"
    assert {"physical_path", "logical_table"}.issubset(identifier_kinds)
    assert occurrence["raw_value"] == "silver.saleslt_salesorderheader"
    assert reference["entity_type"] == "reference"
    assert reference["resolution"]["state"] == "automatic"
    assert dependency["resolution_method"] == "unique_table_suffix"
    assert dependency["resolved_asset_id"] == table_asset["id"]


def test_partial_schema_table_reference_uses_connection_when_multiple_assets_match():
    document = normalize_metadata_document(
        1,
        "metadata.json",
        _schema_table_suffix_metadata(include_second_lakehouse=True),
    )

    lineage = build_lineage({"_documents": [document], "dataflows": document["dataflows"], "errors": []}, environment_id=21)

    dependency = lineage["dependencies"][0]
    reference = lineage["references"][0]
    occurrence = lineage["reference_occurrences"][0]
    assert occurrence["raw_value"] == "silver.customer"
    expected = next(asset for asset in lineage["assets"] if asset.get("database") == "lh1" and asset.get("schema_name") == "silver")
    assert reference["resolution"]["state"] == "automatic"
    assert dependency["resolved_asset_id"] == expected["id"]
    assert dependency["resolution_method"] == "connection_table_suffix"


def test_project_mapping_can_replace_successful_automatic_resolution():
    document = normalize_metadata_document(1, "metadata.json", _schema_table_suffix_metadata(include_second_lakehouse=True))

    lineage = build_lineage(
        {"_documents": [document], "dataflows": document["dataflows"], "errors": []},
        environment_id=21,
        reference_mappings=[{
            "id": 303,
            "reference_type": "table_reference",
            "reference_normalized_value": "silver.customer",
            "target_identifier_kind": "logical_table",
            "target_normalized_value": "catalog:main:lh2|main.lh2.silver.customer",
        }],
    )

    expected = next(asset for asset in lineage["assets"] if asset.get("database") == "lh2" and asset.get("schema_name") == "silver")
    dependency = next(item for item in lineage["dependencies"] if item["reference_id"])
    assert dependency["resolution"]["state"] == "manual"
    assert dependency["resolved_asset_id"] == expected["id"]
    observation = next(item for item in lineage["references"][0]["observations"] if item.get("mapping_id") == 303)
    assert observation["automatic_suggestion"]["method"] == "connection_table_suffix"


def test_manual_reference_mapping_resolves_ambiguous_reference():
    document = normalize_metadata_document(1, "metadata.json", _ambiguous_orders_metadata())

    lineage = build_lineage(
        {"_documents": [document], "dataflows": document["dataflows"], "errors": []},
        environment_id=9,
        reference_mappings=[{
            "id": 101,
            "reference_type": "table_reference",
            "reference_normalized_value": "orders",
            "target_identifier_kind": "logical_table",
            "target_normalized_value": "catalog:main:warehouse|main.warehouse.sales.orders",
            "note": "manual map to sales orders",
        }],
    )

    query_asset = next(asset for asset in lineage["assets"] if asset["asset_type"] == "sql_query")
    sales_orders = next(
        asset for asset in lineage["assets"]
        if asset.get("schema_name") == "sales" and asset.get("table") == "orders"
    )
    dependency = next(item for item in lineage["dependencies"] if item["target_asset_id"] == query_asset["id"])

    assert dependency["resolution"]["state"] == "manual"
    assert dependency["resolution_method"] == "manual_mapping"
    assert dependency["reference_id"] is not None
    assert dependency["resolved_asset_id"] == sales_orders["id"]
    assert dependency["consumer_asset_id"] == query_asset["id"]
    assert lineage["summary"]["manual_dependencies"] == 1
    assert lineage["summary"]["unresolved_dependencies"] == 0
    assert len(lineage["references"]) == 1
    reference = lineage["references"][0]
    occurrence = lineage["reference_occurrences"][0]
    assert reference["resolution"]["state"] == "manual"
    assert occurrence["resolution"]["state"] == "manual"
    assert occurrence["target_asset_id"] == query_asset["id"]
    assert occurrence["consumer_asset_id"] == query_asset["id"]
    assert reference["resolved_asset_id"] == sales_orders["id"]
    assert sales_orders["id"] in reference["candidate_asset_ids"]


def test_manual_reference_target_missing_keeps_reference():
    document = normalize_metadata_document(1, "metadata.json", _ambiguous_orders_metadata())

    lineage = build_lineage(
        {"_documents": [document], "dataflows": document["dataflows"], "errors": []},
        environment_id=9,
        reference_mappings=[{
            "id": 202,
            "reference_type": "table_reference",
            "reference_normalized_value": "orders",
            "target_identifier_kind": "logical_table",
            "target_normalized_value": "catalog:main:warehouse|main.warehouse.curated.orders_missing",
            "note": "missing in this env",
        }],
    )

    reference = lineage["references"][0]
    dependency = lineage["dependencies"][0]

    assert reference["resolution"] == {"state": "unresolved", "reason": "target_missing"}
    assert dependency["resolution"] == {"state": "unresolved", "reason": "target_missing"}
    assert dependency["resolution_method"] == "manual_target_missing"
    assert dependency["reference_id"] == reference["id"]
    assert dependency["resolved_asset_id"] is None
    assert any(obs.get("mapping_status") == "target_missing" for obs in reference["observations"])
    assert lineage["summary"]["unresolved_dependencies"] == 1


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


def _api_metadata(
    name: str,
    connection_name: str,
    base_url: str,
    endpoint: str,
    destination_table: str,
    *,
    connection_config: dict | None = None,
    source_config: dict | None = None,
) -> dict:
    return {
        "connections": [{
            "name": connection_name,
            "connection_type": "api",
            "format": "api",
            "configure": {"base_url": base_url, **(connection_config or {})},
        }, {
            "name": "lake",
            "connection_type": "file",
            "format": "parquet",
            "configure": {"base_path": "./data"},
        }],
        "dataflows": [{
            "name": name,
            "source": {
                "connection_name": connection_name,
                "table": "orders",
                "configure": {"endpoint": endpoint, **(source_config or {})},
            },
            "destination": {"connection_name": "lake", "table": destination_table},
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


def _ambiguous_orders_metadata() -> dict:
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
                "name": "query_orders",
                "source": {"connection_name": "lake", "query": "SELECT * FROM orders"},
                "destination": {"connection_name": "lake", "schema_name": "curated", "table": "orders"},
            },
        ],
    }


def _schema_table_suffix_metadata(*, include_second_lakehouse: bool) -> dict:
    connections = [{
        "name": "lh1",
        "connection_type": "lakehouse",
        "format": "delta",
        "catalog": "main",
        "database": "lh1",
    }]
    if include_second_lakehouse:
        connections.append({
            "name": "lh2",
            "connection_type": "lakehouse",
            "format": "delta",
            "catalog": "main",
            "database": "lh2",
        })
    dataflows = [
        {
            "name": "seed_lh1_customer",
            "source": {"connection_name": "lh1", "schema_name": "raw", "table": "seed"},
            "destination": {"connection_name": "lh1", "schema_name": "silver", "table": "customer"},
        },
        {
            "name": "query_customer",
            "source": {"connection_name": "lh1", "query": "SELECT * FROM silver.customer"},
            "destination": {"connection_name": "lh1", "schema_name": "gold", "table": "customer"},
        },
    ]
    if include_second_lakehouse:
        dataflows.insert(1, {
            "name": "seed_lh2_customer",
            "source": {"connection_name": "lh2", "schema_name": "raw", "table": "seed"},
            "destination": {"connection_name": "lh2", "schema_name": "silver", "table": "customer"},
        })
    return {"connections": connections, "dataflows": dataflows}


def _path_backed_schema_table_metadata() -> dict:
    return {
        "connections": [{
            "name": "fabric_lakehouse",
            "connection_type": "lakehouse",
            "format": "delta",
            "configure": {"base_path": "Tables"},
        }],
        "dataflows": [
            {
                "name": "seed_salesorderheader",
                "source": {"connection_name": "fabric_lakehouse", "schema_name": "bronze", "table": "saleslt_salesorderheader"},
                "destination": {"connection_name": "fabric_lakehouse", "schema_name": "silver", "table": "saleslt_salesorderheader"},
            },
            {
                "name": "query_salesorderheader",
                "source": {"connection_name": "fabric_lakehouse", "query": "SELECT * FROM silver.saleslt_salesorderheader"},
                "destination": {"connection_name": "fabric_lakehouse", "schema_name": "gold", "table": "fact_sales"},
            },
        ],
    }
