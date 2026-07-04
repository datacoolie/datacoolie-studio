from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


UTC = timezone.utc
MIN_UTC_DATETIME = datetime.min.replace(tzinfo=UTC)


def parse_utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def utc_datetime_sort_key(value: Any) -> datetime:
    return parse_utc_datetime(value) or MIN_UTC_DATETIME
