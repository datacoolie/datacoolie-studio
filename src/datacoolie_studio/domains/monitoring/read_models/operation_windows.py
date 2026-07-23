from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from datacoolie_studio.domains.monitoring.read_models.common import rows


_STATUSES = ("succeeded", "failed", "skipped", "running", "pending", "unknown")


def operation_windows(
    conn: Any,
    ctes: str,
    params: list[Any],
    *,
    timezone_name: str,
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    reference_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        local_today = reference_now.astimezone(ZoneInfo(timezone_name)).date()
    except ZoneInfoNotFoundError:
        local_today = reference_now.date()
    values = rows(conn.execute(
        f"{ctes} {_QUERY}",
        [*params, timezone_name, local_today, reference_now],
    ))
    result = {name: _empty_window() for name in ("today", "last_24_hours", "last_7_days")}
    for value in values:
        window = result[str(value["window_name"])]
        kind = str(value["record_kind"])
        status = str(value["status"])
        count = int(value.get("records") or 0)
        window[f"{kind}_runs"] += count
        if status in _STATUSES and (kind == "dataflow" or status in {"succeeded", "failed", "unknown"}):
            window[f"{kind}_{status}"] += count
        if kind == "job":
            for state in ("running", "pending", "skipped"):
                key = f"job_{state}"
                window[key] = int(window[key]) + int(value.get(f"child_{state}") or 0)
    for window in result.values():
        for kind in ("job", "dataflow"):
            executable = int(window[f"{kind}_succeeded"]) + int(window[f"{kind}_failed"])
            window[f"{kind}_success_rate"] = _rate(int(window[f"{kind}_succeeded"]), executable)
            window[f"{kind}_failure_rate"] = _rate(int(window[f"{kind}_failed"]), executable)
    return result


def empty_operation_windows() -> dict[str, dict[str, Any]]:
    return {name: _empty_window() for name in ("today", "last_24_hours", "last_7_days")}


def _empty_window() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for kind in ("job", "dataflow"):
        result[f"{kind}_runs"] = 0
        for status in _STATUSES:
            result[f"{kind}_{status}"] = 0
        result[f"{kind}_success_rate"] = 0
        result[f"{kind}_failure_rate"] = 0
    return result


def _rate(part: int | float, whole: int | float) -> float:
    return round((part / whole) * 100, 2) if whole else 0


_QUERY = """
, window_bounds AS (
  SELECT ?::VARCHAR AS timezone_name, ?::DATE AS local_today, ?::TIMESTAMPTZ AS current_time
), windowed_runs AS (
  SELECT 'today' AS window_name, 'dataflow' AS record_kind, normalized_status AS status,
         0::BIGINT AS child_running, 0::BIGINT AS child_pending, 0::BIGINT AS child_skipped
  FROM filtered_dataflows, window_bounds
  WHERE CAST(timezone(timezone_name, event_time) AS DATE) = local_today
  UNION ALL
  SELECT 'last_24_hours', 'dataflow', normalized_status, 0, 0, 0
  FROM filtered_dataflows, window_bounds WHERE event_time >= current_time - INTERVAL 24 HOUR
  UNION ALL
  SELECT 'last_7_days', 'dataflow', normalized_status, 0, 0, 0
  FROM filtered_dataflows, window_bounds WHERE event_time >= current_time - INTERVAL 7 DAY
  UNION ALL
  SELECT 'today', 'job', normalized_status, COALESCE(total_running, 0), COALESCE(total_pending, 0), COALESCE(total_skipped, 0)
  FROM filtered_jobs, window_bounds
  WHERE CAST(timezone(timezone_name, event_time) AS DATE) = local_today
  UNION ALL
  SELECT 'last_24_hours', 'job', normalized_status, COALESCE(total_running, 0), COALESCE(total_pending, 0), COALESCE(total_skipped, 0)
  FROM filtered_jobs, window_bounds WHERE event_time >= current_time - INTERVAL 24 HOUR
  UNION ALL
  SELECT 'last_7_days', 'job', normalized_status, COALESCE(total_running, 0), COALESCE(total_pending, 0), COALESCE(total_skipped, 0)
  FROM filtered_jobs, window_bounds WHERE event_time >= current_time - INTERVAL 7 DAY
)
SELECT window_name, record_kind, status, COUNT(*) AS records,
       SUM(child_running) AS child_running, SUM(child_pending) AS child_pending,
       SUM(child_skipped) AS child_skipped
FROM windowed_runs
GROUP BY window_name, record_kind, status
ORDER BY window_name, record_kind, status
"""
