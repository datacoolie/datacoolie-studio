from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from datacoolie_studio.domains.assets.reference_resolution import ReferenceResolution


AssetKind = Literal["table", "path", "sql_query", "python_function", "api", "unresolved"]
DeclarationStatus = Literal["declared", "discovered_only"]
ReferenceType = Literal["table_reference", "path_reference", "api_endpoint_reference", "unknown"]
DependencyKind = Literal["reads", "uses"]
Provenance = Literal["sql", "python", "python_sql"]


@dataclass(slots=True)
class LineageReference:
    id: str
    reference_type: ReferenceType
    display_name: str
    normalized_value: str
    resolution: ReferenceResolution
    entity_type: Literal["reference"] = field(default="reference", init=False)
    resolved_asset_id: str | None = None
    resolved_asset_ids: list[str] = field(default_factory=list)
    candidate_asset_ids: list[str] = field(default_factory=list)
    occurrence_ids: list[str] = field(default_factory=list)
    consumer_asset_ids: list[str] = field(default_factory=list)
    provenances: list[Provenance] = field(default_factory=list)
    dependency_count: int = 0
    observations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LineageReferenceOccurrence:
    id: str
    reference_id: str
    reference_type: ReferenceType
    display_name: str
    resolution: ReferenceResolution
    raw_value: str
    normalized_value: str
    context_scope: str | None
    context_scope_source: Literal["detected", "metadata_context"] | None
    source_location: dict[str, Any] | None
    provenance: Provenance
    target_asset_id: str
    consumer_asset_id: str
    resolved_asset_id: str | None = None
    candidate_asset_ids: list[str] = field(default_factory=list)
    resolution_method: str = "insufficient_identity"
    observations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LineageDataflow:
    id: str
    dataflow_id: str
    name: str
    source_asset_id: str
    destination_asset_id: str
    stage: str | None
    load_type: str | None
    metadata_source_id: int
    metadata_source_uri: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LineageDependency:
    id: str
    target_asset_id: str
    consumer_asset_id: str
    kind: DependencyKind
    provenance: Provenance
    resolution: ReferenceResolution
    resolution_method: str
    reference_id: str
    reference_occurrence_id: str
    resolved_asset_id: str | None = None
    observations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LineageDiagnostic:
    severity: Literal["info", "warning", "error"]
    code: str
    message: str
    asset_id: str | None = None
    dataflow_id: str | None = None
    metadata_source_id: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in (None, {}, [])}
