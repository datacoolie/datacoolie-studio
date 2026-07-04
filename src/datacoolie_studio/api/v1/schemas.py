from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    created_at: datetime
    updated_at: datetime


class ProjectSummaryResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    environment_count: int
    metadata_source_count: int
    etl_log_path_count: int
    environments: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime


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
    path: str
    exists: bool
    size_bytes: int | None = None


class StudioAnalyticsCacheInfo(BaseModel):
    scope: Literal["studio"]
    path: str
    exists: bool
    size_bytes: int | None = None
    dataflow_row_count: int = 0
    job_row_count: int = 0
    filter_value_count: int = 0
    cached_source_count: int = 0
    active_source_count: int = 0
    orphan_source_count: int = 0
    orphan_source_ids: list[int] = Field(default_factory=list)


class StudioStorageInfo(BaseModel):
    workspace_database: StudioPathInfo
    analytics_cache: StudioAnalyticsCacheInfo


class StudioSettingsResponse(BaseModel):
    timezone: str
    timezone_source: Literal["configured", "server_default"]
    updated_at: datetime | None = None
    storage: StudioStorageInfo


class StudioSettingsUpdateRequest(BaseModel):
    timezone: str | None = None


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
    metadata: FreshnessGroupResponse
    etl_logs: FreshnessGroupResponse
    items: list[FreshnessSourceItemResponse]


class MetadataSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    environment_id: int
    uri: str
    label: str | None = None
    enabled: bool
    sync_schedule_enabled: bool = False
    sync_interval_minutes: int | None = None
    last_scheduled_sync_at: datetime | None = None
    created_at: datetime
    source_config: dict[str, Any] | None = None
    latest_validation: SourceValidationResponse | None = None


class LogSourceRead(MetadataSourceRead):
    pass


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


class MetadataEditorDocumentResponse(BaseModel):
    source: dict[str, Any]
    sheets: dict[str, Any]
    issues: list[dict[str, Any]]


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
    discovered_only_assets: int
    resolved_dependencies: int
    discovered_only_dependencies: int
    ambiguous_dependencies: int
    unresolved_dependencies: int
    diagnostics: int


class LineageAssetResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    kind: Literal["table", "path", "sql_query", "python_function", "api", "unresolved"]
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
    query: str | None = None
    python_function: str | None = None
    metadata_source_ids: list[int] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    connection_names: list[str] = Field(default_factory=list)
    identifiers: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)


class LineageReferenceResponse(BaseModel):
    id: str
    kind: Literal["table_reference", "path_reference", "dynamic_expression", "unknown"]
    display_name: str
    resolution_status: Literal["ambiguous", "unresolved"]
    raw_value: str
    provenance: Literal["sql", "python", "python_sql"]
    target_asset_id: str
    candidate_asset_ids: list[str] = Field(default_factory=list)
    reason_code: str
    observations: list[dict[str, Any]] = Field(default_factory=list)


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


class LineageDependencySourceResponse(BaseModel):
    entity_type: Literal["asset", "reference"]
    id: str


class LineageDependencyResponse(BaseModel):
    id: str
    source: LineageDependencySourceResponse
    target_asset_id: str
    kind: Literal["reads", "uses"]
    provenance: Literal["sql", "python", "python_sql"]
    resolution_status: Literal["resolved", "discovered_only", "ambiguous", "unresolved"]
    resolution_method: str
    observations: list[dict[str, Any]] = Field(default_factory=list)


class LineageDiagnosticResponse(BaseModel):
    severity: Literal["info", "warning", "error"]
    code: str
    message: str
    asset_id: str | None = None
    dataflow_id: str | None = None
    metadata_source_id: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class LineageResponse(BaseModel):
    schema_version: Literal["lineage.v2"]
    summary: LineageSummaryResponse
    assets: list[LineageAssetResponse]
    references: list[LineageReferenceResponse]
    dataflows: list[LineageDataflowResponse]
    dependencies: list[LineageDependencyResponse]
    diagnostics: list[LineageDiagnosticResponse]


class AssetSummaryResponse(BaseModel):
    assets: int
    declared: int
    discovered_only: int
    stitched: int
    with_issues: int


class AssetMetadataSourceResponse(BaseModel):
    id: int
    uri: str


class AssetIssueResponse(BaseModel):
    severity: Literal["info", "warning", "error"]
    code: str
    message: str
    dataflow_id: str | None = None
    metadata_source_id: int | None = None
    reference_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AssetResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    display_name: str
    friendly_name: str
    full_identity: str
    kind: Literal["table", "path", "sql_query", "python_function", "api", "unresolved"]
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
    declaration_status: Literal["declared", "discovered_only"]
    roles: list[str] = Field(default_factory=list)
    metadata_source_ids: list[int] = Field(default_factory=list)
    metadata_sources: list[AssetMetadataSourceResponse] = Field(default_factory=list)
    upstream_count: int
    downstream_count: int
    input_dataflow_count: int
    output_dataflow_count: int
    dependency_count: int
    issue_count: int
    issues: list[AssetIssueResponse] = Field(default_factory=list)
    identifiers: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)


class AssetFilterOptionsResponse(BaseModel):
    connections: list[str] = Field(default_factory=list)
    formats: list[str] = Field(default_factory=list)
    kinds: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    declaration_statuses: list[str] = Field(default_factory=list)
    issue_states: list[str] = Field(default_factory=list)


class AssetsResponse(BaseModel):
    summary: AssetSummaryResponse
    assets: list[AssetResponse]
    filter_options: AssetFilterOptionsResponse
    diagnostics: list[LineageDiagnosticResponse]


class AssetDetailResponse(BaseModel):
    asset: AssetResponse
    diagnostics: list[AssetIssueResponse] = Field(default_factory=list)


class MonitoringOverviewResponse(BaseModel):
    summary: dict[str, Any]
    failed_dataflows: list[dict[str, Any]]
    slowest_dataflows: list[dict[str, Any]]
    duration_by_stage: list[dict[str, Any]]
    status_by_stage: list[dict[str, Any]]
    errors: list[dict[str, Any]]


class MonitoringReportResponse(BaseModel):
    summary: dict[str, Any]
    health: dict[str, Any]
    attention: list[dict[str, Any]]
    coverage: dict[str, Any]
    reconciliation: dict[str, Any]
    metric_definitions: dict[str, Any]
    operations: dict[str, Any]
    failures: dict[str, Any]
    performance: dict[str, Any]
    volume: dict[str, Any]
    maintenance: dict[str, Any]
    diagnostics: dict[str, Any]
    freshness: dict[str, Any]
    errors: list[dict[str, Any]]
