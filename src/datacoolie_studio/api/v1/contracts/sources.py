from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from datacoolie_studio.api.v1.contracts.workspace import EnvironmentDependencyVersionsResponse

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
