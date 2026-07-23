from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def environment_health(
    *,
    latest_log_at: str | None,
    latest_job_log_at: str | None,
    latest_dataflow_log_at: str | None,
    coverage: dict[str, Any],
    reconciliation: dict[str, Any],
    failed_jobs_last_3_days: int,
    failed_jobs_last_7_days: int,
    failed_dataflows_last_3_days: int,
    failed_dataflows_last_7_days: int,
    maintenance_failed_last_7_days: int,
    maintenance_failed_last_14_days: int,
    maintenance_skipped_last_7_days: int,
    has_jobs: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply the shared Monitoring environment-health policy to bounded metrics."""
    age_days = _age_days(latest_log_at, now=now)
    reasons: list[str] = []
    status = "healthy"

    if coverage.get("status") in {"missing_sources", "no_records"}:
        status = "no_log_evidence"
        reasons.append("No complete ETL log evidence is available.")
    if coverage.get("status") in {"error", "partial"}:
        status = _max_status(status, "warning")
        reasons.append("Log coverage is partial or has read errors.")
    if age_days is not None and age_days > 30:
        status = "has_issues"
        reasons.append(f"Latest log is {age_days} days old.")
    elif age_days is not None and age_days > 7:
        status = _max_status(status, "warning")
        reasons.append(f"Latest log is {age_days} days old.")
    if failed_jobs_last_3_days or failed_dataflows_last_3_days:
        status = "has_issues"
        reasons.append("Recent failures were found in the last 3 days.")
    elif failed_jobs_last_7_days or failed_dataflows_last_7_days:
        status = _max_status(status, "warning")
        reasons.append("Failures were found in the last 7 days.")
    if maintenance_failed_last_7_days:
        status = "has_issues"
        reasons.append("Maintenance failures were found in the last 7 days.")
    elif maintenance_failed_last_14_days:
        status = _max_status(status, "warning")
        reasons.append("Maintenance failures were found in the last 14 days.")
    if maintenance_skipped_last_7_days:
        status = _max_status(status, "warning")
        reasons.append("Skipped maintenance operations were found in the last 7 days.")
    if reconciliation.get("mismatch_count"):
        status = _max_status(status, "warning")
        reasons.append("Job and dataflow log totals do not fully reconcile.")
    if not reasons and has_jobs:
        reasons.append("No immediate monitoring issues detected.")

    return {
        "status": status,
        "label": {
            "healthy": "Healthy",
            "warning": "Warning",
            "has_issues": "Has issues",
            "no_log_evidence": "No log evidence",
        }.get(status, status),
        "reasons": reasons,
        "latest_log_at": latest_log_at,
        "latest_job_log_at": latest_job_log_at,
        "latest_dataflow_log_at": latest_dataflow_log_at,
        "latest_log_age_days": age_days,
        "failed_jobs_last_3_days": failed_jobs_last_3_days,
        "failed_jobs_last_7_days": failed_jobs_last_7_days,
        "failed_dataflows_last_3_days": failed_dataflows_last_3_days,
        "failed_dataflows_last_7_days": failed_dataflows_last_7_days,
        "maintenance_failed_last_7_days": maintenance_failed_last_7_days,
        "maintenance_failed_last_14_days": maintenance_failed_last_14_days,
        "maintenance_skipped_last_7_days": maintenance_skipped_last_7_days,
    }


def _age_days(value: str | None, *, now: datetime | None) -> int | None:
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0, int((current.astimezone(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds() // 86400))


def _max_status(current: str, candidate: str) -> str:
    order = {"healthy": 0, "no_log_evidence": 1, "warning": 2, "has_issues": 3}
    return candidate if order.get(candidate, 0) > order.get(current, 0) else current
