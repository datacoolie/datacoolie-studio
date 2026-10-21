from datetime import datetime, timedelta, timezone

import pytest


def _item_spec(target_uri: str):
    from datacoolie_studio.domains.metadata.write_operations import WriteItemSpec

    return WriteItemSpec(
        kind="metadata",
        source_id=None,
        source_name="metadata",
        source_uri=target_uri,
        target_uri=target_uri,
        target_descriptor=None,
        binding_snapshot={"provider": "local"},
        expected_revision=None,
        expected_content_hash=None,
        expected_size=None,
        expected_provider_revision=None,
        backup_ref=None,
        backup_hash=None,
        backup_size=None,
        payload_ref="objects/aa/payload",
        payload_hash="a" * 64,
        payload_size=7,
    )


def test_write_operation_state_machine_and_environment_slot(studio_paths):
    from datacoolie_studio.db.models import Environment, Project
    from datacoolie_studio.db.session import create_session, init_db
    from datacoolie_studio.domains.metadata.write_operations import (
        WriteOperationState,
        WriteOperationTransitionError,
        active_operation,
        create_operation,
        operation_items,
        transition_item,
        transition_operation,
        WriteItemState,
    )

    init_db()
    db_session = create_session()
    project = Project(name="write-saga-project")
    environment = Environment(name="dev", project=project)
    db_session.add(project)
    db_session.flush()
    operation = create_operation(
        db_session,
        environment_id=environment.id,
        editor_fingerprint="e" * 64,
        plan_fingerprint="p" * 64,
        items=[_item_spec("metadata.json")],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    assert active_operation(db_session, environment.id).id == operation.id
    item = operation_items(db_session, operation.id)[0]
    transition_operation(operation, WriteOperationState.APPLYING)
    transition_item(item, WriteItemState.APPLYING)
    transition_item(item, WriteItemState.APPLIED)
    transition_operation(operation, WriteOperationState.SUCCEEDED)
    db_session.commit()

    assert active_operation(db_session, environment.id) is None
    with pytest.raises(WriteOperationTransitionError):
        transition_operation(operation, WriteOperationState.APPLYING)
    db_session.close()


def test_raced_environment_slot_conflict_rolls_back_savepoint_only(studio_paths, monkeypatch):
    from sqlalchemy import select
    from datacoolie_studio.db.models import Environment, Project, MetadataWriteOperation
    from datacoolie_studio.db.session import create_session, init_db
    from datacoolie_studio.domains.metadata.errors import MetadataConflictError
    from datacoolie_studio.domains.metadata.write_operations import create_operation

    init_db()
    session = create_session()
    environment = Environment(name="dev", project=Project(name="slot-race"))
    session.add(environment)
    session.flush()
    arguments = dict(environment_id=environment.id, editor_fingerprint="e" * 64,
                     plan_fingerprint="p" * 64, items=[_item_spec("metadata.json")],
                     expires_at=datetime.now(timezone.utc) + timedelta(hours=24))
    first = create_operation(session, **arguments)
    session.commit()
    actual_scalar = session.scalar
    missed = False
    def miss_precheck(statement, *args, **kwargs):
        nonlocal missed
        if not missed:
            missed = True
            return None  # The owner became visible after the optimistic check.
        return actual_scalar(statement, *args, **kwargs)
    monkeypatch.setattr(session, "scalar", miss_precheck)
    with pytest.raises(MetadataConflictError, match="already owns"):
        create_operation(session, **arguments)
    assert session.is_active
    assert session.scalar(select(MetadataWriteOperation.operation_key)) == first.operation_key
    environment.name = "still-usable"
    session.commit()
    assert session.get(Environment, environment.id).name == "still-usable"
    session.close()
