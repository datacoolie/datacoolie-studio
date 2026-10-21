"""Cache and identity orchestration for Monitoring page endpoints.

The page service remains a small public facade. This module owns the
request-level reader fence, result-cache coordination, and destination
identity checks used by evidence and registry endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from datacoolie_studio.core.request_timing import record_cache_outcome, timed_stage
from datacoolie_studio.domains.monitoring.context import reader as analytics_reader
from datacoolie_studio.domains.monitoring.diagnostics_findings import (
    validate_finding_request,
)
from datacoolie_studio.domains.monitoring.identity_context import DestinationIdentityRequest
from datacoolie_studio.domains.monitoring.page_cache import (
    monitoring_dataflow_registry_cache_key,
    monitoring_diagnostics_evidence_cache_key,
    monitoring_evidence_cache_key,
)
from datacoolie_studio.domains.monitoring.page_contracts import DATAFLOW_REGISTRY_SECTIONS
from datacoolie_studio.domains.monitoring.page_evidence import (
    DiagnosticsGenerationChanged,
    diagnostics_findings_page,
    monitoring_page_evidence as compute_monitoring_page_evidence,
)
from datacoolie_studio.domains.monitoring.read_models.dataflow_registry import (
    dataflow_registry_read_model,
)
from datacoolie_studio.domains.read_models.contracts import get_or_compute
from datacoolie_studio.domains.read_models.provider import result_cache_provider


def monitoring_diagnostics_evidence(
    paths,
    *,
    filters: dict[str, str],
    timezone_info,
    as_of: datetime,
    view: str,
    finding_group: str | None,
    limit: int,
    offset: int,
    sort_by: str | None,
    sort_dir: str | None,
    session: Session,
    environment_id: int,
    timezone_label: str,
    kind_filter: str | None = None,
    expected_generation: str | None = None,
) -> dict[str, Any]:
    """Serve cached Diagnostics group/object pages under one analytics generation."""

    def compute(context) -> dict[str, Any]:
        return diagnostics_findings_page(
            paths, filters=filters, timezone_info=timezone_info, as_of=as_of, view=view,
            group=finding_group, limit=limit, offset=offset, sort_by=sort_by,
            sort_dir=sort_dir, kind_filter=kind_filter, expected_generation=expected_generation,
            analytics_context=context,
        )

    with analytics_reader(paths) as analytics_context:
        if expected_generation is not None and analytics_context[2] != expected_generation:
            raise DiagnosticsGenerationChanged(analytics_context[2])
        validate_finding_request(view, finding_group, sort_by, sort_dir, kind_filter)
        key = monitoring_diagnostics_evidence_cache_key(
            session, environment_id=environment_id, paths=paths, filters=filters,
            timezone_label=timezone_label, as_of=as_of, view=view,
            finding_group=finding_group, limit=limit, offset=offset,
            sort_by=sort_by, sort_dir=sort_dir, kind_filter=kind_filter,
            analytics_generation=analytics_context[2],
        )
        provider = result_cache_provider()
        result, _ = get_or_compute(
            provider.store, provider.coordinator, key, lambda: compute(analytics_context),
        )
        return {
            **result.payload,
            "summary": {
                **result.payload.get("summary", {}),
                "cache": "result-cache" if result.stored else "duckdb",
            },
        }


def monitoring_page_evidence(
    paths,
    page: str,
    *,
    filters: dict[str, str],
    timezone_info,
    limit: int,
    offset: int,
    sort_by: str,
    sort_dir: str,
    view: str = "detail",
    session: Session | None = None,
    environment_id: int | None = None,
    timezone_label: str | None = None,
    expected_identity_version: str | None = None,
) -> dict[str, Any]:
    """Serve compact registry/history evidence under its analytics generation."""
    uses_identity = page == "maintenance" or filters.get("investigateKind") == "physical_destination"
    if (view != "registry" and not uses_identity) or session is None or environment_id is None:
        return compute_monitoring_page_evidence(
            paths,
            page,
            filters=filters,
            timezone_info=timezone_info,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_dir=sort_dir,
            view=view,
        )

    with analytics_reader(paths, snapshot=uses_identity) as analytics_context:
        identity_request = DestinationIdentityRequest.capture(session, environment_id, analytics_context) if uses_identity else None
        if identity_request is not None:
            identity_request.require_version(expected_identity_version)
        if page != "maintenance" and view != "registry":
            return compute_monitoring_page_evidence(
                paths, page, filters=filters, timezone_info=timezone_info,
                limit=limit, offset=offset, sort_by=sort_by, sort_dir=sort_dir,
                view=view, analytics_context=analytics_context,
                identity_request=identity_request,
            )
        key = monitoring_evidence_cache_key(
            session,
            environment_id=environment_id,
            paths=paths,
            page=page,
            filters=filters,
            timezone_label=timezone_label or "UTC",
            view=view,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_dir=sort_dir,
            analytics_generation=analytics_context[2],
            identity_version=identity_request.version if identity_request is not None else None,
        )
        provider = result_cache_provider()

        def produce() -> dict[str, Any]:
            if identity_request is not None:
                identity_request.prepare()
            payload = compute_monitoring_page_evidence(
                paths,
                page,
                filters=filters,
                timezone_info=timezone_info,
                limit=limit,
                offset=offset,
                sort_by=sort_by,
                sort_dir=sort_dir,
                view=view,
                analytics_context=analytics_context,
                identity_request=identity_request,
            )
            if identity_request is not None:
                identity_request.assert_current()
            return payload

        result, _ = get_or_compute(
            provider.store,
            provider.coordinator,
            key,
            produce,
        )
        payload = result.payload
        if identity_request is not None:
            identity_request.assert_current()
        return {
            **payload,
            "summary": {
                **payload.get("summary", {}),
                "cache": "result-cache" if result.stored else "duckdb",
            },
        }


def monitoring_dataflow_registry(
    paths,
    *,
    filters: dict[str, str],
    limit: int,
    offset: int,
    sort_by: str,
    sort_dir: str,
    session: Session | None = None,
    environment_id: int | None = None,
    timezone_label: str | None = None,
    target_dataflow_id: str | None = None,
    section: str = "overview",
    facet: str = "all",
    analytics_context=None,
) -> dict[str, Any]:
    """Read the page-independent environment/dataflow registry."""

    if section not in DATAFLOW_REGISTRY_SECTIONS:
        raise ValueError(f"Unknown dataflow registry section: {section}")

    def compute(context, identity_request=None):
        if identity_request is not None:
            identity_request.prepare()
        with timed_stage("registry_sql"):
            payload = dataflow_registry_read_model(
                paths,
                filters,
                limit=limit,
                offset=offset,
                sort_by=sort_by,
                sort_dir=sort_dir,
                section=section,
                facet=facet,
                target_dataflow_id=target_dataflow_id,
                analytics_context=context,
            )
        if identity_request is not None:
            identity_request.assert_current()
            payload = {**payload, "identity_version": identity_request.version}
        return payload

    if session is None or environment_id is None:
        return {**compute(analytics_context), "cache": "duckdb"}

    def serve(context):
        identity_request = DestinationIdentityRequest.capture(session, environment_id, context) if filters.get("investigateKind") == "physical_destination" else None
        with timed_stage("cache_key"):
            key = monitoring_dataflow_registry_cache_key(
                session,
                environment_id=environment_id,
                paths=paths,
                filters=filters,
                timezone_label=timezone_label or "UTC",
                limit=limit,
                offset=offset,
                sort_by=sort_by,
                sort_dir=sort_dir,
                target_dataflow_id=target_dataflow_id,
                section=section,
                facet=facet,
                profile_scope="filtered",
                analytics_generation=context[2],
                identity_version=identity_request.version if identity_request is not None else None,
            )
        provider = result_cache_provider()

        def produce():
            record_cache_outcome(False)
            return compute(context, identity_request)

        with timed_stage("cache_service"):
            result, hit = get_or_compute(provider.store, provider.coordinator, key, produce)
        record_cache_outcome(hit)
        if identity_request is not None:
            identity_request.assert_current()
        return {**result.payload, "cache": "result-cache" if result.stored else "duckdb"}

    if analytics_context is not None:
        return serve(analytics_context)
    with analytics_reader(paths, snapshot=True) as context:
        return serve(context)
