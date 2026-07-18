from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from datacoolie_studio.api.v1.schemas import MonitoringReportResponse
from datacoolie_studio.db.session import get_session
from datacoolie_studio.domains.monitoring.service import (
    dataflow_logs,
    job_logs,
    latest_status,
    latest_status_etag,
    monitoring_filter_options,
)
from datacoolie_studio.domains.monitoring.page_service import (
    monitoring_page,
    monitoring_page_cache_key,
    monitoring_page_evidence,
    monitoring_page_etag,
    public_monitoring_page,
)
from datacoolie_studio.domains.logs.cache import system_log_records
from datacoolie_studio.domains.studio_settings import service as studio_settings
from datacoolie_studio.domains.workspace import service as workspace

router = APIRouter(tags=["monitoring"])


@router.get("/environments/{environment_id}/monitoring/pages/{page}", response_model=MonitoringReportResponse)
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
    investigateKind: str = "",
    investigateValue: str = "",
    session: Session = Depends(get_session),
):
    paths = workspace.list_log_sources(session, environment_id)
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
    )
    cache_key = monitoring_page_cache_key(
        session,
        environment_id=environment_id,
        paths=paths,
        page=page,
        filters=filters,
        timezone_label=timezone_context["timezone"],
    )
    etag = monitoring_page_etag(cache_key)
    cache_headers = {"ETag": etag, "Cache-Control": "private, must-revalidate"}
    if request.headers.get("if-none-match") == etag:
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
    )
    return public_monitoring_page(page, payload)


@router.get("/environments/{environment_id}/monitoring/pages/{page}/evidence")
def get_monitoring_page_evidence(
    environment_id: int,
    page: Literal["performance"],
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sortBy: str = "performance_candidate_priority",
    sortDir: str = "desc",
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
    investigateKind: str = "",
    investigateValue: str = "",
    session: Session = Depends(get_session),
):
    paths = workspace.list_log_sources(session, environment_id)
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
    )
    return monitoring_page_evidence(
        paths,
        page,
        filters=filters,
        session=session,
        timezone_info=timezone_context["timezone_info"],
        timezone_label=timezone_context["timezone"],
        timezone_source=timezone_context["timezone_source"],
        environment_id=environment_id,
        limit=limit,
        offset=offset,
        sort_by=sortBy,
        sort_dir=sortDir,
    )


@router.get("/environments/{environment_id}/monitoring/dataflows")
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
    investigateKind: str = "",
    investigateValue: str = "",
    session: Session = Depends(get_session),
):
    paths = workspace.list_log_sources(session, environment_id)
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
        ),
        session=session,
        timezone_info=timezone_context["timezone_info"],
    )


@router.get("/environments/{environment_id}/monitoring/jobs")
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
    connection: str = "all",
    engine: str = "all",
    provider: str = "all",
    search: str = "",
    investigateKind: str = "",
    investigateValue: str = "",
    session: Session = Depends(get_session),
):
    paths = workspace.list_log_sources(session, environment_id)
    timezone_context = studio_settings.studio_timezone_context(session)
    return job_logs(
        paths,
        limit=limit,
        offset=offset,
        sort_by=sortBy,
        sort_dir=sortDir,
        filters={
            "range": range_value,
            "status": status,
            "connection": connection,
            "engine": engine,
            "provider": provider,
            "search": search,
            "startTime": startTime,
            "endTime": endTime,
            "investigateKind": investigateKind,
            "investigateValue": investigateValue,
        },
        session=session,
        timezone_info=timezone_context["timezone_info"],
    )


@router.get("/environments/{environment_id}/monitoring/filter-options")
def get_monitoring_filter_options(environment_id: int, session: Session = Depends(get_session)):
    paths = workspace.list_log_sources(session, environment_id)
    return monitoring_filter_options(paths, session=session)


@router.get("/environments/{environment_id}/monitoring/latest-status")
def get_latest_status(environment_id: int, request: Request, response: Response, session: Session = Depends(get_session)):
    paths = workspace.list_log_sources(session, environment_id)
    etag = latest_status_etag(session, paths)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "private, must-revalidate"})
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, must-revalidate"
    return latest_status(paths, session=session)


@router.get("/environments/{environment_id}/monitoring/system-logs")
def get_system_logs(
    environment_id: int,
    job_id: str,
    dataflow_id: str = "",
    include_dataflow_logs: bool = False,
    level: str = "",
    q: str = "",
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    paths = workspace.list_log_sources(session, environment_id)
    return system_log_records(
        session,
        paths,
        job_id=job_id,
        dataflow_id=dataflow_id or None,
        include_dataflow_logs=include_dataflow_logs,
        level=level or None,
        q=q or None,
        limit=limit,
        offset=offset,
    )


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
) -> dict[str, str]:
    return {
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
    }
