from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import (
    Base,
    Environment,
    EnvironmentSource,
    Project,
    SourceObservation,
    SyncJob,
)
from datacoolie_studio.db.session import _ensure_sync_job_running_index
from datacoolie_studio.domains.sync.scheduler import (
    _is_due,
    observe_environment_local_sources,
)
from datacoolie_studio.domains.source_observation.contracts import ObservationResult
from datacoolie_studio.domains.source_observation.repository import (
    claim_due_observation_ids,
    complete_observation,
    observation_delay_seconds,
    reset_observation,
)
from datacoolie_studio.domains.sync.service import (
    SyncJobOverlapError,
    begin_sync_job,
    source_sync_status,
    source_refresh_guard,
)
from datacoolie_studio.domains.sources.initialization import (
    queue_source_initializations,
)
from datacoolie_studio.domains.workspace import service as workspace


def test_log_schedule_uses_one_minute_for_legacy_null_interval():
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    log_source = SimpleNamespace(
        source_kind="logs",
        sync_interval_minutes=None,
        last_scheduled_sync_at=now - timedelta(minutes=2),
    )
    assert _is_due(log_source, now) is True


def test_source_refresh_guard_rejects_overlap_and_releases_afterwards():
    with source_refresh_guard(987654) as first:
        assert first is True
        with source_refresh_guard(987654) as overlapping:
            assert overlapping is False

    with source_refresh_guard(987654) as after_release:
        assert after_release is True


def test_queued_initialization_exposes_persisted_operation_phase():
    session, source = _sync_session()
    try:
        job_id = queue_source_initializations(session, [source])[0]

        validating = source_sync_status(session, source)
        assert validating["status"] == "running"
        assert validating["active_operation"] == "validate"
        assert validating["latest_job"]["id"] == job_id

        job = session.get(SyncJob, job_id)
        assert job is not None
        job.message = "Source is readable; syncing cache"
        job.result_json = json.dumps({"active_operation": "sync"})
        session.commit()

        syncing = source_sync_status(session, source)
        assert syncing["status"] == "running"
        assert syncing["active_operation"] == "sync"
    finally:
        session.close()


def test_metadata_initialization_starts_as_sync_without_validation_phase():
    session, source = _sync_session(source_kind="metadata")
    try:
        job_id = queue_source_initializations(session, [source])[0]

        status = source_sync_status(session, source)
        assert status["status"] == "running"
        assert status["active_operation"] == "sync"
        assert status["latest_job"]["id"] == job_id
        assert status["latest_job"]["message"] == "Waiting to sync discovered source"
    finally:
        session.close()


def test_adaptive_source_check_cadence_and_change_reset():
    policy = {
        "source_check_mode": "adaptive",
        "source_check_interval_seconds": 30,
        "source_check_max_interval_seconds": 300,
    }

    assert observation_delay_seconds(
        policy, unchanged_streak=0, failure_streak=0
    ) == 30
    assert observation_delay_seconds(
        policy, unchanged_streak=1, failure_streak=0
    ) == 60
    assert observation_delay_seconds(
        policy, unchanged_streak=2, failure_streak=0
    ) == 300
    assert observation_delay_seconds(
        {**policy, "source_check_mode": "fixed"},
        unchanged_streak=20,
        failure_streak=0,
    ) == 30


def test_due_source_check_lease_prevents_second_claim():
    session, source = _sync_session(storage_provider="s3")
    now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    try:
        reset_observation(session, source.id, due_at=now)
        session.commit()

        first_owner, first = claim_due_observation_ids(
            session, now=now, lease_owner="first"
        )
        second_owner, second = claim_due_observation_ids(
            session, now=now, lease_owner="second"
        )

        assert first_owner == "first"
        assert first == [source.id]
        assert second_owner == "second"
        assert second == []

        complete_observation(
            session,
            result=ObservationResult(
                source_id=source.id,
                source_kind=source.source_kind,
                outcome="unchanged",
                pending_changes=False,
                observed_revision=None,
                error=None,
                inventory_metrics=None,
                started_at=now,
                completed_at=now,
            ),
            lease_owner=first_owner,
            policy={
                "source_check_mode": "adaptive",
                "source_check_interval_seconds": 30,
                "source_check_max_interval_seconds": 300,
            },
        )
        state = session.get(SourceObservation, source.id)
        assert state is not None
        assert state.unchanged_streak == 1
        assert state.lease_owner is None
        next_check_at = state.next_observation_at.replace(
            tzinfo=state.next_observation_at.tzinfo or timezone.utc
        )
        assert next_check_at > now
    finally:
        session.close()


def test_periodic_claim_includes_local_logs_but_excludes_local_metadata():
    session, source = _sync_session()
    now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    try:
        metadata = EnvironmentSource(
            environment_id=source.environment_id,
            source_kind="metadata",
            uri="/tmp/metadata.json",
            storage_provider="local",
        )
        session.add(metadata)
        session.flush()
        reset_observation(session, source.id, due_at=now)
        reset_observation(session, metadata.id, due_at=now)
        session.commit()

        _, claimed = claim_due_observation_ids(session, now=now)

        assert claimed == [source.id]
    finally:
        session.close()


def test_observation_error_preserves_pending_and_skipped_preserves_evidence():
    session, source = _sync_session(storage_provider="s3")
    now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    policy = {
        "source_check_mode": "adaptive",
        "source_check_interval_seconds": 30,
        "source_check_max_interval_seconds": 300,
    }
    try:
        reset_observation(session, source.id, due_at=now, pending_changes=True)
        session.commit()
        owner, claimed = claim_due_observation_ids(
            session, now=now, lease_owner="error-owner"
        )
        assert claimed == [source.id]
        complete_observation(
            session,
            result=ObservationResult(
                source_id=source.id,
                source_kind=source.source_kind,
                outcome="error",
                pending_changes=None,
                observed_revision=None,
                error={"code": "storage_access_failed", "message": "denied"},
                inventory_metrics=None,
                started_at=now,
                completed_at=now,
            ),
            lease_owner=owner,
            policy=policy,
        )
        state = session.get(SourceObservation, source.id)
        assert state is not None
        assert state.pending_changes is True
        assert state.last_outcome == "error"

        state.lease_owner = "skip-owner"
        state.lease_expires_at = now + timedelta(minutes=1)
        session.commit()
        complete_observation(
            session,
            result=ObservationResult(
                source_id=source.id,
                source_kind=source.source_kind,
                outcome="skipped",
                pending_changes=None,
                observed_revision=None,
                error=None,
                inventory_metrics=None,
                started_at=now,
                completed_at=now,
            ),
            lease_owner="skip-owner",
            policy=policy,
        )
        state = session.get(SourceObservation, source.id)
        assert state is not None
        assert state.pending_changes is True
        assert state.last_outcome == "error"
        assert state.lease_owner is None
    finally:
        session.close()


def test_environment_local_observation_does_not_touch_cloud_sources(tmp_path):
    session, source = _sync_session()
    try:
        metadata_path = tmp_path / "metadata.json"
        metadata_path.write_text(
            '{"connections": [], "dataflows": [], "schema_hints": []}',
            encoding="utf-8",
        )
        source.source_kind = "metadata"
        source.uri = str(metadata_path)
        cloud = EnvironmentSource(
            environment_id=source.environment_id,
            source_kind="logs",
            uri="s3://private-bucket/logs",
            storage_provider="s3",
            storage_auth_mode="ambient",
        )
        session.add(cloud)
        session.flush()
        reset_observation(session, source.id)
        reset_observation(session, cloud.id)
        session.commit()

        result = observe_environment_local_sources(session, source.environment_id)

        assert result["total"] == 1
        assert result["observed"] == 1
        assert result["changed"] == 1
        assert result["failed"] == 0
        assert source_sync_status(session, source)["last_observed_at"] is not None
        assert source_sync_status(session, source)["next_check_at"] is None
        cloud_state = session.get(SourceObservation, cloud.id)
        assert cloud_state is not None
        assert cloud_state.last_attempted_at is None
    finally:
        session.close()


def test_database_rejects_second_running_job_for_same_source():
    session, source = _sync_session()
    try:
        begin_sync_job(session, source, "manual_refresh")

        with pytest.raises(SyncJobOverlapError, match="already running"):
            begin_sync_job(session, source, "scheduled_refresh")

        assert session.scalar(
            select(func.count(SyncJob.id)).where(
                SyncJob.source_id == source.id,
                SyncJob.status == "running",
            )
        ) == 1
    finally:
        session.close()


def test_sources_workspace_query_count_is_constant_with_source_count():
    session, source = _sync_session()
    engine = session.get_bind()

    def select_count() -> int:
        statements: list[str] = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            session.expire_all()
            workspace.sources_workspace(session, source.environment_id)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        return len(statements)

    try:
        one_source_queries = select_count()
        session.add_all(
            [
                EnvironmentSource(
                    environment_id=source.environment_id,
                    source_kind=("metadata", "code", "logs")[index % 3],
                    uri=f"source-{index}",
                    storage_provider="local",
                )
                for index in range(12)
            ]
        )
        session.commit()

        many_source_queries = select_count()

        assert one_source_queries == many_source_queries
        assert many_source_queries <= 6
    finally:
        session.close()


def test_running_job_index_migration_closes_older_duplicates():
    session, source = _sync_session()
    engine = session.get_bind()
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP INDEX uq_sync_jobs_running_source"))
        session.add_all([
            SyncJob(
                environment_id=source.environment_id,
                source_id=source.id,
                source_kind="logs",
                job_type="manual_refresh",
                status="running",
                started_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            ),
            SyncJob(
                environment_id=source.environment_id,
                source_id=source.id,
                source_kind="logs",
                job_type="scheduled_refresh",
                status="running",
                started_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
            ),
        ])
        session.commit()

        _ensure_sync_job_running_index(engine)
        session.expire_all()

        jobs = list(session.scalars(select(SyncJob).order_by(SyncJob.started_at)))
        assert [job.status for job in jobs] == ["failed", "running"]
        assert any(
            index["name"] == "uq_sync_jobs_running_source" and index["unique"]
            for index in inspect(engine).get_indexes("sync_jobs")
        )
    finally:
        session.close()


def _sync_session(
    *,
    storage_provider: str = "local",
    source_kind: str = "logs",
) -> tuple[Session, EnvironmentSource]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    project = Project(name="scheduler")
    session.add(project)
    session.flush()
    environment = Environment(project_id=project.id, name="dev")
    session.add(environment)
    session.flush()
    source = EnvironmentSource(
        environment_id=environment.id,
        source_kind=source_kind,
        uri="logs",
        storage_provider=storage_provider,
    )
    session.add(source)
    session.commit()
    return session, source
