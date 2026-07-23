from pathlib import Path

import duckdb

from benchmarks.monitoring_fixture import build_analytics_fixture
from benchmarks.monitoring_performance import _nearest_rank, _summarize, benchmark_call
from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.logs import cache as logs_cache
from datacoolie_studio.domains.analytics.serving_facts import (
    MONITORING_DATAFLOW_FACTS_TABLE,
    MONITORING_JOB_FACTS_TABLE,
    monitoring_serving_schema_is_ready,
)


def test_monitoring_fixture_is_published_and_deterministic(tmp_path: Path, monkeypatch):
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setattr(logs_cache, "analytics_database_path", lambda: analytics_path)

    counts = build_analytics_fixture(analytics_path, source_ids=[7, 8], dataflow_rows=250)

    assert counts == {"sources": 2, "dataflow_rows": 250, "job_rows": 50}
    sources = [
        EnvironmentSource(
            id=source_id,
            environment_id=1,
            source_kind="logs",
            uri=f"benchmark://fixture/{source_id}",
            enabled=True,
        )
        for source_id in (7, 8)
    ]
    assert logs_cache.analytics_materialization_token(sources).startswith("analytics-v")
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM etl_dataflow_runs").fetchone()[0] == 250
        assert connection.execute("SELECT COUNT(*) FROM etl_job_runs").fetchone()[0] == 50
        assert connection.execute("SELECT COUNT(*) FROM etl_job_runs WHERE __event_time IS NULL").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM etl_job_runs WHERE __run_date IS NULL").fetchone()[0] == 0
        assert monitoring_serving_schema_is_ready(connection)
        assert connection.execute(
            f"SELECT COUNT(*) FROM {MONITORING_DATAFLOW_FACTS_TABLE}"
        ).fetchone()[0] == 250
        assert connection.execute(
            f"SELECT COUNT(*) FROM {MONITORING_JOB_FACTS_TABLE}"
        ).fetchone()[0] == 50
        assert connection.execute(
            f"SELECT COUNT(*) FROM {logs_cache.FILTER_VALUES_TABLE}"
        ).fetchone()[0] > 0


def test_benchmark_summary_uses_nearest_rank_percentile():
    samples = [
        {"duration_ms": value, "payload_bytes": 100 + value, "duckdb_connections": 1}
        for value in range(1, 21)
    ]

    assert _nearest_rank([float(value) for value in range(1, 21)], 0.95) == 19
    assert _summarize(samples)["duration_ms"] == {
        "median": 10.5,
        "p95": 19.0,
        "min": 1.0,
        "max": 20.0,
    }


def test_benchmark_call_measures_http_style_response_content():
    class Response:
        content = b'{"page":"overview"}'

    result = benchmark_call(lambda: Response(), samples=2, warmups=0)

    assert result["summary"]["payload_bytes"]["min"] == len(Response.content)
