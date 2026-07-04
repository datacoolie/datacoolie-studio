from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlsplit


def build_asset_identifiers(endpoint: dict[str, Any]) -> list[dict[str, str]]:
    identifiers: list[dict[str, str]] = []
    logical = _logical_table_identifier(endpoint)
    physical = _physical_path_identifier(endpoint)
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

    if connection_type == "database" or database_type:
        parts = [database_type or "database", host or "local", port]
        return ":".join(part for part in parts if part)
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
    table = _clean(endpoint.get("table"))
    catalog = _clean(endpoint.get("catalog"))
    database = _clean(endpoint.get("database"))
    schema = _clean(endpoint.get("schema_name"))
    if not table or not (catalog or database):
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
