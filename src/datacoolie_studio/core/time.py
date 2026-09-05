from __future__ import annotations

import math
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


def epoch_value_to_nanoseconds(value: Any) -> int | None:
    """Normalize an epoch value expressed in seconds through nanoseconds.

    Storage providers do not use one epoch unit consistently. Local file
    revisions use nanoseconds, while some provider revision tokens use
    milliseconds. Keeping this normalization at the boundary prevents a
    provider token from being rendered as a date in 1970.
    """
    if isinstance(value, bool):
        return None
    try:
        numeric = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if numeric <= 0:
        return None

    magnitude = abs(numeric)
    if magnitude < 100_000_000_000:
        return int(numeric * 1_000_000_000)
    if magnitude < 100_000_000_000_000:
        return int(numeric * 1_000_000)
    if magnitude < 100_000_000_000_000_000:
        return int(numeric * 1_000)
    return int(numeric)
