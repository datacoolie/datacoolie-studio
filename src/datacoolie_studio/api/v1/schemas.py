from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TargetIdentifierKind = Literal["logical_table", "physical_path", "api_endpoint"]
ReferenceType = Literal["table_reference", "path_reference", "api_endpoint_reference", "unknown"]
ResolutionState = Literal["automatic", "manual", "unresolved"]
ResolutionReason = Literal["no_match", "multiple_matches", "conflicting_targets", "incomplete", "target_missing"]


class ReferenceResolutionResponse(BaseModel):
    state: ResolutionState
    reason: ResolutionReason | None = None


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None

    @field_validator("name")
    @classmethod
    def validate_project_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Project name cannot be blank")
        if len(normalized) > 255:
            raise ValueError("Project name cannot exceed 255 characters")
        return normalized


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    created_at: datetime
    updated_at: datetime


class ProjectEnvironmentSummaryResponse(BaseModel):
    id: int
    name: str
    metadata_source_count: int
    etl_log_path_count: int
    code_artifact_count: int
    created_at: datetime
    updated_at: datetime


class ProjectSummaryResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    environment_count: int
    metadata_source_count: int
    etl_log_path_count: int
    reference_mapping_count: int
    environments: list[ProjectEnvironmentSummaryResponse]
    created_at: datetime
    updated_at: datetime


class ProjectReferenceMappingCreate(BaseModel):
    reference_type: ReferenceType
    reference_value: str
    target_identifier_kind: TargetIdentifierKind
    target_value: str
    target_display_value: str | None = None
    note: str | None = None

    @field_validator("reference_type", mode="before")
    @classmethod
    def normalize_reference_type(cls, value: Any) -> Any:
        if str(value or "").strip().lower() == "dynamic_expression":
            return "unknown"
        return value


class ProjectReferenceMappingUpdate(BaseModel):
    reference_type: ReferenceType | None = None
    reference_value: str | None = None
    target_identifier_kind: TargetIdentifierKind | None = None
    target_value: str | None = None
    target_display_value: str | None = None
    note: str | None = None

    @field_validator("reference_type", mode="before")
    @classmethod
    def normalize_reference_type(cls, value: Any) -> Any:
        if value is None:
            return None
        if str(value).strip().lower() == "dynamic_expression":
            return "unknown"
        return value


class ProjectReferenceMappingRead(BaseModel):
    id: int
    project_id: int
    reference_type: ReferenceType
    reference_normalized_value: str
    reference_signature: dict[str, Any]
    target_identifier_kind: TargetIdentifierKind
    target_normalized_value: str
    target_display_value: str
    note: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("reference_type", mode="before")
    @classmethod
    def normalize_reference_type(cls, value: Any) -> Any:
        if str(value or "").strip().lower() == "dynamic_expression":
            return "unknown"
        return value


class EnvironmentCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def validate_environment_name(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,49}", normalized):
            raise ValueError("Environment must start with a letter or number and contain only lowercase letters, numbers, hyphens, or underscores")
        return normalized


class EnvironmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    created_at: datetime
    updated_at: datetime


class StudioPathInfo(BaseModel):
    backend: str
    path: str
    exists: bool
    size_bytes: int | None = None
    maintenance_supported: bool = False


class StudioWorkspaceDatabaseMaintenanceRequest(BaseModel):
    confirm: Literal[True]


class StudioAnalyticsCacheInfo(BaseModel):
    scope: Literal["studio"]
    path: str
    exists: bool
    size_bytes: int | None = None
    schema_version: int | None = None
    generation: int | None = None
    build_state: Literal["ready", "rebuild_required"] = "rebuild_required"
    published_at: datetime | None = None
    dataflow_row_count: int = 0
    job_row_count: int = 0
    filter_value_count: int = 0
    cached_source_count: int = 0
    active_source_count: int = 0
    orphan_source_count: int = 0
    orphan_source_ids: list[int] = Field(default_factory=list)


class StudioDiagnosticsResponse(BaseModel):
    workspace_database: StudioPathInfo
    analytics_cache: StudioAnalyticsCacheInfo


class StudioCacheStatusResponse(BaseModel):
    result_cache: dict[str, Any]
    analytics_cache: dict[str, Any]
    sync_job_retention: dict[str, Any] | None = None


class StudioCacheClearRequest(BaseModel):
    scope: Literal["read_models", "analytics", "all_disposable"]
    environment_id: int | None = Field(default=None, ge=1)
    features: list[Literal["overview", "assets", "lineage", "monitoring"]] = Field(default_factory=list)
    confirm: Literal[True]


class StudioCacheMaintenanceRequest(BaseModel):
    confirm: Literal[True]


class StudioCacheMutationResponse(BaseModel):
    scope: str
    environment_id: int | None = None
    features: list[str] = Field(default_factory=list)
    read_models: dict[str, Any] | None = None
    analytics: dict[str, Any] | None = None
    analytics_dependent_read_models: dict[str, Any] | None = None


class StudioSettingsResponse(BaseModel):
    timezone: str
    timezone_source: Literal["configured", "server_default"]
    timezone_offset_minutes: int
    source_check_interval_seconds: int
    updated_at: datetime | None = None


class StudioSettingsUpdateRequest(BaseModel):
    timezone: str | None = None
    source_check_interval_seconds: int | None = Field(default=None, ge=5, le=3600)


class ModuleInfo(BaseModel):
    key: str
    name: str
    description: str
    group: str
    status: Literal["available", "coming_soon"]
    togglable: bool
    default_enabled: bool
    pages: list[str]
    enabled: bool


class ModuleStateUpdateRequest(BaseModel):
    enabled: bool


class SourceCreate(BaseModel):
    uri: str
    label: str | None = None
    enabled: bool = True
    source_config: dict[str, Any] | None = None


class MetadataSourceImportRequest(BaseModel):
    uri: str
    label: str | None = None
    enabled: bool = True


class DatacoolieProjectSourceImportRequest(BaseModel):
    project_uri: str
    metadata_subpath: str = "metadata"
    code_subpath: str = "functions"
    metadata_uri: str | None = None
    code_uri: str | None = None
    include_metadata: bool = True
    include_code: bool = True
    enabled: bool = True


class SourceImportItemResponse(BaseModel):
    status: Literal["created", "existing"]
    id: int
    source_kind: str
    uri: str
    label: str | None = None
    record_counts: dict[str, int] = Field(default_factory=dict)
    source_config: dict[str, Any] = Field(default_factory=dict)


class SourceImportResponse(BaseModel):
    created: list[SourceImportItemResponse]
    existing: list[SourceImportItemResponse]
    errors: list[dict[str, Any]]
    summary: dict[str, int]


class SourceUpdate(BaseModel):
    uri: str | None = None
    label: str | None = None
    enabled: bool | None = None
    sync_schedule_enabled: bool | None = None
    sync_interval_minutes: int | None = None
    source_config: dict[str, Any] | None = None

    @field_validator("sync_interval_minutes")
    @classmethod
    def validate_sync_interval_minutes(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("Sync interval must be at least 1 minute")
        return value


class SourceValidationResponse(BaseModel):
    source_id: int
    source_kind: str
    status: str
    message: str
    detected_provider: str | None = None
    detected_format: str | None = None
    record_counts: dict[str, int] = {}
    records_scanned: int = 0
    validated_at: datetime | None = None
    errors: list[dict[str, Any]] = []


class SourceSyncJobResponse(BaseModel):
    id: int
    environment_id: int
    source_id: int
    source_kind: str
    job_type: str
    status: str
    message: str | None = None
    result: dict[str, Any] | None = None
    started_at: datetime
    completed_at: datetime | None = None


class SourceSyncStatusResponse(BaseModel):
    source_id: int
    source_kind: str
    status: str
    message: str
    revision: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    checked_at: datetime | None = None
    latest_job: SourceSyncJobResponse | None = None


class LogSyncLookbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_partition: str
    to_partition: str

    @field_validator("from_partition", "to_partition")
    @classmethod
    def validate_partition_date(cls, value: str) -> str:
        normalized = value.strip()
        try:
            parsed = datetime.strptime(normalized, "%Y-%m-%d")
        except ValueError as error:
            raise ValueError("Partition bounds must use YYYY-MM-DD") from error
        return parsed.date().isoformat()

    @model_validator(mode="after")
    def validate_partition_order(self) -> LogSyncLookbackRequest:
        if self.from_partition > self.to_partition:
            raise ValueError("Lookback start partition cannot be after end partition")
        return self


class LogSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["incremental", "incremental_with_lookback"]
    lookback: LogSyncLookbackRequest | None = None

    @model_validator(mode="after")
    def validate_mode_contract(self) -> LogSyncRequest:
        if self.mode == "incremental_with_lookback" and self.lookback is None:
            raise ValueError("Lookback bounds are required for incremental with lookback")
        if self.mode == "incremental" and self.lookback is not None:
            raise ValueError("Lookback bounds are only valid for incremental with lookback")
        return self


class FreshnessSourceItemResponse(BaseModel):
    source_id: int
    source_kind: str
    label: str | None = None
    uri: str
    status: str
    source_modified_at: datetime | None = None
    cache_synced_at: datetime | None = None
    cache_source_modified_at: datetime | None = None
    revision: dict[str, Any] | None = None
    cache_revision: dict[str, Any] | None = None
    message: str


class FreshnessGroupResponse(BaseModel):
    status: str
    max_source_modified_at: datetime | None = None
    cache_synced_at: datetime | None = None
    count: int


class EnvironmentFreshnessResponse(BaseModel):
    environment_id: int
    status: str
    message: str
    max_source_modified_at: datetime | None = None
    metadata_source_count: int
    etl_log_path_count: int
    source_cache_version: str
    structural_cache_version: str
    metadata: FreshnessGroupResponse
    etl_logs: FreshnessGroupResponse
    items: list[FreshnessSourceItemResponse]


class EnvironmentContextIdentityResponse(BaseModel):
    id: int
    name: str


class EnvironmentContextEnvironmentResponse(EnvironmentContextIdentityResponse):
    project_id: int


class EnvironmentContextSourceCountsResponse(BaseModel):
    metadata: int
    logs: int
    code: int


class EnvironmentContextFreshnessResponse(BaseModel):
    status: str
    message: str
    max_source_modified_at: datetime | None = None
    metadata: FreshnessGroupResponse
    etl_logs: FreshnessGroupResponse


class EnvironmentDependencyVersionsResponse(BaseModel):
    source_registry: str
    metadata_catalog: str
    code_catalog: str
    operations: str
    reference_mappings: str


class EnvironmentContextResponse(BaseModel):
    schema_version: Literal["environment-context.v1"]
    project: EnvironmentContextIdentityResponse
    environment: EnvironmentContextEnvironmentResponse
    source_counts: EnvironmentContextSourceCountsResponse
    freshness: EnvironmentContextFreshnessResponse
    versions: EnvironmentDependencyVersionsResponse
    checked_at: datetime


class MetadataSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    environment_id: int
    uri: str
    label: str | None = None
    enabled: bool
    created_at: datetime
    source_config: dict[str, Any] | None = None
    latest_validation: SourceValidationResponse | None = None


class LogSourceRead(MetadataSourceRead):
    sync_schedule_enabled: bool = False
    sync_interval_minutes: int | None = None
    last_scheduled_sync_at: datetime | None = None


class CodeArtifactRead(MetadataSourceRead):
    pass


class SourceDeleteImpactItem(BaseModel):
    kind: str
    label: str
    count: int
    severity: str = "info"


class SourceDeleteImpactResponse(BaseModel):
    source_id: int
    source_kind: str
    source_uri: str
    mode: str = "hard_delete"
    metadata_file_deleted: bool = False
    has_impact: bool
    impacts: list[SourceDeleteImpactItem]
    summary: str


class MetadataResponse(BaseModel):
    summary: dict[str, Any]
    sources: list[dict[str, Any]]
    connections: list[dict[str, Any]]
    dataflows: list[dict[str, Any]]
    schema_hints: list[dict[str, Any]]
    errors: list[dict[str, Any]]


class OverviewSourceGroupResponse(BaseModel):
    configured: int
    enabled: int


class OverviewSourceValidationResponse(BaseModel):
    errors: int
    warnings: int


class OverviewSourcesResponse(BaseModel):
    metadata: OverviewSourceGroupResponse
    logs: OverviewSourceGroupResponse
    validation: OverviewSourceValidationResponse


class OverviewNamedCountResponse(BaseModel):
    name: str
    count: int


class OverviewMetadataResponse(BaseModel):
    connections: int
    enabled_connections: int
    dataflows: int
    enabled_dataflows: int
    schema_hints: int
    enabled_schema_hints: int
    stages: list[OverviewNamedCountResponse]
    load_types: list[OverviewNamedCountResponse]
    errors: list[dict[str, Any]]


class OverviewLineageResponse(BaseModel):
    assets: int
    references: int
    dataflows: int
    dependencies: int
    stitched_assets: int
    declared_assets: int
    automatic_references: int
    manual_references: int
    unresolved_references: int
    automatic_dependencies: int
    manual_dependencies: int
    unresolved_dependencies: int
    diagnostics: int
    error_count: int


class OverviewMonitoringResponse(BaseModel):
    job_records: int
    total_failures: int
    dataflow_success_rate: float
    failed_job_windows: dict[str, int]
    active_engines: int
    latest_log_at: str | None = None
    date_range: dict[str, str | None]
    errors: list[dict[str, Any]]


class OverviewCacheResponse(BaseModel):
    state: Literal["hit", "miss"]
    computed_at: datetime


class EnvironmentOverviewResponse(BaseModel):
    schema_version: Literal["environment-overview.v2"]
    sources: OverviewSourcesResponse
    metadata: OverviewMetadataResponse
    lineage: OverviewLineageResponse
    monitoring: OverviewMonitoringResponse
    cache: OverviewCacheResponse


class MetadataEditorDocumentResponse(BaseModel):
    source: dict[str, Any]
    sheets: dict[str, Any]
    issues: list[dict[str, Any]]


class MetadataEditorWorkspaceResponse(BaseModel):
    schema_version: Literal["metadata-editor-workspace.v1"]
    environment_id: int
    metadata_catalog_version: str
    document: MetadataEditorDocumentResponse
    draft: MetadataEditorDocumentResponse | None = None


class MetadataEditorValidationRequest(BaseModel):
    source: dict[str, Any] | None = None
    sheets: dict[str, Any]
    issues: list[dict[str, Any]] = []


class MetadataEditorValidationResponse(BaseModel):
    status: str
    summary: dict[str, Any]
    issues: list[dict[str, Any]]


class MetadataEditorSaveRequest(BaseModel):
    expected_revision: dict[str, Any]
    editor_document: MetadataEditorValidationRequest
    confirm_overwrite: bool = False


class MetadataBackupResponse(BaseModel):
    id: int
    project_id: int
    environment_id: int
    source_id: int
    source_uri: str
    backup_path: str
    source_revision: dict[str, Any] | None = None
    saved_revision: dict[str, Any] | None = None
    created_at: datetime


class MetadataBackupRestoreRequest(BaseModel):
    expected_revision: dict[str, Any]
    confirm_restore: bool = False


class LineageSummaryResponse(BaseModel):
    assets: int
    references: int
    dataflows: int
    dependencies: int
    stitched_assets: int
    declared_assets: int
    automatic_references: int
    manual_references: int
    unresolved_references: int
    automatic_dependencies: int
    manual_dependencies: int
    unresolved_dependencies: int
    diagnostics: int


class LineageAssetResponse(BaseModel):
    id: str
    entity_type: Literal["asset"]
    asset_type: Literal["table", "path", "sql_query", "python_function", "api", "unresolved"]
    declaration_status: Literal["declared", "discovered_only"]
    display_name: str
    label: str
    display_label: str | None = None
    endpoint_locator: str | None = None
    endpoint_kind: str | None = None
    identity_type: str | None = None
    connection_name: str | None = None
    connection_type: str | None = None
    format: str | None = None
    catalog: str | None = None
    database: str | None = None
    schema_name: str | None = None
    table: str | None = None
    path: str | None = None
    python_function: str | None = None
    metadata_source_ids: list[int] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    connection_names: list[str] = Field(default_factory=list)
    mapping_target: dict[str, str] | None = None


class LineageReferenceResponse(BaseModel):
    id: str
    entity_type: Literal["reference"]
    reference_type: ReferenceType
    display_name: str
    normalized_value: str
    resolution: ReferenceResolutionResponse
    resolved_asset_id: str | None = None
    resolved_asset_ids: list[str] = Field(default_factory=list)
    candidate_asset_ids: list[str] = Field(default_factory=list)
    occurrence_count: int = 0
    consumer_asset_ids: list[str] = Field(default_factory=list)
    provenances: list[Literal["sql", "python", "python_sql"]] = Field(default_factory=list)
    dependency_count: int = 0
    manual_mapping: dict[str, Any] | None = None

    @field_validator("reference_type", mode="before")
    @classmethod
    def normalize_reference_type(cls, value: Any) -> Any:
        if str(value or "").strip().lower() == "dynamic_expression":
            return "unknown"
        return value


class SourceLocationResponse(BaseModel):
    module: str | None = None
    path: str | None = None
    function_path: str | None = None
    line: int | None = None
    column: int | None = None
    end_line: int | None = None
    end_column: int | None = None
    coordinate_space: Literal["query_source", "function_source"] | None = None


class LineageDataflowResponse(BaseModel):
    id: str
    dataflow_id: str
    name: str
    source_asset_id: str
    destination_asset_id: str
    stage: str | None = None
    load_type: str | None = None
    metadata_source_id: int
    metadata_source_uri: str


class LineageDependencyResponse(BaseModel):
    id: str
    target_asset_id: str
    consumer_asset_id: str
    kind: Literal["reads", "uses"]
    provenance: Literal["sql", "python", "python_sql"]
    resolution: ReferenceResolutionResponse
    resolution_method: str
    reference_id: str
    reference_occurrence_id: str
    resolved_asset_id: str | None = None


class LineageResponse(BaseModel):
    schema_version: Literal["lineage.v4"]
    summary: LineageSummaryResponse
    assets: list[LineageAssetResponse]
    references: list[LineageReferenceResponse]
    dataflows: list[LineageDataflowResponse]
    dependencies: list[LineageDependencyResponse]


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


class MonitoringPageResponse(BaseModel):
    schema_version: Literal["monitoring-page.v9"]
    page: Literal["environment-overview", "overview", "jobs", "dataflows", "failures", "diagnostics", "performance", "volume", "maintenance", "freshness"]
    summary: dict[str, Any]
    health: dict[str, Any] | None = None
    attention: list[dict[str, Any]] | None = None
    coverage: dict[str, Any] | None = None
    reconciliation: dict[str, Any] | None = None
    operations: dict[str, Any] | None = None
    failures: dict[str, Any] | None = None
    performance: dict[str, Any] | None = None
    volume: dict[str, Any] | None = None
    maintenance: dict[str, Any] | None = None
    diagnostics: dict[str, Any] | None = None
    freshness: dict[str, Any] | None = None
    errors: list[dict[str, Any]] | None = None
