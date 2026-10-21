from __future__ import annotations

from tests.support.v6_records import insert_native_rows

from contextlib import nullcontext
import duckdb
import pytest
from datetime import datetime, timezone

from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.analytics import schema_tables as analytics_schema_tables
from datacoolie_studio.domains.monitoring import log_repository
from datacoolie_studio.domains.monitoring.read_models.dataflows import dataflows_read_model
from datacoolie_studio.domains.monitoring.read_models.jobs import jobs_read_model
from datacoolie_studio.domains.monitoring.read_models.common import filtered_ctes
from datacoolie_studio.domains.analytics.v6_projection import field_expression

from tests.domains.monitoring.monitoring_read_model_support import (
    _dataflow_metric_connection,
    _rebuild_serving_facts,
)

def test_filtered_ctes_apply_dataflow_scope_to_jobs() -> None:
    connection = duckdb.connect(":memory:")
    from tests.support.v6_records import native_record
    analytics_schema_tables.ensure_tables(connection)
    for job_id, stage, end in (("job-1", "silver", "2026-07-20T00:01:00Z"), ("job-2", "bronze", "2026-07-20T00:02:00Z")):
        flow = native_record({"job_id": job_id, "stage": stage, "status": "failed", "end_time": end, "dataflow_run_id": job_id + '-run'})
        job = native_record({"_type": "job_run_log", "job_id": job_id, "status": "failed", "end_time": end, "observed_at": end, "engine_name": "duckdb"})
        insert_native_rows(connection, analytics_schema.DATAFLOW_TABLE, 1, [(job_id, "dataflow", "{}", flow)], analytics_schema.DATAFLOW_COLUMN_TYPES)
        insert_native_rows(connection, analytics_schema.JOB_TABLE, 1, [(job_id, "job", "{}", job)], analytics_schema.JOB_COLUMN_TYPES)
    _rebuild_serving_facts(connection)
    ctes, params = filtered_ctes(
        [1],
        {"status": "failed", "stage": "silver"},
        dataflow_columns=("_source_id", "job_id", "status", "stage"),
        job_columns=("_source_id", "job_id", "status"),
    )

    dataflows = connection.execute(
        f"{ctes} SELECT job_id, stage FROM filtered_dataflows ORDER BY job_id",
        params,
    ).fetchall()
    jobs = connection.execute(
        f"{ctes} SELECT job_id FROM filtered_jobs ORDER BY job_id",
        params,
    ).fetchall()

    assert dataflows == [("job-1", "silver")]
    assert jobs == [("job-1",)]

def test_filtered_ctes_reject_unknown_projection_columns() -> None:
    with pytest.raises(ValueError, match="Unsupported Monitoring columns"):
        filtered_ctes(
            [1],
            {},
            dataflow_columns=("not_a_column",),
            job_columns=("job_id",),
        )


def test_native_presence_projection_uses_value_nullability() -> None:
    expression = field_expression(
        "message_present",
        "BOOLEAN",
        raw="r",
        available={"message"},
    )
    assert expression == 'r."message" IS NOT NULL'
    assert "_present_paths" not in expression


def test_filtered_ctes_use_physical_metrics_and_source_free_raw_marker() -> None:
    connection = _dataflow_metric_connection()
    ctes, params = filtered_ctes(
        [1],
        {},
        dataflow_columns=("source_rows_read", "destination_rows_written", "raw_record_json"),
        job_columns=("job_id",),
    )

    explain = connection.execute(
        f"EXPLAIN {ctes} SELECT source_rows_read, destination_rows_written, raw_record_json "
        "FROM filtered_dataflows",
        params,
    ).fetchone()[1]
    assert "etl_dataflow_runs" not in ctes
    assert "etl_dataflow_runs" not in explain
    assert "metrics" not in ctes
    values = connection.execute(
        f"{ctes} SELECT source_rows_read, destination_rows_written, raw_record_json "
        "FROM filtered_dataflows ORDER BY source_rows_read",
        params,
    ).fetchall()
    assert [(int(row[0]), int(row[1]), row[2]) for row in values] == [
        (50, 0, None),
        (100, 90, None),
    ]


def test_filtered_ctes_hydrate_native_nested_fields_by_exact_provenance() -> None:
    connection = duckdb.connect(":memory:")
    analytics_schema_tables.ensure_tables(connection)
    analytics_schema_tables.ensure_typed_table(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    analytics_schema_tables.ensure_typed_table(
        connection,
        analytics_schema.JOB_TABLE,
        analytics_schema.JOB_COLUMN_TYPES,
    )

    dataflow_rows = {
        1: [("shared.log", "dataflow_jsonl", "{}", {
            "_record_line": 1,
            "dataflow_id": "flow-1-1",
            "dataflow_run_id": "run-1-1",
            "job_id": "job-1-1",
            "log_session_id": None,
            "status": "succeeded",
            "start_time": "2026-07-20T00:00:00Z",
            "end_time": "2026-07-20T00:01:00Z",
            "read_request": {"read_range": {"column": "marker-1-1"}},
            "attempts": [{
                "attempt_number": 1,
                "status": "succeeded",
                "source": {"status": "succeeded", "message": "marker-1-1"},
                "transform": {"status": "succeeded", "transformers_applied": ["marker-1-1"]},
                "destination": {
                    "status": "succeeded",
                    "operation_details": [{"operation": "marker-1-1"}],
                },
            }],
        }), ("shared.log", "dataflow_jsonl", "{}", {
            "_record_line": 2,
            "dataflow_id": "flow-1-2",
            "dataflow_run_id": "run-1-2",
            "job_id": "job-1-2",
            "log_session_id": None,
            "status": "succeeded",
            "start_time": "2026-07-20T00:02:00Z",
            "end_time": "2026-07-20T00:03:00Z",
            "read_request": {"read_range": {"column": "marker-1-2"}},
            "attempts": [{
                "attempt_number": 1,
                "status": "succeeded",
                "source": {"status": "succeeded", "message": "marker-1-2"},
                "transform": {"status": "succeeded", "transformers_applied": ["marker-1-2"]},
                "destination": {
                    "status": "succeeded",
                    "operation_details": [{"operation": "marker-1-2"}],
                },
            }],
        })],
        2: [("shared.log", "dataflow_jsonl", "{}", {
            "_record_line": 1,
            "dataflow_id": "flow-2-1",
            "dataflow_run_id": "run-2-1",
            "job_id": "job-2-1",
            "log_session_id": None,
            "status": "succeeded",
            "start_time": "2026-07-20T00:04:00Z",
            "end_time": "2026-07-20T00:05:00Z",
            "read_request": {"read_range": {"column": "marker-2-1"}},
            "attempts": [{
                "attempt_number": 1,
                "status": "succeeded",
                "source": {"status": "succeeded", "message": "marker-2-1"},
                "transform": {"status": "succeeded", "transformers_applied": ["marker-2-1"]},
                "destination": {
                    "status": "succeeded",
                    "operation_details": [{"operation": "marker-2-1"}],
                },
            }],
        })],
    }
    for source_id, rows_for_source in dataflow_rows.items():
        insert_native_rows(
            connection,
            analytics_schema.DATAFLOW_TABLE,
            source_id,
            rows_for_source,
            analytics_schema.DATAFLOW_COLUMN_TYPES,
        )

    job_rows = {
        1: [("shared-jobs.log", "job_jsonl", "{}", {
            "_record_line": 1,
            "job_id": "job-1-1",
            "log_session_id": None,
            "status": "succeeded",
            "start_time": "2026-07-20T00:00:00Z",
            "end_time": "2026-07-20T00:01:00Z",
            "observed_at": "2026-07-20T00:01:00Z",
            "retry_count": 11,
        }), ("shared-jobs.log", "job_jsonl", "{}", {
            "_record_line": 2,
            "job_id": "job-1-2",
            "log_session_id": None,
            "status": "succeeded",
            "start_time": "2026-07-20T00:02:00Z",
            "end_time": "2026-07-20T00:03:00Z",
            "observed_at": "2026-07-20T00:03:00Z",
            "retry_count": 12,
        })],
        2: [("shared-jobs.log", "job_jsonl", "{}", {
            "_record_line": 1,
            "job_id": "job-2-1",
            "log_session_id": None,
            "status": "succeeded",
            "start_time": "2026-07-20T00:04:00Z",
            "end_time": "2026-07-20T00:05:00Z",
            "observed_at": "2026-07-20T00:05:00Z",
            "retry_count": 21,
        })],
    }
    for source_id, rows_for_source in job_rows.items():
        insert_native_rows(
            connection,
            analytics_schema.JOB_TABLE,
            source_id,
            rows_for_source,
            analytics_schema.JOB_COLUMN_TYPES,
        )
    _rebuild_serving_facts(connection)

    ctes, params = filtered_ctes(
        [1, 2],
        {},
        dataflow_columns=(
            "_source_id", "_file_uri", "_record_line", "log_session_id", "attempts",
            "read_request", "destination_operation_details", "transformers_applied",
        ),
        job_columns=("_source_id", "_file_uri", "_record_line", "job_id", "log_session_id", "retry_count"),
    )
    dataflows = connection.execute(
        f"{ctes} SELECT _source_id, _file_uri, _record_line, log_session_id, "
        "attempts[-1].source.message, read_request.read_range.column, "
        "CAST(destination_operation_details AS VARCHAR), CAST(transformers_applied AS VARCHAR) "
        "FROM filtered_dataflows ORDER BY _source_id, _record_line",
        params,
    ).fetchall()
    assert [(row[0], row[1], row[2], row[3]) for row in dataflows] == [
        (1, "shared.log", 1, None),
        (1, "shared.log", 2, None),
        (2, "shared.log", 1, None),
    ]
    for row, marker in zip(dataflows, ("marker-1-1", "marker-1-2", "marker-2-1"), strict=True):
        assert row[4] == marker
        assert row[5] == marker
        assert marker in row[6]
        assert marker in row[7]

    jobs = connection.execute(
        f"{ctes} SELECT _source_id, _file_uri, _record_line, job_id, log_session_id, retry_count "
        "FROM filtered_jobs ORDER BY _source_id, _record_line",
        params,
    ).fetchall()
    assert [(row[0], row[1], row[2], row[4]) for row in jobs] == [
        (1, "shared-jobs.log", 1, None),
        (1, "shared-jobs.log", 2, None),
        (2, "shared-jobs.log", 1, None),
    ]
    assert [int(row[5]) for row in jobs] == [11, 12, 21]


def test_dataflow_event_time_is_materialized_and_reconciled() -> None:
    connection = duckdb.connect(":memory:")
    analytics_schema_tables.ensure_typed_table(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    analytics_schema_tables.ensure_typed_table(
        connection,
        analytics_schema.JOB_TABLE,
        analytics_schema.JOB_COLUMN_TYPES,
    )
    insert_native_rows(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        1,
        [
            (
                "explicit.parquet",
                "dataflow_parquet",
                "{}",
                {
                    "dataflow_run_id": "run-explicit",
                    "__event_time": "2026-07-20T03:00:00Z",
                    "start_time": "2026-07-20T01:00:00Z",
                    "end_time": "2026-07-20T02:00:00Z",
                },
            ),
            (
                "start-only.parquet",
                "dataflow_parquet",
                "{}",
                {
                    "dataflow_run_id": "run-start",
                    "start_time": "2026-07-20T04:00:00Z",
                },
            ),
        ],
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    _rebuild_serving_facts(connection)

    assert connection.execute(
        """
        SELECT
          dataflow_run_id,
          epoch(__event_time),
          event_time = __event_time
        FROM monitoring_dataflow_facts
        ORDER BY dataflow_run_id
        """
    ).fetchall() == [
        ("run-explicit", 1784516400.0, True),
        ("run-start", 1784520000.0, True),
    ]

def test_job_log_query_preserves_correlated_stage_operation_scope(monkeypatch) -> None:
    connection = duckdb.connect(":memory:")
    analytics_schema_tables.ensure_typed_table(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    analytics_schema_tables.ensure_typed_table(
        connection,
        analytics_schema.JOB_TABLE,
        analytics_schema.JOB_COLUMN_TYPES,
    )
    insert_native_rows(
        connection,
        analytics_schema.JOB_TABLE,
        1,
        [
            (
                "jobs.jsonl",
                "job_jsonl",
                "{}",
                {
                    "job_id": "job-crossed",
                    "status": "succeeded",
                    "start_time": "2026-07-20T00:00:00Z",
                    "end_time": "2026-07-20T00:10:00Z",
                    "stages": '["bronze", "silver"]',
                    "operation_types": '["etl", "maintenance"]',
                },
            ),
            (
                "jobs.jsonl",
                "job_jsonl",
                "{}",
                {
                    "job_id": "job-match",
                    "status": "succeeded",
                    "start_time": "2026-07-20T01:00:00Z",
                    "end_time": "2026-07-20T01:10:00Z",
                    "stages": '["silver"]',
                    "operation_types": '["etl"]',
                },
            ),
        ],
        analytics_schema.JOB_COLUMN_TYPES,
    )
    dataflow_rows = [
        {
            "job_id": "job-crossed",
            "dataflow_run_id": "run-bronze-etl",
            "stage": "bronze",
            "operation_type": "etl",
            "status": "succeeded",
            "start_time": "2026-07-20T00:00:00Z",
            "end_time": "2026-07-20T00:01:00Z",
        },
        {
            "job_id": "job-crossed",
            "dataflow_run_id": "run-silver-maintenance",
            "stage": "silver",
            "operation_type": "maintenance",
            "status": "succeeded",
            "start_time": "2026-07-20T00:01:00Z",
            "end_time": "2026-07-20T00:02:00Z",
        },
        {
            "job_id": "job-match",
            "dataflow_run_id": "run-silver-etl",
            "stage": "silver",
            "operation_type": "etl",
            "status": "succeeded",
            "start_time": "2026-07-20T01:00:00Z",
            "end_time": "2026-07-20T01:01:00Z",
        },
    ]
    insert_native_rows(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        1,
        [
            (f"{row['dataflow_run_id']}.parquet", "dataflow_parquet", "{}", row)
            for row in dataflow_rows
        ],
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    _rebuild_serving_facts(connection)
    monkeypatch.setattr(
        log_repository,
        "reader",
        lambda _paths: nullcontext((connection, [1], "generation-1")),
    )

    records, total, errors = log_repository.query_cached_job_logs(
        object(),
        [],
        {"range": "all", "stage": "silver", "operationType": "etl"},
    )

    assert errors == []
    assert total == 1
    assert [row["job_id"] for row in records] == ["job-match"]
    assert records[0]["child_dataflow_count"] == 1

def test_default_all_dataflow_filters_do_not_exclude_jobs_without_children() -> None:
    assert not log_repository.monitoring_has_dataflow_scope(
        {
            "stage": "all",
            "connection": "all",
            "sourceType": "all",
            "destinationType": "all",
            "loadType": "all",
            "operationType": "all",
        }
    )

def test_dataflows_read_model_aggregates_bounded_metric_contracts() -> None:
    connection = _dataflow_metric_connection()

    result = dataflows_read_model(
        [],
        {},
        grain="day",
        timezone_name="UTC",
        now=datetime(2026, 7, 21, tzinfo=timezone.utc),
        analytics_context=(connection, [1], "generation-1"),
    )

    summary = result["summary"]
    assert summary["dataflow_records"] == 2
    assert summary["succeeded"] == 1
    assert summary["failed"] == 1
    assert summary["success_rate"] == 50
    assert summary["p50_duration_seconds"] == 60
    assert result["duration_by_stage"][0]["operation_mix"] == "etl: 2"
    # Recorded phase durations contribute independently; missing attempt duration
    # leaves only Overhead unavailable, while root metrics keep their population.
    phases = result["phase_health_by_stage"][0]
    assert phases["phase_runs_measured"] == phases["phase_runs_eligible"] == 2
    assert phases["total_duration_seconds"] == 140
    assert phases["source_duration_seconds"] == 30
    assert phases["transform_duration_seconds"] == 50
    assert phases["destination_duration_seconds"] == 60
    assert phases["overhead_duration_seconds"] is None
    assert result["endpoint_health"][0]["runs"] == 2
    assert result["name_status_health"][0]["dataflow_name"] == "orders"
    assert result["windows"]["last_24_hours"]["dataflow_runs"] == 2

def test_dataflows_read_model_returns_empty_contract_without_analytics() -> None:
    result = dataflows_read_model(
        [],
        {},
        grain="day",
        timezone_name="UTC",
        analytics_context=(None, [], "analytics-v2:empty"),
    )

    assert result["summary"] == {}
    assert result["duration_by_stage"] == []
    assert result["windows"]["today"]["dataflow_runs"] == 0

def test_jobs_read_model_reuses_bounded_duration_and_window_metrics() -> None:
    connection = _dataflow_metric_connection()

    result = jobs_read_model(
        [],
        {},
        grain="day",
        timezone_name="UTC",
        now=datetime(2026, 7, 21, tzinfo=timezone.utc),
        analytics_context=(connection, [1], "generation-1"),
    )

    assert result["summary"]["total_jobs"] == 1
    assert result["summary"]["job_failure_rate"] == 100
    assert result["summary"]["p95_duration_seconds"] == 180
    assert result["job_duration_by_operation"][0]["operation_type"] == "etl, maintenance"
    assert result["workload_efficiency"][0]["child_dataflow_count"] == 2
    assert result["child_fanout"][0]["total_dataflows"] == 2
    assert result["status_by_stage"][0]["failed"] == 1
    assert result["latest_failed_job"]["job_id"] == "job-1"
    assert result["reconciliation_checks"] == []
    assert result["summary"]["total_running"] is None
    assert result["summary"]["total_pending"] is None
    assert result["windows"]["last_24_hours"]["job_running"] is None
    assert result["windows"]["last_24_hours"]["job_pending"] is None
