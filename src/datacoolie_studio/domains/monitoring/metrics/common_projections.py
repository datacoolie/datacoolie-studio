from __future__ import annotations

import json
import re
import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
from datacoolie_studio.core.time import parse_utc_datetime
from datacoolie_studio.domains.monitoring.metrics.failure import (
    classify_failure,
    dataflow_failed_phases,
    dataflow_failure_phase_and_message,
)

_DATE_GRAINS = ("hour", "day", "week", "month")


def _empty_health_page() -> dict[str, Any]:
    return {"status": "unknown", "label": "Unknown", "reasons": []}


def _empty_operations_page() -> dict[str, Any]:
    return {
        "kpis": {},
        "windows": {},
        "job_status_distribution": [],
        "jobs_by_date_status": [],
        "dataflows_by_date_status": [],
        "failed_jobs": [],
        "dataflow_kpis": {},
        "status_by_stage": [],
    }


def _empty_failures_page() -> dict[str, Any]:
    return {
        "failed_by_stage": [],
        "failed_by_source_connection_type": [],
        "top_failing_dataflows": [],
        "error_categories": [],
        "failure_trend_by_date": [],
        "failed_records": [],
    }


def _empty_performance_page() -> dict[str, Any]:
    return {
        "duration_breakdown": [],
        "duration_vs_rows": [],
        "slowest_dataflows": [],
        "duration_by_stage": [],
        "engine_stage_matrix": [],
    }


def _empty_volume_page() -> dict[str, Any]:
    return {
        "kpis": {},
        "rows_by_date": [],
        "bytes_by_date": [],
        "volume_by_load_type": [],
        "top_dataflows_by_rows_written": [],
    }


def _empty_maintenance_page() -> dict[str, Any]:
    return {
        "kpis": {},
        "bytes_reclaimed_by_table": [],
        "format_comparison": [],
        "per_table": [],
        "duration_vs_files_removed": [],
        "bytes_reclaimed_by_date": [],
    }


def _empty_freshness_page() -> dict[str, Any]:
    return {
        "kpis": {},
        "latest_freshness_by_dataflow": [],
        "watermark_movement": [],
        "stale_candidates": [],
        "skipped_patterns": [],
    }


def _empty_diagnostics_page() -> dict[str, Any]:
    return {"kpis": {}, "job_id_evidence": [], "read_errors": []}


def _filter_log_rows(
    rows: list[dict[str, Any]],
    filters: dict[str, str],
    include_dataflow_filters: bool,
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if _matches_log_filters(row, filters, include_dataflow_filters)
    ]


def _normalize_monitoring_filters_for_timezone(
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


def _matches_log_filters(
    row: dict[str, Any], filters: dict[str, str], include_dataflow_filters: bool
) -> bool:
    range_value = filters.get("range")
    if range_value in {"24h", "3d", "7d", "30d", "90d"}:
        days = {"24h": 1, "3d": 3, "7d": 7, "30d": 30, "90d": 90}[range_value]
        timestamp = _row_timestamp(row)
        if timestamp is None:
            return False
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        relative_start = parse_utc_datetime(filters.get("_relativeStartTime"))
        cutoff = relative_start or datetime.now(timezone.utc) - timedelta(days=days)
        if timestamp.astimezone(timezone.utc) < cutoff:
            return False
    elif range_value == "custom":
        timestamp = _row_timestamp(row)
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
        if not _matches_filter_value(row.get(row_key), filters.get(filter_key)):
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
            if not _matches_filter_value(row.get(row_key), filters.get(filter_key)):
                return False
        if not _matches_connection(row, filters.get("connection")):
            return False

    search = (filters.get("search") or "").strip().lower()
    if search and not _row_matches_search(row, search, include_dataflow_filters):
        return False
    if not _matches_investigation(row, filters, include_dataflow_filters):
        return False
    return True


def _row_matches_search(
    row: dict[str, Any], search: str, include_dataflow_filters: bool
) -> bool:
    if any(search in str(value or "").lower() for value in row.values()):
        return True
    if not include_dataflow_filters:
        return False
    normalized = search.replace("`", "")
    candidates = [
        _norm(row.get("source_full_table")),
        _norm(row.get("destination_full_table")),
        _maintenance_target_identity(row).lower().replace("`", ""),
    ]
    return any(normalized in candidate for candidate in candidates)


def _matches_investigation(
    row: dict[str, Any], filters: dict[str, str], include_dataflow_filters: bool
) -> bool:
    kind = (filters.get("investigateKind") or "").strip()
    value = (filters.get("investigateValue") or "").strip()
    if not kind or not value:
        return True
    normalized = value.lower().replace("`", "")
    if kind == "job_id":
        return _norm(row.get("job_id")) == normalized
    if not include_dataflow_filters:
        return True
    if kind == "dataflow_run_id":
        return _norm(row.get("dataflow_run_id")) == normalized
    if kind == "dataflow":
        return (
            _norm(row.get("dataflow_id")) == normalized
            or _norm(row.get("dataflow_name")) == normalized
        )
    if kind == "destination_table":
        target_identity = _maintenance_target_identity(row).lower().replace("`", "")
        return normalized in {
            target_identity,
            _norm(row.get("destination_full_table")),
            _norm(row.get("destination_table")),
            _norm(row.get("destination_path")),
        }
    return True


def _norm(value: Any) -> str:
    return str(value or "").replace("`", "").lower()


def _matches_filter_value(value: Any, filter_value: str | None) -> bool:
    selected = _split_filter_values(filter_value)
    return not selected or str(value or "unknown") in selected


def _matches_connection(row: dict[str, Any], filter_value: str | None) -> bool:
    selected = _split_filter_values(filter_value)
    if not selected:
        return True
    return (
        str(row.get("source_name") or "unknown") in selected
        or str(row.get("destination_name") or "unknown") in selected
    )


def _split_filter_values(value: str | None) -> list[str]:
    if not value or value == "all":
        return []
    return [item.strip() for item in value.split("|") if item.strip()]


def _enrich_job_run_for_investigation(
    job: dict[str, Any],
    child_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    child_rows = child_rows or []
    enriched = dict(job)
    if child_rows:
        child_status = Counter(_status(row) for row in child_rows)
        child_durations = [
            _num(row, "duration_seconds") or 0
            for row in child_rows
            if _num(row, "duration_seconds")
        ]
        enriched.update(
            {
                "child_dataflow_count": len(child_rows),
                "child_succeeded_count": child_status.get("succeeded", 0),
                "child_failed_count": child_status.get("failed", 0),
                "child_skipped_count": child_status.get("skipped", 0),
                "child_running_count": child_status.get("running", 0),
                "child_pending_count": child_status.get("pending", 0),
                "child_p95_duration_seconds": _percentile_clean(child_durations, 0.95),
                "child_total_rows_read": _sum(child_rows, "source_rows_read"),
                "child_total_rows_written": _sum(
                    child_rows, "destination_rows_written"
                ),
                "child_total_bytes_added": _sum(child_rows, "destination_bytes_added"),
                "child_total_bytes_removed": _sum(
                    child_rows, "destination_bytes_removed"
                ),
            }
        )
    for key in (
        "child_dataflow_count",
        "child_succeeded_count",
        "child_failed_count",
        "child_skipped_count",
        "child_running_count",
        "child_pending_count",
        "child_p95_duration_seconds",
        "child_total_rows_read",
        "child_total_rows_written",
        "child_total_bytes_added",
        "child_total_bytes_removed",
    ):
        enriched[key] = _num(enriched, key) or 0
    enriched["error_preview"] = _error_preview(enriched)
    enriched["reconciliation_mismatch_count"] = _job_reconciliation_mismatch_count(
        enriched
    )
    enriched["reconciliation_status"] = _job_reconciliation_status(enriched)
    enriched["job_key"] = _job_key(enriched)
    enriched["job_shape_label"] = _job_shape_label(enriched)
    return enriched


def _job_reconciliation_mismatch_count(job: dict[str, Any]) -> int:
    expected = job.get("total_dataflows")
    if expected in (None, ""):
        return 0
    observed = _num(job, "child_dataflow_count") or 0
    return 0 if int(expected or 0) == observed else 1


def _job_reconciliation_status(job: dict[str, Any]) -> str:
    if job.get("total_dataflows") in (None, "") and not (
        _num(job, "child_dataflow_count") or 0
    ):
        return "not_available"
    if _job_reconciliation_mismatch_count(job):
        return "mismatch"
    return "matched"


def _enrich_dataflow_run_for_investigation(row: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(row)
    if _status(enriched) == "failed":
        enriched = _failure_enriched_dataflow(enriched)
    enriched["source_display"] = _endpoint_display(enriched, "source")
    enriched["destination_display"] = _endpoint_display(enriched, "destination")
    enriched["phase_health"] = _phase_health(enriched)
    enriched["error_phase"] = _error_phase(enriched)
    enriched["error_preview"] = _error_preview(enriched)
    watermark = _watermark_classification(enriched)
    enriched["coverage_state"] = watermark["coverage_state"]
    enriched["movement_state"] = watermark["movement_state"]
    enriched["adjustment_state"] = watermark["adjustment_state"]
    enriched["linked_job_status"] = enriched.get("job_status") or "unknown"
    enriched["linked_job_duration_seconds"] = enriched.get("job_duration_seconds")
    return enriched


def _endpoint_display(row: dict[str, Any], direction: str) -> str:
    full_table = row.get(f"{direction}_full_table")
    if full_table:
        return str(full_table).replace("`", "")
    qualified = ".".join(
        str(part)
        for part in (
            row.get(f"{direction}_catalog"),
            row.get(f"{direction}_database"),
            row.get(f"{direction}_schema"),
            row.get(f"{direction}_table"),
        )
        if part not in (None, "")
    )
    if qualified:
        return qualified
    path = row.get(f"{direction}_path")
    if path:
        return _tail_path(str(path))
    python_function = row.get(f"{direction}_python_function")
    if python_function:
        return str(python_function)
    query = row.get(f"{direction}_query")
    if query:
        return _compact_text(str(query), 80)
    return str(
        row.get(f"{direction}_table") or row.get(f"{direction}_name") or "unknown"
    )


def _tail_path(path: str) -> str:
    normalized = path.replace("\\", "/").rstrip("/")
    parts = [part for part in normalized.split("/") if part]
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[-1] if parts else path


def _phase_health(row: dict[str, Any]) -> str:
    failed_phases = dataflow_failed_phases(row)
    if failed_phases:
        return f"{failed_phases[0]}_failed"
    phases = ("source", "transform", "destination", "overhead")
    durations = {phase: _performance_phase_duration(row, phase) for phase in phases}
    if any(durations.values()):
        slowest = max(durations.items(), key=lambda item: item[1])
        return f"{slowest[0]}_bottleneck"
    return "unknown" if _status(row) == "unknown" else "ok"


def _error_phase(row: dict[str, Any]) -> str:
    failed_phases = dataflow_failed_phases(row)
    if failed_phases:
        return failed_phases[0]
    for phase in ("source", "transform", "destination"):
        if row.get(f"{phase}_error_message"):
            return phase
    return "job" if row.get("error_message") else ""


def _error_preview(row: dict[str, Any]) -> str:
    message = (
        row.get("error_message")
        or row.get("source_error_message")
        or row.get("transform_error_message")
        or row.get("destination_error_message")
        or row.get("last_error")
        or ""
    )
    return _compact_text(str(message), 140)


def _compact_text(value: str, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", value or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: max(0, limit - 3)]}..."


def _watermark_classification(row: dict[str, Any]) -> dict[str, str | None]:
    value_keys = (
        "source_watermark_before",
        "source_watermark_after",
        "source_watermark_effective",
        "source_watermark_columns",
    )
    values = [row.get(key) for key in value_keys]
    if not any(not _is_missing_value(value) for value in values):
        return {
            "coverage_state": "not_configured",
            "movement_state": "not_configured",
            "adjustment_state": "not_configured",
            "before": None,
            "after": None,
            "effective": None,
        }
    runtime_values = [
        row.get("source_watermark_before"),
        row.get("source_watermark_after"),
        row.get("source_watermark_effective"),
    ]
    if any(
        _looks_json(value) and not _is_valid_json(value) for value in runtime_values
    ):
        return {
            "coverage_state": "invalid",
            "movement_state": "invalid",
            "adjustment_state": "invalid",
            "before": None,
            "after": None,
            "effective": None,
        }
    before = _normalized_json_string(row.get("source_watermark_before"))
    after = _normalized_json_string(row.get("source_watermark_after"))
    effective = _normalized_json_string(row.get("source_watermark_effective"))
    status = _status(row)
    if status in {"running", "pending"}:
        movement = "incomplete"
    elif status in {"failed", "skipped"}:
        movement = "unchanged"
    elif before and after:
        movement = "advanced" if before != after else "unchanged"
    elif not before and after:
        movement = "initialized"
    else:
        movement = "incomplete"

    if before and effective:
        adjustment = "adjusted" if effective != before else "not_adjusted"
    elif before and not effective:
        adjustment = "not_adjusted"
    else:
        adjustment = "unknown"

    return {
        "coverage_state": "configured",
        "movement_state": movement,
        "adjustment_state": adjustment,
        "before": before,
        "after": after,
        "effective": effective,
    }


def _is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    try:
        if value == "":
            return True
        return bool(value != value)
    except (TypeError, ValueError):
        return False


def _looks_json(value: Any) -> bool:
    if _is_missing_value(value):
        return False
    if not isinstance(value, str):
        return isinstance(value, (dict, list))
    stripped = value.strip()
    return (stripped.startswith("{") and stripped.endswith("}")) or (
        stripped.startswith("[") and stripped.endswith("]")
    )


def _is_valid_json(value: Any) -> bool:
    if isinstance(value, (dict, list)):
        return True
    if _is_missing_value(value):
        return True
    if not isinstance(value, str):
        return True
    try:
        json.loads(value)
    except (TypeError, ValueError):
        return False
    return True


def _failure_enriched_dataflow(row: dict[str, Any]) -> dict[str, Any]:
    phase, message = dataflow_failure_phase_and_message(row)
    all_evidence = "\n".join(
        str(row.get(key) or "").strip()
        for key in (
            "source_error_message",
            "transform_error_message",
            "destination_error_message",
            "error_message",
        )
        if _has_value(row.get(key))
    )
    classification = classify_failure(message, all_evidence=all_evidence)
    category = classification.category
    signature = _failure_signature(category, phase, message)
    target = _failure_target(row)
    return {
        **row,
        "failure_kind": "dataflow",
        "failure_phase": phase,
        "failure_message": message,
        "failure_category": category,
        "failure_tags": list(classification.tags),
        "failure_rule_id": classification.rule_id,
        "failure_signature": signature,
        "failure_target": target,
        "failure_time": row.get("end_time") or row.get("start_time"),
    }


def _failure_signature(category: str, phase: str, message: str) -> str:
    normalized = _normalize_error_message(message)
    return f"{category}|{phase}|{normalized or 'unknown'}"


def _normalize_error_message(message: str) -> str:
    text = str(message or "").strip().lower()
    text = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<uuid>", text
    )
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}(?:[t ][0-9:.+\-z]+)?\b", "<timestamp>", text)
    text = re.sub(r"\b\d+\b", "<number>", text)
    text = re.sub(r"\s+", " ", text)
    return text[:360]


def _failure_target(row: dict[str, Any]) -> str:
    destination = (
        row.get("destination_full_table")
        or row.get("destination_table")
        or row.get("destination_path")
        or row.get("destination_name")
    )
    source = (
        row.get("source_full_table")
        or row.get("source_table")
        or row.get("source_path")
        or row.get("source_name")
    )
    return str(
        destination
        or source
        or row.get("dataflow_name")
        or row.get("dataflow_id")
        or "unknown"
    )


def _performance_phase_duration(row: dict[str, Any], phase: str) -> float:
    if phase != "overhead":
        return max(0.0, _num(row, f"{phase}_duration_seconds") or 0)
    overhead = _num(row, "overhead_duration_seconds")
    if overhead is not None:
        return max(0.0, overhead)
    duration = _num(row, "duration_seconds") or 0
    known_duration = sum(
        max(0.0, _num(row, f"{known_phase}_duration_seconds") or 0)
        for known_phase in ("source", "transform", "destination")
    )
    return max(0.0, duration - known_duration)


def _is_lakehouse_destination(row: dict[str, Any]) -> bool:
    metadata_candidates = [
        row.get("destination_connection_type"),
        row.get("destination_format"),
    ]
    metadata = [
        str(value).strip().lower()
        for value in metadata_candidates
        if str(value or "").strip().lower()
        not in {"", "unknown", "none", "null", "n/a"}
    ]
    if metadata:
        text = " ".join(metadata)
        return any(
            token in text
            for token in ("lakehouse", "delta", "iceberg", "onelake", "deltalake")
        )

    fallback_candidates = [
        row.get("destination_name"),
        row.get("destination_path"),
    ]
    text = " ".join(str(value or "").lower() for value in fallback_candidates)
    return any(
        token in text
        for token in ("lakehouse", "delta", "iceberg", "onelake", "deltalake")
    )


def _maintenance_target_identity(row: dict[str, Any]) -> str:
    connection = str(
        row.get("destination_name")
        or row.get("destination_connection_name")
        or "unknown"
    ).strip()
    table = _qualified_table_name(
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


def _qualified_table_name(*parts: object) -> str:
    values = [str(part).strip("` ") for part in parts if part not in (None, "")]
    return ".".join(values)


def _normalized_json_string(value: Any) -> str | None:
    if _is_missing_value(value):
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    text = str(value)
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return text
    return json.dumps(parsed, sort_keys=True, ensure_ascii=False)


def _job_runs_by_dataflow_operation_type(
    _rows: list[dict[str, Any]], jobs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    buckets: dict[str, Counter] = defaultdict(Counter)
    for job in jobs:
        operation_types = _listish_values(job.get("operation_types")) or ["unknown"]
        for operation_type in operation_types:
            operation_type = _dimension_value(operation_type)
            bucket = buckets[operation_type]
            bucket["count"] += 1
            status = _status(job)
            bucket[status] += 1
            if status == "failed":
                bucket["failed_count"] += 1

    return sorted(
        (
            {"operation_type": operation_type, **dict(values)}
            for operation_type, values in buckets.items()
        ),
        key=lambda item: (-int(item["count"]), str(item["operation_type"])),
    )


def _dataflow_operation_type(row: dict[str, Any]) -> str:
    return _dimension_value(row.get("operation_type"))


def _dataflow_id(row: dict[str, Any]) -> str:
    value = row.get("dataflow_id")
    if value in (None, ""):
        return ""
    text = str(value).strip()
    return (
        "" if not text or text.lower() in {"none", "null", "nan", "unknown"} else text
    )


def _normalized_job_shape(job: dict[str, Any]) -> dict[str, list[str]]:
    stages = [_dimension_value(value) for value in _listish_values(job.get("stages"))]
    operation_types = [
        _dimension_value(value) for value in _listish_values(job.get("operation_types"))
    ]
    return {
        "operation_types": sorted(set(operation_types or ["unknown"])),
        "stages": stages or ["unknown"],
    }


def _job_key(job: dict[str, Any]) -> str:
    payload = json.dumps(
        _normalized_job_shape(job),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _job_shape_label(job: dict[str, Any]) -> str:
    shape = _normalized_job_shape(job)
    return f"{', '.join(shape['operation_types'])} | {', '.join(shape['stages'])}"


def _dimension_value(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    if not text or text.lower() in {
        "none",
        "null",
        "nan",
        "not_available",
        "not available",
    }:
        return "unknown"
    return text


def _has_value(value: Any) -> bool:
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return value not in (None, "")


def _listish_values(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if not _has_value(value):
        return []
    text_value = str(value).strip()
    if not text_value:
        return []
    if text_value.startswith("[") and text_value.endswith("]"):
        try:
            parsed = json.loads(text_value)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [item.strip() for item in text_value.split(",") if item.strip()]


def _destination_operation_type(row: dict[str, Any]) -> str:
    return _dimension_value(row.get("destination_operation_type"))


def _phase_duration_summary(
    rows: list[dict[str, Any]],
    group_key: str,
    resolve_group,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    phase_definitions = [
        ("source", "source_status", "source_duration_seconds"),
        ("transform", "transform_status", "transform_duration_seconds"),
        ("destination", "destination_status", "destination_duration_seconds"),
        ("overhead", None, "overhead_duration_seconds"),
    ]
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "statuses": {phase: Counter() for phase, _, _ in phase_definitions},
            "durations": {phase: [] for phase, _, _ in phase_definitions},
        }
    )
    total_bucket = {
        "statuses": {phase: Counter() for phase, _, _ in phase_definitions},
        "durations": {phase: [] for phase, _, _ in phase_definitions},
    }

    def add_run(bucket: dict[str, Any], row: dict[str, Any]) -> None:
        failed_phases = set(dataflow_failed_phases(row))
        for phase, status_key, duration_key in phase_definitions:
            if status_key and row.get(status_key):
                phase_status = _status({**row, "status": row.get(status_key)})
                bucket["statuses"][phase][phase_status] += 1
            duration = _phase_duration(row, phase, duration_key)
            if duration is not None:
                bucket["durations"][phase].append(duration)
        if "overhead" in failed_phases:
            bucket["statuses"]["overhead"]["failed"] += 1

    for row in rows:
        if _status(row) not in {"succeeded", "failed", "skipped"}:
            continue
        group_value = resolve_group(row)
        add_run(buckets[group_value], row)
        add_run(total_bucket, row)

    def summary_row(
        group_value: str, bucket: dict[str, Any], is_total: bool = False
    ) -> dict[str, Any] | None:
        phase_durations = bucket["durations"]
        total_duration = sum(sum(values) for values in phase_durations.values())
        if total_duration <= 0:
            return None
        row: dict[str, Any] = {
            group_key: group_value,
            "group": group_value,
            "total_duration_seconds": round(total_duration, 3),
        }
        if is_total:
            row["is_total"] = 1
        for phase, _, _ in phase_definitions:
            statuses = bucket["statuses"][phase]
            durations = phase_durations[phase]
            duration = sum(durations)
            row[f"{phase}_duration_seconds"] = round(duration, 3)
            row[f"{phase}_duration_percent"] = _rate(duration, total_duration)
            row[f"{phase}_run_count"] = len(durations)
            row[f"{phase}_avg_duration_seconds"] = _avg(durations)
            row[f"{phase}_p95_duration_seconds"] = _percentile_clean(durations, 0.95)
            row[f"{phase}_succeeded"] = statuses.get("succeeded", 0)
            row[f"{phase}_failed"] = statuses.get("failed", 0)
            row[f"{phase}_skipped"] = statuses.get("skipped", 0)
            row[f"{phase}_running"] = statuses.get("running", 0)
            row[f"{phase}_pending"] = statuses.get("pending", 0)
            row[f"{phase}_unknown"] = statuses.get("unknown", 0)
        return row

    result = [
        row
        for group_value, bucket in buckets.items()
        if (row := summary_row(group_value, bucket)) is not None
    ]
    sorted_result = sorted(
        result,
        key=lambda item: (-float(item["total_duration_seconds"]), str(item["group"])),
    )
    if limit is not None:
        sorted_result = sorted_result[:limit]
    total = summary_row("Total", total_bucket, is_total=True)
    return ([total] if total is not None else []) + sorted_result


def _phase_duration(row: dict[str, Any], phase: str, duration_key: str) -> float | None:
    if phase != "overhead":
        return _num(row, duration_key)
    total_duration = _num(row, "duration_seconds")
    if total_duration is None:
        return None
    known_duration = sum(
        _num(row, known_key) or 0
        for known_key in (
            "source_duration_seconds",
            "transform_duration_seconds",
            "destination_duration_seconds",
        )
    )
    return max(0.0, total_duration - known_duration)


def _dominant_dataflow_operation_type(rows: list[dict[str, Any]]) -> str:
    values = Counter(_dataflow_operation_type(row) for row in rows)
    return values.most_common(1)[0][0] if values else "unknown"


def _dominant_value(rows: list[dict[str, Any]], key: str) -> str:
    values = Counter(_dimension_value(row.get(key)) for row in rows)
    return values.most_common(1)[0][0] if values else "unknown"


def _counter_rows(counts: Counter, key_name: str) -> list[dict[str, Any]]:
    return [{key_name: key, "count": value} for key, value in counts.most_common()]


def _trend_context(
    filters: dict[str, str], rows: list[dict[str, Any]], timezone_info: tzinfo
) -> dict[str, Any]:
    requested_grain = str(filters.get("grain") or "auto").strip().lower()
    if requested_grain not in {"auto", *_DATE_GRAINS}:
        requested_grain = "auto"
    start, end = _trend_bounds(filters, rows, timezone_info)
    effective_grain = _resolve_effective_grain(requested_grain, start, end)
    return {
        "requested_grain": requested_grain,
        "effective_grain": effective_grain,
        "timezone_info": timezone_info,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
    }


def _trend_bounds(
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
        _ensure_aware(timestamp).astimezone(timezone_info)
        for row in rows
        if (timestamp := _row_timestamp(row)) is not None
    ]
    if timestamps:
        return min(timestamps), max(timestamps)
    return now - timedelta(days=30), now


def _resolve_effective_grain(
    requested_grain: str, start: datetime | None, end: datetime | None
) -> str:
    if start is None or end is None:
        return "day" if requested_grain == "auto" else requested_grain
    if end < start:
        start, end = end, start
    minimum_grain = _minimum_allowed_grain(start, end)
    if requested_grain == "auto":
        return minimum_grain
    if requested_grain not in _DATE_GRAINS:
        return minimum_grain
    requested_index = _DATE_GRAINS.index(requested_grain)
    minimum_index = _DATE_GRAINS.index(minimum_grain)
    return _DATE_GRAINS[max(requested_index, minimum_index)]


def _minimum_allowed_grain(start: datetime, end: datetime) -> str:
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


def _date_bucket(
    row: dict[str, Any], trend_context: dict[str, Any]
) -> dict[str, str | None]:
    timestamp = _row_timestamp(row)
    if timestamp is None:
        return {"bucket": "unknown", "bucket_start": None, "bucket_end": None}
    timezone_info = trend_context.get("timezone_info") or timezone.utc
    local_time = _ensure_aware(timestamp).astimezone(timezone_info)
    grain = str(trend_context.get("effective_grain") or "day")
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


def _ensure_aware(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp


def _run_date(row: dict[str, Any]) -> str:
    value = row.get("__run_date")
    if value:
        return str(value)[:10]
    timestamp = _row_timestamp(row)
    if timestamp:
        return timestamp.date().isoformat()
    return "unknown"


def _row_timestamp(row: dict[str, Any]) -> datetime | None:
    for key in ("end_time", "start_time"):
        timestamp = parse_utc_datetime(row.get(key))
        if timestamp is not None:
            return timestamp
    return None


def _status(row: dict[str, Any]) -> str:
    return str(row.get("status") or "unknown")


def _sum(rows: list[dict[str, Any]], key: str) -> float:
    return round(sum(_num(row, key) or 0 for row in rows), 3)


def _avg(values: list[float | None]) -> float:
    clean = [value for value in values if value is not None]
    return round(sum(clean) / len(clean), 3) if clean else 0


def _rate(part: int | float, whole: int | float) -> float:
    return round((part / whole) * 100, 2) if whole else 0


def _duration_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [_num(row, "duration_seconds") for row in rows]
    clean = [value for value in values if value is not None]
    return {
        "count": len(clean),
        "avg_duration_seconds": _avg(values),
        "q1_duration_seconds": _percentile(clean, 0.25),
        "p50_duration_seconds": _percentile(clean, 0.50),
        "q3_duration_seconds": _percentile(clean, 0.75),
        "p95_duration_seconds": _percentile(clean, 0.95),
        "p99_duration_seconds": _percentile(clean, 0.99),
        "max_duration_seconds": round(max(clean), 3) if clean else 0,
    }


def _percentile_clean(values: list[float | None], percentile: float) -> float:
    return _percentile([value for value in values if value is not None], percentile)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return round(ordered[index], 3)


def _num(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _time_value(value: object) -> datetime:
    return parse_utc_datetime(value) or datetime.min.replace(tzinfo=timezone.utc)
