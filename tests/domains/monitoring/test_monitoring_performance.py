from __future__ import annotations

from pathlib import Path

from datacoolie_studio.domains.monitoring.metrics.performance_projections import _performance_page
from datacoolie_studio.domains.monitoring.page_service import monitoring_page

from tests.domains.monitoring.monitoring_test_support import (
    _published_monitoring_paths,
)

def test_monitoring_performance_page_uses_dataflow_logs(tmp_path: Path, monkeypatch):
    paths = _published_monitoring_paths(tmp_path, monkeypatch)
    performance = monitoring_page(paths, page="performance")
    assert performance["summary"]["dataflow_records"] > 0
    assert performance["performance"]["duration_distribution_by_stage"]

def test_performance_page_builds_runtime_optimization_signals():
    rows = [
        {
            "job_id": "job-1",
            "dataflow_id": "df-small-slow",
            "dataflow_run_id": "run-1",
            "dataflow_name": "small_slow",
            "stage": "bronze",
            "operation_type": "etl",
            "status": "succeeded",
            "duration_seconds": 100,
            "source_duration_seconds": 10,
            "transform_duration_seconds": 5,
            "destination_duration_seconds": 5,
            "overhead_duration_seconds": 80,
            "source_rows_read": 1,
            "destination_rows_written": 1,
            "end_time": "2026-06-20T00:00:00+00:00",
        },
        {
            "job_id": "job-2",
            "dataflow_id": "df-phase-skew",
            "dataflow_run_id": "run-2",
            "dataflow_name": "phase_skew",
            "stage": "silver",
            "operation_type": "etl",
            "status": "succeeded",
            "duration_seconds": 80,
            "source_duration_seconds": 0,
            "transform_duration_seconds": 0,
            "destination_duration_seconds": 78,
            "source_rows_read": 10_000,
            "destination_rows_written": 0,
            "end_time": "2026-06-20T00:01:00+00:00",
        },
        {
            "job_id": "job-3",
            "dataflow_id": "df-fast",
            "dataflow_run_id": "run-3",
            "dataflow_name": "fast",
            "stage": "bronze",
            "operation_type": "etl",
            "status": "succeeded",
            "duration_seconds": 1,
            "source_duration_seconds": 0.2,
            "transform_duration_seconds": 0.2,
            "destination_duration_seconds": 0.2,
            "source_rows_read": 10,
            "destination_rows_written": 10,
            "end_time": "2026-06-20T00:02:00+00:00",
        },
    ]

    page = _performance_page(rows)

    assert page["kpis"]["run_count"] == 3
    assert page["kpis"]["optimization_candidate_count"] >= 1
    assert page["kpis"]["high_overhead_count"] >= 1
    assert page["duration_distribution_by_stage"]
    assert page["phase_contribution_by_stage_operation"]
    assert page["workload_efficiency_points"]
    assert page["slowest_dataflow_profiles"]
    assert page["runtime_context_profiles"]
    assert page["performance_trend"]
    assert page["investigation_queue"][0]["performance_candidate_reason"]
    skew = next(row for row in page["investigation_queue"] if row["dataflow_id"] == "df-phase-skew")
    assert skew["overhead_duration_seconds"] == 2
    assert {row["dataflow_id"] for row in page["slowest_dataflow_profiles"]} == {"df-small-slow", "df-phase-skew", "df-fast"}

def test_performance_page_uses_executable_runs_and_operation_specific_workload_rules():
    rows = [
        {
            "dataflow_id": "etl-positive",
            "operation_type": "etl",
            "status": "succeeded",
            "duration_seconds": 10,
            "source_rows_read": 100,
            "destination_rows_written": 100,
            "source_duration_seconds": 2,
            "transform_duration_seconds": 2,
            "destination_duration_seconds": 4,
            "end_time": "2026-06-20T00:00:00+00:00",
        },
        {
            "dataflow_id": "etl-zero",
            "operation_type": "etl",
            "status": "succeeded",
            "duration_seconds": 20,
            "source_rows_read": 0,
            "destination_rows_written": 0,
            "source_duration_seconds": 4,
            "transform_duration_seconds": 4,
            "destination_duration_seconds": 8,
            "end_time": "2026-06-20T01:00:00+00:00",
        },
        {
            "dataflow_id": "maintenance-zero",
            "operation_type": "maintenance",
            "status": "succeeded",
            "duration_seconds": 80,
            "destination_duration_seconds": 80,
            "end_time": "2026-06-20T02:00:00+00:00",
        },
        {
            "dataflow_id": "maintenance-small",
            "operation_type": "maintenance",
            "status": "succeeded",
            "duration_seconds": 100,
            "destination_duration_seconds": 100,
            "destination_bytes_removed": 10,
            "destination_files_removed": 1,
            "end_time": "2026-06-20T03:00:00+00:00",
        },
        {
            "dataflow_id": "maintenance-large",
            "operation_type": "maintenance",
            "status": "succeeded",
            "duration_seconds": 5,
            "destination_duration_seconds": 5,
            "destination_bytes_removed": 1_000,
            "destination_files_removed": 20,
            "end_time": "2026-06-20T04:00:00+00:00",
        },
        {
            "dataflow_id": "skipped-with-duration",
            "operation_type": "etl",
            "status": "skipped",
            "duration_seconds": 999,
            "end_time": "2026-06-20T05:00:00+00:00",
        },
    ]

    page = _performance_page(rows)
    queue = {row["dataflow_id"]: row for row in page["investigation_queue"]}

    assert page["kpis"]["run_count"] == 5
    assert "skipped-with-duration" not in queue
    assert queue["etl-zero"]["performance_candidate_code"] != "slow_small_workload"
    assert queue["maintenance-zero"]["performance_candidate_code"] != "slow_small_workload"
    assert queue["maintenance-zero"]["performance_candidate_code"] != "slow_small_maintenance"
    assert queue["maintenance-small"]["performance_candidate_code"] == "slow_small_maintenance"
    assert "phase_skew" not in queue["maintenance-small"]["performance_candidate_codes"]
    assert {row["operation_type"] for row in page["workload_efficiency_points"]} == {"etl", "maintenance"}
