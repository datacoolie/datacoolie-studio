from __future__ import annotations

from collections import defaultdict
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from datacoolie_studio.core.time import parse_utc_datetime
from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.analytics.errors import AnalyticsRebuildRequired
from datacoolie_studio.domains.monitoring.log_repository import (
    cached_monitoring_summary,
    query_cached_dataflow_logs,
    query_cached_job_logs,
    query_cached_latest_dataflow_runs,
)
from datacoolie_studio.domains.monitoring.repository import (
    monitoring_filter_options_read_model,
)
from datacoolie_studio.domains.monitoring.context import (
    materialization_token as analytics_materialization_token,
)
from datacoolie_studio.domains.read_models.cache import (
    cached_read_model,
    empty_parameters_fingerprint,
    read_model_build_lock,
    read_model_generation,
    replace_read_model,
)
from datacoolie_studio.domains.read_models.keys import LINEAGE_LATEST_RUNS
from datacoolie_studio.domains.monitoring.metrics.common_projections import (
    _enrich_dataflow_run_for_investigation,
    _enrich_job_run_for_investigation,
    _normalize_monitoring_filters_for_timezone,
    _rate,
    _time_value,
)

_LATEST_RUNS_PRODUCER_VERSION = "lineage-latest-runs-v1"


def cached_environment_overview_summary(
    session: Session,
    paths: list[EnvironmentSource],
    *,
    timezone_info: tzinfo,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the SQL-backed Environment Overview Monitoring summary."""
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    local_boundary = now_utc.astimezone(timezone_info).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    cutoff = (local_boundary - timedelta(days=30)).astimezone(timezone.utc)
    timezone_name = timezone_info.key if isinstance(timezone_info, ZoneInfo) else None
    now_offset = now_utc.astimezone(timezone_info).utcoffset()
    cutoff_offset = cutoff.astimezone(timezone_info).utcoffset()
    if not timezone_name and now_offset != cutoff_offset:
        raise ValueError(
            "Environment timezone must have a stable name across the overview range"
        )
    cached = cached_monitoring_summary(
        session,
        paths,
        cutoff=cutoff,
        timezone_name=timezone_name,
        utc_offset_seconds=int(now_offset.total_seconds()) if now_offset else 0,
        local_today=now_utc.astimezone(timezone_info).date(),
    )
    if cached is None:
        raise ValueError("Environment timezone must provide a name or fixed UTC offset")

    summary, errors = cached
    succeeded = int(summary["dataflow_succeeded"] or 0)
    failed = int(summary["dataflow_failed"] or 0)
    latest_log_at = parse_utc_datetime(summary["latest_log_at"])
    return {
        "job_records": int(summary["job_records"] or 0),
        "total_failures": int(summary["total_failures"] or 0),
        "dataflow_success_rate": _rate(succeeded, succeeded + failed),
        "failed_job_windows": {
            "last7": int(summary["failed_last7"] or 0),
            "last30": int(summary["failed_last30"] or 0),
            "last365": int(summary["failed_last365"] or 0),
        },
        "active_engines": int(summary["active_engines"] or 0),
        "latest_log_at": latest_log_at.isoformat() if latest_log_at else None,
        "date_range": {
            "min": str(summary["date_min"]) if summary["date_min"] else None,
            "max": str(summary["date_max"]) if summary["date_max"] else None,
        },
        "errors": errors,
    }


def dataflow_logs(
    paths: list[EnvironmentSource],
    limit: int = 1000,
    offset: int = 0,
    filters: dict[str, str] | None = None,
    sort_by: str = "start_time",
    sort_dir: str = "desc",
    session: Session | None = None,
    timezone_info: tzinfo | None = None,
) -> dict[str, Any]:
    if session is None:
        raise ValueError("Monitoring dataflow queries require a database session")
    normalized_filters = _normalize_monitoring_filters_for_timezone(
        filters or {},
        timezone_info=timezone_info,
    )
    cached = query_cached_dataflow_logs(
        session,
        paths,
        filters=normalized_filters,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    if cached is None:
        raise _analytics_unavailable(paths)
    rows, total, errors = cached
    rows = [_enrich_dataflow_run_for_investigation(row) for row in rows]
    return {
        "records": rows,
        "errors": errors,
        "summary": {
            "records": len(rows),
            "total_records": total,
            "limit": limit,
            "offset": offset,
            "cache": "duckdb",
        },
    }


def job_logs(
    paths: list[EnvironmentSource],
    limit: int = 1000,
    offset: int = 0,
    filters: dict[str, str] | None = None,
    sort_by: str = "start_time",
    sort_dir: str = "desc",
    session: Session | None = None,
    timezone_info: tzinfo | None = None,
) -> dict[str, Any]:
    if session is None:
        raise ValueError("Monitoring job queries require a database session")
    normalized_filters = _normalize_monitoring_filters_for_timezone(
        filters or {},
        timezone_info=timezone_info,
    )
    cached = query_cached_job_logs(
        session,
        paths,
        filters=normalized_filters,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    if cached is None:
        raise _analytics_unavailable(paths)
    rows, total, errors = cached
    rows = [_enrich_job_run_for_investigation(row) for row in rows]
    return {
        "records": rows,
        "errors": errors,
        "summary": {
            "records": len(rows),
            "total_records": total,
            "limit": limit,
            "offset": offset,
            "cache": "duckdb",
        },
    }


def latest_status(
    paths: list[EnvironmentSource], session: Session | None = None
) -> dict[str, Any]:
    if session is None:
        raise ValueError("Latest Monitoring status requires a database session")
    environment_id = paths[0].environment_id if paths else None
    parameters_fingerprint = empty_parameters_fingerprint()
    input_fingerprint = (
        _latest_runs_input_fingerprint(session, paths) if session is not None else ""
    )
    if session is not None and environment_id is not None:
        cached_model = cached_read_model(
            session,
            environment_id=environment_id,
            model_key=LINEAGE_LATEST_RUNS,
            parameters_fingerprint=parameters_fingerprint,
            input_fingerprint=input_fingerprint,
            producer_version=_LATEST_RUNS_PRODUCER_VERSION,
        )
        if cached_model is not None:
            return cached_model.payload

    build_key = f"{environment_id}:{LINEAGE_LATEST_RUNS}:{parameters_fingerprint}"
    lock = (
        read_model_build_lock(build_key)
        if session is not None and environment_id is not None
        else nullcontext()
    )
    generation: str | None = None
    with lock:
        if session is not None and environment_id is not None:
            input_fingerprint = _latest_runs_input_fingerprint(session, paths)
            cached_model = cached_read_model(
                session,
                environment_id=environment_id,
                model_key=LINEAGE_LATEST_RUNS,
                parameters_fingerprint=parameters_fingerprint,
                input_fingerprint=input_fingerprint,
                producer_version=_LATEST_RUNS_PRODUCER_VERSION,
            )
            if cached_model is not None:
                return cached_model.payload
            generation = read_model_generation(
                environment_id=environment_id,
                model_key=LINEAGE_LATEST_RUNS,
                parameters_fingerprint=parameters_fingerprint,
                input_fingerprint=input_fingerprint,
                producer_version=_LATEST_RUNS_PRODUCER_VERSION,
            )
            focused = query_cached_latest_dataflow_runs(session, paths)
        else:
            focused = None
        if focused is not None:
            rows, ambiguous_names, errors = focused
        else:
            raise _analytics_unavailable(paths)
        response = _latest_runs_response(rows, errors, ambiguous_names)
        if session is not None and environment_id is not None:
            replace_read_model(
                session,
                environment_id=environment_id,
                model_key=LINEAGE_LATEST_RUNS,
                parameters_fingerprint=parameters_fingerprint,
                input_fingerprint=input_fingerprint,
                producer_version=_LATEST_RUNS_PRODUCER_VERSION,
                payload=response,
                expected_generation=generation,
            )
        return response


def latest_status_etag(session: Session, paths: list[EnvironmentSource]) -> str:
    return f'"{_latest_runs_input_fingerprint(session, paths)}:{_LATEST_RUNS_PRODUCER_VERSION}"'


def _latest_runs_response(
    rows: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    known_ambiguous_names: list[str] | None,
) -> dict[str, Any]:
    latest_by_id: dict[str, dict[str, Any]] = {}
    rows_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        dataflow_id = row.get("dataflow_id")
        if dataflow_id and (
            str(dataflow_id) not in latest_by_id
            or _is_later(row, latest_by_id[str(dataflow_id)])
        ):
            latest_by_id[str(dataflow_id)] = row
        dataflow_name = row.get("dataflow_name")
        if dataflow_name:
            rows_by_name[str(dataflow_name)].append(row)

    latest_by_name: dict[str, dict[str, Any]] = {}
    ambiguous_names = set(known_ambiguous_names or [])
    for name, items in rows_by_name.items():
        ids = {str(item["dataflow_id"]) for item in items if item.get("dataflow_id")}
        if name in ambiguous_names or len(ids) > 1:
            ambiguous_names.add(name)
            continue
        latest_by_name[name] = max(
            items,
            key=lambda item: _time_value(
                item.get("end_time") or item.get("start_time")
            ),
        )
    return {
        "latest_by_id": latest_by_id,
        "latest_by_name": latest_by_name,
        "ambiguous_names": sorted(ambiguous_names),
        "errors": errors,
    }


def monitoring_input_fingerprint(
    session: Session, paths: list[EnvironmentSource]
) -> str:
    del (
        session
    )  # Core manifests belong to sync change detection, not request cache keys.
    return analytics_materialization_token(paths)


def _latest_runs_input_fingerprint(
    session: Session, paths: list[EnvironmentSource]
) -> str:
    return monitoring_input_fingerprint(session, paths)


def monitoring_filter_options(
    paths: list[EnvironmentSource], session: Session
) -> dict[str, Any]:
    del session
    read_model = monitoring_filter_options_read_model(paths)
    values = read_model["options"]
    return {
        "options": values,
        "summary": {"source": "duckdb_filter_values", "fields": len(values)},
        "errors": [],
    }


def _is_later(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    candidate_time = candidate.get("end_time") or candidate.get("start_time")
    current_time = current.get("end_time") or current.get("start_time")
    return _time_value(candidate_time) > _time_value(current_time)


def _analytics_unavailable(paths: list[EnvironmentSource]) -> AnalyticsRebuildRequired:
    source_ids = sorted(path.id for path in paths if path.enabled)
    return AnalyticsRebuildRequired(
        "Monitoring analytics are unavailable; sync the Log sources to rebuild them",
        source_ids=source_ids,
        missing_source_ids=source_ids,
        reason="not_ready",
    )
