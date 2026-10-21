from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.sources.repository import monitoring_admission
from datacoolie_studio.domains.monitoring.page_contracts import (
    DATAFLOW_REGISTRY_PRODUCER_VERSION,
    DATAFLOW_REGISTRY_SECTIONS,
    FRESHNESS_REGISTRY_PRODUCER_VERSION,
    MONITORING_PAGES,
    MULTI_VALUE_FILTERS,
    PERFORMANCE_PAGE_PRODUCER_VERSION,
    VOLUME_PAGE_PRODUCER_VERSION,
    JOBS_PAGE_PRODUCER_VERSION,
    MAINTENANCE_PAGE_PRODUCER_VERSION,
    OVERVIEW_PAGE_PRODUCER_VERSION,
    DIAGNOSTICS_PAGE_PRODUCER_VERSION,
    DIAGNOSTICS_EVIDENCE_PRODUCER_VERSION,
    PRODUCER_VERSION,
    RESPONSE_VERSION,
)
from datacoolie_studio.domains.monitoring.page_dispatch import build_monitoring_page
from datacoolie_studio.domains.monitoring.evidence_hydration import (
    CACHE_GENERATION_FIELD,
    CACHE_INPUT_FINGERPRINT_FIELD,
    EvidenceGenerationMismatch,
    MAX_HYDRATION_ROWS,
    cache_payload,
    hydrate_payload,
)
from datacoolie_studio.domains.monitoring.query_service import monitoring_input_fingerprint
from datacoolie_studio.domains.read_models.cache import fingerprint
from datacoolie_studio.domains.read_models.contracts import ResultCacheKey, get_or_compute
from datacoolie_studio.domains.read_models.keys import monitoring_page as monitoring_page_key
from datacoolie_studio.domains.read_models.provider import result_cache_provider


def monitoring_page(
    paths: list[EnvironmentSource],
    page: str,
    filters: dict[str, str] | None = None,
    session: Session | None = None,
    timezone_info: tzinfo | None = None,
    timezone_label: str | None = None,
    timezone_source: str = "server_default",
    environment_id: int | None = None,
    cache_key: ResultCacheKey | None = None,
    analytics_context: tuple[Any, list[int], str] | None = None,
    identity_request=None,
) -> dict[str, Any]:
    resolved_environment_id = environment_id or (getattr(paths[0], "environment_id", None) if paths else None)
    needs_identity = page == 'maintenance' or (filters or {}).get('investigateKind') == 'physical_destination'
    if needs_identity and identity_request is None and session is not None and resolved_environment_id is not None:
        from datacoolie_studio.domains.monitoring.identity_context import DestinationIdentityRequest
        if analytics_context is None:
            from datacoolie_studio.domains.monitoring.context import reader
            with reader(paths, snapshot=True) as context:
                request = DestinationIdentityRequest.capture(session, resolved_environment_id, context)
                return monitoring_page(paths, page, filters, session, timezone_info,
                    timezone_label, timezone_source, resolved_environment_id,
                    None, context, request)
        identity_request = DestinationIdentityRequest.capture(session, resolved_environment_id, analytics_context)
    if session is not None and resolved_environment_id is not None:
        key = cache_key or monitoring_page_cache_key(
            session,
            environment_id=resolved_environment_id,
            paths=paths,
            page=page,
            filters=filters or {},
            timezone_label=timezone_label or "UTC",
            identity_version=identity_request.version if identity_request is not None else None,
        )
        provider = result_cache_provider()
        def produce() -> dict[str, Any]:
            if identity_request is not None:
                identity_request.prepare()
            built = build_monitoring_page(
                paths,
                page,
                filters,
                timezone_info,
                timezone_label,
                timezone_source,
                analytics_context,
                identity_request,
            )
            if identity_request is not None:
                identity_request.assert_current()
                built = {**built, "summary": {**built.get("summary", {}), "identity_version": identity_request.version}}
            return cache_payload(
                built,
                generation=(analytics_context[2] if analytics_context is not None else key.input_fingerprint),
                input_fingerprint=key.input_fingerprint,
            )

        result, _ = get_or_compute(
            provider.store,
            provider.coordinator,
            key,
            produce,
        )
        payload = result.payload
        if analytics_context is not None:
            try:
                payload = hydrate_payload(
                    payload,
                    analytics_context,
                    expected_generation=analytics_context[2],
                    expected_input_fingerprint=key.input_fingerprint,
                    max_rows=MAX_HYDRATION_ROWS,
                )
            except EvidenceGenerationMismatch:
                # A cache entry can outlive the DuckDB reader generation even
                # when its identity has not changed yet.  Recompute under the
                # result-cache lock, then hydrate the replacement in the same
                # reader fence.
                with provider.coordinator.acquire(key.lock_identity):
                    current = provider.store.get(key)
                    if current is not None:
                        try:
                            payload = hydrate_payload(
                                current.payload,
                                analytics_context,
                                expected_generation=analytics_context[2],
                                expected_input_fingerprint=key.input_fingerprint,
                                max_rows=MAX_HYDRATION_ROWS,
                            )
                        except EvidenceGenerationMismatch:
                            generation = provider.store.generation(key)
                            safe_payload = produce()
                            stored = provider.store.put(
                                key,
                                safe_payload,
                                expected_generation=generation,
                            )
                            payload = hydrate_payload(
                                stored.payload,
                                analytics_context,
                                expected_generation=analytics_context[2],
                                expected_input_fingerprint=key.input_fingerprint,
                                max_rows=MAX_HYDRATION_ROWS,
                            )
                    else:
                        generation = provider.store.generation(key)
                        safe_payload = produce()
                        stored = provider.store.put(
                            key,
                            safe_payload,
                            expected_generation=generation,
                        )
                        payload = hydrate_payload(
                            stored.payload,
                            analytics_context,
                            expected_generation=analytics_context[2],
                            expected_input_fingerprint=key.input_fingerprint,
                            max_rows=MAX_HYDRATION_ROWS,
                        )
        else:
            # Keep the legacy service call usable without a route-owned reader
            # while ensuring a cache hit cannot expose an old raw copy.
            safe = cache_payload(
                payload,
                generation=key.input_fingerprint,
                input_fingerprint=key.input_fingerprint,
            )
            payload = {
                key_name: value
                for key_name, value in safe.items()
                if key_name not in {CACHE_GENERATION_FIELD, CACHE_INPUT_FINGERPRINT_FIELD}
            }
        if identity_request is not None:
            identity_request.assert_current()
        return _with_admission(payload, monitoring_admission(session, paths))
    return build_monitoring_page(
        paths,
        page,
        filters,
        timezone_info,
        timezone_label,
        timezone_source,
        analytics_context,
        identity_request,
    )


def monitoring_page_cache_key(
    session: Session,
    *,
    environment_id: int,
    paths: list[EnvironmentSource],
    page: str,
    filters: dict[str, str],
    timezone_label: str,
    now: datetime | None = None,
    input_fingerprint: str | None = None,
    identity_version: str | None = None,
) -> ResultCacheKey:
    if page not in MONITORING_PAGES:
        raise ValueError(f"Unknown Monitoring page: {page}")
    parameters = canonical_parameters(page, filters, timezone_label, now=now)
    return ResultCacheKey(
        environment_id=environment_id,
        namespace=monitoring_page_key(page),
        parameters_fingerprint=fingerprint(parameters),
        input_fingerprint=fingerprint([
            input_fingerprint or monitoring_input_fingerprint(session, paths),
            monitoring_admission(session, paths), identity_version,
        ]),
        producer_version={
            "jobs": JOBS_PAGE_PRODUCER_VERSION,
            "performance": PERFORMANCE_PAGE_PRODUCER_VERSION,
            "volume": VOLUME_PAGE_PRODUCER_VERSION,
            "maintenance": MAINTENANCE_PAGE_PRODUCER_VERSION,
            "overview": OVERVIEW_PAGE_PRODUCER_VERSION,
            "diagnostics": DIAGNOSTICS_PAGE_PRODUCER_VERSION,
        }.get(page, PRODUCER_VERSION),
    )


def monitoring_evidence_cache_key(
    session: Session,
    *,
    environment_id: int,
    paths: list[EnvironmentSource],
    page: str,
    filters: dict[str, str],
    timezone_label: str,
    view: str,
    limit: int,
    offset: int,
    sort_by: str,
    sort_dir: str,
    now: datetime | None = None,
    analytics_generation: str | None = None,
    identity_version: str | None = None,
) -> ResultCacheKey:
    if not ((page == "freshness" and view == "registry") or (page == "maintenance" and view in {"detail", "registry", "destination-runs"})):
        raise ValueError("Unsupported compact evidence cache view")
    parameters = canonical_parameters(page, filters, timezone_label, now=now)
    # Exact investigation values are lexical identities.  Restore the raw
    # value after the shared page canonicalizer's display-filter trimming so
    # padded IDs cannot collide in the registry cache.
    canonical_filters = dict(parameters["filters"])
    if filters.get("investigateValue") is not None:
        canonical_filters["investigateValue"] = str(filters["investigateValue"])
    if filters.get('_destinationTarget') is not None:
        canonical_filters['_destinationTarget']=str(filters['_destinationTarget'])
    parameters = {
        **parameters,
        "filters": canonical_filters,
        "view": view,
        "limit": int(limit),
        "offset": int(offset),
        "sort_by": sort_by,
        "sort_dir": sort_dir,
    }
    admission = monitoring_admission(session, paths)
    source_fingerprint = monitoring_input_fingerprint(session, paths)
    return ResultCacheKey(
        environment_id=environment_id,
        namespace=f"{monitoring_page_key(page)}.evidence.{view}",
        parameters_fingerprint=fingerprint(parameters),
        input_fingerprint=fingerprint([source_fingerprint, analytics_generation, admission, identity_version]),
        producer_version=MAINTENANCE_PAGE_PRODUCER_VERSION if page == "maintenance" else FRESHNESS_REGISTRY_PRODUCER_VERSION,
    )


def monitoring_diagnostics_evidence_cache_key(
    session: Session,
    *,
    environment_id: int,
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_label: str,
    as_of: datetime,
    view: str,
    finding_group: str | None,
    limit: int,
    offset: int,
    sort_by: str | None,
    sort_dir: str | None,
    analytics_generation: str,
    kind_filter: str | None = None,
) -> ResultCacheKey:
    """Key a Diagnostics group/object page by selector, scope, anchor and generation."""

    parameters = canonical_parameters("diagnostics", filters, timezone_label, now=as_of)
    canonical_filters = dict(parameters["filters"])
    if filters.get("investigateValue") is not None:
        # Exact investigation values are lexical identities, not display filters.
        canonical_filters["investigateValue"] = str(filters["investigateValue"])
    parameters = {
        **parameters,
        "filters": canonical_filters,
        "view": view,
        "finding_group": finding_group,
        "kind_filter": kind_filter,
        "limit": int(limit),
        "offset": int(offset),
        "sort_by": sort_by,
        "sort_dir": sort_dir,
    }
    return ResultCacheKey(
        environment_id=environment_id,
        namespace=f"{monitoring_page_key('diagnostics')}.evidence.{view}",
        parameters_fingerprint=fingerprint(parameters),
        input_fingerprint=fingerprint([
            monitoring_input_fingerprint(session, paths),
            analytics_generation,
            monitoring_admission(session, paths),
        ]),
        producer_version=DIAGNOSTICS_EVIDENCE_PRODUCER_VERSION,
    )


def monitoring_dataflow_registry_cache_key(
    session: Session,
    *,
    environment_id: int,
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    timezone_label: str,
    limit: int,
    offset: int,
    sort_by: str,
    sort_dir: str,
    target_dataflow_id: str | None = None,
    section: str = "overview",
    facet: str = "all",
    profile_scope: str | None = None,
    now: datetime | None = None,
    analytics_generation: str | None = None,
    identity_version: str | None = None,
) -> ResultCacheKey:
    """Build a generation-fenced key for the shared environment/dataflow registry."""

    if section not in DATAFLOW_REGISTRY_SECTIONS:
        raise ValueError(f"Unknown dataflow registry section: {section}")

    parameters = canonical_parameters("dataflows", filters, timezone_label, now=now)
    canonical_filters = dict(parameters["filters"])
    if filters.get("investigateValue") is not None:
        canonical_filters["investigateValue"] = str(filters["investigateValue"])
    if filters.get('_destinationTarget') is not None:
        canonical_filters['_destinationTarget']=str(filters['_destinationTarget'])
    parameters = {
        **parameters,
        "filters": canonical_filters,
        "target_dataflow_id": target_dataflow_id,
        "section": section,
        "facet": facet,
        # All sections share the active comparison filters.
        "profile_scope": profile_scope or "filtered",
        "limit": int(limit),
        "offset": int(offset),
        "sort_by": sort_by,
        "sort_dir": sort_dir,
    }
    admission = monitoring_admission(session, paths)
    # The enclosing registry reader validated the same admitted population in
    # its snapshot. Reopening a reader repeats work and could capture a newer
    # generation than the SQL/hydration snapshot.
    source_fingerprint = analytics_generation if analytics_generation is not None else monitoring_input_fingerprint(session, paths)
    return ResultCacheKey(
        environment_id=environment_id,
        namespace="monitoring.dataflow-registry",
        parameters_fingerprint=fingerprint(parameters),
        input_fingerprint=fingerprint([source_fingerprint, analytics_generation, admission, identity_version]),
        producer_version=DATAFLOW_REGISTRY_PRODUCER_VERSION,
    )


def _with_admission(payload: dict[str, Any], admission: dict) -> dict[str, Any]:
    result = {**payload, "log_admission": admission}
    if admission["excluded"]:
        result["errors"] = [*payload.get("errors", []), {
            "code": "unsupported_log_schema", "severity": "warning",
            "message": f"Excluded {admission['excluded']} records outside log schema v6",
        }]
        if isinstance(payload.get("health"), dict):
            status = payload["health"].get("status")
            result["health"] = {**payload["health"], "status": status if status in {"bad", "critical", "error"} else "warning", "label": payload["health"].get("label") if status in {"bad", "critical", "error"} else "Schema exclusions",
                                "reasons": [*payload["health"].get("reasons", []), "Unsupported log records were excluded"]}
    return result


def monitoring_page_etag(key: ResultCacheKey) -> str:
    return f'"{fingerprint([*key.identity, RESPONSE_VERSION])}"'


def canonical_parameters(
    page: str,
    filters: dict[str, str],
    timezone_label: str,
    *,
    now: datetime | None,
) -> dict[str, Any]:
    canonical_filters: dict[str, str] = {}
    for name, raw_value in sorted(filters.items()):
        value = str(raw_value) if name == "log_session_id" and raw_value is not None else str(raw_value or "").strip()
        if name in MULTI_VALUE_FILTERS and value:
            value = "|".join(sorted({item.strip() for item in value.split("|") if item.strip()}))
        canonical_filters[name] = value
    range_value = canonical_filters.get("range", "").lower()
    anchor = None
    if page == "freshness" or range_value in {"24h", "3d", "7d", "30d", "90d", "today"}:
        try:
            anchor_timezone = ZoneInfo(timezone_label)
        except ZoneInfoNotFoundError:
            anchor_timezone = timezone.utc
        current = (now or datetime.now(timezone.utc)).astimezone(anchor_timezone)
        anchor = (
            current.replace(second=0, microsecond=0).isoformat()
            if page == "freshness"
            else current.replace(minute=0, second=0, microsecond=0).isoformat()
            if range_value == "24h"
            else current.date().isoformat()
        )
    return {
        "page": page,
        "filters": canonical_filters,
        "timezone": timezone_label,
        "window_anchor": anchor,
    }
