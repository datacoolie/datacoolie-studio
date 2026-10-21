from __future__ import annotations

import duckdb
import pytest
from datetime import datetime, timedelta, timezone

from datacoolie_studio.domains.monitoring.read_models.failures import (
    _latest_failure_activity,
    failures_read_model,
)
from datacoolie_studio.domains.monitoring.read_models.overview import overview_read_model
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

from tests.domains.monitoring.monitoring_read_model_support import (
    _dataflow_metric_connection,
)


def test_latest_failure_activity_keeps_recovered_attempt_after_60_newer_runs() -> None:
    recovered_time = datetime(2026, 7, 21, tzinfo=timezone.utc)
    failure_offsets = (*range(67, 0, -1), *range(-1, -11, -1))
    failed_records = [
        {
            "dataflow_run_id": f"failed-{offset}",
            "failure_time": (recovered_time + timedelta(minutes=offset)).isoformat(),
        }
        for offset in failure_offsets
    ]
    recovered_records = [{
        "dataflow_run_id": "recovered-run",
        "activity_time": recovered_time.isoformat(),
        "attempt_number": 1,
    }]

    latest = _latest_failure_activity(failed_records, recovered_records)

    assert len(latest) == 78
    assert latest[67]["activity_type"] == "recovered_attempt"
    assert latest[67]["dataflow_run_id"] == "recovered-run"

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
            {"status": "failed"},
            ("unknown",),
            ("unknown", ""),
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
        "log_schema_version": 6,
        "raw_record_json": '{"log_schema_version":6,"attempts":[]}',
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
    ) == tuple(
        phase for phase in expected_phases
        if phase in {"source", "transform", "destination", "overhead"}
    )

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
    from datacoolie_studio.domains.analytics import schema
    from datacoolie_studio.domains.analytics.record_codec import reconstruct_record
    from tests.support.v6_records import insert_native_rows
    cursor = connection.execute(f"SELECT * FROM {schema.DATAFLOW_TABLE} WHERE dataflow_run_id = 'run-2'")
    physical = dict(zip([column[0] for column in cursor.description], cursor.fetchone()))
    record = reconstruct_record(physical, 'dataflow_run_log')
    record['attempts'][-1]['source']['message'] = 'connection refused'
    connection.execute(f"DELETE FROM {schema.DATAFLOW_TABLE} WHERE dataflow_run_id = 'run-2'")
    insert_native_rows(connection, schema.DATAFLOW_TABLE, 1, [(physical['_file_uri'], 'dataflow', '{}', record)], schema.DATAFLOW_COLUMN_TYPES)

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
