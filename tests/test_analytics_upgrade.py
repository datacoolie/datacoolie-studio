from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import duckdb


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


def test_healthy_sources_publish_when_one_source_is_unbuildable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """One unreachable source is parked and skipped; the healthy sources still publish."""
    from datacoolie_studio.db.models import SourceObservation
    from datacoolie_studio.db.session import create_session
    from datacoolie_studio.domains.analytics_upgrade import service as upgrade

    session, sources = _workspace_with_sources(tmp_path, monkeypatch, count=2)
    session.close()
    healthy_id, broken_id = sources[0].id, sources[1].id
    analytics_path = tmp_path / "analytics.duckdb"
    _write_old_live_cache(analytics_path)
    monkeypatch.setattr(upgrade, "analytics_database_path", lambda: analytics_path)

    real_refresh = upgrade.refresh_log_source_cache

    def selective_refresh(session, source, **kwargs):
        if source.id == broken_id:
            return {
                "status": "error",
                "error": {"code": "storage_access_failed", "message": "Unavailable"},
            }
        return real_refresh(session, source, **kwargs)

    monkeypatch.setattr(upgrade, "refresh_log_source_cache", selective_refresh)

    result = upgrade.run_analytics_upgrade_once()

    assert result["state"] == "succeeded"
    assert result["source_ids"] == [healthy_id]
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        cached_sources = [
            row[0]
            for row in connection.execute(
                "SELECT source_id FROM etl_cache_sources ORDER BY source_id"
            ).fetchall()
        ]
    assert cached_sources == [healthy_id]

    verify_session = create_session()
    try:
        broken = verify_session.get(SourceObservation, broken_id)
        assert broken is not None
        assert broken.automatic_observation_paused_at is not None
    finally:
        verify_session.close()

    # A second run is idempotent: the healthy scope is current, no rebuild loop.
    second = upgrade.run_analytics_upgrade_once()
    assert second["state"] == "current"
    assert second["source_ids"] == [healthy_id]


def test_unbuildable_source_is_parked_and_does_not_block_manual_sync(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A source whose storage cannot be read is parked instead of wedging everything.

    The complete-candidate upgrade must not fail forever on one unreachable source and
    must not block manual/scheduled per-source sync. The broken source is paused (and
    drops out of Monitoring coverage), the live cache is preserved, and manual publish
    keeps working.
    """
    from datacoolie_studio.db.models import EnvironmentSource, SourceObservation
    from datacoolie_studio.db.session import create_session
    from datacoolie_studio.domains.analytics import store
    from datacoolie_studio.domains.analytics_upgrade import service as upgrade
    from datacoolie_studio.domains.monitoring import context as monitoring_context

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
    checked_at = datetime(2026, 8, 3, tzinfo=timezone.utc)

    result = upgrade.run_analytics_upgrade_once(now=checked_at)

    # The upgrade settles instead of staying "failed" and retrying forever.
    assert result["state"] == "succeeded"
    assert result["source_ids"] == []

    verify_session = create_session()
    try:
        observation = verify_session.get(SourceObservation, sources[0].id)
        assert observation is not None
        assert observation.automatic_observation_paused_at is not None
        assert observation.last_outcome == "error"
        parked_source = verify_session.get(EnvironmentSource, sources[0].id)
        assert parked_source is not None
        # A parked source drops out of Monitoring coverage, matching the upgrade scope.
        assert monitoring_context.source_ids([parked_source]) == []
    finally:
        verify_session.close()

    # The live cache is untouched (no swap) and no candidate is left behind.
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        assert connection.execute(
            "SELECT job_id FROM etl_job_runs"
        ).fetchall() == [("old-job",)]
    assert not analytics_path.with_name("analytics.candidate.duckdb").exists()

    # Manual per-source publish is no longer blocked by the upgrade state.
    manual_publish = store.publish_rows(
        sources[0].id,
        [],
        [
            (
                "new.jsonl",
                "job_jsonl",
                "{}",
                {
                    "job_id": "partial-job",
                    "status": "succeeded",
                    "end_time": "2026-08-03T00:00:00Z",
                },
            )
        ],
        [],
        ["new.jsonl"],
        database_path=analytics_path,
    )
    assert manual_publish["published"] is True
    assert not any(
        error.get("code") == "analytics_upgrade_in_progress"
        for error in manual_publish["errors"]
    )


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


def test_resumable_candidate_reports_already_built_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A retried upgrade skips sources already published into a compatible candidate."""
    from datacoolie_studio.domains.analytics import store
    from datacoolie_studio.domains.analytics_upgrade import service as upgrade

    session, _sources = _workspace_with_sources(tmp_path, monkeypatch, count=0)
    session.close()
    candidate = tmp_path / "analytics.candidate.duckdb"
    for source_id in (10, 20):
        store.publish_rows(
            source_id,
            [],
            [
                (
                    f"{source_id}.jsonl",
                    "job_jsonl",
                    "{}",
                    {
                        "job_id": f"job-{source_id}",
                        "status": "succeeded",
                        "end_time": "2026-08-03T00:00:00Z",
                    },
                )
            ],
            [],
            [f"{source_id}.jsonl"],
            database_path=candidate,
        )

    assert upgrade._resumable_candidate_sources(candidate, [10, 20, 30]) == {10, 20}
    assert upgrade._resumable_candidate_sources(tmp_path / "missing.duckdb", [10]) == set()


def test_reconcile_orphaned_sync_jobs_fails_running_jobs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Running jobs left by a killed process are failed on restart so they stop blocking."""
    from datacoolie_studio.db.models import SyncJob, utc_now
    from datacoolie_studio.domains.sync.service import reconcile_orphaned_sync_jobs

    session, sources = _workspace_with_sources(tmp_path, monkeypatch, count=1)
    job = SyncJob(
        environment_id=sources[0].environment_id,
        source_id=sources[0].id,
        source_kind="logs",
        job_type="analytics_upgrade",
        status="running",
        started_at=utc_now(),
    )
    session.add(job)
    session.commit()

    assert reconcile_orphaned_sync_jobs(session) == 1
    session.refresh(job)
    assert job.status == "failed"
    assert job.completed_at is not None
    # A second pass is a no-op once nothing is left running.
    assert reconcile_orphaned_sync_jobs(session) == 0
    session.close()


def test_parallel_build_publishes_every_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The concurrent per-source build publishes exact coverage for many sources."""
    from datacoolie_studio.domains.analytics import schema
    from datacoolie_studio.domains.analytics_upgrade import service as upgrade

    session, sources = _workspace_with_sources(tmp_path, monkeypatch, count=5)
    session.close()
    analytics_path = tmp_path / "analytics.duckdb"
    _write_old_live_cache(analytics_path)
    monkeypatch.setattr(upgrade, "analytics_database_path", lambda: analytics_path)

    result = upgrade.run_analytics_upgrade_once()

    assert result["state"] == "succeeded"
    assert result["source_ids"] == [source.id for source in sources]
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
    assert meta == (schema.ANALYTICS_SCHEMA_VERSION, "ready")
    assert cached_sources == [source.id for source in sources]


def test_worker_exception_aborts_and_preserves_live_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """An unexpected worker failure aborts the upgrade; the live cache is untouched."""
    from datacoolie_studio.domains.analytics_upgrade import service as upgrade

    session, _sources = _workspace_with_sources(tmp_path, monkeypatch, count=2)
    session.close()
    analytics_path = tmp_path / "analytics.duckdb"
    _write_old_live_cache(analytics_path)
    monkeypatch.setattr(upgrade, "analytics_database_path", lambda: analytics_path)

    def boom(_session, _source, **_kwargs):
        raise RuntimeError("materialization exploded")

    monkeypatch.setattr(upgrade, "refresh_log_source_cache", boom)

    result = upgrade.run_analytics_upgrade_once()

    assert result["state"] == "failed"
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        assert connection.execute(
            "SELECT schema_version FROM etl_analytics_meta WHERE singleton_id = 1"
        ).fetchone()[0] == 7
        assert connection.execute(
            "SELECT job_id FROM etl_job_runs"
        ).fetchall() == [("old-job",)]


def test_concurrent_sync_job_inserts_do_not_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """WAL + busy_timeout let concurrent worker sessions write without locking errors."""
    from sqlalchemy import func, select

    from datacoolie_studio.db.models import SyncJob, utc_now
    from datacoolie_studio.db.session import create_session

    session, sources = _workspace_with_sources(tmp_path, monkeypatch, count=1)
    environment_id = sources[0].environment_id
    source_id = sources[0].id
    session.close()

    def insert(_index: int) -> None:
        worker = create_session()
        try:
            worker.add(
                SyncJob(
                    environment_id=environment_id,
                    source_id=source_id,
                    source_kind="logs",
                    job_type="analytics_upgrade",
                    status="succeeded",
                    started_at=utc_now(),
                    completed_at=utc_now(),
                )
            )
            worker.commit()
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(insert, range(16)))

    verify = create_session()
    try:
        count = verify.scalar(
            select(func.count(SyncJob.id)).where(SyncJob.source_id == source_id)
        )
    finally:
        verify.close()
    assert count == 16
