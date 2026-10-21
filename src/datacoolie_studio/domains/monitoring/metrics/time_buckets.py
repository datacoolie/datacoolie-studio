from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

from datacoolie_studio.core.time import parse_utc_datetime


DATE_GRAINS = ("hour", "day", "week", "month")


def ensure_aware(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp


def row_timestamp(row: dict[str, Any]) -> datetime | None:
    for key in ("end_time", "start_time"):
        timestamp = parse_utc_datetime(row.get(key))
        if timestamp is not None:
            return timestamp
    return None


def trend_context(
    filters: dict[str, str], rows: list[dict[str, Any]], timezone_info: tzinfo
) -> dict[str, Any]:
    requested_grain = str(filters.get("grain") or "auto").strip().lower()
    if requested_grain not in {"auto", *DATE_GRAINS}:
        requested_grain = "auto"
    start, end = trend_bounds(filters, rows, timezone_info)
    effective_grain = resolve_effective_grain(requested_grain, start, end)
    return {
        "requested_grain": requested_grain,
        "effective_grain": effective_grain,
        "timezone_info": timezone_info,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
    }


def trend_bounds(
    filters: dict[str, str], rows: list[dict[str, Any]], timezone_info: tzinfo
) -> tuple[datetime | None, datetime | None]:
    now = datetime.now(timezone.utc).astimezone(timezone_info)
    range_value = str(filters.get("range") or "30d").strip().lower()
    if range_value in {"24h", "3d", "7d", "30d", "90d"}:
        days = {"24h": 1, "3d": 3, "7d": 7, "30d": 30, "90d": 90}[range_value]
        return now - timedelta(days=days), now
    if range_value == "custom":
        start = parse_utc_datetime(filters.get("startTime"))
        end = parse_utc_datetime(filters.get("endTime"))
        if start is not None:
            start = start.astimezone(timezone_info)
        if end is not None:
            end = end.astimezone(timezone_info)
        if start is not None or end is not None:
            return start, end

    timestamps = [
        ensure_aware(timestamp).astimezone(timezone_info)
        for row in rows
        if (timestamp := row_timestamp(row)) is not None
    ]
    if timestamps:
        return min(timestamps), max(timestamps)
    return now - timedelta(days=30), now


def resolve_effective_grain(
    requested_grain: str, start: datetime | None, end: datetime | None
) -> str:
    if start is None or end is None:
        return "day" if requested_grain == "auto" else requested_grain
    if end < start:
        start, end = end, start
    minimum_grain = minimum_allowed_grain(start, end)
    if requested_grain == "auto":
        return minimum_grain
    if requested_grain not in DATE_GRAINS:
        return minimum_grain
    requested_index = DATE_GRAINS.index(requested_grain)
    minimum_index = DATE_GRAINS.index(minimum_grain)
    return DATE_GRAINS[max(requested_index, minimum_index)]


def minimum_allowed_grain(start: datetime, end: datetime) -> str:
    if end < start:
        start, end = end, start
    span_seconds = max(0, (end - start).total_seconds())
    if span_seconds <= 3 * 24 * 3600:
        return "hour"
    if span_seconds <= 90 * 86400:
        return "day"
    if span_seconds <= 365 * 86400:
        return "week"
    return "month"


def date_bucket(
    row: dict[str, Any], trend: dict[str, Any]
) -> dict[str, str | None]:
    timestamp = row_timestamp(row)
    if timestamp is None:
        return {"bucket": "unknown", "bucket_start": None, "bucket_end": None}
    timezone_info = trend.get("timezone_info") or timezone.utc
    local_time = ensure_aware(timestamp).astimezone(timezone_info)
    grain = str(trend.get("effective_grain") or "day")
    if grain == "hour":
        start = local_time.replace(minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)
        bucket = start.strftime("%Y-%m-%d %H:00")
    elif grain == "week":
        start = (local_time - timedelta(days=local_time.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = start + timedelta(days=7)
        iso_year, iso_week, _ = start.isocalendar()
        bucket = f"{iso_year}-W{iso_week:02d}"
    elif grain == "month":
        start = local_time.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (
            start.replace(year=start.year + 1, month=1)
            if start.month == 12
            else start.replace(month=start.month + 1)
        )
        bucket = start.strftime("%Y-%m")
    else:
        start = local_time.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        bucket = start.strftime("%Y-%m-%d")
    return {
        "bucket": bucket,
        "bucket_start": start.isoformat(),
        "bucket_end": end.isoformat(),
    }


def run_date(row: dict[str, Any]) -> str:
    value = row.get("__run_date")
    if value:
        return str(value)[:10]
    timestamp = row_timestamp(row)
    if timestamp:
        return timestamp.date().isoformat()
    return "unknown"


def time_value(value: object) -> datetime:
    return parse_utc_datetime(value) or datetime.min.replace(tzinfo=timezone.utc)
