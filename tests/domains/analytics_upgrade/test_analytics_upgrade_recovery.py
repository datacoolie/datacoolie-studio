from __future__ import annotations

from tests.support.v6_records import native_record

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

from tests.domains.analytics_upgrade.analytics_upgrade_test_support import (
    _write_system_log,
    _workspace_with_sources,
    _write_old_live_cache,
)

def test_startup_upgrade_replays_all_sources_and_swaps_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from datacoolie_studio.domains.analytics import schema
    from datacoolie_studio.domains.analytics_upgrade import service as upgrade
    from datacoolie_studio.db.models import AnalyticsUpgrade
    from datacoolie_studio.db.session import create_session

    session, sources = _workspace_with_sources(tmp_path, monkeypatch)
    session.close()
    analytics_path = tmp_path / "analytics.duckdb"
    _write_old_live_cache(
        analytics_path,
        schema_version=9,
        include_legacy_transform_policy=True,
    )
    monkeypatch.setattr(upgrade, "analytics_database_path", lambda: analytics_path)

    result = upgrade.run_analytics_upgrade_once()

    assert result["state"] == "succeeded"
    assert result["source_ids"] == [source.id for source in sources]
    assert result["completed_source_ids"] == [source.id for source in sources]
    verify_session = create_session()
    persisted_upgrade = verify_session.get(AnalyticsUpgrade, 1)
    assert persisted_upgrade is not None
    persisted_context = json.loads(persisted_upgrade.source_context_json)
    persisted_snapshot = json.loads(persisted_upgrade.source_snapshot_json)
    persisted_checkpoints = json.loads(persisted_upgrade.batch_checkpoint_json)
    assert persisted_context[str(sources[0].id)]["project_name"] == f"upgrade-{tmp_path.name}"
    assert persisted_context[str(sources[0].id)]["environment_name"] == "dev"
    assert set(persisted_snapshot) == {str(source.id) for source in sources}
    assert set(persisted_checkpoints) == {str(source.id) for source in sources} | {"rollback"}
    assert Path(persisted_checkpoints["rollback"]["path"]).is_file()
    assert all(
        checkpoint["status"] == "completed"
        and checkpoint["batches"]
        for key, checkpoint in persisted_checkpoints.items() if key != "rollback"
    )
    verify_session.close()
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
            SELECT job_num, job_index, transform_select_columns, transform_configure
            FROM monitoring_dataflow_facts
            WHERE dataflow_run_id = 'run-1'
            """
        ).fetchone()
        dataflow_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info('etl_dataflow_runs')").fetchall()
        }
    assert meta == (schema.ANALYTICS_SCHEMA_VERSION, "ready")
    assert cached_sources == [source.id for source in sources]
    assert jobs == ["job-1", "job-2"]
    assert transform_values == (
        2,
        1,
        '["id", "email"]',
        '{"missing_column_policy": "ignore"}',
    )
    assert "transform_missing_column_policy" not in dataflow_columns
    assert not analytics_path.with_name("analytics.candidate.duckdb").exists()

    second = upgrade.run_analytics_upgrade_once()
    assert second["state"] == "current"
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM etl_job_runs").fetchone()[0] == 2

def test_full_replay_reindexes_unchanged_system_log_files_after_control_reset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from datacoolie_studio.db.models import LogFileManifest
    from datacoolie_studio.db.session import create_session
    from datacoolie_studio.domains.analytics_upgrade import service as upgrade
    from datacoolie_studio.domains.logs import ingestion

    session, sources = _workspace_with_sources(tmp_path, monkeypatch, count=1)
    _write_system_log(tmp_path / "logs-1", "job-1")
    analytics_path = tmp_path / "analytics.duckdb"

    # Seed the operational control state first. The system file is unchanged by
    # the later full replay, so it must be present in ``plan.files`` even though
    # it is absent from ``plan.candidates``.
    seeded = ingestion.refresh_log_source_cache(
        session,
        sources[0],
        database_path_override=analytics_path,
    )
    assert seeded["status"] == "ok"
    session.close()

    _write_old_live_cache(analytics_path, schema_version=9)
    monkeypatch.setattr(upgrade, "analytics_database_path", lambda: analytics_path)

    result = upgrade.run_analytics_upgrade_once()

    assert result["state"] == "succeeded"
    verify_session = create_session()
    try:
        system_files = verify_session.query(LogFileManifest).filter_by(
            source_id=sources[0].id,
            file_kind="system_json",
        ).all()
        assert [item.file_uri for item in system_files] == [
            (tmp_path / "logs-1" / "system_logs" / "20260803" / "system-events.json").as_posix()
        ]
    finally:
        verify_session.close()

def test_same_schema_repairs_only_missing_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A compatible cache adds one missing source without replaying healthy rows."""
    from datacoolie_studio.domains.analytics import schema
    from datacoolie_studio.domains.analytics_upgrade import service as upgrade

    session, sources = _workspace_with_sources(tmp_path, monkeypatch, count=2)
    session.close()
    healthy_id, missing_id = (source.id for source in sources)
    analytics_path = tmp_path / "analytics.duckdb"
    _write_old_live_cache(
        analytics_path,
        source_id=healthy_id,
        schema_version=schema.ANALYTICS_SCHEMA_VERSION,
    )
    monkeypatch.setattr(upgrade, "analytics_database_path", lambda: analytics_path)
    real_refresh = upgrade.refresh_log_source_cache
    refreshed_ids: list[int] = []

    def counted_refresh(worker_session, source, **kwargs):
        refreshed_ids.append(source.id)
        return real_refresh(worker_session, source, **kwargs)

    monkeypatch.setattr(upgrade, "refresh_log_source_cache", counted_refresh)

    result = upgrade.run_analytics_upgrade_once()

    assert result["state"] == "succeeded"
    assert result["operation_kind"] == "source_repair"
    assert result["reason_code"] == "missing_source_coverage"
    assert result["source_ids"] == [missing_id]
    assert refreshed_ids == [missing_id]
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        cached_sources = [
            row[0]
            for row in connection.execute(
                "SELECT source_id FROM etl_cache_sources ORDER BY source_id"
            ).fetchall()
        ]
        generations = dict(
            connection.execute(
                "SELECT source_id, generation FROM etl_cache_sources"
            ).fetchall()
        )
        job_rows_by_source = dict(
            connection.execute(
                "SELECT _source_id, count(*) FROM etl_job_runs GROUP BY _source_id"
            ).fetchall()
        )
    assert cached_sources == [healthy_id, missing_id]
    assert generations[healthy_id] == 1
    assert generations[missing_id] == 1
    assert job_rows_by_source[healthy_id] == 1
    assert job_rows_by_source[missing_id] == 1
    assert not analytics_path.with_name("analytics.candidate.duckdb").exists()

    second = upgrade.run_analytics_upgrade_once()
    assert second["state"] == "current"

def test_same_schema_complete_cache_is_a_noop(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from datacoolie_studio.domains.analytics import schema
    from datacoolie_studio.domains.analytics_upgrade import service as upgrade

    session, sources = _workspace_with_sources(tmp_path, monkeypatch, count=1)
    session.close()
    analytics_path = tmp_path / "analytics.duckdb"
    _write_old_live_cache(
        analytics_path,
        source_id=sources[0].id,
        schema_version=schema.ANALYTICS_SCHEMA_VERSION,
    )
    monkeypatch.setattr(upgrade, "analytics_database_path", lambda: analytics_path)

    result = upgrade.run_analytics_upgrade_once()

    assert result["state"] == "current"
    assert result["source_ids"] == [sources[0].id]
    assert result.get("attempt_count") is None
    assert not analytics_path.with_name("analytics.candidate.duckdb").exists()

def test_source_repair_failure_backoff_keeps_healthy_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from datacoolie_studio.domains.analytics import schema
    from datacoolie_studio.domains.analytics_upgrade import service as upgrade

    session, sources = _workspace_with_sources(tmp_path, monkeypatch, count=2)
    session.close()
    healthy_id, missing_id = (source.id for source in sources)
    analytics_path = tmp_path / "analytics.duckdb"
    _write_old_live_cache(
        analytics_path,
        source_id=healthy_id,
        schema_version=schema.ANALYTICS_SCHEMA_VERSION,
    )
    monkeypatch.setattr(upgrade, "analytics_database_path", lambda: analytics_path)
    calls: list[int] = []

    def fail_refresh(_session, source, **_kwargs):
        calls.append(source.id)
        return {
            "status": "error",
            "error": {"code": "file_read_failed", "message": "Unreadable log"},
        }

    monkeypatch.setattr(upgrade, "refresh_log_source_cache", fail_refresh)
    checked_at = datetime(2026, 8, 3, tzinfo=timezone.utc)

    first = upgrade.run_analytics_upgrade_once(now=checked_at)
    second = upgrade.run_analytics_upgrade_once(now=checked_at)

    assert first["state"] == "failed"
    assert first["operation_kind"] == "source_repair"
    assert first["error_code"] == "file_read_failed"
    assert first["episode_attempt_count"] == 1
    assert second["state"] == "failed"
    assert calls == [missing_id]
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        assert connection.execute(
            "SELECT source_id FROM etl_cache_sources ORDER BY source_id"
        ).fetchall() == [(healthy_id,)]

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

    assert result["state"] == "completed_with_warnings"
    assert result["source_ids"] == [healthy_id, broken_id]
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
    drops out of Monitoring coverage), and the live cache is preserved. A manual
    refresh stays available but cannot replace an incompatible cache by itself.
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
    assert result["state"] == "completed_with_warnings"
    assert result["source_ids"] == [sources[0].id]

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

    # Manual publication is not blocked by stale upgrade state, but still routes
    # the incompatible cache through the complete upgrade owner.
    manual_publish = store.publish_rows(
        sources[0].id,
        [],
        [
            (
                "new.json",
                "job_json",
                "{}",
                native_record({
                    "_type": "job_run_log",
                    "log_schema_version": 3,
                    "job_id": "partial-job",
                    "status": "succeeded",
                    "end_time": "2026-08-03T00:00:00Z",
                }),
            )
        ],
        [],
        ["new.json"],
        database_path=analytics_path,
    )
    assert manual_publish["published"] is False
    assert manual_publish["errors"][0]["code"] == "analytics_upgrade_required"
    assert not any(
        error.get("code") == "analytics_upgrade_in_progress"
        for error in manual_publish["errors"]
    )
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        assert connection.execute("SELECT job_id FROM etl_job_runs").fetchall() == [("old-job",)]

def test_source_scope_change_discards_candidate_before_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from datacoolie_studio.db.models import EnvironmentSource
    from datacoolie_studio.db.session import create_session
    from datacoolie_studio.domains.analytics import publication
    from datacoolie_studio.domains.analytics_upgrade import service as upgrade

    session, sources = _workspace_with_sources(tmp_path, monkeypatch)
    session.close()
    analytics_path = tmp_path / "analytics.duckdb"
    _write_old_live_cache(analytics_path)
    monkeypatch.setattr(upgrade, "analytics_database_path", lambda: analytics_path)
    validate_candidate = publication.validate_analytics_candidate

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

    monkeypatch.setattr(
        upgrade.publication,
        "validate_analytics_candidate",
        validate_then_change_scope,
    )

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
                    f"{source_id}.json",
                    "job_json",
                    "{}",
                    native_record({
                        "_type": "job_run_log",
                        "log_schema_version": 3,
                        "job_id": f"job-{source_id}",
                        "status": "succeeded",
                        "end_time": "2026-08-03T00:00:00Z",
                    }),
                )
            ],
            [],
            [f"{source_id}.json"],
            database_path=candidate,
        )

    from datacoolie_studio.domains.logs.batching import materialization_fingerprint
    assert upgrade._resumable_candidate_sources(candidate, [10, 20, 30]) == set()
    checkpoints = {
        str(source_id): {"status": "completed", "materialization_fingerprint": materialization_fingerprint()}
        for source_id in (10, 20)
    }
    assert upgrade._resumable_candidate_sources(candidate, [10, 20, 30], checkpoints=checkpoints) == {10, 20}
    assert upgrade._resumable_candidate_sources(tmp_path / "missing.duckdb", [10]) == set()

def test_failed_upgrade_keeps_and_resumes_committed_batch_checkpoints(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from datacoolie_studio.db.models import AnalyticsUpgrade
    from datacoolie_studio.db.session import create_session
    from datacoolie_studio.domains.analytics_upgrade import service as upgrade
    from datacoolie_studio.domains.logs import batching

    session, sources = _workspace_with_sources(tmp_path, monkeypatch, count=1)
    session.close()
    analytics_path = tmp_path / "analytics.duckdb"
    _write_old_live_cache(analytics_path)
    monkeypatch.setattr(upgrade, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(batching, "LOG_BATCH_MAX_FILES", 1)

    real_build = upgrade._build_sources_parallel
    failed_once = True

    def fail_after_committed_batches(
        source_ids: list[int],
        candidate_path: Path,
        **kwargs,
    ):
        nonlocal failed_once
        result = real_build(source_ids, candidate_path, **kwargs)
        if failed_once:
            failed_once = False
            raise RuntimeError("coordinator interrupted after batch commits")
        return result

    monkeypatch.setattr(upgrade, "_build_sources_parallel", fail_after_committed_batches)
    first = upgrade.run_analytics_upgrade_once(
        now=datetime(2026, 8, 3, 0, 0, tzinfo=timezone.utc)
    )

    assert first["state"] == "failed"
    candidate = analytics_path.with_name("analytics.candidate.duckdb")
    assert candidate.exists()
    checkpoint_session = create_session()
    try:
        persisted = checkpoint_session.get(AnalyticsUpgrade, 1)
        assert persisted is not None
        checkpoints = json.loads(persisted.batch_checkpoint_json)
        source_checkpoint = checkpoints[str(sources[0].id)]
        assert source_checkpoint["status"] == "building"
        assert len(source_checkpoint["batches"]) == 2
    finally:
        checkpoint_session.close()

    monkeypatch.setattr(upgrade, "_build_sources_parallel", real_build)
    second = upgrade.run_analytics_upgrade_once(
        now=datetime(2026, 8, 3, 0, 1, tzinfo=timezone.utc)
    )

    assert second["state"] == "succeeded"
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        assert connection.execute(
            "SELECT count(*) FROM etl_job_runs"
        ).fetchone()[0] == 1
    assert not candidate.exists()

def test_upgrade_commits_log_control_only_after_candidate_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A failed candidate never advances the live log-control manifest."""
    from datacoolie_studio.db.models import LogFileManifest, LogStreamState
    from datacoolie_studio.db.session import create_session
    from datacoolie_studio.domains.analytics_upgrade import service as upgrade

    session, sources = _workspace_with_sources(tmp_path, monkeypatch, count=1)
    session.close()
    analytics_path = tmp_path / "analytics.duckdb"
    _write_old_live_cache(analytics_path)
    monkeypatch.setattr(upgrade, "analytics_database_path", lambda: analytics_path)

    real_finalize = upgrade.publication.finalize_candidate
    failed_once = True

    def fail_first_finalize(candidate_path: Path, source_ids: list[int]) -> None:
        nonlocal failed_once
        if failed_once:
            failed_once = False
            raise RuntimeError("finalization interrupted")
        real_finalize(candidate_path, source_ids)

    monkeypatch.setattr(upgrade.publication, "finalize_candidate", fail_first_finalize)
    first = upgrade.run_analytics_upgrade_once(
        now=datetime(2026, 8, 3, tzinfo=timezone.utc)
    )
    assert first["state"] == "failed"

    verify_session = create_session()
    try:
        assert verify_session.query(LogFileManifest).count() == 0
        assert verify_session.query(LogStreamState).count() == 0
    finally:
        verify_session.close()

    second = upgrade.run_analytics_upgrade_once(
        now=datetime(2026, 8, 3, 0, 1, tzinfo=timezone.utc)
    )
    assert second["state"] == "succeeded"

    verify_session = create_session()
    try:
        assert verify_session.query(LogFileManifest).count() == 2
        assert verify_session.query(LogStreamState).count() == 3
    finally:
        verify_session.close()

def test_post_swap_log_control_failure_is_recoverable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from datacoolie_studio.db.models import LogFileManifest, LogStreamState
    from datacoolie_studio.db.session import create_session
    from datacoolie_studio.domains.analytics_upgrade import service as upgrade

    session, _sources = _workspace_with_sources(tmp_path, monkeypatch, count=1)
    session.close()
    analytics_path = tmp_path / "analytics.duckdb"
    _write_old_live_cache(analytics_path)
    monkeypatch.setattr(upgrade, "analytics_database_path", lambda: analytics_path)

    real_apply = upgrade.apply_deferred_log_control
    failed_once = True

    def fail_once(*args, **kwargs):
        nonlocal failed_once
        if failed_once:
            failed_once = False
            raise RuntimeError("control commit interrupted after swap")
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(upgrade, "apply_deferred_log_control", fail_once)
    first = upgrade.run_analytics_upgrade_once(
        now=datetime(2026, 8, 3, tzinfo=timezone.utc)
    )
    assert first["state"] == "publishing"
    assert first["error_code"] == "control_state_pending"
    assert not analytics_path.with_name("analytics.candidate.duckdb").exists()

    second = upgrade.run_analytics_upgrade_once(
        now=datetime(2026, 8, 3, 0, 1, tzinfo=timezone.utc)
    )
    assert second["state"] == "succeeded"

    verify_session = create_session()
    try:
        assert verify_session.query(LogFileManifest).count() == 2
        assert verify_session.query(LogStreamState).count() == 3
    finally:
        verify_session.close()

def test_retry_replays_source_when_checkpoint_file_revision_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from datacoolie_studio.domains.analytics_upgrade import service as upgrade
    from datacoolie_studio.domains.logs import batching

    session, _sources = _workspace_with_sources(tmp_path, monkeypatch, count=1)
    session.close()
    analytics_path = tmp_path / "analytics.duckdb"
    _write_old_live_cache(analytics_path)
    monkeypatch.setattr(upgrade, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(batching, "LOG_BATCH_MAX_FILES", 1)

    real_build = upgrade._build_sources_parallel
    failed_once = True

    def fail_after_first_attempt(source_ids: list[int], candidate_path: Path, **kwargs):
        nonlocal failed_once
        result = real_build(source_ids, candidate_path, **kwargs)
        if failed_once:
            failed_once = False
            raise RuntimeError("coordinator interrupted after source batches")
        return result

    monkeypatch.setattr(upgrade, "_build_sources_parallel", fail_after_first_attempt)
    first = upgrade.run_analytics_upgrade_once(
        now=datetime(2026, 8, 3, 0, 0, tzinfo=timezone.utc)
    )
    assert first["state"] == "failed"

    dataflow_path = (
        tmp_path
        / "logs-1"
        / "execution_logs"
        / "dataflow_run_log"
        / "20260803"
        / "flow-1.json"
    )
    changed_record = native_record({
        "_type": "dataflow_run_log",
        "log_schema_version": 3,
        "dataflow_run_id": "run-added",
        "dataflow_id": "flow-added",
        "job_id": "job-1",
        "status": "succeeded",
        "start_time": "2026-08-03T00:02:00Z",
        "end_time": "2026-08-03T00:03:00Z",
    })
    with dataflow_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(changed_record) + "\n")

    monkeypatch.setattr(upgrade, "_build_sources_parallel", real_build)
    second = upgrade.run_analytics_upgrade_once(
        now=datetime(2026, 8, 3, 0, 1, tzinfo=timezone.utc)
    )

    assert second["state"] == "succeeded"
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM etl_dataflow_runs").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM etl_job_runs").fetchone()[0] == 1

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

def test_upgrade_status_projects_per_source_timing(tmp_path: Path, monkeypatch) -> None:
    from datacoolie_studio.db.models import AnalyticsUpgrade, Environment, Project, SyncJob
    from datacoolie_studio.domains.analytics_upgrade.service import upgrade_status

    session, sources = _workspace_with_sources(tmp_path, monkeypatch, count=2)
    environment = session.get(Environment, sources[0].environment_id)
    assert environment is not None
    project = session.get(Project, environment.project_id)
    assert project is not None
    started_at = datetime(2026, 8, 11, 3, 0, tzinfo=timezone.utc)
    completed_at = started_at + timedelta(seconds=12.5)
    upgrade = AnalyticsUpgrade(
        id=1,
        source_schema_version=10,
        target_schema_version=11,
        state="building",
        source_ids_json=json.dumps([source.id for source in sources]),
        completed_source_ids_json="[]",
        attempt_count=1,
        started_at=started_at,
    )
    session.add(upgrade)
    session.add(
        SyncJob(
            environment_id=sources[0].environment_id,
            source_id=sources[0].id,
            source_kind="logs",
            job_type="analytics_upgrade",
            status="succeeded",
            message="Log source cache refreshed",
            started_at=started_at,
            completed_at=completed_at,
        )
    )
    session.commit()

    status = upgrade_status(upgrade, session)

    source_context = {
        "project_id": project.id,
        "project_name": project.name,
        "environment_id": environment.id,
        "environment_name": environment.name,
        "source_kind": "logs",
        "label": str(sources[0].uri),
        "uri": str(sources[0].uri),
        "is_registered": True,
    }

    assert status["duration_seconds"] >= 12.5
    assert status["completed_source_ids"] == [sources[0].id]
    assert status["source_progress"] == [
        {
            "source_id": sources[0].id,
            **source_context,
            "status": "succeeded",
            "message": "Log source cache refreshed",
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_seconds": 12.5,
        },
        {
            "source_id": sources[1].id,
            "project_id": project.id,
            "project_name": project.name,
            "environment_id": environment.id,
            "environment_name": environment.name,
            "source_kind": "logs",
            "label": str(sources[1].uri),
            "uri": str(sources[1].uri),
            "is_registered": True,
            "status": "pending",
            "message": None,
            "started_at": None,
            "completed_at": None,
            "duration_seconds": None,
        },
    ]
    session.close()

def test_upgrade_status_uses_snapshot_when_source_is_removed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from datacoolie_studio.db.models import AnalyticsUpgrade, Environment, Project
    from datacoolie_studio.domains.analytics_upgrade.service import upgrade_status

    session, sources = _workspace_with_sources(tmp_path, monkeypatch, count=1)
    source = sources[0]
    environment = session.get(Environment, source.environment_id)
    assert environment is not None
    project = session.get(Project, environment.project_id)
    assert project is not None
    upgrade = AnalyticsUpgrade(
        id=1,
        source_schema_version=11,
        target_schema_version=11,
        state="completed_with_warnings",
        operation_kind="source_repair",
        reason_code="missing_source_coverage",
        source_ids_json=json.dumps([source.id]),
        source_context_json=json.dumps({
            str(source.id): {
                "project_id": project.id,
                "project_name": project.name,
                "environment_id": environment.id,
                "environment_name": environment.name,
                "source_kind": "logs",
                "label": "Historical logs",
                "uri": str(source.uri),
            }
        }),
    )
    session.add(upgrade)
    session.commit()

    session.delete(source)
    session.commit()
    session.expire_all()

    status = upgrade_status(session.get(AnalyticsUpgrade, 1), session)

    assert status["source_progress"] == [{
        "source_id": source.id,
        "project_id": project.id,
        "project_name": project.name,
        "environment_id": environment.id,
        "environment_name": environment.name,
        "source_kind": "logs",
        "label": "Historical logs",
        "uri": str(source.uri),
        "is_registered": False,
        "status": "pending",
        "message": None,
        "started_at": None,
        "completed_at": None,
        "duration_seconds": None,
    }]
    session.close()
