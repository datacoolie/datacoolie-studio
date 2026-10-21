"""Diagnostic-to-attention policy for the Assets read model."""
from __future__ import annotations

from typing import Any

from datacoolie_studio.domains.assets.models import AssetAttention
from datacoolie_studio.domains.assets.normalization import (
    resolution_reason,
    resolution_state,
    string_or_none,
    to_int,
)


def append_asset_attention(
    attention_by_asset: dict[str, list[AssetAttention]],
    attention_keys_by_asset: dict[str, set[tuple[str, str, str | None, str | None, str | None]]],
    asset_id: str,
    attention: AssetAttention,
) -> None:
    key = (attention.code, attention.message, attention.dataflow_id, attention.reference_id, attention.reference_occurrence_id)
    if key in attention_keys_by_asset[asset_id]:
        return
    attention_keys_by_asset[asset_id].add(key)
    attention_by_asset[asset_id].append(attention)

def attention_from_diagnostic(diagnostic: dict[str, Any]) -> AssetAttention:
    details = diagnostic.get("details")
    subject_type = "asset" if diagnostic.get("asset_id") else "dataflow" if diagnostic.get("dataflow_id") else "graph"
    return AssetAttention(
        severity=severity(str(diagnostic.get("severity") or "warning")),
        code=str(diagnostic.get("code") or "lineage_diagnostic"),
        message=str(diagnostic.get("message") or "Lineage diagnostic"),
        source_type="lineage_diagnostic",
        subject_type=subject_type,
        dataflow_id=string_or_none(diagnostic.get("dataflow_id")),
        metadata_source_id=to_int(diagnostic.get("metadata_source_id")),
        reference_id=string_or_none((details or {}).get("reference_id") if isinstance(details, dict) else None),
        reference_occurrence_id=string_or_none((details or {}).get("reference_occurrence_id") if isinstance(details, dict) else None),
        details=dict(details) if isinstance(details, dict) else {},
    )

def is_reference_resolution_diagnostic(diagnostic: dict[str, Any]) -> bool:
    code = str(diagnostic.get("code") or "")
    details = diagnostic.get("details")
    has_reference_identity = isinstance(details, dict) and (
        bool(string_or_none(details.get("reference_id")))
        or bool(string_or_none(details.get("reference_occurrence_id")))
    )
    return has_reference_identity and code.startswith(("dependency_", "reference_"))

def build_reference_attention(reference: dict[str, Any]) -> AssetAttention | None:
    reference_resolution_state = resolution_state(reference)
    if reference_resolution_state != "unresolved":
        return None
    reference_resolution_reason = resolution_reason(reference)
    reference_occurrence_id = string_or_none(reference.get("id"))
    reference_id = string_or_none(reference.get("reference_id"))
    reference_message = str(reference.get("raw_value") or reference.get("display_name") or reference_occurrence_id or "reference")
    return AssetAttention(
        severity="warning" if reference_resolution_reason in {"multiple_matches", "conflicting_targets", "target_missing"} else "info",
        code=f"reference_{reference_resolution_reason or 'unresolved'}",
        message=f"{(reference_resolution_reason or 'unresolved').replace('_', ' ')} reference: {reference_message}",
        source_type=reference_attention_source_type(reference),
        subject_type="reference",
        reference_id=reference_id,
        reference_occurrence_id=reference_occurrence_id,
        details={"candidate_asset_ids": list(reference.get("candidate_asset_ids") or [])},
    )

def reference_attention_source_type(reference: dict[str, Any]) -> str:
    provenance = str(reference.get("provenance") or "").strip().lower()
    if provenance == "python_sql":
        return "python_sql_reference"
    if provenance == "python":
        return "python_reference"
    if provenance == "sql":
        return "sql_reference"
    return "reference"

def severity(value: str) -> str:
    return value if value in {"info", "warning", "error"} else "warning"

def severity_rank(value: str) -> int:
    return {"error": 0, "warning": 1, "info": 2}.get(value, 3)
__all__ = [
    "append_asset_attention",
    "attention_from_diagnostic",
    "build_reference_attention",
    "is_reference_resolution_diagnostic",
    "reference_attention_source_type",
    "severity",
    "severity_rank",
]
