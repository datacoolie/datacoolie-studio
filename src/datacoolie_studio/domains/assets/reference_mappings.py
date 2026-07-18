from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from datacoolie_studio.domains.assets.identifiers import (
    api_authority,
    normalize_api_url,
    normalize_physical_path,
)
from datacoolie_studio.domains.assets.reference_identity import (
    ReferenceSignature,
    normalize_optional,
    normalize_reference_type,
    normalize_reference_value,
    require_text,
)


TARGET_IDENTIFIER_KINDS = {"logical_table", "physical_path", "api_endpoint"}


@dataclass(slots=True)
class MappingMatch:
    status: Literal["none", "matched", "ambiguous"]
    mapping: dict[str, Any] | None = None
    candidate_mapping_ids: list[int] = field(default_factory=list)

def normalize_target_identifier(kind: str, value: str) -> tuple[str, str]:
    normalized_kind = _normalize_target_identifier_kind(kind)
    if normalized_kind == "logical_table":
        normalized_value = _normalize_logical_table_identifier(value)
    elif normalized_kind == "api_endpoint":
        normalized_value = _normalize_api_endpoint_identifier(value)
    else:
        normalized_value = normalize_physical_path(_require_text(value, "target_value"))
    return normalized_kind, normalized_value


def match_reference_mapping(
    signature: ReferenceSignature,
    mappings: list[dict[str, Any]],
) -> MappingMatch:
    matches = [mapping for mapping in mappings if _mapping_matches_signature(mapping, signature)]
    if not matches:
        return MappingMatch(status="none")
    candidate_ids = [int(item.get("id") or 0) for item in matches if int(item.get("id") or 0) > 0]
    if len(matches) > 1:
        return MappingMatch(status="ambiguous", candidate_mapping_ids=candidate_ids)
    return MappingMatch(status="matched", mapping=matches[0], candidate_mapping_ids=candidate_ids)


def _mapping_matches_signature(mapping: dict[str, Any], signature: ReferenceSignature) -> bool:
    try:
        mapping_type = normalize_reference_type(str(mapping.get("reference_type") or ""))
        mapping_value = normalize_reference_value(signature.reference_type, str(mapping.get("reference_normalized_value") or ""))
    except ValueError:
        return False
    if mapping_type != signature.reference_type:
        return False
    if mapping_value != signature.normalized_value:
        return False
    return True


def _normalize_target_identifier_kind(value: str) -> str:
    normalized = normalize_optional(value, lower=True)
    if normalized not in TARGET_IDENTIFIER_KINDS:
        raise ValueError(f"Unsupported target identifier kind: {value}")
    return normalized


def _normalize_logical_table_identifier(value: str) -> str:
    text = require_text(value, "target_value").lower()
    if "|" not in text:
        return text
    namespace, logical_name = text.split("|", 1)
    namespace = namespace.strip()
    logical_name = ".".join(part.strip() for part in logical_name.split(".") if part.strip())
    return f"{namespace}|{logical_name}" if namespace else logical_name


def _normalize_api_endpoint_identifier(value: str) -> str:
    text = require_text(value, "target_value")
    namespace = ""
    endpoint = text
    if "|" in text:
        namespace, endpoint = text.split("|", 1)
        namespace = api_authority(namespace.strip()) or namespace.strip().lower()
    parts = endpoint.strip().split(None, 1)
    if len(parts) == 2 and parts[0].isalpha():
        method, url = parts[0].upper(), parts[1]
    else:
        method, url = "GET", endpoint
    normalized_url = normalize_api_url(url)
    if not namespace:
        namespace = api_authority(normalized_url)
    normalized = f"{method} {normalized_url}"
    return f"{namespace}|{normalized}" if namespace else normalized


def _require_text(value: Any, field_name: str) -> str:
    return require_text(value, field_name)


def _normalize_optional(value: Any, *, lower: bool) -> str | None:
    return normalize_optional(value, lower=lower)
