from __future__ import annotations

import hashlib
from typing import Any

from datacoolie_studio.core.identity import name_to_uuid
from datacoolie_studio.domains.assets.identifiers import build_asset_identifiers


METADATA_NORMALIZER_VERSION = "metadata-normalizer-v4"


def normalize_metadata_document(source_id: int, source_uri: str, raw: dict[str, Any]) -> dict[str, Any]:
    connections = [dict(item, metadata_source_id=source_id, metadata_source_uri=source_uri) for item in raw.get("connections", [])]
    connection_by_name = {conn.get("name"): conn for conn in connections if conn.get("name")}
    schema_hints = _flatten_schema_hints(source_id, source_uri, raw.get("schema_hints", []))
    dataflows = []
    nodes: dict[str, dict[str, Any]] = {}
    edges = []
    active_index = 0
    for item in raw.get("dataflows", []):
        if not _is_active(item.get("is_active", True)):
            continue
        source = _resolve_endpoint(item.get("source", {}), connection_by_name)
        destination = _resolve_endpoint(item.get("destination", {}), connection_by_name)
        source_asset = build_asset(source, "source")
        destination_asset = build_asset(destination, "destination")
        nodes[source_asset["id"]] = source_asset
        nodes[destination_asset["id"]] = destination_asset
        active_index += 1
        dataflow_name = item.get("name") or item.get("dataflow_id") or f"dataflow_{active_index}"
        dataflow_id = item.get("dataflow_id") or name_to_uuid(str(dataflow_name))
        dataflow = {
            "metadata_source_id": source_id,
            "metadata_source_uri": source_uri,
            "dataflow_id": dataflow_id,
            "name": dataflow_name,
            "description": item.get("description"),
            "stage": item.get("stage"),
            "processing_mode": item.get("processing_mode", "batch"),
            "is_active": item.get("is_active", True),
            "load_type": destination.get("load_type"),
            "source": source,
            "destination": destination,
            "transform": item.get("transform", {}),
            "source_asset_id": source_asset["id"],
            "destination_asset_id": destination_asset["id"],
        }
        dataflows.append(dataflow)
        edges.append({
            "id": f"{source_id}:{active_index - 1}:{_slug(dataflow_name)}",
            "dataflow_id": dataflow_id,
            "source": source_asset["id"],
            "target": destination_asset["id"],
            "dataflow_name": dataflow_name,
            "stage": item.get("stage"),
            "load_type": destination.get("load_type"),
            "metadata_source_id": source_id,
            "metadata_source_uri": source_uri,
            "provenance": {
                "metadata_source_id": source_id,
                "metadata_source_uri": source_uri,
                "dataflow_name": dataflow_name,
                "stage": item.get("stage"),
            },
        })
    return {
        "_normalizer_version": METADATA_NORMALIZER_VERSION,
        "source": {
            "id": source_id,
            "uri": source_uri,
            "connections": len(connections),
            "dataflows": len(dataflows),
            "schema_hints": len(schema_hints),
        },
        "connections": connections,
        "dataflows": dataflows,
        "schema_hints": schema_hints,
        "nodes": list(nodes.values()),
        "edges": edges,
    }


def enrich_metadata_documents_with_connections(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    connection_by_name = {
        connection.get("name"): connection
        for document in documents
        for connection in document.get("connections", [])
        if connection.get("name")
    }
    if not connection_by_name:
        return documents

    for document in documents:
        nodes: dict[str, dict[str, Any]] = {}
        edges = []
        source_info = document.get("source") or {}
        source_id = int(source_info.get("id") or 0)
        source_uri = str(source_info.get("uri") or "")
        for index, dataflow in enumerate(document.get("dataflows", [])):
            source = _resolve_endpoint(dataflow.get("source", {}), connection_by_name)
            destination = _resolve_endpoint(dataflow.get("destination", {}), connection_by_name)
            source_asset = build_asset(source, "source")
            destination_asset = build_asset(destination, "destination")
            nodes[source_asset["id"]] = source_asset
            nodes[destination_asset["id"]] = destination_asset

            dataflow["source"] = source
            dataflow["destination"] = destination
            dataflow["source_asset_id"] = source_asset["id"]
            dataflow["destination_asset_id"] = destination_asset["id"]
            dataflow["load_type"] = destination.get("load_type")

            dataflow_name = dataflow.get("name") or dataflow.get("dataflow_id") or f"dataflow_{index + 1}"
            edges.append({
                "id": f"{source_id}:{index}:{_slug(dataflow_name)}",
                "dataflow_id": dataflow.get("dataflow_id") or name_to_uuid(str(dataflow_name)),
                "source": source_asset["id"],
                "target": destination_asset["id"],
                "dataflow_name": dataflow_name,
                "stage": dataflow.get("stage"),
                "load_type": destination.get("load_type"),
                "metadata_source_id": source_id,
                "metadata_source_uri": source_uri,
                "provenance": {
                    "metadata_source_id": source_id,
                    "metadata_source_uri": source_uri,
                    "dataflow_name": dataflow_name,
                    "stage": dataflow.get("stage"),
                },
            })
        if document.get("dataflows"):
            document["nodes"] = list(nodes.values())
            document["edges"] = edges
    return documents


def _resolve_endpoint(raw_endpoint: dict[str, Any], connection_by_name: dict[str, dict[str, Any]]) -> dict[str, Any]:
    endpoint = dict(raw_endpoint or {})
    conn_name = endpoint.get("connection_name") or endpoint.get("connection")
    conn = dict(connection_by_name.get(conn_name, {})) if isinstance(conn_name, str) else {}
    configure = conn.get("configure") or {}
    source_configure = endpoint.get("configure") or {}
    return {
        "connection_name": conn_name,
        "connection_instance": conn.get("connection_instance") or configure.get("connection_instance"),
        "connection_type": conn.get("connection_type"),
        "format": conn.get("format"),
        "catalog": conn.get("catalog") or configure.get("catalog"),
        "database": conn.get("database") or configure.get("database"),
        "database_type": configure.get("database_type"),
        "host": configure.get("host"),
        "port": configure.get("port"),
        "workspace_id": conn.get("workspace_id") or configure.get("workspace_id"),
        "base_path": configure.get("base_path"),
        "base_url": configure.get("base_url"),
        "schema_name": endpoint.get("schema_name") or endpoint.get("schema"),
        "table": endpoint.get("table"),
        "query": endpoint.get("query"),
        "python_function": endpoint.get("python_function"),
        "api_endpoint": source_configure.get("endpoint"),
        "method": source_configure.get("method", "GET"),
        "path": _endpoint_path(conn, endpoint),
        "load_type": endpoint.get("load_type"),
        "merge_keys": endpoint.get("merge_keys", []),
        "partition_columns": endpoint.get("partition_columns", []),
        "watermark_columns": endpoint.get("watermark_columns", []),
        "configure": source_configure,
    }


def _endpoint_path(connection: dict[str, Any], endpoint: dict[str, Any]) -> str | None:
    configure = connection.get("configure") or {}
    base_path = configure.get("base_path")
    table = endpoint.get("table")
    if not base_path or not table:
        return None
    schema = endpoint.get("schema_name") or endpoint.get("schema")
    parts = [str(base_path).replace("\\", "/").rstrip("/")]
    if schema:
        parts.append(str(schema).strip("/"))
    parts.append(str(table).strip("/"))
    return "/".join(part.strip("/") if index else part for index, part in enumerate(parts)).replace("\\", "/")


def build_asset(endpoint: dict[str, Any], role: str) -> dict[str, Any]:
    identity_type, identity, label = _asset_identity(endpoint)
    identifiers = (
        []
        if endpoint.get("query") or endpoint.get("python_function")
        else build_asset_identifiers(endpoint)
    )
    endpoint_locator = _endpoint_locator(endpoint, identity_type, label)
    endpoint_kind = _endpoint_kind(endpoint, identity_type)
    return {
        "id": identity,
        "label": label,
        "display_label": endpoint_locator,
        "endpoint_locator": endpoint_locator,
        "endpoint_kind": endpoint_kind,
        "role": role,
        "identity": identity,
        "identity_type": identity_type,
        "identifiers": identifiers,
        "connection_name": endpoint.get("connection_name"),
        "connection_type": endpoint.get("connection_type"),
        "format": endpoint.get("format"),
        "catalog": endpoint.get("catalog"),
        "database": endpoint.get("database"),
        "schema_name": endpoint.get("schema_name"),
        "table": endpoint.get("table"),
        "path": endpoint.get("path"),
        "query": endpoint.get("query"),
        "python_function": endpoint.get("python_function"),
        "base_url": endpoint.get("base_url"),
        "api_endpoint": endpoint.get("api_endpoint"),
        "method": endpoint.get("method"),
    }


def _endpoint_locator(endpoint: dict[str, Any], identity_type: str, fallback: str) -> str:
    table = endpoint.get("table")
    schema = endpoint.get("schema_name")
    if identity_type == "logical_table":
        parts = [
            endpoint.get("catalog"),
            endpoint.get("database"),
            schema,
            table,
        ]
        return ".".join(str(part) for part in parts if part)
    if identity_type == "physical_path":
        if schema and table:
            return f"{schema}/{table}"
        path = endpoint.get("path")
        if path:
            return _tail_path(str(path), 2)
        if table:
            return str(table)
    if endpoint.get("query"):
        return "SQL query"
    if endpoint.get("python_function"):
        return str(endpoint["python_function"])
    if identity_type == "api_endpoint":
        identifier = next(
            (item for item in build_asset_identifiers(endpoint) if item["kind"] == "api_endpoint"),
            None,
        )
        if identifier:
            return identifier["display_value"]
    return str(table or fallback)


def _endpoint_kind(endpoint: dict[str, Any], identity_type: str) -> str:
    if endpoint.get("query"):
        return "sql"
    if endpoint.get("python_function"):
        return "python"
    if identity_type == "logical_table":
        return "table"
    connection_type = str(endpoint.get("connection_type") or "").lower()
    if connection_type in {"api", "http", "rest"}:
        return "api"
    if identity_type == "physical_path":
        return "file"
    return connection_type or "unresolved"


def _tail_path(value: str, segments: int) -> str:
    parts = [part for part in value.replace("\\", "/").rstrip("/").split("/") if part and part != "."]
    return "/".join(parts[-segments:]) if parts else value


def _asset_identity(endpoint: dict[str, Any]) -> tuple[str, str, str]:
    if endpoint.get("query"):
        digest = _computational_identity_digest(endpoint, "query")
        return "unresolved", f"query:{digest}", "SQL query"
    if endpoint.get("python_function"):
        value = str(endpoint["python_function"])
        digest = _computational_identity_digest(endpoint, "python_function")
        return "unresolved", f"function:{digest}", value
    api_identifier = next(
        (item for item in build_asset_identifiers(endpoint) if item["kind"] == "api_endpoint"),
        None,
    )
    if api_identifier:
        return "api_endpoint", f"api:{api_identifier['normalized_value']}", api_identifier["display_value"]
    table = endpoint.get("table")
    catalog = endpoint.get("catalog")
    database = endpoint.get("database")
    if table and (catalog or database):
        identifier = next(
            item for item in build_asset_identifiers(endpoint)
            if item["kind"] == "logical_table"
        )
        return "logical_table", f"table:{identifier['normalized_value']}", identifier["display_value"]
    path = endpoint.get("path")
    if path:
        identifier = next(
            item for item in build_asset_identifiers(endpoint)
            if item["kind"] == "physical_path"
        )
        return "physical_path", f"path:{identifier['normalized_value']}", str(path)
    fallback = endpoint.get("connection_name") or "unknown"
    digest = hashlib.sha1(repr(endpoint).encode("utf-8")).hexdigest()[:12]
    return "unresolved", f"unresolved:{fallback}:{digest}", str(fallback)


def _computational_identity_digest(endpoint: dict[str, Any], implementation_key: str) -> str:
    identity = {
        "implementation": endpoint.get(implementation_key),
        "connection_name": endpoint.get("connection_name"),
        "connection_type": endpoint.get("connection_type"),
        "catalog": endpoint.get("catalog"),
        "database": endpoint.get("database"),
        "schema_name": endpoint.get("schema_name"),
        "base_path": endpoint.get("base_path"),
    }
    return hashlib.sha1(repr(sorted(identity.items())).encode("utf-8")).hexdigest()[:20]


def _flatten_schema_hints(source_id: int, source_uri: str, raw_hints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for group in raw_hints:
        for hint in group.get("hints", []):
            rows.append({
                "metadata_source_id": source_id,
                "metadata_source_uri": source_uri,
                "connection_name": group.get("connection_name") or group.get("connection_id"),
                "table_name": group.get("table_name"),
                "schema_name": group.get("schema_name"),
                **hint,
            })
    return rows


def _slug(value: object) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value)).strip("_")


def _is_active(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"false", "0", "no", "n"}:
            return False
        if normalized in {"true", "1", "yes", "y"}:
            return True
    return value is not False
