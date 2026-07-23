from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timezone
from typing import Any
from datacoolie_studio.domains.monitoring.metrics.common_projections import (
    _dataflow_id,
    _dataflow_operation_type,
    _date_bucket,
    _dimension_value,
    _dominant_value,
    _is_lakehouse_destination,
    _num,
    _percentile,
    _percentile_clean,
    _rate,
    _status,
    _sum,
    _time_value,
    _trend_context,
)


def _volume_page(
    rows: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    trend_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trend_context = trend_context or _trend_context({}, rows, timezone.utc)
    total_runs = len(rows)
    skip_count = sum(1 for row in rows if _status(row) == "skipped")
    rows_read_total = _sum(rows, "source_rows_read")
    lakehouse_rows_written_total = _sum(rows, "destination_rows_written")
    est_rows_written_total = round(sum(_estimated_rows_written(row) for row in rows), 3)
    files_added = _sum(rows, "destination_files_added")
    files_removed = _sum(rows, "destination_files_removed")
    bytes_added = _sum(rows, "destination_bytes_added")
    bytes_removed = _sum(rows, "destination_bytes_removed")
    lakehouse_runs = sum(1 for row in rows if _is_lakehouse_destination(row))
    high_volume_queue = _volume_investigation_queue(rows)
    dataflow_registry = _volume_dataflow_registry(rows, high_volume_queue)
    candidate_dataflows = [
        row for row in dataflow_registry if row.get("volume_candidate_priority", 0) > 0
    ]
    return {
        "kpis": {
            "total_rows_read": rows_read_total,
            "total_rows_written": lakehouse_rows_written_total,
            "total_est_rows_written": est_rows_written_total,
            "total_est_rows_written_non_lakehouse": round(
                est_rows_written_total - lakehouse_rows_written_total, 3
            ),
            "total_rows_inserted": _sum(rows, "destination_rows_inserted"),
            "total_rows_updated": _sum(rows, "destination_rows_updated"),
            "total_rows_deleted": _sum(rows, "destination_rows_deleted"),
            "lakehouse_destination_run_count": lakehouse_runs,
            "lakehouse_destination_share": _rate(lakehouse_runs, total_runs),
            "files_added": files_added,
            "files_removed": files_removed,
            "total_bytes_added": bytes_added,
            "total_bytes_removed": bytes_removed,
            "total_bytes_saved": _sum(rows, "destination_bytes_saved"),
            "net_bytes_change": bytes_added - bytes_removed,
            "avg_bytes_per_file_added": round(bytes_added / files_added, 3)
            if files_added
            else 0,
            "high_volume_run_count": len(high_volume_queue),
            "high_volume_dataflow_count": len(candidate_dataflows),
            "high_volume_candidate_run_count": len(high_volume_queue),
            "high_volume_rows_count": sum(
                1
                for row in high_volume_queue
                if row.get("volume_candidate_kind") == "read"
            ),
            "high_volume_est_rows_count": sum(
                1
                for row in high_volume_queue
                if row.get("volume_candidate_kind") == "est_rows"
            ),
            "high_volume_lakehouse_rows_count": sum(
                1
                for row in high_volume_queue
                if row.get("volume_candidate_kind") == "lakehouse_rows"
            ),
            "high_volume_bytes_count": sum(
                1
                for row in high_volume_queue
                if row.get("volume_candidate_kind") == "bytes"
            ),
            "high_volume_files_count": sum(
                1
                for row in high_volume_queue
                if row.get("volume_candidate_kind") == "files"
            ),
            "skip_count": skip_count,
            "skip_rate": _rate(skip_count, total_runs),
        },
        "rows_by_date": _rows_by_date(rows, trend_context=trend_context),
        "bytes_by_date": _bytes_by_date(rows, trend_context=trend_context),
        "volume_by_load_type": _volume_by_load_type(rows),
        "volume_by_workload_type": _volume_by_workload_type(rows),
        "route_volume": _route_volume(rows),
        "top_dataflows_by_rows_read": _top_dataflow_sum(
            rows, "source_rows_read", limit=20
        ),
        "top_dataflows_by_est_rows_written": _top_dataflow_est_rows_written(
            rows, limit=20
        ),
        "top_dataflows_by_rows_written": _top_dataflow_sum(
            rows, "destination_rows_written", limit=20
        ),
        "top_dataflows_by_bytes_added": _top_dataflow_sum(
            rows, "destination_bytes_added", limit=20
        ),
        "top_dataflows_by_net_bytes": _top_dataflow_net_bytes(rows, limit=20),
        "dataflow_registry": [
            _compact_volume_registry_row(row) for row in dataflow_registry
        ],
    }


_VOLUME_REGISTRY_BASE_FIELDS = {
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
    "source_name",
    "source_connection_type",
    "source_format",
    "source_full_table",
    "source_table",
    "source_path",
    "destination_name",
    "destination_connection_type",
    "destination_format",
    "destination_full_table",
    "destination_table",
    "destination_path",
    "destination_load_type",
    "latest_run_at",
    "latest_run_status",
    "run_count",
    "candidate_run_count",
    "candidate_run_reasons",
}


def _compact_volume_registry_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key in _VOLUME_REGISTRY_BASE_FIELDS
        or key.startswith("volume_")
        or key.startswith("peak_")
        or key.startswith("p95_")
    }


def _rows_by_date(
    rows: list[dict[str, Any]], trend_context: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    trend_context = trend_context or _trend_context({}, rows, timezone.utc)
    buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "rows_read": 0,
            "rows_written": 0,
            "est_rows_written": 0,
            "rows_output": 0,
            "rows_output_estimated": 0,
            "rows_inserted": 0,
            "rows_updated": 0,
            "rows_deleted": 0,
            "dataflow_runs": 0,
        }
    )
    bucket_metadata: dict[str, dict[str, str | None]] = {}
    for row in rows:
        bucket = _date_bucket(row, trend_context)
        key = bucket["bucket"]
        bucket_metadata[key] = bucket
        rows_read = _num(row, "source_rows_read") or 0
        rows_written = _num(row, "destination_rows_written") or 0
        est_rows_written = _estimated_rows_written(row)
        rows_output, estimated = _output_rows(row)
        buckets[key]["rows_read"] += rows_read
        buckets[key]["rows_written"] += rows_written
        buckets[key]["est_rows_written"] += est_rows_written
        buckets[key]["rows_output"] += rows_output
        buckets[key]["rows_output_estimated"] += estimated
        buckets[key]["rows_inserted"] += _num(row, "destination_rows_inserted") or 0
        buckets[key]["rows_updated"] += _num(row, "destination_rows_updated") or 0
        buckets[key]["rows_deleted"] += _num(row, "destination_rows_deleted") or 0
        buckets[key]["dataflow_runs"] += 1
    return [
        {
            "date": key,
            "bucket": key,
            "bucket_start": bucket_metadata.get(key, {}).get("bucket_start"),
            "bucket_end": bucket_metadata.get(key, {}).get("bucket_end"),
            "grain": trend_context["effective_grain"],
            **values,
        }
        for key, values in sorted(
            buckets.items(),
            key=lambda item: str(
                bucket_metadata.get(item[0], {}).get("bucket_start") or item[0]
            ),
        )
    ]


def _output_rows(row: dict[str, Any]) -> tuple[float, float]:
    rows_written = _num(row, "destination_rows_written")
    if rows_written is not None and rows_written > 0:
        return rows_written, 0
    rows_read = _num(row, "source_rows_read") or 0
    if (
        _status(row) == "succeeded"
        and rows_read > 0
        and not _is_lakehouse_destination(row)
    ):
        return rows_read, rows_read
    return rows_written or 0, 0


def _estimated_rows_written(row: dict[str, Any]) -> float:
    rows_written = _num(row, "destination_rows_written") or 0
    if _is_lakehouse_destination(row):
        return rows_written
    if _status(row) == "succeeded":
        return _num(row, "source_rows_read") or rows_written
    return rows_written


def _bytes_by_date(
    rows: list[dict[str, Any]], trend_context: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    trend_context = trend_context or _trend_context({}, rows, timezone.utc)
    buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "bytes_added": 0,
            "bytes_removed": 0,
            "bytes_saved": 0,
            "net_bytes": 0,
            "files_added": 0,
            "files_removed": 0,
        }
    )
    bucket_metadata: dict[str, dict[str, str | None]] = {}
    for row in rows:
        bucket = _date_bucket(row, trend_context)
        key = bucket["bucket"]
        bucket_metadata[key] = bucket
        bytes_added = _num(row, "destination_bytes_added") or 0
        bytes_removed = _num(row, "destination_bytes_removed") or 0
        buckets[key]["bytes_added"] += bytes_added
        buckets[key]["bytes_removed"] += bytes_removed
        buckets[key]["bytes_saved"] += _num(row, "destination_bytes_saved") or 0
        buckets[key]["net_bytes"] += bytes_added - bytes_removed
        buckets[key]["files_added"] += _num(row, "destination_files_added") or 0
        buckets[key]["files_removed"] += _num(row, "destination_files_removed") or 0
    return [
        {
            "date": key,
            "bucket": key,
            "bucket_start": bucket_metadata.get(key, {}).get("bucket_start"),
            "bucket_end": bucket_metadata.get(key, {}).get("bucket_end"),
            "grain": trend_context["effective_grain"],
            **values,
        }
        for key, values in sorted(
            buckets.items(),
            key=lambda item: str(
                bucket_metadata.get(item[0], {}).get("bucket_start") or item[0]
            ),
        )
    ]


def _volume_by_load_type(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {"rows_written": 0, "est_rows_written": 0, "bytes_added": 0, "count": 0}
    )
    for row in rows:
        key = (
            row.get("destination_load_type")
            or row.get("destination_operation_type")
            or "unknown"
        )
        buckets[key]["rows_written"] += _num(row, "destination_rows_written") or 0
        buckets[key]["est_rows_written"] += _estimated_rows_written(row)
        buckets[key]["bytes_added"] += _num(row, "destination_bytes_added") or 0
        buckets[key]["count"] += 1
    return sorted(
        ({"load_type": key, **values} for key, values in buckets.items()),
        key=lambda item: item["est_rows_written"],
        reverse=True,
    )


def _volume_by_workload_type(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "rows_read": 0,
            "rows_written": 0,
            "est_rows_written": 0,
            "rows_inserted": 0,
            "rows_updated": 0,
            "rows_deleted": 0,
            "bytes_added": 0,
            "bytes_removed": 0,
            "runs": 0,
            "skipped": 0,
        }
    )
    for row in rows:
        operation = _dataflow_operation_type(row)
        load_type = _dimension_value(
            row.get("destination_load_type") or row.get("destination_operation_type")
        )
        key = f"{operation} · {load_type}"
        bucket = buckets[key]
        bucket["rows_read"] += _num(row, "source_rows_read") or 0
        bucket["rows_written"] += _num(row, "destination_rows_written") or 0
        bucket["est_rows_written"] += _estimated_rows_written(row)
        bucket["rows_inserted"] += _num(row, "destination_rows_inserted") or 0
        bucket["rows_updated"] += _num(row, "destination_rows_updated") or 0
        bucket["rows_deleted"] += _num(row, "destination_rows_deleted") or 0
        bucket["bytes_added"] += _num(row, "destination_bytes_added") or 0
        bucket["bytes_removed"] += _num(row, "destination_bytes_removed") or 0
        bucket["runs"] += 1
        bucket["skipped"] += 1 if _status(row) == "skipped" else 0
    return sorted(
        ({"workload_type": key, **values} for key, values in buckets.items()),
        key=lambda item: (
            item["rows_read"],
            item["est_rows_written"],
            item["bytes_added"] + item["bytes_removed"],
            item["runs"],
        ),
        reverse=True,
    )


def _route_volume(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        source = str(
            row.get("source_name") or row.get("source_connection_name") or "unknown"
        )
        destination = str(
            row.get("destination_name")
            or row.get("destination_connection_name")
            or "unknown"
        )
        buckets[(source, destination)].append(row)

    result: list[dict[str, Any]] = []
    for (source, destination), items in buckets.items():
        result.append(
            {
                "source_name": source,
                "destination_name": destination,
                "source_format": _dominant_value(items, "source_format"),
                "destination_format": _dominant_value(items, "destination_format"),
                "source_connection_type": _dominant_value(
                    items, "source_connection_type"
                ),
                "destination_connection_type": _dominant_value(
                    items, "destination_connection_type"
                ),
                "runs": len(items),
                "skipped": sum(1 for item in items if _status(item) == "skipped"),
                "rows_read": _sum(items, "source_rows_read"),
                "rows_written": _sum(items, "destination_rows_written"),
                "est_rows_written": round(
                    sum(_estimated_rows_written(item) for item in items), 3
                ),
                "rows_inserted": _sum(items, "destination_rows_inserted"),
                "rows_updated": _sum(items, "destination_rows_updated"),
                "rows_deleted": _sum(items, "destination_rows_deleted"),
                "bytes_added": _sum(items, "destination_bytes_added"),
                "bytes_removed": _sum(items, "destination_bytes_removed"),
                "files_added": _sum(items, "destination_files_added"),
                "files_removed": _sum(items, "destination_files_removed"),
            }
        )
    return sorted(
        result,
        key=lambda row: (
            -float(row["rows_read"]),
            -float(row["est_rows_written"]),
            -float(row["rows_written"]),
            -float(row["bytes_added"]) - float(row["bytes_removed"]),
            -int(row["runs"]),
            str(row["source_name"]),
            str(row["destination_name"]),
        ),
    )


def _top_dataflow_net_bytes(
    rows: list[dict[str, Any]], limit: int = 20
) -> list[dict[str, Any]]:
    buckets: dict[str, float] = defaultdict(float)
    counts: Counter[str] = Counter()
    labels: dict[str, str] = {}
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if not dataflow_id:
            continue
        labels.setdefault(dataflow_id, str(row.get("dataflow_name") or dataflow_id))
        buckets[dataflow_id] += (_num(row, "destination_bytes_added") or 0) - (
            _num(row, "destination_bytes_removed") or 0
        )
        counts[dataflow_id] += 1
    return [
        {
            "dataflow_id": dataflow_id,
            "name": labels.get(dataflow_id, dataflow_id),
            "value": round(value, 3),
            "count": counts[dataflow_id],
        }
        for dataflow_id, value in sorted(
            buckets.items(), key=lambda item: abs(item[1]), reverse=True
        )[:limit]
    ]


def _top_dataflow_est_rows_written(
    rows: list[dict[str, Any]], limit: int = 20
) -> list[dict[str, Any]]:
    buckets: dict[str, float] = defaultdict(float)
    counts: Counter[str] = Counter()
    labels: dict[str, str] = {}
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if not dataflow_id:
            continue
        labels.setdefault(dataflow_id, str(row.get("dataflow_name") or dataflow_id))
        buckets[dataflow_id] += _estimated_rows_written(row)
        counts[dataflow_id] += 1
    return [
        {
            "dataflow_id": dataflow_id,
            "name": labels.get(dataflow_id, dataflow_id),
            "value": round(value, 3),
            "count": counts[dataflow_id],
        }
        for dataflow_id, value in sorted(
            buckets.items(), key=lambda item: item[1], reverse=True
        )[:limit]
    ]


def _volume_investigation_queue(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows_read_values = [
        _num(row, "source_rows_read") or 0
        for row in rows
        if (_num(row, "source_rows_read") or 0) > 0
    ]
    est_rows_values = [
        _estimated_rows_written(row) for row in rows if _estimated_rows_written(row) > 0
    ]
    lakehouse_rows_values = [
        _num(row, "destination_rows_written") or 0
        for row in rows
        if (_num(row, "destination_rows_written") or 0) > 0
    ]
    net_byte_values = [
        abs(
            (_num(row, "destination_bytes_added") or 0)
            - (_num(row, "destination_bytes_removed") or 0)
        )
        for row in rows
        if abs(
            (_num(row, "destination_bytes_added") or 0)
            - (_num(row, "destination_bytes_removed") or 0)
        )
        > 0
    ]
    file_change_values = [
        (_num(row, "destination_files_added") or 0)
        + (_num(row, "destination_files_removed") or 0)
        for row in rows
        if (
            (_num(row, "destination_files_added") or 0)
            + (_num(row, "destination_files_removed") or 0)
        )
        > 0
    ]
    thresholds = {
        "read": _percentile(rows_read_values, 0.95),
        "est_rows": _percentile(est_rows_values, 0.95),
        "lakehouse_rows": _percentile(lakehouse_rows_values, 0.95),
        "bytes": _percentile(net_byte_values, 0.95),
        "files": _percentile(file_change_values, 0.95),
    }
    candidates = [_enrich_volume_candidate(row, thresholds) for row in rows]
    candidates = [row for row in candidates if row["volume_candidate_priority"] > 0]
    return sorted(
        candidates,
        key=lambda row: (
            -float(row["volume_candidate_priority"]),
            -float(row["volume_rows_read"]),
            -float(row["volume_est_rows_written"]),
            -float(row["volume_lakehouse_rows_written"]),
            -abs(float(row["volume_net_bytes"])),
            -_time_value(row.get("end_time") or row.get("start_time")).timestamp(),
        ),
    )


def _enrich_volume_candidate(
    row: dict[str, Any], thresholds: dict[str, float]
) -> dict[str, Any]:
    rows_read = _num(row, "source_rows_read") or 0
    est_rows_written = _estimated_rows_written(row)
    lakehouse_rows_written = _num(row, "destination_rows_written") or 0
    bytes_added = _num(row, "destination_bytes_added") or 0
    bytes_removed = _num(row, "destination_bytes_removed") or 0
    files_added = _num(row, "destination_files_added") or 0
    files_removed = _num(row, "destination_files_removed") or 0
    net_bytes = bytes_added - bytes_removed
    files_changed = files_added + files_removed
    checks = [
        ("read", rows_read, thresholds["read"], "High rows read"),
        (
            "est_rows",
            est_rows_written,
            thresholds["est_rows"],
            "High estimated rows written",
        ),
        (
            "lakehouse_rows",
            lakehouse_rows_written,
            thresholds["lakehouse_rows"],
            "High lakehouse rows written",
        ),
        ("bytes", abs(net_bytes), thresholds["bytes"], "High lakehouse net bytes"),
        ("files", files_changed, thresholds["files"], "High lakehouse file churn"),
    ]
    matched = [
        (kind, value, threshold, reason)
        for kind, value, threshold, reason in checks
        if threshold > 0 and value >= threshold and value > 0
    ]
    if matched:
        kind, value, threshold, reason = max(
            matched, key=lambda item: item[1] / item[2] if item[2] else 0
        )
        priority = round(value / threshold, 3) if threshold else 0
    else:
        kind, reason, priority = "none", "", 0
    return {
        **row,
        "volume_rows_read": rows_read,
        "volume_est_rows_written": est_rows_written,
        "volume_lakehouse_rows_written": lakehouse_rows_written,
        "volume_rows_inserted": _num(row, "destination_rows_inserted") or 0,
        "volume_rows_updated": _num(row, "destination_rows_updated") or 0,
        "volume_rows_deleted": _num(row, "destination_rows_deleted") or 0,
        "volume_bytes_added": bytes_added,
        "volume_bytes_removed": bytes_removed,
        "volume_net_bytes": net_bytes,
        "volume_files_added": files_added,
        "volume_files_removed": files_removed,
        "volume_files_changed": files_changed,
        "volume_candidate_kind": kind,
        "volume_candidate_reason": reason,
        "volume_candidate_priority": priority,
    }


def _volume_dataflow_registry(
    rows: list[dict[str, Any]],
    run_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if dataflow_id:
            grouped[dataflow_id].append(row)

    candidate_runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in run_candidates:
        dataflow_id = _dataflow_id(row)
        if dataflow_id:
            candidate_runs[dataflow_id].append(row)

    registry: list[dict[str, Any]] = []
    for dataflow_id, items in grouped.items():
        ordered = sorted(
            items,
            key=lambda item: _time_value(
                item.get("end_time") or item.get("start_time")
            ),
            reverse=True,
        )
        latest = ordered[0]
        durations = [
            value
            for item in items
            if (value := _num(item, "duration_seconds")) is not None
        ]
        rows_read_values = [(_num(item, "source_rows_read") or 0) for item in items]
        est_rows_values = [_estimated_rows_written(item) for item in items]
        lakehouse_rows_values = [
            (_num(item, "destination_rows_written") or 0) for item in items
        ]
        bytes_added = _sum(items, "destination_bytes_added")
        bytes_removed = _sum(items, "destination_bytes_removed")
        files_added = _sum(items, "destination_files_added")
        files_removed = _sum(items, "destination_files_removed")
        matched_run_reasons = sorted(
            {
                str(item.get("volume_candidate_reason"))
                for item in candidate_runs[dataflow_id]
                if item.get("volume_candidate_reason")
            }
        )
        registry.append(
            {
                **latest,
                "dataflow_id": dataflow_id,
                "latest_run_at": latest.get("end_time") or latest.get("start_time"),
                "latest_run_status": _status(latest),
                "run_count": len(items),
                "candidate_run_count": len(candidate_runs[dataflow_id]),
                "candidate_run_reasons": matched_run_reasons,
                "volume_rows_read": sum(rows_read_values),
                "volume_est_rows_written": round(sum(est_rows_values), 3),
                "volume_lakehouse_rows_written": sum(lakehouse_rows_values),
                "volume_rows_inserted": _sum(items, "destination_rows_inserted"),
                "volume_rows_updated": _sum(items, "destination_rows_updated"),
                "volume_rows_deleted": _sum(items, "destination_rows_deleted"),
                "volume_bytes_added": bytes_added,
                "volume_bytes_removed": bytes_removed,
                "volume_net_bytes": bytes_added - bytes_removed,
                "volume_files_added": files_added,
                "volume_files_removed": files_removed,
                "volume_files_changed": files_added + files_removed,
                "avg_rows_read": round(sum(rows_read_values) / len(items), 3),
                "avg_est_rows_written": round(sum(est_rows_values) / len(items), 3),
                "peak_rows_read": max(rows_read_values, default=0),
                "peak_est_rows_written": max(est_rows_values, default=0),
                "peak_lakehouse_rows_written": max(lakehouse_rows_values, default=0),
                "p95_rows_read": _percentile_clean(rows_read_values, 0.95),
                "p95_est_rows_written": _percentile_clean(est_rows_values, 0.95),
                "p95_lakehouse_rows_written": _percentile_clean(
                    lakehouse_rows_values, 0.95
                ),
                "avg_duration_seconds": round(sum(durations) / len(durations), 3)
                if durations
                else 0,
                "p95_duration_seconds": _percentile_clean(durations, 0.95),
            }
        )

    threshold_fields = {
        "read": "volume_rows_read",
        "est_rows": "volume_est_rows_written",
        "lakehouse_rows": "volume_lakehouse_rows_written",
        "bytes": "volume_net_bytes",
        "files": "volume_files_changed",
    }
    thresholds = {
        kind: _percentile(
            [
                abs(float(row.get(field) or 0))
                for row in registry
                if abs(float(row.get(field) or 0)) > 0
            ],
            0.95,
        )
        for kind, field in threshold_fields.items()
    }
    labels = {
        "read": "High rows read",
        "est_rows": "High estimated rows written",
        "lakehouse_rows": "High lakehouse rows written",
        "bytes": "High lakehouse net bytes",
        "files": "High lakehouse file churn",
    }
    result: list[dict[str, Any]] = []
    for row in registry:
        matched = []
        for kind, field in threshold_fields.items():
            value = abs(float(row.get(field) or 0))
            threshold = float(thresholds.get(kind) or 0)
            if threshold > 0 and value > 0 and value >= threshold:
                matched.append(
                    {
                        "kind": kind,
                        "label": labels[kind],
                        "value": value,
                        "threshold": threshold,
                        "ratio": round(value / threshold, 3),
                    }
                )
        primary = max(matched, key=lambda item: float(item["ratio"]), default=None)
        result.append(
            {
                **row,
                "volume_candidate_kind": primary["kind"] if primary else "none",
                "volume_candidate_reason": primary["label"] if primary else "",
                "volume_candidate_priority": primary["ratio"] if primary else 0,
                "volume_candidate_signals": matched,
            }
        )
    return sorted(
        result,
        key=lambda row: (
            -float(row.get("volume_candidate_priority") or 0),
            -float(row.get("volume_rows_read") or 0),
            -float(row.get("volume_est_rows_written") or 0),
            str(row.get("dataflow_name") or row.get("dataflow_id") or ""),
        ),
    )


def _top_dataflow_sum(
    rows: list[dict[str, Any]], value_key: str, limit: int = 20
) -> list[dict[str, Any]]:
    buckets: dict[str, float] = defaultdict(float)
    counts: Counter[str] = Counter()
    labels: dict[str, str] = {}
    for row in rows:
        dataflow_id = _dataflow_id(row)
        if not dataflow_id:
            continue
        labels.setdefault(dataflow_id, str(row.get("dataflow_name") or dataflow_id))
        buckets[dataflow_id] += _num(row, value_key) or 0
        counts[dataflow_id] += 1
    return [
        {
            "dataflow_id": dataflow_id,
            "name": labels.get(dataflow_id, dataflow_id),
            "value": round(value, 3),
            "count": counts[dataflow_id],
        }
        for dataflow_id, value in sorted(
            buckets.items(), key=lambda item: item[1], reverse=True
        )[:limit]
    ]
