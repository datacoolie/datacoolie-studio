from __future__ import annotations

import json

import duckdb
import pytest

from datacoolie_studio.db.models import EnvironmentSource, LogFileManifest
from datacoolie_studio.db.session import create_session
from datacoolie_studio.domains.analytics_upgrade import service as upgrade
from datacoolie_studio.domains.analytics_upgrade.recovery import (
    source_replay_has_warning,
    source_replay_succeeded,
)
from tests.domains.analytics_upgrade.analytics_upgrade_test_support import _workspace_with_sources, _write_old_live_cache
from tests.support.v6_records import native_record


@pytest.mark.parametrize("invalid_json", [False, True])
def test_invalid_v6_blocks_complete_upgrade_without_parking_source(tmp_path, monkeypatch, invalid_json):
    session, sources = _workspace_with_sources(tmp_path, monkeypatch, count=2)
    source_ids = [source.id for source in sources]
    session.close()
    path = next((tmp_path / "logs-2" / "execution_logs" / "dataflow_run_log").rglob("*.json"))
    record = native_record({"_type": "dataflow_run_log"})
    record.pop("metrics")
    path.write_text('{"broken":' if invalid_json else json.dumps(record), encoding="utf-8")
    live = tmp_path / "analytics.duckdb"
    _write_old_live_cache(live, schema_version=14)
    original = live.read_bytes()
    monkeypatch.setattr(upgrade, "analytics_database_path", lambda: live)
    result = upgrade.run_analytics_upgrade_once()
    assert result["state"] == "failed"
    assert result["error_code"] == ("invalid_json" if invalid_json else "schema_incompatible")
    assert live.read_bytes() == original
    with create_session() as session:
        assert all(session.get(EnvironmentSource, source_id).enabled for source_id in source_ids)
    with duckdb.connect(str(live), read_only=True) as conn:
        assert conn.execute("SELECT schema_version FROM etl_analytics_meta").fetchone() == (14,)


def test_old_only_replay_publishes_deferred_control_with_warning(tmp_path, monkeypatch):
    session, sources = _workspace_with_sources(tmp_path, monkeypatch, count=1)
    source = sources[0]
    for path in (tmp_path / "logs-1").rglob("*.json"):
        expected_type = (
            "dataflow_run_log"
            if "dataflow_run_log" in path.parts
            else "job_run_log"
        )
        path.write_text(
            json.dumps({"log_schema_version": 5, "_type": expected_type}) + "\n",
            encoding="utf-8",
        )
    session.close()
    live = tmp_path / "analytics.duckdb"
    _write_old_live_cache(live, schema_version=14)
    monkeypatch.setattr(upgrade, "analytics_database_path", lambda: live)

    result = upgrade.run_analytics_upgrade_once()

    assert result["state"] == "completed_with_warnings"
    assert result["source_ids"] == [source.id]
    assert result["completed_source_ids"] == [source.id]
    with duckdb.connect(str(live), read_only=True) as connection:
        assert connection.execute(
            "SELECT source_id FROM etl_cache_sources"
        ).fetchall() == [(source.id,)]
        assert connection.execute("SELECT count(*) FROM etl_job_runs").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM etl_dataflow_runs").fetchone() == (0,)
    with create_session() as verify:
        persisted_source = verify.get(EnvironmentSource, source.id)
        assert persisted_source is not None and persisted_source.enabled
        manifests = verify.query(LogFileManifest).filter_by(source_id=source.id).all()
        assert len(manifests) == 2
        summaries = [json.loads(row.admission_summary_json) for row in manifests]
        assert all(summary["admitted"] == 0 for summary in summaries)
        assert all(summary["excluded"] == 1 for summary in summaries)


def test_warning_replay_requires_a_successful_nested_execution():
    control = {
        "changed_files": [
            {"admission_summary": {"admitted": 0, "excluded": 1}}
        ]
    }
    warning = {
        "status": "unknown",
        "sync_execution": {
            "state": "succeeded",
            "result": {
                "status": "ok",
                "deferred_control": control,
            },
        },
    }
    failed = {
        "status": "error",
        "sync_execution": {
            "state": "succeeded",
            "result": {
                "status": "error",
                "deferred_control": control,
            },
        },
    }

    assert source_replay_succeeded(warning)
    assert source_replay_has_warning(warning)
    assert not source_replay_succeeded(failed)
