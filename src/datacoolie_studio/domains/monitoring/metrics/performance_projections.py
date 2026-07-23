from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any
from datacoolie_studio.domains.monitoring.metrics.common_projections import (
    _avg,
    _dataflow_id,
    _dataflow_operation_type,
    _date_bucket,
    _dimension_value,
    _dominant_dataflow_operation_type,
    _duration_stats,
    _error_preview,
    _failure_enriched_dataflow,
    _num,
    _percentile,
    _percentile_clean,
    _performance_phase_duration,
    _phase_duration_summary,
    _phase_health,
    _rate,
    _row_timestamp,
    _status,
    _sum,
    _time_value,
    _trend_context,
)


def _performance_page(
    rows: list[dict[str, Any]],
    trend_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    phase_runs = [
        row for row in rows if _status(row) in {"succeeded", "failed", "skipped"}
    ]
    executable = [
        row
        for row in rows
        if _status(row) in {"succeeded", "failed"}
        and _num(row, "duration_seconds") is not None
    ]
    durations = [_num(row, "duration_seconds") or 0 for row in executable]
    duration_stats = _duration_stats(executable)
    thresholds_by_operation = _performance_thresholds_by_operation(executable)
    executable = [
        _performance_enriched_run(
            row,
            thresholds_by_operation.get(
                _dataflow_operation_type(row), thresholds_by_operation["__all__"]
            ),
        )
        for row in executable
    ]
    candidate_counts: Counter[str] = Counter()
    for row in executable:
        for code in row.get("performance_candidate_codes") or []:
            candidate_counts[str(code)] += 1
    phase_totals = _performance_phase_totals(executable)
    phase_total_duration = sum(phase_totals.values())
    bottleneck_phase = (
        max(phase_totals.items(), key=lambda item: item[1])[0]
        if phase_total_duration > 0
        else "unknown"
    )
    throughput_duration = sum(_num(row, "duration_seconds") or 0 for row in executable)
    total_rows_read = _sum(executable, "source_rows_read")
    total_rows_written = _sum(executable, "destination_rows_written")
    return {
        "kpis": {
            "run_count": len(executable),
            "avg_duration_seconds": duration_stats.get("avg_duration_seconds", 0),
            "p50_duration_seconds": duration_stats.get("p50_duration_seconds", 0),
            "p75_duration_seconds": duration_stats.get("q3_duration_seconds", 0),
            "p95_duration_seconds": duration_stats.get("p95_duration_seconds", 0),
            "p99_duration_seconds": duration_stats.get("p99_duration_seconds", 0),
            "max_duration_seconds": duration_stats.get("max_duration_seconds", 0),
            "duration_pressure_ratio": _safe_ratio(
                duration_stats.get("p95_duration_seconds", 0) or 0,
                duration_stats.get("p50_duration_seconds", 0) or 0,
            ),
            "duration_outlier_count": sum(
                int(row.get("outlier_count") or 0)
                for row in _performance_duration_distribution_by_stage(
                    executable, limit=None
                )
            ),
            "slowest_run_duration_seconds": round(max(durations), 3)
            if durations
            else 0,
            "slowest_run_dataflow_name": (
                max(executable, key=lambda row: _num(row, "duration_seconds") or 0).get(
                    "dataflow_name"
                )
                if executable
                else None
            ),
            "slowest_run_stage": (
                max(executable, key=lambda row: _num(row, "duration_seconds") or 0).get(
                    "stage"
                )
                if executable
                else None
            ),
            "bottleneck_phase": bottleneck_phase,
            "source_duration_percent": _rate(
                phase_totals["source"], phase_total_duration
            ),
            "transform_duration_percent": _rate(
                phase_totals["transform"], phase_total_duration
            ),
            "destination_duration_percent": _rate(
                phase_totals["destination"], phase_total_duration
            ),
            "overhead_duration_percent": _rate(
                phase_totals["overhead"], phase_total_duration
            ),
            "rows_read_per_second": _safe_ratio(total_rows_read, throughput_duration),
            "total_rows_read": total_rows_read,
            "total_rows_written": total_rows_written,
            "optimization_candidate_count": sum(
                1 for row in executable if row.get("performance_candidate_code")
            ),
            "slow_small_workload_count": candidate_counts.get("slow_small_workload", 0),
            "slow_small_maintenance_count": candidate_counts.get(
                "slow_small_maintenance", 0
            ),
            "high_overhead_count": candidate_counts.get("high_overhead", 0),
            "phase_skew_count": candidate_counts.get("phase_skew", 0),
        },
        "duration_distribution_by_stage": _performance_duration_distribution_by_stage(
            executable
        ),
        "phase_contribution_by_stage_operation": _performance_phase_contribution_by_stage_operation(
            phase_runs
        ),
        "workload_efficiency_points": _performance_workload_efficiency_points(
            executable
        ),
        "slowest_dataflow_profiles": _performance_slowest_dataflow_profiles(executable),
        "runtime_context_profiles": _performance_runtime_context_profiles(executable),
        "performance_trend": _performance_trend(
            executable, trend_context=trend_context
        ),
        "investigation_queue": [
            _compact_performance_evidence(row)
            for row in _performance_investigation_queue(executable)
        ],
        "duration_breakdown": _duration_breakdown(executable),
        "duration_vs_rows": _duration_vs_rows(executable),
        "slowest_dataflows": [
            _compact_performance_evidence(row)
            for row in sorted(
                executable,
                key=lambda row: _num(row, "duration_seconds") or 0,
                reverse=True,
            )[:25]
        ],
        "slowest_dataflows_by_p95": _slowest_dataflows_by_p95(executable),
        "overview_p95_duration_seconds": _percentile(durations, 0.95),
        "duration_by_stage": _duration_by_stage(executable),
        "engine_stage_matrix": _engine_stage_matrix(executable),
    }


def _performance_enriched_run(
    row: dict[str, Any], thresholds: dict[str, float]
) -> dict[str, Any]:
    rows_processed = _performance_rows_processed(row)
    maintenance_bytes, maintenance_files = _performance_maintenance_workload(row)
    source_duration = _performance_phase_duration(row, "source")
    transform_duration = _performance_phase_duration(row, "transform")
    destination_duration = _performance_phase_duration(row, "destination")
    overhead_duration = _performance_phase_duration(row, "overhead")
    phase_totals = {
        "source": source_duration,
        "transform": transform_duration,
        "destination": destination_duration,
        "overhead": overhead_duration,
    }
    bottleneck_phase = (
        max(phase_totals.items(), key=lambda item: item[1])[0]
        if any(phase_totals.values())
        else "unknown"
    )
    duration = _num(row, "duration_seconds") or 0
    matched_reasons = _performance_candidate_reasons(
        duration=duration,
        rows_processed=rows_processed,
        operation_type=_dataflow_operation_type(row),
        maintenance_bytes=maintenance_bytes,
        maintenance_files=maintenance_files,
        phase_totals=phase_totals,
        thresholds=thresholds,
    )
    primary_reason = matched_reasons[0] if matched_reasons else (None, None, 0)
    return {
        **row,
        "rows_processed": rows_processed,
        "maintenance_bytes_processed": maintenance_bytes,
        "maintenance_files_processed": maintenance_files,
        "rows_read_per_second": _safe_ratio(
            _num(row, "source_rows_read") or 0, duration
        ),
        "lakehouse_bytes_moved": (_num(row, "destination_bytes_added") or 0)
        + (_num(row, "destination_bytes_removed") or 0),
        "source_duration_seconds": source_duration,
        "transform_duration_seconds": transform_duration,
        "destination_duration_seconds": destination_duration,
        "overhead_duration_seconds": overhead_duration,
        "performance_bottleneck_phase": bottleneck_phase,
        "performance_candidate_codes": [reason[0] for reason in matched_reasons],
        "performance_candidate_reasons": [reason[1] for reason in matched_reasons],
        "performance_candidate_code": primary_reason[0],
        "performance_candidate_reason": primary_reason[1],
        "performance_candidate_priority": primary_reason[2],
    }


def _performance_candidate_reasons(
    *,
    duration: float,
    rows_processed: float,
    operation_type: str,
    maintenance_bytes: float,
    maintenance_files: float,
    phase_totals: dict[str, float],
    thresholds: dict[str, float],
) -> list[tuple[str, str, int]]:
    duration_p75 = thresholds.get("duration_p75_seconds", 0) or 0
    duration_p95 = thresholds.get("duration_p95_seconds", 0) or 0
    rows_p50 = thresholds.get("rows_processed_p50", 0) or 0
    maintenance_bytes_p50 = thresholds.get("maintenance_bytes_p50", 0) or 0
    maintenance_files_p50 = thresholds.get("maintenance_files_p50", 0) or 0
    total_phase_duration = sum(phase_totals.values())
    overhead_share = _safe_ratio(phase_totals.get("overhead", 0), total_phase_duration)
    largest_phase = (
        max(phase_totals.items(), key=lambda item: item[1])
        if total_phase_duration > 0
        else ("unknown", 0)
    )
    largest_phase_share = _safe_ratio(largest_phase[1], total_phase_duration)

    reasons: list[tuple[str, str, int]] = []
    is_maintenance = operation_type == "maintenance"
    if (
        not is_maintenance
        and rows_p50 > 0
        and 0 < rows_processed <= rows_p50
        and duration_p95 > 0
        and duration >= duration_p95
    ):
        reasons.append(("slow_small_workload", "Slow small workload", 300))
    maintenance_is_small = (
        maintenance_bytes_p50 > 0 and 0 < maintenance_bytes <= maintenance_bytes_p50
    ) or (
        maintenance_bytes <= 0
        and maintenance_files_p50 > 0
        and 0 < maintenance_files <= maintenance_files_p50
    )
    if (
        is_maintenance
        and maintenance_is_small
        and duration_p95 > 0
        and duration >= duration_p95
    ):
        reasons.append(
            ("slow_small_maintenance", "Slow small maintenance workload", 300)
        )
    if duration_p75 > 0 and duration >= duration_p75 and overhead_share >= 0.20:
        reasons.append(("high_overhead", "High overhead", 200))
    if (
        not is_maintenance
        and duration_p75 > 0
        and duration >= duration_p75
        and largest_phase_share >= 0.90
    ):
        reasons.append(
            ("phase_skew", f"{_title_word(largest_phase[0])} phase skew", 100)
        )
    return sorted(reasons, key=lambda reason: reason[2], reverse=True)


def _performance_thresholds_by_operation(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[_dataflow_operation_type(row)].append(row)

    def thresholds(items: list[dict[str, Any]]) -> dict[str, float]:
        durations = [
            _num(item, "duration_seconds")
            for item in items
            if _num(item, "duration_seconds") is not None
        ]
        rows_processed = [_performance_rows_processed(item) for item in items]
        maintenance_workload = [
            _performance_maintenance_workload(item) for item in items
        ]
        return {
            "duration_p75_seconds": _percentile_clean(durations, 0.75),
            "duration_p95_seconds": _percentile_clean(durations, 0.95),
            "rows_processed_p50": _percentile_clean(
                [value for value in rows_processed if value > 0], 0.50
            ),
            "maintenance_bytes_p50": _percentile_clean(
                [value[0] for value in maintenance_workload if value[0] > 0], 0.50
            ),
            "maintenance_files_p50": _percentile_clean(
                [value[1] for value in maintenance_workload if value[1] > 0], 0.50
            ),
        }

    return {
        "__all__": thresholds(rows),
        **{
            operation_type: thresholds(items)
            for operation_type, items in buckets.items()
        },
    }


def _performance_maintenance_workload(row: dict[str, Any]) -> tuple[float, float]:
    bytes_processed = sum(
        _num(row, field) or 0
        for field in (
            "destination_bytes_added",
            "destination_bytes_removed",
            "destination_bytes_saved",
        )
    )
    files_processed = sum(
        _num(row, field) or 0
        for field in ("destination_files_added", "destination_files_removed")
    )
    return max(0.0, bytes_processed), max(0.0, files_processed)


def _performance_rows_processed(row: dict[str, Any]) -> float:
    row_candidates = [
        _num(row, "source_rows_read") or 0,
        _num(row, "destination_rows_written") or 0,
        (_num(row, "destination_rows_inserted") or 0)
        + (_num(row, "destination_rows_updated") or 0)
        + (_num(row, "destination_rows_deleted") or 0),
    ]
    return max(row_candidates)


def _performance_phase_totals(rows: list[dict[str, Any]]) -> dict[str, float]:
    return {
        phase: sum(_performance_phase_duration(row, phase) for row in rows)
        for phase in ("source", "transform", "destination", "overhead")
    }


def _performance_duration_distribution_by_stage(
    rows: list[dict[str, Any]],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    return _performance_duration_distribution(
        rows,
        group_key="stage",
        resolve_group=lambda row: _dimension_value(row.get("stage")),
        limit=limit,
    )


def _performance_duration_distribution(
    rows: list[dict[str, Any]],
    *,
    group_key: str,
    resolve_group,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        duration = _num(row, "duration_seconds")
        if duration is None:
            continue
        buckets[resolve_group(row)].append(row)

    result: list[dict[str, Any]] = []
    for group_value, items in buckets.items():
        clean = [
            _num(item, "duration_seconds")
            for item in items
            if _num(item, "duration_seconds") is not None
        ]
        if not clean:
            continue
        q1_duration = _percentile(clean, 0.25)
        q3_duration = _percentile(clean, 0.75)
        iqr = q3_duration - q1_duration
        lower_fence = q1_duration - 1.5 * iqr
        upper_fence = q3_duration + 1.5 * iqr
        non_outlier_durations = [
            duration for duration in clean if lower_fence <= duration <= upper_fence
        ] or clean
        outliers = [
            {
                "duration_seconds": round(duration, 3),
                "dataflow_name": item.get("dataflow_name")
                or item.get("dataflow_id")
                or "unknown",
                "dataflow_id": item.get("dataflow_id"),
                "dataflow_run_id": item.get("dataflow_run_id"),
                "status": _status(item),
                "operation_type": _dataflow_operation_type(item),
            }
            for item in items
            for duration in [_num(item, "duration_seconds")]
            if duration is not None
            and (duration < lower_fence or duration > upper_fence)
        ]
        statuses = Counter(_status(item) for item in items)
        operation_mix = Counter(_dataflow_operation_type(item) for item in items)
        result.append(
            {
                group_key: group_value,
                "group": group_value,
                "count": len(clean),
                "min_duration_seconds": round(min(clean), 3),
                "whisker_min_duration_seconds": round(min(non_outlier_durations), 3),
                "q1_duration_seconds": q1_duration,
                "p50_duration_seconds": _percentile(clean, 0.50),
                "q3_duration_seconds": q3_duration,
                "p95_duration_seconds": _percentile(clean, 0.95),
                "p99_duration_seconds": _percentile(clean, 0.99),
                "whisker_max_duration_seconds": round(max(non_outlier_durations), 3),
                "max_duration_seconds": round(max(clean), 3),
                "avg_duration_seconds": _avg(clean),
                "succeeded": statuses.get("succeeded", 0),
                "failed": statuses.get("failed", 0),
                "skipped": statuses.get("skipped", 0),
                "running": statuses.get("running", 0),
                "pending": statuses.get("pending", 0),
                "unknown": statuses.get("unknown", 0),
                "operation_mix": ", ".join(
                    f"{name}: {count}" for name, count in sorted(operation_mix.items())
                ),
                "outlier_count": len(outliers),
                "outliers": sorted(
                    outliers,
                    key=lambda item: float(item["duration_seconds"]),
                    reverse=True,
                )[:8],
            }
        )
    sorted_result = sorted(
        result,
        key=lambda row: (
            -float(row["p95_duration_seconds"]),
            -int(row["count"]),
            str(row["group"]),
        ),
    )
    return sorted_result[:limit] if limit is not None else sorted_result


def _performance_phase_contribution_by_stage_operation(
    rows: list[dict[str, Any]],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    return _phase_duration_summary(
        rows,
        group_key="context",
        resolve_group=lambda row: (
            f"{_dataflow_operation_type(row)} · {_dimension_value(row.get('stage'))}"
        ),
        limit=limit,
    )


def _performance_workload_efficiency_points(
    rows: list[dict[str, Any]], limit: int = 200
) -> list[dict[str, Any]]:
    def sort_key(row: dict[str, Any]) -> tuple[int, float, float]:
        return (
            int(row.get("performance_candidate_priority") or 0),
            _num(row, "duration_seconds") or 0,
            _performance_rows_processed(row),
        )

    ranked = sorted(rows, key=sort_key, reverse=True)
    maintenance = [
        row for row in ranked if _dataflow_operation_type(row) == "maintenance"
    ]
    pipeline = [row for row in ranked if _dataflow_operation_type(row) != "maintenance"]
    if maintenance and pipeline:
        maintenance_limit = max(1, limit // 4)
        selected = [
            *pipeline[: limit - maintenance_limit],
            *maintenance[:maintenance_limit],
        ]
        selected_ids = {id(row) for row in selected}
        selected.extend(
            row
            for row in ranked
            if id(row) not in selected_ids and len(selected) < limit
        )
        selected.sort(key=sort_key, reverse=True)
    else:
        selected = ranked[:limit]

    points = []
    for row in selected:
        duration = _num(row, "duration_seconds") or 0
        rows_processed = _performance_rows_processed(row)
        points.append(
            {
                "job_id": row.get("job_id"),
                "dataflow_id": row.get("dataflow_id"),
                "dataflow_run_id": row.get("dataflow_run_id"),
                "dataflow_name": row.get("dataflow_name")
                or row.get("dataflow_id")
                or "unknown",
                "stage": row.get("stage") or "unknown",
                "operation_type": _dataflow_operation_type(row),
                "status": _status(row),
                "rows_processed": rows_processed,
                "rows_read_per_second": _safe_ratio(
                    _num(row, "source_rows_read") or 0, duration
                ),
                "duration_seconds": duration,
                "lakehouse_bytes_moved": (_num(row, "destination_bytes_added") or 0)
                + (_num(row, "destination_bytes_removed") or 0),
                "destination_bytes_added": _num(row, "destination_bytes_added") or 0,
                "destination_bytes_removed": _num(row, "destination_bytes_removed")
                or 0,
                "performance_bottleneck_phase": row.get("performance_bottleneck_phase")
                or "unknown",
                "performance_candidate_reason": row.get("performance_candidate_reason"),
                "performance_candidate_reasons": row.get(
                    "performance_candidate_reasons"
                )
                or [],
                "performance_candidate_priority": row.get(
                    "performance_candidate_priority"
                )
                or 0,
                "maintenance_bytes_processed": row.get("maintenance_bytes_processed")
                or 0,
                "maintenance_files_processed": row.get("maintenance_files_processed")
                or 0,
            }
        )
    return points


def _performance_slowest_dataflow_profiles(
    rows: list[dict[str, Any]], limit: int | None = None
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if dataflow_id:
            buckets[dataflow_id].append(row)

    result: list[dict[str, Any]] = []
    for dataflow_id, items in buckets.items():
        durations = [
            _num(item, "duration_seconds")
            for item in items
            if _num(item, "duration_seconds") is not None
        ]
        if not durations:
            continue
        latest = max(items, key=lambda item: _row_timestamp(item) or datetime.min)
        phase_totals = _performance_phase_totals(items)
        bottleneck_phase = (
            max(phase_totals.items(), key=lambda item: item[1])[0]
            if any(phase_totals.values())
            else "unknown"
        )
        result.append(
            {
                "dataflow_id": dataflow_id,
                "dataflow_name": latest.get("dataflow_name") or dataflow_id,
                "stage": latest.get("stage") or "unknown",
                "operation_type": _dominant_dataflow_operation_type(items),
                "run_count": len(items),
                "avg_duration_seconds": _avg(durations),
                "p50_duration_seconds": _percentile(durations, 0.50),
                "p95_duration_seconds": _percentile(durations, 0.95),
                "p99_duration_seconds": _percentile(durations, 0.99),
                "max_duration_seconds": round(max(durations), 3),
                "performance_bottleneck_phase": bottleneck_phase,
                "source_name": latest.get("source_name")
                or latest.get("source_connection_name"),
                "destination_name": latest.get("destination_name")
                or latest.get("destination_connection_name"),
                "source_format": latest.get("source_format"),
                "destination_format": latest.get("destination_format"),
            }
        )
    sorted_result = sorted(
        result,
        key=lambda row: (
            -float(row["p95_duration_seconds"]),
            -float(row["max_duration_seconds"]),
            -int(row["run_count"]),
        ),
    )
    return sorted_result[:limit] if limit is not None else sorted_result


def _performance_runtime_context_profiles(
    rows: list[dict[str, Any]], limit: int | None = None
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[
            (
                _dimension_value(row.get("platform_name")),
                _dimension_value(row.get("engine_name")),
                _dimension_value(row.get("metadata_provider_name")),
            )
        ].append(row)

    result: list[dict[str, Any]] = []
    for (platform, engine, provider), items in buckets.items():
        statuses = Counter(_status(item) for item in items)
        executable = statuses.get("succeeded", 0) + statuses.get("failed", 0)
        durations = [
            _num(item, "duration_seconds")
            for item in items
            if _num(item, "duration_seconds") is not None
        ]
        total_duration = sum(durations)
        rows_read = _sum(items, "source_rows_read")
        result.append(
            {
                "platform_name": platform,
                "engine_name": engine,
                "metadata_provider_name": provider,
                "context": f"{platform} · {engine} · {provider}",
                "runs": len(items),
                "success_rate": _rate(statuses.get("succeeded", 0), executable),
                "failed": statuses.get("failed", 0),
                "avg_duration_seconds": _avg(durations),
                "p50_duration_seconds": _percentile_clean(durations, 0.50),
                "p95_duration_seconds": _percentile_clean(durations, 0.95),
                "rows_read_per_second": _safe_ratio(rows_read, total_duration),
                "slow_candidate_count": sum(
                    1 for item in items if item.get("performance_candidate_code")
                ),
            }
        )
    sorted_result = sorted(
        result,
        key=lambda row: (
            -int(row["slow_candidate_count"]),
            -float(row["p95_duration_seconds"]),
            -int(row["runs"]),
            str(row["context"]),
        ),
    )
    return sorted_result[:limit] if limit is not None else sorted_result


def _performance_trend(
    rows: list[dict[str, Any]],
    trend_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    trend_context = trend_context or _trend_context({}, rows, timezone.utc)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    bucket_metadata: dict[str, dict[str, str | None]] = {}
    for row in rows:
        bucket = _date_bucket(row, trend_context)
        buckets[str(bucket["bucket"])].append(row)
        bucket_metadata[str(bucket["bucket"])] = bucket

    result: list[dict[str, Any]] = []
    for bucket_key, items in buckets.items():
        durations = [
            _num(item, "duration_seconds")
            for item in items
            if _num(item, "duration_seconds") is not None
        ]
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
                "run_count": len(items),
                "p50_duration_seconds": _percentile_clean(durations, 0.50),
                "p95_duration_seconds": _percentile_clean(durations, 0.95),
                "candidate_count": sum(
                    1 for item in items if item.get("performance_candidate_code")
                ),
            }
        )
    return sorted(result, key=lambda item: str(item["bucket_start"] or item["bucket"]))


def _performance_investigation_queue(
    rows: list[dict[str, Any]], limit: int = 500
) -> list[dict[str, Any]]:
    def sort_key(row: dict[str, Any]) -> tuple[int, float, datetime]:
        return (
            int(row.get("performance_candidate_priority") or 0),
            _num(row, "duration_seconds") or 0,
            _time_value(row.get("end_time") or row.get("start_time")),
        )

    return sorted(rows, key=sort_key, reverse=True)[:limit]


_PERFORMANCE_EVIDENCE_FIELDS = {
    "job_id",
    "dataflow_id",
    "dataflow_run_id",
    "dataflow_name",
    "stage",
    "status",
    "start_time",
    "end_time",
    "duration_seconds",
    "operation_type",
    "engine_name",
    "metadata_provider_name",
    "platform_name",
    "source_name",
    "source_connection_type",
    "source_format",
    "source_full_table",
    "source_table",
    "source_path",
    "source_status",
    "source_duration_seconds",
    "source_rows_read",
    "source_error_message",
    "transform_status",
    "transform_duration_seconds",
    "transform_error_message",
    "destination_name",
    "destination_connection_type",
    "destination_format",
    "destination_full_table",
    "destination_table",
    "destination_path",
    "destination_load_type",
    "destination_status",
    "destination_duration_seconds",
    "destination_rows_written",
    "destination_bytes_added",
    "destination_bytes_removed",
    "destination_error_message",
    "overhead_duration_seconds",
    "error_message",
    "error_preview",
    "failure_phase",
    "failure_message",
    "phase_health",
    "performance_bottleneck_phase",
    "performance_candidate_code",
    "performance_candidate_codes",
    "performance_candidate_reason",
    "performance_candidate_reasons",
    "performance_candidate_priority",
    "performance_rows_processed",
    "performance_rows_per_second",
    "performance_overhead_ratio",
    "performance_dominant_phase_ratio",
}


def _compact_performance_evidence(row: dict[str, Any]) -> dict[str, Any]:
    evidence = _failure_enriched_dataflow(row) if _status(row) == "failed" else row
    compact = {
        key: value
        for key, value in evidence.items()
        if key in _PERFORMANCE_EVIDENCE_FIELDS
    }
    compact["error_preview"] = _error_preview(evidence)
    compact["phase_health"] = _phase_health(evidence)
    return compact


def _safe_ratio(
    numerator: float | int | None, denominator: float | int | None
) -> float:
    numerator_value = float(numerator or 0)
    denominator_value = float(denominator or 0)
    if denominator_value <= 0:
        return 0
    return round(numerator_value / denominator_value, 3)


def _title_word(value: Any) -> str:
    return str(value or "unknown").replace("_", " ").strip().title() or "Unknown"


def _duration_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    top_rows = sorted(
        rows, key=lambda row: _num(row, "duration_seconds") or 0, reverse=True
    )[:30]
    return [
        {
            "dataflow_name": row.get("dataflow_name")
            or row.get("dataflow_id")
            or "unknown",
            "stage": row.get("stage") or "unknown",
            "engine_name": row.get("engine_name") or "unknown",
            "source_duration_seconds": _num(row, "source_duration_seconds") or 0,
            "transform_duration_seconds": _num(row, "transform_duration_seconds") or 0,
            "destination_duration_seconds": _num(row, "destination_duration_seconds")
            or 0,
            "overhead_duration_seconds": _performance_phase_duration(row, "overhead"),
            "duration_seconds": _num(row, "duration_seconds") or 0,
        }
        for row in top_rows
    ]


def _duration_vs_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points = []
    for row in rows:
        rows_processed = _performance_rows_processed(row)
        points.append(
            {
                "dataflow_id": row.get("dataflow_id"),
                "dataflow_run_id": row.get("dataflow_run_id"),
                "dataflow_name": row.get("dataflow_name")
                or row.get("dataflow_id")
                or "unknown",
                "stage": row.get("stage") or "unknown",
                "engine_name": row.get("engine_name") or "unknown",
                "rows_processed": rows_processed,
                "duration_seconds": _num(row, "duration_seconds") or 0,
                "status": _status(row),
                "performance_bottleneck_phase": row.get("performance_bottleneck_phase")
                or "unknown",
            }
        )
    return sorted(points, key=lambda item: item["duration_seconds"], reverse=True)[:200]


def _duration_by_stage(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = _num(row, "duration_seconds")
        if value is not None:
            buckets[row.get("stage") or "unknown"].append(value)
    result = []
    for stage, values in buckets.items():
        result.append(
            {
                "stage": stage,
                "count": len(values),
                "avg_duration_seconds": round(sum(values) / len(values), 3),
                "p50_duration_seconds": _percentile(values, 0.50),
                "p95_duration_seconds": _percentile(values, 0.95),
                "max_duration_seconds": round(max(values), 3),
            }
        )
    return sorted(result, key=lambda item: item["p95_duration_seconds"], reverse=True)[
        :40
    ]


def _slowest_dataflows_by_p95(
    rows: list[dict[str, Any]], limit: int = 25
) -> list[dict[str, Any]]:
    buckets: dict[str, list[float]] = defaultdict(list)
    labels: dict[str, dict[str, Any]] = {}
    latest_runs: dict[str, dict[str, Any]] = {}
    for row in rows:
        duration = _num(row, "duration_seconds")
        if duration is None:
            continue
        key = _dataflow_id(row)
        if not key:
            continue
        buckets[key].append(duration)
        if key not in labels:
            labels[key] = {
                "dataflow_id": key,
                "dataflow_name": row.get("dataflow_name")
                or row.get("dataflow_id")
                or "unknown",
            }
        latest = latest_runs.get(key)
        if latest is None or _time_value(
            row.get("end_time") or row.get("start_time")
        ) > _time_value(latest.get("end_time") or latest.get("start_time")):
            latest_runs[key] = row
    result = []
    for key, values in buckets.items():
        latest = latest_runs.get(key, {})
        label = labels.get(key, {"dataflow_id": None, "dataflow_name": key})
        result.append(
            {
                "dataflow_id": label["dataflow_id"],
                "dataflow_name": label["dataflow_name"],
                "run_count": len(values),
                "avg_duration_seconds": round(sum(values) / len(values), 3),
                "p95_duration_seconds": _percentile(values, 0.95),
                "max_duration_seconds": round(max(values), 3),
                "stage": latest.get("stage") or "unknown",
            }
        )
    return sorted(
        result,
        key=lambda item: (
            item["p95_duration_seconds"],
            item["max_duration_seconds"],
            item["run_count"],
        ),
        reverse=True,
    )[:limit]


def _engine_stage_matrix(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        duration = _num(row, "duration_seconds")
        if duration is not None:
            buckets[
                (row.get("stage") or "unknown", row.get("engine_name") or "unknown")
            ].append(duration)
    result = []
    for (stage, engine), values in buckets.items():
        result.append(
            {
                "stage": stage,
                "engine_name": engine,
                "count": len(values),
                "p50_duration_seconds": _percentile(values, 0.50),
                "avg_duration_seconds": round(sum(values) / len(values), 3),
            }
        )
    return sorted(result, key=lambda item: (item["stage"], item["engine_name"]))
