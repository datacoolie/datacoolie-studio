from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from datacoolie_studio.api.v1.contracts.shared import ReferenceType, TargetIdentifierKind


def _normalize_project_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Project name cannot be blank")
    if len(normalized) > 255:
        raise ValueError("Project name cannot exceed 255 characters")
    return normalized


def _normalize_environment_name(value: str) -> str:
    display_name = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,49}", display_name):
        raise ValueError(
            "Environment must start with a letter or number and contain only "
            "letters, numbers, hyphens, or underscores"
        )
    return display_name


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None

    @field_validator("name")
    @classmethod
    def validate_project_name(cls, value: str) -> str:
        return _normalize_project_name(value)


class ProjectRename(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def validate_project_name(cls, value: str) -> str:
        return _normalize_project_name(value)


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
        return _normalize_environment_name(value)


class EnvironmentRename(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def validate_environment_name(cls, value: str) -> str:
        return _normalize_environment_name(value)


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
    source_check_mode: Literal["fixed", "adaptive"]
    source_check_interval_seconds: int
    source_check_max_interval_seconds: int
    updated_at: datetime | None = None


class StudioSettingsUpdateRequest(BaseModel):
    timezone: str | None = None
    source_check_mode: Literal["fixed", "adaptive"] | None = None
    source_check_interval_seconds: int | None = Field(default=None, ge=5, le=3600)
    source_check_max_interval_seconds: int | None = Field(default=None, ge=5, le=3600)


class EnvironmentDependencyVersionsResponse(BaseModel):
    source_registry: str
    metadata_catalog: str
    code_catalog: str
    operations: str
    reference_mappings: str
