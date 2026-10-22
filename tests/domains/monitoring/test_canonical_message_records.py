from __future__ import annotations

import pytest

from tests.support.v6_records import insert_native_rows

from datacoolie_studio.domains.analytics import schema
from datacoolie_studio.domains.monitoring.query_service import dataflow_logs, job_logs
from tests.domains.monitoring.monitoring_test_support import _published_monitoring_paths
from tests.domains.monitoring.monitoring_read_model_support import _rebuild_serving_facts


@pytest.mark.usefixtures("monitoring_workspace")
def test_canonical_messages_reach_record_queries_with_null_precedence(tmp_path, monkeypatch):
    paths = _published_monitoring_paths(tmp_path, monkeypatch)
    import duckdb

    path = tmp_path / "monitoring.duckdb"
    conn = duckdb.connect(str(path))
    try:
        common = {"job_id": "JOB`UPPER", "log_session_id": "SESSION", "dataflow_id": "DF`UPPER",
                  "status": "failed", "source_status": "failed", "end_time": "2026-10-02T00:00:00Z"}
        rows = [
            {**common, "dataflow_run_id": "canonical", "message": "root failure", "source_message": "source failure",
             "error_message": "old root", "source_error_message": "old source"},
            {**common, "dataflow_run_id": "null", "message": None, "source_message": None,
             "error_message": "stale root", "source_error_message": "stale source"},
            {**common, "dataflow_run_id": "legacy", "error_message": "legacy root", "source_error_message": "legacy source"},
        ]
        insert_native_rows(conn, schema.DATAFLOW_TABLE, 1,
                                [(row["dataflow_run_id"], "dataflow", "{}", row) for row in rows], schema.DATAFLOW_COLUMN_TYPES)
        insert_native_rows(conn, schema.JOB_TABLE, 1, [("canonical-job", "job", "{}", {
            "job_id": "JOB`UPPER", "log_session_id": "SESSION", "status": "failed",
            "message": "job failure", "message_truncated": True, "datacoolie_version": "0.2.0",
            "observed_at": "2026-10-02T00:00:01Z", "end_time": "2026-10-02T00:00:00Z",
        })], schema.JOB_COLUMN_TYPES)
        _rebuild_serving_facts(conn)
    finally:
        conn.close()
    filters = {
        "investigateKind": "job_id",
        "investigateValue": "JOB`UPPER",
        "log_session_scope": "exact",
        "log_session_id": "SESSION",
    }
    records = dataflow_logs(paths, session=object(), filters=filters)["records"]
    by_run = {row["dataflow_run_id"]: row for row in records}
    assert by_run["canonical"]["error_message"] == "root failure"
    assert by_run["canonical"]["source_error_message"] == "source failure"
    assert by_run["null"]["error_message"] is None
    assert by_run["null"]["source_error_message"] is None
    assert by_run["legacy"]["error_message"] is None
    assert by_run["legacy"]["source_error_message"] is None
    job = job_logs(paths, session=object(), filters=filters)["records"][0]
    assert job["error_message"] == "job failure"
    assert job["error_message_truncated"] is True
    assert job["datacoolie_version"] == "0.2.0"
    assert job["observed_at"] == "2026-10-02T00:00:01+00:00"
