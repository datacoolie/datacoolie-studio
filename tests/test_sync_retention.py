from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from datacoolie_studio.db.models import Base, Environment, EnvironmentSource, Project, SyncJob
from datacoolie_studio.domains.sync.service import prune_terminal_sync_jobs


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    project = Project(name="retention")
    session.add(project)
    session.flush()
    environment = Environment(project_id=project.id, name="dev")
    session.add(environment)
    session.flush()
    session.add(
        EnvironmentSource(
            environment_id=environment.id,
            source_kind="metadata",
            uri="metadata.json",
        )
    )
    session.commit()
    return session


def _add_jobs(
    session: Session,
    source_id: int,
    completed_times: list[datetime],
) -> list[int]:
    jobs = [
        SyncJob(
            environment_id=1,
            source_id=source_id,
            source_kind="metadata",
            job_type="refresh",
            status="succeeded",
            started_at=completed_at - timedelta(minutes=1),
            completed_at=completed_at,
        )
        for completed_at in completed_times
    ]
    session.add_all(jobs)
    session.commit()
    return [job.id for job in jobs]


def _job_ids(session: Session, source_id: int) -> list[int]:
    return list(
        session.scalars(
            select(SyncJob.id).where(SyncJob.source_id == source_id).order_by(SyncJob.id)
        )
    )


def test_sparse_source_keeps_latest_100_even_when_older_than_30_days():
    with _session() as session:
        source_id = 1
        expected = _add_jobs(
            session,
            source_id,
            [NOW - timedelta(days=400 - index * 10) for index in range(20)],
        )

        result = prune_terminal_sync_jobs(session, source_id, now=NOW)

        assert result["deleted_jobs"] == 0
        assert _job_ids(session, source_id) == expected


def test_old_high_volume_source_keeps_exactly_latest_100():
    with _session() as session:
        source_id = 1
        created = _add_jobs(
            session,
            source_id,
            [NOW - timedelta(days=200) + timedelta(hours=index) for index in range(120)],
        )

        result = prune_terminal_sync_jobs(session, source_id, now=NOW)

        assert result["deleted_jobs"] == 20
        assert _job_ids(session, source_id) == created[-100:]


def test_recent_high_volume_source_may_keep_more_than_100():
    with _session() as session:
        source_id = 1
        expected = _add_jobs(
            session,
            source_id,
            [NOW - timedelta(days=10) + timedelta(minutes=index) for index in range(180)],
        )

        result = prune_terminal_sync_jobs(session, source_id, now=NOW)

        assert result["deleted_jobs"] == 0
        assert _job_ids(session, source_id) == expected


def test_running_and_malformed_jobs_are_preserved_and_sources_are_isolated():
    with _session() as session:
        environment = session.get(Environment, 1)
        assert environment is not None
        other_source = EnvironmentSource(
            environment_id=environment.id,
            source_kind="metadata",
            uri="other.json",
        )
        session.add(other_source)
        session.flush()
        _add_jobs(
            session,
            1,
            [NOW - timedelta(days=200) + timedelta(minutes=index) for index in range(101)],
        )
        other_ids = _add_jobs(session, other_source.id, [NOW - timedelta(days=500)])
        running = SyncJob(
            environment_id=1,
            source_id=1,
            source_kind="metadata",
            job_type="refresh",
            status="running",
            started_at=NOW - timedelta(days=500),
        )
        malformed = SyncJob(
            environment_id=1,
            source_id=1,
            source_kind="metadata",
            job_type="refresh",
            status="failed",
            started_at=NOW - timedelta(days=500),
        )
        session.add_all([running, malformed])
        session.commit()

        result = prune_terminal_sync_jobs(session, 1, now=NOW)

        assert result["deleted_jobs"] == 1
        assert result["malformed_terminal_jobs"] == 1
        assert session.get(SyncJob, running.id) is not None
        assert session.get(SyncJob, malformed.id) is not None
        assert _job_ids(session, other_source.id) == other_ids
