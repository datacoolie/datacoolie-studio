"""Compile the pinned JSON Schema into explicit native DuckDB field types."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Mapping

from datacoolie_studio.domains.analytics.sql import quote_identifier


@dataclass(frozen=True)
class MappingNode:
    name: str
    duckdb_type: str
    category: str
    required: bool
    children: tuple[MappingNode, ...] = ()
    item: MappingNode | None = None
    schema_ref: str | None = None



def numeric_range_mapping(mapping: Mapping[str, MappingNode]) -> dict[str, MappingNode]:
    """Prune native mappings to numeric leaves and their container ancestors."""
    def prune(node: MappingNode) -> MappingNode | None:
        from dataclasses import replace

        if node.category in {"integer", "number"}:
            return node
        if node.category == "struct":
            children = tuple(child for source in node.children if (child := prune(source)) is not None)
            return replace(node, children=children) if children else None
        if node.category == "list" and node.item is not None:
            item = prune(node.item)
            return replace(node, item=item) if item is not None else None
        return None

    return {name: selected for name, node in mapping.items() if (selected := prune(node)) is not None}


def load_schema(version: int = 6) -> dict[str, Any]:
    return json.loads(
        files("datacoolie_studio.domains.analytics")
        .joinpath(f"logging_v{int(version)}.schema.json")
        .read_text(encoding="utf-8")
    )


def resolve_ref(document: Mapping[str, Any], ref: str) -> Mapping[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"Only pinned local schema references are supported: {ref}")
    value: Any = document
    for part in ref[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    return value


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    raise ValueError("Non-scalar enum/const requires an explicit schema type")


def compile_node(
    document: Mapping[str, Any],
    name: str,
    node: Mapping[str, Any],
    *,
    required: bool,
    refs: tuple[str, ...] = (),
) -> MappingNode:
    ref = node.get("$ref")
    if ref:
        if ref in refs:
            raise ValueError(f"Unsupported recursive native schema at {name}: {ref}")
        node = {
            **resolve_ref(document, ref),
            **{k: v for k, v in node.items() if k != "$ref"},
        }
        refs = (*refs, ref)
    raw_type = node.get("type")
    types = set(raw_type if isinstance(raw_type, list) else [raw_type]) - {None, "null"}
    if not types and "const" in node:
        types = {_value_type(node["const"])} - {"null"}
    if not types and "enum" in node:
        types = {_value_type(value) for value in node["enum"]} - {"null"}
    if not types:
        alternatives = node.get("oneOf", node.get("anyOf", []))
        # A heterogeneous value with a dynamic object branch is schema-approved
        # JSON, including recursive typed-value leaves. Do not unfold that recursion.
        if any(
            branch.get("type") == "object"
            and branch.get("additionalProperties", True) is not False
            for branch in alternatives
        ):
            return MappingNode(name, "JSON", "json", required, schema_ref=ref)
        branches = [
            compile_node(document, name, branch, required=required, refs=refs)
            for branch in alternatives
            if branch.get("type") != "null"
        ]
        if branches and len({branch.duckdb_type for branch in branches}) == 1:
            return branches[0]
        raise ValueError(f"Unsupported schema union at {name}")
    if len(types) != 1:
        raise ValueError(f"Unsupported schema types at {name}: {sorted(types)}")
    kind = next(iter(types))
    if kind == "object":
        if node.get("additionalProperties", True) is not False:
            return MappingNode(name, "JSON", "json", required, schema_ref=ref)
        properties = node.get("properties", {})
        if not properties:
            raise ValueError(f"Empty fixed native object at {name}")
        required_names = set(node.get("required", []))
        children = tuple(
            compile_node(
                document, key, child, required=key in required_names, refs=refs
            )
            for key, child in properties.items()
        )
        dtype = (
            "STRUCT("
            + ", ".join(
                f"{quote_identifier(child.name)} {child.duckdb_type}"
                for child in children
            )
            + ")"
        )
        return MappingNode(
            name, dtype, "struct", required, children=children, schema_ref=ref
        )
    if kind == "array":
        if "prefixItems" in node or not isinstance(node.get("items"), dict):
            raise ValueError(f"Unsupported tuple/untyped array at {name}")
        item = compile_node(
            document, name + "_item", node["items"], required=True, refs=refs
        )
        return MappingNode(
            name, item.duckdb_type + "[]", "list", required, item=item, schema_ref=ref
        )
    scalar = {
        "string": ("VARCHAR", "string"),
        "boolean": ("BOOLEAN", "boolean"),
        "number": ("DOUBLE", "number"),
        "integer": ("HUGEINT", "integer"),
    }
    if kind not in scalar:
        raise ValueError(f"Unsupported scalar schema at {name}: {kind}")
    dtype, category = scalar[kind]
    if kind == "integer":
        values = [node["const"]] if "const" in node else node.get("enum")
        bounded = values and all(
            isinstance(value, int)
            and not isinstance(value, bool)
            and -(2**63) <= value < 2**63
            for value in values
            if value is not None
        )
        if bounded or (
            node.get("minimum", -(2**128)) >= -(2**63)
            and node.get("maximum", 2**128) < 2**63
        ):
            dtype = "BIGINT"
    return MappingNode(name, dtype, category, required, schema_ref=ref)


def compile_mapping(
    document: Mapping[str, Any], record_type: str
) -> dict[str, MappingNode]:
    for branch in document.get("oneOf", []):
        resolved = resolve_ref(document, branch["$ref"]) if "$ref" in branch else branch
        if resolved.get("properties", {}).get("_type", {}).get("const") != record_type:
            continue
        root = compile_node(document, record_type, branch, required=True)
        if root.category != "struct":
            raise ValueError("Execution record must be a closed object")
        return {child.name: child for child in root.children}
    raise ValueError(f"Schema has no execution record type {record_type!r}")


SCHEMA_DOCUMENT = load_schema(6)
V7_SCHEMA_DOCUMENT = load_schema(7)


def mapping_document(document: Mapping[str, Any] | None = None) -> dict[str, Any]:
    document = SCHEMA_DOCUMENT if document is None else document
    return {
        "codec": "native-v1",
        "schema": document,
        "columns": {
            kind: {
                name: node.duckdb_type
                for name, node in compile_mapping(document, kind).items()
            }
            for kind in ("dataflow_run_log", "job_run_log")
        },
    }


def contract_fingerprint() -> str:
    return hashlib.sha256(
        json.dumps(mapping_document(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
