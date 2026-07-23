from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any
from datacoolie_studio.core.time import parse_utc_datetime
from datacoolie_studio.domains.monitoring.metrics.common_projections import (
    _dataflow_id,
    _dataflow_operation_type,
    _date_bucket,
    _dimension_value,
    _is_missing_value,
    _percentile,
    _rate,
    _status,
    _time_value,
    _trend_context,
    _watermark_classification,
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
        row for row in etl_rows if _status(row) in {"succeeded", "skipped"}
    ]
    watermark_rows = [row for row in etl_rows if _has_watermark(row)]
    movement = [
        _watermark_movement_row(row) for row in watermark_rows if _dataflow_id(row)
    ]
    advanced = [row for row in movement if row["movement"] == "advanced"]
    initialized = [row for row in movement if row["movement"] == "initialized"]
    unchanged = [row for row in movement if row["movement"] == "unchanged"]
    incomplete = [row for row in movement if row["movement"] == "incomplete"]
    invalid = [row for row in movement if row["movement"] == "invalid"]
    unknown = [row for row in movement if row["movement"] == "unknown"]
    adjusted = [row for row in movement if row.get("adjustment_state") == "adjusted"]
    latest_freshness = _latest_freshness_by_dataflow(freshness_rows)
    stale_candidates = _stale_freshness_candidates_from_latest(
        latest_freshness, days=_FRESHNESS_STALE_DAYS
    )
    skipped_patterns = _consecutive_skipped_patterns(
        etl_rows, threshold=_SKIPPED_STREAK_RUNS
    )
    skipped_by_dataflow = _latest_skipped_streaks_by_dataflow(etl_rows)
    observed_dataflows = len(
        {dataflow_id for row in etl_rows if (dataflow_id := _dataflow_id(row))}
    )
    watermark_dataflows = len(
        {dataflow_id for row in watermark_rows if (dataflow_id := _dataflow_id(row))}
    )
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
        latest_freshness, movement, skipped_by_dataflow, etl_rows
    )
    stale_dataflow_count = len(stale_candidates)
    latest_by_dataflow = _latest_rows_by_dataflow(etl_rows)
    latest_watermark_states = [
        _watermark_classification(row) for row in latest_by_dataflow.values()
    ]
    latest_invalid_watermarks = [
        row for row in latest_watermark_states if row["movement_state"] == "invalid"
    ]
    latest_incomplete_watermarks = [
        row for row in latest_watermark_states if row["movement_state"] == "incomplete"
    ]
    latest_status_issues = [
        row
        for row in latest_by_dataflow.values()
        if _status(row) not in {"succeeded", "skipped"}
    ]
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
            "latest_watermark_invalid_dataflows": len(latest_invalid_watermarks),
            "latest_watermark_incomplete_dataflows": len(latest_incomplete_watermarks),
            "latest_watermark_issue_dataflows": len(latest_invalid_watermarks)
            + len(latest_incomplete_watermarks),
            "watermark_enabled_dataflows": len(
                {
                    dataflow_id
                    for row in watermark_rows
                    if (dataflow_id := _dataflow_id(row))
                }
            ),
            "watermark_coverage_rate": _rate(watermark_dataflows, observed_dataflows),
            "watermark_advanced_runs": len(advanced),
            "watermark_initialized_runs": len(initialized),
            "watermark_unchanged_runs": len(unchanged),
            "watermark_incomplete_runs": len(incomplete),
            "watermark_adjusted_runs": len(adjusted),
            "watermark_invalid_runs": len(invalid),
            "watermark_unknown_runs": len(unknown),
            "watermark_advanced_rate": _rate(
                len(advanced), len(advanced) + len(unchanged)
            ),
            "skipped_no_new_data": len(skipped),
            "skipped_streak_threshold": _SKIPPED_STREAK_RUNS,
            "skipped_streak_dataflows": len(skipped_patterns),
            "stale_candidates": stale_dataflow_count,
            "stale_dataflows": stale_dataflow_count,
            "stale_threshold_days": _FRESHNESS_STALE_DAYS,
            "stale_dataflow_rate": _rate(stale_dataflow_count, observed_dataflows),
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
            watermark_rows, trend_context=trend_context
        ),
        "age_distribution": _freshness_age_distribution(latest_freshness),
        "watermark_coverage_by_stage": _watermark_coverage_by_stage(etl_rows),
        "skipped_streak_distribution": _skipped_streak_distribution(skipped_patterns),
        "dataflow_registry": dataflow_registry,
        "stale_candidates": stale_candidates,
        "skipped_patterns": skipped_patterns,
    }


def _latest_freshness_by_dataflow(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    now = datetime.now(timezone.utc)
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if dataflow_id:
            buckets[dataflow_id].append(row)
    result = []
    for dataflow_id, items in buckets.items():
        latest = max(
            items,
            key=lambda item: _time_value(
                item.get("end_time") or item.get("start_time")
            ),
        )
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
                "source_watermark_effective": latest.get("source_watermark_effective"),
                "destination_load_type": latest.get("destination_load_type")
                or "unknown",
                "status": _status(latest),
            }
        )
    return sorted(
        result, key=lambda item: _time_value(item["latest_freshness_at"]), reverse=True
    )


def _latest_rows_by_dataflow(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if not dataflow_id:
            continue
        current = result.get(dataflow_id)
        if current is None or _time_value(
            row.get("end_time") or row.get("start_time")
        ) > _time_value(current.get("end_time") or current.get("start_time")):
            result[dataflow_id] = row
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
        "adjustment_state": watermark["adjustment_state"],
        "movement": movement,
        "before": watermark["before"],
        "after": watermark["after"],
        "effective": watermark["effective"],
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
        buckets[bucket["bucket"]][movement_row["movement"]] += 1
        if movement_row.get("adjustment_state") == "adjusted":
            buckets[bucket["bucket"]]["adjusted"] += 1
        bucket_metadata[bucket["bucket"]] = bucket
    result = []
    for bucket_key, counts in buckets.items():
        advanced = int(counts.get("advanced", 0))
        initialized = int(counts.get("initialized", 0))
        unchanged = int(counts.get("unchanged", 0))
        incomplete = int(counts.get("incomplete", 0))
        invalid = int(counts.get("invalid", 0))
        unknown = int(counts.get("unknown", 0))
        adjusted = int(counts.get("adjusted", 0))
        comparable = advanced + unchanged
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
                "incomplete": incomplete,
                "invalid": invalid,
                "unknown": unknown,
                "adjusted": adjusted,
                "total": advanced
                + initialized
                + unchanged
                + incomplete
                + invalid
                + unknown,
                "advanced_rate": _rate(advanced, comparable) if comparable else None,
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
        buckets[stage][key].add(dataflow_id)
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
    skipped_by_dataflow: dict[str, dict[str, Any]],
    etl_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    latest_movement: dict[str, dict[str, Any]] = {}
    for row in movement_rows:
        dataflow_id = str(row.get("dataflow_id") or "")
        if not dataflow_id:
            continue
        current = latest_movement.get(dataflow_id)
        if current is None or _time_value(row.get("end_time")) > _time_value(
            current.get("end_time")
        ):
            latest_movement[dataflow_id] = row

    latest_any: dict[str, dict[str, Any]] = {}
    latest_success: dict[str, dict[str, Any]] = {}
    aggregates: dict[str, Counter] = defaultdict(Counter)
    latest_status_time: dict[str, dict[str, Any]] = defaultdict(dict)
    rows_by_dataflow: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in etl_rows:
        dataflow_id = _dataflow_id(row)
        if not dataflow_id:
            continue
        rows_by_dataflow[dataflow_id].append(row)
        status = _status(row)
        aggregates[dataflow_id]["runs"] += 1
        aggregates[dataflow_id][status] += 1
        row_time = row.get("end_time") or row.get("start_time")
        if status == "succeeded":
            current_success = latest_status_time[dataflow_id].get("last_success_at")
            if current_success is None or _time_value(row_time) > _time_value(
                current_success
            ):
                latest_status_time[dataflow_id]["last_success_at"] = row_time
            current_success_row = latest_success.get(dataflow_id)
            if current_success_row is None or _time_value(row_time) > _time_value(
                current_success_row.get("end_time")
                or current_success_row.get("start_time")
            ):
                latest_success[dataflow_id] = row
        if status == "failed":
            current_failed = latest_status_time[dataflow_id].get("last_failed_at")
            if current_failed is None or _time_value(row_time) > _time_value(
                current_failed
            ):
                latest_status_time[dataflow_id]["last_failed_at"] = row_time
        current = latest_any.get(dataflow_id)
        if current is None or _time_value(row_time) > _time_value(
            current.get("end_time") or current.get("start_time")
        ):
            latest_any[dataflow_id] = row

    result = []
    latest_freshness_by_dataflow = {
        str(row.get("dataflow_id") or ""): row
        for row in latest_freshness_rows
        if row.get("dataflow_id")
    }
    all_dataflows = (
        set(latest_freshness_by_dataflow)
        | set(latest_movement)
        | set(skipped_by_dataflow)
        | set(latest_any)
    )
    for dataflow_id in all_dataflows:
        fallback = latest_any.get(dataflow_id, {})
        row = latest_freshness_by_dataflow.get(dataflow_id) or {
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
            "source_watermark_effective": fallback.get("source_watermark_effective"),
            "age_seconds": None,
            "destination_load_type": fallback.get("destination_load_type") or "unknown",
            "status": _status(fallback) if fallback else "unknown",
        }
        movement = latest_movement.get(dataflow_id, {})
        skipped = skipped_by_dataflow.get(dataflow_id, {})
        movement_state = movement.get("movement_state") or movement.get("movement")
        adjustment_state = movement.get("adjustment_state")
        counts = aggregates.get(dataflow_id, Counter())
        latest = latest_any.get(dataflow_id, {})
        latest_success_row = latest_success.get(dataflow_id, {})
        result.append(
            {
                **_freshness_master_data(latest),
                **row,
                "coverage_state": movement.get("coverage_state")
                or (
                    "configured"
                    if row.get("source_watermark_effective")
                    else "not_configured"
                ),
                "movement_state": movement_state
                or (
                    "not_configured"
                    if not row.get("source_watermark_effective")
                    else "incomplete"
                ),
                "adjustment_state": adjustment_state
                or (
                    "unknown"
                    if row.get("source_watermark_effective")
                    else "not_configured"
                ),
                "watermark_time": movement.get("end_time"),
                "source_watermark_before": movement.get("before"),
                "source_watermark_after": movement.get("after"),
                "source_watermark_effective": movement.get("effective")
                or row.get("source_watermark_effective"),
                "latest_success_watermark": _latest_success_watermark_value(
                    latest_success_row
                ),
                "skipped_streak": int(skipped.get("consecutive_skipped") or 0),
                "latest_skip_time": skipped.get("latest_time"),
                "source_action": skipped.get("source_action"),
                "latest_run_at": latest.get("end_time") or latest.get("start_time"),
                "latest_run_status": _status(latest) if latest else "unknown",
                "latest_error_message": (
                    latest.get("error_message") or latest.get("error_messages")
                )
                if latest
                else None,
                "run_count": int(counts.get("runs", 0)),
                "succeeded_count": int(counts.get("succeeded", 0)),
                "failed_count": int(counts.get("failed", 0)),
                "skipped_count": int(counts.get("skipped", 0)),
                "running_count": int(counts.get("running", 0)),
                "pending_count": int(counts.get("pending", 0)),
                "last_statuses": _last_dataflow_statuses(
                    rows_by_dataflow.get(dataflow_id, []), limit=5
                ),
                **latest_status_time.get(dataflow_id, {}),
            }
        )
    return sorted(
        result,
        key=lambda item: _time_value(item.get("latest_freshness_at")),
        reverse=True,
    )


def _latest_success_watermark_value(row: dict[str, Any]) -> Any:
    if not row:
        return None
    for field in (
        "source_watermark_after",
        "source_watermark_effective",
        "source_watermark_before",
    ):
        value = row.get(field)
        if not _is_missing_value(value):
            return value
    return None


def _last_dataflow_statuses(
    rows: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    ordered = sorted(
        rows,
        key=lambda item: _time_value(item.get("end_time") or item.get("start_time")),
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
) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if dataflow_id:
            buckets[dataflow_id].append(row)
    result: dict[str, dict[str, Any]] = {}
    for dataflow_id, items in buckets.items():
        ordered = sorted(
            items,
            key=lambda item: _time_value(
                item.get("end_time") or item.get("start_time")
            ),
            reverse=True,
        )
        consecutive = 0
        for item in ordered:
            if _status(item) != "skipped":
                break
            consecutive += 1
        latest = ordered[0]
        result[dataflow_id] = {
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


def _has_watermark(row: dict[str, Any]) -> bool:
    return any(
        row.get(key)
        for key in (
            "source_watermark_columns",
            "source_watermark_before",
            "source_watermark_after",
            "source_watermark_effective",
        )
    )


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
