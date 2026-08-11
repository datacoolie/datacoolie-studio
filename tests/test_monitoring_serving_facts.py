from pathlib import Path

import duckdb

from benchmarks.monitoring_fixture import build_analytics_fixture
from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.analytics import store as analytics_store
from datacoolie_studio.domains.analytics.serving_facts import (
    MONITORING_DATAFLOW_FACTS_TABLE,
    MONITORING_JOB_FACTS_TABLE,
    rebuild_monitoring_serving_facts,
    validate_monitoring_serving_facts,
)
from datacoolie_studio.domains.logs import ingestion as log_ingestion


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
    parquet_path = tmp_path / "dataflow.parquet"
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
        "transform_missing_column_policy": "ignore",
    }
    with duckdb.connect() as connection:
        connection.execute(
            """
            CREATE TABLE dataflow_source (
              job_id VARCHAR,
              dataflow_id VARCHAR,
              dataflow_run_id VARCHAR,
              status VARCHAR,
              start_time TIMESTAMPTZ,
              end_time TIMESTAMPTZ,
              transform_select_columns VARCHAR,
              transform_drop_columns VARCHAR,
              transform_rename_columns VARCHAR,
              transform_value_rules VARCHAR,
              transform_hash_columns VARCHAR,
              transform_masking_rules VARCHAR,
              transform_configure VARCHAR,
              transform_missing_column_policy VARCHAR
            )
            """
        )
        connection.execute(
            "INSERT INTO dataflow_source VALUES (?, ?, ?, ?, ?::TIMESTAMPTZ, ?::TIMESTAMPTZ, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "job-1",
                "customers",
                "run-1",
                "succeeded",
                "2026-08-02T10:00:00Z",
                "2026-08-02T10:01:00Z",
                *source_transform_values.values(),
            ],
        )
        escaped_path = str(parquet_path).replace("'", "''")
        connection.execute(
            f"COPY dataflow_source TO '{escaped_path}' (FORMAT PARQUET)"
        )

    published = analytics_store.publish_rows(
        7,
        [(str(parquet_path), "dataflow_parquet", "{}")],
        [
            (
                "job-1.jsonl",
                "job_jsonl",
                "{}",
                {"job_id": "job-1", "status": "succeeded"},
            )
        ],
        [],
        [str(parquet_path), "job-1.jsonl"],
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
    records, total, errors = query_cached_dataflow_logs(
        None,
        [source],
        {},
        limit=1,
    )

    assert total == 1
    assert errors == []
    assert records[0]["transform_configure"] == direct_transform_values["transform_configure"]
    assert "transform_missing_column_policy" not in records[0]


def test_serving_validation_rejects_row_count_drift(tmp_path: Path):
    analytics_path = tmp_path / "analytics.duckdb"
    build_analytics_fixture(analytics_path, source_ids=[7], dataflow_rows=10)

    with duckdb.connect(str(analytics_path)) as connection:
        connection.execute(f"DELETE FROM {MONITORING_DATAFLOW_FACTS_TABLE} WHERE dataflow_run_id = 'run-0'")
        try:
            validate_monitoring_serving_facts(
                connection,
                dataflow_table=analytics_schema.DATAFLOW_TABLE,
                job_table=analytics_schema.JOB_TABLE,
            )
        except RuntimeError as exc:
            assert "row counts" in str(exc)
        else:
            raise AssertionError("Expected serving-fact reconciliation to reject row-count drift")


def test_failed_serving_validation_rolls_back_generation(tmp_path: Path, monkeypatch):
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    first = analytics_store.publish_rows(
        7,
        [],
        [("job-1.jsonl", "job_jsonl", "{}", {"job_id": "job-1", "status": "succeeded"})],
        [],
        ["job-1.jsonl"],
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
        analytics_store,
        "validate_monitoring_serving_facts",
        reject_serving_facts,
    )
    failed = analytics_store.publish_rows(
        7,
        [],
        [("job-2.jsonl", "job_jsonl", "{}", {"job_id": "job-2", "status": "failed"})],
        [],
        ["job-2.jsonl"],
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
