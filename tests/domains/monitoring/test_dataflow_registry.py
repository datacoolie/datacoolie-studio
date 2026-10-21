from __future__ import annotations

import json

import pytest

from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.monitoring.read_models.dataflow_registry import (
    dataflow_registry_read_model,
)

from tests.domains.monitoring.monitoring_read_model_support import (
    _V6_SUCCESS_EVIDENCE,
    _dataflow_metric_connection,
    _rebuild_serving_facts,
)
from tests.support.v6_records import insert_native_rows


def test_registry_latest_null_save_does_not_backfill_and_check_has_own_rank() -> None:
    connection = _dataflow_metric_connection()
    evidence = json.loads(_V6_SUCCESS_EVIDENCE)
    evidence["attempts"][-1]["watermark"]["saved_watermark"] = None
    insert_native_rows(
        connection, analytics_schema.DATAFLOW_TABLE, 1,
        [("latest-null-save.parquet", "dataflow_parquet", "revision", {
            "dataflow_id": "dataflow-1", "dataflow_run_id": "run-latest-null",
            "status": "failed", "operation_type": "etl",
            "end_time": "2026-07-20T03:00:00Z",
            "raw_record_json": json.dumps(evidence),
        })], analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    _rebuild_serving_facts(connection)
    result = dataflow_registry_read_model(
        [], {"range": "all"}, limit=1, offset=0,
        sort_by="latest_run_at", sort_dir="desc",
        analytics_context=(connection, [1], "generation-1"),
    )
    row = result["records"][0]
    assert row["freshness_latest_run_id"] == "run-latest-null"
    assert row["latest_success_watermark_run_id"] == "run-latest-null"
    assert row["latest_success_watermark_preview"] is None
    assert row["latest_freshness_status"] == "succeeded"
    assert row["freshness_checks_eligible"] == 1
    assert row["freshness_run_count"] == 3


@pytest.mark.parametrize("facet", ["freshness", "performance", "volume"])
def test_registry_facet_lists_preserve_population_and_owner_values(facet: str) -> None:
    connection = _dataflow_metric_connection()
    kwargs = dict(limit=100, offset=0, sort_by="latest_run_at", sort_dir="desc",
                  analytics_context=(connection, [1], "generation-1"))
    filters = {"_asOf": "2026-10-14T00:00:00Z"}
    complete = dataflow_registry_read_model([], filters, **kwargs)
    compact = dataflow_registry_read_model([], filters, facet=facet, **kwargs)
    assert compact["total_records"] == complete["total_records"]
    assert [row["dataflow_id"] for row in compact["records"]] == [
        row["dataflow_id"] for row in complete["records"]
    ]
    for row, expected in zip(compact["records"], complete["records"], strict=True):
        assert row["run_count"] == expected["run_count"]
        assert "attempts" not in row and "raw_record_json" not in row
        for key, value in row.items():
            if key == "volume_candidate_run_reasons":
                assert sorted(value) == sorted(expected[key])
            else:
                assert value == expected[key]
        for other in {"performance", "volume"} - {facet}:
            assert not any(key.startswith(other + "_") for key in row)


def test_registry_deduplicates_admitted_cross_source_run_copies() -> None:
    connection = _dataflow_metric_connection()
    insert_native_rows(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        2,
        [
            (
                "source-2-copy.parquet",
                "dataflow_parquet",
                _V6_SUCCESS_EVIDENCE,
                {
                    "dataflow_run_id": "run-1",
                    "log_schema_version": 6,
                    "raw_record_json": _V6_SUCCESS_EVIDENCE,
                    "dataflow_id": "dataflow-1",
                    "dataflow_name": "orders",
                    "stage": "silver",
                    "operation_type": "etl",
                    "status": "succeeded",
                    "start_time": "2026-07-20T00:00:00Z",
                    "end_time": "2026-07-20T00:01:00Z",
                    "duration_seconds": 60,
                    "source_name": "source",
                    "destination_name": "destination",
                    "source_status": "succeeded",
                    "source_duration_seconds": 10,
                    "transform_status": "succeeded",
                    "transform_duration_seconds": 20,
                    "destination_status": "succeeded",
                    "destination_duration_seconds": 20,
                    "source_rows_read": 100,
                    "destination_rows_written": 90,
                },
            )
        ],
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    _rebuild_serving_facts(connection)

    result = dataflow_registry_read_model(
        [],
        {},
        limit=100,
        offset=0,
        sort_by="latest_run_at",
        sort_dir="desc",
        analytics_context=(connection, [1, 2], "generation-1"),
    )

    assert result["total_records"] == 1
    row = result["records"][0]
    assert row["dataflow_id"] == "dataflow-1"
    assert row["run_count"] == 2
    assert row["volume_rows_read"] == 150
    assert row["volume_rows_written"] == 90
    assert "read_request" not in row

    read_watermark = dataflow_registry_read_model(
        [],
        {"investigateKind": "dataflow_id", "investigateValue": "dataflow-1"},
        limit=1,
        offset=0,
        sort_by="latest_run_at",
        sort_dir="desc",
        section="read-watermark",
        analytics_context=(connection, [1, 2], "generation-1"),
    )
    # Compact read evidence carries the selected in-filter run reference;
    # native read/attempt hydration belongs to the API boundary.
    assert read_watermark["records"][0]["latest_run_id"] == "run-2"


def test_registry_uses_dataflow_id_as_entity_grain_and_keeps_latest_provenance() -> None:
    connection = _dataflow_metric_connection()
    insert_native_rows(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        2,
        [
            (
                "source-2-later.parquet",
                "dataflow_parquet",
                _V6_SUCCESS_EVIDENCE,
                {
                    "dataflow_run_id": "run-3",
                    "log_schema_version": 6,
                    "raw_record_json": _V6_SUCCESS_EVIDENCE,
                    "dataflow_id": "dataflow-1",
                    "dataflow_name": "orders",
                    "stage": "gold",
                    "operation_type": "etl",
                    "status": "succeeded",
                    "start_time": "2026-07-20T02:00:00Z",
                    "end_time": "2026-07-20T02:01:00Z",
                    "duration_seconds": 60,
                    "source_name": "source-2",
                    "destination_name": "destination-2",
                    "source_rows_read": 7,
                    "destination_rows_written": 6,
                },
            )
        ],
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    _rebuild_serving_facts(connection)

    result = dataflow_registry_read_model(
        [],
        {},
        limit=100,
        offset=0,
        sort_by="latest_run_at",
        sort_dir="desc",
        analytics_context=(connection, [1, 2], "generation-1"),
    )

    assert result["total_records"] == 1
    row = result["records"][0]
    assert row["run_count"] == 3
    assert row["latest_run_id"] == "run-3"
    assert row["latest_source_id"] == 2
    assert row["source_name"] == "source-2"
    assert row["freshness_latest_run_id"] == "run-3"
    assert len(row["last_statuses"]) == 3


def test_registry_technical_section_returns_bounded_physical_reference_evidence() -> None:
    connection = _dataflow_metric_connection()
    result = dataflow_registry_read_model(
        [],
        {},
        limit=100,
        offset=0,
        sort_by="latest_run_at",
        sort_dir="desc",
        section="technical",
        analytics_context=(connection, [1], "generation-1"),
    )

    row = result["records"][0]
    assert row["technical_reference_total"] == 2
    assert row["technical_reference_inspected"] == 2
    assert row["technical_reference_complete"] is True
    assert len(row["technical_references"]) == 2
    assert all("raw_record_json" not in reference for reference in row["technical_references"])


def test_registry_execution_section_returns_canonical_runs_without_raw_payload() -> None:
    connection = _dataflow_metric_connection()

    result = dataflow_registry_read_model(
        [],
        {"investigateKind": "dataflow_id", "investigateValue": "dataflow-1"},
        limit=100,
        offset=0,
        sort_by="latest_run_at",
        sort_dir="desc",
        section="execution",
        analytics_context=(connection, [1], "generation-1"),
    )

    assert result["total_records"] == 2
    assert {row["dataflow_run_id"] for row in result["records"]} == {"run-1", "run-2"}
    assert all("raw_record_json" not in row for row in result["records"])
    assert all("attempts" not in row and "execution_attempts" not in row for row in result["records"])


def test_registry_keeps_v6_missing_workload_unknown_and_exposes_coverage() -> None:
    connection = _dataflow_metric_connection()
    insert_native_rows(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        2,
        [
            (
                "sparse.parquet",
                "dataflow_parquet",
                _V6_SUCCESS_EVIDENCE,
                {
                    "dataflow_run_id": "run-sparse",
                    "log_schema_version": 6,
                    "raw_record_json": _V6_SUCCESS_EVIDENCE,
                    "dataflow_id": "dataflow-1",
                    "dataflow_name": "orders",
                    "operation_type": "etl",
                    "status": "succeeded",
                    "start_time": "2026-07-20T03:00:00Z",
                    "end_time": "2026-07-20T03:01:00Z",
                    "duration_seconds": 60,
                    "source_rows_read": None,
                    "destination_rows_written": None,
                },
            )
        ],
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    _rebuild_serving_facts(connection)

    result = dataflow_registry_read_model(
        [],
        {},
        limit=100,
        offset=0,
        sort_by="latest_run_at",
        sort_dir="desc",
        analytics_context=(connection, [1, 2], "generation-1"),
    )

    row = result["records"][0]
    assert row["run_count"] == 3
    assert row["volume_rows_read"] is None
    assert row["volume_rows_read_observed"] == 150
    assert row["volume_rows_read_measured"] == 2
    assert row["volume_rows_read_eligible"] == 3
    assert "attempts" not in row


def test_registry_detail_uses_filtered_profile_and_bounded_recent_runs() -> None:
    connection = _dataflow_metric_connection()
    history = [
        ("run-3", "failed", "2026-07-20T02:00:00Z", "maintenance", "maintenance profile"),
        ("run-4", "skipped", "2026-07-20T03:00:00Z", "maintenance", "skipped profile"),
        ("run-5", "running", "2026-07-20T04:00:00Z", "unknown", "running profile"),
        ("run-6", "succeeded", "2026-07-20T05:00:00Z", "etl", "older success"),
        ("run-7", "succeeded", "2026-07-20T06:00:00Z", "etl", "filtered success"),
        # The whole-run profile must preserve this latest explicit null rather
        # than borrowing the older name/route from run-7.
        ("run-8", "paused", "2026-07-20T07:00:00Z", "maintenance", None),
    ]
    insert_native_rows(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        1,
        [
            (
                f"{run_id}.parquet",
                "dataflow_parquet",
                _V6_SUCCESS_EVIDENCE,
                {
                    "dataflow_id": "dataflow-1",
                    "dataflow_run_id": run_id,
                    "job_id": "job-1",
                    "status": status,
                    "operation_type": operation,
                    "dataflow_name": name,
                    "dataflow_description": None if name is None else f"{name} description",
                    "source_name": None if name is None else "source",
                    "end_time": event_time,
                    "observed_at": event_time,
                },
            )
            for run_id, status, event_time, operation, name in history
        ],
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    _rebuild_serving_facts(connection)

    result = dataflow_registry_read_model(
        [],
        {"range": "all", "status": "succeeded"},
        limit=100,
        offset=0,
        sort_by="latest_run_at",
        sort_dir="desc",
        section="overview",
        target_dataflow_id="dataflow-1",
        analytics_context=(connection, [1], "generation-1"),
    )

    row = result["records"][0]
    assert row["latest_run_id"] == "run-7"
    assert row["run_count"] == 3  # run-1, run-6 and run-7
    assert [run["dataflow_run_id"] for run in row["recent_runs"]] == ["run-1", "run-6", "run-7"]
    assert row["profile_snapshot"]["dataflow_run_id"] == "run-7"
    assert row["profile_snapshot"]["dataflow_name"] == "filtered success"
    assert row["profile_snapshot"]["source_name"] == "source"
    assert row["profile_snapshot"]["outside_filter"] is False

    configuration = dataflow_registry_read_model(
        [],
        {"range": "all", "status": "succeeded"},
        limit=1,
        offset=0,
        sort_by="latest_run_at",
        sort_dir="desc",
        section="configuration",
        target_dataflow_id="dataflow-1",
        analytics_context=(connection, [1], "generation-1"),
    )
    assert configuration["records"][0]["latest_run_id"] == "run-7"
    assert configuration["records"][0]["profile_snapshot"]["dataflow_name"] == "filtered success"
    assert configuration["records"][0]["profile_snapshot"]["outside_filter"] is False

    all_statuses = dataflow_registry_read_model(
        [], {"range": "all"}, limit=1, offset=0, sort_by="latest_run_at", sort_dir="desc",
        section="overview", target_dataflow_id="dataflow-1",
        analytics_context=(connection, [1], "generation-1"),
    )["records"][0]
    assert all_statuses["profile_snapshot"]["dataflow_run_id"] == "run-8"
    assert all_statuses["profile_snapshot"]["dataflow_name"] is None
    assert all_statuses["profile_snapshot"]["source_name"] is None
    assert [run["dataflow_run_id"] for run in all_statuses["recent_runs"]] == ["run-4", "run-5", "run-6", "run-7", "run-8"]
    assert all_statuses["recent_runs"][-1]["dataflow_run_id"] == all_statuses["latest_run_id"]
    assert {state: all_statuses[f"{state}_count"] for state in ("succeeded", "failed", "skipped", "running", "pending", "other")} == {
        "succeeded": 3, "failed": 2, "skipped": 1, "running": 1, "pending": 0, "other": 1,
    }


def test_registry_window_controls_profile_watermark_and_physical_reference_counts() -> None:
    connection = _dataflow_metric_connection()
    evidence = json.loads(_V6_SUCCESS_EVIDENCE)
    evidence["attempts"][-1]["watermark"].update({"save_status": "saved", "saved_watermark": {"id": 99}})
    insert_native_rows(
        connection, analytics_schema.DATAFLOW_TABLE, 1,
        [("later-save.parquet", "dataflow_parquet", json.dumps(evidence), {
            "dataflow_id": "dataflow-1", "dataflow_run_id": "run-later-save",
            "job_id": "job-later", "status": "succeeded", "operation_type": "etl",
            "dataflow_name": "later profile", "end_time": "2026-08-20T03:00:00Z",
            "observed_at": "2026-08-20T03:00:00Z", "raw_record_json": json.dumps(evidence),
            "duration_seconds": 120, "source_duration_seconds": 30,
            "transform_duration_seconds": 20, "destination_duration_seconds": 20,
            "source_rows_read": 300,
        })], analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    _rebuild_serving_facts(connection)
    window = {"range": "custom", "startTime": "2026-07-20T00:00:00Z", "endTime": "2026-07-21T00:00:00Z"}

    def detail(filters, section="overview"):
        return dataflow_registry_read_model(
            [], filters, limit=1, offset=0, sort_by="latest_run_at", sort_dir="desc",
            section=section, target_dataflow_id="dataflow-1",
            analytics_context=(connection, [1], "generation-1"),
        )["records"][0]

    all_time = detail({"range": "all"})
    scoped = detail(window)
    assert all_time["profile_snapshot"]["dataflow_run_id"] == "run-later-save"
    assert all_time["last_saved_watermark_run_id"] == "run-later-save"
    assert scoped["run_count"] == all_time["run_count"] - 1
    assert (scoped["succeeded_count"], scoped["failed_count"]) == (1, 1)
    assert (all_time["succeeded_count"], all_time["failed_count"]) == (2, 1)
    assert scoped["performance_avg_duration_seconds"] == 90
    assert all_time["performance_avg_duration_seconds"] == 100
    # These V6 records lack complete attempt timing; filtering must not turn
    # missing strict phase measurements into fabricated totals.
    assert scoped["performance_source_duration_seconds"] is None
    assert all_time["performance_source_duration_seconds"] is None
    assert scoped["performance_rows_read_per_second"] == pytest.approx(150 / 180, abs=0.001)
    assert all_time["performance_rows_read_per_second"] == pytest.approx(450 / 300, abs=0.001)
    assert scoped["profile_snapshot"]["dataflow_run_id"] != "run-later-save"
    assert scoped["last_saved_watermark_run_id"] != "run-later-save"
    assert scoped["last_saved_watermark_scope"] == "filtered"
    assert scoped["last_saved_watermark_outside_filter"] is False
    assert detail(window, "configuration")["latest_run_id"] == scoped["latest_run_id"]
    assert detail(window, "technical")["technical_reference_total"] < detail({"range": "all"}, "technical")["technical_reference_total"]

    for kind, value in [("job_id", "job-1"), ("dataflow_run_id", "run-1")]:
        investigation = {"range": "all", "investigateKind": kind, "investigateValue": value}
        investigated = detail(investigation)
        assert investigated["profile_snapshot"]["dataflow_run_id"] != "run-later-save"
        assert investigated["profile_snapshot"]["outside_filter"] is False
        assert investigated["last_saved_watermark_run_id"] != "run-later-save"
        assert detail(investigation, "configuration")["latest_run_id"] == investigated["latest_run_id"]

    direct = dataflow_registry_read_model(
        [], {"range": "all", "investigateKind": "dataflow_id", "investigateValue": "dataflow-1"},
        limit=1, offset=0, sort_by="latest_run_at", sort_dir="desc",
        analytics_context=(connection, [1], "generation-1"),
    )["records"][0]
    assert direct["last_saved_watermark_scope"] == "filtered"
    assert direct["last_saved_watermark_outside_filter"] is False


def test_registry_recent_runs_puts_canonical_latest_on_right_with_time_ties_and_nulls() -> None:
    connection = _dataflow_metric_connection()
    insert_native_rows(
        connection, analytics_schema.DATAFLOW_TABLE, 1,
        [(file_uri, "dataflow_parquet", run_id, {
            "dataflow_id": "dataflow-1", "dataflow_run_id": run_id, "job_id": "job-1",
            "status": status, "operation_type": "etl", "end_time": event_time,
            "observed_at": event_time, "raw_record_json": _V6_SUCCESS_EVIDENCE,
        }) for file_uri, run_id, status, event_time in [
            ("a-latest.parquet", "run-z", "succeeded", "2026-07-20T05:00:00Z"),
            ("z-tied.parquet", "run-a", "succeeded", "2026-07-20T05:00:00Z"),
            ("unknown-time.parquet", "run-null", "pending", None),
        ]], analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    _rebuild_serving_facts(connection)
    row = dataflow_registry_read_model(
        [], {"range": "all"}, limit=1, offset=0, sort_by="latest_run_at", sort_dir="desc",
        target_dataflow_id="dataflow-1", analytics_context=(connection, [1], "generation-1"),
    )["records"][0]
    assert [run["dataflow_run_id"] for run in row["recent_runs"]] == ["run-null", "run-1", "run-2", "run-a", "run-z"]
    assert row["recent_runs"][-1]["dataflow_run_id"] == row["latest_run_id"] == "run-z"


def test_registry_runtime_groups_joint_null_context_and_pages_before_limit() -> None:
    connection = _dataflow_metric_connection()
    insert_native_rows(
        connection,
        analytics_schema.JOB_TABLE,
        1,
        [
            (
                "spark-job.jsonl",
                "job_jsonl",
                "{}",
                {
                    "job_id": "job-2",
                    "log_session_id": "session-2",
                    "status": "succeeded",
                    "engine_name": "spark",
                    "metadata_provider_name": "file",
                    "platform_name": "local",
                    "end_time": "2026-07-20T03:03:00Z",
                    "observed_at": "2026-07-20T03:03:00Z",
                },
            ),
            (
                "unknown-job.jsonl",
                "job_jsonl",
                "{}",
                {
                    "job_id": "job-3",
                    "status": "succeeded",
                    "engine_name": None,
                    "metadata_provider_name": None,
                    "platform_name": None,
                    "end_time": "2026-07-20T04:03:00Z",
                    "observed_at": "2026-07-20T04:03:00Z",
                },
            ),
        ],
        analytics_schema.JOB_COLUMN_TYPES,
    )
    insert_native_rows(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        1,
        [
            (
                "spark-run-1.parquet",
                "dataflow_parquet",
                _V6_SUCCESS_EVIDENCE,
                {
                    "dataflow_id": "dataflow-1",
                    "dataflow_run_id": "spark-run-1",
                    "job_id": "job-2",
                    "log_session_id": "session-2",
                    "status": "succeeded",
                    "end_time": "2026-07-20T03:00:00Z",
                    "observed_at": "2026-07-20T03:00:00Z",
                },
            ),
            (
                "spark-run-2.parquet",
                "dataflow_parquet",
                _V6_SUCCESS_EVIDENCE,
                {
                    "dataflow_id": "dataflow-1",
                    "dataflow_run_id": "spark-run-2",
                    "job_id": "job-2",
                    "log_session_id": "session-2",
                    "status": "succeeded",
                    "end_time": "2026-07-20T03:01:00Z",
                    "observed_at": "2026-07-20T03:01:00Z",
                },
            ),
            (
                "unknown-run.parquet",
                "dataflow_parquet",
                _V6_SUCCESS_EVIDENCE,
                {
                    "dataflow_id": "dataflow-1",
                    "dataflow_run_id": "unknown-run",
                    "job_id": "job-3",
                    "status": "succeeded",
                    "end_time": "2026-07-20T04:00:00Z",
                    "observed_at": "2026-07-20T04:00:00Z",
                },
            ),
        ],
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    _rebuild_serving_facts(connection)

    result = dataflow_registry_read_model(
        [],
        {"range": "all"},
        limit=2,
        offset=0,
        sort_by="run_count",
        sort_dir="desc",
        section="runtime",
        target_dataflow_id="dataflow-1",
        analytics_context=(connection, [1], "generation-1"),
    )

    assert result["total_records"] == 3
    assert {row["engine_name"] for row in result["records"]} == {"spark", "duckdb"}
    assert all(row["dataflow_id"] == "dataflow-1" for row in result["records"])
    assert next(row for row in result["records"] if row["engine_name"] == "spark")["run_count"] == 2

    final_page = dataflow_registry_read_model(
        [],
        {"range": "all"},
        limit=2,
        offset=2,
        sort_by="engine_name",
        sort_dir="asc",
        section="runtime",
        target_dataflow_id="dataflow-1",
        analytics_context=(connection, [1], "generation-1"),
    )
    assert final_page["total_records"] == 3
    assert len(final_page["records"]) == 1
    assert final_page["records"][0]["engine_name"] is None
    assert final_page["records"][0]["latest_run_id"] == "unknown-run"

    with pytest.raises(ValueError, match="runtime sort"):
        dataflow_registry_read_model(
            [],
            {"range": "all"},
            limit=100,
            offset=0,
            sort_by="status",
            sort_dir="desc",
            section="runtime",
            target_dataflow_id="dataflow-1",
            analytics_context=(connection, [1], "generation-1"),
        )


def test_registry_runtime_paging_bounds_large_group_population() -> None:
    connection = _dataflow_metric_connection()
    runtime_jobs = [
        (
            f"runtime-job-{index}.jsonl",
            "job_jsonl",
            "{}",
            {
                "job_id": f"runtime-job-{index}",
                "status": "succeeded",
                "end_time": f"2026-07-21T00:{index // 60:02d}:{index % 60:02d}Z",
                "duration_seconds": 1,
                "engine_name": f"engine-{index}",
                "metadata_provider_name": "provider",
                "platform_name": "platform",
                "observed_at": f"2026-07-21T00:{index // 60:02d}:{index % 60:02d}Z",
            },
        )
        for index in range(501)
    ]
    runtime_runs = [
        (
            f"runtime-run-{index}.parquet",
            "dataflow_parquet",
            _V6_SUCCESS_EVIDENCE,
            {
                "dataflow_id": "dataflow-1",
                "dataflow_run_id": f"runtime-run-{index}",
                "job_id": f"runtime-job-{index}",
                "status": "succeeded",
                "operation_type": "etl",
                "start_time": f"2026-07-21T00:{index // 60:02d}:{index % 60:02d}Z",
                "end_time": f"2026-07-21T00:{index // 60:02d}:{index % 60:02d}Z",
                "observed_at": f"2026-07-21T00:{index // 60:02d}:{index % 60:02d}Z",
            },
        )
        for index in range(501)
    ]
    insert_native_rows(
        connection,
        analytics_schema.JOB_TABLE,
        1,
        runtime_jobs,
        analytics_schema.JOB_COLUMN_TYPES,
    )
    insert_native_rows(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        1,
        runtime_runs,
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    _rebuild_serving_facts(connection)

    default_page = dataflow_registry_read_model(
        [],
        {"range": "all"},
        limit=100,
        offset=0,
        sort_by="run_count",
        sort_dir="desc",
        section="runtime",
        target_dataflow_id="dataflow-1",
        analytics_context=(connection, [1], "generation-1"),
    )
    assert default_page["total_records"] == 502  # 501 inserted + base local tuple
    assert len(default_page["records"]) == 100

    maximum_page = dataflow_registry_read_model(
        [],
        {"range": "all"},
        limit=500,
        offset=0,
        sort_by="engine_name",
        sort_dir="asc",
        section="runtime",
        target_dataflow_id="dataflow-1",
        analytics_context=(connection, [1], "generation-1"),
    )
    assert maximum_page["total_records"] == 502
    assert len(maximum_page["records"]) == 500

    beyond_maximum = dataflow_registry_read_model(
        [],
        {"range": "all"},
        limit=500,
        offset=500,
        sort_by="engine_name",
        sort_dir="asc",
        section="runtime",
        target_dataflow_id="dataflow-1",
        analytics_context=(connection, [1], "generation-1"),
    )
    assert beyond_maximum["records"]
    assert beyond_maximum["total_records"] == 502
