from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from datacoolie_studio.domains.analysis.models import InputEvidence
from datacoolie_studio.domains.assets.identifiers import (
    build_asset_identifiers,
    connection_instance,
    normalize_physical_path,
    storage_authority,
)
from datacoolie_studio.domains.assets.registry import AssetRegistry


@dataclass(slots=True)
class Resolution:
    status: str
    asset_id: str | None
    method: str
    candidates: list[str]
    evidence: InputEvidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "asset_id": self.asset_id,
            "method": self.method,
            "candidates": self.candidates,
            "evidence": self.evidence.to_dict(),
        }


class AssetResolver:
    def __init__(self, registry: AssetRegistry):
        self.registry = registry

    def resolve(self, evidence: InputEvidence, context: dict[str, Any]) -> Resolution:
        identifier = _evidence_identifier(evidence, context)
        observation = {
            "source_type": evidence.provenance,
            "resolution_status": "resolved",
            "resolution_method": "exact_identifier",
            "evidence": evidence.to_dict(),
        }
        if identifier is not None:
            asset_id = self.registry.resolve_identifier(identifier["kind"], identifier["normalized_value"])
            if asset_id:
                self.registry.add_observation(asset_id, observation)
                return Resolution("resolved", asset_id, "exact_identifier", [asset_id], evidence)

        if evidence.kind == "table" and evidence.table:
            namespace = connection_instance(context)
            suffix = ".".join(
                part
                for part in (evidence.catalog, evidence.database, evidence.schema_name, evidence.table)
                if part
            )
            candidates = self.registry.find_logical_table_suffix(suffix or evidence.table, namespace or None)
            if len(candidates) == 1:
                observation["resolution_method"] = "unique_scoped_table"
                self.registry.add_observation(candidates[0], observation)
                return Resolution("resolved", candidates[0], "unique_scoped_table", candidates, evidence)
            if len(candidates) > 1:
                return Resolution("ambiguous", None, "multiple_scoped_tables", candidates, evidence)

        if identifier is None:
            return Resolution("unresolved", None, "insufficient_identity", [], evidence)
        node = _discovered_node(evidence, identifier, context)
        observation.update({
            "resolution_status": "discovered_only",
            "resolution_method": "new_exact_identifier",
        })
        asset_id = self.registry.add_discovered_asset(node, identifier, observation)
        return Resolution("discovered_only", asset_id, "new_exact_identifier", [asset_id], evidence)


def _evidence_identifier(evidence: InputEvidence, context: dict[str, Any]) -> dict[str, str] | None:
    if evidence.kind == "path":
        normalized = normalize_physical_path(evidence.value)
        return {
            "kind": "physical_path",
            "normalized_value": normalized,
            "display_value": evidence.value,
            "namespace": storage_authority(normalized),
            "source": evidence.provenance,
        }
    if evidence.kind != "table" or not evidence.table:
        return None
    endpoint = {
        **context,
        "catalog": evidence.catalog or context.get("catalog"),
        "database": evidence.database or context.get("database"),
        "schema_name": evidence.schema_name or context.get("schema_name"),
        "table": evidence.table,
    }
    return next(
        (item for item in build_asset_identifiers(endpoint) if item["kind"] == "logical_table"),
        None,
    )


def _discovered_node(
    evidence: InputEvidence,
    identifier: dict[str, str],
    context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "label": evidence.table or evidence.value,
        "display_label": evidence.value,
        "endpoint_locator": evidence.value,
        "endpoint_kind": "table" if evidence.kind == "table" else "file",
        "identity_type": identifier["kind"],
        "identifiers": [identifier],
        "connection_name": context.get("connection_name"),
        "connection_type": context.get("connection_type"),
        "format": context.get("format"),
        "catalog": evidence.catalog or context.get("catalog"),
        "database": evidence.database or context.get("database"),
        "schema_name": evidence.schema_name or context.get("schema_name"),
        "table": evidence.table,
        "path": evidence.value if evidence.kind == "path" else None,
    }
