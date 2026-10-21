from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _prepared_operation(tmp_path: Path, studio_paths):
    from datacoolie_studio.db.models import Environment, Project
    from datacoolie_studio.db.session import create_session, init_db
    from datacoolie_studio.domains.metadata.service import (
        ensure_metadata_materialization,
        load_environment_editor_workspace,
    )
    from datacoolie_studio.domains.metadata.write_executor import (
        prepare_environment_write,
    )
    from datacoolie_studio.domains.sources.persistence import create_source

    init_db()
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps({"connections": [{"name": "lake"}], "dataflows": [], "schema_hints": []}),
        encoding="utf-8",
    )
    session = create_session()
    project = Project(name="reconciliation-project")
    session.add(project)
    session.flush()
    environment = Environment(project_id=project.id, name="dev")
    session.add(environment)
    session.flush()
    source = create_source(
        session,
        environment.id,
        source_kind="metadata",
        uri=str(metadata_path),
        label="metadata",
        enabled=True,
    )
    session.commit()
    ensure_metadata_materialization(session, source, force=True)
    document = load_environment_editor_workspace(session, environment.id, [source])["document"]
    document["sheets"]["connections"]["rows"][0]["name"] = "warehouse"
    operation = prepare_environment_write(
        session,
        environment.id,
        [source],
        document,
        document["source"]["revision"],
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    return session, environment, metadata_path, operation


def test_reconciliation_defers_unconfirmed_and_resumes_confirmed_operation(
    studio_paths,
    tmp_path: Path,
):
    from datacoolie_studio.domains.metadata.reconciliation import (
        reconcile_metadata_write_operations,
    )
    from datacoolie_studio.domains.metadata.write_operations import (
        WriteOperationState,
        transition_operation,
    )

    session, _environment, metadata_path, operation = _prepared_operation(
        tmp_path,
        studio_paths,
    )
    before = metadata_path.read_bytes()
    current = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)

    deferred = reconcile_metadata_write_operations(session, now=current)
    assert deferred == {"resumed": 0, "expired": 0, "deferred": 1, "errors": 0}
    assert operation.state == WriteOperationState.PREPARED.value
    assert metadata_path.read_bytes() == before

    transition_operation(operation, WriteOperationState.APPLYING, now=current)
    operation.confirmed_at = current
    session.commit()
    resumed = reconcile_metadata_write_operations(session, now=current)
    assert resumed == {"resumed": 1, "expired": 0, "deferred": 0, "errors": 0}
    assert operation.state == WriteOperationState.SUCCEEDED.value
    assert b"warehouse" in metadata_path.read_bytes()
    session.close()


def test_reconciliation_expires_stale_unconfirmed_operation_without_writing(
    studio_paths,
    tmp_path: Path,
):
    from datacoolie_studio.domains.metadata.artifact_store import MetadataArtifactStore
    from datacoolie_studio.domains.metadata.reconciliation import (
        reconcile_metadata_write_operations,
    )
    from datacoolie_studio.domains.metadata.write_operations import (
        WriteOperationState,
        operation_items,
    )

    session, _environment, metadata_path, operation = _prepared_operation(
        tmp_path,
        studio_paths,
    )
    item = operation_items(session, operation.id)[0]
    payload_ref = item.payload_ref
    before = metadata_path.read_bytes()
    expired = reconcile_metadata_write_operations(
        session,
        now=operation.expires_at + timedelta(seconds=1),
    )
    assert expired == {"resumed": 0, "expired": 1, "deferred": 0, "errors": 0}
    assert operation.state == WriteOperationState.EXPIRED.value
    assert metadata_path.read_bytes() == before
    try:
        MetadataArtifactStore().read(
            payload_ref,
            expected_sha256=item.payload_hash or "",
            expected_size=item.payload_size or 0,
        )
    except Exception:
        pass
    else:
        raise AssertionError("Expired operation payload was not cleaned up")
    session.close()
