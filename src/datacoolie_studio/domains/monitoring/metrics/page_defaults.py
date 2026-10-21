from __future__ import annotations

from typing import Any


def empty_health_page() -> dict[str, Any]:
    return {"status": "unknown", "label": "Unknown", "reasons": []}


def empty_operations_page() -> dict[str, Any]:
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


def empty_failures_page() -> dict[str, Any]:
    return {
        "failed_by_stage": [],
        "failed_by_source_connection_type": [],
        "top_failing_dataflows": [],
        "error_categories": [],
        "failure_trend_by_date": [],
        "failed_records": [],
    }


def empty_performance_page() -> dict[str, Any]:
    return {
        "duration_breakdown": [],
        "duration_vs_rows": [],
        "slowest_dataflows": [],
        "duration_by_stage": [],
        "engine_stage_matrix": [],
    }


def empty_volume_page() -> dict[str, Any]:
    return {
        "kpis": {},
        "rows_by_date": [],
        "bytes_by_date": [],
        "volume_by_load_type": [],
        "top_dataflows_by_rows_written": [],
    }


def empty_maintenance_page() -> dict[str, Any]:
    return {
        "kpis": {},
        "bytes_reclaimed_by_table": [],
        "format_comparison": [],
        "per_table": [],
        "duration_vs_files_removed": [],
        "bytes_reclaimed_by_date": [],
    }


def empty_freshness_page() -> dict[str, Any]:
    return {
        "kpis": {},
        "latest_freshness_by_dataflow": [],
        "watermark_movement": [],
        "stale_candidates": [],
        "skipped_patterns": [],
    }


def empty_diagnostics_page() -> dict[str, Any]:
    return {"kpis": {}, "job_id_evidence": [], "read_errors": []}
