from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from datacoolie_studio.api.v1.contracts.workspace import EnvironmentDependencyVersionsResponse

class SourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str
    label: str | None = None
    enabled: bool = True
    source_config: dict[str, Any] | None = None
    storage: StorageBindingRequest | None = None


class StorageBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal[
        "local", "s3", "minio", "adls", "onelake", "gcs", "dbfs"
    ]
    auth_mode: Literal["none", "ambient", "anonymous", "credential_profile"]
    credential_profile_id: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class StorageBindingResponse(StorageBindingRequest):
    pass


class ConfiguredSourceLocationResponse(BaseModel):
    registration_id: int
    purpose: Literal["project", "metadata", "code", "logs"]
    input_uri: str
    canonical_uri: str
    input_locations: dict[str, str] = Field(default_factory=dict)
    canonical_locations: dict[str, str] = Field(default_factory=dict)


class MetadataSourceImportRequest(BaseModel):
    uri: str
    label: str | None = None
    enabled: bool = True
    storage: StorageBindingRequest | None = None


class DatacoolieProjectSourceImportRequest(BaseModel):
    project_uri: str
    metadata_subpath: str = "metadata"
    code_subpath: str = "functions"
    metadata_uri: str | None = None
    code_uri: str | None = None
    include_metadata: bool = True
    include_code: bool = True
    enabled: bool = True
    storage: StorageBindingRequest | None = None


class SourceImportItemResponse(BaseModel):
    status: Literal["created", "existing"]
    id: int
    source_kind: str
    uri: str
    label: str | None = None
    record_counts: dict[str, int] = Field(default_factory=dict)
    source_config: dict[str, Any] = Field(default_factory=dict)
    configured_location: ConfiguredSourceLocationResponse | None = None


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
    storage: StorageBindingRequest | None = None

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
    last_observed_at: datetime | None = None
    next_check_at: datetime | None = None
    pending_changes: bool | None = None
    observation_state: Literal["active", "retrying", "paused"] = "active"
    observation_failure_count: int = 0
    observation_paused_at: datetime | None = None
    active_operation: Literal["validate", "sync"] | None = None
    latest_job: SourceSyncJobResponse | None = None


class SourceObservationOutcomeResponse(BaseModel):
    source_id: int
    source_kind: str
    outcome: Literal["changed", "unchanged", "error", "skipped"]
    pending_changes: bool | None = None
    error: dict[str, Any] | None = None
    started_at: datetime
    completed_at: datetime
    status: SourceSyncStatusResponse


class LocalSourceObservationResponse(BaseModel):
    environment_id: int
    total: int
    observed: int
    changed: int
    skipped: int
    failed: int
    observed_at: datetime
    outcomes: list[SourceObservationOutcomeResponse]


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
    storage: StorageBindingResponse
    configured_location: ConfiguredSourceLocationResponse | None = None
    latest_validation: SourceValidationResponse | None = None


class StorageConnectionValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str
    storage: StorageBindingRequest | None = None
    source_config: dict[str, Any] = Field(default_factory=dict)


class StorageConnectionValidationResponse(BaseModel):
    status: Literal["ok", "error"]
    provider: str
    canonical_uri: str | None = None
    object_type: Literal["file", "directory"] | None = None
    objects_scanned: int = 0
    provider_revision: str | None = None
    metadata_write_back_supported: bool = False
    message: str
    error: dict[str, Any] | None = None


class LogSourceRead(MetadataSourceRead):
    sync_schedule_enabled: bool = False
    sync_interval_minutes: int | None = None
    last_scheduled_sync_at: datetime | None = None


class CodeArtifactRead(MetadataSourceRead):
    pass


class SourcesWorkspaceResponse(BaseModel):
    schema_version: Literal["sources-workspace.v1"]
    environment_id: int
    metadata_sources: list[MetadataSourceRead]
    log_sources: list[LogSourceRead]
    code_artifacts: list[CodeArtifactRead]
    statuses: list[SourceSyncStatusResponse]
    earliest_cloud_due_at: datetime | None = None
    dependency_version: str


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
