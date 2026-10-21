"""Metadata source naming, format, and write-capability policy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.storage.capabilities import (
    metadata_write_back_supported,
)


def metadata_source_name(source: EnvironmentSource) -> str:
    path = Path(source.uri).expanduser()
    if source.label:
        if source.label == path.stem:
            if path.parent.name.lower() in {
                "connection",
                "connections",
                "dataflow",
                "dataflows",
                "schema_hint",
                "schema_hints",
            }:
                return f"{path.parent.name}/{path.name}"
            metadata_root = metadata_root_uri(source)
            if metadata_root is not None:
                try:
                    relative = path.relative_to(metadata_root).as_posix()
                except ValueError:
                    relative = ""
                if "/" in relative:
                    return relative
        return source.label
    return path.name or source.uri


def editor_source_config(source: EnvironmentSource) -> dict[str, Any]:
    try:
        config = json.loads(source.source_config_json or "{}")
    except (TypeError, json.JSONDecodeError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    allowed = {
        "project_uri",
        "config_uri",
        "config_found",
        "config_revision",
        "manifest_uri",
        "manifest_found",
        "directive_kind",
        "directive_uri",
        "directive_revision",
        "metadata_root_uri",
        "metadata_root_uris",
        "sql_root_uri",
        "sql_root_uris",
        "functions_root_uri",
        "functions_root_uris",
        "component_paths",
        "component_paths_all",
        "component_descriptors",
        "component_path_provenance",
        "effective_component_path_provenance",
        "component_resolved_from",
        "component_path_diagnostics",
        "discovery_mode",
        "metadata_validation_mode",
        "datacoolie_compatible",
        "metadata_write_back_supported",
        "read_only",
    }
    result = {
        key: value
        for key, value in config.items()
        if key in allowed and value is not None
    }
    if source_is_read_only(source):
        result["read_only"] = True
    return result


def metadata_source_uses_strict_validation(
    source: EnvironmentSource | None,
) -> bool:
    return False


def source_is_read_only(source: EnvironmentSource) -> bool:
    """Return persisted source capability without probing external storage."""

    provider = str(getattr(source, "storage_provider", "local") or "local").lower()
    if metadata_format(Path(source.uri).expanduser()) == "xls":
        return True
    if not metadata_write_back_supported(provider):
        return True

    try:
        config = json.loads(source.source_config_json or "{}")
    except (TypeError, json.JSONDecodeError):
        config = {}
    if isinstance(config, dict):
        if config.get("read_only") is True:
            return True
        if config.get("metadata_write_back_supported") is False:
            return True

    try:
        validation = json.loads(source.read_check_result_json or "{}")
    except (TypeError, json.JSONDecodeError):
        validation = {}
    return (
        isinstance(validation, dict)
        and validation.get("metadata_write_back_supported") is False
    )


def metadata_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    if suffix == ".xlsx":
        return "xlsx"
    if suffix == ".xls":
        return "xls"
    return "json"


def metadata_root_uri(source: EnvironmentSource) -> Path | None:
    try:
        config = json.loads(source.source_config_json or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(config, dict):
        return None
    value = config.get("metadata_root_uri")
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value).expanduser()
