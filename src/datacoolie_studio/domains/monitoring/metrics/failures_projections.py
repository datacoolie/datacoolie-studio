from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any
from datacoolie_studio.domains.monitoring.metrics.failure import (
    categorize_failure,
    classify_failure,
)
from datacoolie_studio.domains.monitoring.metrics.common_projections import (
    _counter_rows,
    _dataflow_id,
    _dimension_value,
    _dominant_value,
    _failure_enriched_dataflow,
    _failure_signature,
    _job_key,
    _job_shape_label,
    _rate,
    _run_date,
    _status,
    _time_value,
)


def _failures_page(
    rows: list[dict[str, Any]], jobs: list[dict[str, Any]]
) -> dict[str, Any]:
    failed = [
        _failure_enriched_dataflow(row) for row in rows if _status(row) == "failed"
    ]
    failed_jobs = [
        _failure_enriched_job(job) for job in jobs if _status(job) == "failed"
    ]
    return {
        "kpis": _failure_kpis(failed, failed_jobs),
        "latest_queue": _latest_failure_queue(failed),
        "repeated_signatures": _repeated_failure_signatures(failed),
        "failure_by_phase": _top_counter(failed, "failure_phase", limit=12),
        "failure_category_phase_matrix": _failure_category_phase_matrix(failed),
        "endpoint_impact": _failure_endpoint_impact(failed),
        "failed_by_stage": _failure_phase_breakdown(
            failed, "stage", label_key="name", count_key="count", limit=30
        ),
        "failed_by_source_connection_type": _top_counter(
            failed, "source_connection_type", limit=20
        ),
        "top_failing_dataflows": _top_failing_dataflows(failed),
        "error_categories": _error_categories(failed),
        "failure_trend_by_date": _failure_trend([*failed, *failed_jobs]),
        "failed_records": failed[:100],
    }


def _failure_kpis(
    failed: list[dict[str, Any]], failed_jobs: list[dict[str, Any]]
) -> dict[str, Any]:
    latest = _latest_failure(failed)
    failed_job_ids = {
        str(row.get("job_id"))
        for row in failed_jobs
        if row.get("job_id") not in (None, "", "unknown")
    }
    affected_dataflow_jobs = {
        str(row.get("job_id"))
        for row in failed
        if row.get("job_id") not in (None, "", "unknown")
    }
    affected_job_shapes = {_job_key(job) for job in failed_jobs}
    affected_dataflows = {_dataflow_id(row) for row in failed if _dataflow_id(row)}
    affected_routes = {
        (
            str(
                row.get("source_name") or row.get("source_connection_name") or "unknown"
            ),
            str(
                row.get("destination_name")
                or row.get("destination_connection_name")
                or "unknown"
            ),
        )
        for row in failed
    }
    repeated = _repeated_failure_signatures(failed, limit=1000)
    total_failed_records = len(failed)
    repeated_runs = sum(
        int(row.get("failed_runs") or 0)
        for row in repeated
        if int(row.get("failed_runs") or 0) >= 2
    )
    top_signature = max(
        repeated, key=lambda row: int(row.get("failed_runs") or 0), default=None
    )
    top_cause_runs = int(top_signature.get("failed_runs") or 0) if top_signature else 0
    return {
        "failed_jobs": len(failed_jobs),
        "failed_dataflows": len(failed),
        "affected_jobs": len(failed_job_ids),
        "affected_dataflow_jobs": len(affected_dataflow_jobs),
        "affected_job_shapes": len(affected_job_shapes),
        "affected_job_contexts": len(affected_job_shapes),
        "affected_stages": len(affected_job_shapes),
        "affected_dataflows": len(affected_dataflows),
        "affected_routes": len(affected_routes),
        "repeated_signatures": sum(
            1 for row in repeated if int(row.get("failed_runs") or 0) >= 2
        ),
        "unique_signatures": len(repeated),
        "repeated_failure_runs": repeated_runs,
        "repeated_failure_share": _rate(repeated_runs, total_failed_records),
        "total_failed_records": total_failed_records,
        "top_cause_runs": top_cause_runs,
        "top_cause_share": _rate(top_cause_runs, total_failed_records),
        "top_cause_category": top_signature.get("failure_category")
        if top_signature
        else None,
        "top_cause_phase": top_signature.get("failure_phase")
        if top_signature
        else None,
        "top_cause_signature": top_signature.get("failure_signature")
        if top_signature
        else None,
        "latest_failure_at": latest.get("failure_time") if latest else None,
        "latest_failure_name": latest.get("dataflow_name") or latest.get("job_id")
        if latest
        else None,
    }


def _failure_enriched_job(job: dict[str, Any]) -> dict[str, Any]:
    message = str(job.get("error_message") or "")
    classification = classify_failure(message)
    category = classification.category
    phase = "job"
    return {
        **job,
        "failure_kind": "job",
        "failure_phase": phase,
        "failure_message": message,
        "failure_category": category,
        "failure_tags": list(classification.tags),
        "failure_rule_id": classification.rule_id,
        "failure_signature": _failure_signature(category, phase, message),
        "failure_target": job.get("job_id") or "unknown",
        "failure_time": job.get("end_time") or job.get("start_time"),
        "job_key": _job_key(job),
        "job_shape_label": _job_shape_label(job),
    }


def _latest_failure(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(
        rows,
        key=lambda row: _time_value(
            row.get("failure_time") or row.get("end_time") or row.get("start_time")
        ),
    )


def _latest_failure_queue(
    failed: list[dict[str, Any]], limit: int = 60
) -> list[dict[str, Any]]:
    rows = [*failed]
    rows.sort(
        key=lambda row: _time_value(
            row.get("failure_time") or row.get("end_time") or row.get("start_time")
        ),
        reverse=True,
    )
    return rows[:limit]


def _repeated_failure_signatures(
    failed: list[dict[str, Any]],
    limit: int = 30,
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in failed:
        buckets[str(row.get("failure_signature") or "unknown")].append(row)

    result: list[dict[str, Any]] = []
    for signature, items in buckets.items():
        latest = _latest_failure(items) or {}
        affected_jobs = {
            str(item.get("job_id"))
            for item in items
            if item.get("job_id") not in (None, "", "unknown")
        }
        affected_dataflows = {
            str(item.get("dataflow_id") or item.get("dataflow_name"))
            for item in items
            if item.get("dataflow_id") or item.get("dataflow_name")
        }
        result.append(
            {
                "failure_signature": signature,
                "failure_category": latest.get("failure_category") or "Unspecified",
                "failure_phase": latest.get("failure_phase") or "unknown",
                "failed_runs": len(items),
                "affected_jobs": len(affected_jobs),
                "affected_dataflows": len(affected_dataflows),
                "latest_time": latest.get("failure_time"),
                "latest_error": latest.get("failure_message"),
                "sample_dataflow": latest.get("dataflow_name"),
                "sample_job_id": latest.get("job_id"),
                "failure_target": latest.get("failure_target"),
            }
        )
    result.sort(key=lambda item: str(item.get("latest_time") or ""), reverse=True)
    result.sort(key=lambda item: int(item.get("affected_jobs") or 0), reverse=True)
    result.sort(key=lambda item: int(item.get("failed_runs") or 0), reverse=True)
    return result[:limit]


def _failure_endpoint_impact(
    failed: list[dict[str, Any]], limit: int = 30
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in failed:
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
        latest = _latest_failure(items) or {}
        affected_jobs = {
            str(item.get("job_id"))
            for item in items
            if item.get("job_id") not in (None, "", "unknown")
        }
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
                "failed_runs": len(items),
                "affected_jobs": len(affected_jobs),
                "failure_category": _dominant_value(items, "failure_category"),
                "failure_phase": _dominant_value(items, "failure_phase"),
                "latest_time": latest.get("failure_time"),
                "latest_error": latest.get("failure_message"),
            }
        )
    result.sort(key=lambda item: str(item.get("latest_time") or ""), reverse=True)
    result.sort(key=lambda item: int(item.get("affected_jobs") or 0), reverse=True)
    result.sort(key=lambda item: int(item.get("failed_runs") or 0), reverse=True)
    return result[:limit]


def _failure_category_phase_matrix(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    phases = ("source", "transform", "destination", "overhead", "unknown")
    buckets: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        category = str(row.get("failure_category") or "Unspecified")
        phase = _failure_phase_value(row, phases)
        buckets[category][phase] += 1

    result = []
    for category, counts in buckets.items():
        item = {
            "category": category,
            **{phase: int(counts.get(phase, 0)) for phase in phases},
        }
        item["total"] = sum(int(item[phase]) for phase in phases)
        result.append(item)
    result.sort(key=lambda item: str(item.get("category") or ""))
    result.sort(key=lambda item: int(item.get("total") or 0), reverse=True)
    return result


def _failure_phase_breakdown(
    rows: list[dict[str, Any]],
    dimension_key: str,
    *,
    label_key: str,
    count_key: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    phases = ("source", "transform", "destination", "overhead", "unknown")
    buckets: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        label = _dimension_value(row.get(dimension_key))
        buckets[label][_failure_phase_value(row, phases)] += 1

    result: list[dict[str, Any]] = []
    for label, counts in buckets.items():
        item = {
            label_key: label,
            **{phase: int(counts.get(phase, 0)) for phase in phases},
        }
        item[count_key] = sum(int(item[phase]) for phase in phases)
        result.append(item)
    result.sort(key=lambda item: str(item.get(label_key) or "unknown"))
    result.sort(key=lambda item: int(item.get(count_key) or 0), reverse=True)
    return result[:limit]


def _failure_phase_value(row: dict[str, Any], phases: tuple[str, ...]) -> str:
    phase = str(row.get("failure_phase") or "unknown")
    return phase if phase in phases else "unknown"


def _top_failing_dataflows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    phases = ("source", "transform", "destination", "overhead", "unknown")
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
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
        affected_job_ids = {
            str(item.get("job_id"))
            for item in items
            if item.get("job_id") not in (None, "", "unknown")
        }
        phase_counts = Counter(_failure_phase_value(item, phases) for item in items)
        result.append(
            {
                "dataflow_id": dataflow_id,
                "dataflow_name": latest.get("dataflow_name") or dataflow_id,
                "error_count": len(items),
                **{phase: int(phase_counts.get(phase, 0)) for phase in phases},
                "affected_job_count": len(affected_job_ids),
                "last_error": latest.get("error_message")
                or latest.get("destination_error_message")
                or latest.get("source_error_message"),
                "last_time": latest.get("end_time") or latest.get("start_time"),
                "stage": latest.get("stage") or "unknown",
                "engine_name": latest.get("engine_name") or "unknown",
            }
        )
    result.sort(key=lambda item: str(item.get("dataflow_name") or "unknown"))
    result.sort(key=lambda item: int(item.get("affected_job_count") or 0), reverse=True)
    result.sort(key=lambda item: _time_value(item.get("last_time")), reverse=True)
    result.sort(key=lambda item: int(item.get("error_count") or 0), reverse=True)
    return result[:30]


def _error_categories(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for row in rows:
        category = row.get("failure_category")
        if category:
            counts[str(category)] += 1
            continue
        message = (
            row.get("source_error_message")
            or row.get("transform_error_message")
            or row.get("destination_error_message")
            or row.get("error_messages")
            or row.get("error_message")
            or ""
        )
        counts[_error_category(str(message))] += 1
    return _counter_rows(counts, "category")


def _failure_trend(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        if _status(row) != "failed":
            continue
        date_key = _run_date(row)
        kind = str(row.get("failure_kind") or "dataflow")
        if kind == "job":
            buckets[date_key]["failed_jobs"] += 1
        else:
            buckets[date_key]["failed_dataflows"] += 1
    return [
        {
            "date": key,
            "failed": values.get("failed_jobs", 0) + values.get("failed_dataflows", 0),
            "failed_jobs": values.get("failed_jobs", 0),
            "failed_dataflows": values.get("failed_dataflows", 0),
        }
        for key, values in sorted(buckets.items())
    ]


def _top_counter(
    rows: list[dict[str, Any]], key: str, limit: int = 20
) -> list[dict[str, Any]]:
    counts = Counter(str(row.get(key) or "unknown") for row in rows)
    return [{"name": name, "count": count} for name, count in counts.most_common(limit)]


def _error_category(message: str) -> str:
    return categorize_failure(message)
