"""Public Monitoring service facade.

Page construction, cache keys, evidence readers, and request orchestration are
kept in their owning boundary modules. This module intentionally only
re-exports the public entry points used by the API routes and callers.
"""

from datacoolie_studio.domains.monitoring.diagnostics_findings import FindingRequestError
from datacoolie_studio.domains.monitoring.page_cache import (
    monitoring_dataflow_registry_cache_key,
    monitoring_evidence_cache_key,
    monitoring_page,
    monitoring_page_cache_key,
    monitoring_page_etag,
)
from datacoolie_studio.domains.monitoring.page_contracts import (
    MONITORING_PAGES,
    RESPONSE_VERSION as MONITORING_PAGE_RESPONSE_VERSION,
)
from datacoolie_studio.domains.monitoring.page_dispatch import build_monitoring_page
from datacoolie_studio.domains.monitoring.page_evidence import DiagnosticsGenerationChanged
from datacoolie_studio.domains.monitoring.page_orchestration import (
    monitoring_dataflow_registry,
    monitoring_diagnostics_evidence,
    monitoring_page_evidence,
)
from datacoolie_studio.domains.monitoring.page_projection import public_monitoring_page

__all__ = [
    "DiagnosticsGenerationChanged",
    "FindingRequestError",
    "monitoring_diagnostics_evidence",
    "monitoring_dataflow_registry",
    "MONITORING_PAGES",
    "MONITORING_PAGE_RESPONSE_VERSION",
    "build_monitoring_page",
    "monitoring_page",
    "monitoring_page_cache_key",
    "monitoring_page_etag",
    "monitoring_evidence_cache_key",
    "monitoring_dataflow_registry_cache_key",
    "monitoring_page_evidence",
    "public_monitoring_page",
]
