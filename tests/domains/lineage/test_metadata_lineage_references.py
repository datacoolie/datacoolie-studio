from __future__ import annotations


from datacoolie_studio.domains.lineage.service import build_lineage
from datacoolie_studio.domains.analysis.models import InputEvidence
from datacoolie_studio.domains.assets.reference_identity import build_reference_context_scope
from datacoolie_studio.domains.metadata.normalizer import (
    normalize_metadata_document,
)
from tests.support.paths import FIXTURE_ROOT


from tests.domains.lineage.lineage_test_support import (
    _database_reference_with_file_candidate,
    _path_backed_schema_table_metadata,
    _schema_table_suffix_metadata,
)

SAMPLE_METADATA = FIXTURE_ROOT / "usecase-sim" / "metadata" / "file" / "local_use_cases.json"

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
    assert table_asset["asset_type"] == "table"
    assert {"physical_path", "logical_table"}.issubset(identifier_kinds)
    assert occurrence["raw_value"] == "silver.saleslt_salesorderheader"
    assert reference["entity_type"] == "reference"
    assert reference["resolution"]["state"] == "automatic"
    assert dependency["resolution_method"] == "connection_scoped_table"
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
    assert dependency["resolution_method"] == "connection_scoped_table"

def test_database_reference_does_not_cross_map_to_unique_file_suffix():
    document = normalize_metadata_document(
        1,
        "metadata.json",
        _database_reference_with_file_candidate(include_database_target=False),
    )

    lineage = build_lineage({"_documents": [document], "dataflows": document["dataflows"], "errors": []}, environment_id=31)

    reference = next(item for item in lineage["references"] if item["normalized_value"] == "application.stateprovinces")
    dependency = next(item for item in lineage["dependencies"] if item["reference_id"] == reference["id"])
    bronze = next(asset for asset in lineage["assets"] if asset.get("connection_name") == "bronze_parquet")

    assert reference["resolution"] == {"state": "unresolved", "reason": "out_of_scope"}
    assert dependency["resolution"] == {"state": "unresolved", "reason": "out_of_scope"}
    assert dependency["resolved_asset_id"] is None
    assert dependency["resolution_method"] == "out_of_scope_table_candidate"
    assert bronze["id"] in reference["candidate_asset_ids"]

def test_database_reference_resolves_only_inside_database_scope():
    document = normalize_metadata_document(
        1,
        "metadata.json",
        _database_reference_with_file_candidate(include_database_target=True),
    )

    lineage = build_lineage({"_documents": [document], "dataflows": document["dataflows"], "errors": []}, environment_id=32)

    reference = next(item for item in lineage["references"] if item["normalized_value"] == "application.stateprovinces")
    dependency = next(item for item in lineage["dependencies"] if item["reference_id"] == reference["id"])
    target = next(
        asset for asset in lineage["assets"]
        if asset.get("connection_name") == "wwi_sqlserver"
        and asset.get("database") == "WideWorldImporters"
        and asset.get("schema_name") == "Application"
        and asset.get("table") == "StateProvinces"
    )

    assert reference["resolution"] == {"state": "automatic", "reason": None}
    assert dependency["resolved_asset_id"] == target["id"]
    assert dependency["resolution_method"] == "connection_scoped_table"

def test_database_scope_keeps_same_host_databases_distinct():
    document = normalize_metadata_document(
        1,
        "metadata.json",
        {
            "connections": [
                {
                    "name": "db_a",
                    "connection_type": "database",
                    "database": "DatabaseA",
                    "configure": {"database_type": "mssql", "host": "sql.example", "port": 1433},
                },
                {
                    "name": "db_b",
                    "connection_type": "database",
                    "database": "DatabaseB",
                    "configure": {"database_type": "mssql", "host": "sql.example", "port": 1433},
                },
            ],
            "dataflows": [
                {
                    "name": "declare_db_a_table",
                    "source": {"connection_name": "db_a", "schema_name": "Application", "table": "StateProvinces"},
                    "destination": {"connection_name": "db_a", "schema_name": "Application", "table": "StateProvinces"},
                },
                {
                    "name": "declare_db_b_table",
                    "source": {"connection_name": "db_b", "schema_name": "Application", "table": "StateProvinces"},
                    "destination": {"connection_name": "db_b", "schema_name": "Application", "table": "StateProvinces"},
                },
                {
                    "name": "query_db_a_table",
                    "source": {"connection_name": "db_a", "query": "SELECT * FROM Application.StateProvinces"},
                    "destination": {"connection_name": "db_a", "schema_name": "stage", "table": "StateProvinces"},
                },
            ],
        },
    )

    lineage = build_lineage({"_documents": [document], "dataflows": document["dataflows"], "errors": []}, environment_id=34)

    dependency = next(item for item in lineage["dependencies"] if item["resolution_method"] == "connection_scoped_table")
    target = next(
        asset for asset in lineage["assets"]
        if asset.get("connection_name") == "db_a" and asset.get("database") == "DatabaseA"
    )
    assert dependency["resolved_asset_id"] == target["id"]
    assert dependency["resolution"] == {"state": "automatic", "reason": None}

def test_fully_qualified_database_reference_can_resolve_exact_cross_connection_target():
    document = normalize_metadata_document(
        1,
        "metadata.json",
        {
            "connections": [
                {
                    "name": "query_sqlserver",
                    "connection_type": "database",
                    "database": "Reporting",
                    "configure": {"database_type": "mssql", "connection_instance": "sqlserver:reporting"},
                },
                {
                    "name": "wwi_sqlserver",
                    "connection_type": "database",
                    "database": "WideWorldImporters",
                    "configure": {"database_type": "mssql", "connection_instance": "sqlserver:wwi"},
                },
            ],
            "dataflows": [
                {
                    "name": "declare_wwi_stateprovinces",
                    "source": {
                        "connection_name": "wwi_sqlserver",
                        "database": "WideWorldImporters",
                        "schema_name": "Application",
                        "table": "StateProvinces",
                    },
                    "destination": {
                        "connection_name": "wwi_sqlserver",
                        "database": "WideWorldImporters",
                        "schema_name": "Application",
                        "table": "StateProvinces",
                    },
                },
                {
                    "name": "query_external_stateprovinces",
                    "source": {
                        "connection_name": "query_sqlserver",
                        "query": "SELECT * FROM WideWorldImporters.Application.StateProvinces",
                    },
                    "destination": {
                        "connection_name": "query_sqlserver",
                        "database": "Reporting",
                        "schema_name": "stage",
                        "table": "StateProvinces",
                    },
                },
            ],
        },
    )

    lineage = build_lineage({"_documents": [document], "dataflows": document["dataflows"], "errors": []}, environment_id=33)

    dependency = next(item for item in lineage["dependencies"] if item["resolution_method"] == "qualified_cross_connection")
    assert dependency["resolution"] == {"state": "automatic", "reason": None}
    assert dependency["addressing_mode"] == "connection_bound"
    assert dependency["qualification_level"] == "fully_qualified"
