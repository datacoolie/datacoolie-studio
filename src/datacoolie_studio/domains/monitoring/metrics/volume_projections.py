from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timezone
from typing import Any
from datacoolie_studio.domains.monitoring.metrics.row_normalization import (
    dataflow_operation_type as _dataflow_operation_type,
    dimension_value as _dimension_value,
    dominant_value as _dominant_value,
)
from datacoolie_studio.domains.monitoring.metrics.investigation import (
    is_lakehouse_destination as _is_lakehouse_destination,
)
from datacoolie_studio.domains.monitoring.metrics.aggregates import (
    numeric_value as _num,
    percentile as _percentile,
    percentile_clean as _percentile_clean,
    rate as _rate,
    status as _status,
    sum_observed as _sum,
)
from datacoolie_studio.domains.monitoring.metrics.time_buckets import (
    date_bucket as _date_bucket,
    time_value as _time_value,
    trend_context as _trend_context,
)


def _volume_page(
    rows: list[dict[str, Any]],
    trend_context: dict[str, Any] | None = None,
    *,
    version_mode: str = "v6",
) -> dict[str, Any]:
    trend_context = trend_context or _trend_context({}, rows, timezone.utc)
    total_runs = len(rows)
    skip_count = sum(1 for row in rows if _status(row) == "skipped")
    rows_read_total = _sum(rows, "source_rows_read", mode=version_mode)
    rows_written_total = _sum(rows, "destination_rows_written", mode=version_mode)
    lakehouse_rows = [row for row in rows if _is_lakehouse_destination(row)]
    lakehouse_rows_written_total = _sum(lakehouse_rows, "destination_rows_written", mode=version_mode)
    non_lakehouse_rows = [row for row in rows if not _is_lakehouse_destination(row)]
    non_lakehouse_rows_written_total = _sum(non_lakehouse_rows, "destination_rows_written", mode=version_mode)
    files_added = _sum(rows, "destination_files_added", mode=version_mode)
    files_removed = _sum(rows, "destination_files_removed", mode=version_mode)
    bytes_added = _sum(rows, "destination_bytes_added", mode=version_mode)
    bytes_removed = _sum(rows, "destination_bytes_removed", mode=version_mode)
    paired_net_values = [_net_bytes(row) for row in rows]
    net_measured = sum(value is not None for value in paired_net_values)
    paired_file_rows = [
        row for row in rows
        if _num(row, "destination_bytes_added") is not None
        and _num(row, "destination_files_added") is not None
    ]
    paired_bytes = sum(_num(row, "destination_bytes_added") for row in paired_file_rows)
    paired_files = sum(_num(row, "destination_files_added") for row in paired_file_rows)
    file_average = (
        round(paired_bytes / paired_files, 3)
        if paired_files and (version_mode == "observed" or len(paired_file_rows) == total_runs)
        else None
    )
    lakehouse_runs = sum(1 for row in rows if _is_lakehouse_destination(row))
    high_volume_queue = _volume_investigation_queue(rows)
    dataflow_registry = _volume_dataflow_registry(
        rows, high_volume_queue, version_mode=version_mode
    )
    candidate_dataflows = [
        row for row in dataflow_registry if row.get("volume_candidate_priority", 0) > 0
    ]
    return {
        "kpis": {
            "total_rows_read": rows_read_total,
            "total_rows_written": rows_written_total,
            "total_lakehouse_rows_written": lakehouse_rows_written_total,
            "total_non_lakehouse_rows_written": non_lakehouse_rows_written_total,
            "observed_rows_read": sum(
                _num(row, "source_rows_read") or 0 for row in rows
            ),
            "rows_read_measured": sum(
                _num(row, "source_rows_read") is not None for row in rows
            ),
            "rows_read_eligible": total_runs,
            "observed_rows_written": sum(
                _num(row, "destination_rows_written") or 0 for row in rows
            ),
            "rows_written_measured": sum(
                _num(row, "destination_rows_written") is not None for row in rows
            ),
            "rows_written_eligible": total_runs,
            **{
                key: value
                for field, column in (
                    ("rows_inserted", "destination_rows_inserted"),
                    ("rows_updated", "destination_rows_updated"),
                    ("rows_deleted", "destination_rows_deleted"),
                    ("files_added", "destination_files_added"),
                    ("files_removed", "destination_files_removed"),
                    ("bytes_added", "destination_bytes_added"),
                    ("bytes_removed", "destination_bytes_removed"),
                )
                for key, value in _volume_observation(rows, field, column).items()
            },
            **_volume_observation(lakehouse_rows, "lakehouse_rows_written", "destination_rows_written"),
            "total_rows_inserted": _sum(rows, "destination_rows_inserted", mode=version_mode),
            "total_rows_updated": _sum(rows, "destination_rows_updated", mode=version_mode),
            "total_rows_deleted": _sum(rows, "destination_rows_deleted", mode=version_mode),
            "lakehouse_destination_run_count": lakehouse_runs,
            "lakehouse_destination_share": _rate(lakehouse_runs, total_runs),
            "files_added": files_added,
            "files_removed": files_removed,
            "total_bytes_added": bytes_added,
            "total_bytes_removed": bytes_removed,
            "total_bytes_saved": _sum(rows, "destination_bytes_saved", mode=version_mode),
            "net_bytes_change": _sum_derived(rows, _net_bytes, mode=version_mode) if net_measured else None,
            "observed_net_bytes_change": sum(value for value in paired_net_values if value is not None),
            "net_bytes_change_measured": net_measured,
            "net_bytes_change_eligible": total_runs,
            "avg_bytes_per_file_added": file_average,
            "bytes_per_file_measured": len(paired_file_rows),
            "bytes_per_file_eligible": total_runs,
            "high_volume_run_count": len(high_volume_queue),
            "high_volume_dataflow_count": len(candidate_dataflows),
            "high_volume_candidate_run_count": len(high_volume_queue),
            "high_volume_rows_count": sum(
                1
                for row in high_volume_queue
                if row.get("volume_candidate_kind") == "read"
            ),
            "high_volume_rows_written_count": sum(
                1
                for row in high_volume_queue
                if row.get("volume_candidate_kind") == "rows_written"
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
        "rows_by_date": _rows_by_date(
            rows, trend_context=trend_context, version_mode=version_mode
        ),
        "bytes_by_date": _bytes_by_date(
            rows, trend_context=trend_context, version_mode=version_mode
        ),
        "volume_by_load_type": _volume_by_load_type(
            rows, version_mode=version_mode
        ),
        "volume_by_workload_type": _volume_by_workload_type(
            rows, version_mode=version_mode
        ),
        "route_volume": _route_volume(rows, version_mode=version_mode),
        "top_dataflows_by_rows_read": _top_dataflow_sum(
            rows, "source_rows_read", limit=20, version_mode=version_mode
        ),
        "top_dataflows_by_rows_written": _top_dataflow_sum(
            rows, "destination_rows_written", limit=20, version_mode=version_mode
        ),
        "top_dataflows_by_lakehouse_rows_written": _top_dataflow_sum(
            [row for row in rows if _is_lakehouse_destination(row)],
            "destination_rows_written", limit=20, version_mode=version_mode,
        ),
        "top_dataflows_by_bytes_added": _top_dataflow_sum(
            rows, "destination_bytes_added", limit=20, version_mode=version_mode
        ),
        "top_dataflows_by_net_bytes": _top_dataflow_net_bytes(
            rows, limit=20, version_mode=version_mode
        ),
        "dataflow_registry": [
            _compact_volume_registry_row(row) for row in dataflow_registry
        ],
    }


def _volume_observation(rows: list[dict[str, Any]], field: str, column: str) -> dict[str, Any]:
    values = [_num(row, column) for row in rows]
    return {
        f"observed_{field}": sum(value for value in values if value is not None),
        f"{field}_measured": sum(value is not None for value in values),
        f"{field}_eligible": len(rows),
    }


_VOLUME_REGISTRY_BASE_FIELDS = {
    "_source_id",
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
    "latest_log_session_id",
    "latest_dataflow_run_id",
    "latest_raw_record_json",
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
    rows: list[dict[str, Any]],
    trend_context: dict[str, Any] | None = None,
    *,
    version_mode: str = "v6",
) -> list[dict[str, Any]]:
    trend_context = trend_context or _trend_context({}, rows, timezone.utc)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    bucket_metadata: dict[str, dict[str, str | None]] = {}
    for row in rows:
        bucket = _date_bucket(row, trend_context)
        key = bucket["bucket"]
        bucket_metadata[key] = bucket
        buckets[key].append(row)
    return [
        {
            "date": key,
            "bucket": key,
            "bucket_start": bucket_metadata.get(key, {}).get("bucket_start"),
            "bucket_end": bucket_metadata.get(key, {}).get("bucket_end"),
            "grain": trend_context["effective_grain"],
            "rows_read": _sum(values, "source_rows_read", mode=version_mode),
            "rows_written": _sum(
                values, "destination_rows_written", mode=version_mode
            ),
            "rows_output": _sum_derived(values, _output_rows, mode=version_mode),
            "rows_inserted": _sum(
                values, "destination_rows_inserted", mode=version_mode
            ),
            "rows_updated": _sum(
                values, "destination_rows_updated", mode=version_mode
            ),
            "rows_deleted": _sum(
                values, "destination_rows_deleted", mode=version_mode
            ),
            "dataflow_runs": len(values),
        }
        for key, values in sorted(
            buckets.items(),
            key=lambda item: str(
                bucket_metadata.get(item[0], {}).get("bucket_start") or item[0]
            ),
        )
    ]


def _output_rows(row: dict[str, Any]) -> float | None:
    rows_written = _num(row, "destination_rows_written")
    if rows_written is not None:
        return rows_written
    # A missing destination metric remains unknown.  Source rows are not an
    # output estimate under the v6 contract.
    return None


def _measured_rows_written(row: dict[str, Any]) -> float | int | None:
    return _num(row, "destination_rows_written")


def _nullable_total(values: list[float | int | None]) -> float | int | None:
    if not values:
        return 0
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _sum_derived(
    rows: list[dict[str, Any]], resolver, *, mode: str = "v6"
) -> int | float | None:
    return _sum(
        [{"_value": resolver(row)} for row in rows],
        "_value",
        mode=mode,
    )


def _nullable_add(left: float | int | None, right: float | int | None) -> float | int | None:
    if left is None or right is None:
        return None
    return left + right


def _nullable_average(values: list[float | int | None]) -> float | None:
    if not values or any(value is None for value in values):
        return None
    return round(sum(value for value in values if value is not None) / len(values), 3)


def _nullable_max(values: list[float | int | None]) -> float | int | None:
    if not values or any(value is None for value in values):
        return None
    return max(value for value in values if value is not None)


def _net_bytes(row: dict[str, Any]) -> int | float | None:
    added = _num(row, "destination_bytes_added")
    removed = _num(row, "destination_bytes_removed")
    return added - removed if added is not None and removed is not None else None


def _file_changes(row: dict[str, Any]) -> int | float | None:
    added = _num(row, "destination_files_added")
    removed = _num(row, "destination_files_removed")
    return added + removed if added is not None and removed is not None else None


def _bytes_by_date(
    rows: list[dict[str, Any]],
    trend_context: dict[str, Any] | None = None,
    *,
    version_mode: str = "v6",
) -> list[dict[str, Any]]:
    trend_context = trend_context or _trend_context({}, rows, timezone.utc)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    bucket_metadata: dict[str, dict[str, str | None]] = {}
    for row in rows:
        bucket = _date_bucket(row, trend_context)
        key = bucket["bucket"]
        bucket_metadata[key] = bucket
        buckets[key].append(row)
    return [
        {
            "date": key,
            "bucket": key,
            "bucket_start": bucket_metadata.get(key, {}).get("bucket_start"),
            "bucket_end": bucket_metadata.get(key, {}).get("bucket_end"),
            "grain": trend_context["effective_grain"],
            "bytes_added": _sum(
                values, "destination_bytes_added", mode=version_mode
            ),
            "bytes_removed": _sum(
                values, "destination_bytes_removed", mode=version_mode
            ),
            "bytes_saved": _sum(
                values, "destination_bytes_saved", mode=version_mode
            ),
            "net_bytes": _sum_derived(values, _net_bytes, mode=version_mode),
            "files_added": _sum(
                values, "destination_files_added", mode=version_mode
            ),
            "files_removed": _sum(
                values, "destination_files_removed", mode=version_mode
            ),
        }
        for key, values in sorted(
            buckets.items(),
            key=lambda item: str(
                bucket_metadata.get(item[0], {}).get("bucket_start") or item[0]
            ),
        )
    ]


def _volume_by_load_type(
    rows: list[dict[str, Any]], *, version_mode: str = "v6"
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            row.get("destination_load_type")
            or row.get("destination_operation_type")
            or "unknown"
        )
        buckets[key].append(row)
    return sorted(
        (
            {
                "load_type": key,
                "rows_written": _sum(
                    values, "destination_rows_written", mode=version_mode
                ),
                "bytes_added": _sum(
                    values, "destination_bytes_added", mode=version_mode
                ),
                "count": len(values),
            }
            for key, values in buckets.items()
        ),
        key=lambda item: (item["rows_written"] is None, item["rows_written"] or 0),
        reverse=True,
    )


def _volume_by_workload_type(
    rows: list[dict[str, Any]], *, version_mode: str = "v6"
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        operation = _dataflow_operation_type(row)
        load_type = _dimension_value(
            row.get("destination_load_type") or row.get("destination_operation_type")
        )
        key = f"{operation} · {load_type}"
        buckets[key].append(row)
    return sorted(
        (
            {
                "workload_type": key,
                "rows_read": _sum(
                    values, "source_rows_read", mode=version_mode
                ),
                "rows_written": _sum(
                    values, "destination_rows_written", mode=version_mode
                ),
                "rows_inserted": _sum(
                    values, "destination_rows_inserted", mode=version_mode
                ),
                "rows_updated": _sum(
                    values, "destination_rows_updated", mode=version_mode
                ),
                "rows_deleted": _sum(
                    values, "destination_rows_deleted", mode=version_mode
                ),
                "bytes_added": _sum(
                    values, "destination_bytes_added", mode=version_mode
                ),
                "bytes_removed": _sum(
                    values, "destination_bytes_removed", mode=version_mode
                ),
                "runs": len(values),
                "skipped": sum(1 for value in values if _status(value) == "skipped"),
            }
            for key, values in buckets.items()
        ),
        key=lambda item: (
            item["rows_read"] is None, item["rows_read"] or 0,
            item["rows_written"] is None, item["rows_written"] or 0,
            (item["bytes_added"] or 0) + (item["bytes_removed"] or 0),
            item["runs"],
        ),
        reverse=True,
    )


def _route_volume(
    rows: list[dict[str, Any]], *, version_mode: str = "v6"
) -> list[dict[str, Any]]:
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
                "rows_read": _sum(items, "source_rows_read", mode=version_mode),
                "rows_written": _sum(
                    items, "destination_rows_written", mode=version_mode
                ),
                "rows_inserted": _sum(
                    items, "destination_rows_inserted", mode=version_mode
                ),
                "rows_updated": _sum(
                    items, "destination_rows_updated", mode=version_mode
                ),
                "rows_deleted": _sum(
                    items, "destination_rows_deleted", mode=version_mode
                ),
                "bytes_added": _sum(
                    items, "destination_bytes_added", mode=version_mode
                ),
                "bytes_removed": _sum(
                    items, "destination_bytes_removed", mode=version_mode
                ),
                "files_added": _sum(
                    items, "destination_files_added", mode=version_mode
                ),
                "files_removed": _sum(
                    items, "destination_files_removed", mode=version_mode
                ),
            }
        )
    return sorted(
        result,
        key=lambda row: (
            -float(row["rows_read"] or 0),
            -float(row["rows_written"] or 0),
            -float(row["rows_written"] or 0),
            -float(row["bytes_added"] or 0) - float(row["bytes_removed"] or 0),
            -int(row["runs"]),
            str(row["source_name"]),
            str(row["destination_name"]),
        ),
    )


def _top_dataflow_net_bytes(
    rows: list[dict[str, Any]],
    limit: int = 20,
    *,
    version_mode: str = "v6",
) -> list[dict[str, Any]]:
    buckets: dict[tuple[Any, str], list[dict[str, Any]]] = defaultdict(list)
    counts: Counter[tuple[Any, str]] = Counter()
    labels: dict[tuple[Any, str], str] = {}
    for row in rows:
        raw_dataflow_id = row.get("dataflow_id")
        if raw_dataflow_id is None:
            continue
        dataflow_id = str(raw_dataflow_id)
        key = (row.get("_source_id"), dataflow_id)
        labels.setdefault(key, str(row.get("dataflow_name") or dataflow_id))
        buckets[key].append(row)
        counts[key] += 1
    values = {
        key: _sum_derived(items, _net_bytes, mode=version_mode)
        for key, items in buckets.items()
    }
    return [
        {
            "_source_id": source_id,
            "dataflow_id": dataflow_id,
            "name": labels.get((source_id, dataflow_id), dataflow_id),
            "value": round(value, 3),
            "count": counts[(source_id, dataflow_id)],
        }
        for (source_id, dataflow_id), value in sorted(
            ((key, value) for key, value in values.items() if value is not None),
            key=lambda item: abs(item[1] or 0), reverse=True
        )[:limit]
    ]


def _volume_investigation_queue(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows_read_values = [
        value for row in rows
        if (value := _num(row, "source_rows_read")) is not None and value > 0
    ]
    rows_written_values = [
        value for row in rows
        if (value := _measured_rows_written(row)) is not None and value > 0
    ]
    lakehouse_rows_values = [
        value for row in rows
        if _is_lakehouse_destination(row)
        and (value := _num(row, "destination_rows_written")) is not None
        and value > 0
    ]
    net_byte_values = [
        abs(value)
        for row in rows
        if (value := _net_bytes(row)) is not None and abs(value) > 0
    ]
    file_change_values = [
        value
        for row in rows
        if (value := _file_changes(row)) is not None and value > 0
    ]
    thresholds = {
        "read": _percentile(rows_read_values, 0.95),
        "rows_written": _percentile(rows_written_values, 0.95),
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
            -float(row["volume_rows_read"] or 0),
            -float(row["volume_rows_written"] or 0),
            -float(row["volume_lakehouse_rows_written"] or 0),
            -abs(float(row["volume_net_bytes"] or 0)),
            -_time_value(row.get("end_time") or row.get("start_time")).timestamp(),
        ),
    )


def _enrich_volume_candidate(
    row: dict[str, Any], thresholds: dict[str, float]
) -> dict[str, Any]:
    rows_read = _num(row, "source_rows_read")
    rows_written = _measured_rows_written(row)
    lakehouse_rows_written = (
        _num(row, "destination_rows_written")
        if _is_lakehouse_destination(row)
        else None
    )
    bytes_added = _num(row, "destination_bytes_added")
    bytes_removed = _num(row, "destination_bytes_removed")
    files_added = _num(row, "destination_files_added")
    files_removed = _num(row, "destination_files_removed")
    net_bytes = bytes_added - bytes_removed if bytes_added is not None and bytes_removed is not None else None
    files_changed = files_added + files_removed if files_added is not None and files_removed is not None else None
    checks = [
        ("read", rows_read, thresholds["read"], "High rows read"),
        (
            "rows_written",
            rows_written,
            thresholds["rows_written"],
            "High rows written",
        ),
        (
            "lakehouse_rows",
            lakehouse_rows_written,
            thresholds["lakehouse_rows"],
            "High lakehouse rows written",
        ),
        (
            "bytes",
            abs(net_bytes) if net_bytes is not None else None,
            thresholds["bytes"],
            "High lakehouse net bytes",
        ),
        ("files", files_changed, thresholds["files"], "High lakehouse file churn"),
    ]
    matched = [
        (kind, value, threshold, reason)
        for kind, value, threshold, reason in checks
        if value is not None and threshold > 0 and value >= threshold and value > 0
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
        "volume_rows_written": rows_written,
        "volume_lakehouse_rows_written": lakehouse_rows_written,
        "volume_rows_inserted": _num(row, "destination_rows_inserted"),
        "volume_rows_updated": _num(row, "destination_rows_updated"),
        "volume_rows_deleted": _num(row, "destination_rows_deleted"),
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
    *,
    version_mode: str = "v6",
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        raw_dataflow_id = row.get("dataflow_id")
        if raw_dataflow_id is None:
            continue
        grouped[(row.get("_source_id"), str(raw_dataflow_id))].append(row)

    candidate_runs: dict[tuple[Any, str], list[dict[str, Any]]] = defaultdict(list)
    for row in run_candidates:
        raw_dataflow_id = row.get("dataflow_id")
        if raw_dataflow_id is None:
            continue
        candidate_runs[(row.get("_source_id"), str(raw_dataflow_id))].append(row)

    registry: list[dict[str, Any]] = []
    for (source_id, dataflow_id), items in grouped.items():
        ordered = sorted(
            items,
            key=_registry_order,
            reverse=True,
        )
        latest = ordered[0]
        durations = [
            value
            for item in items
            if (value := _num(item, "duration_seconds")) is not None
        ]
        rows_read_values = [_num(item, "source_rows_read") for item in items]
        rows_written_values = [_num(item, "destination_rows_written") for item in items]
        lakehouse_rows_values = [
            _num(item, "destination_rows_written")
            for item in items if _is_lakehouse_destination(item)
        ]
        bytes_added = _sum(
            items, "destination_bytes_added", mode=version_mode
        )
        bytes_removed = _sum(
            items, "destination_bytes_removed", mode=version_mode
        )
        files_added = _sum(items, "destination_files_added", mode=version_mode)
        files_removed = _sum(
            items, "destination_files_removed", mode=version_mode
        )
        matched_run_reasons = sorted(
            {
                str(item.get("volume_candidate_reason"))
                for item in candidate_runs[(source_id, dataflow_id)]
                if item.get("volume_candidate_reason")
            }
        )
        registry.append(
            {
                **latest,
                "_source_id": source_id,
                "dataflow_id": dataflow_id,
                "latest_run_at": latest.get("event_time") or latest.get("end_time") or latest.get("start_time"),
                "latest_run_status": _status(latest),
                "latest_log_session_id": latest.get("log_session_id"),
                "latest_dataflow_run_id": latest.get("dataflow_run_id"),
                "latest_raw_record_json": latest.get("raw_record_json"),
                "run_count": len(items),
                "candidate_run_count": len(candidate_runs[(source_id, dataflow_id)]),
                "candidate_run_reasons": matched_run_reasons,
                "volume_rows_read": _sum(
                    items, "source_rows_read", mode=version_mode
                ),
                "volume_rows_written": _sum(
                    items, "destination_rows_written", mode=version_mode
                ),
                "volume_lakehouse_rows_written": _sum(
                    [
                        item
                        for item in items
                        if _is_lakehouse_destination(item)
                    ],
                    "destination_rows_written",
                    mode=version_mode,
                ),
                "volume_rows_inserted": _sum(
                    items, "destination_rows_inserted", mode=version_mode
                ),
                "volume_rows_updated": _sum(
                    items, "destination_rows_updated", mode=version_mode
                ),
                "volume_rows_deleted": _sum(
                    items, "destination_rows_deleted", mode=version_mode
                ),
                "volume_bytes_added": bytes_added,
                "volume_bytes_removed": bytes_removed,
                "volume_net_bytes": (
                    bytes_added - bytes_removed
                    if bytes_added is not None and bytes_removed is not None
                    else None
                ),
                "volume_files_added": files_added,
                "volume_files_removed": files_removed,
                "volume_files_changed": (
                    files_added + files_removed
                    if files_added is not None and files_removed is not None
                    else None
                ),
                "avg_rows_read": _nullable_average(rows_read_values),
                "peak_rows_read": _nullable_max(rows_read_values),
                "peak_rows_written": _nullable_max(rows_written_values),
                "peak_lakehouse_rows_written": _nullable_max(lakehouse_rows_values),
                "p95_rows_read": _percentile_clean(rows_read_values, 0.95),
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
        "rows_written": "volume_rows_written",
        "lakehouse_rows": "volume_lakehouse_rows_written",
        "bytes": "volume_net_bytes",
        "files": "volume_files_changed",
    }
    thresholds = {
        kind: _percentile(
            [
                abs(float(row.get(field) or 0))
                for row in registry
                if row.get(field) is not None and abs(float(row.get(field))) > 0
            ],
            0.95,
        )
        for kind, field in threshold_fields.items()
    }
    labels = {
        "read": "High rows read",
        "rows_written": "High rows written",
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
            -float(row.get("volume_rows_written") or 0),
            str(row.get("dataflow_name") or row.get("dataflow_id") or ""),
        ),
    )


def _registry_order(item: dict[str, Any]) -> tuple[Any, ...]:
    """Choose one coherent latest row after aggregating a source/flow history."""
    return (
        _time_value(item.get("event_time") or item.get("end_time") or item.get("start_time")),
        _time_value(item.get("_ingested_at")),
        int(_num(item, "_record_line") or -1),
        str(item.get("_file_uri") or ""),
        str(item.get("log_session_id") or ""),
        str(item.get("dataflow_run_id") or ""),
    )


def _top_dataflow_sum(
    rows: list[dict[str, Any]],
    value_key: str,
    limit: int = 20,
    *,
    version_mode: str = "v6",
) -> list[dict[str, Any]]:
    buckets: dict[tuple[Any, str], list[dict[str, Any]]] = defaultdict(list)
    counts: Counter[tuple[Any, str]] = Counter()
    labels: dict[tuple[Any, str], str] = {}
    for row in rows:
        raw_dataflow_id = row.get("dataflow_id")
        if raw_dataflow_id is None:
            continue
        dataflow_id = str(raw_dataflow_id)
        key = (row.get("_source_id"), dataflow_id)
        labels.setdefault(key, str(row.get("dataflow_name") or dataflow_id))
        buckets[key].append(row)
        counts[key] += 1
    return [
        {
            "_source_id": source_id,
            "dataflow_id": dataflow_id,
            "name": labels.get((source_id, dataflow_id), dataflow_id),
            "value": round(value, 3),
            "count": counts[(source_id, dataflow_id)],
        }
        for (source_id, dataflow_id), value in sorted(
            (
                (key, _sum(items, value_key, mode=version_mode))
                for key, items in buckets.items()
            ),
            key=lambda item: item[1] or 0,
            reverse=True,
        )
        if value is not None
    ][:limit]
