from __future__ import annotations

import argparse
import os
import tracemalloc

import pytest

from datacoolie_studio.core import config
from datacoolie_studio.db import session as session_module
from datacoolie_studio.domains.analytics_upgrade import service as upgrade_service


pytestmark = pytest.mark.benchmark


@pytest.fixture
def benchmark_module(script_module_loader):
    return script_module_loader(
        "scripts/benchmarks/analytics_upgrade.py",
        "analytics_upgrade_benchmark",
    )


def _arguments() -> argparse.Namespace:
    return argparse.Namespace(sources=1, files_per_stream=1, rows_per_file=1)


def test_benchmark_failure_restores_environment_and_removes_workspace(
    monkeypatch,
    tmp_path,
    benchmark_module,
):
    target = tmp_path / "benchmark-workspace"
    disposed = []

    def make_workspace(**_kwargs):
        target.mkdir()
        return str(target)

    monkeypatch.setattr(session_module, "_dispose_engine", lambda: disposed.append(True))
    monkeypatch.setattr(benchmark_module.tempfile, "mkdtemp", make_workspace)
    monkeypatch.setattr(
        benchmark_module,
        "_create_workspace",
        lambda _root, _arguments: (_ for _ in ()).throw(RuntimeError("injected failure")),
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", "caller-data")
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", "sqlite:///caller.db")

    with pytest.raises(RuntimeError, match="injected failure"):
        benchmark_module._run(_arguments())

    assert not target.exists()
    assert disposed == [True]
    assert os.environ["DATACOOLIE_STUDIO_DATA_DIR"] == "caller-data"
    assert os.environ["DATACOOLIE_STUDIO_DATABASE_URL"] == "sqlite:///caller.db"


@pytest.mark.benchmark
def test_benchmark_success_restores_process_state_and_removes_workspace(
    monkeypatch,
    tmp_path,
    benchmark_module,
):
    target = tmp_path / "benchmark-workspace"
    def make_workspace(**_kwargs):
        target.mkdir()
        return str(target)

    monkeypatch.setattr(benchmark_module.tempfile, "mkdtemp", make_workspace)
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", "caller-data")
    monkeypatch.setattr(benchmark_module, "_create_workspace", lambda _root, _arguments: None)
    monkeypatch.setattr(
        upgrade_service,
        "run_analytics_upgrade_once",
        lambda **_kwargs: {"state": "succeeded"},
    )
    monkeypatch.setattr(benchmark_module, "_job_receipt", lambda: ({"rows": 1}, {"total": 1.0}))
    monkeypatch.setattr(
        config,
        "analytics_database_path",
        lambda: target / "cache" / "analytics.duckdb",
    )
    tracing_before = tracemalloc.is_tracing()

    receipt = benchmark_module._run(_arguments())

    assert receipt["result_state"] == "succeeded"
    assert not target.exists()
    assert os.environ["DATACOOLIE_STUDIO_DATA_DIR"] == "caller-data"
    assert tracemalloc.is_tracing() is tracing_before
