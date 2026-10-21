from __future__ import annotations

import json
import os

import pytest

from datacoolie_studio.db import models, session as session_module
from datacoolie_studio.domains.logs import ingestion


pytestmark = pytest.mark.benchmark


@pytest.fixture
def benchmark_module(script_module_loader):
    return script_module_loader(
        "scripts/benchmarks/databricks_log_sync.py",
        "databricks_log_sync_benchmark",
    )


def test_write_acknowledgement_is_required_before_session_creation(
    monkeypatch,
    tmp_path,
    benchmark_module,
):
    monkeypatch.setattr(
        session_module,
        "create_session",
        lambda: pytest.fail("session must not be created without --allow-write"),
    )

    with pytest.raises(SystemExit):
        benchmark_module.main(
            ["--source-id", "42", "--data-dir", str(tmp_path)]
        )


def test_accepted_run_uses_explicit_target_and_restores_environment(
    monkeypatch,
    tmp_path,
    capsys,
    benchmark_module,
):
    calls: list[object] = []

    class FakeSource:
        id = 42
        source_kind = "logs"
        storage_provider = "dbfs"

    class FakeSession:
        def get(self, model, source_id):
            calls.append(("get", model, source_id))
            return FakeSource()

        def close(self):
            calls.append("close")

    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", "caller-data")
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", "sqlite:///caller.db")
    monkeypatch.setattr(session_module, "init_db", lambda: calls.append("init"))
    monkeypatch.setattr(session_module, "create_session", lambda: FakeSession())
    monkeypatch.setattr(session_module, "_dispose_engine", lambda: calls.append("dispose"))
    monkeypatch.setattr(models, "EnvironmentSource", object)
    monkeypatch.setattr(
        ingestion,
        "refresh_log_source_cache",
        lambda session, source, *, job_type: {
            "status": "succeeded",
            "latest_job": {
                "id": 7,
                "result": {"timings_ms": {"total": 1.0}},
            },
        },
    )

    result = benchmark_module.main(
        [
            "--source-id",
            "42",
            "--runs",
            "2",
            "--data-dir",
            str(tmp_path),
            "--allow-write",
        ]
    )

    assert result == 0
    captured = capsys.readouterr().out
    receipt = json.loads(captured)
    assert receipt["source_id"] == 42
    assert receipt["target_name"] == tmp_path.name
    assert str(tmp_path) not in captured
    assert "init" in calls
    assert "dispose" in calls
    assert os.environ["DATACOOLIE_STUDIO_DATA_DIR"] == "caller-data"
    assert os.environ["DATACOOLIE_STUDIO_DATABASE_URL"] == "sqlite:///caller.db"


def test_failure_does_not_echo_target_or_provider_details(
    monkeypatch,
    tmp_path,
    capsys,
    benchmark_module,
):
    class FakeSource:
        id = 42
        source_kind = "logs"
        storage_provider = "dbfs"

    class FakeSession:
        def get(self, _model, _source_id):
            return FakeSource()

        def close(self):
            pass

    monkeypatch.setattr(session_module, "init_db", lambda: None)
    monkeypatch.setattr(session_module, "create_session", lambda: FakeSession())
    monkeypatch.setattr(session_module, "_dispose_engine", lambda: None)
    monkeypatch.setattr(models, "EnvironmentSource", object)
    monkeypatch.setattr(
        ingestion,
        "refresh_log_source_cache",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(f"failed for {tmp_path}; token=secret-value")
        ),
    )

    result = benchmark_module.main(
        [
            "--source-id",
            "42",
            "--data-dir",
            str(tmp_path),
            "--allow-write",
        ]
    )

    assert result == 1
    error = capsys.readouterr().err
    assert error == "error: RuntimeError\n"
    assert str(tmp_path) not in error
    assert "secret-value" not in error
