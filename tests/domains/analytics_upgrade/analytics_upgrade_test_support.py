from __future__ import annotations

from tests.support.v6_records import native_record

import json
from pathlib import Path

import duckdb

def _write_job_log(root: Path, job_id: str) -> None:
    path = root / "execution_logs" / "job_run_log" / "20260803" / f"{job_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            native_record({
                "_type": "job_run_log",
                "log_schema_version": 3,
                "job_id": job_id,
                "status": "succeeded",
                "start_time": "2026-08-03T00:00:00Z",
                "end_time": "2026-08-03T00:01:00Z",
            })
        )
        + "\n",
        encoding="utf-8",
    )

def _write_dataflow_log(root: Path, index: int) -> None:
    path = root / "execution_logs" / "dataflow_run_log" / "20260803" / f"flow-{index}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            native_record({
                "_type": "dataflow_run_log",
                "log_schema_version": 3,
                "dataflow_run_id": f"run-{index}",
                "dataflow_id": f"flow-{index}",
                "job_id": f"job-{index}",
                "job_num": 2,
                "job_index": 1,
                "status": "succeeded",
                "start_time": "2026-08-03T00:00:00Z",
                "end_time": "2026-08-03T00:01:00Z",
                "transform_select_columns": '["id", "email"]',
                "transform_configure": '{"missing_column_policy": "ignore"}',
            })
        )
        + "\n",
        encoding="utf-8",
    )

def _write_system_log(root: Path, job_id: str) -> None:
    path = root / "system_logs" / "20260803" / "system-events.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            native_record({
                "_type": "system_log",
                "log_schema_version": 3,
                "job_id": job_id,
                "dataflow_id": "flow-1",
                "dataflow_run_id": "run-1",
                "event_name": "run_started",
                "ts": "2026-08-03T00:00:00Z",
                "level": "INFO",
                "logger": "datacoolie",
                "msg": "run started",
            })
        )
        + "\n",
        encoding="utf-8",
    )

def _workspace_with_sources(tmp_path: Path, monkeypatch, count: int = 2):
    from datacoolie_studio.db.models import Environment, EnvironmentSource, Project
    from datacoolie_studio.db.session import create_session, init_db

    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))

    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")
    init_db()
    session = create_session()
    project = Project(name=f"upgrade-{tmp_path.name}")
    session.add(project)
    session.flush()
    environment = Environment(project_id=project.id, name="dev")
    session.add(environment)
    session.flush()
    sources = []
    for index in range(count):
        root = tmp_path / f"logs-{index + 1}"
        _write_job_log(root, f"job-{index + 1}")
        _write_dataflow_log(root, index + 1)
        source = EnvironmentSource(
            environment_id=environment.id,
            source_kind="logs",
            uri=str(root),
            storage_provider="local",
            storage_auth_mode="none",
            enabled=True,
        )
        session.add(source)
        sources.append(source)
    session.commit()
    for source in sources:
        session.refresh(source)
    return session, sources

def _write_old_live_cache(
    path: Path,
    source_id: int = 999,
    *,
    schema_version: int = 7,
    include_legacy_transform_policy: bool = False,
) -> None:
    from datacoolie_studio.domains.analytics import store

    result = store.publish_rows(
        source_id,
        [],
        [
            (
                "old.json",
                "job_json",
                "{}",
                native_record({
                    "_type": "job_run_log",
                    "log_schema_version": 3,
                    "job_id": "old-job",
                    "status": "succeeded",
                    "end_time": "2026-08-02T00:00:00Z",
                }),
            )
        ],
        [],
        ["old.json"],
        database_path=path,
    )
    assert result["published"] is True
    with duckdb.connect(str(path)) as connection:
        if include_legacy_transform_policy:
            connection.execute(
                "ALTER TABLE etl_dataflow_runs "
                "ADD COLUMN transform_missing_column_policy VARCHAR"
            )
        connection.execute(
            "UPDATE etl_analytics_meta SET schema_version = ? WHERE singleton_id = 1",
            [schema_version],
        )
