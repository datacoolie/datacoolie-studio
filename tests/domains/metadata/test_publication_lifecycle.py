"""Regression checks for publication ownership and durable checkpoints."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import func, select

from datacoolie_studio.db.models import MetadataBackup, MetadataWriteOperation
from datacoolie_studio.db.session import create_session
from datacoolie_studio.domains.metadata import write_operations
from datacoolie_studio.domains.metadata.errors import MetadataConflictError
from datacoolie_studio.domains.metadata.write_executor import (
    abandon_environment_write,
    apply_environment_write,
    prepare_environment_write,
)
from tests.domains.metadata.test_boundary_remediation import editor as editor

from datacoolie_studio.domains.metadata.backups import delete_backup, read_backup_bytes
from datacoolie_studio.domains.metadata.artifact_store import MetadataArtifactStore


def prepare(editor):
    session, environment, sources, document = editor
    document["sheets"]["connections"]["rows"][0]["description"] = "changed"
    operation = prepare_environment_write(
        session, environment.id, sources, document, document["source"]["revision"]
    )
    return operation.operation_key, environment.id


def test_concurrent_apply_is_busy_then_stale_session_returns_terminal(editor, monkeypatch):
    session = editor[0]
    key, environment_id = prepare(editor)
    second_session = create_session()
    stale = write_operations.operation_for_key(second_session, key)
    entered, release = Event(), Event()
    original = write_operations.operation_for_key

    def delayed(db, *args, **kwargs):
        result = original(db, *args, **kwargs)
        if db is not second_session and not entered.is_set():
            entered.set()
            assert release.wait(10)
        return result

    def run():
        db = create_session()
        try:
            return apply_environment_write(db, key, environment_id=environment_id, confirm=True).state
        finally:
            db.close()

    monkeypatch.setattr(write_operations, "operation_for_key", delayed)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run)
            assert entered.wait(10)
            try:
                with pytest.raises(MetadataConflictError, match="busy"):
                    apply_environment_write(second_session, key, environment_id=environment_id, confirm=True)
                with pytest.raises(MetadataConflictError, match="busy"):
                    abandon_environment_write(second_session, key, environment_id=environment_id)
            finally:
                release.set()
            assert future.result(timeout=10) == "succeeded"
        assert stale.state == "prepared"
        assert apply_environment_write(second_session, key, environment_id=environment_id, confirm=True).state == "succeeded"
        session.expire_all()
        assert session.scalar(select(MetadataWriteOperation.state)) == "succeeded"
        assert session.scalar(select(func.count(MetadataBackup.id))) == 1
    finally:
        release.set()
        second_session.close()


def test_abandon_repeat_is_terminal_and_guard_releases(editor):
    key, environment_id = prepare(editor)
    session = editor[0]
    first = abandon_environment_write(session, key, environment_id=environment_id)
    assert first.state == "abandoned"
    assert abandon_environment_write(session, key, environment_id=environment_id).state == "abandoned"
    with write_operations.environment_publication_guard(session, environment_id):
        with write_operations.environment_publication_guard(session, environment_id):
            assert first.state == "abandoned"


def test_publication_lock_survives_process_contention_and_releases_on_exit(editor):
    import os
    import subprocess
    import sys
    from datacoolie_studio.db.session import workspace_lock_path

    session, environment, *_ = editor
    path = workspace_lock_path(session, f"environment-{environment.id}")
    program = "from pathlib import Path; import sys; from datacoolie_studio.core.process_lock import reentrant_process_lock\nwith reentrant_process_lock(Path(sys.argv[1]), owner_label='test'):\n print('ready', flush=True)\n sys.stdin.read()"
    child = subprocess.Popen([sys.executable, "-c", program, str(path)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(MetadataConflictError, match="busy"):
            with write_operations.environment_publication_guard(session, environment.id):
                pytest.fail("foreign process owns guard")
        with write_operations.environment_publication_guard(session, environment.id + 1):
            pass
    finally:
        child.terminate()
        child.communicate(timeout=10)
    with write_operations.environment_publication_guard(session, environment.id):
        pass


def test_backup_rollback_keeps_blob(editor, monkeypatch):
    key, environment_id = prepare(editor)
    session = editor[0]
    assert apply_environment_write(session, key, environment_id=environment_id, confirm=True).state == "succeeded"
    backup = session.scalar(select(MetadataBackup))
    expected = read_backup_bytes(backup)
    original = session.commit
    monkeypatch.setattr(session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("commit failed")))
    with pytest.raises(RuntimeError, match="commit failed"):
        delete_backup(session, backup.id)
    session.rollback()
    monkeypatch.setattr(session, "commit", original)
    assert read_backup_bytes(session.get(MetadataBackup, backup.id)) == expected


def test_prepare_commit_failure_does_not_publish_reference_checkpoint(editor, monkeypatch):
    from datacoolie_studio.domains.metadata.backups import cleanup_orphaned_metadata_artifacts

    session = editor[0]
    original = session.commit
    monkeypatch.setattr(session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("commit failed")))
    with pytest.raises(RuntimeError, match="commit failed"):
        prepare(editor)
    session.rollback()
    monkeypatch.setattr(session, "commit", original)
    with create_session() as committed:
        assert committed.scalar(select(func.count(MetadataWriteOperation.id))) == 0
    assert cleanup_orphaned_metadata_artifacts(session) > 0


def test_cleanup_failure_keeps_succeeded(editor, monkeypatch):
    key, environment_id = prepare(editor)
    session = editor[0]
    monkeypatch.setattr(MetadataArtifactStore, "remove", lambda *args: (_ for _ in ()).throw(OSError("offline")))
    operation = apply_environment_write(session, key, environment_id=environment_id, confirm=True)
    assert operation.state == "succeeded"
    assert operation.error_code is None
    assert read_backup_bytes(session.scalar(select(MetadataBackup)))


def test_artifact_root_rejects_other_database_and_defers_unclaimed(editor, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    session = editor[0]
    store = MetadataArtifactStore(tmp_path / "claimed")
    with store.lifecycle(session, staging=True):
        artifact = store.put(b"shared")
    engine = create_engine(f"sqlite:///{(tmp_path / 'other.db').as_posix()}")
    try:
        with Session(engine) as other:
            with pytest.raises(RuntimeError, match="another workspace"):
                with store.lifecycle(other, staging=True):
                    store.put(b"foreign")
        assert store.read(artifact.ref, expected_sha256=artifact.sha256, expected_size=artifact.size) == b"shared"
        unclaimed = MetadataArtifactStore(tmp_path / "unclaimed")
        unclaimed.put(b"old")
        with unclaimed.lifecycle(session, staging=True) as owned:
            assert owned is False
            unclaimed.put(b"new")
    finally:
        engine.dispose()


def test_registration_effective_hosts_and_legacy_rekey(editor):
    import json
    from datacoolie_studio.db.models import CredentialProfile
    from datacoolie_studio.domains.sources import application

    session, environment, *_ = editor
    for key in ["a", "b", "a2"]:
        session.add(CredentialProfile(id=key, name=key, provider="dbfs", auth_type="pat",
            config_json=json.dumps({"host": f"https://{'a' if key == 'a2' else key}.example"}),
            secret_state="present"))
    session.commit()

    def create(key):
        return application.create_sql_source(session, environment.id, uri="dbfs:/queries",
            label=None, enabled=True, storage={"provider": "dbfs", "auth_mode": "credential_profile",
            "credential_profile_id": key}).source

    first, second = create("a"), create("b")
    assert first.id != second.id
    assert create("a2").id == first.id
    source_id, registration_id = first.id, first.registration_id
    first.registration.identity_key = "0" * 64
    session.commit()
    assert create("a").id == source_id
    assert first.registration_id == registration_id
    assert first.registration.identity_key != "0" * 64


@pytest.mark.parametrize("kind", ["metadata", "restore"])
def test_metadata_profile_host_change_conflicts_before_factory(editor, monkeypatch, kind):
    import json
    from datacoolie_studio.db.models import CredentialProfile
    from datacoolie_studio.domains.metadata import service, write_executor
    from datacoolie_studio.domains.metadata.storage_io import MetadataStorage
    from datacoolie_studio.domains.sources.persistence import create_source
    from datacoolie_studio.domains.credentials.service import update_profile
    from tests.support.credentials import MemorySecretStore
    from tests.domains.metadata.test_metadata_cloud_storage import _MemoryAdapter, _MemoryWriter, _metadata_json

    session, environment, *_ = editor
    profile = CredentialProfile(id="p", name="p", provider="dbfs", auth_type="pat",
        config_json=json.dumps({"host": "https://a.example"}), secret_ref="p", secret_state="present")
    session.add(profile)
    session.commit()
    source = create_source(session, environment.id, source_kind="metadata", uri="dbfs:/metadata.json",
        label=None, enabled=True, storage={"provider": "dbfs", "auth_mode": "credential_profile", "credential_profile_id": "p"})
    session.commit()
    store = MemorySecretStore()
    store.set("p", {"token": "synthetic"})
    adapter = _MemoryAdapter(_metadata_json("before"))
    storage = MetadataStorage(adapter, _MemoryWriter(adapter))
    monkeypatch.setattr(service, "storage_for_source", lambda *a, **kw: storage)
    monkeypatch.setattr(write_executor, "storage_for_source", lambda *a, **kw: storage)
    service.ensure_metadata_materialization(session, source, force=True, secret_store=store)
    doc = service.load_environment_editor_workspace(session, environment.id, [source], secret_store=store)["document"]
    doc["sheets"]["connections"]["rows"][0]["description"] = "after"
    if kind == "metadata":
        op = prepare_environment_write(session, environment.id, [source], doc, doc["source"]["revision"], secret_store=store)
    else:
        from datacoolie_studio.domains.metadata.write_executor import prepare_backup_restore
        from datacoolie_studio.domains.metadata.revisions import environment_revision_sources

        artifacts = MetadataArtifactStore()
        with artifacts.lifecycle(session, staging=True):
            artifact = artifacts.put(_metadata_json("restored"))
            backup = MetadataBackup(project_id=environment.project_id, environment_id=environment.id,
                source_id=source.id, source_uri=source.uri, backup_path=artifact.ref,
                backup_ref=artifact.ref, backup_hash=artifact.sha256, backup_size=artifact.size)
            session.add(backup)
            session.commit()
        op = prepare_backup_restore(session, backup.id,
            expected_revision=environment_revision_sources(doc["source"]["revision"])[source.id], secret_store=store)
    update_profile(session, "p", name=None, config={"host": "https://b.example"}, secret=None, secret_store=store)
    monkeypatch.setattr(write_executor, "storage_for_source", lambda *a, **kw: pytest.fail("factory opened changed backend"))
    result = apply_environment_write(session, op.operation_key, environment_id=environment.id, confirm=True, secret_store=store)
    assert result.state == "failed"
    assert write_operations.operation_items(session, op.id)[0].state == "conflict"
    assert adapter.data == _metadata_json("before")


def test_project_reimport_active_source_is_atomic(editor, tmp_path):
    import json
    from datacoolie_studio.db.models import EnvironmentSource, SourceRegistration
    from datacoolie_studio.domains.sources import application
    from datacoolie_studio.domains.metadata import service

    session, environment, *_ = editor
    project = tmp_path / "project"
    for name in ["a", "b"]:
        directory = project / name
        directory.mkdir(parents=True)
        (directory / "metadata.json").write_text(json.dumps({"connections": [{"name": name}], "dataflows": [], "schema_hints": []}))
    imported = application.import_datacoolie_project_sources(session, environment.id, project_uri=str(project), metadata_subpath="a")
    source = session.get(EnvironmentSource, imported["created"][0]["id"])
    service.ensure_metadata_materialization(session, source, force=True)
    doc = service.load_environment_editor_workspace(session, environment.id, [source])["document"]
    doc["sheets"]["connections"]["rows"][0]["description"] = "prepared"
    prepare_environment_write(session, environment.id, [source], doc, doc["source"]["revision"])
    registrations = session.scalar(select(func.count(SourceRegistration.id)))
    with pytest.raises(write_operations.ActiveWriteOperationError):
        application.import_datacoolie_project_sources(session, environment.id, project_uri=str(project), metadata_subpath="b")
    session.refresh(source)
    assert source.enabled
    assert session.scalar(select(func.count(SourceRegistration.id))) == registrations


def test_profile_scope_collision_rolls_back_config_and_identity(editor):
    import json
    from datacoolie_studio.db.models import CredentialProfile
    from datacoolie_studio.domains.sources import application
    from datacoolie_studio.domains.credentials.service import update_profile, CredentialProfileConflict
    from tests.support.credentials import MemorySecretStore

    session, environment, *_ = editor
    for key in ["a", "b"]:
        session.add(CredentialProfile(id=key, name=key, provider="dbfs", auth_type="pat",
            config_json=json.dumps({"host": f"https://{key}.example"}), secret_ref=key, secret_state="present"))
    session.commit()
    sources = [application.create_sql_source(session, environment.id, uri="dbfs:/queries", label=None, enabled=True,
        storage={"provider": "dbfs", "auth_mode": "credential_profile", "credential_profile_id": key}).source for key in ["a", "b"]]
    keys = [source.registration.identity_key for source in sources]
    with pytest.raises(CredentialProfileConflict, match="collid"):
        update_profile(session, "a", name=None, config={"host": "https://b.example"}, secret=None, secret_store=MemorySecretStore())
    assert json.loads(session.get(CredentialProfile, "a").config_json)["host"] == "https://a.example"
    assert [source.registration.identity_key for source in sources] == keys


def test_source_delete_rollback_retains_backup(editor, monkeypatch):
    from datacoolie_studio.domains.sources import application

    key, environment_id = prepare(editor)
    session, _, sources, _ = editor
    apply_environment_write(session, key, environment_id=environment_id, confirm=True)
    backup = session.scalar(select(MetadataBackup))
    backup_id, expected = backup.id, read_backup_bytes(backup)
    commit = session.commit
    monkeypatch.setattr(session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("db failed")))
    with pytest.raises(RuntimeError, match="db failed"):
        application.delete_metadata_source(session, environment_id, sources[0].id)
    monkeypatch.setattr(session, "commit", commit)
    assert read_backup_bytes(session.get(MetadataBackup, backup_id)) == expected


def test_artifact_staging_blocks_other_session_cleanup(editor, tmp_path):
    from datacoolie_studio.core.process_lock import ProcessLockTimeoutError

    session = editor[0]
    store = MetadataArtifactStore(tmp_path / "staging")
    def contender():
        other = create_session()
        try:
            # Root OS lock is held by another thread; nested ownership never leaks.
            from datacoolie_studio.core.process_lock import reentrant_process_lock
            with pytest.raises(ProcessLockTimeoutError):
                with reentrant_process_lock(store.root / ".lifecycle.lock", owner_label="artifact", timeout_seconds=0):
                    pytest.fail("foreign thread inherited staging ownership")
        finally:
            other.close()
    with store.lifecycle(session, staging=True):
        store.put(b"not yet referenced")
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(contender).result(timeout=10)


def test_profile_update_rechecks_new_environment_membership(editor, monkeypatch):
    import json
    from contextlib import contextmanager
    from datacoolie_studio.db.models import CredentialProfile, Environment
    from datacoolie_studio.domains.credentials import service as credentials
    from datacoolie_studio.domains.sources import application
    from tests.support.credentials import MemorySecretStore

    session, first_environment, *_ = editor
    second = Environment(name="second", project_id=first_environment.project_id)
    profile = CredentialProfile(id="p", name="p", provider="dbfs", auth_type="pat",
        config_json=json.dumps({"host": "https://a.example"}), secret_ref="p", secret_state="present")
    session.add_all([second, profile])
    session.commit()
    second_id = second.id
    original = credentials.credential_profile_guards
    entered = False

    @contextmanager
    def add_membership_before_profile_lock(db, profile_ids):
        nonlocal entered
        if not entered:
            entered = True
            other = create_session()
            try:
                application.create_sql_source(other, second_id, uri="dbfs:/queries", label=None, enabled=True,
                    storage={"provider": "dbfs", "auth_mode": "credential_profile", "credential_profile_id": "p"})
            finally:
                other.close()
        with original(db, profile_ids):
            yield

    monkeypatch.setattr(credentials, "credential_profile_guards", add_membership_before_profile_lock)
    credentials.update_profile(session, "p", name=None, config={"host": "https://b.example"}, secret=None,
        secret_store=MemorySecretStore())
    from datacoolie_studio.db.models import EnvironmentSource
    source = session.scalar(select(EnvironmentSource).where(EnvironmentSource.environment_id == second_id))
    assert source is not None
    assert json.loads(session.get(CredentialProfile, "p").config_json)["host"] == "https://b.example"
    old_identity = source.registration.identity_key
    duplicate = application.create_sql_source(session, second_id, uri="dbfs:/queries", label=None, enabled=True,
        storage={"provider": "dbfs", "auth_mode": "credential_profile", "credential_profile_id": "p"}).source
    assert duplicate.id == source.id
    assert source.registration.identity_key == old_identity


def test_artifact_cleanup_uses_committed_reads_after_root_lock(editor, monkeypatch):
    from datacoolie_studio.domains.metadata.backups import cleanup_orphaned_metadata_artifacts
    from datacoolie_studio.db.session import ensure_workspace_transaction

    session, environment, sources, _ = editor
    ensure_workspace_transaction(session)
    assert session.scalar(select(func.count(MetadataBackup.id))) == 0
    other = create_session()
    store = MetadataArtifactStore()
    try:
        with store.lifecycle(other, staging=True):
            artifact = store.put(b"new referenced bytes")
            other.add(MetadataBackup(project_id=environment.project_id, environment_id=environment.id,
                source_id=sources[0].id, source_uri=sources[0].uri, backup_path=artifact.ref,
                backup_ref=artifact.ref, backup_hash=artifact.sha256, backup_size=artifact.size))
            other.commit()
        assert session.scalar(select(func.count(MetadataBackup.id))) == 0
        assert cleanup_orphaned_metadata_artifacts(session) == 0
        assert store.read(artifact.ref, expected_sha256=artifact.sha256, expected_size=artifact.size) == b"new referenced bytes"
    finally:
        other.close()
        session.rollback()


def test_apply_ends_stale_wal_read_snapshot(editor):
    from datacoolie_studio.db.session import ensure_workspace_transaction

    key, environment_id = prepare(editor)
    with create_session() as stale, create_session() as current:
        ensure_workspace_transaction(stale)
        assert write_operations.operation_for_key(stale, key).state == "prepared"
        assert apply_environment_write(current, key, environment_id=environment_id, confirm=True).state == "succeeded"
        assert write_operations.operation_for_key(stale, key).state == "prepared"
        assert apply_environment_write(stale, key, environment_id=environment_id, confirm=True).state == "succeeded"


@pytest.mark.parametrize("flushed", [False, True])
def test_source_mutation_preserves_uncommitted_caller_work_on_custom_bind(editor, flushed):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from datacoolie_studio.db.models import Project
    from datacoolie_studio.domains.sources import application

    environment_id = editor[1].id
    engine = create_engine(editor[0].get_bind().url)
    try:
        with Session(engine) as session:
            pending = Project(name="caller-owned")
            session.add(pending)
            if flushed:
                session.flush()
            with pytest.raises(MetadataConflictError, match="pending|flushed"):
                application.create_sql_source(session, environment_id, uri="s3://bucket/queries", label=None, enabled=True)
            assert pending in session
            if not flushed:
                assert pending in session.new
            session.commit()
            assert session.scalar(select(Project.id).where(Project.name == "caller-owned")) == pending.id
    finally:
        engine.dispose()


@pytest.mark.parametrize("kind", ["metadata", "code", "logs"])
def test_storage_only_update_reconciles_registration(editor, kind):
    import json
    from datacoolie_studio.db.models import CredentialProfile
    from datacoolie_studio.domains.sources import application

    session, environment, *_ = editor
    for key in ["a", "b"]:
        session.add(CredentialProfile(id=key, name=key, provider="dbfs", auth_type="pat",
            config_json=json.dumps({"host": f"https://{key}.example"}), secret_state="present"))
    session.commit()
    create = {"metadata": application.create_metadata_source, "code": application.create_code_artifact,
              "logs": application.create_log_source}[kind]
    update = {"metadata": application.update_metadata_source, "code": application.update_code_artifact,
              "logs": application.update_log_source}[kind]
    def storage(key):
        return {"provider": "dbfs", "auth_mode": "credential_profile", "credential_profile_id": key}
    source = create(session, environment.id, uri="dbfs:/object", label=None, enabled=True, storage=storage("a")).source
    source_id = source.id
    update(session, environment.id, source_id, storage=storage("b"))
    another = create(session, environment.id, uri="dbfs:/object", label=None, enabled=True, storage=storage("a")).source
    assert another.id != source_id
    assert another.credential_profile_id == "a"
    assert source.credential_profile_id == "b"


def test_stale_identity_hash_never_reuses_different_scope(editor):
    import json
    from datacoolie_studio.db.models import CredentialProfile
    from datacoolie_studio.domains.sources import application
    from datacoolie_studio.domains.storage.errors import StorageConfigurationError

    session, environment, *_ = editor
    for key in ["a", "b"]:
        session.add(CredentialProfile(id=key, name=key, provider="dbfs", auth_type="pat",
            config_json=json.dumps({"host": f"https://{key}.example"}), secret_state="present"))
    session.commit()
    def storage(key):
        return {"provider": "dbfs", "auth_mode": "credential_profile", "credential_profile_id": key}
    first = application.create_metadata_source(session, environment.id, uri="dbfs:/object", label=None,
        enabled=True, storage=storage("a")).source
    first.credential_profile_id = "b"  # Simulate a legacy stale derived key.
    session.commit()
    with pytest.raises(StorageConfigurationError, match="collides"):
        application.create_metadata_source(session, environment.id, uri="dbfs:/object", label=None,
            enabled=True, storage=storage("a"))
    assert session.get(type(first), first.id).credential_profile_id == "b"


@pytest.mark.parametrize("change", ["root", "disable", "delete"])
def test_new_cloud_target_keeps_parent_ownership_and_rejects_changed_parent(editor, monkeypatch, change):
    import json
    from datetime import datetime, timezone
    from datacoolie_studio.domains.metadata.environment_documents import build_metadata_source_for_routing
    from datacoolie_studio.domains.metadata.publication_scope import metadata_descriptor
    from datacoolie_studio.domains.sources.persistence import create_source
    from datacoolie_studio.domains.sources.storage_binding import binding_to_dict
    from datacoolie_studio.domains.sources import application
    from datacoolie_studio.domains.metadata import write_executor

    session, environment, *_ = editor
    parent = create_source(session, environment.id, source_kind="metadata", uri="s3://bucket/a/parent.json",
        label=None, enabled=True, source_config={"metadata_root_uri": "s3://bucket/a"})
    session.commit()
    target = build_metadata_source_for_routing(session, environment.id, "new.json", [parent])
    descriptor = metadata_descriptor(session, target, descriptor=write_executor._new_source_descriptor(target))
    assert descriptor["storage_source_id"] == parent.id
    store = MetadataArtifactStore()
    with store.lifecycle(session, staging=True):
        artifact = store.put(b'{"connections":[],"dataflows":[],"schema_hints":[]}')
        spec = write_operations.WriteItemSpec(kind="metadata", source_id=None, source_name="new.json",
            target_uri=target.uri, binding_snapshot=binding_to_dict(target), target_descriptor=descriptor,
            source_uri=target.uri, expected_revision=None, expected_content_hash=None, expected_size=None,
            expected_provider_revision=None, backup_ref=None, backup_hash=None, backup_size=None,
            payload_ref=artifact.ref, payload_hash=artifact.sha256, payload_size=artifact.size)
        op = write_operations.create_operation(session, environment_id=environment.id, editor_fingerprint="e",
            plan_fingerprint="p", items=[spec], expires_at=write_operations.operation_expiry(now=datetime.now(timezone.utc)))
        session.commit()
    with pytest.raises(write_operations.ActiveWriteOperationError):
        application.update_metadata_source(session, environment.id, parent.id, enabled=False)
    # Simulate an out-of-process legacy writer bypassing the application guard.
    if change == "root":
        parent.source_config_json = json.dumps({"metadata_root_uri": "s3://bucket/b"})
    elif change == "disable":
        parent.enabled = False
    else:
        session.delete(parent)
    session.commit()
    monkeypatch.setattr(write_executor, "storage_for_source", lambda *a, **kw: pytest.fail("changed parent opened a client"))
    assert apply_environment_write(session, op.operation_key, environment_id=environment.id, confirm=True).state == "failed"
    assert write_operations.operation_items(session, op.id)[0].state == "conflict"


def test_applied_finalization_needs_no_secrets_or_clients(editor, monkeypatch):
    from datacoolie_studio.domains.metadata import publication_scope, write_executor

    key, environment_id = prepare(editor)
    session = editor[0]
    original = write_executor._finalize_operation
    monkeypatch.setattr(write_executor, "_finalize_operation", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("checkpoint lost")))
    assert apply_environment_write(session, key, environment_id=environment_id, confirm=True).state == "partial"
    monkeypatch.setattr(write_executor, "_finalize_operation", original)
    monkeypatch.setattr(publication_scope, "publication_scope", lambda *a, **kw: pytest.fail("finalizer reads secrets"))
    monkeypatch.setattr(write_executor, "storage_for_source", lambda *a, **kw: pytest.fail("finalizer constructs client"))
    assert apply_environment_write(session, key, environment_id=environment_id, confirm=True).state == "succeeded"
