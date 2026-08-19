from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from datacoolie_studio.domains.analysis.models import InputEvidence
from datacoolie_studio.domains.assets.identifiers import (
    normalize_physical_path,
    storage_authority,
)
from datacoolie_studio.domains.assets.reference_mappings import match_reference_mapping
from datacoolie_studio.domains.assets.reference_identity import (
    ReferenceSignature,
    reference_addressing_mode,
    reference_identifier_parts,
    reference_qualification_level,
    build_reference_context_scope,
    build_reference_signature,
)
from datacoolie_studio.domains.assets.reference_resolution import (
    AUTOMATIC_RESOLUTION,
    MANUAL_RESOLUTION,
    ReferenceResolution,
    unresolved_resolution,
)
from datacoolie_studio.domains.assets.registry import AssetRegistry


@dataclass(slots=True)
class Resolution:
    resolution: ReferenceResolution
    asset_id: str | None
    method: str
    candidates: list[str]
    evidence: InputEvidence
    reference_signature: ReferenceSignature
    observations: list[dict[str, Any]] = field(default_factory=list)
    context_scope: str | None = None
    context_scope_source: str | None = None
    addressing_mode: str | None = None
    qualification_level: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolution": self.resolution.to_dict(),
            "asset_id": self.asset_id,
            "method": self.method,
            "candidates": self.candidates,
            "evidence": self.evidence.to_dict(),
            "reference_signature": self.reference_signature.to_dict(),
            "observations": self.observations,
            "context_scope": self.context_scope,
            "context_scope_source": self.context_scope_source,
            "addressing_mode": self.addressing_mode,
            "qualification_level": self.qualification_level,
        }


class AssetResolver:
    def __init__(self, registry: AssetRegistry, reference_mappings: list[dict[str, Any]] | None = None):
        self.registry = registry
        self.reference_mappings = list(reference_mappings or [])

    def resolve(self, evidence: InputEvidence, context: dict[str, Any]) -> Resolution:
        signature = build_reference_signature(evidence)
        context_scope = build_reference_context_scope(evidence, context)
        addressing_mode = reference_addressing_mode(evidence, context)
        qualification_level = reference_qualification_level(evidence)

        def result(
            resolution: ReferenceResolution,
            asset_id: str | None,
            method: str,
            candidates: list[str],
        ) -> Resolution:
            return Resolution(
                resolution,
                asset_id,
                method,
                candidates,
                evidence,
                signature,
                context_scope=context_scope.value if context_scope else None,
                context_scope_source=context_scope.source if context_scope else None,
                addressing_mode=addressing_mode,
                qualification_level=qualification_level,
            )

        automatic = self._resolve_auto(evidence, context, result)
        mapping_match = match_reference_mapping(signature, self.reference_mappings)
        if mapping_match.status == "matched":
            return self._apply_mapping(automatic, mapping_match.mapping or {})
        if mapping_match.status == "ambiguous":
            automatic.method = "manual_mapping_ambiguous"
            automatic.observations.append({
                "source_type": "manual_mapping",
                "mapping_status": "ambiguous",
                "candidate_mapping_ids": mapping_match.candidate_mapping_ids,
                "reference_signature": signature.to_dict(),
            })
        self._record_automatic_observation(automatic)
        return automatic

    def _record_automatic_observation(self, resolution: Resolution) -> None:
        if not resolution.asset_id:
            return
        self.registry.add_observation(resolution.asset_id, {
            "source_type": resolution.evidence.provenance,
            "resolution": AUTOMATIC_RESOLUTION.to_dict(),
            "resolution_method": resolution.method,
            "evidence": resolution.evidence.to_dict(),
        })

    def _resolve_auto(self, evidence: InputEvidence, context: dict[str, Any], result) -> Resolution:
        context_scope = build_reference_context_scope(evidence, context)
        addressing_mode = reference_addressing_mode(evidence, context)
        qualification_level = reference_qualification_level(evidence)

        identifier = _evidence_identifier(evidence, context)
        if identifier is not None:
            asset_id = self.registry.resolve_identifier(identifier["kind"], identifier["normalized_value"])
            if asset_id:
                return result(AUTOMATIC_RESOLUTION, asset_id, "exact_identifier", [asset_id])

        if evidence.kind == "table" and evidence.table:
            suffix = _logical_suffix(evidence)
            exact = qualification_level == "fully_qualified"
            candidates = self.registry.find_logical_table_suffix(suffix, exact=exact)
            scoped_candidates = self.registry.find_logical_table_suffix(
                suffix,
                resolution_scope=context_scope.value if context_scope else None,
                exact=exact,
            )

            if len(scoped_candidates) == 1:
                return result(AUTOMATIC_RESOLUTION, scoped_candidates[0], "connection_scoped_table", scoped_candidates)
            if len(scoped_candidates) > 1:
                return result(unresolved_resolution("multiple_matches"), None, "multiple_connection_table_matches", scoped_candidates)

            if (addressing_mode == "qualified_logical" or exact) and len(candidates) == 1:
                return result(AUTOMATIC_RESOLUTION, candidates[0], "qualified_cross_connection", candidates)
            if len(candidates) > 1:
                if context_scope and addressing_mode in {"connection_bound", "weak_logical"}:
                    return result(unresolved_resolution("out_of_scope"), None, "out_of_scope_table_candidates", candidates)
                return result(unresolved_resolution("multiple_matches"), None, "multiple_table_suffix_matches", candidates)
            if candidates:
                return result(unresolved_resolution("out_of_scope"), None, "out_of_scope_table_candidate", candidates)

        return result(unresolved_resolution("no_match"), None, "no_declared_asset_match", [])

    def _apply_mapping(self, resolution: Resolution, mapping: dict[str, Any]) -> Resolution:
        signature = resolution.reference_signature
        target_asset_id = self.registry.resolve_identifier(
            str(mapping.get("target_identifier_kind") or ""),
            str(mapping.get("target_normalized_value") or ""),
        )
        if target_asset_id:
            mapping_observation = {
                "source_type": "manual_mapping",
                "resolution": MANUAL_RESOLUTION.to_dict(),
                "resolution_method": "manual_mapping",
                "mapping_status": "applied",
                "mapping_id": mapping.get("id"),
                "mapping_note": mapping.get("note"),
                "reference_signature": signature.to_dict(),
                "target_identifier_kind": mapping.get("target_identifier_kind"),
                "target_normalized_value": mapping.get("target_normalized_value"),
                "automatic_suggestion": {
                    "resolution": resolution.resolution.to_dict(),
                    "asset_id": resolution.asset_id,
                    "method": resolution.method,
                    "candidates": resolution.candidates,
                },
            }
            self.registry.add_observation(target_asset_id, mapping_observation)
            return Resolution(
                MANUAL_RESOLUTION,
                target_asset_id,
                "manual_mapping",
                [target_asset_id],
                resolution.evidence,
                resolution.reference_signature,
                observations=[mapping_observation],
                context_scope=resolution.context_scope,
                context_scope_source=resolution.context_scope_source,
                addressing_mode=resolution.addressing_mode,
                qualification_level=resolution.qualification_level,
            )
        return Resolution(
            unresolved_resolution("target_missing"),
            None,
            "manual_target_missing",
            resolution.candidates,
            resolution.evidence,
            resolution.reference_signature,
            observations=[
                {
                    "source_type": "manual_mapping",
                    "mapping_status": "target_missing",
                    "mapping_id": mapping.get("id"),
                    "mapping_note": mapping.get("note"),
                    "reference_signature": signature.to_dict(),
                    "target_identifier_kind": mapping.get("target_identifier_kind"),
                    "target_normalized_value": mapping.get("target_normalized_value"),
                }
            ],
            context_scope=resolution.context_scope,
            context_scope_source=resolution.context_scope_source,
            addressing_mode=resolution.addressing_mode,
            qualification_level=resolution.qualification_level,
        )


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
    return None


def _logical_suffix(evidence: InputEvidence) -> str:
    parts = reference_identifier_parts(evidence)
    return ".".join(parts) or str(evidence.table or evidence.value).strip()
