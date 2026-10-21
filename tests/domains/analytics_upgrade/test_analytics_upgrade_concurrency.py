from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb

from tests.domains.analytics_upgrade.analytics_upgrade_test_support import (
    _workspace_with_sources,
    _write_old_live_cache,
)

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
    finalize_calls: list[list[int]] = []
    real_finalize = upgrade.publication.finalize_candidate

    def counted_finalize(candidate_path: Path, source_ids: list[int]) -> None:
        finalize_calls.append(list(source_ids))
        real_finalize(candidate_path, source_ids)

    monkeypatch.setattr(upgrade.publication, "finalize_candidate", counted_finalize)

    result = upgrade.run_analytics_upgrade_once()

    assert result["state"] == "succeeded"
    assert result["source_ids"] == [source.id for source in sources]
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        meta = connection.execute(
            "SELECT schema_version, build_state, generation FROM etl_analytics_meta WHERE singleton_id = 1"
        ).fetchone()
        cached_sources = [
            row[0]
            for row in connection.execute(
                "SELECT source_id FROM etl_cache_sources ORDER BY source_id"
            ).fetchall()
        ]
    assert meta == (schema.ANALYTICS_SCHEMA_VERSION, "ready", 1)
    assert cached_sources == [source.id for source in sources]
    assert finalize_calls == [[source.id for source in sources]]

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

def test_analytics_process_ownership_rejects_second_writer(tmp_path: Path) -> None:
    from datacoolie_studio.domains.analytics.access import (
        AnalyticsProcessOwnershipError,
        analytics_process_ownership,
    )

    analytics_path = tmp_path / "analytics.duckdb"
    with analytics_process_ownership(analytics_path):
        try:
            with analytics_process_ownership(analytics_path):
                raise AssertionError("second analytics owner unexpectedly acquired the lock")
        except AnalyticsProcessOwnershipError as exc:
            assert "analytics cache" in str(exc)
