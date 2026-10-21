"""Shared endpoint identity and destination-signature rules.

This module deliberately contains no database or metadata I/O.  Metadata
normalization and Monitoring adapters use the same pure functions so that an
endpoint described by a published document and an endpoint captured in a log
cannot acquire different identity semantics merely because they came from a
different producer.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from datacoolie_studio.domains.assets.identifiers import (
    build_asset_identifiers,
    identifier_qualification,
)


# v3 removes declaration/source scope from qualified Lakehouse
# catalog/database tables.  Keep the fence in the canonical identity owner so
# every derived Assets, Lineage and Monitoring cache misses IDs produced by the
# previous weak binding policy.
ASSETS_ENDPOINT_IDENTITY_VERSION = "assets-endpoint-identity-v3"

# These fields are the complete input to the SQL signature emitted below.  The
# source/dataflow/connection values are lookup context; they are intentionally
# not included in the canonical object ID itself.
DESTINATION_SIGNATURE_COLUMNS: tuple[str, ...] = (
    "_source_id",
    "workspace_id",
    "dataflow_id",
    "dataflow_name",
    "destination_id",
    "destination_name",
    "destination_connection_type",
    "destination_catalog",
    "destination_database",
    "destination_schema",
    "destination_table",
    "destination_full_table",
    "destination_path",
    "destination_configure",
)
# Lower-case spelling is kept as a convenient read-model import while the
# upper-case constant follows the project's constants convention.
destination_signature_columns = DESTINATION_SIGNATURE_COLUMNS

# Only fields that can describe an endpoint binding are admitted from the
# endpoint configure JSON.  Write modes, merge options and credentials stay
# out of the identity signature and cache payload.
DESTINATION_CONFIGURE_IDENTITY_FIELDS: tuple[str, ...] = (
    "base_path",
    "base_url",
    "endpoint",
    "method",
    "host",
    "port",
    "database_type",
    "connection_instance",
    "workspace_id",
    "catalog",
    "database",
    "schema",
    "namespace",
    "bucket",
    "container",
)


def endpoint_identity(endpoint: Mapping[str, Any]) -> tuple[str, str, str]:
    """Return ``(identity_type, stable_candidate_id, display_label)``.

    The candidate ID is environment independent.  ``AssetRegistry`` applies
    the environment namespace when it creates the public ``asset:`` ID.  The
    computational query/function branches are kept here because they are
    assets with an existing identity contract even though they have no table,
    path or API locator.
    """

    if _present(endpoint.get("query")):
        digest = computational_identity_digest(endpoint, "query")
        return "unresolved", f"query:{digest}", "SQL query"
    if _present(endpoint.get("python_function")):
        value = str(endpoint["python_function"])
        digest = computational_identity_digest(endpoint, "python_function")
        return "unresolved", f"function:{digest}", value

    identifiers = build_asset_identifiers(dict(endpoint))
    api_identifier = next(
        (item for item in identifiers if item["kind"] == "api_endpoint"),
        None,
    )
    if api_identifier is not None:
        return (
            "api_endpoint",
            f"api:{api_identifier['normalized_value']}",
            api_identifier["display_value"],
        )

    table = endpoint.get("table")
    catalog = endpoint.get("catalog")
    database = endpoint.get("database")
    logical_identifier = next(
        (item for item in identifiers if item["kind"] == "logical_table"),
        None,
    )
    if table and (catalog or database or endpoint.get("schema_name")) and logical_identifier is not None:
        return (
            "logical_table",
            f"table:{logical_identifier['normalized_value']}",
            logical_identifier["display_value"],
        )

    path = endpoint.get("path")
    physical_identifier = next(
        (item for item in identifiers if item["kind"] == "physical_path"),
        None,
    )
    if _present(path) and physical_identifier is not None:
        return (
            "physical_path",
            f"path:{physical_identifier['normalized_value']}",
            str(path),
        )

    weak = weak_identity_identifier(endpoint)
    fallback = _first_text(endpoint, "connection_name", "connection_id") or "unknown"
    return "unresolved", weak["normalized_value"], str(fallback)


def endpoint_identity_quality(endpoint: Mapping[str, Any], identity_type: str) -> str:
    """Return the binding strength without changing the stable tuple API."""
    if identity_type in {"api_endpoint", "logical_table", "physical_path"}:
        return identifier_qualification(dict(endpoint), identity_type)
    return "computational" if endpoint.get("query") or endpoint.get("python_function") else "unresolved"


def project_endpoint_identity(
    endpoint: Mapping[str, Any] | None,
    connection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project one metadata/log endpoint into the shared identity fields.

    Adapters may retain their own display/configuration fields around this
    projection, but binding selection and path derivation must use this same
    pure rule. Connection configure is read only through the allowlist; the
    returned ``configure`` is also allowlisted so the compact catalog cannot
    carry credentials or write options.
    """
    endpoint = endpoint if isinstance(endpoint, Mapping) else {}
    connection = connection if isinstance(connection, Mapping) else {}
    connection_configure = connection.get("configure")
    connection_configure = (
        connection_configure if isinstance(connection_configure, Mapping) else {}
    )
    endpoint_configure = endpoint.get("configure")
    endpoint_configure = (
        endpoint_configure if isinstance(endpoint_configure, Mapping) else {}
    )

    def value(*values: Any) -> Any:
        for candidate in values:
            if candidate is None:
                continue
            if isinstance(candidate, str) and not candidate.strip():
                continue
            if not isinstance(candidate, (str, int, float, bool)):
                # A malformed binding container is not a valid fallback to a
                # later source. The adapter can diagnose it separately.
                return None
            return candidate
        return None

    resolved: dict[str, Any] = {
        "connection_name": value(connection.get("name"), connection.get("connection_name"), endpoint.get("connection_name")),
        "connection_id": value(connection.get("connection_id"), endpoint.get("connection_id")),
        "connection_type": value(endpoint.get("connection_type"), connection.get("connection_type")),
        "format": value(endpoint.get("format"), connection.get("format")),
        "connection_instance": value(connection.get("connection_instance"), connection_configure.get("connection_instance")),
        "database_type": value(connection.get("database_type"), connection_configure.get("database_type")),
        "host": value(connection.get("host"), connection_configure.get("host")),
        "port": value(connection.get("port"), connection_configure.get("port")),
        "workspace_id": value(endpoint.get("workspace_id"), connection.get("workspace_id"), connection_configure.get("workspace_id")),
        "base_path": value(endpoint.get("base_path"), endpoint_configure.get("base_path"), connection.get("base_path"), connection_configure.get("base_path")),
        "base_url": value(endpoint.get("base_url"), endpoint_configure.get("base_url"), connection.get("base_url"), connection_configure.get("base_url")),
        "namespace": value(endpoint.get("namespace"), endpoint_configure.get("namespace"), connection.get("namespace"), connection_configure.get("namespace")),
        "bucket": value(endpoint.get("bucket"), endpoint_configure.get("bucket"), connection.get("bucket"), connection_configure.get("bucket")),
        "container": value(endpoint.get("container"), endpoint_configure.get("container"), connection.get("container"), connection_configure.get("container")),
        "catalog": value(endpoint.get("catalog"), connection.get("catalog"), connection_configure.get("catalog")),
        "database": value(endpoint.get("database"), connection.get("database"), connection_configure.get("database")),
        "schema_name": value(endpoint.get("schema_name"), endpoint.get("schema")),
        "table": value(endpoint.get("table")),
        "full_table": value(endpoint.get("full_table"), endpoint.get("fullTable")),
        "path": value(endpoint.get("path")),
        "api_endpoint": value(endpoint_configure.get("endpoint"), endpoint.get("api_endpoint")),
        "method": value(endpoint_configure.get("method"), endpoint.get("method"), "GET"),
        "query": value(endpoint.get("query")),
        "python_function": value(endpoint.get("python_function")),
        "configure": {
            key: value(endpoint_configure.get(key))
            for key in DESTINATION_CONFIGURE_IDENTITY_FIELDS
            if value(endpoint_configure.get(key)) is not None
        },
    }
    if (
        _present(resolved.get("path")) is False
        and _present(resolved.get("base_path"))
        and _present(resolved.get("table"))
    ):
        pieces = [str(resolved["base_path"]).replace("\\", "/").rstrip("/")]
        if _present(resolved.get("schema_name")):
            pieces.append(str(resolved["schema_name"]).strip("/"))
        pieces.append(str(resolved["table"]).strip("/"))
        resolved["path"] = "/".join(pieces)
    return resolved


def weak_identity_identifier(endpoint: Mapping[str, Any]) -> dict[str, str]:
    """Build a stable environment-scoped weak declaration identifier.

    Dataflow IDs and runtime dimensions are deliberately excluded. Source scope
    applies only to weak declarations. A
    weak row still gets a stable Assets target for one-to-one joins, while its
    resolution status communicates that the object was not physically bound.
    """
    projection = {
        "connection_id": endpoint.get("connection_id"),
        "connection_name": endpoint.get("connection_name"),
        # This is used only when the declaration remains weak. Qualified
        # identifiers never include producer source scope.
        "scope_source_id": endpoint.get("scope_source_id"),
        "connection_type": endpoint.get("connection_type"),
        "connection_instance": endpoint.get("connection_instance"),
        "database_type": endpoint.get("database_type"),
        "host": endpoint.get("host"),
        "port": endpoint.get("port"),
        "workspace_id": endpoint.get("workspace_id"),
        "base_path": endpoint.get("base_path"),
        "base_url": endpoint.get("base_url"),
        "namespace": endpoint.get("namespace"),
        "bucket": endpoint.get("bucket"),
        "container": endpoint.get("container"),
        "api_endpoint": endpoint.get("api_endpoint"),
        "method": endpoint.get("method"),
        "catalog": endpoint.get("catalog"),
        "database": endpoint.get("database"),
        "schema_name": endpoint.get("schema_name"),
        "table": endpoint.get("table"),
        "full_table": endpoint.get("full_table"),
        "path": endpoint.get("path"),
        "query": endpoint.get("query"),
        "python_function": endpoint.get("python_function"),
    }
    digest = _stable_digest(projection)[:20]
    label = _first_text(endpoint, "table", "path", "connection_name", "connection_id") or "unresolved"
    return {
        "kind": "unresolved",
        "normalized_value": f"unresolved:weak:{digest}",
        "display_value": label,
        "namespace": _first_text(endpoint, "workspace_id", "connection_instance") or "",
        "qualification": "weak_logical",
        "source": "metadata",
    }


def primary_asset_identifier(
    node: Mapping[str, Any],
    identifiers: Mapping[tuple[str, str], Mapping[str, str]],
) -> dict[str, str]:
    """Choose the canonical seed identifier using one deterministic policy."""

    preferred_kind = _text(node.get("identity_type"))
    for (kind, _), identifier in sorted(identifiers.items()):
        if kind == preferred_kind:
            return dict(identifier)
    for kind in ("api_endpoint", "logical_table", "physical_path"):
        for (identifier_kind, _), identifier in sorted(identifiers.items()):
            if identifier_kind == kind:
                return dict(identifier)
    return {
        "kind": preferred_kind or "unresolved",
        "normalized_value": _text(node.get("identity")) or _text(node.get("id")) or "unknown",
        "display_value": _text(node.get("label")) or _text(node.get("id")) or "unknown",
        "namespace": "",
        "source": "metadata",
    }


def asset_seed_sort_key(node: Mapping[str, Any]) -> tuple[str, ...]:
    """Stable order used before seeding a registry.

    Sorting by all identifiers prevents the first document/source encountered
    from deciding whether a path or a qualified table becomes the group seed.
    """

    identifiers = tuple(
        sorted(
            (
                _text(item.get("kind")),
                _text(item.get("normalized_value")),
            )
            for item in (node.get("identifiers") or [])
            if isinstance(item, Mapping)
        )
    )
    return (
        identifiers[0][0] if identifiers else "unresolved",
        identifiers[0][1] if identifiers else "",
        json.dumps(identifiers, ensure_ascii=False, separators=(",", ":")),
        _text(node.get("id")),
        _text(node.get("label")),
    )


def destination_signature_payload(values: Mapping[str, Any]) -> dict[str, Any]:
    """Return the JSON-safe Python equivalent of ``destination_signature_sql``.

    SQL receives serving-fact column names while callers using a resolved log
    row often have short names.  Both forms are accepted.  Scalar values are
    converted to their SQL VARCHAR representation while null remains null, so
    null, empty text, zero and false retain distinct meanings.
    """

    def get(column: str, *aliases: str) -> Any:
        for name in (column, *aliases):
            if name in values:
                return values[name]
        return None

    payload: dict[str, Any] = {}
    payload["source_id"] = _sql_scalar(get("_source_id", "source_id"))
    for field in ("workspace_id", "dataflow_id", "dataflow_name"):
        payload[field] = _sql_scalar(get(field))
    payload["destination_id"] = _sql_scalar(get("destination_id", "connection_id"))
    payload["destination_name"] = _sql_scalar(get("destination_name", "connection_name"))
    payload["destination_connection_type"] = _sql_scalar(
        get("destination_connection_type", "connection_type")
    )
    for field in (
        "destination_catalog",
        "destination_database",
        "destination_schema",
        "destination_table",
        "destination_full_table",
        "destination_path",
    ):
        payload[field] = _sql_scalar(get(field, field.removeprefix("destination_")))
    binding = get("destination_configure_binding")
    if isinstance(binding, (list, tuple)):
        payload["configure_binding"] = [
            _sql_scalar(binding[index]) if index < len(binding) else None
            for index in range(len(DESTINATION_CONFIGURE_IDENTITY_FIELDS))
        ]
    else:
        configure = get("destination_configure", "configure")
        configure = configure if isinstance(configure, Mapping) else _parse_json_object(configure)
        payload["configure_binding"] = [
            _sql_scalar(configure.get(field) if configure else None)
            for field in DESTINATION_CONFIGURE_IDENTITY_FIELDS
        ]
    raw_configure = get("destination_configure", "configure")
    parsed = raw_configure
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except (TypeError, ValueError):
            parsed = "invalid"
    payload["configure_invalid"] = bool(values.get("destination_configure_invalid")) or (
        raw_configure is not None and not isinstance(parsed, Mapping)
    ) or (isinstance(parsed, Mapping) and any(
        isinstance(parsed.get(field), (Mapping, list, tuple))
        for field in DESTINATION_CONFIGURE_IDENTITY_FIELDS
    ))
    return payload


def destination_signature(values: Mapping[str, Any]) -> str:
    return json.dumps(
        destination_signature_payload(values),
        ensure_ascii=False,
        # Keep insertion order equal to ``destination_signature_sql``.  The
        # signature is an opaque join key, so JSON key sorting here would not
        # match DuckDB's struct field order even when the values are equal.
        sort_keys=False,
        separators=(",", ":"),
    )


def destination_signature_sql(
    alias: str = "d",
    *,
    configure_column: str = "destination_configure",
) -> str:
    """Return the canonical DuckDB signature expression for a fact alias.

    The alias is restricted to a SQL identifier because it is always supplied
    by an internal query builder. Configure values are extracted as one
    ordered allowlist operation; this avoids shipping or parsing unrelated raw
    log JSON in Monitoring. The ``destination_configure_binding`` form is
    already a VARCHAR[] produced by that operation and is used directly.
    """

    alias = _sql_identifier(alias)
    configure_column = _sql_identifier(configure_column)
    fields: list[tuple[str, str]] = [
        ("source_id", f"CAST({alias}._source_id AS VARCHAR)"),
        ("workspace_id", f"CAST({alias}.workspace_id AS VARCHAR)"),
        ("dataflow_id", f"CAST({alias}.dataflow_id AS VARCHAR)"),
        ("dataflow_name", f"CAST({alias}.dataflow_name AS VARCHAR)"),
        ("destination_id", f"CAST({alias}.destination_id AS VARCHAR)"),
        ("destination_name", f"CAST({alias}.destination_name AS VARCHAR)"),
        (
            "destination_connection_type",
            f"CAST({alias}.destination_connection_type AS VARCHAR)",
        ),
    ]
    for field in (
        "destination_catalog",
        "destination_database",
        "destination_schema",
        "destination_table",
        "destination_full_table",
        "destination_path",
    ):
        fields.append((field, f"CAST({alias}.{field} AS VARCHAR)"))
    if configure_column == "destination_configure_binding":
        # Cache producers already project the allowlist to VARCHAR[] once in
        # the fact scan. Do not run JSON extraction against that list.
        configure_binding = f"{alias}.{configure_column}"
        invalid = f"{alias}.destination_configure_invalid"
    else:
        configure = f"TRY_CAST({alias}.{configure_column} AS JSON)"
        paths = ", ".join(repr(f"$.{field}") for field in DESTINATION_CONFIGURE_IDENTITY_FIELDS)
        null_binding = "[" + ", ".join("NULL::VARCHAR" for _ in DESTINATION_CONFIGURE_IDENTITY_FIELDS) + "]"
        # Extract the allowlist as one ordered list. Nested objects/arrays are
        # malformed identity bindings: DuckDB would otherwise preserve their
        # source key order while Python canonicalizes them, producing a join
        # key mismatch (and potentially carrying credential-shaped content).
        # Keep those entries NULL in both adapters. The second list extraction
        # is only the type guard; no per-key JSON parsing is introduced.
        values = f"json_extract_string({configure}, [{paths}])"
        types = f"json_type({configure}, [{paths}])"
        configure_binding = (
            "COALESCE("
            f"list_transform({values}, (value, index) -> "
            f"CASE WHEN list_extract({types}, index) IN ('OBJECT', 'ARRAY') "
            "THEN NULL ELSE value END), "
            f"{null_binding})"
        )
        invalid = (f"COALESCE(({alias}.{configure_column} IS NOT NULL AND ({configure} IS NULL OR json_type({configure}) <> 'OBJECT')) "
                   f"OR list_contains({types}, 'OBJECT') OR list_contains({types}, 'ARRAY'), FALSE)")
    fields.append(("configure_binding", configure_binding))
    fields.append(("configure_invalid", invalid))
    packed = ", ".join(f"{name} := {expression}" for name, expression in fields)
    return f"CAST(to_json(struct_pack({packed})) AS VARCHAR)"


def computational_identity_digest(endpoint: Mapping[str, Any], implementation_key: str) -> str:
    identity = {
        "implementation": endpoint.get(implementation_key),
        "connection_name": endpoint.get("connection_name"),
        "connection_id": endpoint.get("connection_id"),
        "connection_type": endpoint.get("connection_type"),
        "catalog": endpoint.get("catalog"),
        "database": endpoint.get("database"),
        "schema_name": endpoint.get("schema_name"),
        "base_path": endpoint.get("base_path"),
    }
    # Preserve the pre-shared-identity computational IDs.  They are already
    # part of the public Assets/Lineage contract.
    return hashlib.sha1(repr(sorted(identity.items())).encode("utf-8")).hexdigest()[:20]


def endpoint_identity_version() -> str:
    return ASSETS_ENDPOINT_IDENTITY_VERSION


def _stable_digest(value: Any) -> str:
    canonical = json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _parse_json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _sql_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    # Identity bindings are scalar only. Treat nested objects/arrays as an
    # invalid/missing binding so SQL and Python cannot disagree on JSON object
    # key ordering or accidentally include credential-shaped configuration.
    return None


def _first_text(value: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        text = _text(value.get(key))
        if text:
            return text
    return None


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _present(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _sql_identifier(value: str) -> str:
    text = str(value or "d")
    if not text.replace("_", "").isalnum() or text[0].isdigit():
        raise ValueError("SQL alias must be an internal identifier")
    return text


__all__ = [
    "ASSETS_ENDPOINT_IDENTITY_VERSION",
    "DESTINATION_CONFIGURE_IDENTITY_FIELDS",
    "DESTINATION_SIGNATURE_COLUMNS",
    "asset_seed_sort_key",
    "computational_identity_digest",
    "destination_signature",
    "destination_signature_columns",
    "destination_signature_payload",
    "destination_signature_sql",
    "endpoint_identity",
    "endpoint_identity_quality",
    "project_endpoint_identity",
    "weak_identity_identifier",
    "endpoint_identity_version",
    "primary_asset_identifier",
]
