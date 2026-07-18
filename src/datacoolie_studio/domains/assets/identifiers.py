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
        parts = [database_type or "database", host or "local", port]
        return ":".join(part for part in parts if part)
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
    return {
        "kind": "logical_table",
        "normalized_value": normalized,
        "display_value": qualified,
        "namespace": instance,
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
    namespace = api_authority(endpoint.get("base_url"))
    normalized = f"{method} {url.lower() if '://' not in url else url}"
    if namespace:
        normalized = f"{namespace}|{normalized}"
    return {
        "kind": "api_endpoint",
        "normalized_value": normalized,
        "display_value": f"{method} {url}",
        "namespace": namespace,
        "source": "metadata",
    }


def _physical_path_identifier(endpoint: dict[str, Any]) -> dict[str, str] | None:
    path = _clean(endpoint.get("path"))
    if not path:
        return None
    normalized = normalize_physical_path(path)
    return {
        "kind": "physical_path",
        "normalized_value": normalized,
        "display_value": path,
        "namespace": storage_authority(normalized),
        "source": "metadata",
    }


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _normalize_url_path(value: str) -> str:
    path = value.strip().replace("\\", "/")
    while "//" in path:
        path = path.replace("//", "/")
    if path != "/":
        path = path.rstrip("/")
    return path
