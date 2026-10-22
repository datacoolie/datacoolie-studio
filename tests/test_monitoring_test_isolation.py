from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests import conftest as workspace_fixtures
from datacoolie_studio.db import session
from datacoolie_studio.domains.read_models import database


ROOT = Path(__file__).resolve().parents[1]
MONITORING = "tests/domains/monitoring/"
PURE = (
    "test_monitoring_duration_distribution.py::test_duration_distribution_returns_empty_for_no_eligible_runs",
    "test_log_v6_status_message.py::test_v6_explicit_null_root_message_does_not_resurrect_phase_text",
    "test_monitoring_read_models_common.py::test_filtered_ctes_reject_unknown_projection_columns",
    "test_log_v6_status_message.py::test_v6_omitted_root_message_uses_current_failed_phase_evidence",
)
WORKSPACE = (
    "test_canonical_message_records.py::test_canonical_messages_reach_record_queries_with_null_precedence",
    "test_v7_observed_metrics.py::test_throughput_duration_uses_measured_population_and_requires_measurements",
    "test_evidence_hydration.py::test_warm_sqlite_cache_hydrates_native_typed_evidence_without_raw_storage",
)


@pytest.mark.parametrize("nodes", [
    PURE,
    WORKSPACE,
    (PURE[0], WORKSPACE[0], PURE[1], WORKSPACE[1], PURE[2], WORKSPACE[2], PURE[3]),
], ids=["pure", "workspace", "mixed-order"])
def test_monitoring_cases_keep_workspace_access_and_teardown_isolated(tmp_path, nodes):
    sentinel = tmp_path / "untouched-default-workspace"
    environment = {
        **os.environ,
        "DATACOOLIE_STUDIO_DATA_DIR": str(sentinel),
        "DATACOOLIE_STUDIO_DATABASE_URL": f"sqlite:///{sentinel.as_posix()}/studio.db",
        "DATACOOLIE_STUDIO_RESULT_CACHE_URL": f"sqlite:///{sentinel.as_posix()}/cache.db",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    environment.pop("DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_TEMP_DIRECTORY", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "tests.support.monitoring_isolation_probe",
         *[MONITORING + node for node in nodes]],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not sentinel.exists()


def test_workspace_cleanup_restores_environment_when_analytics_cleanup_fails(tmp_path, monkeypatch):
    previous = {key: os.environ.get(key) for key in workspace_fixtures._ENVIRONMENT_KEYS}
    workspace = workspace_fixtures.studio_paths.__wrapped__(tmp_path)
    paths = next(workspace)
    try:
        session.init_db()
        database.get_result_cache_engine()
        assert paths.database_path.exists()

        def fail_cleanup():
            raise RuntimeError("injected analytics cleanup failure")

        monkeypatch.setattr(workspace_fixtures.analytics_maintenance, "clear_cache", fail_cleanup)
        with pytest.raises(RuntimeError, match="injected analytics cleanup failure"):
            next(workspace)
    finally:
        workspace.close()
    assert {key: os.environ.get(key) for key in previous} == previous
    assert session._engine is None
    assert database._engine is None
