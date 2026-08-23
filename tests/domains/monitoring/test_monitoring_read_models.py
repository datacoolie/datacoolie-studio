from __future__ import annotations

from contextlib import nullcontext
import duckdb
import pytest
from datetime import datetime, timezone

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.analytics import store as analytics_store
from datacoolie_studio.domains.monitoring import page_service, query_service
from datacoolie_studio.domains.monitoring import log_repository
from datacoolie_studio.domains.monitoring.read_models.dataflows import dataflows_read_model
from datacoolie_studio.domains.monitoring.read_models.jobs import jobs_read_model
from datacoolie_studio.domains.monitoring.read_models.failures import failures_read_model
from datacoolie_studio.domains.monitoring.read_models.overview import overview_read_model
from datacoolie_studio.domains.monitoring.read_models.volume import (
    volume_evidence_read_model,
    volume_read_model,
)
from datacoolie_studio.domains.monitoring.read_models.freshness import (
    freshness_evidence_read_model,
    freshness_read_model,
)
from datacoolie_studio.domains.monitoring.read_models.performance import (
    performance_evidence_read_model,
    performance_read_model,
)
from datacoolie_studio.domains.monitoring.read_models.maintenance import (
    maintenance_evidence_read_model,
    maintenance_read_model,
)
from datacoolie_studio.domains.monitoring.metrics.failure import (
    categorize_failure,
    classify_failure,
    dataflow_failed_phases,
    dataflow_failure_phase_and_message,
    dataflow_phase_failed_sql,
    failure_category_sql,
    failure_message_sql,
    failure_phase_sql,
    failure_rule_id_sql,
    failure_tags_sql,
)
from datacoolie_studio.domains.monitoring.read_models.common import filtered_ctes
from datacoolie_studio.domains.analytics.serving_facts import rebuild_monitoring_serving_facts


def test_filtered_ctes_apply_dataflow_scope_to_jobs() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE etl_dataflow_runs (
          _source_id BIGINT,
          job_id VARCHAR,
          status VARCHAR,
          stage VARCHAR,
          start_time TIMESTAMPTZ,
          end_time TIMESTAMPTZ,
          __event_time TIMESTAMPTZ,
          __run_date DATE
        );
        CREATE TABLE etl_job_runs (
          _source_id BIGINT,
          job_id VARCHAR,
          status VARCHAR,
          engine_name VARCHAR,
          metadata_provider_name VARCHAR,
          platform_name VARCHAR,
          __event_time TIMESTAMPTZ,
          __run_date DATE
        );
        INSERT INTO etl_dataflow_runs VALUES
          (1, 'job-1', 'failed', 'silver', '2026-07-20', '2026-07-20 00:01:00+00', '2026-07-20 00:01:00+00', '2026-07-20'),
          (1, 'job-2', 'failed', 'bronze', '2026-07-20', '2026-07-20 00:02:00+00', '2026-07-20 00:02:00+00', '2026-07-20');
        INSERT INTO etl_job_runs VALUES
          (1, 'job-1', 'failed', 'duckdb', 'file', 'local', '2026-07-20 00:01:00+00', '2026-07-20'),
          (1, 'job-2', 'failed', 'duckdb', 'file', 'local', '2026-07-20 00:02:00+00', '2026-07-20');
        """
    )
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


def test_dataflow_event_time_is_materialized_and_reconciled() -> None:
    connection = duckdb.connect(":memory:")
    analytics_store.ensure_typed_table(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    analytics_store.ensure_typed_table(
        connection,
        analytics_schema.JOB_TABLE,
        analytics_schema.JOB_COLUMN_TYPES,
    )
    analytics_store.insert_typed_rows(
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
    analytics_store.ensure_typed_table(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    analytics_store.ensure_typed_table(
        connection,
        analytics_schema.JOB_TABLE,
        analytics_schema.JOB_COLUMN_TYPES,
    )
    analytics_store.insert_typed_rows(
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
    analytics_store.insert_typed_rows(
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
    total_phase = result["phase_health_by_stage"][0]
    assert total_phase["is_total"] == 1
    assert total_phase["source_run_count"] == 2
    assert total_phase["transform_failed"] == 1
    assert total_phase["overhead_duration_seconds"] == 40
    assert total_phase["overhead_unknown"] == 0
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


def test_failure_category_sql_and_python_share_one_rule_set() -> None:
    messages = [
        "ModuleNotFoundError: no module named demo",
        "connection refused",
        "401 unauthorized",
        "table does not exist",
        "column type mismatch",
        "validation constraint",
        "source_id;destination_id",
        "",
        "unclassified failure",
    ]
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TABLE messages(message VARCHAR)")
    connection.executemany("INSERT INTO messages VALUES (?)", [(message,) for message in messages])

    sql_categories = [
        row[0]
        for row in connection.execute(
            f"SELECT {failure_category_sql('message')} FROM messages"
        ).fetchall()
    ]

    assert sql_categories == [categorize_failure(message) for message in messages]


def test_failure_classifier_corpus_has_sql_python_parity() -> None:
    corpus = [
        ("ModuleNotFoundError: No module named connectorx", "Dependency"),
        ("required package pyarrow missing; pip install pyarrow", "Dependency"),
        ("invalid configuration option batch_size", "Configuration"),
        ("missing required parameter warehouse", "Configuration"),
        ("connection refused by sql.internal", "Connectivity"),
        ("DNS resolution failure for api.internal", "Connectivity"),
        ("request timed out after 30 seconds", "Timeout / throttling"),
        ("HTTP 429 too many requests", "Timeout / throttling"),
        ("HTTP 401 unauthorized", "Authentication"),
        ("authentication failed: token expired", "Authentication"),
        ("HTTP 403 forbidden", "Authorization"),
        ("permission denied for table orders", "Authorization"),
        ("table orders does not exist", "Missing object"),
        ("no such column customer_id", "Missing object"),
        ("cannot cast VARCHAR to INTEGER", "Schema / format"),
        ("malformed json payload", "Schema / format"),
        ("data quality check failed for order_id", "Data quality"),
        ("duplicate key violates uniqueness constraint", "Data quality"),
        ("OutOfMemoryError while joining", "Resource / capacity"),
        ("no space left on device", "Resource / capacity"),
        ("deadlock detected", "Concurrency / conflict"),
        ("DELTA_CONCURRENT_WRITE conflict", "Concurrency / conflict"),
        ("SyntaxError near FROM", "Runtime / code"),
        ("task execution failed", "Runtime / code"),
        ("unclassified provider failure", "Other"),
        ("source_id;destination_id", "Other"),
        ("", "Unspecified"),
        ("none", "Unspecified"),
    ]
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TABLE messages(message VARCHAR)")
    connection.executemany("INSERT INTO messages VALUES (?)", [(message,) for message, _ in corpus])
    category_sql = failure_category_sql("message")
    rule_sql = failure_rule_id_sql("message")
    tags_sql = failure_tags_sql("message", "category")
    sql_results = connection.execute(
        f"""
        WITH classified AS (
          SELECT message, {category_sql} AS category, {rule_sql} AS rule_id
          FROM messages
        )
        SELECT category, rule_id, {tags_sql} AS tags FROM classified
        """
    ).fetchall()
    python_results = [classify_failure(message) for message, _ in corpus]

    assert [result.category for result in python_results] == [expected for _, expected in corpus]
    assert sql_results == [
        (result.category, result.rule_id, list(result.tags))
        for result in python_results
    ]


@pytest.mark.parametrize(
    ("row", "expected_phases", "expected_primary"),
    [
        (
            {"status": "failed", "source_status": "failed", "source_error_message": "source failed"},
            ("source",),
            ("source", "source failed"),
        ),
        (
            {
                "status": "failed",
                "source_status": "failed",
                "transform_status": "failed",
                "source_error_message": "source failed first",
                "transform_error_message": "transform failed later",
            },
            ("source", "transform"),
            ("source", "source failed first"),
        ),
        (
            {
                "status": "failed",
                "source_status": "failed",
                "transform_status": "failed",
                "transform_error_message": "later phase message",
                "error_message": "root failure",
            },
            ("source", "transform"),
            ("source", "root failure"),
        ),
        (
            {"status": "failed", "error_message": "scheduler failed"},
            ("overhead",),
            ("overhead", "scheduler failed"),
        ),
        (
            {
                "status": "failed",
                "transform_status": "failed",
                "source_error_message": "stale source warning",
                "error_message": "root failure",
            },
            ("transform",),
            ("transform", "root failure"),
        ),
        (
            {"status": "pending", "source_status": "failed", "source_error_message": "not a run"},
            (),
            ("unknown", ""),
        ),
        (
            {"status": "running", "destination_status": "failed"},
            (),
            ("unknown", ""),
        ),
    ],
)
def test_failure_phase_source_of_truth_has_python_sql_parity(
    row: dict[str, object],
    expected_phases: tuple[str, ...],
    expected_primary: tuple[str, str],
) -> None:
    complete = {
        "normalized_status": row.get("status"),
        "source_status": None,
        "transform_status": None,
        "destination_status": None,
        "source_error_message": None,
        "transform_error_message": None,
        "destination_error_message": None,
        "error_message": None,
        **row,
    }
    assert dataflow_failed_phases(complete) == expected_phases
    assert dataflow_failure_phase_and_message(complete) == expected_primary

    columns = ", ".join(f"? AS {key}" for key in complete)
    values = list(complete.values())
    failed_sql = [dataflow_phase_failed_sql("r", phase) for phase in ("source", "transform", "destination", "overhead")]
    sql_result = duckdb.connect(":memory:").execute(
        f"""
        SELECT {failure_phase_sql("r")}, {failure_message_sql("r")},
               {", ".join(failed_sql)}
        FROM (SELECT {columns}) r
        """,
        values,
    ).fetchone()
    assert sql_result[:2] == expected_primary
    assert tuple(
        phase
        for phase, is_failed in zip(("source", "transform", "destination", "overhead"), sql_result[2:])
        if is_failed
    ) == expected_phases


def test_failure_classifier_keeps_secondary_matches_as_tags_only() -> None:
    result = classify_failure(
        "OAuth2 token request failed: connection refused",
        all_evidence="OAuth2 token request failed: connection refused",
    )

    assert result.category == "Connectivity"
    assert result.rule_id == "connectivity.connection_refused"
    assert result.tags == ("Authentication", "Connection refused", "OAuth")


@pytest.mark.parametrize(
    "message",
    [
        "dependency graph rendered successfully",
        "configuration loaded successfully",
        "connection pool size is 10",
        "timeout_seconds is configured to 30",
        "token count metric completed",
        "permission matrix cached",
        "table list loaded",
        "schema registry refreshed",
        "constraint metadata loaded",
        "memory usage is 20 percent",
        "lock owner recorded",
        "execution plan generated",
    ],
)
def test_failure_classifier_does_not_promote_safe_context_terms(message: str) -> None:
    assert classify_failure(message).category == "Other"


def test_failures_read_model_classifies_and_bounds_failure_evidence() -> None:
    connection = _dataflow_metric_connection()

    result = failures_read_model(
        [],
        {},
        timezone_name="UTC",
        now=datetime(2026, 7, 21, tzinfo=timezone.utc),
        analytics_context=(connection, [1], "generation-1"),
    )

    assert result["summary"]["failed_jobs"] == 1
    assert result["summary"]["failed_dataflows"] == 1
    assert result["summary"]["top_cause_category"] == "Schema / format"
    assert result["summary"]["top_cause_phase"] == "transform"
    assert result["error_categories"] == [{"category": "Schema / format", "count": 1}]
    assert result["failed_by_stage"][0]["transform"] == 1
    assert result["failed_records"][0]["failure_rule_id"] == "schema.type_mismatch"
    assert result["failed_records"][0]["failure_tags"] == ["Data type"]
    assert sum(row["count"] for row in result["error_categories"]) == result["summary"]["failed_dataflows"]
    assert len(result["failed_records"]) <= 100


def test_failure_classifier_prefers_failed_phase_and_keeps_residual_message_as_tag() -> None:
    connection = _dataflow_metric_connection()
    connection.execute(
        """
        UPDATE monitoring_dataflow_facts
        SET source_error_message = 'connection refused'
        WHERE dataflow_run_id = 'run-2'
        """
    )

    result = failures_read_model(
        [], {}, timezone_name="UTC",
        analytics_context=(connection, [1], "generation-1"),
    )
    failed = result["failed_records"][0]

    assert failed["failure_phase"] == "transform"
    assert failed["failure_category"] == "Schema / format"
    assert failed["failure_tags"] == ["Connection refused", "Connectivity", "Data type"]


def test_overview_and_failures_share_primary_category_counts() -> None:
    connection = _dataflow_metric_connection()
    failures = failures_read_model(
        [], {}, timezone_name="UTC",
        analytics_context=(connection, [1], "generation-1"),
    )
    overview = overview_read_model(
        [], {}, grain="day", timezone_name="UTC",
        analytics_context=(connection, [1], "generation-1"),
    )

    assert overview["error_categories"] == failures["error_categories"]
    assert sum(row["count"] for row in failures["error_categories"]) == failures["summary"]["failed_dataflows"]


def test_overview_read_model_returns_only_bounded_visual_metrics() -> None:
    connection = _dataflow_metric_connection()

    result = overview_read_model(
        [],
        {},
        grain="day",
        timezone_name="UTC",
        now=datetime(2026, 7, 21, tzinfo=timezone.utc),
        analytics_context=(connection, [1], "generation-1"),
    )

    assert result["summary"]["job_records"] == 1
    assert result["summary"]["dataflow_records"] == 2
    assert result["summary"]["job_failed"] == 1
    assert result["summary"]["dataflow_failed"] == 1
    assert result["runtime_contexts"][0]["engine_name"] == "duckdb"
    assert result["dataflow_operation_health"][0]["operation_type"] == "etl"
    assert result["phase_health"][0]["is_total"] == 1
    assert result["error_categories"] == [{"category": "Schema / format", "count": 1}]
    assert sum(row["est_rows_written"] for row in result["rows_by_date"]) == 100
    assert result["rows_by_date"][0]["bucket"] == "2026-07-20"
    assert result["bytes_by_date"][0]["bucket"] == "2026-07-20"
    assert result["health"]["mismatch_count"] == 0


def test_volume_read_model_keeps_estimated_write_metric_consistent() -> None:
    connection = _dataflow_metric_connection()
    result = volume_read_model(
        [], {}, grain="day", timezone_name="UTC",
        analytics_context=(connection, [1], "generation-1"),
    )
    evidence = volume_evidence_read_model(
        [], {}, limit=100, offset=0,
        sort_by="volume_candidate_priority", sort_dir="desc",
        analytics_context=(connection, [1], "generation-1"),
    )

    assert result["summary"]["total_rows_read"] == 150
    assert result["summary"]["total_est_rows_written"] == 100
    assert sum(row["est_rows_written"] for row in result["rows_by_date"]) == 100
    assert result["rows_by_date"][0]["bucket"] == "2026-07-20"
    assert result["volume_by_workload_type"][0]["workload_type"] == "etl · unknown"
    assert result["route_volume"][0]["runs"] == 2
    assert result["top_dataflows_by_rows_read"][0]["value"] == 150
    assert evidence["records"][0]["volume_rows_read"] == 150
    assert evidence["total_records"] == 1
    assert "dataflow_registry" not in result


def test_freshness_read_model_aggregates_at_dataflow_grain() -> None:
    connection = _dataflow_metric_connection(include_asset_metadata=True)
    result = freshness_read_model(
        [], {}, grain="day", timezone_name="UTC",
        analytics_context=(connection, [1], "generation-1"),
    )
    evidence = freshness_evidence_read_model(
        [], {}, limit=100, offset=0,
        sort_by="latest_freshness_at", sort_dir="desc",
        analytics_context=(connection, [1], "generation-1"),
    )

    assert result["summary"]["observed_dataflows"] == 1
    assert result["summary"]["successful_runs"] == 1
    assert result["summary"]["failed_runs"] == 1
    assert result["summary"]["watermark_enabled_dataflows"] == 0
    assert result["age_by_dataflow"][0]["dataflow_name"] == "orders"
    assert result["age_distribution"][0]["dataflows"] == 1
    assert result["watermark_coverage_by_stage"][0]["observed_dataflows"] == 1
    row = evidence["records"][0]
    assert row["dataflow_name"] == "orders"
    assert row["latest_run_status"] == "failed"
    assert row["latest_run_at"] is not None
    assert [item["status"] for item in row["last_statuses"]] == ["failed", "succeeded"]
    assert row["succeeded_count"] == 1
    assert row["failed_count"] == 1
    assert row["source_table"] == "raw.orders"
    assert row["source_full_table"] == "catalog.raw.orders"
    assert row["destination_table"] == "curated.orders"
    assert row["destination_path"] == "/lakehouse/curated/orders"
    assert row["dataflow_description"] == "Load orders"
    assert row["transform_select_columns"] == '["id", "email"]'
    assert row["transform_rename_columns"] == '{"email": "contact_email"}'
    assert row["transform_configure"] == '{"missing_column_policy": "ignore"}'


def test_freshness_read_model_tracks_the_actual_consecutive_skipped_streak() -> None:
    connection = _dataflow_metric_connection()
    skipped_rows = []
    for index in range(4):
        skipped_rows.append((
            f"skipped-{index}.parquet",
            "dataflow_parquet",
            "{}",
            {
                "job_id": "job-1",
                "dataflow_id": "dataflow-1",
                "dataflow_run_id": f"skipped-{index}",
                "dataflow_name": "orders",
                "stage": "silver",
                "operation_type": "etl",
                "source_name": "source",
                "destination_name": "destination",
                "status": "skipped",
                "start_time": f"2026-07-21T0{index}:00:00Z",
                "end_time": f"2026-07-21T0{index}:01:00Z",
            },
        ))
    analytics_store.insert_typed_rows(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        1,
        skipped_rows,
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    _rebuild_serving_facts(connection)

    result = freshness_read_model(
        [], {}, grain="day", timezone_name="UTC",
        analytics_context=(connection, [1], "generation-1"),
    )
    evidence = freshness_evidence_read_model(
        [], {}, limit=100, offset=0,
        sort_by="latest_freshness_at", sort_dir="desc",
        analytics_context=(connection, [1], "generation-1"),
    )

    assert result["summary"]["skipped_streak_dataflows"] == 1
    assert result["skipped_streak_distribution"] == [{"bucket": "4–7", "dataflows": 1}]
    assert evidence["records"][0]["skipped_streak"] == 4


def test_performance_read_model_uses_executable_run_population() -> None:
    connection = _dataflow_metric_connection()
    result = performance_read_model(
        [], {}, grain="day", timezone_name="UTC",
        analytics_context=(connection, [1], "generation-1"),
    )
    evidence = performance_evidence_read_model(
        [], {}, limit=100, offset=0,
        sort_by="duration_seconds", sort_dir="desc",
        analytics_context=(connection, [1], "generation-1"),
    )

    assert result["summary"]["run_count"] == 2
    assert result["summary"]["p50_duration_seconds"] == 60
    assert result["summary"]["bottleneck_phase"] in {"source", "transform", "destination", "overhead"}
    assert result["duration_distribution_by_stage"][0]["count"] == 2
    assert result["slowest_dataflow_profiles"][0]["p95_duration_seconds"] == 120
    assert result["summary"]["slowest_run_dataflow_run_id"] == "run-2"
    assert result["summary"]["slowest_run_operation_type"] == "etl"
    assert result["summary"]["slowest_run_start_time"] == "2026-07-20T01:00:00+00:00"
    assert result["summary"]["slowest_run_end_time"] == "2026-07-20T01:02:00+00:00"
    assert result["runtime_context_profiles"][0]["run_count"] == 2
    assert result["runtime_context_profiles"][0]["engine_name"] == "duckdb"
    assert result["performance_trend"][0]["run_count"] == 2
    assert evidence["records"][0]["performance_bottleneck_phase"] in {"source", "transform", "destination", "overhead"}
    assert "destination_rows_inserted" in evidence["records"][0]
    assert "destination_bytes_saved" in evidence["records"][0]
    assert "slowest_dataflows" not in result
    assert "investigation_queue" not in result
    assert "duration_by_stage" not in result
    assert "slowest_dataflows_by_p95" not in result


def test_maintenance_read_model_returns_empty_evidence_without_maintenance_runs() -> None:
    result = maintenance_read_model(
        [], {}, grain="day", timezone_name="UTC",
        analytics_context=(_dataflow_metric_connection(), [1], "generation-1"),
    )

    assert result["summary"]["total_maintenance_runs"] == 0
    assert result["summary"]["coverage_missing_tables"] == 0
    assert result["table_outcome"] == []


def test_maintenance_read_model_returns_wide_trends_and_registry_drawer_evidence() -> None:
    connection = _dataflow_metric_connection(include_asset_metadata=True)
    analytics_store.insert_typed_rows(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        1,
        [(
            "maintenance.parquet",
            "dataflow_parquet",
            "{}",
            {
                "job_id": "job-2",
                "dataflow_id": "maintenance-flow",
                "dataflow_run_id": "maintenance-run-1",
                "dataflow_name": "compact_orders",
                "stage": "silver",
                "operation_type": "maintenance",
                "status": "succeeded",
                "start_time": "2026-07-21T00:00:00Z",
                "end_time": "2026-07-21T00:02:00Z",
                "duration_seconds": 120,
                "destination_name": "destination",
                "destination_connection_type": "lakehouse",
                "destination_format": "delta",
                "destination_table": "curated.orders",
                "destination_bytes_removed": 4096,
                "destination_bytes_saved": 1024,
                "destination_files_removed": 2,
            },
        )],
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    _rebuild_serving_facts(connection)

    result = maintenance_read_model(
        [], {}, grain="day", timezone_name="UTC",
        analytics_context=(connection, [1], "generation-1"),
    )
    evidence = maintenance_evidence_read_model(
        [], {}, limit=100, offset=0,
        sort_by="attention_priority", sort_dir="desc",
        analytics_context=(connection, [1], "generation-1"),
    )

    assert result["status_by_date"] == [{
        "date": "2026-07-21T00:00:00+00:00",
        "bucket": "2026-07-21T00:00:00+00:00",
        "bucket_start": "2026-07-21T00:00:00+00:00",
        "bucket_end": None,
        "grain": "day",
        "succeeded": 1,
        "failed": 0,
        "skipped": 0,
        "running": 0,
        "pending": 0,
        "unknown": 0,
        "total": 1,
        "success_rate": 100.0,
    }]
    assert result["reclaim_by_date"][0]["bytes_reclaimed"] == 4096
    assert result["reclaim_by_date"][0]["files_removed"] == 2
    registry = evidence["records"][0]
    assert registry["destination_table"] == "curated.orders"
    assert registry["latest_status"] == "succeeded"
    assert registry["bytes_reclaimed"] == 4096
    assert registry["upstream_run_count"] == 1
    assert registry["upstream_dataflows"][0]["dataflow_name"] == "orders"
    assert registry["upstream_dataflows"][0]["run_count"] == 1


def test_all_monitoring_pages_bypass_legacy_fact_materialization() -> None:
    connection = _dataflow_metric_connection()
    paths = [EnvironmentSource(
        id=1, environment_id=1, source_kind="logs", uri="test://monitoring/1", enabled=True,
    )]

    assert not hasattr(query_service, "_monitoring_rows")
    for page in page_service.MONITORING_PAGES:
        result = page_service._build_monitoring_page(
            paths,
            page,
            filters={"range": "all"},
            session=object(),
            timezone_info=timezone.utc,
            timezone_label="UTC",
            timezone_source="configured",
            analytics_context=(connection, [1], "generation-1"),
        )
        assert result["summary"]["dataflow_records"] == 2


def _dataflow_metric_connection(*, include_asset_metadata: bool = False):
    connection = duckdb.connect(":memory:")
    analytics_store.ensure_tables(connection)
    analytics_store.ensure_typed_table(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    analytics_store.ensure_typed_table(
        connection,
        analytics_schema.JOB_TABLE,
        analytics_schema.JOB_COLUMN_TYPES,
    )
    analytics_store.insert_typed_rows(
        connection,
        analytics_schema.JOB_TABLE,
        1,
        [
            (
                "job.jsonl",
                "job_jsonl",
                "{}",
                {
                    "job_id": "job-1",
                    "status": "failed",
                    "end_time": "2026-07-20T01:00:00Z",
                    "duration_seconds": 180,
                    "engine_name": "duckdb",
                    "metadata_provider_name": "file",
                    "platform_name": "local",
                    "operation_types": '["etl", "maintenance"]',
                    "total_dataflows": 2,
                    "total_succeeded": 1,
                    "total_failed": 1,
                    "total_skipped": 0,
                },
            )
        ],
        analytics_schema.JOB_COLUMN_TYPES,
    )
    common = {
        "job_id": "job-1",
        "dataflow_id": "dataflow-1",
        "dataflow_name": "orders",
        "stage": "silver",
        "operation_type": "etl",
        "source_name": "source",
        "destination_name": "destination",
    }
    if include_asset_metadata:
        common.update({
            "dataflow_description": "Load orders",
            "source_format": "delta",
            "source_table": "raw.orders",
            "source_full_table": "catalog.raw.orders",
            "destination_format": "delta",
            "destination_table": "curated.orders",
            "destination_path": "/lakehouse/curated/orders",
            "transform_select_columns": '["id", "email"]',
            "transform_drop_columns": "[]",
            "transform_rename_columns": '{"email": "contact_email"}',
            "transform_value_rules": '[{"operation": "trim", "columns": ["email"]}]',
            "transform_hash_columns": '[{"target_column": "email_hash", "columns": ["email"]}]',
            "transform_masking_rules": '[{"method": "redact", "columns": ["email"]}]',
            "transform_configure": '{"missing_column_policy": "ignore"}',
        })
    analytics_store.insert_typed_rows(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        1,
        [
            (
                "success.parquet",
                "dataflow_parquet",
                "{}",
                {
                    **common,
                    "dataflow_run_id": "run-1",
                    "status": "succeeded",
                    "start_time": "2026-07-20T00:00:00Z",
                    "end_time": "2026-07-20T00:01:00Z",
                    "duration_seconds": 60,
                    "source_status": "succeeded",
                    "source_duration_seconds": 10,
                    "transform_status": "succeeded",
                    "transform_duration_seconds": 20,
                    "destination_status": "succeeded",
                    "destination_duration_seconds": 20,
                    "source_rows_read": 100,
                    "destination_rows_written": 90,
                },
            ),
            (
                "failed.parquet",
                "dataflow_parquet",
                "{}",
                {
                    **common,
                    "dataflow_run_id": "run-2",
                    "status": "failed",
                    "start_time": "2026-07-20T01:00:00Z",
                    "end_time": "2026-07-20T01:02:00Z",
                    "duration_seconds": 120,
                    "source_status": "succeeded",
                    "source_duration_seconds": 20,
                    "transform_status": "failed",
                    "transform_duration_seconds": 30,
                    "transform_error_message": "column type mismatch",
                    "destination_status": "pending",
                    "destination_duration_seconds": 40,
                    "source_rows_read": 50,
                    "destination_rows_written": 0,
                },
            ),
        ],
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    _rebuild_serving_facts(connection)
    return connection


def _rebuild_serving_facts(connection) -> None:
    rebuild_monitoring_serving_facts(
        connection,
        dataflow_table=analytics_schema.DATAFLOW_TABLE,
        job_table=analytics_schema.JOB_TABLE,
        dataflow_column_types=analytics_schema.cache_table_column_types(analytics_schema.DATAFLOW_COLUMN_TYPES),
        job_column_types=analytics_schema.cache_table_column_types(analytics_schema.JOB_COLUMN_TYPES),
    )
