from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any
from datacoolie_studio.core.time import parse_utc_datetime
from datacoolie_studio.domains.monitoring.metrics.v6_semantics import (
    final_attempt,
    freshness_evidence,
    is_supported_log_row,
    read_boundary_adjustment,
)
from datacoolie_studio.domains.monitoring.metrics.row_normalization import (
    dataflow_id as _dataflow_id,
    dataflow_operation_type as _dataflow_operation_type,
    dimension_value as _dimension_value,
)
from datacoolie_studio.domains.monitoring.metrics.investigation import (
    watermark_classification as _watermark_classification,
)
from datacoolie_studio.domains.monitoring.metrics.aggregates import (
    percentile as _percentile,
    rate as _rate,
    status as _status,
)
from datacoolie_studio.domains.monitoring.metrics.time_buckets import (
    date_bucket as _date_bucket,
    time_value as _time_value,
    trend_context as _trend_context,
)

_FRESHNESS_STALE_DAYS = 7

_SKIPPED_STREAK_RUNS = 3


def _freshness_page(
    rows: list[dict[str, Any]], trend_context: dict[str, Any] | None = None
) -> dict[str, Any]:
    etl_rows = [row for row in rows if _dataflow_operation_type(row) == "etl"]
    successful = [row for row in etl_rows if _status(row) == "succeeded"]
    failed = [row for row in etl_rows if _status(row) == "failed"]
    skipped = [row for row in etl_rows if _status(row) == "skipped"]
    freshness_rows = [
        row for row in etl_rows if _check_eligible(row)
    ]
    watermark_rows = [row for row in etl_rows if _has_watermark(row)]
    movement = [_watermark_movement_row(row) for row in watermark_rows]
    advanced = [row for row in movement if row["movement"] == "advanced"]
    initialized = [row for row in movement if row["movement"] == "initialized"]
    unchanged = [row for row in movement if row["movement"] == "unchanged"]
    unknown = [row for row in movement if row["movement"] == "unknown"]
    changed = [row for row in movement if row["movement"] == "changed"]
    empty = [row for row in movement if row["movement"] == "empty"]
    persistence_counts = Counter(
        freshness_evidence(row).get("persistence_state") for row in etl_rows
    )
    latest_freshness = _latest_freshness_by_dataflow(freshness_rows)
    stale_candidates = _stale_freshness_candidates_from_latest(
        latest_freshness, days=_FRESHNESS_STALE_DAYS
    )
    skipped_patterns = _consecutive_skipped_patterns(
        etl_rows, threshold=_SKIPPED_STREAK_RUNS
    )
    skipped_by_dataflow = _latest_skipped_streaks_by_dataflow(etl_rows)
    observed_dataflows = len({_registry_key(row) for row in etl_rows if _dataflow_id(row)})
    watermark_dataflows = len({_registry_key(row) for row in watermark_rows if _dataflow_id(row)})
    missing_dataflow_id_runs = sum(1 for row in etl_rows if not _dataflow_id(row))
    age_values = [
        float(item["age_days"])
        for item in latest_freshness
        if item.get("age_days") is not None
    ]
    age_seconds_values = [
        float(item["age_seconds"])
        for item in latest_freshness
        if item.get("age_seconds") is not None
    ]
    dataflow_registry = _freshness_dataflow_registry(
        latest_freshness,
        [row for row in movement if row.get("dataflow_id")],
        skipped_by_dataflow,
        etl_rows,
    )
    stale_dataflow_count = len(stale_candidates)
    latest_by_dataflow = _latest_rows_by_dataflow(etl_rows)
    latest_watermark_states = [
        _watermark_classification(row) for row in latest_by_dataflow.values()
    ]
    latest_unknown_watermarks = [
        row for row in latest_watermark_states if row["movement_state"] == "unknown"
    ]
    latest_status_issues = [
        row
        for row in latest_by_dataflow.values()
        if _status(row) not in {"succeeded", "skipped"}
    ]
    latest_check_keys = {_registry_key(row) for row in latest_freshness}
    latest_check_missing = sum(
        1 for key in latest_by_dataflow if key not in latest_check_keys
    )
    latest_watermark_issues = sum(
        1 for evidence in latest_watermark_states
        if evidence.get("watermark_issue_state") in {"error", "insufficient_evidence"}
    )
    return {
        "kpis": {
            "latest_successful_runs": len(successful),
            "successful_runs": len(successful),
            "freshness_runs": len(freshness_rows),
            "failed_runs": len(failed),
            "skipped_runs": len(skipped),
            "observed_dataflows": observed_dataflows,
            "missing_dataflow_id_runs": missing_dataflow_id_runs,
            "dataflows_with_freshness_evidence": len(latest_freshness),
            "latest_status_issue_dataflows": len(latest_status_issues),
            "latest_watermark_unknown_dataflows": len(latest_unknown_watermarks),
            "latest_watermark_issue_dataflows": latest_watermark_issues,
            "latest_check_missing_dataflows": latest_check_missing,
            "latest_watermark_invalid_dataflows": None,
            "latest_watermark_incomplete_dataflows": None,
            "latest_watermark_changed_dataflows": sum(
                1 for row in latest_watermark_states if row["movement_state"] == "changed"
            ),
            "watermark_enabled_dataflows": len(
                {
                    _registry_key(row)
                    for row in watermark_rows
                    if _dataflow_id(row)
                }
            ),
            "watermark_coverage_rate": (
                _rate(watermark_dataflows, observed_dataflows)
                if observed_dataflows else None
            ),
            "watermark_advanced_runs": len(advanced),
            "watermark_initialized_runs": len(initialized),
            "watermark_unchanged_runs": len(unchanged),
            "watermark_changed_runs": len(changed),
            "watermark_empty_runs": len(empty),
            "watermark_unknown_runs": len(unknown),
            "watermark_incomplete_runs": None,
            "watermark_invalid_runs": None,
            "watermark_adjusted_runs": None,
            "watermark_not_requested_runs": int(persistence_counts.get("not_requested", 0)),
            "watermark_not_attempted_runs": int(persistence_counts.get("not_attempted", 0)),
            "watermark_no_candidate_runs": int(persistence_counts.get("no_candidate", 0)),
            "watermark_save_error_runs": int(persistence_counts.get("save_error", 0)),
            "watermark_enabled_runs": len(watermark_rows),
            "watermark_rate_runs": len(etl_rows),
            "watermark_advanced_rate": (
                _rate(len(advanced), len(etl_rows)) if etl_rows else None
            ),
            "skipped_no_new_data": len(skipped),
            "skipped_streak_threshold": _SKIPPED_STREAK_RUNS,
            "skipped_streak_dataflows": len(skipped_patterns),
            "stale_candidates": stale_dataflow_count,
            "stale_dataflows": stale_dataflow_count,
            "stale_threshold_days": _FRESHNESS_STALE_DAYS,
            "stale_dataflow_rate": (
                _rate(stale_dataflow_count, observed_dataflows)
                if observed_dataflows else None
            ),
            "min_age_days": min(age_values) if age_values else 0,
            "p50_age_days": _percentile(age_values, 0.50),
            "p95_age_days": _percentile(age_values, 0.95),
            "max_age_days": max(age_values) if age_values else 0,
            "min_age_seconds": min(age_seconds_values) if age_seconds_values else 0,
            "p50_age_seconds": _percentile(age_seconds_values, 0.50),
            "p95_age_seconds": _percentile(age_seconds_values, 0.95),
            "max_age_seconds": max(age_seconds_values) if age_seconds_values else 0,
        },
        "latest_freshness_by_dataflow": latest_freshness,
        "watermark_movement": movement[:100],
        "watermark_movement_by_date": _watermark_movement_by_date(
            [
                row for row in etl_rows
                if parse_utc_datetime(row.get("end_time") or row.get("start_time")) is not None
            ],
            trend_context=trend_context,
        ),
        "age_distribution": _freshness_age_distribution(latest_freshness),
        "watermark_coverage_by_stage": _watermark_coverage_by_stage(etl_rows),
        "skipped_streak_distribution": _skipped_streak_distribution(skipped_patterns),
        "dataflow_registry": dataflow_registry,
        "stale_candidates": stale_candidates,
        "skipped_patterns": skipped_patterns,
    }


def _latest_freshness_by_dataflow(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[Any, str], list[dict[str, Any]]] = defaultdict(list)
    now = datetime.now(timezone.utc)
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if dataflow_id:
            buckets[_registry_key(row)].append(row)
    result = []
    for _, items in buckets.items():
        latest = max(items, key=_latest_run_key)
        dataflow_id = _dataflow_id(latest)
        latest_at = latest.get("end_time") or latest.get("start_time")
        timestamp = parse_utc_datetime(latest_at)
        age_days = None
        age_seconds = None
        if timestamp is not None:
            age_seconds = max(
                0, int((now - timestamp.astimezone(timezone.utc)).total_seconds())
            )
            age_days = age_seconds / 86400
        result.append(
            {
                "dataflow_name": latest.get("dataflow_name")
                or latest.get("dataflow_id")
                or "unknown",
                "dataflow_id": dataflow_id,
                "_source_id": latest.get("_source_id"),
                "log_session_id": latest.get("log_session_id"),
                "dataflow_run_id": latest.get("dataflow_run_id"),
                "stage": latest.get("stage") or "unknown",
                "target": _target_identity(latest),
                "latest_freshness_at": latest_at,
                "latest_freshness_status": _status(latest),
                "age_days": age_days,
                "age_seconds": age_seconds,
                "source_name": latest.get("source_name")
                or latest.get("source_id")
                or "unknown",
                "destination_name": latest.get("destination_name")
                or latest.get("destination_id")
                or "unknown",
                "source_format": latest.get("source_format")
                or latest.get("source_connection_type")
                or "unknown",
                "destination_format": latest.get("destination_format")
                or latest.get("destination_connection_type")
                or "unknown",
                **freshness_evidence(latest),
                "destination_load_type": latest.get("destination_load_type")
                or "unknown",
                "status": _status(latest),
            }
        )
    return sorted(
        result, key=lambda item: _time_value(item["latest_freshness_at"]), reverse=True
    )


def _latest_rows_by_dataflow(rows: list[dict[str, Any]]) -> dict[tuple[Any, str], dict[str, Any]]:
    result: dict[tuple[Any, str], dict[str, Any]] = {}
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if not dataflow_id:
            continue
        key = _registry_key(row)
        current = result.get(key)
        if current is None or _latest_run_key(row) > _latest_run_key(current):
            result[key] = row
    return result


def _watermark_movement_row(row: dict[str, Any]) -> dict[str, Any]:
    watermark = _watermark_classification(row)
    movement = watermark["movement_state"]
    return {
        "dataflow_name": row.get("dataflow_name")
        or row.get("dataflow_id")
        or "unknown",
        "dataflow_id": _dataflow_id(row),
        "target": _target_identity(row),
        "end_time": row.get("end_time") or row.get("start_time"),
        "coverage_state": watermark["coverage_state"],
        "movement_state": movement,
        "movement": movement,
        "loaded_watermark": watermark.get("loaded_watermark"),
        "saved_watermark": watermark.get("saved_watermark"),
        "before": row.get("source_watermark_before"),
        "after": row.get("source_watermark_after"),
        "effective": row.get("source_watermark_effective"),
        "watermark_to_save": watermark.get("watermark_to_save"),
        "candidate_kind": watermark.get("candidate_kind"),
        "load_status": watermark.get("load_status"),
        "save_status": watermark.get("save_status"),
        "merge_base": watermark.get("merge_base"),
        "comparison_basis": watermark.get("comparison_basis"),
        "persistence_state": watermark.get("persistence_state"),
        "watermark_issue_state": watermark.get("watermark_issue_state"),
        "configuration_state": watermark.get("configuration_state"),
        "selection_state": watermark.get("selection_state"),
        "status": _status(row),
    }


def _stale_freshness_candidates_from_latest(
    rows: list[dict[str, Any]], days: int
) -> list[dict[str, Any]]:
    result = []
    for item in rows:
        age_days = item.get("age_days")
        if age_days is None:
            continue
        age_value = max(0, float(age_days))
        if age_value > days:
            result.append({**item, "age_days": age_value})
    return sorted(result, key=lambda item: item["age_days"], reverse=True)[:50]


def _watermark_movement_by_date(
    rows: list[dict[str, Any]],
    trend_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    trend_context = trend_context or _trend_context({}, rows, timezone.utc)
    buckets: dict[str, Counter] = defaultdict(Counter)
    bucket_metadata: dict[str, dict[str, str | None]] = {}
    for row in rows:
        bucket = _date_bucket(row, trend_context)
        movement_row = _watermark_movement_row(row)
        buckets[bucket["bucket"]]["total_runs"] += 1
        buckets[bucket["bucket"]][movement_row["movement"]] += 1
        buckets[bucket["bucket"]][f"persistence:{movement_row['persistence_state']}"] += 1
        bucket_metadata[bucket["bucket"]] = bucket
    result = []
    for bucket_key, counts in buckets.items():
        advanced = int(counts.get("advanced", 0))
        initialized = int(counts.get("initialized", 0))
        unchanged = int(counts.get("unchanged", 0))
        changed = int(counts.get("changed", 0))
        empty = int(counts.get("empty", 0))
        unknown = int(counts.get("unknown", 0))
        total_runs = int(counts["total_runs"])
        bucket_info = bucket_metadata.get(
            bucket_key, {"bucket_start": None, "bucket_end": None}
        )
        result.append(
            {
                "date": bucket_key,
                "bucket": bucket_key,
                "bucket_start": bucket_info["bucket_start"],
                "bucket_end": bucket_info["bucket_end"],
                "grain": trend_context["effective_grain"],
                "advanced": advanced,
                "initialized": initialized,
                "unchanged": unchanged,
                "changed": changed,
                "empty": empty,
                "unknown": unknown,
                "incomplete": None,
                "invalid": None,
                "adjusted": None,
                "not_requested": int(counts.get("persistence:not_requested", 0)),
                "not_attempted": int(counts.get("persistence:not_attempted", 0)),
                "no_candidate": int(counts.get("persistence:no_candidate", 0)),
                "save_error": int(counts.get("persistence:save_error", 0)),
                "watermark_enabled_runs": advanced + initialized + unchanged + changed + empty + unknown,
                "watermark_rate_runs": total_runs,
                "total": advanced
                + initialized
                + unchanged
                + changed
                + empty
                + unknown,
                "advanced_rate": _rate(advanced, total_runs) if total_runs else None,
            }
        )
    return sorted(result, key=lambda item: str(item["bucket_start"] or item["bucket"]))


def _freshness_age_distribution(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bucket_defs = [
        ("<24h", 0, 0),
        ("1-3d", 1, 3),
        ("3-7d", 4, 7),
        ("7-30d", 8, 30),
        (">30d", 31, None),
    ]
    counts = Counter({label: 0 for label, _, _ in bucket_defs})
    counts["unknown"] = 0
    for row in rows:
        age_days = row.get("age_days")
        if age_days is None:
            counts["unknown"] += 1
            continue
        age = int(age_days)
        for label, lower, upper in bucket_defs:
            if age >= lower and (upper is None or age <= upper):
                counts[label] += 1
                break
    order = [label for label, _, _ in bucket_defs] + ["unknown"]
    return [
        {
            "bucket": label,
            "dataflows": int(counts[label]),
            "targets": int(counts[label]),
        }
        for label in order
        if counts[label] > 0
    ]


def _watermark_coverage_by_stage(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"enabled": set(), "missing": set()}
    )
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if not dataflow_id:
            continue
        stage = _dimension_value(row.get("stage"))
        key = "enabled" if _has_watermark(row) else "missing"
        buckets[stage][key].add(_registry_key(row))
    result = []
    for stage, values in buckets.items():
        enabled = len(values["enabled"])
        missing = len(values["missing"] - values["enabled"])
        total = enabled + missing
        result.append(
            {
                "stage": stage,
                "enabled": enabled,
                "missing": missing,
                "not_configured": missing,
                "total": total,
                "coverage_rate": _rate(enabled, total),
            }
        )
    return sorted(
        result,
        key=lambda item: (
            -int(item["missing"]),
            -int(item["total"]),
            str(item["stage"]),
        ),
    )[:50]


def _skipped_streak_distribution(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets = Counter()
    for row in rows:
        streak = int(row.get("consecutive_skipped") or 0)
        if streak <= 0:
            continue
        if streak == 1:
            bucket = "1"
        elif streak <= 3:
            bucket = "2-3"
        elif streak <= 7:
            bucket = "4-7"
        else:
            bucket = ">7"
        buckets[bucket] += 1
    order = ["1", "2-3", "4-7", ">7"]
    return [
        {
            "bucket": bucket,
            "dataflows": int(buckets[bucket]),
            "targets": int(buckets[bucket]),
        }
        for bucket in order
        if buckets[bucket] > 0
    ]


def _freshness_dataflow_registry(
    latest_freshness_rows: list[dict[str, Any]],
    movement_rows: list[dict[str, Any]],
    skipped_by_dataflow: dict[tuple[Any, str], dict[str, Any]],
    etl_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    latest_movement: dict[tuple[Any, str], dict[str, Any]] = {}
    for row in movement_rows:
        dataflow_id = _dataflow_id(row)
        if not dataflow_id:
            continue
        key = _registry_key(row)
        current = latest_movement.get(key)
        if current is None or _latest_run_key(row) > _latest_run_key(current):
            latest_movement[key] = row

    latest_any: dict[tuple[Any, str], dict[str, Any]] = {}
    latest_success: dict[tuple[Any, str], dict[str, Any]] = {}
    aggregates: dict[tuple[Any, str], Counter] = defaultdict(Counter)
    latest_status_rows: dict[tuple[Any, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    rows_by_dataflow: dict[tuple[Any, str], list[dict[str, Any]]] = defaultdict(list)
    for row in etl_rows:
        dataflow_id = _dataflow_id(row)
        if not dataflow_id:
            continue
        key = _registry_key(row)
        rows_by_dataflow[key].append(row)
        status = _status(row)
        aggregates[key]["runs"] += 1
        aggregates[key][status] += 1
        if status == "succeeded":
            current_success_row = latest_success.get(key)
            if current_success_row is None or _latest_run_key(row) > _latest_run_key(current_success_row):
                latest_success[key] = row
        if status == "failed":
            current_failed = latest_status_rows[key].get("last_failed")
            if current_failed is None or _latest_run_key(row) > _latest_run_key(current_failed):
                latest_status_rows[key]["last_failed"] = row
        if status == "succeeded":
            current_success = latest_status_rows[key].get("last_success")
            if current_success is None or _latest_run_key(row) > _latest_run_key(current_success):
                latest_status_rows[key]["last_success"] = row
        current = latest_any.get(key)
        if current is None or _latest_run_key(row) > _latest_run_key(current):
            latest_any[key] = row

    result = []
    latest_freshness_by_dataflow = {
        _registry_key(row): row
        for row in latest_freshness_rows
        if row.get("dataflow_id")
    }
    all_dataflows = (
        set(latest_freshness_by_dataflow)
        | set(latest_movement)
        | set(skipped_by_dataflow)
        | set(latest_any)
    )
    for key in all_dataflows:
        dataflow_id = key[1]
        fallback = latest_any.get(key, {})
        row = latest_freshness_by_dataflow.get(key) or {
            "_source_id": key[0],
            "dataflow_name": fallback.get("dataflow_name")
            or fallback.get("dataflow_id")
            or "unknown",
            "dataflow_id": dataflow_id,
            "stage": fallback.get("stage") or "unknown",
            "target": _target_identity(fallback) if fallback else "unknown",
            "latest_freshness_at": None,
            "latest_freshness_status": None,
            "age_days": None,
            "source_name": fallback.get("source_name")
            or fallback.get("source_id")
            or "unknown",
            "destination_name": fallback.get("destination_name")
            or fallback.get("destination_id")
            or "unknown",
            "source_format": fallback.get("source_format")
            or fallback.get("source_connection_type")
            or "unknown",
            "destination_format": fallback.get("destination_format")
            or fallback.get("destination_connection_type")
            or "unknown",
            "saved_watermark": freshness_evidence(fallback).get("saved_watermark"),
            "age_seconds": None,
            "destination_load_type": fallback.get("destination_load_type") or "unknown",
            "status": _status(fallback) if fallback else "unknown",
        }
        skipped = skipped_by_dataflow.get(key, {})
        counts = aggregates.get(key, Counter())
        latest = latest_any.get(key, {})
        latest_success_row = latest_success.get(key, {})
        evidence = freshness_evidence(latest) if latest else {}
        adjustment = read_boundary_adjustment(latest) if latest else {
            "adjustment_state": "unknown",
            "adjustment_basis": None,
            "adjustment_reason": "attempt_unavailable",
            "adjustment_input_json": None,
            "adjustment_effective_json": None,
        }
        adjustment_counts = Counter(
            read_boundary_adjustment(item).get("adjustment_state")
            for item in rows_by_dataflow.get(key, [])
        )
        status_rows = latest_status_rows.get(key, {})
        entry = {
                **_freshness_master_data(latest),
                **row,
                **evidence,
                "source_watermark_before": latest.get("source_watermark_before") if latest else None,
                "source_watermark_after": latest.get("source_watermark_after") if latest else None,
                "source_watermark_effective": latest.get("source_watermark_effective") if latest else None,
                "watermark_time": latest.get("end_time") or latest.get("start_time"),
                "status": _status(latest) if latest else "unknown",
                "latest_success_watermark": _latest_success_watermark_value(
                    latest_success_row
                ),
                "skipped_streak": int(skipped.get("consecutive_skipped") or 0),
                "latest_skip_time": skipped.get("latest_time"),
                "source_action": skipped.get("source_action"),
                "latest_run_at": latest.get("end_time") or latest.get("start_time"),
                "latest_run_id": latest.get("dataflow_run_id") if latest else None,
                "latest_run_status": _status(latest) if latest else "unknown",
                "latest_message": _latest_run_message(latest),
                "latest_error_message": _latest_error_message(latest),
                "run_count": int(counts.get("runs", 0)),
                "succeeded_count": int(counts.get("succeeded", 0)),
                "failed_count": int(counts.get("failed", 0)),
                "skipped_count": int(counts.get("skipped", 0)),
                "running_count": int(counts.get("running", 0)),
                "pending_count": int(counts.get("pending", 0)),
                "read_boundary_adjustment_state": adjustment.get("adjustment_state"),
                "read_boundary_adjustment_basis": adjustment.get("adjustment_basis"),
                "read_boundary_adjustment_reason": adjustment.get("adjustment_reason"),
                "read_boundary_adjustment_input_json": adjustment.get("adjustment_input_json"),
                "read_boundary_adjustment_effective_json": adjustment.get("adjustment_effective_json"),
                "read_boundary_adjusted_runs": int(adjustment_counts.get("adjusted", 0)),
                "read_boundary_unchanged_runs": int(adjustment_counts.get("unchanged", 0)),
                "read_boundary_unknown_runs": int(adjustment_counts.get("unknown", 0)),
                "read_boundary_not_applied_runs": int(adjustment_counts.get("not_applied", 0)),
                "read_boundary_comparable_runs": int(
                    adjustment_counts.get("adjusted", 0)
                    + adjustment_counts.get("unchanged", 0)
                ),
                "read_boundary_total_runs": int(sum(adjustment_counts.values())),
                "last_statuses": _last_dataflow_statuses(
                    rows_by_dataflow.get(key, []), limit=5
                ),
                "last_success_at": (
                    status_rows["last_success"].get("end_time")
                    or status_rows["last_success"].get("start_time")
                    if status_rows.get("last_success") else None
                ),
                "last_failed_at": (
                    status_rows["last_failed"].get("end_time")
                    or status_rows["last_failed"].get("start_time")
                    if status_rows.get("last_failed") else None
                ),
            }
        # Registry provenance is always the same deterministic latest row.
        # Freshness age/evidence above may come from the latest eligible row,
        # while status, message, raw payload, session, and run identify the
        # latest observed row as one unit.
        if latest:
            entry.update(
                {
                    "_source_id": latest.get("_source_id", key[0]),
                    "log_session_id": latest.get("log_session_id"),
                    "dataflow_run_id": latest.get("dataflow_run_id"),
                    "raw_record_json": latest.get("raw_record_json"),
                    "latest_raw_record_json": latest.get("raw_record_json"),
                }
            )
        result.append(entry)
    return sorted(
        result,
        key=lambda item: _time_value(item.get("latest_freshness_at")),
        reverse=True,
    )


def _latest_run_message(row: dict[str, Any]) -> Any:
    if not row:
        return None
    present = row.get("message_present")
    if present is True or (present is None and "message" in row):
        return row.get("message")
    return row.get("error_message")


def _latest_error_message(row: dict[str, Any]) -> Any:
    """Keep phase error fallback separate from an explicit root message."""

    if not row:
        return None
    present = row.get("message_present")
    return None if present is True or (present is None and "message" in row) else row.get("error_message")


def _latest_run_key(row: dict[str, Any]) -> tuple[Any, ...]:
    timestamp = parse_utc_datetime(row.get("event_time") or row.get("__event_time") or row.get("end_time") or row.get("start_time"))
    def text_key(value: Any) -> tuple[bool, str]:
        return value is not None, str(value) if value is not None else ""
    return (
        timestamp is not None, _time_value(timestamp),
        text_key(row.get("dataflow_run_id")),
        row.get("_source_id") is not None, int(row.get("_source_id") or 0),
        text_key(row.get("log_session_id")), text_key(row.get("job_id")),
        text_key(row.get("normalized_status") or _status(row)),
        text_key(_latest_run_message(row)), text_key(row.get("error_message")),
        text_key(row.get("_file_uri")),
        row.get("_record_line") is not None,
        int(row.get("_record_line") or 0),
    )


def _latest_success_watermark_value(row: dict[str, Any]) -> Any:
    evidence = freshness_evidence(row)
    return evidence.get("saved_watermark") if evidence.get("save_status") == "saved" else None


def _last_dataflow_statuses(
    rows: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    ordered = sorted(
        rows,
        key=_latest_run_key,
        reverse=True,
    )
    return [
        {
            "status": _status(row),
            "time": row.get("end_time") or row.get("start_time"),
            "dataflow_run_id": row.get("dataflow_run_id"),
        }
        for row in ordered[:limit]
    ]


def _freshness_master_data(row: dict[str, Any]) -> dict[str, Any]:
    if not row:
        return {}
    fields = [
        "workspace_id",
        "dataflow_id",
        "dataflow_name",
        "dataflow_description",
        "stage",
        "group_number",
        "execution_order",
        "processing_mode",
        "is_active",
        "configure",
        "operation_type",
        "source_id",
        "source_name",
        "source_connection_type",
        "source_format",
        "source_catalog",
        "source_database",
        "source_schema",
        "source_table",
        "source_full_table",
        "source_path",
        "source_query",
        "source_python_function",
        "source_watermark_columns",
        "source_filter_expression",
        "source_configure",
        "source_action",
        "transform_deduplicate_columns",
        "transform_latest_data_columns",
        "transform_filter_expression",
        "transform_additional_columns",
        "transform_schema_hints",
        "transform_select_columns",
        "transform_drop_columns",
        "transform_rename_columns",
        "transform_value_rules",
        "transform_hash_columns",
        "transform_masking_rules",
        "transform_configure",
        "destination_id",
        "destination_name",
        "destination_connection_type",
        "destination_format",
        "destination_catalog",
        "destination_database",
        "destination_schema",
        "destination_table",
        "destination_full_table",
        "destination_path",
        "destination_load_type",
        "destination_merge_keys",
        "destination_partition_columns",
        "destination_configure",
    ]
    return {field: row.get(field) for field in fields if field in row}


def _consecutive_skipped_patterns(
    rows: list[dict[str, Any]], threshold: int = _SKIPPED_STREAK_RUNS
) -> list[dict[str, Any]]:
    result = [
        row
        for row in _latest_skipped_streaks_by_dataflow(rows).values()
        if int(row.get("consecutive_skipped") or 0) >= threshold
    ]
    return sorted(result, key=lambda item: item["consecutive_skipped"], reverse=True)[
        :50
    ]


def _latest_skipped_streaks_by_dataflow(
    rows: list[dict[str, Any]],
) -> dict[tuple[Any, str], dict[str, Any]]:
    buckets: dict[tuple[Any, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if dataflow_id:
            buckets[_registry_key(row)].append(row)
    result: dict[tuple[Any, str], dict[str, Any]] = {}
    for key, items in buckets.items():
        dataflow_id = key[1]
        ordered = sorted(
            items,
            key=_latest_run_key,
            reverse=True,
        )
        consecutive = 0
        for item in ordered:
            if _status(item) != "skipped":
                break
            consecutive += 1
        latest = ordered[0]
        result[key] = {
            "_source_id": key[0],
            "dataflow_id": dataflow_id,
            "dataflow_name": latest.get("dataflow_name")
            or latest.get("dataflow_id")
            or dataflow_id,
            "target": _target_identity(latest),
            "consecutive_skipped": consecutive,
            "threshold": _SKIPPED_STREAK_RUNS,
            "latest_time": latest.get("end_time") or latest.get("start_time"),
            "source_action": latest.get("source_action") or "unknown",
        }
    return result


def _check_eligible(row: dict[str, Any]) -> bool:
    if _dataflow_operation_type(row) != "etl" or _status(row) not in {"succeeded", "skipped"}:
        return False
    # A read/check attempt is required for a freshness age.  A terminal v6
    # record with no attempt is a preflight/no-op outcome, not evidence.
    if is_supported_log_row(row) or row.get("raw_record_json"):
        attempt = final_attempt(row)
        source = attempt.get("source") if attempt else None
        return isinstance(source, dict) and str(source.get("status") or "").lower() in {
            "succeeded", "skipped"
        }
    return True


def _has_watermark(row: dict[str, Any]) -> bool:
    return freshness_evidence(row).get("coverage_state") == "configured"


def _target_identity(row: dict[str, Any]) -> str:
    schema = row.get("destination_schema") or row.get("destination_schema_name")
    return str(
        row.get("destination_full_table")
        or ".".join(
            part
            for part in (
                row.get("destination_catalog"),
                row.get("destination_database"),
                schema,
                row.get("destination_table"),
            )
            if part
        )
        or row.get("destination_path")
        or row.get("destination_table")
        or row.get("dataflow_name")
        or row.get("dataflow_id")
        or "unknown"
    )


def _registry_key(row: dict[str, Any]) -> tuple[Any, str]:
    """Registry grain: source and dataflow across all sessions."""

    return row.get("_source_id"), str(_dataflow_id(row))
