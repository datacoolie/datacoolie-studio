from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class MonitoringSectionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")


class MonitoringSummaryResponse(MonitoringSectionResponse):
    dataflow_records: int = 0
    job_records: int = 0
    date_range: dict[str, str | None] = {}
    latest_log_at: str | None = None
    latest_job_log_at: str | None = None
    latest_dataflow_log_at: str | None = None
    timezone: str | None = None
    timezone_source: str | None = None
    requested_grain: str | None = None
    effective_grain: str | None = None
    active_engines: int = 0
    active_metadata_providers: int = 0
    log_paths: int = 0


class MonitoringHealthResponse(MonitoringSectionResponse):
    status: str = "unknown"
    label: str = "Unknown"
    reasons: list[str] = []
    latest_log_at: str | None = None


class MonitoringAttentionResponse(MonitoringSectionResponse):
    severity: str
    code: str
    title: str
    detail: str
    target: str
    evidence: dict[str, Any] | None = None


class MonitoringReconciliationResponse(MonitoringSectionResponse):
    status: str = "unknown"
    mismatch_count: int = 0
    checks: list[dict[str, Any]] = []


class MonitoringOperationsResponse(MonitoringSectionResponse):
    kpis: dict[str, float] = {}
    jobs_by_date_status: list[dict[str, Any]] = []
    dataflows_by_date_status: list[dict[str, Any]] = []
    failed_jobs: list[dict[str, Any]] = []
    status_by_stage: list[dict[str, Any]] = []


class MonitoringFailuresResponse(MonitoringSectionResponse):
    kpis: dict[str, Any] = {}
    failed_by_stage: list[dict[str, Any]] = []
    top_failing_dataflows: list[dict[str, Any]] = []
    error_categories: list[dict[str, Any]] = []
    failure_trend_by_date: list[dict[str, Any]] = []
    failed_records: list[dict[str, Any]] = []


class MonitoringPerformanceResponse(MonitoringSectionResponse):
    kpis: dict[str, Any] = {}
    duration_distribution_by_stage: list[dict[str, Any]] = []
    performance_trend: list[dict[str, Any]] = []


class MonitoringVolumeResponse(MonitoringSectionResponse):
    kpis: dict[str, float] = {}
    rows_by_date: list[dict[str, Any]] = []
    bytes_by_date: list[dict[str, Any]] = []
    volume_by_load_type: list[dict[str, Any]] = []


class MonitoringMaintenanceResponse(MonitoringSectionResponse):
    kpis: dict[str, Any] = {}
    status_by_date: list[dict[str, Any]] = []
    reclaim_by_date: list[dict[str, Any]] = []
    format_comparison: list[dict[str, Any]] = []
    bytes_reclaimed_by_date: list[dict[str, Any]] = []


class MonitoringDiagnosticsResponse(MonitoringSectionResponse):
    kpis: dict[str, Any] = {}
    job_id_evidence: list[dict[str, Any]] = []
    read_errors: list[dict[str, Any]] = []


class MonitoringFreshnessResponse(MonitoringSectionResponse):
    kpis: dict[str, float] = {}
    age_by_dataflow: list[dict[str, Any]] = []
    watermark_movement_by_date: list[dict[str, Any]] = []


class MonitoringPageResponse(BaseModel):
    schema_version: Literal["monitoring-page.v9"]
    page: Literal["environment-overview", "overview", "jobs", "dataflows", "failures", "diagnostics", "performance", "volume", "maintenance", "freshness"]
    summary: MonitoringSummaryResponse
    health: MonitoringHealthResponse | None = None
    attention: list[MonitoringAttentionResponse] | None = None
    coverage: dict[str, Any] | None = None
    reconciliation: MonitoringReconciliationResponse | None = None
    operations: MonitoringOperationsResponse | None = None
    failures: MonitoringFailuresResponse | None = None
    performance: MonitoringPerformanceResponse | None = None
    volume: MonitoringVolumeResponse | None = None
    maintenance: MonitoringMaintenanceResponse | None = None
    diagnostics: MonitoringDiagnosticsResponse | None = None
    freshness: MonitoringFreshnessResponse | None = None
    errors: list[dict[str, Any]] | None = None
