from __future__ import annotations

import copy
from typing import Any

import pytest

from datacoolie_studio.domains.analytics.schema_compiler import (
    SCHEMA_DOCUMENT,
    compile_mapping,
    contract_fingerprint,
    mapping_document,
    resolve_ref,
)
from datacoolie_studio.domains.analytics.schema_migration import diff_mapping_documents


def _record_definition(document: dict[str, Any], record_type: str) -> dict[str, Any]:
    for branch in document["oneOf"]:
        definition = resolve_ref(document, branch["$ref"])
        if definition["properties"]["_type"]["const"] == record_type:
            return definition  # type: ignore[return-value]
    raise AssertionError(f"missing record type {record_type}")


def _mapping_document(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "codec": "native-v1",
        "schema": document,
        "columns": {
            record_type: {
                name: node.duckdb_type
                for name, node in compile_mapping(document, record_type).items()
            }
            for record_type in ("dataflow_run_log", "job_run_log")
        },
    }


def _rename_all_defs(document: dict[str, Any]) -> dict[str, Any]:
    renamed = copy.deepcopy(document)
    names = list(renamed["$defs"])
    replacement = {name: f"definition_{index}" for index, name in enumerate(names)}
    renamed["$defs"] = {
        replacement[name]: definition for name, definition in renamed["$defs"].items()
    }

    def rewrite(value: Any) -> Any:
        if isinstance(value, dict):
            for key, child in list(value.items()):
                if key == "$ref" and isinstance(child, str) and child.startswith("#/$defs/"):
                    value[key] = "#/$defs/" + replacement[child.removeprefix("#/$defs/")]
                else:
                    rewrite(child)
        elif isinstance(value, list):
            for child in value:
                rewrite(child)
        return value

    rewrite(renamed)
    return renamed


def test_compiler_emits_explicit_native_struct_list_and_dynamic_json_types() -> None:
    compiled = _mapping_document(SCHEMA_DOCUMENT)
    dataflow = compiled["columns"]["dataflow_run_log"]
    job = compiled["columns"]["job_run_log"]

    assert dataflow["log_schema_version"] == "BIGINT"
    assert dataflow["read_request"].startswith("STRUCT(")
    assert dataflow["attempts"].endswith("[]")
    assert dataflow["read_request"].count("JSON") >= 2
    assert job["total_rows_read"] == "HUGEINT"
    assert len(contract_fingerprint()) == 64
    assert mapping_document()["codec"] == "native-v1"


def test_compiler_resolves_refs_without_assuming_definition_names() -> None:
    renamed = _rename_all_defs(SCHEMA_DOCUMENT)
    original = _mapping_document(SCHEMA_DOCUMENT)["columns"]
    compiled = _mapping_document(renamed)["columns"]

    assert compiled == original


def test_optional_root_struct_and_list_members_generate_mapping_without_registry_edits() -> None:
    document = copy.deepcopy(SCHEMA_DOCUMENT)
    dataflow = _record_definition(document, "dataflow_run_log")
    dataflow["properties"]["optional_root"] = {"type": ["string", "null"]}

    metrics = resolve_ref(document, dataflow["properties"]["metrics"]["$ref"])
    metrics["properties"]["optional_metric"] = {"type": ["integer", "null"]}

    issue = resolve_ref(
        document,
        resolve_ref(document, dataflow["properties"]["diagnostic_issues"]["$ref"])["items"]["$ref"],
    )
    issue["properties"]["optional_reason"] = {"type": ["string", "null"]}

    mapping = compile_mapping(document, "dataflow_run_log")
    assert mapping["optional_root"].duckdb_type == "VARCHAR"
    assert mapping["optional_root"].required is False
    assert '"optional_metric" HUGEINT' in mapping["metrics"].duckdb_type
    assert '"optional_reason" VARCHAR' in mapping["diagnostic_issues"].duckdb_type
    assert mapping["diagnostic_issues"].category == "list"


def test_optional_root_diff_is_additive_and_carries_fingerprints() -> None:
    previous = mapping_document(SCHEMA_DOCUMENT)
    target_schema = copy.deepcopy(SCHEMA_DOCUMENT)
    dataflow = _record_definition(target_schema, "dataflow_run_log")
    dataflow["properties"]["optional_root"] = {"type": ["string", "null"]}
    target = _mapping_document(target_schema)

    diff = diff_mapping_documents(
        previous,
        target,
        previous_fingerprint="before",
        target_fingerprint="after",
    )
    assert diff.kind == "additive_ddl"
    assert diff.reason_code == "optional_fields_added"
    assert diff.added_columns == ("dataflow_run_log.optional_root",)
    assert diff.previous_fingerprint == "before"
    assert diff.target_fingerprint == "after"


def test_optional_nested_struct_member_requires_safe_stored_column_rewrite() -> None:
    previous = mapping_document(SCHEMA_DOCUMENT)
    target_schema = copy.deepcopy(SCHEMA_DOCUMENT)
    dataflow = _record_definition(target_schema, "dataflow_run_log")
    metrics = resolve_ref(target_schema, dataflow["properties"]["metrics"]["$ref"])
    metrics["properties"]["optional_metric"] = {"type": ["integer", "null"]}
    target = _mapping_document(target_schema)

    diff = diff_mapping_documents(previous, target)
    assert diff.kind == "stored_column_rewrite"
    assert diff.reason_code == "optional_fields_added"


def test_recursive_dynamic_json_is_supported_but_fixed_recursive_struct_is_rejected() -> None:
    dynamic = copy.deepcopy(SCHEMA_DOCUMENT)
    dataflow = _record_definition(dynamic, "dataflow_run_log")
    dataflow["properties"]["dynamic_payload"] = {"$ref": "#/$defs/jsonValue"}
    dynamic_mapping = compile_mapping(dynamic, "dataflow_run_log")
    assert dynamic_mapping["dynamic_payload"].duckdb_type == "JSON"
    assert dynamic_mapping["dynamic_payload"].category == "json"

    recursive = copy.deepcopy(SCHEMA_DOCUMENT)
    recursive["$defs"]["fixedRecursive"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"next": {"$ref": "#/$defs/fixedRecursive"}},
    }
    _record_definition(recursive, "dataflow_run_log")["properties"]["recursive"] = {
        "$ref": "#/$defs/fixedRecursive"
    }
    with pytest.raises(ValueError, match="Unsupported recursive native schema"):
        compile_mapping(recursive, "dataflow_run_log")


def test_heterogeneous_union_and_tuple_array_are_rejected_explicitly() -> None:
    union = copy.deepcopy(SCHEMA_DOCUMENT)
    _record_definition(union, "dataflow_run_log")["properties"]["ambiguous"] = {
        "oneOf": [{"type": "string"}, {"type": "integer"}]
    }
    with pytest.raises(ValueError, match="Unsupported schema union"):
        compile_mapping(union, "dataflow_run_log")

    tuple_array = copy.deepcopy(SCHEMA_DOCUMENT)
    _record_definition(tuple_array, "dataflow_run_log")["properties"]["tuple"] = {
        "type": "array",
        "prefixItems": [{"type": "string"}],
        "items": {"type": "string"},
    }
    with pytest.raises(ValueError, match="Unsupported tuple/untyped array"):
        compile_mapping(tuple_array, "dataflow_run_log")



def test_numeric_range_plan_derives_optional_root_struct_and_list_fields():
    from datacoolie_studio.domains.analytics.schema_compiler import numeric_range_mapping
    from datacoolie_studio.domains.analytics.native_loading import _check_native_range
    from datacoolie_studio.domains.analytics.errors import AnalyticsSchemaIncompatibleError
    from decimal import Decimal

    document = copy.deepcopy(SCHEMA_DOCUMENT)
    definition = _record_definition(document, "dataflow_run_log")
    definition["properties"]["new_numeric"] = {"type": ["number", "null"]}
    definition["properties"]["new_container"] = {"type": ["object", "null"], "properties": {
        "label": {"type": "string"}, "counts": {"type": "array", "items": {"type": "integer"}}
    }, "additionalProperties": False}
    mapping = compile_mapping(document, "dataflow_run_log")
    pruned = numeric_range_mapping(mapping)
    assert "new_numeric" in pruned
    assert "dataflow_run_id" not in pruned
    assert [n.name for n in pruned["new_container"].children] == ["counts"]
    for value in (1, Decimal("1.0"), Decimal("1e0"), None):
        _check_native_range(pruned["new_container"], {"counts": [value]}, uri="canonical://new", line=7, field="/new_container")
    with pytest.raises(AnalyticsSchemaIncompatibleError, match="canonical://new:7.*new_container/counts/0.*HUGEINT"):
        _check_native_range(pruned["new_container"], {"counts": [2**127]}, uri="canonical://new", line=7, field="/new_container")
    with pytest.raises(AnalyticsSchemaIncompatibleError, match="new_numeric.*DOUBLE"):
        _check_native_range(pruned["new_numeric"], Decimal("1e1000"), uri="canonical://new", line=7, field="/new_numeric")


def test_numeric_range_reports_unbounded_integer_double_overflow_as_modeled_error():
    from datacoolie_studio.domains.analytics.schema_compiler import MappingNode
    from datacoolie_studio.domains.analytics.native_loading import _check_native_range
    from datacoolie_studio.domains.analytics.errors import AnalyticsSchemaIncompatibleError
    with pytest.raises(AnalyticsSchemaIncompatibleError, match="canonical://huge:9.*DOUBLE"):
        _check_native_range(MappingNode("measure", "DOUBLE", "number", False), 10**1000, uri="canonical://huge", line=9, field="/measure")
