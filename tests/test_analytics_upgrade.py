from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest


def test_schema_version_has_explicit_replay_path() -> None:
    from datacoolie_studio.domains.analytics.migrations import (
        migration_path,
        validate_registry,
    )

    validate_registry()
    assert [(step.from_version, step.to_version) for step in migration_path(7)] == [
        (7, 8),
        (8, 9),
    ]
    assert all(step.requires_source_replay for step in migration_path(7))
    assert migration_path(6) == ()


def _write_job_log(root: Path, job_id: str) -> None:
    path = root / "job_run_log" / "20260803" / f"{job_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "job_id": job_id,
                "status": "succeeded",
                "start_time": "2026-08-03T00:00:00Z",
                "end_time": "2026-08-03T00:01:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _write_dataflow_log(root: Path, index: int) -> None:
    path = root / "dataflow_run_log" / "20260803" / f"flow-{index}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    escaped_path = str(path).replace("'", "''")
    with duckdb.connect() as connection:
        connection.execute(
            """
            CREATE TABLE fixture AS SELECT
              ?::VARCHAR AS dataflow_run_id,
              ?::VARCHAR AS dataflow_id,
              ?::VARCHAR AS job_id,
              'succeeded'::VARCHAR AS status,
              '2026-08-03T00:00:00Z'::TIMESTAMPTZ AS start_time,
              '2026-08-03T00:01:00Z'::TIMESTAMPTZ AS end_time,
              '["id", "email"]'::VARCHAR AS transform_select_columns,
              '{"missing_column_policy": "ignore"}'::VARCHAR AS transform_configure
            """,
            [f"run-{index}", f"flow-{index}", f"job-{index}"],
        )
        connection.execute(f"COPY fixture TO '{escaped_path}' (FORMAT PARQUET)")


def _workspace_with_sources(tmp_path: Path, monkeypatch, count: int = 2):
    from datacoolie_studio.db.models import Environment, EnvironmentSource, Project
    from datacoolie_studio.db.session import create_session, init_db

    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))
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


def _write_old_live_cache(path: Path, source_id: int = 999) -> None:
    from datacoolie_studio.domains.analytics import store

    result = store.publish_rows(
        source_id,
        [],
        [
            (
                "old.jsonl",
                "job_jsonl",
                "{}",
                {
                    "job_id": "old-job",
                    "status": "succeeded",
                    "end_time": "2026-08-02T00:00:00Z",
                },
            )
        ],
        [],
        ["old.jsonl"],
        database_path=path,
    )
    assert result["published"] is True
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            "UPDATE etl_analytics_meta SET schema_version = 7 WHERE singleton_id = 1"
        )


def test_startup_upgrade_replays_all_sources_and_swaps_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from datacoolie_studio.domains.analytics import schema
    from datacoolie_studio.domains.analytics_upgrade import service as upgrade

    session, sources = _workspace_with_sources(tmp_path, monkeypatch)
    session.close()
    analytics_path = tmp_path / "analytics.duckdb"
    _write_old_live_cache(analytics_path)
    monkeypatch.setattr(upgrade, "analytics_database_path", lambda: analytics_path)

    result = upgrade.run_analytics_upgrade_once()

    assert result["state"] == "succeeded"
    assert result["source_ids"] == [source.id for source in sources]
    assert result["completed_source_ids"] == [source.id for source in sources]
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        meta = connection.execute(
            "SELECT schema_version, build_state FROM etl_analytics_meta WHERE singleton_id = 1"
        ).fetchone()
        cached_sources = [
            row[0]
            for row in connection.execute(
                "SELECT source_id FROM etl_cache_sources ORDER BY source_id"
            ).fetchall()
        ]
        jobs = [
            row[0]
            for row in connection.execute(
                "SELECT job_id FROM etl_job_runs ORDER BY job_id"
            ).fetchall()
        ]
        transform_values = connection.execute(
            """
            SELECT transform_select_columns, transform_configure
            FROM monitoring_dataflow_facts
            WHERE dataflow_run_id = 'run-1'
            """
        ).fetchone()
    assert meta == (schema.ANALYTICS_SCHEMA_VERSION, "ready")
    assert cached_sources == [source.id for source in sources]
    assert jobs == ["job-1", "job-2"]
    assert transform_values == (
        '["id", "email"]',
        '{"missing_column_policy": "ignore"}',
    )
    assert not analytics_path.with_name("analytics.candidate.duckdb").exists()

    second = upgrade.run_analytics_upgrade_once()
    assert second["state"] == "current"
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM etl_job_runs").fetchone()[0] == 2


def test_failed_upgrade_preserves_live_cache_and_schedules_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from datacoolie_studio.db.session import create_session
    from datacoolie_studio.domains.analytics import access, store
    from datacoolie_studio.domains.analytics_upgrade import service as upgrade
    from datacoolie_studio.domains.analytics.errors import AnalyticsRebuildRequired

    session, sources = _workspace_with_sources(tmp_path, monkeypatch, count=1)
    session.close()
    analytics_path = tmp_path / "analytics.duckdb"
    _write_old_live_cache(analytics_path)
    monkeypatch.setattr(upgrade, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(
        upgrade,
        "refresh_log_source_cache",
        lambda *_args, **_kwargs: {
            "status": "error",
            "error": {"code": "storage_access_failed", "message": "Unavailable"},
        },
    )
    failed_at = datetime(2026, 8, 3, tzinfo=timezone.utc)

    result = upgrade.run_analytics_upgrade_once(now=failed_at)

    assert result["state"] == "failed"
    assert result["source_ids"] == [sources[0].id]
    assert result["error_code"] == "storage_access_failed"
    assert result["next_retry_at"] == "2026-08-03T00:00:30+00:00"
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        assert connection.execute(
            "SELECT schema_version FROM etl_analytics_meta WHERE singleton_id = 1"
        ).fetchone()[0] == 7
        assert connection.execute(
            "SELECT job_id FROM etl_job_runs"
        ).fetchall() == [("old-job",)]
    assert not analytics_path.with_name("analytics.candidate.duckdb").exists()

    manual_publish = store.publish_rows(
        sources[0].id,
        [],
        [("new.jsonl", "job_jsonl", "{}", {"job_id": "partial-job"})],
        [],
        ["new.jsonl"],
        database_path=analytics_path,
    )
    assert manual_publish["published"] is False
    assert manual_publish["errors"][0]["code"] == "analytics_upgrade_in_progress"

    monkeypatch.setattr(access, "analytics_database_path", lambda: analytics_path)
    with pytest.raises(AnalyticsRebuildRequired) as raised:
        with access.reader([sources[0].id]):
            pass
    assert raised.value.reason == "analytics_upgrade_failed"

    retry_session = create_session()
    try:
        retry = upgrade.request_analytics_upgrade_retry(retry_session)
    finally:
        retry_session.close()
    assert retry["state"] == "pending"
    assert retry["next_retry_at"] is None


def test_source_scope_change_discards_candidate_before_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from datacoolie_studio.db.models import EnvironmentSource
    from datacoolie_studio.db.session import create_session
    from datacoolie_studio.domains.analytics import store
    from datacoolie_studio.domains.analytics_upgrade import service as upgrade

    session, sources = _workspace_with_sources(tmp_path, monkeypatch)
    session.close()
    analytics_path = tmp_path / "analytics.duckdb"
    _write_old_live_cache(analytics_path)
    monkeypatch.setattr(upgrade, "analytics_database_path", lambda: analytics_path)
    validate_candidate = store.validate_analytics_candidate

    def validate_then_change_scope(candidate_path: Path, source_ids: list[int]) -> None:
        validate_candidate(candidate_path, source_ids)
        mutation_session = create_session()
        try:
            source = mutation_session.get(EnvironmentSource, sources[-1].id)
            assert source is not None
            source.enabled = False
            mutation_session.commit()
        finally:
            mutation_session.close()

    monkeypatch.setattr(store, "validate_analytics_candidate", validate_then_change_scope)

    result = upgrade.run_analytics_upgrade_once()

    assert result["state"] == "failed"
    assert result["error_code"] == "source_scope_changed"
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        assert connection.execute(
            "SELECT schema_version FROM etl_analytics_meta WHERE singleton_id = 1"
        ).fetchone()[0] == 7
        assert connection.execute(
            "SELECT job_id FROM etl_job_runs"
        ).fetchall() == [("old-job",)]


def test_simultaneous_startup_upgrade_calls_publish_one_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from datacoolie_studio.domains.analytics_upgrade import service as upgrade

    session, _sources = _workspace_with_sources(tmp_path, monkeypatch, count=1)
    session.close()
    analytics_path = tmp_path / "analytics.duckdb"
    _write_old_live_cache(analytics_path)
    monkeypatch.setattr(upgrade, "analytics_database_path", lambda: analytics_path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: upgrade.run_analytics_upgrade_once(), range(2)))

    assert sorted(result["state"] for result in results) == ["current", "succeeded"]
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM etl_job_runs").fetchone()[0] == 1
