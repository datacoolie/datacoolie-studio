from __future__ import annotations

from tests.support.v6_records import insert_native_rows

import pytest

from datacoolie_studio.domains.analytics import schema
from datacoolie_studio.domains.monitoring.diagnostics_repository import diagnostics_read_model
from datacoolie_studio.domains.monitoring.read_models.jobs import jobs_read_model

from tests.domains.monitoring.monitoring_read_model_support import (
    _dataflow_metric_connection,
    _rebuild_serving_facts,
)


def _insert_job_snapshot(
    connection,
    *,
    observed_at: str,
    log_records_dropped: int,
    log_bytes_dropped: int = 0,
) -> None:
    insert_native_rows(
        connection,
        schema.JOB_TABLE,
        1,
        [
            (
                f"snapshot-{observed_at}.json",
                "job_jsonl",
                "{}",
                {
                    "job_id": "job-1",
                    "status": "failed",
                    "end_time": "2026-07-20T01:03:00Z",
                    "observed_at": observed_at,
                    "total_dataflows": 2,
                    "total_succeeded": 1,
                    "total_failed": 1,
                    "total_skipped": 0,
                    "log_records_dropped": log_records_dropped,
                    "log_bytes_dropped": log_bytes_dropped,
                },
            )
        ],
        schema.JOB_COLUMN_TYPES,
    )
    _rebuild_serving_facts(connection)


@pytest.mark.parametrize(
    ("observed_at", "log_records_dropped", "expected_coverage"),
    [
        ("2026-07-20T01:03:00Z", 2, "lost_detail"),
        ("2026-07-20T01:01:00Z", 0, "stale_snapshot"),
    ],
)
def test_latest_job_snapshot_loss_or_staleness_suppresses_mismatch(
    observed_at: str,
    log_records_dropped: int,
    expected_coverage: str,
) -> None:
    connection = _dataflow_metric_connection()
    try:
        if expected_coverage == "stale_snapshot":
            connection.execute(
                f"UPDATE {schema.JOB_TABLE} "
                "SET observed_at = '2026-07-20T00:30:00Z'"
            )
            _rebuild_serving_facts(connection)
        _insert_job_snapshot(
            connection,
            observed_at=observed_at,
            log_records_dropped=log_records_dropped,
        )
        result = jobs_read_model(
            [], {}, grain="day", timezone_name="UTC",
            analytics_context=(connection, [1], "reconciliation-contract"),
        )

        checks = result["reconciliation_checks"]
        assert checks
        assert {row["scope_coverage"] for row in checks} == {expected_coverage}
        assert all(row["severity"] == "insufficient_coverage" for row in checks)
        assert all(row["difference"] is None for row in checks)
        assert not any(row["severity"] == "warning" for row in checks)
    finally:
        connection.close()


def test_filtered_child_scope_is_insufficient_even_with_complete_latest_snapshot() -> None:
    connection = _dataflow_metric_connection()
    try:
        result = jobs_read_model(
            [], {"stage": "silver"}, grain="day", timezone_name="UTC",
            analytics_context=(connection, [1], "reconciliation-contract"),
        )

        assert result["reconciliation_checks"] == [{
            "severity": "insufficient_coverage",
            "_source_id": 1,
            "job_id": "job-1",
            "log_session_id": None,
            "metric": "child_scope",
            "expected": None,
            "observed": 2,
            "difference": None,
            "scope_coverage": "subset",
        }]
    finally:
        connection.close()


def test_diagnostics_reconciliation_surfaces_loss_without_counting_a_mismatch() -> None:
    connection = _dataflow_metric_connection()
    try:
        _insert_job_snapshot(
            connection,
            observed_at="2026-07-20T01:03:00Z",
            log_records_dropped=3,
            log_bytes_dropped=128,
        )
        result = diagnostics_read_model(
            [], {}, grain="day", timezone_name="UTC",
            analytics_context=(connection, [1], "reconciliation-contract"),
        )

        reconciliation = result["reconciliation"]
        assert reconciliation["status"] == "insufficient_coverage"
        assert reconciliation["mismatch_count"] == 0
        assert reconciliation["checks"]
        assert {row["scope_coverage"] for row in reconciliation["checks"]} == {"lost_detail"}
        assert all(row["difference"] is None for row in reconciliation["checks"])
        assert result["diagnostics"]["kpis"]["reconciliation_mismatches"] == 0
    finally:
        connection.close()
