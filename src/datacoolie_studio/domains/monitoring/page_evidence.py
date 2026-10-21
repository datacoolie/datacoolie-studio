from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.diagnostics_findings import (
    parse_finding_group,
    validate_finding_request,
)
from datacoolie_studio.domains.monitoring.diagnostics_repository import diagnostics_findings_evidence
from datacoolie_studio.domains.monitoring.metrics.filtering import (
    normalize_monitoring_filters_for_timezone,
)
from datacoolie_studio.domains.monitoring.page_contracts import PERFORMANCE_EVIDENCE_SORT_FIELDS
from datacoolie_studio.domains.monitoring.evidence_hydration import (
    MAX_HYDRATION_ROWS,
    hydrate_rows,
    reader_context,
)
from datacoolie_studio.domains.monitoring.read_models.freshness import freshness_evidence_read_model
from datacoolie_studio.domains.monitoring.read_models.destinations import destination_evidence_read_model
from datacoolie_studio.domains.monitoring.read_models.performance import performance_evidence_read_model
from datacoolie_studio.domains.monitoring.read_models.volume import volume_evidence_read_model


class DiagnosticsGenerationChanged(Exception):
    """The requested analytics generation is no longer the served snapshot."""


def diagnostics_findings_page(
    paths: list[EnvironmentSource],
    *,
    filters: dict[str, str],
    timezone_info: tzinfo,
    as_of: datetime,
    view: str,
    group: str | None,
    limit: int,
    offset: int,
    sort_by: str | None,
    sort_dir: str | None,
    kind_filter: str | None = None,
    expected_generation: str | None = None,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    """Serve complete Diagnostics group or affected-object pages for one snapshot.

    Raises FindingRequestError for an unsupported mode, selector or sort.
    """

    validate_finding_request(view, group, sort_by, sort_dir, kind_filter)
    selected = parse_finding_group(group) if view == "detail" else None
    normalized_filters = normalize_monitoring_filters_for_timezone(
        filters, timezone_info=timezone_info, now=as_of.astimezone(timezone_info),
    )
    with reader_context(analytics_context, paths) as fence:
        if expected_generation is not None and fence[2] != expected_generation:
            raise DiagnosticsGenerationChanged(fence[2])
        result = diagnostics_findings_evidence(
            paths, normalized_filters, view=view, group=selected,
            limit=limit, offset=offset, sort_by=sort_by, sort_dir=sort_dir,
            kind_filter=kind_filter, analytics_context=fence,
        )
    rows = result["groups"] if view == "registry" else result["objects"]
    return {
        "mode": view,
        "group": result["group"],
        "groups": result["groups"],
        "objects": result["objects"],
        "errors": [],
        "summary": {
            "records": len(rows),
            "total_records": result["total"],
            "limit": limit,
            "offset": offset,
            "cache": "duckdb",
            "analytics_generation": result["generation"],
            "as_of": as_of.astimezone(timezone.utc).isoformat(),
        },
    }


def monitoring_page_evidence(
    paths: list[EnvironmentSource],
    page: str,
    *,
    filters: dict[str, str],
    timezone_info: tzinfo,
    limit: int,
    offset: int,
    sort_by: str,
    sort_dir: str,
    view: str = "detail",
    analytics_context: tuple[Any, list[int], str] | None = None,
    identity_request=None,
) -> dict[str, Any]:
    safe_sort_dir = sort_dir if sort_dir in {"asc", "desc"} else "desc"
    normalized_filters = normalize_monitoring_filters_for_timezone(
        filters, timezone_info=timezone_info,
    )
    readers = {
        "performance": performance_evidence_read_model,
        "freshness": freshness_evidence_read_model,
        "volume": volume_evidence_read_model,
        "maintenance": destination_evidence_read_model,
    }
    reader = readers.get(page)
    if reader is None:
        raise ValueError(f"Monitoring evidence is not defined for page: {page}")
    if view not in {"detail", "registry", "destination-runs"}:
        raise ValueError(f"Unsupported monitoring evidence view: {view}")
    if view == "registry" and page not in {"freshness", "maintenance"}:
        raise ValueError("Registry evidence is only defined for Freshness and Destinations")
    if view == "destination-runs" and (page != "maintenance" or not (normalized_filters.get('_destinationTarget') or normalized_filters.get("investigateKind") == "physical_destination" and normalized_filters.get("investigateValue"))):
        raise ValueError("Destination history requires a physical destination investigation")
    safe_sort_by = (
        sort_by
        if page != "performance" or sort_by in PERFORMANCE_EVIDENCE_SORT_FIELDS
        else "performance_candidate_priority"
    )
    with reader_context(analytics_context, paths) as analytics_fence:
        if identity_request is not None:
            identity_request.prepare()
        reader_kwargs = {
            "limit": limit,
            "offset": offset,
            "sort_by": safe_sort_by,
            "sort_dir": safe_sort_dir,
            "analytics_context": analytics_fence,
        }
        if page in {"freshness", "maintenance"}:
            reader_kwargs["view"] = view
        if page == "maintenance":
            reader_kwargs["identity_request"] = identity_request
        result = reader(paths, normalized_filters, **reader_kwargs)
        records = result["records"] if view == "registry" or page == "maintenance" else hydrate_rows(
            result["records"],
            analytics_fence,
            evidence_fields=("raw_record_json", "latest_raw_record_json", "attempt_record_json"),
            max_rows=min(MAX_HYDRATION_ROWS, max(1, int(limit))),
        )
        if identity_request is not None:
            identity_request.assert_current()
    return {
        "records": records,
        "errors": [],
        "summary": {
            "records": len(records),
            "total_records": result["total_records"],
            "limit": limit,
            "offset": offset,
            "cache": "duckdb",
            **({"analytics_generation": result.get("generation"), "identity_version": identity_request.version if identity_request is not None else result.get("identity_version")} if page == "maintenance" or identity_request is not None else {}),
        },
    }
