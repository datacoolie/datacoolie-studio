from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from typing import Any

from datacoolie_studio.domains.monitoring.metrics.aggregates import status
from datacoolie_studio.domains.monitoring.dimensions import dimension_key, dimension_label, UNKNOWN_KEY


def dataflow_operation_type(row: dict[str, Any]) -> str:
    return dimension_value(row.get("operation_type"))


def dataflow_id(row: dict[str, Any]) -> str:
    value = row.get("dataflow_id")
    if value in (None, ""):
        return ""
    text = str(value)
    # These are valid authored lexical IDs in v6.  Only absent/empty values
    # represent a missing identity; normalizing a literal ``unknown`` or
    # ``null`` into a sentinel loses linkage and merges independent runs.
    return text


def destination_operation_type(row: dict[str, Any]) -> str:
    return dimension_value(row.get("destination_operation_type"))


def dimension_value(value: Any) -> str:
    return dimension_key(value)


def has_value(value: Any) -> bool:
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return value not in (None, "")


def listish_values(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if not has_value(value):
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


def normalized_job_shape(job: dict[str, Any]) -> dict[str, list[str]]:
    stages = [dimension_value(value) for value in listish_values(job.get("stages"))]
    operation_types = [
        dimension_value(value) for value in listish_values(job.get("operation_types"))
    ]
    return {
        "operation_types": sorted(set(operation_types or [UNKNOWN_KEY])),
        "stages": stages or [UNKNOWN_KEY],
    }


def job_key(job: dict[str, Any]) -> str:
    payload = json.dumps(
        normalized_job_shape(job),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def job_shape_label(job: dict[str, Any]) -> str:
    shape = normalized_job_shape(job)
    return f"{', '.join(map(dimension_label, shape['operation_types']))} | {', '.join(map(dimension_label, shape['stages']))}"


def job_runs_by_dataflow_operation_type(
    _rows: list[dict[str, Any]], jobs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    buckets: dict[str, Counter] = defaultdict(Counter)
    for job in jobs:
        operation_types = listish_values(job.get("operation_types")) or [None]
        for operation_type in operation_types:
            normalized_operation_type = dimension_value(operation_type)
            bucket = buckets[normalized_operation_type]
            bucket["count"] += 1
            job_status = status(job)
            bucket[job_status] += 1
            if job_status == "failed":
                bucket["failed_count"] += 1

    return sorted(
        (
            {"operation_type": operation_type, **dict(values)}
            for operation_type, values in buckets.items()
        ),
        key=lambda item: (-int(item["count"]), str(item["operation_type"])),
    )


def dominant_dataflow_operation_type(rows: list[dict[str, Any]]) -> str:
    values = Counter(dataflow_operation_type(row) for row in rows)
    return values.most_common(1)[0][0] if values else "unknown"


def dominant_value(rows: list[dict[str, Any]], key: str) -> str:
    values = Counter(dimension_value(row.get(key)) for row in rows)
    return values.most_common(1)[0][0] if values else "unknown"


def counter_rows(counts: Counter, key_name: str) -> list[dict[str, Any]]:
    return [{key_name: key, "count": value} for key, value in counts.most_common()]
