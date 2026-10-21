"""Offline admission of the pinned DataCoolie v6 and v7 log contracts."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from jsonschema_rs import Draft202012Validator

from datacoolie_studio.domains.analytics.schema_compiler import (
    SCHEMA_DOCUMENT,
    V7_SCHEMA_DOCUMENT,
    resolve_ref,
)

from datacoolie_studio.domains.analytics.errors import AnalyticsSchemaIncompatibleError


LOG_SCHEMA_VERSION = 6
SUPPORTED_LOG_SCHEMA_VERSIONS = (6, 7)
_SCHEMA_DOCUMENTS = {6: SCHEMA_DOCUMENT, 7: V7_SCHEMA_DOCUMENT}


@lru_cache(maxsize=2)
def log_validator(version: int = LOG_SCHEMA_VERSION) -> Draft202012Validator:
    try:
        document = _SCHEMA_DOCUMENTS[int(version)]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Unsupported log schema version: {version!r}") from exc
    return Draft202012Validator(document, validate_formats=True)


@lru_cache(maxsize=6)
def record_validator(
    expected_type: str,
    version: int = LOG_SCHEMA_VERSION,
) -> Draft202012Validator:
    try:
        document = _SCHEMA_DOCUMENTS[int(version)]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Unsupported log schema version: {version!r}") from exc
    for branch in document["oneOf"]:
        definition = resolve_ref(document, branch["$ref"]) if "$ref" in branch else branch
        if definition.get("properties", {}).get("_type", {}).get("const") == expected_type:
            return Draft202012Validator({"$defs": document["$defs"], **definition},
                                        validate_formats=True)
    raise ValueError(f"Unsupported log record type: {expected_type}")


def admit_log_record(record: Any, *, expected_type: str, uri: str, line: int) -> bool:
    """Exclude unsupported versions; reject malformed current records atomically."""
    if not isinstance(record, dict):
        raise AnalyticsSchemaIncompatibleError(f"{uri}:{line}: log record must be an object")
    version = record.get("log_schema_version")
    if isinstance(version, bool) or version not in SUPPORTED_LOG_SCHEMA_VERSIONS:
        return False
    if record.get("_type") != expected_type:
        raise AnalyticsSchemaIncompatibleError(
            f"{uri}:{line}: expected {expected_type!r}, received {record.get('_type')!r}"
        )
    validator = record_validator(expected_type, int(version))
    if not validator.is_valid(record):
        error = next(validator.iter_errors(record))
        path = "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in error.instance_path)
        raise AnalyticsSchemaIncompatibleError(
            f"{uri}:{line}: invalid v{int(version)} record at {path}: {error.message}"
        )
    return True
