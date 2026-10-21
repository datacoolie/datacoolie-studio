from __future__ import annotations

import duckdb
import pytest

from datacoolie_studio.domains.monitoring.log_repository import monitoring_filter_sql
from datacoolie_studio.domains.monitoring.metrics.filtering import matches_investigation


@pytest.mark.parametrize("kind,column", [
    ("job_id", "job_id"),
    ("dataflow_run_id", "dataflow_run_id"),
    ("dataflow", "dataflow_id"),
])
def test_exact_identity_preserves_case_and_backticks(kind, column):
    identity = " ABC`-123 "
    records = [
        {column: identity, "dataflow_name": "other", "log_session_id": "session-A", "_source_id": 1},
        {column: identity.lower(), "dataflow_name": "other", "log_session_id": "session-A", "_source_id": 1},
        {column: identity.replace("`", ""), "dataflow_name": "other", "log_session_id": "session-A", "_source_id": 1},
        {column: identity, "dataflow_name": "other", "log_session_id": "session-B"},
        {column: identity, "dataflow_name": "other", "log_session_id": "session-A", "_source_id": 2},
    ]
    filters = {
        "investigateKind": kind,
        "investigateValue": identity,
        "log_session_scope": "exact",
        "log_session_id": "session-A",
        "investigateSourceId": "1",
    }
    assert [row for row in records if matches_investigation(row, filters, True)] == records[:1]
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(f"CREATE TABLE runs ({column} VARCHAR, dataflow_name VARCHAR, log_session_id VARCHAR, _source_id BIGINT)")
        conn.executemany("INSERT INTO runs VALUES (?, ?, ?, ?)", [
            [row[column], row["dataflow_name"], row["log_session_id"], row.get("_source_id", 1)] for row in records
        ])
        where, params = monitoring_filter_sql(filters, "d", "d")
        assert conn.execute(f"SELECT {column} FROM runs d WHERE TRUE{where}", params).fetchall() == [(identity,)]
    finally:
        conn.close()


def test_dataflow_name_investigation_remains_case_insensitive():
    assert matches_investigation(
        {"dataflow_id": "id", "dataflow_name": "Orders"},
        {"investigateKind": "dataflow", "investigateValue": "orders"}, True,
    )


@pytest.mark.parametrize("filters", [
    {"investigateKind": "dataflow_run_id", "investigateValue": "RUN`A"},
    {"connection": "source-A"},
])
def test_job_child_scope_does_not_cross_sessions(filters):
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE jobs (_source_id BIGINT, job_id VARCHAR, log_session_id VARCHAR)")
        conn.execute("INSERT INTO jobs VALUES (1, 'JOB`A', 'A'), (1, 'JOB`A', 'B')")
        conn.execute("CREATE TABLE runs (_source_id BIGINT, job_id VARCHAR, log_session_id VARCHAR, "
                     "dataflow_run_id VARCHAR, source_name VARCHAR, destination_name VARCHAR)")
        conn.execute("INSERT INTO runs VALUES (1, 'JOB`A', 'A', 'RUN`A', 'source-A', 'destination')")
        where, params = monitoring_filter_sql(filters, "j", "j", include_dataflow_filters=False,
                                              dataflow_table="runs")
        assert conn.execute(f"SELECT log_session_id FROM jobs j WHERE TRUE{where}", params).fetchall() == [("A",)]
    finally:
        conn.close()
