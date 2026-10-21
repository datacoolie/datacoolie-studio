from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from datacoolie_studio.api.v1.contracts.monitoring_values import IntegerMetric

from datacoolie_studio.api.v1.contracts.monitoring_rows import (
    MonitoringDataflowRecordResponse,
    MonitoringDiagnosticCaptureResponse,
    MonitoringDateBucketResponse,
    MonitoringErrorCategoryResponse,
    MonitoringExtensionRowResponse,
    MonitoringFailureTrendResponse,
    MonitoringJobRecordResponse,
    MonitoringNamedCountResponse,
    MonitoringReconciliationCheckResponse,
    MonitoringRecoveredAttemptResponse,
    MonitoringStatusCountResponse,
)


MonitoringValue = str | int | float | bool | None
MonitoringValueMap = dict[str, MonitoringValue]
MonitoringNumberMap = dict[str, str | int | float | None]
MonitoringWindowMap = dict[str, MonitoringNumberMap]
MonitoringObject = dict[str, object]


class MonitoringSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataflow_records: IntegerMetric = "0"
    job_records: IntegerMetric = "0"
    date_range: dict[str, str | None] = Field(default_factory=dict)
    latest_log_at: str | None = None
    latest_job_log_at: str | None = None
    latest_dataflow_log_at: str | None = None
    timezone: str | None = None
    timezone_source: str | None = None
    identity_version: str | None = None
    requested_grain: str | None = None
    effective_grain: str | None = None
    active_engines: IntegerMetric = "0"
    active_metadata_providers: IntegerMetric = "0"
    log_base_paths: IntegerMetric = "0"


class MonitoringHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "unknown"
    label: str = "Unknown"
    reasons: list[str] = Field(default_factory=list)
    latest_log_at: str | None = None
    latest_job_log_at: str | None = None
    latest_dataflow_log_at: str | None = None
    latest_log_age_days: float | None = None
    failed_jobs_last_3_days: IntegerMetric | None = None
    failed_jobs_last_7_days: IntegerMetric | None = None
    failed_dataflows_last_3_days: IntegerMetric | None = None
    failed_dataflows_last_7_days: IntegerMetric | None = None
    maintenance_failed_last_7_days: IntegerMetric | None = None
    maintenance_failed_last_14_days: IntegerMetric | None = None
    maintenance_skipped_last_7_days: IntegerMetric | None = None


class MonitoringAttentionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: str
    code: str
    title: str
    detail: str
    target: str
    evidence: MonitoringObject | None = None


class MonitoringCoverageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataflow_job_ids: IntegerMetric | None = None
    dataflow_records: IntegerMetric | None = None
    enabled_log_base_paths: IntegerMetric | None = None
    job_ids: IntegerMetric | None = None
    job_records: IntegerMetric | None = None
    jobs_without_dataflow_records: IntegerMetric | None = None
    linked_job_ids: IntegerMetric | None = None
    orphan_dataflow_job_ids: IntegerMetric | None = None
    read_errors: IntegerMetric | None = None
    scope_coverage: str | None = None
    log_records_dropped: IntegerMetric | None = None
    log_bytes_dropped: IntegerMetric | None = None
    log_loss_job_count: IntegerMetric | None = None
    log_records_dropped_reporting_jobs: IntegerMetric | None = None
    log_bytes_dropped_reporting_jobs: IntegerMetric | None = None
    log_records_dropped_observed_subtotal: IntegerMetric | None = None
    log_bytes_dropped_observed_subtotal: IntegerMetric | None = None
    log_loss_unknown_jobs: IntegerMetric | None = None
    log_loss_coverage: float | None = None
    log_bytes_loss_coverage: float | None = None
    status: str | None = None


class MonitoringReconciliationSummaryResponse(MonitoringExtensionRowResponse):
    total_checks_count: IntegerMetric = "0"
    total_jobs_count: IntegerMetric = "0"
    comparable_checks_count: IntegerMetric = "0"
    comparable_jobs_count: IntegerMetric = "0"
    mismatch_checks_count: IntegerMetric = "0"
    mismatch_jobs_count: IntegerMetric = "0"
    unassessed_checks_count: IntegerMetric = "0"
    unassessed_jobs_count: IntegerMetric = "0"
    scope_coverage: str | None = None
    unassessed_reason: str | None = None


class MonitoringDiagnosticsAssessmentReasonResponse(MonitoringExtensionRowResponse):
    code: str
    severity: str
    count: IntegerMetric = "0"
    scope_coverage: str | None = None


class MonitoringDiagnosticsAssessmentResponse(MonitoringExtensionRowResponse):
    status: str
    reasons: list[MonitoringDiagnosticsAssessmentReasonResponse] = Field(default_factory=list)
    coverage: str | None = None


class MonitoringDiagnosticsRegistrySummaryResponse(MonitoringExtensionRowResponse):
    total_issues: IntegerMetric = "0"
    loaded_issues: IntegerMetric = "0"
    limit: int = 200
    truncated: bool = False


FindingKind = Literal["issue", "warning", "limitation", "notice"]
FindingSubjectKind = Literal["job", "dataflow_record", "field_group"]


class MonitoringDiagnosticsFindingGroupResponse(BaseModel):
    """One structural finding group; counts use the declared subject unit."""

    model_config = ConfigDict(extra="forbid")

    group_key: str
    kind: FindingKind
    rule: str
    reason: str
    section: str = ""
    subject_kind: FindingSubjectKind
    affected_objects: IntegerMetric
    checks: IntegerMetric | None = None
    occurrences: IntegerMetric | None = None
    latest_time: str | None = None


class MonitoringDiagnosticsFindingMetricResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    expected: IntegerMetric | None = None
    observed: IntegerMetric | None = None
    difference: IntegerMetric | None = None


class MonitoringDiagnosticsFindingObjectResponse(BaseModel):
    """One affected object with the exact identity needed for drill-in."""

    model_config = ConfigDict(extra="forbid")

    group_key: str
    rule: str
    reason: str
    section: str = ""
    subject_kind: FindingSubjectKind
    source_id: int | None = None
    log_session_id: str | None = None
    job_id: str | None = None
    dataflow_id: str | None = None
    dataflow_run_id: str | None = None
    dataflow_name: str | None = None
    file_uri: str | None = None
    file_kind: str | None = None
    record_line: IntegerMetric | None = None
    latest_time: str | None = None
    checks: IntegerMetric | None = None
    occurrences: IntegerMetric | None = None
    job_status: str | None = None
    job_total_dataflows: IntegerMetric | None = None
    filtered_dataflow_records: IntegerMetric | None = None
    recorded_dataflow_records: IntegerMetric | None = None
    excluded_dataflow_records: IntegerMetric | None = None
    observed_at: str | None = None
    records_dropped: IntegerMetric | None = None
    bytes_dropped: IntegerMetric | None = None
    metrics: list[MonitoringDiagnosticsFindingMetricResponse] = Field(default_factory=list)
    field_group: MonitoringExtensionRowResponse | None = None


class MonitoringDiagnosticsFindingObjectCountResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FindingKind
    subject_kind: FindingSubjectKind
    count: IntegerMetric


class MonitoringDiagnosticsFindingsSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analytics_generation: str | None = None
    as_of: str | None = None
    scope: Literal["full", "subset", "none"] = "none"
    issue_groups: IntegerMetric = "0"
    warning_groups: IntegerMetric = "0"
    limitation_groups: IntegerMetric = "0"
    groups_total: IntegerMetric = "0"
    groups_loaded: IntegerMetric = "0"
    group_limit: int = 50
    affected_objects: list[MonitoringDiagnosticsFindingObjectCountResponse] = Field(default_factory=list)
    restrictions: list[str] = Field(default_factory=list)


class MonitoringDiagnosticsScopeNoticeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    group_key: str
    jobs: IntegerMetric
    checks: IntegerMetric
    restrictions: list[str] = Field(default_factory=list)


class MonitoringReconciliationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "unknown"
    scope_coverage: str | None = None
    mismatch_count: IntegerMetric = "0"
    checks: list[MonitoringReconciliationCheckResponse] = Field(default_factory=list)
    summary: MonitoringReconciliationSummaryResponse | None = None


class MonitoringOperationsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kpis: MonitoringNumberMap = Field(default_factory=dict)
    windows: MonitoringWindowMap = Field(default_factory=dict)
    job_duration_stats: MonitoringNumberMap | None = None
    job_status_distribution: list[MonitoringStatusCountResponse] = Field(default_factory=list)
    job_runs_by_operation_type: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    job_runs_by_dataflow_operation_type: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    job_duration_by_operation_types: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    dataflow_duration_by_stage: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    job_workload_efficiency: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    job_child_fanout_distribution: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    jobs_by_date_status: list[MonitoringDateBucketResponse] = Field(default_factory=list)
    latest_failed_job: MonitoringJobRecordResponse | None = None
    slowest_jobs: list[MonitoringJobRecordResponse] = Field(default_factory=list)
    jobs_by_engine_provider: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    jobs_by_child_impact: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    job_attention: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    dataflows_by_date_status: list[MonitoringDateBucketResponse] = Field(default_factory=list)
    failed_jobs: list[MonitoringJobRecordResponse] = Field(default_factory=list)
    dataflow_kpis: MonitoringNumberMap = Field(default_factory=dict)
    dataflow_duration_stats: MonitoringNumberMap | None = None
    dataflow_runs_by_operation_type: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    dataflow_runs_by_destination_operation_type: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    phase_health: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    phase_health_by_stage: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    dataflow_endpoint_health: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    dataflow_name_status_health: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    dataflow_watermark_summary: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    job_status_by_stage: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    dataflow_status_by_stage: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    status_by_stage: list[MonitoringExtensionRowResponse] = Field(default_factory=list)


class MonitoringFailuresResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kpis: MonitoringValueMap = Field(default_factory=dict)
    latest_queue: list[MonitoringDataflowRecordResponse] = Field(default_factory=list)
    repeated_signatures: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    failure_by_phase: list[MonitoringNamedCountResponse] = Field(default_factory=list)
    failure_category_phase_matrix: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    endpoint_impact: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    failed_by_stage: list[MonitoringNamedCountResponse] = Field(default_factory=list)
    failed_by_source_connection_type: list[MonitoringNamedCountResponse] = Field(default_factory=list)
    top_failing_dataflows: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    error_categories: list[MonitoringErrorCategoryResponse] = Field(default_factory=list)
    failure_trend_by_date: list[MonitoringFailureTrendResponse] = Field(default_factory=list)
    failed_records: list[MonitoringDataflowRecordResponse] = Field(default_factory=list)
    recovered_attempts: list[MonitoringRecoveredAttemptResponse] = Field(default_factory=list)


class MonitoringPerformanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kpis: MonitoringValueMap = Field(default_factory=dict)
    duration_breakdown: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    duration_vs_rows: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    slowest_dataflows: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    duration_by_stage: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    engine_stage_matrix: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    duration_distribution_by_stage: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    phase_contribution_by_stage_operation: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    workload_efficiency_points: list[list[MonitoringValue] | MonitoringExtensionRowResponse] = Field(default_factory=list)
    slowest_dataflow_profiles: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    runtime_context_profiles: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    performance_trend: list[MonitoringDateBucketResponse] = Field(default_factory=list)


class MonitoringVolumeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kpis: MonitoringNumberMap = Field(default_factory=dict)
    rows_by_date: list[MonitoringDateBucketResponse] = Field(default_factory=list)
    bytes_by_date: list[MonitoringDateBucketResponse] = Field(default_factory=list)
    volume_by_load_type: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    volume_by_workload_type: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    route_volume: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    top_dataflows_by_rows_read: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    top_dataflows_by_rows_written: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    top_dataflows_by_lakehouse_rows_written: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    top_dataflows_by_bytes_added: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    top_dataflows_by_net_bytes: list[MonitoringExtensionRowResponse] = Field(default_factory=list)


class MonitoringMaintenanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kpis: MonitoringValueMap = Field(default_factory=dict)
    operation_type_counts: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    maintenance_status_counts: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    bytes_reclaimed_by_table: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    per_table: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    duration_vs_files_removed: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    status_by_date: list[MonitoringDateBucketResponse] = Field(default_factory=list)
    reclaim_by_date: list[MonitoringDateBucketResponse] = Field(default_factory=list)
    table_efficiency_points: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    table_outcome: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    format_comparison: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    bytes_reclaimed_by_date: list[MonitoringDateBucketResponse] = Field(default_factory=list)


class MonitoringDiagnosticsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kpis: MonitoringValueMap = Field(default_factory=dict)
    assessment: MonitoringDiagnosticsAssessmentResponse | None = None
    registry_summary: MonitoringDiagnosticsRegistrySummaryResponse | None = None
    record_evidence_by_date: list[MonitoringDateBucketResponse] = Field(default_factory=list)
    job_linkage_summary: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    reconciliation_by_metric: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    field_completeness: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    source_coverage: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    investigation_queue: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    finding_groups: list[MonitoringDiagnosticsFindingGroupResponse] = Field(default_factory=list)
    findings_summary: MonitoringDiagnosticsFindingsSummaryResponse | None = None
    scope_notices: list[MonitoringDiagnosticsScopeNoticeResponse] = Field(default_factory=list)
    diagnostic_capture: MonitoringDiagnosticCaptureResponse | None = None
    job_id_evidence: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    read_errors: list[MonitoringExtensionRowResponse] = Field(default_factory=list)


class MonitoringFreshnessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kpis: MonitoringNumberMap = Field(default_factory=dict)
    latest_freshness_by_dataflow: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    watermark_movement: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    stale_candidates: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    skipped_patterns: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    age_by_dataflow: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    watermark_movement_by_date: list[MonitoringDateBucketResponse] = Field(default_factory=list)
    age_distribution: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    watermark_coverage_by_stage: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    skipped_streak_distribution: list[MonitoringExtensionRowResponse] = Field(default_factory=list)
    read_boundary_adjustment_by_dataflow: list[MonitoringExtensionRowResponse] = Field(default_factory=list)


class MonitoringPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["monitoring-page.v12"]
    page: Literal[
        "environment-overview",
        "overview",
        "jobs",
        "dataflows",
        "failures",
        "diagnostics",
        "performance",
        "volume",
        "maintenance",
        "freshness",
    ]
    summary: MonitoringSummaryResponse
    log_admission: MonitoringObject | None = None
    health: MonitoringHealthResponse | None = None
    attention: list[MonitoringAttentionResponse] | None = None
    coverage: MonitoringCoverageResponse | None = None
    reconciliation: MonitoringReconciliationResponse | None = None
    operations: MonitoringOperationsResponse | None = None
    failures: MonitoringFailuresResponse | None = None
    performance: MonitoringPerformanceResponse | None = None
    volume: MonitoringVolumeResponse | None = None
    maintenance: MonitoringMaintenanceResponse | None = None
    diagnostics: MonitoringDiagnosticsResponse | None = None
    freshness: MonitoringFreshnessResponse | None = None
    errors: list[MonitoringExtensionRowResponse] | None = None
