from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from datacoolie_studio.domains.analysis.models import InputEvidence
from datacoolie_studio.domains.assets.identifiers import (
    database_resolution_scope,
    normalize_api_url,
    normalize_physical_path,
    storage_authority,
)


REFERENCE_TYPES = {"table_reference", "path_reference", "api_endpoint_reference", "unknown"}
AddressingMode = Literal["physical_exact", "connection_bound", "qualified_logical", "weak_logical"]
QualificationLevel = Literal["table", "schema_table", "fully_qualified"]


@dataclass(frozen=True, slots=True)
class ReferenceSignature:
    reference_type: str
    normalized_value: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "reference_type": self.reference_type,
            "normalized_value": self.normalized_value,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)


def canonical_reference_id(signature: ReferenceSignature) -> str:
    value = signature.to_json()
    return f"reference:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:20]}"


def normalize_reference_signature(
    *,
    reference_type: str,
    value: str,
) -> ReferenceSignature:
    normalized_type = normalize_reference_type(reference_type)
    normalized_value = normalize_reference_value(normalized_type, value)
    return ReferenceSignature(
        reference_type=normalized_type,
        normalized_value=normalized_value,
    )


def build_reference_signature(evidence: InputEvidence) -> ReferenceSignature:
    reference_type = reference_type_from_evidence(evidence.kind)
    value = evidence.value
    if reference_type == "table_reference":
        qualified = ".".join(
            part.strip()
            for part in (evidence.catalog, evidence.database, evidence.schema_name, evidence.table)
            if str(part or "").strip()
        )
        value = qualified or evidence.value
    elif reference_type == "path_reference":
        value = normalize_physical_path(evidence.value)
    elif reference_type == "api_endpoint_reference":
        value = _normalize_api_reference_input(evidence.value, evidence.details)
    return normalize_reference_signature(
        reference_type=reference_type,
        value=value,
    )


ContextScopeSource = Literal["detected", "metadata_context"]


@dataclass(frozen=True, slots=True)
class ReferenceContextScope:
    value: str
    source: ContextScopeSource


def build_reference_context_scope(
    evidence: InputEvidence,
    context: dict[str, Any],
) -> ReferenceContextScope | None:
    reference_type = reference_type_from_evidence(evidence.kind)
    if reference_type == "table_reference":
        value = normalize_optional(database_resolution_scope(context), lower=True)
        return ReferenceContextScope(value, "metadata_context") if value else None
    if reference_type == "path_reference":
        value = normalize_optional(storage_authority(evidence.value), lower=True)
        return ReferenceContextScope(value, "detected") if value else None
    if reference_type == "api_endpoint_reference":
        normalized = _normalize_api_reference_input(evidence.value, evidence.details)
        value = _api_reference_scope(_api_reference_url(normalized))
        return ReferenceContextScope(value, "detected") if value else None
    return None


def reference_type_from_evidence(value: str) -> str:
    if value == "table":
        return "table_reference"
    if value == "path":
        return "path_reference"
    if value in {"api", "api_endpoint", "http"}:
        return "api_endpoint_reference"
    return "unknown"


def reference_identifier_parts(evidence: InputEvidence) -> tuple[str, ...]:
    details = evidence.details if isinstance(evidence.details, dict) else {}
    raw_parts = details.get("identifier_parts")
    if isinstance(raw_parts, (list, tuple)):
        parts = tuple(str(part).strip() for part in raw_parts if str(part).strip())
        if parts:
            return parts
    if evidence.kind == "table":
        parts = tuple(
            str(part).strip()
            for part in (evidence.catalog, evidence.database, evidence.schema_name, evidence.table)
            if str(part or "").strip()
        )
        if parts:
            return parts
        return tuple(part.strip() for part in evidence.value.split(".") if part.strip())
    return ()


def reference_qualification_level(evidence: InputEvidence) -> QualificationLevel:
    part_count = len(reference_identifier_parts(evidence))
    if part_count >= 3:
        return "fully_qualified"
    if part_count >= 2:
        return "schema_table"
    return "table"


def reference_addressing_mode(
    evidence: InputEvidence,
    context: dict[str, Any],
) -> AddressingMode:
    reference_type = reference_type_from_evidence(evidence.kind)
    if reference_type in {"path_reference", "api_endpoint_reference"}:
        return "physical_exact"
    if reference_type != "table_reference":
        return "weak_logical"

    connection_type = normalize_optional(context.get("connection_type"), lower=True)
    database_type = normalize_optional(context.get("database_type"), lower=True)
    if connection_type == "database" or database_type:
        return "connection_bound"
    if reference_qualification_level(evidence) != "table":
        return "qualified_logical"
    return "weak_logical"


def normalize_reference_type(value: Any) -> str:
    normalized = normalize_optional(value, lower=True)
    if normalized == "dynamic_expression":
        normalized = "unknown"
    if normalized not in REFERENCE_TYPES:
        raise ValueError(f"Unsupported reference type: {value}")
    return normalized


def normalize_reference_value(reference_type: str, value: str) -> str:
    text = require_text(value, "reference_value")
    if reference_type == "path_reference":
        return normalize_physical_path(text)
    if reference_type == "table_reference":
        return ".".join(part.strip() for part in text.lower().split(".") if part.strip())
    if reference_type == "api_endpoint_reference":
        return _normalize_api_reference_input(text, {})
    return " ".join(text.lower().split())


def require_text(value: Any, field_name: str) -> str:
    text = normalize_optional(value, lower=False)
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def normalize_optional(value: Any, *, lower: bool) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text.lower() if lower else text


def _normalize_api_reference_input(value: str, details: dict[str, Any]) -> str:
    method = str(details.get("method") or "GET").strip().upper() or "GET"
    text = require_text(value, "reference_value")
    parts = text.split(None, 1)
    if len(parts) == 2 and parts[0].isalpha():
        method = parts[0].upper()
        text = parts[1]
    return f"{method} {normalize_api_url(text)}"


def _api_reference_url(value: str) -> str:
    parts = value.split(None, 1)
    return parts[1] if len(parts) == 2 and parts[0].isalpha() else value


def _api_reference_scope(value: str) -> str | None:
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
