from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from datacoolie_studio.api.v1.contracts.lineage import SourceLocationResponse
from datacoolie_studio.api.v1.contracts.shared import ReferenceResolutionResponse, ReferenceType, TargetIdentifierKind
from datacoolie_studio.api.v1.contracts.workspace import ProjectReferenceMappingRead

class AssetSummaryResponse(BaseModel):
    assets: int
    references: int = 0
    manual_mappings: int = 0
    visible: int = 0
    asset_attention: int = 0
    with_attention: int = 0
    automatic_references: int = 0
    manual_references: int = 0
    unresolved_references: int = 0


class AssetMetadataSourceResponse(BaseModel):
    id: int
    uri: str


class AssetAttentionResponse(BaseModel):
    severity: Literal["info", "warning", "error"]
    code: str
    message: str
    source_type: str
    subject_type: str = "asset"
    dataflow_id: str | None = None
    metadata_source_id: int | None = None
    reference_id: str | None = None
    reference_occurrence_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AssetResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    display_name: str
    friendly_name: str
    full_identity: str
    asset_type: Literal["table", "path", "sql_query", "python_function", "api", "unresolved"]
    format: str | None = None
    connection_name: str | None = None
    connection_type: str | None = None
    catalog: str | None = None
    database: str | None = None
    schema_name: str | None = None
    table: str | None = None
    path: str | None = None
    query: str | None = None
    python_function: str | None = None
    roles: list[str] = Field(default_factory=list)
    metadata_source_ids: list[int] = Field(default_factory=list)
    metadata_sources: list[AssetMetadataSourceResponse] = Field(default_factory=list)
    upstream_count: int
    downstream_count: int
    input_dataflow_count: int
    output_dataflow_count: int
    depends_on_count: int
    used_by_count: int = 0
    attention_count: int
    attention_items: list[AssetAttentionResponse] = Field(default_factory=list)
    identifiers: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)


class AssetFilterOptionsResponse(BaseModel):
    connections: list[str] = Field(default_factory=list)
    formats: list[str] = Field(default_factory=list)
    asset_types: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    attention_states: list[str] = Field(default_factory=list)


class AssetReferenceGroupResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    reference_type: ReferenceType
    normalized_value: str
    display_name: str
    resolution: ReferenceResolutionResponse
    resolved_asset_id: str | None = None
    resolved_asset_ids: list[str] = Field(default_factory=list)
    resolved_asset: dict[str, Any] | None = None
    candidate_asset_ids: list[str] = Field(default_factory=list)
    candidate_assets: list[dict[str, Any]] = Field(default_factory=list)
    occurrence_ids: list[str] = Field(default_factory=list)
    consumer_asset_ids: list[str] = Field(default_factory=list)
    consumer_assets: list[dict[str, Any]] = Field(default_factory=list)
    provenances: list[str] = Field(default_factory=list)
    resolution_methods: list[str] = Field(default_factory=list)
    occurrence_count: int | None = None
    dependency_count: int = 0
    dataflow_ids: list[str] = Field(default_factory=list)
    attention_count: int = 0
    attention_items: list[AssetAttentionResponse] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    manual_mapping: dict[str, Any] | None = None


class AssetReferenceOccurrenceResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    reference_id: str
    reference_type: ReferenceType
    raw_value: str
    normalized_value: str
    context_scope: str | None = None
    context_scope_source: Literal["detected", "metadata_context"] | None = None
    addressing_mode: str | None = None
    qualification_level: str | None = None
    source_location: SourceLocationResponse | None = None
    display_name: str
    provenance: Literal["sql", "python", "python_sql"] | None = None
    consumer_asset_id: str | None = None
    consumer_asset: dict[str, Any] | None = None
    connection_name: str | None = None
    resolution: ReferenceResolutionResponse
    resolution_method: str | None = None
    resolved_asset_id: str | None = None
    resolved_asset: dict[str, Any] | None = None
    candidate_asset_ids: list[str] = Field(default_factory=list)
    candidate_assets: list[dict[str, Any]] = Field(default_factory=list)
    dependency_count: int = 0
    dataflow_ids: list[str] = Field(default_factory=list)
    attention_count: int = 0
    attention_items: list[AssetAttentionResponse] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    manual_mapping: dict[str, Any] | None = None


class AssetListItemResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    display_name: str
    friendly_name: str
    full_identity: str
    asset_type: Literal["table", "path", "sql_query", "python_function", "api", "unresolved"]
    format: str | None = None
    connection_name: str | None = None
    connection_type: str | None = None
    catalog: str | None = None
    database: str | None = None
    schema_name: str | None = None
    table: str | None = None
    path: str | None = None
    query: str | None = None
    python_function: str | None = None
    roles: list[str] = Field(default_factory=list)
    metadata_source_ids: list[int] = Field(default_factory=list)
    upstream_count: int
    downstream_count: int
    input_dataflow_count: int
    output_dataflow_count: int
    depends_on_count: int
    used_by_count: int = 0
    attention_count: int
    identifier_count: int
    observation_count: int
    metadata_source_count: int
    mapping_target: dict[str, str] | None = None


class AssetInventoryResponse(BaseModel):
    summary: AssetSummaryResponse
    items: list[AssetListItemResponse]
    filter_options: AssetFilterOptionsResponse
    catalog_version: str


class AssetReferenceListResponse(BaseModel):
    items: list[AssetReferenceGroupResponse]
    filter_options: dict[str, list[str]] = Field(default_factory=dict)
    catalog_version: str


class ProjectReferenceRegistryTargetResponse(BaseModel):
    id: str
    asset_id: str
    asset_ids: list[str] = Field(default_factory=list)
    environment_ids: list[int] = Field(default_factory=list)
    environment_names: list[str] = Field(default_factory=list)
    asset_type: Literal["table", "path", "sql_query", "python_function", "api", "unresolved"]
    format: str | None = None
    connection_name: str
    display_name: str
    context: str | None = None
    kind: TargetIdentifierKind
    value: str
    display: str


class ProjectReferenceRegistryEnvironmentResponse(BaseModel):
    environment_id: int
    environment_name: str
    resolution: ReferenceResolutionResponse
    resolved_asset_id: str | None = None
    resolved_asset_ids: list[str] = Field(default_factory=list)
    observed_target_ids: list[str] = Field(default_factory=list)
    candidate_asset_ids: list[str] = Field(default_factory=list)
    manual_mapping_id: int | None = None
    manual_mapping_status: str | None = None
    occurrence_count: int = 0
    consumer_count: int = 0


class ProjectReferenceRegistryObservedTargetResponse(BaseModel):
    target: ProjectReferenceRegistryTargetResponse
    environment_ids: list[int] = Field(default_factory=list)
    environment_names: list[str] = Field(default_factory=list)


class ProjectReferenceRegistryTargetCoverageResponse(BaseModel):
    available_environment_names: list[str] = Field(default_factory=list)
    missing_environment_names: list[str] = Field(default_factory=list)
    available: int
    total: int


class ProjectReferenceRegistryRowResponse(BaseModel):
    id: str
    reference_type: ReferenceType
    normalized_value: str
    mapping: ProjectReferenceMappingRead | None = None
    resolution: ReferenceResolutionResponse
    environments: list[ProjectReferenceRegistryEnvironmentResponse] = Field(default_factory=list)
    candidate_asset_ids: list[str] = Field(default_factory=list)
    resolved_asset_ids: list[str] = Field(default_factory=list)
    target: ProjectReferenceRegistryTargetResponse | None = None
    observed_targets: list[ProjectReferenceRegistryObservedTargetResponse] = Field(default_factory=list)
    target_coverage: ProjectReferenceRegistryTargetCoverageResponse


class ProjectReferenceRegistryFailureResponse(BaseModel):
    environment_id: int
    environment_name: str
    message: str


class ProjectReferenceRegistryResponse(BaseModel):
    project_id: int
    mappings: list[ProjectReferenceMappingRead]
    rows: list[ProjectReferenceRegistryRowResponse]
    targets: list[ProjectReferenceRegistryTargetResponse]
    failures: list[ProjectReferenceRegistryFailureResponse]


class AssetReferenceDetailResponse(BaseModel):
    reference: AssetReferenceGroupResponse
    occurrences: list[AssetReferenceOccurrenceResponse] = Field(default_factory=list)
    catalog_version: str


class AssetSourceResponse(BaseModel):
    definition: "AssetDefinitionResponse"
    catalog_version: str


class AssetDefinitionDiagnosticResponse(BaseModel):
    severity: Literal["info", "warning", "error"]
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class AssetDefinitionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: Literal["sql_query", "python_function", "api", "path", "unresolved"]
    language: str | None = None
    status: Literal["available", "unavailable", "ambiguous", "empty"]
    title: str | None = None
    raw: str | None = None
    formatted: str | None = None
    source: str | None = None
    function_path: str | None = None
    module_name: str | None = None
    relative_path: str | None = None
    line_count: int = 0
    diagnostics: list[AssetDefinitionDiagnosticResponse] = Field(default_factory=list)


class AssetDetailResponse(BaseModel):
    asset: AssetResponse
    definition: AssetDefinitionResponse | None = None
    attention_items: list[AssetAttentionResponse] = Field(default_factory=list)
    direct_relationships: dict[str, Any] = Field(default_factory=dict)
    upstream_assets: list[dict[str, Any]] = Field(default_factory=list)
    downstream_assets: list[dict[str, Any]] = Field(default_factory=list)
    input_flows: list[dict[str, Any]] = Field(default_factory=list)
    output_flows: list[dict[str, Any]] = Field(default_factory=list)
    depends_on: list[dict[str, Any]] = Field(default_factory=list)
    used_by: list[dict[str, Any]] = Field(default_factory=list)


class ReferenceSourceMatchResponse(BaseModel):
    line: int
    column: int
    end_line: int
    end_column: int
    precision: Literal["exact_reference", "detection_expression", "location_only"]


class ReferenceSourceViewResponse(BaseModel):
    id: Literal["query_source", "consumer_source", "evaluated_sql"]
    label: str
    language: Literal["sql", "python"]
    content: str
    path: str | None = None
    function_path: str | None = None
    module_name: str | None = None
    matches: list[ReferenceSourceMatchResponse] = Field(default_factory=list)


class ReferenceSourceDiagnosticResponse(BaseModel):
    severity: Literal["info", "warning", "error"]
    code: str
    message: str


class ReferenceOccurrenceSourceResponse(BaseModel):
    occurrence_id: str
    consumer_asset_id: str
    views: list[ReferenceSourceViewResponse] = Field(default_factory=list)
    diagnostics: list[ReferenceSourceDiagnosticResponse] = Field(default_factory=list)
