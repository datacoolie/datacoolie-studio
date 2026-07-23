from __future__ import annotations

from collections import defaultdict
from typing import Any
from datacoolie_studio.domains.monitoring.metrics.common_projections import (
    _dataflow_id,
    _status,
    _sum,
    _time_value,
)


def _maintenance_upstream_dataflows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = _dataflow_id(row) or str(row.get("dataflow_name") or "unknown")
        buckets[key].append(row)
    result = []
    for dataflow_id, items in buckets.items():
        latest = max(
            items,
            key=lambda item: _time_value(
                item.get("end_time") or item.get("start_time")
            ),
        )
        source_connection = (
            latest.get("source_name")
            or latest.get("source_connection_name")
            or "unknown"
        )
        source_object = (
            latest.get("source_full_table")
            or latest.get("source_table")
            or latest.get("source_path")
            or latest.get("source_python_function")
            or latest.get("source_query")
            or "-"
        )
        result.append(
            {
                "dataflow_id": dataflow_id,
                "dataflow_name": latest.get("dataflow_name") or dataflow_id,
                "stage": latest.get("stage") or "unknown",
                "operation_type": latest.get("operation_type") or "unknown",
                "source": f"{source_connection} · {source_object}",
                "load_type": latest.get("destination_load_type")
                or latest.get("destination_operation_type")
                or "-",
                "latest_status": _status(latest),
                "latest_time": latest.get("end_time") or latest.get("start_time"),
                "run_count": len(items),
                "rows_read": _sum(items, "source_rows_read"),
            }
        )
    return sorted(result, key=lambda item: str(item["dataflow_name"]))
