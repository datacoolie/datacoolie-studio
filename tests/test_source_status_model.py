import json
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import (
    Base,
    Environment,
    EnvironmentSource,
    LogFileManifest,
    MetadataMaterialization,
    Project,
    SourceObservation,
    SyncJob,
)
from datacoolie_studio.db.session import _backfill_last_successful_sync_at
from datacoolie_studio.domains.freshness.service import source_freshness_statuses
from datacoolie_studio.domains.sync.service import (
    finish_sync_job,
    source_sync_status,
)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _status_session() -> tuple[Session, EnvironmentSource]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    project = Project(name="status")
    session.add(project)
    session.flush()
    environment = Environment(project_id=project.id, name="dev")
    session.add(environment)
    session.flush()
    source = EnvironmentSource(
        environment_id=environment.id,
        source_kind="metadata",
        uri="metadata.json",
        storage_provider="local",
    )
    session.add(source)
    session.commit()
    return session, source


def test_successful_qualifying_sync_persists_last_success_but_other_jobs_do_not():
    session, source = _status_session()
    first_success = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
    later_failure = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)
    try:
        successful = SyncJob(
            environment_id=source.environment_id,
            source_id=source.id,
            source_kind=source.source_kind,
            job_type="manual_refresh",
            status="running",
            started_at=first_success,
        )
        session.add(successful)
        session.commit()
        finish_sync_job(
            session,
            successful,
            status="succeeded",
            message="Synced",
            result={"status": "ok"},
            completed_at=first_success,
        )
        assert _utc(source.last_successful_sync_at) == first_success

        failed = SyncJob(
            environment_id=source.environment_id,
            source_id=source.id,
            source_kind=source.source_kind,
            job_type="force_refresh",
            status="running",
            started_at=later_failure,
        )
        session.add(failed)
        session.commit()
        finish_sync_job(
            session,
            failed,
            status="failed",
            message="Failed",
            result={"status": "error"},
            completed_at=later_failure,
        )
        assert _utc(source.last_successful_sync_at) == first_success

        maintenance = SyncJob(
            environment_id=source.environment_id,
            source_id=source.id,
            source_kind=source.source_kind,
            job_type="analytics_upgrade",
            status="running",
            started_at=later_failure,
        )
        session.add(maintenance)
        session.commit()
        finish_sync_job(
            session,
            maintenance,
            status="succeeded",
            message="Maintenance complete",
            result={"status": "ok"},
            completed_at=later_failure,
        )
        assert _utc(source.last_successful_sync_at) == first_success

        skipped = SyncJob(
            environment_id=source.environment_id,
            source_id=source.id,
            source_kind=source.source_kind,
            job_type="manual_refresh",
            status="skipped",
            message="Skipped because the cache is already current",
            started_at=later_failure,
            completed_at=later_failure,
        )
        session.add(skipped)
        session.commit()
        assert source_sync_status(session, source)["sync_execution"]["state"] == "failed"
    finally:
        session.close()


def test_status_contract_keeps_validation_check_and_sync_independent():
    session, source = _status_session()
    checked_at = datetime(2026, 8, 18, 11, 0, tzinfo=timezone.utc)
    synced_at = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)
    try:
        source.read_check_status = "ok"
        source.read_checked_at = checked_at
        source.read_check_result_json = json.dumps({"status": "ok", "message": "Readable"})
        source.last_successful_sync_at = synced_at
        session.add(
            SourceObservation(
                source_id=source.id,
                last_outcome="changed",
                pending_changes=True,
                last_attempted_at=checked_at,
                last_succeeded_at=checked_at,
            )
        )
        session.commit()

        status = source_sync_status(session, source)

        assert status["validation"]["state"] == "ready"
        assert status["validation"]["completed_at"] == checked_at
        assert status["observation"]["state"] == "changed"
        assert status["observation"]["checked_at"] == checked_at
        assert status["sync_execution"]["state"] == "succeeded"
        assert status["sync_execution"]["last_successful_at"] == synced_at

        observation = session.query(SourceObservation).filter_by(source_id=source.id).one()
        observation.last_outcome = "unchanged"
        session.commit()

        status = source_sync_status(session, source)

        assert status["observation"]["state"] == "changed"

        observation.pending_changes = False
        session.commit()

        status = source_sync_status(session, source)

        assert status["observation"]["state"] == "unchanged"
    finally:
        session.close()


def test_freshness_uses_last_successful_sync_instead_of_observation_time():
    session, source = _status_session()
    checked_at = datetime(2026, 8, 18, 11, 0, tzinfo=timezone.utc)
    synced_at = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)
    try:
        source.last_successful_sync_at = synced_at
        session.add(
            SourceObservation(
                source_id=source.id,
                last_outcome="unchanged",
                last_attempted_at=checked_at,
                last_succeeded_at=checked_at,
            )
        )
        session.commit()

        items, _ = source_freshness_statuses(session, [source])

        assert items[source.id]["cache_synced_at"] == synced_at
    finally:
        session.close()


def test_initial_validation_failure_does_not_appear_as_a_sync_failure():
    session, source = _status_session()
    started_at = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    try:
        job = SyncJob(
            environment_id=source.environment_id,
            source_id=source.id,
            source_kind=source.source_kind,
            job_type="initial_refresh",
            status="running",
            started_at=started_at,
            result_json=json.dumps({"active_operation": "validate"}),
        )
        session.add(job)
        session.commit()

        finish_sync_job(
            session,
            job,
            status="failed",
            message="Source validation failed",
            result={
                "status": "error",
                "message": "Source validation failed",
                "active_operation": "validate",
            },
            completed_at=started_at,
        )

        assert source_sync_status(session, source)["sync_execution"]["state"] == "never"
    finally:
        session.close()


def test_backfill_prefers_successful_jobs_then_materialization_or_manifest_fallback():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    project = Project(name="backfill")
    session.add(project)
    session.flush()
    environment = Environment(project_id=project.id, name="dev")
    session.add(environment)
    session.flush()
    job_source = EnvironmentSource(
        environment_id=environment.id,
        source_kind="metadata",
        uri="metadata.json",
        storage_provider="local",
    )
    materialized_source = EnvironmentSource(
        environment_id=environment.id,
        source_kind="metadata",
        uri="metadata-legacy.json",
        storage_provider="local",
    )
    log_source = EnvironmentSource(
        environment_id=environment.id,
        source_kind="logs",
        uri="logs",
        storage_provider="local",
    )
    session.add_all([job_source, materialized_source, log_source])
    session.flush()
    successful_at = datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc)
    session.add_all(
        [
            SyncJob(
                environment_id=environment.id,
                source_id=job_source.id,
                source_kind="metadata",
                job_type="manual_refresh",
                status="succeeded",
                started_at=successful_at,
                completed_at=successful_at,
            ),
            MetadataMaterialization(
                source_id=materialized_source.id,
                source_revision_json="{}",
                normalizer_version="test",
                materialization_fingerprint="materialized",
                editor_document_json="{}",
                materialized_at=successful_at,
            ),
            LogFileManifest(
                source_id=log_source.id,
                file_uri="logs/file.jsonl",
                file_kind="job",
                revision_json="{}",
                status="ok",
                first_seen_at=successful_at,
                last_seen_at=successful_at,
            ),
        ]
    )
    session.commit()
    try:
        _backfill_last_successful_sync_at(engine)
        session.expire_all()
        assert _utc(session.get(EnvironmentSource, job_source.id).last_successful_sync_at) == successful_at
        assert _utc(session.get(EnvironmentSource, materialized_source.id).last_successful_sync_at) == successful_at
        assert _utc(session.get(EnvironmentSource, log_source.id).last_successful_sync_at) == successful_at
    finally:
        session.close()
