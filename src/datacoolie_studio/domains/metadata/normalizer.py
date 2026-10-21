from __future__ import annotations

from typing import Any

from datacoolie_studio.core.identity import name_to_uuid
from datacoolie_studio.domains.assets.endpoint_identity import (
    endpoint_identity,
    endpoint_identity_quality,
    project_endpoint_identity,
)
from datacoolie_studio.domains.assets.identifiers import build_asset_identifiers


# Reproject unchanged editor documents after the v3 Assets identity correction;
# persisted normalized payloads can contain canonical asset IDs.
METADATA_NORMALIZER_VERSION = "metadata-normalizer-v8-assets-identity"


def normalize_metadata_document(source_id: int, source_uri: str, raw: dict[str, Any]) -> dict[str, Any]:
    try:
        return _normalize_metadata_projection(source_id, source_uri, raw)
    except (TypeError, ValueError, AttributeError):
        # Authoring can precede runnable metadata. Preserve the editor payload;
        # lineage/assets are unavailable until its runtime shapes can project.
        return _normalize_metadata_projection(source_id, source_uri, {})


def _normalize_metadata_projection(source_id: int, source_uri: str, raw: dict[str, Any]) -> dict[str, Any]:
    from datacoolie_studio.domains.metadata.document_normalization import opaque_metadata_sections

    opaque = opaque_metadata_sections(raw)
    raw = {key: value for key, value in raw.items() if key not in opaque}
    connections = [dict(item, metadata_source_id=source_id, metadata_source_uri=source_uri) for item in raw.get("connections", [])]
    connection_by_name = _index_connections(connections, "name")
    connection_by_id = _index_connections(connections, "connection_id")
    schema_hints = _flatten_schema_hints(source_id, source_uri, raw.get("schema_hints", []))
    dataflows = []
    nodes: dict[str, dict[str, Any]] = {}
    edges = []
    active_index = 0
    for item in raw.get("dataflows", []):
        if not _is_active(item.get("is_active", True)):
            continue
        source = _resolve_endpoint(item.get("source", {}), connection_by_name, connection_by_id)
        destination = _resolve_endpoint(item.get("destination", {}), connection_by_name, connection_by_id)
        for endpoint in (source, destination):
            endpoint["scope_source_id"] = source_id
            if endpoint.get("workspace_id") is None:
                endpoint["workspace_id"] = item.get("workspace_id")
        source_asset = build_asset(source, "source")
        destination_asset = build_asset(destination, "destination")
        nodes[source_asset["id"]] = source_asset
        nodes[destination_asset["id"]] = destination_asset
        for role in ("source", "destination"):
            for candidate in _endpoint_candidate_nodes(item.get(role, {}), role, source_id, item.get("workspace_id"), connection_by_name, connection_by_id):
                nodes[candidate["id"]] = candidate
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
            "workspace_id": item.get("workspace_id"),
            "group_number": item.get("group_number"),
            "execution_order": item.get("execution_order"),
            "processing_mode": item.get("processing_mode", "batch"),
            "is_active": item.get("is_active", True),
            "configure": item.get("configure", {}),
            "load_type": destination.get("load_type"),
            "source": source,
            "destination": destination,
            "_identity_authored_endpoints": {"source": item.get("source", {}), "destination": item.get("destination", {})},
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
    connection_by_name = _index_connections(
        [
            connection
            for document in documents
            for connection in document.get("connections", [])
            if isinstance(connection, dict)
        ],
        "name",
    )
    connection_by_id = _index_connections(
        [
            connection
            for document in documents
            for connection in document.get("connections", [])
            if isinstance(connection, dict)
        ],
        "connection_id",
    )
    if not connection_by_name and not connection_by_id:
        return documents

    for document in documents:
        nodes: dict[str, dict[str, Any]] = {}
        edges = []
        source_info = document.get("source") or {}
        source_id = int(source_info.get("id") or 0)
        source_uri = str(source_info.get("uri") or "")
        for index, dataflow in enumerate(document.get("dataflows", [])):
            authored = dataflow.get("_identity_authored_endpoints") or dataflow
            source = _resolve_endpoint(authored.get("source", {}), connection_by_name, connection_by_id)
            destination = _resolve_endpoint(authored.get("destination", {}), connection_by_name, connection_by_id)
            for role, endpoint in (("source", source), ("destination", destination)):
                previous = dataflow.get(role) or {}
                for key in ("query_content", "query_file", "query_resolution_error"):
                    if previous.get(key) is not None:
                        endpoint[key] = previous[key]
            for endpoint in (source, destination):
                endpoint["scope_source_id"] = source_id
                if endpoint.get("workspace_id") is None:
                    endpoint["workspace_id"] = dataflow.get("workspace_id")
            source_asset = build_asset(source, "source")
            destination_asset = build_asset(destination, "destination")
            nodes[source_asset["id"]] = source_asset
            nodes[destination_asset["id"]] = destination_asset
            for role in ("source", "destination"):
                for candidate in _endpoint_candidate_nodes(authored.get(role, {}), role, source_id, dataflow.get("workspace_id"), connection_by_name, connection_by_id):
                    nodes[candidate["id"]] = candidate

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
                "group_number": dataflow.get("group_number"),
                "execution_order": dataflow.get("execution_order"),
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


def _index_connections(
    connections: list[dict[str, Any]],
    field: str,
) -> dict[str, list[dict[str, Any]]]:
    """Index all candidates without silently applying last-source-wins.

    Duplicate names are valid metadata input.  Keeping the candidate list
    lets endpoint resolution preserve an ambiguity until Assets can compare
    the complete binding rather than choosing whichever row happened to be
    iterated last.
    """
    index: dict[str, list[dict[str, Any]]] = {}
    for connection in connections:
        value = _string(connection.get(field))
        if not value:
            continue
        index.setdefault(value, []).append(connection)
    for value, candidates in index.items():
        index[value] = sorted(
            candidates,
            key=lambda candidate: (
                _string(candidate.get("connection_id")),
                _string(candidate.get("name")),
                repr(sorted(candidate.items())),
            ),
        )
    return index


def _lookup_connections(
    index: dict[str, list[dict[str, Any]]] | dict[str, dict[str, Any]],
    value: str,
) -> list[dict[str, Any]]:
    candidates = index.get(value) or []
    if isinstance(candidates, dict):
        return [candidates]
    return [dict(item) for item in candidates if isinstance(item, dict)]


def _select_connection_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {}
    if len(candidates) == 1:
        return dict(candidates[0])
    # Equivalent duplicate declarations are safe to coalesce.  Distinct
    # configure/binding values remain unresolved so metadata cannot invent a
    # host, path or database by source order.
    fingerprints = {
        repr(sorted(_identity_connection_projection(candidate).items()))
        for candidate in candidates
    }
    return dict(candidates[0]) if len(fingerprints) == 1 else {}


def _endpoint_candidate_nodes(endpoint, role, source_id, workspace_id, by_name, by_id):
    """Retain declared bindings without assigning an ambiguous flow to one."""
    reference = endpoint.get("connection") if isinstance(endpoint, dict) else None
    if isinstance(reference, dict):
        return []
    reference = str(endpoint.get("connection_name") or endpoint.get("connection_id") or reference or "") if isinstance(endpoint, dict) else ""
    candidates = _lookup_connections(by_name, reference) or _lookup_connections(by_id, reference)
    if len(candidates) < 2 or _select_connection_candidate(candidates):
        return []
    nodes = []
    for candidate in candidates:
        resolved = _resolve_endpoint(endpoint, {reference: [candidate]}, {reference: [candidate]})
        resolved["scope_source_id"] = source_id
        if resolved.get("workspace_id") is None:
            resolved["workspace_id"] = workspace_id
        nodes.append({**build_asset(resolved, role), "identity_candidate": True})
    return nodes


def _identity_connection_projection(connection: dict[str, Any]) -> dict[str, Any]:
    projection = project_endpoint_identity({}, connection)
    # Connection name/id are lookup labels and must not make two equivalent
    # physical declarations appear different. The remaining fields use the
    # same endpoint projection as metadata/log resolution, including the
    # filtered binding configure values.
    return {
        key: projection.get(key)
        for key in (
            "connection_type",
            "connection_instance",
            "format",
            "catalog",
            "database",
            "database_type",
            "host",
            "port",
            "workspace_id",
            "base_path",
            "base_url",
            "namespace",
            "bucket",
            "container",
            "configure",
        )
    }


def _resolve_endpoint(
    raw_endpoint: dict[str, Any],
    connection_by_name: dict[str, list[dict[str, Any]]] | dict[str, dict[str, Any]],
    connection_by_id: dict[str, list[dict[str, Any]]] | dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    endpoint = dict(raw_endpoint) if isinstance(raw_endpoint, dict) else {}
    connection_by_id = connection_by_id or {}
    # A normalized endpoint carries both a display/reference name and the
    # original inline connection dictionary.  Prefer the dictionary whenever
    # it is present so a second enrichment pass cannot discard inline
    # configure/base-path fields by looking up the generated display name in
    # the named-connection map.
    inline_reference = endpoint.get("connection")
    raw_reference = (
        inline_reference
        if isinstance(inline_reference, dict)
        else endpoint.get("connection_name") or endpoint.get("connection_id") or inline_reference
    )
    inline_connection: dict[str, Any] | None = None
    if isinstance(raw_reference, dict):
        inline_connection = dict(raw_reference)
        conn_name = _string(inline_connection.get("name") or inline_connection.get("connection_name")) or None
        conn_id = _string(inline_connection.get("connection_id")) or (
            name_to_uuid(conn_name) if conn_name else None
        )
        conn = inline_connection
    elif isinstance(raw_reference, str):
        reference = raw_reference.strip()
        candidates = _lookup_connections(connection_by_name, reference)
        if not candidates and connection_by_id is not None:
            candidates = _lookup_connections(connection_by_id, reference)
        conn = _select_connection_candidate(candidates)
        conn_name = _string(conn.get("name")) or reference or None
        conn_id = _string(conn.get("connection_id")) or (
            name_to_uuid(conn_name) if conn_name and conn else None
        )
    else:
        conn_name = None
        conn_id = None
        conn = {}
    source_configure = endpoint.get("configure")
    source_configure = dict(source_configure) if isinstance(source_configure, dict) else {}
    projection_connection = dict(conn)
    if conn_name:
        projection_connection.setdefault("name", conn_name)
        projection_connection.setdefault("connection_name", conn_name)
    if conn_id:
        projection_connection.setdefault("connection_id", conn_id)
    resolved = project_endpoint_identity(endpoint, projection_connection)
    # SQL content is attached after normalization by the SQL resolver.
    # Preserve the derived preview fields when connection enrichment runs
    # again (for example, for draft lineage materialization).
    resolved.update({
        "query": endpoint.get("query"),
        # SQL content is attached after normalization by the SQL resolver.
        # Preserve the derived preview fields when connection enrichment runs
        # again (for example, for draft lineage materialization).
        "query_content": endpoint.get("query_content"),
        "query_file": endpoint.get("query_file"),
        "query_resolution_error": endpoint.get("query_resolution_error"),
        "filter_expression": endpoint.get("filter_expression"),
        "python_function": endpoint.get("python_function"),
        "load_type": endpoint.get("load_type"),
        "merge_keys": endpoint.get("merge_keys", []),
        "partition_columns": endpoint.get("partition_columns", []),
        "watermark_columns": endpoint.get("watermark_columns", []),
        "configure": source_configure,
    })
    if inline_connection is not None:
        resolved["connection"] = inline_connection
    return resolved


def build_asset(endpoint: dict[str, Any], role: str) -> dict[str, Any]:
    identity_type, identity, label = endpoint_identity(endpoint)
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
        "identity_quality": endpoint_identity_quality(endpoint, identity_type),
        "identifiers": identifiers,
        "connection_name": endpoint.get("connection_name"),
        "connection_id": endpoint.get("connection_id"),
        "connection_instance": endpoint.get("connection_instance"),
        "connection_type": endpoint.get("connection_type"),
        "format": endpoint.get("format"),
        "database_type": endpoint.get("database_type"),
        "host": endpoint.get("host"),
        "port": endpoint.get("port"),
        "workspace_id": endpoint.get("workspace_id"),
        "base_path": endpoint.get("base_path"),
        "catalog": endpoint.get("catalog"),
        "database": endpoint.get("database"),
        "schema_name": endpoint.get("schema_name"),
        "table": endpoint.get("table"),
        "path": endpoint.get("path"),
        "query": endpoint.get("query"),
        "query_file": endpoint.get("query_file"),
        "filter_expression": endpoint.get("filter_expression"),
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
    """Compatibility wrapper for callers that used the old private helper.

    Assets owns the implementation; keeping this adapter avoids forcing every
    metadata/lineage caller to change at once.
    """
    return endpoint_identity(endpoint)


def _flatten_schema_hints(source_id: int, source_uri: str, raw_hints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for group in raw_hints:
        for hint in group.get("hints", []):
            row = {
                "metadata_source_id": source_id,
                "metadata_source_uri": source_uri,
                "connection_name": group.get("connection_name") or group.get("connection_id"),
                "table_name": group.get("table_name"),
                "schema_name": group.get("schema_name"),
                **hint,
            }
            if group.get("connection_id"):
                row["connection_id"] = group["connection_id"]
            rows.append(row)
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


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""
