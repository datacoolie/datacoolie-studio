from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def build_asset_identifiers(endpoint: dict[str, Any]) -> list[dict[str, str]]:
    identifiers: list[dict[str, str]] = []
    api = _api_endpoint_identifier(endpoint)
    logical = _logical_table_identifier(endpoint)
    physical = _physical_path_identifier(endpoint)
    if api:
        identifiers.append(api)
    if logical:
        identifiers.append(logical)
    if physical:
        identifiers.append(physical)
    return identifiers


def identifier_qualification(endpoint: dict[str, Any], kind: str) -> str:
    """Describe how strongly an endpoint locator is bound.

    A relative/local path can still be a useful Assets declaration, but its
    identity is scoped by the available connection/workspace binding.  Keeping
    this explicit prevents a weak label from being presented as a globally
    resolved physical URI.
    """
    if kind == "api_endpoint":
        # The endpoint itself may be an absolute URL even when the connection
        # has no separate base URL.  Keep the binding strength consistent for
        # captured and metadata representations.
        endpoint_url = api_endpoint_url(
            endpoint.get("base_url"),
            endpoint.get("api_endpoint"),
        )
        return "physical_exact" if api_authority(endpoint_url) else "weak_logical"
    if kind == "physical_path":
        path = _clean(endpoint.get("path"))
        if storage_authority(path):
            return "physical_exact"
        scope = _binding_scope(endpoint)
        return "scoped_weak" if scope else "weak_logical"
    if kind == "logical_table":
        scope = _binding_scope(endpoint)
        return "connection_bound" if scope else "weak_logical"
    return "weak_logical"


def canonical_asset_id(environment_id: int, identifier: dict[str, str]) -> str:
    value = f"{environment_id}:{identifier['kind']}:{identifier['normalized_value']}"
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:20]
    return f"asset:{digest}"


def connection_instance(endpoint: dict[str, Any]) -> str:
    explicit = _clean(endpoint.get("connection_instance"))
    if explicit:
        return explicit

    connection_type = _clean(endpoint.get("connection_type")).lower()
    database_type = _clean(endpoint.get("database_type")).lower()
    host = _clean(endpoint.get("host")).lower()
    port = _clean(endpoint.get("port"))
    workspace = _clean(endpoint.get("workspace_id")).lower()
    base_path = _clean(endpoint.get("base_path"))
    base_url = _clean(endpoint.get("base_url"))

    if connection_type == "database" or database_type:
        # A database type or port alone does not identify a physical
        # namespace. Require a host; an explicit instance was handled above.
        if host:
            parts = [database_type or "database", host, port]
            return ":".join(part for part in parts if part)
        # A producer workspace is a lookup context, not a database authority.
        # Without a host or explicit instance this remains a weak declaration;
        # using the workspace here would merge unrelated databases.
        return ""
    if connection_type in {"api", "http", "rest"} and base_url:
        return api_authority(base_url)
    if workspace:
        return f"workspace:{workspace}"
    authority = storage_authority(base_path)
    if authority:
        return authority

    catalog = _clean(endpoint.get("catalog")).lower()
    database = _clean(endpoint.get("database")).lower()
    if catalog or database:
        return f"catalog:{catalog or '_'}:{database or '_'}"
    return ""


def database_resolution_scope(endpoint: dict[str, Any]) -> str:
    """Return a matching scope without changing persisted asset identifiers.

    ``connection_instance`` intentionally remains the stable physical namespace
    used by logical identifiers. Database resolution also needs the selected
    catalog/database, especially when host information is absent or shared.
    Non-database connections keep their existing scope so Lakehouse and file
    references retain their current cross-connection behavior.
    """

    physical = connection_instance(endpoint).strip().lower()
    connection_type = _clean(endpoint.get("connection_type")).lower()
    database_type = _clean(endpoint.get("database_type")).lower()
    if connection_type != "database" and not database_type:
        return physical

    if not physical:
        return ""
    parts = [physical]
    catalog = _clean(endpoint.get("catalog")).lower()
    database = _clean(endpoint.get("database")).lower()
    if catalog:
        parts.append(f"catalog:{catalog}")
    if database:
        parts.append(f"database:{database}")
    return "|".join(parts)


def normalize_api_url(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        return ""
    parsed = urlsplit(normalized)
    if not parsed.scheme:
        path = _normalize_url_path(normalized)
        return path or "/"
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = _normalize_url_path(parsed.path)
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def api_authority(value: str | None) -> str:
    if not value:
        return ""
    normalized = normalize_api_url(value)
    parsed = urlsplit(normalized)
    if not parsed.scheme:
        return ""
    path = parsed.path.rstrip("/")
    base = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    return f"{base}{path}" if path else base


def api_origin(value: str | None) -> str:
    """Return the scheme/host binding for an absolute API URL."""
    if not value:
        return ""
    normalized = normalize_api_url(value)
    parsed = urlsplit(normalized)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def api_endpoint_url(base_url: str | None, endpoint: str | None) -> str:
    base = _clean(base_url)
    route = _clean(endpoint)
    if "://" in route:
        return normalize_api_url(route)
    if not base:
        return ""
    if route:
        return normalize_api_url(f"{base.rstrip('/')}/{route.lstrip('/')}")
    return normalize_api_url(base)


def normalize_physical_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    if "://" in normalized:
        scheme, remainder = normalized.split("://", 1)
        scheme = "s3" if scheme.lower() == "s3a" else scheme.lower()
        while "//" in remainder:
            remainder = remainder.replace("//", "/")
        normalized = f"{scheme}://{remainder}"
    else:
        while "//" in normalized:
            normalized = normalized.replace("//", "/")
    return normalized.rstrip("/")


def storage_authority(value: str | None) -> str:
    if not value:
        return ""
    normalized = normalize_physical_path(value)
    if "://" not in normalized:
        if normalized.startswith("/Volumes/"):
            parts = [part for part in normalized.split("/") if part]
            return f"databricks-volume:{'/'.join(parts[:4])}" if len(parts) >= 4 else "databricks-volume"
        if normalized.startswith("dbfs:/"):
            return "dbfs"
        return ""
    parsed = urlsplit(normalized)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _logical_table_identifier(endpoint: dict[str, Any]) -> dict[str, str] | None:
    connection_type = _clean(endpoint.get("connection_type")).lower()
    if connection_type in {"api", "http", "rest"}:
        return None
    table = _clean(endpoint.get("table"))
    catalog = _clean(endpoint.get("catalog"))
    database = _clean(endpoint.get("database"))
    schema = _clean(endpoint.get("schema_name"))
    if not table or not (catalog or database or schema):
        return None
    qualified = ".".join(part for part in (catalog, database, schema, table) if part)
    instance = connection_instance(endpoint)
    normalized_name = ".".join(part.lower() for part in (catalog, database, schema, table) if part)
    normalized = f"{instance}|{normalized_name}" if instance else normalized_name
    if identifier_qualification(endpoint, "logical_table") == "weak_logical":
        normalized = f"{_declaration_scope(endpoint)}|{normalized}"
    return {
        "kind": "logical_table",
        "normalized_value": normalized,
        "display_value": qualified,
        "namespace": instance,
        "qualification": identifier_qualification(endpoint, "logical_table"),
        "source": "metadata",
    }


def _api_endpoint_identifier(endpoint: dict[str, Any]) -> dict[str, str] | None:
    connection_type = _clean(endpoint.get("connection_type")).lower()
    if connection_type not in {"api", "http", "rest"}:
        return None
    url = api_endpoint_url(endpoint.get("base_url"), endpoint.get("api_endpoint"))
    if not url:
        return None
    method = _clean(endpoint.get("method")).upper() or "GET"
    # When a producer stores the complete URL in ``endpoint`` but omits the
    # connection base URL, derive the same authority namespace from that URL.
    namespace = api_origin(url)
    normalized = f"{method} {url.lower() if '://' not in url else url}"
    if namespace:
        normalized = f"{namespace}|{normalized}"
    return {
        "kind": "api_endpoint",
        "normalized_value": normalized,
        "display_value": f"{method} {url}",
        "namespace": namespace,
        "qualification": identifier_qualification(endpoint, "api_endpoint"),
        "source": "metadata",
    }


def _physical_path_identifier(endpoint: dict[str, Any]) -> dict[str, str] | None:
    path = _clean(endpoint.get("path"))
    if not path:
        return None
    normalized = normalize_physical_path(path)
    namespace = storage_authority(normalized)
    if not namespace:
        # Relative/local paths are scoped by the physical connection when one
        # exists.  Do not silently merge the same relative label from two
        # otherwise unrelated connections.
        scope = _binding_scope(endpoint)
        if scope:
            normalized = f"scope:{scope}|{normalized}"
        normalized = f"{_declaration_scope(endpoint)}|{normalized}"
    return {
        "kind": "physical_path",
        "normalized_value": normalized,
        "display_value": path,
        "namespace": namespace,
        "qualification": identifier_qualification(endpoint, "physical_path"),
        "source": "metadata",
    }


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _declaration_scope(endpoint: dict[str, Any]) -> str:
    import json
    return "declaration:" + hashlib.sha1(json.dumps([
        endpoint.get("scope_source_id"), endpoint.get("workspace_id"),
        endpoint.get("connection_id"), endpoint.get("connection_name"),
    ], separators=(",", ":"), default=str).encode("utf-8")).hexdigest()[:20]


def _binding_scope(endpoint: dict[str, Any]) -> str:
    """Return an actual connection/workspace scope, never fake localhost."""
    explicit = _clean(endpoint.get("connection_instance"))
    if explicit:
        return explicit
    connection_type = _clean(endpoint.get("connection_type")).lower()
    database_type = _clean(endpoint.get("database_type")).lower()
    host = _clean(endpoint.get("host"))
    port = _clean(endpoint.get("port"))
    workspace = _clean(endpoint.get("workspace_id"))
    if connection_type == "database" or database_type:
        # Database type, port and producer workspace are descriptors, not
        # enough to identify a physical namespace. A host or explicit instance
        # is required; workspace is only a lookup context.
        if host:
            return ":".join(part for part in (database_type or "database", host, port) if part)
        return ""
    if connection_type in {"api", "http", "rest"}:
        return api_authority(
            api_endpoint_url(
                _clean(endpoint.get("base_url")),
                _clean(endpoint.get("api_endpoint")),
            )
        )
    workspace = _clean(endpoint.get("workspace_id"))
    if workspace:
        return f"workspace:{workspace.lower()}"
    # Catalog/database bindings are valid logical scopes even when the
    # physical connection has no host or explicit instance. Reuse the same
    # canonical projection used by logical identifiers so published metadata
    # from separate files can resolve against one qualified table.
    instance = connection_instance(endpoint)
    if instance:
        return instance
    return storage_authority(_clean(endpoint.get("base_path")))


def _normalize_url_path(value: str) -> str:
    path = value.strip().replace("\\", "/")
    while "//" in path:
        path = path.replace("//", "/")
    if path != "/":
        path = path.rstrip("/")
    return path
