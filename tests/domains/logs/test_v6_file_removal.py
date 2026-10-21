from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

from tests.domains.logs.test_log_ingestion import (
    _create_workspace,
    _raw_ids,
    _test_client,
)
from tests.support.v6_records import native_record


def _write_job_file(path: Path, job_ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(
                native_record(
                    {
                        "_type": "job_run_log",
                        "job_id": job_id,
                        "status": "succeeded",
                        "start_time": f"{index + 1:04d}-01-01T00:00:00+00:00",
                        "end_time": f"{index + 1:04d}-01-01T00:01:00+00:00",
                        "duration_seconds": 60,
                    }
                )
            )
            + "\n"
            for index, job_id in enumerate(job_ids)
        ),
        encoding="utf-8",
    )


def _write_system_file(path: Path, *, job_id: str, session_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            native_record(
                {
                    "_type": "system_log",
                    "job_id": job_id,
                    "log_session_id": session_id,
                    "ts": "2026-10-06T00:00:00+00:00",
                    "level": "INFO",
                    "logger": "removal.test",
                    "msg": "system summary",
                }
            )
        )
        + "\n",
        encoding="utf-8",
    )


def _refresh(client, environment_id: int, source_id: int) -> dict:
    response = client.post(
        f"/api/v1/environments/{environment_id}/log-sources/{source_id}/refresh",
        json={"mode": "incremental"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _workspace_rows(database_path: Path, sql: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(database_path) as connection:
        return connection.execute(sql).fetchall()


def _canonical(path: Path) -> str:
    return path.as_posix()


def test_incremental_removal_is_bounded_to_scanned_partitions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "logs"
    current = datetime.now(timezone.utc).date()
    historical = current - timedelta(days=10)
    current_file = (
        root / "execution_logs" / "job_run_log" / current.strftime("%Y%m%d") / "current.json"
    )
    historical_file = (
        root
        / "execution_logs"
        / "job_run_log"
        / historical.strftime("%Y%m%d")
        / "historical.json"
    )
    _write_job_file(current_file, ["job-current"])
    _write_job_file(historical_file, ["job-historical"])

    client, analytics_path = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, root)
        current_file.unlink()
        historical_file.unlink()

        result = _refresh(client, environment_id, source_id)
        counts = result["sync_execution"]["result"]["record_counts"]

        assert counts["removed_files"] == 1
        assert _raw_ids(analytics_path, "etl_job_runs", "job_id") == [
            "job-historical"
        ]
        assert _workspace_rows(
            tmp_path / "studio.db",
            "SELECT file_uri FROM log_file_manifest ORDER BY file_uri",
        ) == [(_canonical(historical_file),)]


def test_removed_system_file_deletes_monitoring_summary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "logs"
    current = datetime.now(timezone.utc).date()
    system_file = (
        root / "system_logs" / current.strftime("%Y%m%d")
        / f"system_{current.strftime('%Y%m%d')}_000000_1_0_job-system.json"
    )
    _write_system_file(
        system_file,
        job_id="job-system",
        session_id="session-system",
    )

    client, _analytics_path = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, root)
        endpoint = f"/api/v1/environments/{environment_id}/monitoring/system-logs"
        before = client.get(
            endpoint,
            params={
                "job_id": "job-system",
                "source_id": source_id,
                "log_session_id": "session-system",
                "log_session_scope": "exact",
            },
        )
        assert before.status_code == 200, before.text
        assert before.json()["total"] == 1
        assert len(before.json()["files"]) == 1

        system_file.unlink()
        result = _refresh(client, environment_id, source_id)
        assert result["sync_execution"]["result"]["record_counts"][
            "removed_files"
        ] == 1
        assert _workspace_rows(
            tmp_path / "studio.db",
            "SELECT count(*) FROM log_file_manifest WHERE file_kind = 'system_json'",
        ) == [(0,)]

        after = client.get(
            endpoint,
            params={
                "job_id": "job-system",
                "source_id": source_id,
                "log_session_id": "session-system",
                "log_session_scope": "exact",
            },
        )
        assert after.status_code == 200, after.text
        assert after.json()["total"] == 0
        assert after.json()["records"] == []
        assert after.json()["files"] == []


def test_deferred_upgrade_applies_removed_file_control_after_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "logs"
    current = datetime.now(timezone.utc).date().strftime("%Y%m%d")
    job_file = root / "execution_logs" / "job_run_log" / current / "jobs.json"
    dataflow_file = (
        root / "execution_logs" / "dataflow_run_log" / current / "dataflows.json"
    )
    _write_job_file(job_file, ["job-retained"])
    dataflow_file.parent.mkdir(parents=True, exist_ok=True)
    dataflow_file.write_text(
        json.dumps(
            native_record(
                {
                    "_type": "dataflow_run_log",
                    "dataflow_run_id": "run-removed",
                    "job_id": "job-retained",
                    "status": "succeeded",
                    "start_time": "2026-10-06T00:00:00+00:00",
                    "end_time": "2026-10-06T00:01:00+00:00",
                    "duration_seconds": 60.0,
                }
            )
        )
        + "\n",
        encoding="utf-8",
    )

    client, analytics_path = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, root)
        assert _raw_ids(
            analytics_path, "etl_dataflow_runs", "dataflow_run_id"
        ) == ["run-removed"]

        with duckdb.connect(str(analytics_path)) as connection:
            connection.execute(
                "UPDATE etl_analytics_meta SET schema_version = 7 "
                "WHERE singleton_id = 1"
            )
        dataflow_file.unlink()

        from datacoolie_studio.domains.analytics_upgrade import service as upgrade

        monkeypatch.setattr(upgrade, "analytics_database_path", lambda: analytics_path)
        real_apply = upgrade.apply_deferred_log_control
        captured_controls: list[dict] = []

        def capture_control(session, source, control, *, checked_at):
            captured_controls.append(control)
            assert analytics_path.exists()
            assert not analytics_path.with_name(
                "analytics.candidate.duckdb"
            ).exists()
            return real_apply(session, source, control, checked_at=checked_at)

        monkeypatch.setattr(upgrade, "apply_deferred_log_control", capture_control)
        result = upgrade.run_analytics_upgrade_once(now=datetime.now(timezone.utc))

        assert result["state"] == "succeeded"
        assert len(captured_controls) == 1
        assert _canonical(dataflow_file) in captured_controls[0]["removed_files"]
        assert _raw_ids(analytics_path, "etl_dataflow_runs", "dataflow_run_id") == []
        assert _raw_ids(analytics_path, "etl_job_runs", "job_id") == [
            "job-retained"
        ]
        assert _workspace_rows(
            tmp_path / "studio.db",
            "SELECT file_uri FROM log_file_manifest ORDER BY file_uri",
        ) == [(_canonical(job_file),)]


def test_invalid_changed_file_does_not_publish_removal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "logs"
    current = datetime.now(timezone.utc).date().strftime("%Y%m%d")
    invalid_file = root / "execution_logs" / "job_run_log" / current / "invalid.json"
    removed_file = root / "execution_logs" / "job_run_log" / current / "removed.json"
    _write_job_file(invalid_file, ["job-invalid-before"])
    _write_job_file(removed_file, ["job-removed"])

    client, analytics_path = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, root)
        invalid_file.write_text('{"job_id": "broken"\n', encoding="utf-8")
        os.utime(invalid_file, ns=(10**18 + 1, 10**18 + 1))
        removed_file.unlink()

        response = client.post(
            f"/api/v1/environments/{environment_id}/log-sources/{source_id}/refresh",
            json={"mode": "incremental"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "error"
        assert body["sync_execution"]["state"] == "failed"
        assert body["sync_execution"]["result"]["error"]["code"] == "invalid_json"
        assert _raw_ids(analytics_path, "etl_job_runs", "job_id") == [
            "job-invalid-before",
            "job-removed",
        ]
        assert _workspace_rows(
            tmp_path / "studio.db",
            "SELECT file_uri FROM log_file_manifest ORDER BY file_uri",
        ) == [(_canonical(invalid_file),), (_canonical(removed_file),)]
