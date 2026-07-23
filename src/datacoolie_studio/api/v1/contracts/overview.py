from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

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
