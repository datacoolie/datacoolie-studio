from __future__ import annotations

import pytest

from pathlib import Path
import json

from datacoolie_studio.domains.logs.reader import read_system_log_file
from datacoolie_studio.domains.monitoring.metrics.row_normalization import (
    dataflow_operation_type as _dataflow_operation_type,
)
from datacoolie_studio.domains.monitoring.metrics.investigation import (
    enrich_dataflow_run_for_investigation as _enrich_dataflow_run_for_investigation,
    phase_duration_summary as _phase_duration_summary,
)
from datacoolie_studio.domains.monitoring.metrics.maintenance_projections import _maintenance_upstream_dataflows
from datacoolie_studio.domains.monitoring.query_service import dataflow_logs, job_logs

from tests.domains.monitoring.monitoring_test_support import (
    _published_monitoring_paths,
)

def test_maintenance_upstream_dataflows_preserves_full_aggregate():
    rows = [
        {
            "dataflow_id": "orders",
            "dataflow_name": "Orders",
            "status": "succeeded",
            "end_time": "2026-07-16T10:00:00Z",
            "source_name": "crm",
            "source_table": "orders_raw",
            "destination_load_type": "merge",
            "source_rows_read": 10,
        },
        {
            "dataflow_id": "orders",
            "dataflow_name": "Orders",
            "status": "failed",
            "end_time": "2026-07-16T11:00:00Z",
            "source_name": "crm",
            "source_table": "orders_raw",
            "destination_load_type": "merge",
            "source_rows_read": 5,
        },
    ]

    result = _maintenance_upstream_dataflows(rows)

    assert result == [{
        "dataflow_id": "orders",
        "dataflow_name": "Orders",
        "stage": "unknown",
        "operation_type": "unknown",
        "source": "crm · orders_raw",
        "load_type": "merge",
        "latest_status": "failed",
        "latest_time": "2026-07-16T11:00:00Z",
        "run_count": 2,
        "rows_read": 15.0,
    }]

def test_system_log_scope_defaults_to_job_only_and_can_include_dataflows(tmp_path: Path):
    log_file = tmp_path / "system.json"
    log_file.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                    {
                        "log_schema_version": 6, "_type": "system_log",
                        "datacoolie_version": "0.2.0", "log_session_id": "session-1",
                        "job_id": "job-1", "job_num": 1, "job_index": 0,
                        "ts": "2026-07-21T00:00:00+00:00", "level": "INFO",
                        "logger": "test.system", "msg": "job missing dataflow id",
                    },
                    {
                        "log_schema_version": 6, "_type": "system_log",
                        "datacoolie_version": "0.2.0", "log_session_id": "session-1",
                        "job_id": "job-1", "job_num": 1, "job_index": 0,
                        "ts": "2026-07-21T00:00:01+00:00", "level": "INFO",
                        "logger": "test.system", "dataflow_id": None, "msg": "job null id",
                    },
                    {
                        "log_schema_version": 6, "_type": "system_log",
                        "datacoolie_version": "0.2.0", "log_session_id": "session-1",
                        "job_id": "job-1", "job_num": 1, "job_index": 0,
                        "ts": "2026-07-21T00:00:02+00:00", "level": "INFO",
                        "logger": "test.system", "dataflow_id": "", "msg": "job empty id",
                    },
                    {
                        "log_schema_version": 6, "_type": "system_log",
                        "datacoolie_version": "0.2.0", "log_session_id": "session-1",
                        "job_id": "job-1", "job_num": 1, "job_index": 0,
                        "ts": "2026-07-21T00:00:03+00:00", "level": "INFO",
                        "logger": "test.system", "dataflow_id": "df-orders", "msg": "child",
                    },
            ]
        ),
        encoding="utf-8",
    )

    job_rows, job_total, errors = read_system_log_file(str(log_file), job_id="job-1")
    assert not errors
    assert job_total == 3
    assert [row["msg"] for row in job_rows] == [
        "job missing dataflow id",
        "job null id",
        "job empty id",
    ]

    all_rows, all_total, errors = read_system_log_file(
        str(log_file),
        job_id="job-1",
        include_dataflow_logs=True,
    )
    assert not errors
    assert all_total == 4
    assert len(all_rows) == 4

    dataflow_rows, dataflow_total, errors = read_system_log_file(
        str(log_file),
        job_id="job-1",
        dataflow_id="df-orders",
    )
    assert not errors
    assert dataflow_total == 1
    assert [row["msg"] for row in dataflow_rows] == ["child"]

@pytest.mark.usefixtures("monitoring_workspace")
def test_monitoring_job_and_dataflow_logs_include_investigation_fields(
    tmp_path: Path,
    monkeypatch,
):
    paths = _published_monitoring_paths(tmp_path, monkeypatch)
    jobs = job_logs(paths, limit=5, session=object())
    dataflows = dataflow_logs(paths, limit=5, session=object())

    assert jobs["records"]
    assert dataflows["records"]
    assert {
        "child_dataflow_count",
        "child_failed_count",
        "reconciliation_status",
        "error_preview",
    } <= set(jobs["records"][0])
    assert {
        "source_display",
        "destination_display",
        "phase_health",
        "error_phase",
        "error_preview",
        "movement_state",
        "linked_job_status",
    } <= set(dataflows["records"][0])

    failed_dataflow = _enrich_dataflow_run_for_investigation({
        "dataflow_run_id": "run-1",
        "dataflow_name": "read_orders",
        "status": "failed",
        "source_status": "failed",
        "source_error_message": "Path does not exist: /landing/orders",
    })
    assert failed_dataflow["failure_kind"] == "dataflow"
    assert failed_dataflow["failure_phase"] == "source"
    assert failed_dataflow["failure_category"] == "Missing object"
    assert failed_dataflow["failure_message"] == "Path does not exist: /landing/orders"

def test_dataflow_phase_health_includes_overhead_and_preserves_failed_phase_precedence():
    overhead_dominant = _enrich_dataflow_run_for_investigation({
        "dataflow_run_id": "9ea65b5b-799f-49e6-9de4-c671e9c1d7a5",
        "status": "succeeded",
        "duration_seconds": 0.03766,
        "source_duration_seconds": 0.006128,
        "transform_duration_seconds": 0.004543,
        "destination_duration_seconds": 0.004084,
        "overhead_duration_seconds": 0.022905,
    })
    assert overhead_dominant["phase_health"] == "overhead_bottleneck"

    failed_source = _enrich_dataflow_run_for_investigation({
        **overhead_dominant,
        "status": "failed",
        "source_status": "failed",
        "source_error_message": "Source read failed",
    })
    assert failed_source["phase_health"] == "source_failed"

def test_phase_duration_summary_uses_completed_statuses_and_exact_total_metrics():
    rows = [
        {
            "status": "succeeded",
            "operation_type": "etl",
            "duration_seconds": 100,
            "source_duration_seconds": 20,
            "transform_duration_seconds": 30,
            "destination_duration_seconds": 40,
            "source_status": "succeeded",
            "transform_status": "succeeded",
            "destination_status": "succeeded",
        },
        {
            "status": "failed",
            "operation_type": "etl",
            "duration_seconds": 50,
            "source_duration_seconds": 10,
            "transform_duration_seconds": 10,
            "destination_duration_seconds": 10,
            "source_status": "failed",
        },
        {
            "status": "failed",
            "operation_type": "etl",
            "duration_seconds": 20,
            "source_duration_seconds": 5,
            "transform_duration_seconds": 5,
            "destination_duration_seconds": 5,
        },
        {
            "status": "skipped",
            "operation_type": "etl",
            "duration_seconds": 30,
            "source_duration_seconds": 10,
            "transform_duration_seconds": 10,
            "destination_duration_seconds": 0,
        },
        {
            "status": "succeeded",
            "operation_type": "etl",
            "source_status": "succeeded",
        },
        {
            "status": "pending",
            "operation_type": "etl",
            "duration_seconds": 200,
            "source_duration_seconds": 100,
            "transform_duration_seconds": 50,
            "destination_duration_seconds": 25,
        },
        {
            "status": "running",
            "operation_type": "etl",
            "duration_seconds": 200,
            "source_duration_seconds": 100,
            "transform_duration_seconds": 50,
            "destination_duration_seconds": 25,
        },
    ]

    summary = _phase_duration_summary(rows, "operation_type", _dataflow_operation_type)

    assert [row["operation_type"] for row in summary] == ["Total", "etl"]
    total = summary[0]
    assert total["is_total"] == 1
    assert total["source_duration_seconds"] == 45
    assert total["source_run_count"] == 4
    assert total["source_avg_duration_seconds"] == 11.25
    assert total["source_p95_duration_seconds"] == 20
    assert total["source_failed"] == 1
    assert total["overhead_duration_seconds"] == 45
    assert total["overhead_failed"] == 1
