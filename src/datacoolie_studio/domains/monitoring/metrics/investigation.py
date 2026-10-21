from __future__ import annotations

from datacoolie_studio.domains.monitoring.metrics.row_normalization import job_shape_label

import hashlib
import json
import re
from collections import Counter, defaultdict
from typing import Any

from datacoolie_studio.domains.monitoring.metrics.aggregates import (
    average,
    numeric_value,
    percentile_clean,
    rate,
    status,
    sum_observed,
)
from datacoolie_studio.domains.monitoring.metrics.failure import (
    classify_failure,
    dataflow_failed_phases,
    dataflow_failure_phase_and_message,
)
from datacoolie_studio.domains.monitoring.metrics.row_normalization import normalized_job_shape
from datacoolie_studio.domains.monitoring.metrics.v6_semantics import (
    failed_attempts,
    final_phase_duration,
    final_overhead_duration,
    freshness_evidence,
    is_supported_log_row,
    phase_timing,
    read_boundary_adjustment,
    recovered_attempt_failures,
)


DECLARED_DESTINATION_FIELDS = (
    "_source_id", "destination_name", "destination_connection_type",
    "destination_format", "destination_catalog", "destination_database",
    "destination_schema", "destination_full_table", "destination_table",
    "destination_path",
)


def declared_destination_key_sql(alias: str = "") -> str:
    """One exact declared identity for registry grouping and investigation."""
    prefix = f"{alias}." if alias else ""
    return "CAST(json_array(" + ", ".join(prefix + field for field in DECLARED_DESTINATION_FIELDS) + ") AS VARCHAR)"


def enrich_job_run_for_investigation(
    job: dict[str, Any],
    child_rows: list[dict[str, Any]] | None = None,
    *,
    version_mode: str = "v6",
) -> dict[str, Any]:
    child_rows = child_rows or []
    enriched = dict(job)
    if child_rows:
        child_status = Counter(status(row) for row in child_rows)
        child_durations = [
            numeric_value(row, "duration_seconds") or 0
            for row in child_rows
            if numeric_value(row, "duration_seconds")
        ]
        enriched.update(
            {
                "child_dataflow_count": len(child_rows),
                "child_succeeded_count": child_status.get("succeeded", 0),
                "child_failed_count": child_status.get("failed", 0),
                "child_skipped_count": child_status.get("skipped", 0),
                "child_running_count": child_status.get("running", 0),
                "child_pending_count": child_status.get("pending", 0),
                "child_p95_duration_seconds": percentile_clean(child_durations, 0.95),
                "child_total_rows_read": sum_observed(child_rows, "source_rows_read", mode=version_mode),
                "child_total_rows_written": sum_observed(
                    child_rows, "destination_rows_written", mode=version_mode
                ),
                "child_total_bytes_added": sum_observed(
                    child_rows, "destination_bytes_added", mode=version_mode
                ),
                "child_total_bytes_removed": sum_observed(
                    child_rows, "destination_bytes_removed", mode=version_mode
                ),
                "child_metric_eligible_count": len(child_rows),
                "child_rows_read_measured_count": sum(
                    1 for row in child_rows if numeric_value(row, "source_rows_read") is not None
                ),
                "child_rows_written_measured_count": sum(
                    1 for row in child_rows if numeric_value(row, "destination_rows_written") is not None
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
        if key not in enriched:
            enriched[key] = 0
    enriched["error_preview"] = error_preview(enriched)
    enriched["reconciliation_mismatch_count"] = _job_reconciliation_mismatch_count(
        enriched
    )
    enriched["reconciliation_status"] = _job_reconciliation_status(enriched)
    enriched["job_key"] = _job_key(enriched)
    enriched["job_shape_label"] = _job_shape_label(enriched)
    return enriched


def _job_reconciliation_mismatch_count(job: dict[str, Any]) -> int:
    if job.get("child_scope_coverage") not in (None, "full"):
        return 0
    expected = job.get("total_dataflows")
    if expected in (None, ""):
        return 0
    observed = numeric_value(job, "child_dataflow_count")
    return 0 if observed is not None and int(expected) == observed else 1


def _job_reconciliation_status(job: dict[str, Any]) -> str:
    if job.get("child_scope_coverage") not in (None, "full"):
        return "insufficient_coverage"
    if job.get("total_dataflows") in (None, ""):
        return "not_available"
    if _job_reconciliation_mismatch_count(job):
        return "mismatch"
    return "matched"


def enrich_dataflow_run_for_investigation(row: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(row)
    if is_supported_log_row(enriched):
        timing = phase_timing(enriched)
        freshness = freshness_evidence(enriched)
        enriched["attempt_failure_count"] = len(failed_attempts(enriched))
        enriched["recovered_attempt_failures"] = recovered_attempt_failures(enriched)
        enriched["attempt_timing"] = timing
        enriched["timing_coverage"] = timing.get("reason") or "complete"
        enriched["overhead_duration_seconds"] = final_overhead_duration(enriched)
        enriched["freshness_evidence"] = freshness
        adjustment = read_boundary_adjustment(enriched)
        enriched.update({
            f"read_boundary_{key}": value
            for key, value in adjustment.items()
        })
        enriched["selection_upper_origin"] = freshness.get("selection_upper_origin")
        enriched["persistence_state"] = freshness.get("persistence_state")
        enriched["save_status"] = freshness.get("save_status")
    if status(enriched) == "failed":
        enriched = failure_enriched_dataflow(enriched)
    enriched["source_display"] = _endpoint_display(enriched, "source")
    enriched["destination_display"] = _endpoint_display(enriched, "destination")
    enriched["phase_health"] = phase_health(enriched)
    enriched["error_phase"] = _error_phase(enriched)
    enriched["error_preview"] = error_preview(enriched)
    watermark = watermark_classification(enriched)
    enriched["coverage_state"] = watermark["coverage_state"]
    enriched["movement_state"] = watermark["movement_state"]
    enriched["watermark_issue_state"] = watermark["watermark_issue_state"]
    enriched["adjustment_state"] = watermark.get("adjustment_state")
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


def phase_health(row: dict[str, Any]) -> str:
    failed_phases = dataflow_failed_phases(row)
    if failed_phases:
        return f"{failed_phases[0]}_failed"
    if is_supported_log_row(row) and not phase_timing(row).get("available"):
        return "unknown"
    phases = ("source", "transform", "destination", "overhead")
    durations = {phase: performance_phase_duration(row, phase) for phase in phases}
    measurable = {phase: value for phase, value in durations.items() if value is not None}
    if measurable:
        slowest = max(measurable.items(), key=lambda item: item[1])
        return f"{slowest[0]}_bottleneck"
    return "unknown" if status(row) == "unknown" else "ok"


def _error_phase(row: dict[str, Any]) -> str:
    failed_phases = dataflow_failed_phases(row)
    if failed_phases:
        return failed_phases[0]
    for phase in ("source", "transform", "destination"):
        if row.get(f"{phase}_error_message"):
            return phase
    return "job" if row.get("error_message") else ""


def error_preview(row: dict[str, Any]) -> str:
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


def watermark_classification(row: dict[str, Any]) -> dict[str, Any]:
    return {**freshness_evidence(row), **read_boundary_adjustment(row)}


def is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    try:
        if value == "":
            return True
        return bool(value != value)
    except (TypeError, ValueError):
        return False


def failure_enriched_dataflow(row: dict[str, Any]) -> dict[str, Any]:
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
    signature = failure_signature(category, phase, message)
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


def failure_signature(category: str, phase: str, message: str) -> str:
    normalized = _normalize_error_message(message)
    return f"{category}|{phase}|{normalized or 'unknown'}"


def _normalize_error_message(message: str) -> str:
    text = str(message or "").strip().lower()
    text = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "<uuid>",
        text,
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


def performance_phase_duration(row: dict[str, Any], phase: str) -> float | None:
    if is_supported_log_row(row):
        if phase == "overhead":
            # Root elapsed time includes retries and gaps.  It cannot be used
            # as a remainder of final phase durations.
            return final_overhead_duration(row)
        value = final_phase_duration(row, phase)
        return max(0.0, value) if value is not None else None
    if phase != "overhead":
        return max(0.0, numeric_value(row, f"{phase}_duration_seconds") or 0)
    overhead = numeric_value(row, "overhead_duration_seconds")
    if overhead is not None:
        return max(0.0, overhead)
    duration = numeric_value(row, "duration_seconds") or 0
    known_duration = sum(
        max(0.0, numeric_value(row, f"{known_phase}_duration_seconds") or 0)
        for known_phase in ("source", "transform", "destination")
    )
    return max(0.0, duration - known_duration)


def is_lakehouse_destination(row: dict[str, Any]) -> bool:
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

    return False



def phase_duration_summary(
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
        timing = phase_timing(row) if is_supported_log_row(row) else None
        for phase, status_key, duration_key in phase_definitions:
            if timing is not None and not timing.get("available"):
                continue
            if status_key and row.get(status_key):
                phase_status = status({**row, "status": row.get(status_key)})
                bucket["statuses"][phase][phase_status] += 1
            duration = phase_duration(row, phase, duration_key)
            if duration is not None:
                bucket["durations"][phase].append(duration)
        if "overhead" in failed_phases:
            bucket["statuses"]["overhead"]["failed"] += 1

    for row in rows:
        if status(row) not in {"succeeded", "failed", "skipped"}:
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
        if any(is_supported_log_row(item) for item in rows):
            row["timing_coverage"] = "complete"
        if is_total:
            row["is_total"] = 1
        for phase, _, _ in phase_definitions:
            statuses = bucket["statuses"][phase]
            durations = phase_durations[phase]
            duration = sum(durations)
            row[f"{phase}_duration_seconds"] = round(duration, 3)
            row[f"{phase}_duration_percent"] = rate(duration, total_duration)
            row[f"{phase}_run_count"] = len(durations)
            row[f"{phase}_avg_duration_seconds"] = average(durations)
            row[f"{phase}_p95_duration_seconds"] = percentile_clean(durations, 0.95)
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


def phase_duration(row: dict[str, Any], phase: str, duration_key: str) -> float | None:
    if is_supported_log_row(row):
        if phase == "overhead":
            return final_overhead_duration(row)
        value = final_phase_duration(row, phase)
        return max(0.0, value) if value is not None else None
    if phase != "overhead":
        return numeric_value(row, duration_key)
    total_duration = numeric_value(row, "duration_seconds")
    if total_duration is None:
        return None
    known_duration = sum(
        numeric_value(row, known_key) or 0
        for known_key in (
            "source_duration_seconds",
            "transform_duration_seconds",
            "destination_duration_seconds",
        )
    )
    return max(0.0, total_duration - known_duration)


def _job_key(job: dict[str, Any]) -> str:
    payload = json.dumps(
        normalized_job_shape(job),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _job_shape_label(job: dict[str, Any]) -> str:
    return job_shape_label(job)


def _has_value(value: Any) -> bool:
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return value not in (None, "")
