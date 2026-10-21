from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from datacoolie_studio.api.v1.contracts.monitoring_values import exact_metric_transport
from datacoolie_studio.api.v1.contracts.monitoring import (
    MonitoringPageResponse,
)
from datacoolie_studio.api.v1.contracts.monitoring_queries import (
    MonitoringDataflowRecordsResponse,
    MonitoringDataflowRegistryResponse,
    MonitoringDiagnosticsEvidenceResponse,
    MonitoringEvidenceResponse,
    MonitoringFilterOptionsResponse,
    MonitoringJobRecordsResponse,
    MonitoringLatestStatusResponse,
    MonitoringSystemLogsResponse,
    RegistrySection,
    RegistryExecutionSort,
    RegistryRuntimeSort,
    RegistryDetailSort,
)
from datacoolie_studio.api.v1.dependencies import get_credential_secret_store
from datacoolie_studio.core.time import parse_utc_datetime
from datacoolie_studio.core.request_timing import RequestTiming, request_timing_scope, timed_stage
from datacoolie_studio.api.v1.system_log_download import (
    PreparedSystemLogStreamingResponse,
)
from datacoolie_studio.db.session import (
    ensure_workspace_transaction,
    get_session,
    refresh_workspace_reads,
)
from datacoolie_studio.domains.credentials.store import CredentialSecretStore
from datacoolie_studio.domains.monitoring.query_service import dataflow_logs, job_logs, latest_status, latest_status_etag
from datacoolie_studio.domains.monitoring.filter_options import monitoring_filter_options
from datacoolie_studio.domains.monitoring.page_service import (
    DiagnosticsGenerationChanged,
    FindingRequestError,
    monitoring_diagnostics_evidence,
    monitoring_page,
    monitoring_page_cache_key,
    monitoring_dataflow_registry,
    monitoring_page_evidence,
    monitoring_page_etag,
    public_monitoring_page,
)
from datacoolie_studio.domains.logs.system_logs import system_log_records
from datacoolie_studio.domains.logs.system_log_download import (
    SystemLogDownloadError,
    prepare_system_log_download,
)
from datacoolie_studio.domains.monitoring.context import reader as analytics_reader
from datacoolie_studio.domains.monitoring.identity_context import DestinationIdentityRequest
from datacoolie_studio.domains.monitoring.read_models.dataflow_registry import dataflow_registry_sort_fields
from datacoolie_studio.domains.monitoring.evidence_hydration import hydrate_rows
from datacoolie_studio.domains.read_models.cache import fingerprint
from datacoolie_studio.domains.monitoring.page_contracts import MAINTENANCE_PAGE_PRODUCER_VERSION
from datacoolie_studio.domains.monitoring.metrics.filtering import normalize_monitoring_filters_for_timezone
from datacoolie_studio.domains.analytics.record_codec import root_column_types
from datacoolie_studio.domains.sources import repository as source_repository
from datacoolie_studio.domains.studio_settings import service as studio_settings

router = APIRouter(tags=["monitoring"])


async def _registry_request_timing(request: Request):
    # The async dependency owns the context copied into FastAPI's sync worker.
    with request_timing_scope() as timing:
        request.state.registry_request_timing = timing
        try:
            yield timing
        except HTTPException as error:
            error.headers = {**(error.headers or {}), "Server-Timing": timing.server_timing()}
            raise

InvestigationKind = Literal["", "job_id", "dataflow_run_id", "dataflow_id", "declared_destination", "physical_destination", "dataflow", "destination_table"]

_DATAFLOW_ROUTE_FIELDS = (
    "dataflow_run_id", "dataflow_id", "dataflow_name", "dataflow_description",
    "workspace_id", "stage", "operation_type", "source_name",
    "source_connection_type", "source_format", "source_catalog", "source_database",
    "source_schema", "source_full_table", "source_table", "source_path",
    "destination_name", "destination_connection_type", "destination_format",
    "destination_catalog", "destination_database", "destination_schema",
    "destination_full_table", "destination_table", "destination_path",
    "destination_load_type",
)
_DATAFLOW_CONFIGURATION_FIELDS = tuple(
    field for field in root_column_types("dataflow_run_log")
    if field in {
        "dataflow_run_id", "dataflow_id", "workspace_id", "dataflow_name",
        "dataflow_description", "stage", "group_number", "execution_order",
        "processing_mode", "is_active", "datacoolie_version", "operation_type",
        "destination_operation_type", "configure",
    } or field.startswith(("source_", "transform_", "destination_"))
)


@router.get(
    "/environments/{environment_id}/monitoring/dataflow-registry",
    response_model=MonitoringDataflowRegistryResponse,
    response_model_exclude_unset=True,
)
def get_monitoring_dataflow_registry(
    environment_id: int,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sortBy: str = "latest_run_at",
    sortDir: Literal["asc", "desc"] = "desc",
    facet: Literal["all", "freshness", "performance", "volume"] = "all",
    range_value: str = Query("30d", alias="range"),
    grain: str = "auto",
    startTime: str = "",
    endTime: str = "",
    status: str = "all",
    stage: str = "all",
    connection: str = "all",
    engine: str = "all",
    provider: str = "all",
    sourceType: str = "all",
    destinationType: str = "all",
    loadType: str = "all",
    operationType: str = "all",
    search: str = "",
    investigateKind: InvestigationKind = "",
    investigateValue: str = "",
    asOf: str = "",
    log_session_scope: Literal["all", "null", "exact"] = "all",
    log_session_id: str | None = None,
    investigateSourceId: int | None = Query(None, ge=1),
    session: Session = Depends(get_session),
    response: Response = None,
    timing: RequestTiming = Depends(_registry_request_timing),
):
    """Return one entity row per environment/dataflow for all Monitoring facets."""

    if sortBy not in dataflow_registry_sort_fields(facet):
        raise HTTPException(status_code=422, detail=f"Unsupported sort for registry facet {facet}")
    with timed_stage("sources"):
        paths = source_repository.list_sources(session, environment_id, "logs")
    timezone_context = studio_settings.studio_timezone_context(session)
    filters = _monitoring_filters(
        range_value,
        grain,
        status,
        stage,
        connection,
        engine,
        provider,
        sourceType,
        destinationType,
        loadType,
        operationType,
        search,
        startTime,
        endTime,
        investigateKind,
        investigateValue,
        investigateSourceId,
        log_session_scope,
        log_session_id,
    )
    filters, as_of = _registry_time_scope(filters, asOf, timezone_context["timezone"])
    with analytics_reader(paths, snapshot=True) as analytics_context:
        result = monitoring_dataflow_registry(
            paths,
            filters=filters,
            limit=limit,
            offset=offset,
            sort_by=sortBy,
            sort_dir=sortDir,
            facet=facet,
            session=session,
            environment_id=environment_id,
            timezone_label=timezone_context["timezone"],
            analytics_context=analytics_context,
        )
    if response is not None and isinstance(timing, RequestTiming):
        response.headers["Server-Timing"] = timing.server_timing()
    return {
        "section": "list",
        "records": result["records"],
        "errors": [],
        "summary": {
            "records": len(result["records"]),
            "total_records": result["total_records"],
            "limit": limit,
            "offset": offset,
            "cache": result.get("cache", "duckdb"),
            "analytics_generation": result.get("generation"),
            "identity_version": result.get("identity_version"),
            "as_of": as_of,
        },
    }


@router.get(
    "/environments/{environment_id}/monitoring/dataflow-registry/detail",
    response_model=MonitoringDataflowRegistryResponse,
    response_model_exclude_unset=True,
)
def get_monitoring_dataflow_registry_detail(
    environment_id: int,
    dataflowId: str = Query(..., min_length=1),
    section: RegistrySection = Query("overview"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sortBy: RegistryDetailSort | None = None,
    sortDir: Literal["asc", "desc"] = "desc",
    range_value: str = Query("30d", alias="range"),
    grain: str = "auto",
    startTime: str = "",
    endTime: str = "",
    status: str = "all",
    stage: str = "all",
    connection: str = "all",
    engine: str = "all",
    provider: str = "all",
    sourceType: str = "all",
    destinationType: str = "all",
    loadType: str = "all",
    operationType: str = "all",
    search: str = "",
    asOf: str = "",
    log_session_scope: Literal["all", "null", "exact"] = "all",
    log_session_id: str | None = None,
    investigateSourceId: int | None = Query(None, ge=1),
    session: Session = Depends(get_session),
    response: Response = None,
    timing: RequestTiming = Depends(_registry_request_timing),
):
    """Resolve one entity from the shared registry using its exact ID."""

    paged = section in {"execution", "runtime"}
    allowed_sort = get_args(RegistryRuntimeSort if section == "runtime" else RegistryExecutionSort)
    selected_sort = sortBy or ("run_count" if section == "runtime" else "event_time")
    if selected_sort not in allowed_sort:
        raise HTTPException(status_code=422, detail=f"Unsupported sort for registry section {section}")
    with timed_stage("sources"):
        paths = source_repository.list_sources(session, environment_id, "logs")
    timezone_context = studio_settings.studio_timezone_context(session)
    filters = _monitoring_filters(
        range_value,
        grain,
        status,
        stage,
        connection,
        engine,
        provider,
        sourceType,
        destinationType,
        loadType,
        operationType,
        search,
        startTime,
        endTime,
        "dataflow_id",
        dataflowId,
        investigateSourceId,
        log_session_scope,
        log_session_id,
    )
    filters, as_of = _registry_time_scope(filters, asOf, timezone_context["timezone"])
    comparison_filters = dict(filters)
    if (
        comparison_filters.get("investigateKind") == "dataflow_id"
        and comparison_filters.get("investigateValue") == dataflowId
    ):
        comparison_filters["investigateKind"] = ""
        comparison_filters["investigateValue"] = ""
    with analytics_reader(paths, snapshot=True) as analytics_context:
        result = monitoring_dataflow_registry(
            paths,
            filters=comparison_filters,
            limit=limit if paged else 1,
            offset=offset if paged else 0,
            sort_by=selected_sort if paged else "latest_run_at",
            sort_dir=sortDir if paged else "desc",
            session=session,
            environment_id=environment_id,
            timezone_label=timezone_context["timezone"],
            target_dataflow_id=dataflowId,
            section=section,
            analytics_context=analytics_context,
        )
        records = result["records"]
        if not paged and records:
            with timed_stage("profile_hydration"):
                records = _hydrate_registry_section(records, section, analytics_context)
            result = {**result, "records": records}
    if response is not None and isinstance(timing, RequestTiming):
        response.headers["Server-Timing"] = timing.server_timing()
    return {
        "section": section,
        "records": result["records"],
        "errors": [],
        "summary": {
            "records": len(result["records"]),
            "total_records": result["total_records"],
            "limit": limit if paged else 1,
            "offset": offset if paged else 0,
            "cache": result.get("cache", "duckdb"),
            "analytics_generation": result.get("generation"),
            "identity_version": result.get("identity_version"),
            "as_of": as_of,
        },
    }


@router.get(
    "/environments/{environment_id}/monitoring/pages/{page}",
    response_model=MonitoringPageResponse,
    response_model_exclude_unset=True,
)
def get_monitoring_page(
    environment_id: int,
    page: Literal["environment-overview", "overview", "jobs", "dataflows", "failures", "diagnostics", "performance", "volume", "maintenance", "freshness"],
    request: Request,
    response: Response,
    range_value: str = Query("30d", alias="range"),
    grain: str = "auto",
    startTime: str = "",
    endTime: str = "",
    status: str = "all",
    stage: str = "all",
    connection: str = "all",
    engine: str = "all",
    provider: str = "all",
    sourceType: str = "all",
    destinationType: str = "all",
    loadType: str = "all",
    operationType: str = "all",
    search: str = "",
    investigateKind: InvestigationKind = "",
    investigateValue: str = "",
    log_session_scope: Literal["all", "null", "exact"] = "all",
    log_session_id: str | None = None,
    investigateSourceId: int | None = Query(None, ge=1),
    session: Session = Depends(get_session),
):
    paths = source_repository.list_sources(session, environment_id, "logs")
    timezone_context = studio_settings.studio_timezone_context(session)
    filters = _monitoring_filters(
        range_value,
        grain,
        status,
        stage,
        connection,
        engine,
        provider,
        sourceType,
        destinationType,
        loadType,
        operationType,
        search,
        startTime,
        endTime,
        investigateKind,
        investigateValue,
        investigateSourceId,
        log_session_scope,
        log_session_id,
    )
    uses_identity = page == "maintenance" or filters.get('investigateKind') == 'physical_destination'
    with analytics_reader(paths, snapshot=uses_identity) as analytics_context:
        identity_request = DestinationIdentityRequest.capture(session, environment_id, analytics_context) if uses_identity else None
        cache_key = monitoring_page_cache_key(
            session,
            environment_id=environment_id,
            paths=paths,
            page=page,
            filters=filters,
            timezone_label=timezone_context["timezone"],
            input_fingerprint=analytics_context[2] if analytics_context is not None else None,
            identity_version=identity_request.version if identity_request is not None else None,
        )
        etag = monitoring_page_etag(cache_key)
        cache_headers = {"ETag": etag, "Cache-Control": "private, must-revalidate"}
        if request.headers.get("if-none-match") == etag:
            if identity_request is not None:
                identity_request.assert_current()
            return Response(status_code=304, headers=cache_headers)
        response.headers.update(cache_headers)
        payload = monitoring_page(
            paths,
            page=page,
            filters=filters,
            session=session,
            timezone_info=timezone_context["timezone_info"],
            timezone_label=timezone_context["timezone"],
            timezone_source=timezone_context["timezone_source"],
            environment_id=environment_id,
            cache_key=cache_key,
            analytics_context=analytics_context,
            identity_request=identity_request,
        )
    return exact_metric_transport(public_monitoring_page(page, payload))


@router.get(
    "/environments/{environment_id}/monitoring/pages/diagnostics/evidence",
    response_model=MonitoringDiagnosticsEvidenceResponse,
    response_model_exclude_unset=True,
)
def get_monitoring_diagnostics_evidence(
    environment_id: int,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    view: Literal["registry", "detail"] = "registry",
    findingGroup: str | None = Query(None, max_length=600),
    sortBy: str | None = None,
    sortDir: Literal["asc", "desc"] | None = None,
    findingKind: Literal["all", "findings", "limitations"] | None = None,
    asOf: str = "",
    expectedGeneration: str | None = None,
    range_value: str = Query("30d", alias="range"),
    grain: str = "auto",
    startTime: str = "",
    endTime: str = "",
    status: str = "all",
    stage: str = "all",
    connection: str = "all",
    engine: str = "all",
    provider: str = "all",
    sourceType: str = "all",
    destinationType: str = "all",
    loadType: str = "all",
    operationType: str = "all",
    search: str = "",
    investigateKind: InvestigationKind = "",
    investigateValue: str = "",
    log_session_scope: Literal["all", "null", "exact"] = "all",
    log_session_id: str | None = None,
    investigateSourceId: int | None = Query(None, ge=1),
    session: Session = Depends(get_session),
):
    """Page Diagnostics finding groups, or one group's affected objects."""

    anchor = parse_utc_datetime(asOf) if asOf else datetime.now(timezone.utc).replace(second=0, microsecond=0)
    if anchor is None:
        raise HTTPException(status_code=422, detail="asOf must be a valid ISO-8601 timestamp")
    paths = source_repository.list_sources(session, environment_id, "logs")
    timezone_context = studio_settings.studio_timezone_context(session)
    filters = _monitoring_filters(
        range_value, grain, status, stage, connection, engine, provider, sourceType,
        destinationType, loadType, operationType, search, startTime, endTime,
        investigateKind, investigateValue, investigateSourceId, log_session_scope,
        log_session_id,
    )
    try:
        return monitoring_diagnostics_evidence(
            paths,
            filters=filters,
            timezone_info=timezone_context["timezone_info"],
            as_of=anchor,
            view=view,
            finding_group=findingGroup,
            limit=limit,
            offset=offset,
            sort_by=sortBy,
            sort_dir=sortDir,
            session=session,
            environment_id=environment_id,
            timezone_label=timezone_context["timezone"],
            kind_filter=findingKind,
            expected_generation=expectedGeneration,
        )
    except FindingRequestError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except DiagnosticsGenerationChanged as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "analytics_generation_changed",
                "message": "Diagnostics evidence changed; reload the report.",
            },
        ) from error


@router.get(
    "/environments/{environment_id}/monitoring/pages/{page}/evidence",
    response_model=MonitoringEvidenceResponse,
    response_model_exclude_unset=True,
)
def get_monitoring_page_evidence(
    request: Request,
    response: Response,
    environment_id: int,
    page: Literal["performance", "freshness", "volume", "maintenance"],
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sortBy: str = "performance_candidate_priority",
    sortDir: str = "desc",
    view: Literal["detail", "registry", "destination-runs"] = "detail",
    expectedIdentityVersion: str | None = None,
    destinationTarget: str = "",
    range_value: str = Query("30d", alias="range"),
    grain: str = "auto",
    startTime: str = "",
    endTime: str = "",
    status: str = "all",
    stage: str = "all",
    connection: str = "all",
    engine: str = "all",
    provider: str = "all",
    sourceType: str = "all",
    destinationType: str = "all",
    loadType: str = "all",
    operationType: str = "all",
    search: str = "",
    investigateKind: InvestigationKind = "",
    investigateValue: str = "",
    log_session_scope: Literal["all", "null", "exact"] = "all",
    log_session_id: str | None = None,
    investigateSourceId: int | None = Query(None, ge=1),
    session: Session = Depends(get_session),
):
    if view == "registry" and page not in {"freshness", "maintenance"}:
        raise HTTPException(status_code=422, detail="Registry evidence is only defined for Freshness and Destinations")
    if view == "destination-runs" and (page != "maintenance" or not (destinationTarget or investigateKind == "physical_destination" and investigateValue)):
        raise HTTPException(status_code=422, detail="Destination history requires a physical destination investigation")
    paths = source_repository.list_sources(session, environment_id, "logs")
    timezone_context = studio_settings.studio_timezone_context(session)
    filters = _monitoring_filters(
        range_value,
        grain,
        status,
        stage,
        connection,
        engine,
        provider,
        sourceType,
        destinationType,
        loadType,
        operationType,
        search,
        startTime,
        endTime,
        investigateKind,
        investigateValue,
        investigateSourceId,
        log_session_scope,
        log_session_id,
    )
    if page == "maintenance" and destinationTarget:
        filters['_destinationTarget']=destinationTarget
    payload = monitoring_page_evidence(
        paths,
        page,
        filters=filters,
        timezone_info=timezone_context["timezone_info"],
        limit=limit,
        offset=offset,
        sort_by=sortBy,
        sort_dir=sortDir,
        view=view,
        session=session,
        environment_id=environment_id,
        timezone_label=timezone_context["timezone"],
        expected_identity_version=expectedIdentityVersion,
    )
    if page == "maintenance":
        # Fingerprint the compact response selected under one generation. This
        # includes the Assets identity version and current metadata fence.
        etag = f'"{fingerprint([MAINTENANCE_PAGE_PRODUCER_VERSION, filters, view, sortBy, sortDir, limit, offset, payload])}"'
        headers = {"ETag": etag, "Cache-Control": "private, must-revalidate"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        response.headers.update(headers)
    return payload


@router.get(
    "/environments/{environment_id}/monitoring/dataflows",
    response_model=MonitoringDataflowRecordsResponse,
    response_model_exclude_unset=True,
)
def get_dataflow_logs(
    environment_id: int,
    limit: int = Query(1000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    sortBy: str = "start_time",
    sortDir: str = "desc",
    range_value: str = Query("30d", alias="range"),
    startTime: str = "",
    endTime: str = "",
    status: str = "all",
    stage: str = "all",
    connection: str = "all",
    engine: str = "all",
    provider: str = "all",
    sourceType: str = "all",
    destinationType: str = "all",
    loadType: str = "all",
    operationType: str = "all",
    search: str = "",
    investigateKind: InvestigationKind = "",
    investigateValue: str = "",
    log_session_scope: Literal["all", "null", "exact"] = "all",
    log_session_id: str | None = None,
    investigateSourceId: int | None = Query(None, ge=1),
    session: Session = Depends(get_session),
):
    paths = source_repository.list_sources(session, environment_id, "logs")
    timezone_context = studio_settings.studio_timezone_context(session)
    return dataflow_logs(
        paths,
        limit=limit,
        offset=offset,
        sort_by=sortBy,
        sort_dir=sortDir,
        filters=_monitoring_filters(
            range_value,
            "auto",
            status,
            stage,
            connection,
            engine,
            provider,
            sourceType,
            destinationType,
            loadType,
            operationType,
            search,
            startTime,
            endTime,
            investigateKind,
            investigateValue,
                investigateSourceId,
            log_session_scope,
            log_session_id,
        ),
        session=session,
        timezone_info=timezone_context["timezone_info"],
    )


@router.get(
    "/environments/{environment_id}/monitoring/jobs",
    response_model=MonitoringJobRecordsResponse,
    response_model_exclude_unset=True,
)
def get_job_logs(
    environment_id: int,
    limit: int = Query(1000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    sortBy: str = "start_time",
    sortDir: str = "desc",
    range_value: str = Query("30d", alias="range"),
    startTime: str = "",
    endTime: str = "",
    status: str = "all",
    stage: str = "all",
    connection: str = "all",
    engine: str = "all",
    provider: str = "all",
    sourceType: str = "all",
    destinationType: str = "all",
    loadType: str = "all",
    operationType: str = "all",
    search: str = "",
    investigateKind: InvestigationKind = "",
    investigateValue: str = "",
    log_session_scope: Literal["all", "null", "exact"] = "all",
    log_session_id: str | None = None,
    investigateSourceId: int | None = Query(None, ge=1),
    session: Session = Depends(get_session),
):
    paths = source_repository.list_sources(session, environment_id, "logs")
    timezone_context = studio_settings.studio_timezone_context(session)
    return job_logs(
        paths,
        limit=limit,
        offset=offset,
        sort_by=sortBy,
        sort_dir=sortDir,
        filters=_monitoring_filters(
            range_value,
            "auto",
            status,
            stage,
            connection,
            engine,
            provider,
            sourceType,
            destinationType,
            loadType,
            operationType,
            search,
            startTime,
            endTime,
            investigateKind,
            investigateValue,
                investigateSourceId,
            log_session_scope,
            log_session_id,
        ),
        session=session,
        timezone_info=timezone_context["timezone_info"],
    )


@router.get(
    "/environments/{environment_id}/monitoring/filter-options",
    response_model=MonitoringFilterOptionsResponse,
    response_model_exclude_unset=True,
)
def get_monitoring_filter_options(
    environment_id: int,
    range_value: Literal["all", "24h", "3d", "7d", "30d", "90d", "custom", "today"] = Query("all", alias="range"),
    startTime: str = "",
    endTime: str = "",
    session: Session = Depends(get_session),
):
    paths = source_repository.list_sources(session, environment_id, "logs")
    timezone_context = studio_settings.studio_timezone_context(session)
    return monitoring_filter_options(
        paths, session=session, environment_id=environment_id,
        filters={"range": range_value, "startTime": startTime, "endTime": endTime},
        timezone_info=timezone_context["timezone_info"],
        timezone_label=timezone_context["timezone"],
    )


@router.get(
    "/environments/{environment_id}/monitoring/latest-status",
    response_model=MonitoringLatestStatusResponse,
    response_model_exclude_unset=True,
)
def get_latest_status(environment_id: int, request: Request, response: Response, session: Session = Depends(get_session)):
    paths = source_repository.list_sources(session, environment_id, "logs")
    etag = latest_status_etag(session, paths)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "private, must-revalidate"})
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, must-revalidate"
    return latest_status(paths, session=session)


@router.get(
    "/environments/{environment_id}/monitoring/system-logs",
    response_model=MonitoringSystemLogsResponse,
    response_model_exclude_unset=True,
)
def get_system_logs(
    environment_id: int,
    job_id: str,
    source_id: int | None = Query(None, ge=1),
    log_session_id: str | None = None,
    log_session_scope: Literal["all", "null", "exact"] = "all",
    dataflow_id: str = "",
    dataflow_run_id: str = "",
    event_name: str = "",
    include_dataflow_logs: bool = False,
    level: str = "",
    q: str = "",
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    if log_session_scope == "exact" and log_session_id is None:
        raise HTTPException(status_code=422, detail="Exact session scope requires log_session_id")
    paths = source_repository.list_sources(session, environment_id, "logs")
    return system_log_records(
        session,
        paths,
        job_id=job_id,
        source_id=source_id,
        log_session_id=log_session_id,
        log_session_scope=log_session_scope,
        dataflow_id=dataflow_id or None,
        dataflow_run_id=dataflow_run_id or None,
        event_name=event_name or None,
        include_dataflow_logs=include_dataflow_logs,
        level=level or None,
        q=q or None,
        limit=limit,
        offset=offset,
        secret_store=secret_store,
    )


@router.get(
    "/environments/{environment_id}/monitoring/system-logs/download",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Original system log lines as a binary JSONL attachment",
            "content": {
                "application/x-ndjson": {
                    "schema": {"type": "string", "format": "binary"},
                }
            },
        }
    },
)
def download_system_logs(
    environment_id: int,
    job_id: str,
    source_id: int | None = Query(None, ge=1),
    log_session_id: str | None = None,
    log_session_scope: Literal["all", "null", "exact"] = "all",
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    if log_session_scope == "exact" and log_session_id is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "exact_session_required",
                "message": "Exact session scope requires log_session_id",
            },
        )
    ensure_workspace_transaction(session)
    paths = source_repository.list_sources(session, environment_id, "logs")
    try:
        prepared = prepare_system_log_download(
            session,
            paths,
            job_id=job_id,
            source_id=source_id,
            log_session_id=log_session_id,
            log_session_scope=log_session_scope,
            secret_store=secret_store,
        )
    except SystemLogDownloadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc

    try:
        # Manifest paging has completed before the response is returned. End
        # the read snapshot so it cannot remain open for the download stream.
        refresh_workspace_reads(session)
    except Exception:
        prepared.close()
        raise
    return PreparedSystemLogStreamingResponse(prepared)


def _registry_time_scope(
    filters: dict[str, str],
    as_of_value: str,
    timezone_label: str,
) -> tuple[dict[str, str], str]:
    """Resolve relative-window bounds and freshness age from one request anchor."""

    anchor = parse_utc_datetime(as_of_value) if as_of_value else datetime.now(timezone.utc).replace(second=0, microsecond=0)
    if anchor is None:
        raise HTTPException(status_code=422, detail="asOf must be a valid ISO-8601 timestamp")
    try:
        timezone_info = ZoneInfo(timezone_label)
    except ZoneInfoNotFoundError:
        timezone_info = timezone.utc
    normalized = normalize_monitoring_filters_for_timezone(
        filters,
        timezone_info=timezone_info,
        now=anchor.astimezone(timezone_info),
    )
    as_of = anchor.astimezone(timezone.utc).isoformat()
    normalized["_asOf"] = as_of
    return normalized, as_of


def _hydrate_registry_section(records, section, analytics_context):
    if section == "overview":
        # Keep the all-history profile separate from filtered metric/run fields.
        output = []
        for source in records:
            row = dict(source)
            profile = row.get("profile_snapshot")
            if isinstance(profile, dict):
                selected_profile = hydrate_rows(
                    [dict(profile)], analytics_context, kind="dataflow_run_log",
                    evidence_fields=(), native_fields=_DATAFLOW_ROUTE_FIELDS, max_rows=1,
                )[0]
                if (
                    selected_profile.get("dataflow_run_id") != profile.get("dataflow_run_id")
                    or selected_profile.get("dataflow_id") != profile.get("dataflow_id")
                ):
                    raise HTTPException(status_code=409, detail="The recorded profile snapshot is no longer available")
                row["profile_snapshot"] = selected_profile
            output.append(row)
        return output
    elif section == "read-watermark":
        native_fields = ("dataflow_run_id", "read_request", "attempts")
        evidence_fields = ()
    elif section == "configuration":
        native_fields = _DATAFLOW_CONFIGURATION_FIELDS
        evidence_fields = ()
    elif section == "technical":
        native_fields = (
            "dataflow_run_id", "workspace_id", "job_num", "job_index",
            "datacoolie_version", "log_schema_version",
        )
        evidence_fields = ("latest_raw_record_json",)
    else:
        return records

    selected = []
    for source in records:
        row = dict(source)
        row["_source_id"] = row.get("latest_source_id")
        row["_file_uri"] = row.get("latest_file_uri")
        row["_file_kind"] = row.get("latest_file_kind")
        row["_record_line"] = row.get("latest_record_line")
        if section == "technical":
            row["latest_raw_record_json"] = None
        selected.append(row)

    hydrated = hydrate_rows(
        selected,
        analytics_context,
        kind="dataflow_run_log",
        evidence_fields=evidence_fields,
        native_fields=native_fields,
        max_rows=1,
    )
    for row in hydrated:
        expected_run_id = str(row.get("latest_run_id") or "")
        actual_run_id = str(row.get("dataflow_run_id") or "")
        if not expected_run_id or actual_run_id != expected_run_id:
            raise HTTPException(
                status_code=409,
                detail="The selected dataflow registry reference no longer resolves to its canonical run",
            )
        if section == "configuration" and isinstance(row.get("profile_snapshot"), dict):
            profile = row["profile_snapshot"]
            if row.get("dataflow_id") != profile.get("dataflow_id") or actual_run_id != profile.get("dataflow_run_id"):
                raise HTTPException(status_code=409, detail="The recorded configuration snapshot is no longer available")
    return hydrated


def _monitoring_filters(
    range_value: str,
    grain: str,
    status: str,
    stage: str,
    connection: str,
    engine: str,
    provider: str,
    source_type: str,
    destination_type: str,
    load_type: str,
    operation_type: str,
    search: str,
    start_time: str = "",
    end_time: str = "",
    investigate_kind: str = "",
    investigate_value: str = "",
    investigate_source_id: int | None = None,
    log_session_scope: str = "all",
    log_session_id: str | None = None,
) -> dict[str, str]:
    if log_session_scope == "exact" and log_session_id is None:
        raise HTTPException(status_code=422, detail="Exact session scope requires log_session_id")
    return {
        "log_session_scope": log_session_scope,
        "log_session_id": log_session_id if log_session_id is not None else "",
        "range": range_value,
        "grain": grain,
        "startTime": start_time,
        "endTime": end_time,
        "status": status,
        "stage": stage,
        "connection": connection,
        "engine": engine,
        "provider": provider,
        "sourceType": source_type,
        "destinationType": destination_type,
        "loadType": load_type,
        "operationType": operation_type,
        "search": search,
        "investigateKind": investigate_kind,
        "investigateValue": investigate_value,
        "investigateSourceId": str(investigate_source_id) if investigate_source_id is not None else "",
    }
