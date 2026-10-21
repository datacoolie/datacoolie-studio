from __future__ import annotations


from datacoolie_studio.domains.lineage.service import build_lineage
from datacoolie_studio.domains.metadata.normalizer import (
    normalize_metadata_document,
)
from tests.support.paths import FIXTURE_ROOT


from tests.domains.lineage.lineage_test_support import (
    _ambiguous_orders_metadata,
    _schema_table_suffix_metadata,
)

SAMPLE_METADATA = FIXTURE_ROOT / "usecase-sim" / "metadata" / "file" / "local_use_cases.json"

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
    assert observation["automatic_suggestion"]["method"] == "connection_scoped_table"

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
