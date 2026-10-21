from __future__ import annotations

import argparse
import json
import os

import pytest


pytestmark = pytest.mark.benchmark


@pytest.fixture
def benchmark_module(script_module_loader):
    return script_module_loader(
        "scripts/benchmarks/root_attributes.py",
        "root_attributes_benchmark",
    )


def test_benchmark_requires_modest_profile_and_repeated_samples(benchmark_module):
    arguments = benchmark_module._arguments([])

    assert arguments.rows >= 250
    assert arguments.repeats >= 3
    assert arguments.baseline_serving_source is None

    with pytest.raises(SystemExit, match="--rows must be at least 250"):
        benchmark_module._run(benchmark_module._arguments(["--rows", "249"]))
    with pytest.raises(SystemExit, match="--repeats must be at least 3"):
        benchmark_module._run(
            benchmark_module._arguments(["--rows", "250", "--repeats", "2"])
        )


def test_fixed_clock_actual_suite_uses_one_timestamp(tmp_path, monkeypatch, benchmark_module):
    import duckdb

    database = tmp_path / "read.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE etl_analytics_meta "
            "(singleton_id INTEGER, generation VARCHAR)"
        )
        connection.execute("INSERT INTO etl_analytics_meta VALUES (1, 'generation-1')")

    seen_timestamps = []

    def fake_actual_read_model(connection, name, _record_id):
        current = connection.execute("SELECT current_timestamp").fetchone()[0]
        seen_timestamps.append(current)
        if name in {"latest_page", "record"}:
            return {"records": [], "total_records": 0}
        return {"name": name, "current_timestamp": current}

    monkeypatch.setattr(benchmark_module, "_actual_read_model", fake_actual_read_model)
    results = benchmark_module._actual_read_model_suite(
        database,
        temp_directory=tmp_path / "tmp",
        record_id="run-1",
        repeats=3,
    )

    assert set(results) == set(benchmark_module.ACTUAL_READ_MODEL_NAMES)
    top_level_keys = {
        tuple(result["result"]["top_level_keys"])
        for name, result in results.items()
        if name not in {"latest_page", "record"}
    }
    assert top_level_keys == {("current_timestamp", "name")}
    assert seen_timestamps
    assert set(seen_timestamps) == {benchmark_module.BENCHMARK_NOW}
    assert all(result["cold_ms"] >= 0 for result in results.values())
    assert all(len(result["samples_ms"]) == 3 for result in results.values())


def test_schema16_source_replaces_only_derived_relations(tmp_path, monkeypatch, benchmark_module):
    import duckdb

    baseline_source = tmp_path / "serving_facts.py"
    baseline_source.write_text(
        """
def rebuild_monitoring_serving_facts(
    conn, *, dataflow_table, job_table, dataflow_column_types, job_column_types
):
    del dataflow_column_types, job_column_types
    conn.execute(
        f\"CREATE VIEW monitoring_job_facts AS SELECT * FROM {job_table}\"
    )
    conn.execute(
        f\"CREATE VIEW monitoring_dataflow_facts AS SELECT * FROM {dataflow_table}\"
    )
""",
        encoding="utf-8",
    )

    def fake_typed_layout(path, *, temp_directory, dataflow_path, job_path):
        del temp_directory, dataflow_path, job_path
        with duckdb.connect(str(path)) as connection:
            connection.execute(
                "CREATE TABLE etl_dataflow_runs (dataflow_run_id VARCHAR)"
            )
            connection.execute("INSERT INTO etl_dataflow_runs VALUES ('run-1')")
            connection.execute("CREATE TABLE etl_job_runs (job_id VARCHAR)")
            connection.execute("INSERT INTO etl_job_runs VALUES ('job-1')")
            connection.execute(
                "CREATE TABLE monitoring_dataflow_facts (dataflow_run_id VARCHAR)"
            )
            connection.execute(
                "CREATE TABLE monitoring_job_facts (job_id VARCHAR)"
            )
            connection.execute(
                "CREATE TABLE etl_analytics_meta "
                "(singleton_id INTEGER, schema_version INTEGER, "
                "build_state VARCHAR, generation VARCHAR)"
            )
            connection.execute(
                "INSERT INTO etl_analytics_meta VALUES (1, 17, 'ready', 'generation-1')"
            )
        return {"result": {"published": True, "publication_duration_ms": 1.0}}

    source_database = tmp_path / "source.duckdb"
    fake_typed_layout(
        source_database,
        temp_directory=tmp_path / "tmp",
        dataflow_path=tmp_path / "dataflow.jsonl",
        job_path=tmp_path / "job.jsonl",
    )
    monkeypatch.setattr(benchmark_module, "_typed_layout", fake_typed_layout)
    result = benchmark_module._schema16_view_layout(
        tmp_path / "baseline.duckdb",
        temp_directory=tmp_path / "tmp",
        dataflow_path=tmp_path / "dataflow.jsonl",
        job_path=tmp_path / "job.jsonl",
        baseline_serving_source=baseline_source,
        source_database=source_database,
    )

    assert result["serving_source"] == str(baseline_source)
    assert result["source_clone_ms"] >= 0
    assert result["view_rebuild_ms"] >= 0
    inventory = benchmark_module._inventory(
        tmp_path / "baseline.duckdb", tmp_path / "tmp"
    )
    assert inventory["etl_dataflow_runs"] == "BASE TABLE"
    assert inventory["monitoring_dataflow_facts"] == "VIEW"
    assert inventory["monitoring_job_facts"] == "VIEW"
    with duckdb.connect(str(tmp_path / "baseline.duckdb"), read_only=True) as connection:
        assert connection.execute(
            "SELECT schema_version FROM etl_analytics_meta WHERE singleton_id = 1"
        ).fetchone() == (16,)


def test_actual_result_receipt_keeps_hydration_bounded(benchmark_module):
    page = {"records": [{"raw_record_json": "{}"}] * 25, "total_records": 30}
    receipt = benchmark_module._actual_result_receipt("latest_page", page)

    assert receipt["returned_records"] == 25
    assert receipt["hydrated_records"] == 25
    assert receipt["hydration_bound"] is True

    over_bound = benchmark_module._actual_result_receipt(
        "latest_page", {"records": [{}] * 26, "total_records": 26}
    )
    assert over_bound["hydration_bound"] is False


def test_benchmark_failure_restores_environment(monkeypatch, benchmark_module):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", "caller-data")
    monkeypatch.setenv("DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_MEMORY_LIMIT", "1GB")

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected benchmark failure")

    monkeypatch.setattr(benchmark_module, "_make_dataflow_records", fail)
    arguments = argparse.Namespace(
        rows=250,
        jobs=42,
        repeats=3,
        baseline_serving_source=None,
        receipt=None,
    )

    with pytest.raises(RuntimeError, match="injected benchmark failure"):
        benchmark_module._run(arguments)

    assert os.environ["DATACOOLIE_STUDIO_DATA_DIR"] == "caller-data"
    assert os.environ["DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_MEMORY_LIMIT"] == "1GB"
    assert "DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_THREADS" not in os.environ


def test_benchmark_cli_writes_optional_receipt(monkeypatch, tmp_path, capsys, benchmark_module):
    expected = {
        "benchmark": "test",
        "parity": {"all_queries": True, "mismatches": []},
    }
    monkeypatch.setattr(benchmark_module, "_run", lambda _arguments: expected)
    receipt_path = tmp_path / "receipt.json"

    assert benchmark_module.main(["--rows", "250", "--receipt", str(receipt_path)]) == 0
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == expected
    assert json.loads(capsys.readouterr().out) == expected
