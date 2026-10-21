from __future__ import annotations

from tests.support.v6_records import insert_native_rows

from datacoolie_studio.domains.analytics import schema
from datacoolie_studio.domains.monitoring.read_models.jobs import jobs_read_model
from tests.domains.monitoring.monitoring_read_model_support import (
    _dataflow_metric_connection,
    _rebuild_serving_facts,
)


def test_jobs_with_same_id_in_separate_sessions_reconcile_independently():
    conn = _dataflow_metric_connection()
    try:
        conn.execute(f"UPDATE {schema.JOB_TABLE} SET log_session_id = 'A'")
        conn.execute(f"UPDATE {schema.DATAFLOW_TABLE} SET log_session_id = 'A'")
        job = {"job_id": "job-1", "log_session_id": "B", "status": "succeeded",
               "end_time": "2026-07-21T01:00:00Z", "observed_at": "2026-07-21T01:01:00Z",
               "total_dataflows": 1, "total_succeeded": 1, "total_failed": 0,
               "total_skipped": 0, "log_records_dropped": 0, "log_bytes_dropped": 0}
        child = {"job_id": "job-1", "log_session_id": "B", "dataflow_run_id": "B-1",
                 "dataflow_id": "dataflow-1", "dataflow_name": "orders", "stage": "gold",
                 "status": "succeeded", "operation_type": "etl", "duration_seconds": 60,
                 "end_time": "2026-07-21T01:00:00Z"}
        insert_native_rows(conn, schema.JOB_TABLE, 1, [("B-job", "job", "{}", job)], schema.JOB_COLUMN_TYPES)
        insert_native_rows(conn, schema.DATAFLOW_TABLE, 1, [("B-run", "dataflow", "{}", child)], schema.DATAFLOW_COLUMN_TYPES)
        _rebuild_serving_facts(conn)
        result = jobs_read_model([], {}, grain="day", timezone_name="UTC", analytics_context=(conn, [1], "test"))
        assert result["reconciliation_checks"] == []
        assert len(result["workload_efficiency"]) == 2
        assert {row["child_dataflow_count"] for row in result["workload_efficiency"]} == {1, 2}
        scoped = jobs_read_model([], {"stage": "gold"}, grain="day", timezone_name="UTC",
                                 analytics_context=(conn, [1], "test"))
        assert scoped["summary"]["total_jobs"] == 1
        assert scoped["reconciliation_checks"] == [{
            "severity": "insufficient_coverage",
            "_source_id": 1,
            "job_id": "job-1",
            "log_session_id": "B",
            "metric": "child_scope",
            "expected": None,
            "observed": 1,
            "difference": None,
            "scope_coverage": "subset",
        }]
    finally:
        conn.close()


def test_same_session_cumulative_snapshot_reconciles_multiple_operations():
    conn = _dataflow_metric_connection()
    try:
        conn.execute(f"UPDATE {schema.JOB_TABLE} SET log_session_id = 'same-session'")
        conn.execute(f"UPDATE {schema.DATAFLOW_TABLE} SET log_session_id = 'same-session'")
        result = jobs_read_model([], {}, grain="day", timezone_name="UTC", analytics_context=(conn, [1], "test"))
        assert result["reconciliation_checks"] == []
        assert result["workload_efficiency"][0]["child_dataflow_count"] == 2
    finally:
        conn.close()
