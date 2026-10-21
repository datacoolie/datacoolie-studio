from __future__ import annotations

MONITORING_PAGES = frozenset({
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
})
# Fence corrected Jobs subset notices and distinct Diagnostics record counts.
PRODUCER_VERSION = "monitoring-page-sql-v30"
# Fence the per-scope measured-point sampler and workload-efficiency coverage.
PERFORMANCE_PAGE_PRODUCER_VERSION = f"{PRODUCER_VERSION}.performance-efficiency-sample-v2"
VOLUME_PAGE_PRODUCER_VERSION = f"{PRODUCER_VERSION}.volume-kpis-v1"
JOBS_PAGE_PRODUCER_VERSION = f"{PRODUCER_VERSION}.complete-workload-operations-v1"
MAINTENANCE_PAGE_PRODUCER_VERSION = f"{PRODUCER_VERSION}.destination-workload-layout-v19"
OVERVIEW_PAGE_PRODUCER_VERSION = f"{PRODUCER_VERSION}.maintenance-policy-v3"
DIAGNOSTICS_PAGE_PRODUCER_VERSION = f"{PRODUCER_VERSION}.diagnostics-findings-v1"
DIAGNOSTICS_EVIDENCE_PRODUCER_VERSION = f"{PRODUCER_VERSION}.diagnostics-findings-evidence-v2"
RESPONSE_VERSION = "monitoring-page.v12"
FRESHNESS_REGISTRY_PRODUCER_VERSION = "monitoring-freshness-registry-v2"
# Registry classification and its snapshot/catalog preparation are versioned.
DATAFLOW_REGISTRY_PRODUCER_VERSION = "monitoring-dataflow-registry-v13"
DATAFLOW_REGISTRY_SECTIONS = frozenset({
    "overview", "execution", "runtime", "read-watermark", "configuration", "technical",
})
DATAFLOW_REGISTRY_RUNTIME_SORT_FIELDS = frozenset({
    "platform_name", "engine_name", "metadata_provider_name", "run_count", "latest_run_at",
})
PUBLIC_PAGE_SECTIONS = {
    "overview": ("health", "attention", "operations", "failures", "volume"),
    "jobs": ("operations", "reconciliation"),
    "dataflows": ("operations", "volume"),
    "failures": ("operations", "failures"),
    "freshness": ("freshness",),
    "performance": ("performance",),
    "volume": ("volume",),
    "maintenance": ("maintenance",),
    "diagnostics": ("coverage", "reconciliation", "diagnostics"),
}
MULTI_VALUE_FILTERS = frozenset({
    "status",
    "stage",
    "connection",
    "engine",
    "provider",
    "sourceType",
    "destinationType",
    "loadType",
    "operationType",
})
PERFORMANCE_EVIDENCE_SORT_FIELDS = frozenset({
    "job_id",
    "dataflow_name",
    "stage",
    "performance_bottleneck_phase",
    "duration_seconds",
    "start_time",
    "end_time",
    "status",
    "message",
    "performance_candidate_priority",
    "performance_candidate_reason",
})
