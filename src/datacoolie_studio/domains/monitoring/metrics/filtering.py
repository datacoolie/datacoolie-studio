from __future__ import annotations

from datacoolie_studio.domains.monitoring.dimensions import dimension_key

from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

from datacoolie_studio.core.time import parse_utc_datetime
from datacoolie_studio.domains.monitoring.metrics.time_buckets import row_timestamp


def filter_log_rows(
    rows: list[dict[str, Any]],
    filters: dict[str, str],
    include_dataflow_filters: bool,
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if matches_log_filters(row, filters, include_dataflow_filters)
    ]


def normalize_monitoring_filters_for_timezone(
    filters: dict[str, str],
    timezone_info: tzinfo | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    normalized = dict(filters)
    range_value = (normalized.get("range") or "").strip().lower()
    active_timezone = timezone_info or timezone.utc
    now_value = now or datetime.now(active_timezone)
    if now_value.tzinfo is None:
        now_value = now_value.replace(tzinfo=active_timezone)
    else:
        now_value = now_value.astimezone(active_timezone)
    if range_value in {"24h", "3d", "7d", "30d", "90d"}:
        days = {"24h": 1, "3d": 3, "7d": 7, "30d": 30, "90d": 90}[range_value]
        boundary = (
            now_value.replace(minute=0, second=0, microsecond=0)
            if range_value == "24h"
            else now_value.replace(hour=0, minute=0, second=0, microsecond=0)
        )
        normalized["_relativeStartTime"] = (
            (boundary - timedelta(days=days)).astimezone(timezone.utc).isoformat()
        )
        return normalized
    if range_value != "today":
        return normalized

    start_local = now_value.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1) - timedelta(microseconds=1)

    normalized["range"] = "custom"
    normalized["startTime"] = start_local.astimezone(timezone.utc).isoformat()
    normalized["endTime"] = end_local.astimezone(timezone.utc).isoformat()
    return normalized


def matches_log_filters(
    row: dict[str, Any], filters: dict[str, str], include_dataflow_filters: bool
) -> bool:
    range_value = filters.get("range")
    if range_value in {"24h", "3d", "7d", "30d", "90d"}:
        days = {"24h": 1, "3d": 3, "7d": 7, "30d": 30, "90d": 90}[range_value]
        timestamp = row_timestamp(row)
        if timestamp is None:
            return False
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        relative_start = parse_utc_datetime(filters.get("_relativeStartTime"))
        cutoff = relative_start or datetime.now(timezone.utc) - timedelta(days=days)
        if timestamp.astimezone(timezone.utc) < cutoff:
            return False
    elif range_value == "custom":
        timestamp = row_timestamp(row)
        if timestamp is None:
            return False
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        timestamp = timestamp.astimezone(timezone.utc)
        start_time = parse_utc_datetime(filters.get("startTime"))
        end_time = parse_utc_datetime(filters.get("endTime"))
        if start_time is not None and timestamp < start_time:
            return False
        if end_time is not None and timestamp > end_time:
            return False

    common_filters = {
        "status": "status",
        "engine": "engine_name",
        "provider": "metadata_provider_name",
    }
    for filter_key, row_key in common_filters.items():
        if not matches_filter_value(row.get(row_key), filters.get(filter_key)):
            return False

    if include_dataflow_filters:
        dataflow_filters = {
            "stage": "stage",
            "sourceType": "source_connection_type",
            "destinationType": "destination_connection_type",
            "loadType": "destination_load_type",
            "operationType": "operation_type",
        }
        for filter_key, row_key in dataflow_filters.items():
            if not matches_filter_value(row.get(row_key), filters.get(filter_key)):
                return False
        if not matches_connection(row, filters.get("connection")):
            return False

    search = (filters.get("search") or "").strip().lower()
    if search and not row_matches_search(row, search, include_dataflow_filters):
        return False
    if not matches_investigation(row, filters, include_dataflow_filters):
        return False
    return True


def row_matches_search(
    row: dict[str, Any], search: str, include_dataflow_filters: bool
) -> bool:
    if any(search in str(value or "").lower() for value in row.values()):
        return True
    if not include_dataflow_filters:
        return False
    normalized = search.replace("`", "")
    candidates = [
        norm(row.get("source_full_table")),
        norm(row.get("destination_full_table")),
        maintenance_target_identity(row).lower().replace("`", ""),
    ]
    return any(normalized in candidate for candidate in candidates)


def matches_investigation(
    row: dict[str, Any], filters: dict[str, str], include_dataflow_filters: bool
) -> bool:
    # Session selection is an independent tri-state filter.  It must still
    # apply when no investigation kind/value is supplied (the normal page
    # filters use that shape), and the legacy investigateLogSessionId alias is
    # deliberately ignored when the authoritative scope is ``all``.
    session_scope = filters.get("log_session_scope", "all")
    if session_scope not in {"all", "null", "exact"}:
        raise ValueError("Unsupported log_session_scope")
    if session_scope == "null" and row.get("log_session_id") is not None:
        return False
    if session_scope == "exact":
        if "log_session_id" not in filters or filters.get("log_session_id") is None:
            raise ValueError("log_session_id is required for exact session scope")
        if row.get("log_session_id") != filters.get("log_session_id"):
            return False

    kind = (filters.get("investigateKind") or "").strip()
    value = (filters.get("investigateValue") or "")
    if not kind or (value == "" and kind not in {"dataflow_id", "dataflow_run_id", "job_id", "declared_destination"}):
        return True
    source_id = filters.get("investigateSourceId")
    if source_id not in (None, "") and str(row.get("_source_id")) != str(source_id):
        return False
    normalized = value.lower().replace("`", "")
    if kind == "job_id":
        return row.get("job_id") == value
    if not include_dataflow_filters:
        return True
    if kind == "dataflow_run_id":
        return row.get("dataflow_run_id") == value
    if kind == "dataflow_id":
        return row.get("dataflow_id") == value
    if kind == "declared_destination":
        import json
        from datacoolie_studio.domains.monitoring.metrics.investigation import DECLARED_DESTINATION_FIELDS
        return json.dumps([row.get(field) for field in DECLARED_DESTINATION_FIELDS], separators=(",", ":"), ensure_ascii=False) == value
    if kind == "dataflow":
        return (
            row.get("dataflow_id") == value
            or norm(row.get("dataflow_name")) == normalized
        )
    if kind == "destination_table":
        target_identity = maintenance_target_identity(row).lower().replace("`", "")
        return normalized in {
            target_identity,
            norm(row.get("destination_full_table")),
            norm(row.get("destination_table")),
            norm(row.get("destination_path")),
        }
    return True


def norm(value: Any) -> str:
    return str(value or "").replace("`", "").lower()


def matches_filter_value(value: Any, filter_value: str | None) -> bool:
    selected = split_filter_values(filter_value)
    return not selected or dimension_key(value) in selected


def matches_connection(row: dict[str, Any], filter_value: str | None) -> bool:
    selected = split_filter_values(filter_value)
    if not selected:
        return True
    return (
        dimension_key(row.get("source_name")) in selected
        or dimension_key(row.get("destination_name")) in selected
    )


def split_filter_values(value: str | None) -> list[str]:
    if not value or value == "all":
        return []
    return [item.strip() for item in value.split("|") if item.strip()]


def maintenance_target_identity(row: dict[str, Any]) -> str:
    connection = str(
        row.get("destination_name")
        or row.get("destination_connection_name")
        or "unknown"
    ).strip()
    table = qualified_table_name(
        row.get("destination_catalog"),
        row.get("destination_database"),
        row.get("destination_schema"),
        row.get("destination_table"),
    )
    if table:
        return f"{connection}::{table}"
    for key in ("destination_full_table", "destination_table", "destination_path"):
        value = row.get(key)
        if value:
            return f"{connection}::{str(value)}"
    return f"{connection}:unknown"


def qualified_table_name(*parts: object) -> str:
    values = [str(part).strip("` ") for part in parts if part not in (None, "")]
    return ".".join(values)
