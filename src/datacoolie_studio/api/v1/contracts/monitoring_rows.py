from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator
from datacoolie_studio.api.v1.contracts.monitoring_values import IntegerMetric, exact_metric_transport


class MonitoringExtensionRowResponse(BaseModel):
    """A metric row whose additional columns are intentionally provider-defined."""

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def exact_metrics(cls, value):
        return exact_metric_transport(value)


class MonitoringNamedCountResponse(MonitoringExtensionRowResponse):
    name: str | None = None
    status: str | None = None
    category: str | None = None
    count: IntegerMetric = "0"


class MonitoringStatusCountResponse(MonitoringExtensionRowResponse):
    status: str = "unknown"
    count: IntegerMetric = "0"


class MonitoringDateBucketResponse(MonitoringExtensionRowResponse):
    bucket: str | None = None
    bucket_start: str | None = None
    bucket_end: str | None = None
    date: str | None = None
    grain: str | None = None


class MonitoringDiagnosticCaptureSummaryResponse(MonitoringExtensionRowResponse):
    affected_records: IntegerMetric = "0"
    known_markers: IntegerMetric = "0"
    omitted_marker_records: IntegerMetric = "0"
    overflow_records: IntegerMetric = "0"
    evidence_records_count: IntegerMetric = "0"
    evidence_limit: int = 50
    truncated: bool = False


class MonitoringDiagnosticIssueResponse(MonitoringExtensionRowResponse):
    log_session_id: str | None = None
    job_id: str | None = None
    dataflow_id: str | None = None
    dataflow_run_id: str | None = None
    dataflow_name: str | None = None
    record_type: str | None = None
    latest_time: str | None = None
    field: str
    reason: str


class MonitoringDiagnosticCaptureResponse(BaseModel):
    summary: MonitoringDiagnosticCaptureSummaryResponse
    by_reason: list[MonitoringNamedCountResponse] = Field(default_factory=list)
    evidence: list[MonitoringDiagnosticIssueResponse] = Field(default_factory=list)


class MonitoringRecoveredAttemptResponse(MonitoringExtensionRowResponse):
    log_session_id: str | None = None
    job_id: str | None = None
    dataflow_id: str | None = None
    dataflow_run_id: str
    dataflow_name: str | None = None
    status: str
    event_time: str | None = None
    attempt_number: IntegerMetric
    attempt_message: str | None = None
    attempt_record_json: str


class MonitoringReconciliationCheckResponse(MonitoringExtensionRowResponse):
    scope_coverage: str | None = None
    observed_at: str | None = None
    latest_child_time: str | None = None
    log_records_dropped: IntegerMetric | None = None
    log_bytes_dropped: IntegerMetric | None = None
    raw_record_json: str | None = None
    job_id: str | None = None
    log_session_id: str | None = None
    metric: str | None = None
    expected: IntegerMetric | None = None
    observed: IntegerMetric | None = None
    difference: IntegerMetric | None = None
    severity: str | None = None


class MonitoringJobRecordResponse(MonitoringExtensionRowResponse):
    datacoolie_version: str | None = None
    observed_at: str | None = None
    message: str | None = None
    message_truncated: bool | None = None
    raw_record_json: str | None = None
    job_id: str | None = None
    log_session_id: str | None = None
    engine_name: str | None = None
    metadata_provider_name: str | None = None
    platform_name: str | None = None
    run_attributes: str | None = None
    status: str | None = None
    duration_seconds: float | None = None
    error_message: str | None = None
    error_message_truncated: bool | None = None
    log_records_dropped: IntegerMetric | None = None
    log_bytes_dropped: IntegerMetric | None = None
    total_dataflows: IntegerMetric | None = None
    total_rows_read: IntegerMetric | None = None
    total_rows_written: IntegerMetric | None = None
    total_bytes_added: IntegerMetric | None = None
    total_bytes_removed: IntegerMetric | None = None
    start_time: str | None = None
    end_time: str | None = None


class MonitoringDataflowRecordResponse(MonitoringExtensionRowResponse):
    datacoolie_version: str | None = None
    message: str | None = None
    message_truncated: bool | None = None
    source_message: str | None = None
    transform_message: str | None = None
    destination_message: str | None = None
    dataflow_name: str | None = None
    dataflow_id: str | None = None
    dataflow_run_id: str | None = None
    raw_record_json: str | None = None
    job_id: str | None = None
    log_session_id: str | None = None
    stage: str | None = None
    status: str | None = None
    normalized_status: str | None = None
    operation_type: str | None = None
    duration_seconds: float | None = None
    start_time: str | None = None
    end_time: str | None = None
    event_time: str | None = None
    failure_category: str | None = None
    failure_kind: str | None = None
    failure_phase: str | None = None
    failure_message: str | None = None
    failure_signature: str | None = None
    failure_target: str | None = None
    failure_rule_id: str | None = None
    failure_time: str | None = None
    failure_tags: list[str] = Field(default_factory=list)
    source_name: str | None = None
    source_connection_type: str | None = None
    source_format: str | None = None
    destination_name: str | None = None
    destination_connection_type: str | None = None
    destination_format: str | None = None
    source_status: str | None = None
    destination_status: str | None = None
    error_message: str | None = None


class MonitoringErrorCategoryResponse(MonitoringExtensionRowResponse):
    category: str = "Unspecified"
    count: IntegerMetric = "0"


class MonitoringFailureTrendResponse(MonitoringExtensionRowResponse):
    date: str | None = None
    failed: IntegerMetric = "0"
