from tests.support.v6_records import native_record
import json
from pathlib import Path

import duckdb

from tests.support.monitoring_fixture import build_analytics_fixture
from datacoolie_studio.domains.analytics import publication as analytics_publication
from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.analytics import store as analytics_store
from datacoolie_studio.domains.analytics.contracts import LogFileDescriptor
from datacoolie_studio.domains.analytics.serving_facts import (
    MONITORING_DATAFLOW_FACTS_TABLE,
    MONITORING_JOB_FACTS_TABLE,
    rebuild_monitoring_serving_facts,
    monitoring_serving_schema_is_ready,
    validate_monitoring_serving_facts,
)
from datacoolie_studio.domains.logs import ingestion as log_ingestion


def test_serving_readiness_rejects_view_and_wrong_scalar_type(tmp_path):
    path = tmp_path / "analytics.duckdb"
    build_analytics_fixture(path, source_ids=[7], dataflow_rows=5)
    with duckdb.connect(str(path)) as connection:
        assert monitoring_serving_schema_is_ready(connection)
        connection.execute("CREATE TABLE copied_facts AS SELECT * FROM monitoring_dataflow_facts")
        connection.execute("DROP TABLE monitoring_dataflow_facts")
        connection.execute("CREATE VIEW monitoring_dataflow_facts AS SELECT * FROM copied_facts")
        assert not monitoring_serving_schema_is_ready(connection)
        connection.execute("DROP VIEW monitoring_dataflow_facts")
        connection.execute("CREATE TABLE monitoring_dataflow_facts AS SELECT * FROM copied_facts")
        assert monitoring_serving_schema_is_ready(connection)
        connection.execute("ALTER TABLE monitoring_dataflow_facts ALTER source_rows_read TYPE DOUBLE")
        assert not monitoring_serving_schema_is_ready(connection)
        for table in (MONITORING_DATAFLOW_FACTS_TABLE, MONITORING_JOB_FACTS_TABLE):
            columns = {row[0] for row in connection.execute(f"DESCRIBE {table}").fetchall()}
            assert not columns & {"raw_record_json", "metrics", "attempts", "read_request", "diagnostic_issues", "_present_paths"}


def test_typed_base_readiness_rejects_view_replacing_physical_table():
    from datacoolie_studio.domains.analytics import schema_tables

    with duckdb.connect(":memory:") as connection:
        schema_tables.ensure_tables(connection)
        assert analytics_schema.typed_table_schema_is_current(
            connection, analytics_schema.DATAFLOW_TABLE, analytics_schema.DATAFLOW_COLUMN_TYPES,
        )
        connection.execute("CREATE TABLE copied_rows AS SELECT * FROM etl_dataflow_runs")
        connection.execute("DROP TABLE etl_dataflow_runs")
        connection.execute("CREATE VIEW etl_dataflow_runs AS SELECT * FROM copied_rows")
        assert not analytics_schema.typed_table_schema_is_current(
            connection, analytics_schema.DATAFLOW_TABLE, analytics_schema.DATAFLOW_COLUMN_TYPES,
        )


def test_serving_facts_reconcile_context_and_derived_columns(tmp_path: Path):
    analytics_path = tmp_path / "analytics.duckdb"
    build_analytics_fixture(analytics_path, source_ids=[7, 8], dataflow_rows=250)

    with duckdb.connect(str(analytics_path)) as connection:
        dataflow = connection.execute(
            f"""
            SELECT normalized_status, event_time, run_date, engine_name, metadata_provider_name
            FROM {MONITORING_DATAFLOW_FACTS_TABLE}
            WHERE engine_name <> 'unknown'
            ORDER BY _source_id, run_date, event_time, dataflow_run_id
            LIMIT 1
            """
        ).fetchone()
        assert dataflow[0] in {"failed", "skipped", "succeeded"}
        assert dataflow[1] is not None
        assert dataflow[2] is not None
        assert dataflow[3] in {"DuckDBEngine", "SparkEngine"}
        assert dataflow[4] == "FileProvider"
        validate_monitoring_serving_facts(
            connection,
            dataflow_table=analytics_schema.DATAFLOW_TABLE,
            job_table=analytics_schema.JOB_TABLE,
        )


def test_serving_facts_preserve_dataflow_job_sequence_fields(tmp_path: Path):
    analytics_path = tmp_path / "analytics.duckdb"
    build_analytics_fixture(analytics_path, source_ids=[7], dataflow_rows=10)

    with duckdb.connect(str(analytics_path)) as connection:
        rows = connection.execute(
            f"""
            SELECT job_num, job_index
            FROM {MONITORING_DATAFLOW_FACTS_TABLE}
            ORDER BY job_index
            LIMIT 3
            """
        ).fetchall()

    assert rows == [(0, 0), (0, 1), (0, 2)]


def test_serving_facts_preserve_run_attributes_and_log_health(tmp_path: Path):
    analytics_path = tmp_path / "analytics.duckdb"
    build_analytics_fixture(analytics_path, source_ids=[7], dataflow_rows=40)

    with duckdb.connect(str(analytics_path)) as connection:
        connection.execute(
            f"""
            UPDATE {analytics_schema.JOB_TABLE}
            SET run_attributes = ?,
                log_records_dropped = 2,
                log_bytes_dropped = 128,
                message_truncated = TRUE
            WHERE job_id = 'job-0'
            """,
            ['{"factory_run_id":"run-0"}'],
        )
        rebuild_monitoring_serving_facts(
            connection,
            dataflow_table=analytics_schema.DATAFLOW_TABLE,
            job_table=analytics_schema.JOB_TABLE,
            dataflow_column_types=analytics_schema.cache_table_column_types(analytics_schema.DATAFLOW_COLUMN_TYPES),
            job_column_types=analytics_schema.cache_table_column_types(analytics_schema.JOB_COLUMN_TYPES),
        )
        row = connection.execute(
            f"""
            SELECT run_attributes, log_records_dropped, log_bytes_dropped,
                   error_message_truncated
            FROM {MONITORING_JOB_FACTS_TABLE}
            WHERE job_id = 'job-0'
            """
        ).fetchone()

    assert row == ('{"factory_run_id":"run-0"}', 2, 128, True)


def test_serving_fact_rebuild_is_idempotent(tmp_path: Path):
    analytics_path = tmp_path / "analytics.duckdb"
    build_analytics_fixture(analytics_path, source_ids=[7], dataflow_rows=40)

    with duckdb.connect(str(analytics_path)) as connection:
        before = _serving_digest(connection)
        rebuild_monitoring_serving_facts(
            connection,
            dataflow_table=analytics_schema.DATAFLOW_TABLE,
            job_table=analytics_schema.JOB_TABLE,
            dataflow_column_types=analytics_schema.cache_table_column_types(analytics_schema.DATAFLOW_COLUMN_TYPES),
            job_column_types=analytics_schema.cache_table_column_types(analytics_schema.JOB_COLUMN_TYPES),
        )
        validate_monitoring_serving_facts(
            connection,
            dataflow_table=analytics_schema.DATAFLOW_TABLE,
            job_table=analytics_schema.JOB_TABLE,
        )
        assert _serving_digest(connection) == before


def test_serving_facts_keep_transform_configure_values_grouped(
    tmp_path: Path,
    monkeypatch,
):
    analytics_path = tmp_path / "analytics.duckdb"
    json_path = tmp_path / "dataflow.json"
    direct_transform_values = {
        "transform_select_columns": '["customer_id", "email"]',
        "transform_drop_columns": None,
        "transform_rename_columns": '{"email": "contact_email"}',
        "transform_value_rules": '[{"operation": "trim", "columns": ["email"]}]',
        "transform_hash_columns": '[{"target_column": "email_hash", "columns": ["email"]}]',
        "transform_masking_rules": '[{"method": "redact", "columns": ["email"], "value": "[PRIVATE]"}]',
        "transform_configure": '{"missing_column_policy": "ignore"}',
    }
    source_transform_values = {
        **direct_transform_values,
    }
    json_path.write_text(
        json.dumps(
            native_record({
                "_type": "dataflow_run_log",
                "log_schema_version": 3,
                "job_id": "job-1",
                "dataflow_id": "customers",
                "dataflow_run_id": "run-1",
                "status": "succeeded",
                "start_time": "2026-08-02T10:00:00Z",
                "end_time": "2026-08-02T10:01:00Z",
                **source_transform_values,
            })
        )
        + "\n",
        encoding="utf-8",
    )

    published = analytics_store.publish_rows(
        7,
        [
            LogFileDescriptor(
                canonical_uri=str(json_path),
                file_kind="dataflow_json",
                revision_json="{}",
                read_uri=str(json_path),
            )
        ],
        [
            (
                "job-1.json",
                "job_json",
                "{}",
                native_record({
                    "_type": "job_run_log",
                    "log_schema_version": 3,
                    "job_id": "job-1",
                    "status": "succeeded",
                }),
            )
        ],
        [],
        [str(json_path), "job-1.json"],
        database_path=analytics_path,
    )

    assert published["published"] is True
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        cached = connection.execute(
            f"""
            SELECT {', '.join(direct_transform_values)}
            FROM {analytics_schema.DATAFLOW_TABLE}
            WHERE dataflow_run_id = 'run-1'
            """
        ).fetchone()
        projected = connection.execute(
            f"""
            SELECT {', '.join(direct_transform_values)}
            FROM {MONITORING_DATAFLOW_FACTS_TABLE}
            WHERE dataflow_run_id = 'run-1'
            """
        ).fetchone()
        serving_columns = {
            row[0]
            for row in connection.execute(
                f"DESCRIBE {MONITORING_DATAFLOW_FACTS_TABLE}"
            ).fetchall()
        }
        cache_columns = {
            row[0]
            for row in connection.execute(
                f"DESCRIBE {analytics_schema.DATAFLOW_TABLE}"
            ).fetchall()
        }
    assert cached == tuple(direct_transform_values.values())
    assert projected == tuple(direct_transform_values.values())
    assert "transform_missing_column_policy" not in cache_columns
    assert "transform_missing_column_policy" not in serving_columns

    from datacoolie_studio.db.models import EnvironmentSource
    from datacoolie_studio.domains.analytics import access as analytics_access
    from datacoolie_studio.domains.monitoring.log_repository import (
        query_cached_dataflow_logs,
    )

    monkeypatch.setattr(
        analytics_access,
        "analytics_database_path",
        lambda: analytics_path,
    )
    source = EnvironmentSource(
        id=7,
        environment_id=1,
        source_kind="logs",
        uri=str(tmp_path),
        enabled=True,
    )
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        from datacoolie_studio.domains.analytics.access import materialization_token_from_connection
        expected_generation = materialization_token_from_connection(connection, [7])
        records, total, errors, generation = query_cached_dataflow_logs(
            None, [source], {}, limit=1,
            analytics_context=(connection, [7], expected_generation),
        )

    assert total == 1
    assert errors == []
    assert generation == expected_generation
    assert records[0]["transform_configure"] == direct_transform_values["transform_configure"]
    assert "transform_missing_column_policy" not in records[0]


def test_serving_validation_rejects_row_count_drift(tmp_path: Path):
    analytics_path = tmp_path / "analytics.duckdb"
    build_analytics_fixture(analytics_path, source_ids=[7], dataflow_rows=10)

    with duckdb.connect(str(analytics_path)) as connection:
        connection.execute("DELETE FROM monitoring_dataflow_facts WHERE dataflow_run_id = 'run-0'")
        try:
            validate_monitoring_serving_facts(
                connection,
                dataflow_table=analytics_schema.DATAFLOW_TABLE,
                job_table=analytics_schema.JOB_TABLE,
            )
        except RuntimeError as exc:
            assert "identity counts" in str(exc)
        else:
            raise AssertionError("Expected serving-fact reconciliation to reject row-count drift")


def test_failed_serving_validation_rolls_back_generation(tmp_path: Path, monkeypatch):
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    first = analytics_store.publish_rows(
        7,
        [],
        [
            (
                "job-1.json",
                "job_json",
                "{}",
                native_record({
                    "_type": "job_run_log",
                    "log_schema_version": 3,
                    "job_id": "job-1",
                    "status": "succeeded",
                }),
            )
        ],
        [],
        ["job-1.json"],
        database_path=analytics_path,
    )
    assert first["published"] is True
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        before = connection.execute(
            f"""
            SELECT
              (SELECT generation FROM {analytics_schema.ANALYTICS_META_TABLE} WHERE singleton_id = 1),
              (SELECT COUNT(*) FROM {analytics_schema.JOB_TABLE}),
              (SELECT COUNT(*) FROM {MONITORING_JOB_FACTS_TABLE})
            """
        ).fetchone()

    def reject_serving_facts(*_args, **_kwargs):
        raise RuntimeError("serving validation failed")

    monkeypatch.setattr(
        analytics_publication,
        "validate_monitoring_serving_facts",
        reject_serving_facts,
    )
    failed = analytics_store.publish_rows(
        7,
        [],
        [
            (
                "job-2.json",
                "job_json",
                "{}",
                native_record({
                    "_type": "job_run_log",
                    "log_schema_version": 3,
                    "job_id": "job-2",
                    "status": "failed",
                }),
            )
        ],
        [],
        ["job-2.json"],
        database_path=analytics_path,
    )
    assert failed["published"] is False
    assert "serving validation failed" in failed["errors"][0]["message"]
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        after = connection.execute(
            f"""
            SELECT
              (SELECT generation FROM {analytics_schema.ANALYTICS_META_TABLE} WHERE singleton_id = 1),
              (SELECT COUNT(*) FROM {analytics_schema.JOB_TABLE}),
              (SELECT COUNT(*) FROM {MONITORING_JOB_FACTS_TABLE})
            """
        ).fetchone()
    assert after == before


def _serving_digest(connection) -> tuple[object, ...]:
    return connection.execute(
        f"""
        SELECT
          (SELECT COUNT(*) FROM {MONITORING_DATAFLOW_FACTS_TABLE}),
          (SELECT COUNT(*) FROM {MONITORING_JOB_FACTS_TABLE}),
          (SELECT SUM(HASH(
             COALESCE(dataflow_run_id, ''),
             COALESCE(normalized_status, ''),
             COALESCE(engine_name, ''),
             COALESCE(CAST(event_time AS VARCHAR), '')
           )) FROM {MONITORING_DATAFLOW_FACTS_TABLE}),
          (SELECT SUM(HASH(
             COALESCE(job_id, ''),
             COALESCE(normalized_status, ''),
             COALESCE(engine_name, ''),
             COALESCE(CAST(event_time AS VARCHAR), '')
           )) FROM {MONITORING_JOB_FACTS_TABLE})
        """
    ).fetchone()
