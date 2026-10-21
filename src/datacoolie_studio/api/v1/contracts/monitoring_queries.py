from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from datacoolie_studio.api.v1.contracts.monitoring import (
    MonitoringDiagnosticsFindingGroupResponse,
    MonitoringDiagnosticsFindingObjectResponse,
)
from datacoolie_studio.api.v1.contracts.monitoring_rows import (
    MonitoringDataflowRecordResponse,
    MonitoringExtensionRowResponse,
    MonitoringJobRecordResponse,
)
from datacoolie_studio.api.v1.contracts.monitoring_values import IntegerMetric


RegistrySection = Literal["overview", "execution", "runtime", "read-watermark", "configuration", "technical"]
RegistryExecutionSort = Literal[
    "event_time", "latest_run_at", "dataflow_run_id", "stage", "duration_seconds",
    "status", "phase_health", "source_rows_read", "destination_rows_written", "message",
]
RegistryRuntimeSort = Literal["platform_name", "engine_name", "metadata_provider_name", "run_count", "latest_run_at"]
RegistryDetailSort = Literal[
    "event_time", "latest_run_at", "dataflow_run_id", "stage", "duration_seconds",
    "status", "phase_health", "source_rows_read", "destination_rows_written", "message",
    "platform_name", "engine_name", "metadata_provider_name", "run_count",
]


class MonitoringRegistryRunReferenceResponse(MonitoringExtensionRowResponse):
    dataflow_id: str
    dataflow_run_id: str
    source_id: int = Field(alias="_source_id")
    file_uri: str | None = Field(default=None, alias="_file_uri")
    file_kind: str | None = Field(default=None, alias="_file_kind")
    record_line: int | None = Field(default=None, alias="_record_line")
    log_session_id: str | None = None
    event_time: str | None = None
    status: str | None = None


class MonitoringRegistryProfileSnapshotResponse(MonitoringRegistryRunReferenceResponse):
    dataflow_name: str | None = None
    dataflow_description: str | None = None
    outside_filter: bool = False


class MonitoringRegistryRowResponse(MonitoringExtensionRowResponse):
    """Declared entity/runtime boundaries; facet metrics keep their existing owners."""

    dataflow_id: str | None = None
    profile_snapshot: MonitoringRegistryProfileSnapshotResponse | None = None
    recent_runs: list[MonitoringRegistryRunReferenceResponse] = Field(default_factory=list, max_length=5)
    platform_name: str | None = None
    engine_name: str | None = None
    metadata_provider_name: str | None = None
    run_count: IntegerMetric | None = None
    latest_run_at: str | None = None
    latest_run_id: str | None = None
    latest_source_id: int | None = None
    latest_log_session_id: str | None = None
    latest_file_uri: str | None = None
    latest_file_kind: str | None = None
    latest_record_line: int | None = None


class MonitoringRecordsSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: int
    total_records: int
    limit: int
    offset: int
    cache: str | None = None
    analytics_generation: str | None = None
    identity_version: str | None = None
    as_of: str | None = None


class MonitoringDataflowRecordsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[MonitoringDataflowRecordResponse] = Field(default_factory=list)
    errors: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    summary: MonitoringRecordsSummaryResponse


class MonitoringJobRecordsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[MonitoringJobRecordResponse] = Field(default_factory=list)
    errors: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    summary: MonitoringRecordsSummaryResponse


class MonitoringEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    errors: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    summary: MonitoringRecordsSummaryResponse


class MonitoringDiagnosticsEvidenceResponse(BaseModel):
    """Finding groups (registry) or one group's affected objects (detail)."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["registry", "detail"]
    group: MonitoringDiagnosticsFindingGroupResponse | None = None
    groups: list[MonitoringDiagnosticsFindingGroupResponse] = Field(default_factory=list)
    objects: list[MonitoringDiagnosticsFindingObjectResponse] = Field(default_factory=list)
    errors: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    summary: MonitoringRecordsSummaryResponse


class MonitoringRegistryRuntimeRowResponse(MonitoringRegistryRowResponse):
    dataflow_id: str
    run_count: IntegerMetric
    latest_run_id: str
    latest_source_id: int


class MonitoringRegistryEnvelopeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    errors: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    summary: MonitoringRecordsSummaryResponse


class MonitoringRegistryEntityResponse(MonitoringRegistryEnvelopeResponse):
    section: Literal["list", "overview", "read-watermark", "configuration", "technical"]
    records: list[MonitoringRegistryRowResponse] = Field(default_factory=list)


class MonitoringRegistryExecutionResponse(MonitoringRegistryEnvelopeResponse):
    section: Literal["execution"]
    records: list[MonitoringRegistryRunReferenceResponse] = Field(default_factory=list)


class MonitoringRegistryRuntimeResponse(MonitoringRegistryEnvelopeResponse):
    section: Literal["runtime"]
    records: list[MonitoringRegistryRuntimeRowResponse] = Field(default_factory=list)


class MonitoringDataflowRegistryResponse(RootModel[
    Annotated[
        MonitoringRegistryEntityResponse | MonitoringRegistryExecutionResponse | MonitoringRegistryRuntimeResponse,
        Field(discriminator="section"),
    ]
]):
    """Validate each bounded payload using its section's declared grain."""


class MonitoringFilterOptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    label: str
    count: int | None = None


class MonitoringFilterOptionsSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    fields: int


class MonitoringFilterOptionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    options: dict[str, list[MonitoringFilterOptionResponse]] = Field(default_factory=dict)
    summary: MonitoringFilterOptionsSummaryResponse
    errors: list[MonitoringExtensionRowResponse] = Field(default_factory=list)


class MonitoringLatestStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latest_by_id: dict[str, MonitoringDataflowRecordResponse] = Field(default_factory=dict)
    latest_by_name: dict[str, MonitoringDataflowRecordResponse] = Field(default_factory=dict)
    ambiguous_names: list[str] = Field(default_factory=list)
    errors: list[MonitoringExtensionRowResponse] = Field(default_factory=list)


class MonitoringSystemLogFileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: int
    file_uri: str
    file_kind: str
    log_session_id: str | None = None
    row_count: int
    log_timestamp: str | None = None
    event_min_timestamp: str | None = None
    event_max_timestamp: str | None = None
    run_date: str | None = None


class MonitoringSystemLogsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    total: int
    files: list[MonitoringSystemLogFileResponse] = Field(default_factory=list)
    errors: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
