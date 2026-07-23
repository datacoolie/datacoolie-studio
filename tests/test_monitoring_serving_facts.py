from pathlib import Path

import duckdb

from benchmarks.monitoring_fixture import build_analytics_fixture
from datacoolie_studio.domains.logs import cache as logs_cache
from datacoolie_studio.domains.monitoring.serving_facts import (
    MONITORING_DATAFLOW_FACTS_TABLE,
    MONITORING_JOB_FACTS_TABLE,
    rebuild_monitoring_serving_facts,
    validate_monitoring_serving_facts,
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
            dataflow_table=logs_cache.DATAFLOW_TABLE,
            job_table=logs_cache.JOB_TABLE,
        )


def test_serving_fact_rebuild_is_idempotent(tmp_path: Path):
    analytics_path = tmp_path / "analytics.duckdb"
    build_analytics_fixture(analytics_path, source_ids=[7], dataflow_rows=40)

    with duckdb.connect(str(analytics_path)) as connection:
        before = _serving_digest(connection)
        rebuild_monitoring_serving_facts(
            connection,
            dataflow_table=logs_cache.DATAFLOW_TABLE,
            job_table=logs_cache.JOB_TABLE,
            dataflow_column_types=logs_cache._cache_table_column_types(logs_cache.DATAFLOW_COLUMN_TYPES),
            job_column_types=logs_cache._cache_table_column_types(logs_cache.JOB_COLUMN_TYPES),
        )
        validate_monitoring_serving_facts(
            connection,
            dataflow_table=logs_cache.DATAFLOW_TABLE,
            job_table=logs_cache.JOB_TABLE,
        )
        assert _serving_digest(connection) == before


def test_serving_validation_rejects_row_count_drift(tmp_path: Path):
    analytics_path = tmp_path / "analytics.duckdb"
    build_analytics_fixture(analytics_path, source_ids=[7], dataflow_rows=10)

    with duckdb.connect(str(analytics_path)) as connection:
        connection.execute(f"DELETE FROM {MONITORING_DATAFLOW_FACTS_TABLE} WHERE dataflow_run_id = 'run-0'")
        try:
            validate_monitoring_serving_facts(
                connection,
                dataflow_table=logs_cache.DATAFLOW_TABLE,
                job_table=logs_cache.JOB_TABLE,
            )
        except RuntimeError as exc:
            assert "row counts" in str(exc)
        else:
            raise AssertionError("Expected serving-fact reconciliation to reject row-count drift")


def test_failed_serving_validation_rolls_back_generation(tmp_path: Path, monkeypatch):
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setattr(logs_cache, "analytics_database_path", lambda: analytics_path)
    first = logs_cache._upsert_duckdb_rows(
        7,
        [],
        [("job-1.jsonl", "job_jsonl", "{}", {"job_id": "job-1", "status": "succeeded"})],
        [],
        ["job-1.jsonl"],
    )
    assert first["published"] is True
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        before = connection.execute(
            f"""
            SELECT
              (SELECT generation FROM {logs_cache.ANALYTICS_META_TABLE} WHERE singleton_id = 1),
              (SELECT COUNT(*) FROM {logs_cache.JOB_TABLE}),
              (SELECT COUNT(*) FROM {MONITORING_JOB_FACTS_TABLE})
            """
        ).fetchone()

    def reject_serving_facts(*_args, **_kwargs):
        raise RuntimeError("serving validation failed")

    monkeypatch.setattr(logs_cache, "validate_monitoring_serving_facts", reject_serving_facts)
    failed = logs_cache._upsert_duckdb_rows(
        7,
        [],
        [("job-2.jsonl", "job_jsonl", "{}", {"job_id": "job-2", "status": "failed"})],
        [],
        ["job-2.jsonl"],
    )
    assert failed["published"] is False
    assert "serving validation failed" in failed["errors"][0]["message"]
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        after = connection.execute(
            f"""
            SELECT
              (SELECT generation FROM {logs_cache.ANALYTICS_META_TABLE} WHERE singleton_id = 1),
              (SELECT COUNT(*) FROM {logs_cache.JOB_TABLE}),
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
