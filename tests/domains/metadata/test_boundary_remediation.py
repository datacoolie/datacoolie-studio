"""Observe publication ownership and guards across application boundaries."""

import io
import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from datacoolie_studio.db.models import Environment, Project, EnvironmentSource, MetadataWriteOperation
from datacoolie_studio.db.session import create_session, init_db
from datacoolie_studio.domains.metadata.errors import MetadataConflictError
from datacoolie_studio.domains.metadata.service import ensure_metadata_materialization, load_environment_editor_workspace
from datacoolie_studio.domains.metadata.storage_io import MetadataStorage, storage_for_source
from datacoolie_studio.domains.metadata.write_executor import prepare_environment_write, apply_environment_write, abandon_environment_write
from datacoolie_studio.domains.metadata.write_operations import operation_items, active_operation, ActiveWriteOperationError
from datacoolie_studio.domains.sources.persistence import create_source
from datacoolie_studio.domains.sources import application
from datacoolie_studio.domains.storage.adapters import StorageRevision
from datacoolie_studio.domains.storage.errors import StorageConflictError


@pytest.fixture
def editor(studio_paths, tmp_path):
    init_db()
    session = create_session()
    environment = Environment(name="dev", project=Project(name="boundary"))
    session.add(environment)
    session.flush()
    sources = []
    for index, connections in enumerate([[{"name": "lake"}], []]):
        path = tmp_path / f"metadata-{index}.json"
        path.write_text(json.dumps({"connections": connections, "dataflows": [], "schema_hints": []}), encoding="utf-8")
        sources.append(create_source(session, environment.id, source_kind="metadata", uri=str(path), label=None, enabled=True))
    session.commit()
    for source in sources:
        ensure_metadata_materialization(session, source, force=True)
    document = load_environment_editor_workspace(session, environment.id, sources)["document"]
    yield session, environment, sources, document
    session.close()


@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize("deleted", [False, True])
def test_prepare_checks_unchanged_empty_sibling(editor, changed, deleted):
    from pathlib import Path

    session, environment, sources, document = editor
    if changed:
        document["sheets"]["connections"]["rows"][0]["description"] = "new"
    sibling = Path(sources[1].uri)
    if deleted:
        sibling.unlink()
    else:
        sibling.write_text('{"connections":[],"dataflows":[],"schema_hints":[],"external":true}', encoding="utf-8")
    with pytest.raises(MetadataConflictError):
        prepare_environment_write(session, environment.id, sources, document, document["source"]["revision"])
    assert session.scalar(select(MetadataWriteOperation.id)) is None


def test_failed_first_write_keeps_slot_and_draft_through_restart(editor, monkeypatch):
    from datacoolie_studio.domains.metadata import write_executor
    from datacoolie_studio.domains.metadata.drafts import save_environment_editor_draft, load_environment_editor_draft

    session, environment, sources, document = editor
    document["sheets"]["connections"]["rows"][0]["description"] = "new"
    save_environment_editor_draft(session, environment.id, document, document["source"]["revision"], sources=sources)
    operation = prepare_environment_write(session, environment.id, sources, document, document["source"]["revision"])
    key, environment_id, source_id = operation.operation_key, environment.id, sources[0].id
    original_replace = write_executor.conditional_replace_uri
    monkeypatch.setattr(write_executor, "conditional_replace_uri", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")))
    assert apply_environment_write(session, key, environment_id=environment_id, confirm=True).state == "failed"
    item = operation_items(session, operation.id)[0]
    assert item.payload_ref and item.backup_ref
    assert load_environment_editor_draft(session, environment_id) is not None
    session.close()
    session = create_session()
    try:
        assert active_operation(session, environment_id).operation_key == key
        with pytest.raises(ActiveWriteOperationError):
            application.update_metadata_source(session, environment_id, source_id, label="blocked")
        different = deepcopy(document)
        different["sheets"]["connections"]["rows"][0]["description"] = "another"
        current_sources = [session.get(EnvironmentSource, source.id) for source in sources]
        with pytest.raises(MetadataConflictError):
            prepare_environment_write(session, environment_id, current_sources, different, document["source"]["revision"])
        monkeypatch.setattr(write_executor, "conditional_replace_uri", original_replace)
        assert apply_environment_write(session, key, environment_id=environment_id, confirm=True).state == "succeeded"
        assert active_operation(session, environment_id) is None
        assert load_environment_editor_draft(session, environment_id) is None
    finally:
        session.close()


class SqlObject:
    def __init__(self):
        self.data = b"SELECT 1;"
        self.version = 1
        self.fail_after_write = False

    def stat(self, uri):
        return StorageRevision(canonical_uri=uri, size=len(self.data), last_modified=datetime(2026, 1, 1, tzinfo=timezone.utc), provider_revision=str(self.version))

    def open_read(self, uri):
        return io.BytesIO(self.data)

    def canonical_uri(self, uri):
        return uri

    def replace(self, uri, content, expected_revision):
        if expected_revision.provider_revision != str(self.version):
            raise StorageConflictError(uri)
        self.data = content
        self.version += 1
        if self.fail_after_write:
            raise TimeoutError("lost reply")
        return str(self.version)


def test_sql_read_prepare_apply_retry_use_storage_owner(editor, monkeypatch):
    from datacoolie_studio.domains.metadata import sql_writes, write_executor
    from datacoolie_studio.domains.sql import reader

    session, environment, sources, document = editor
    owner = create_source(session, environment.id, source_kind="sql", uri="s3://bucket/queries", label=None, enabled=True)
    session.commit()
    obj = SqlObject()
    calls = []
    def factory(db, source, **kwargs):
        calls.append(source.id)
        return MetadataStorage(obj, obj) if source.id == owner.id else storage_for_source(db, source, **kwargs)
    for module in [sql_writes, write_executor, reader]:
        monkeypatch.setattr(module, "storage_for_source", factory)
    preview = reader.resolve_sql_reference(session, sources[0], "orders.sql")
    document["sql_files"] = [{"source_id": sources[0].id, "query": "orders.sql", "content": "SELECT 2;", "base_revision": preview["revision"], "base_uri": preview["base_uri"]}]
    operation = prepare_environment_write(session, environment.id, sources, document, document["source"]["revision"])
    item = operation_items(session, operation.id)[0]
    assert item.source_id == sources[0].id
    assert json.loads(item.target_descriptor_json)["storage_source_id"] == owner.id
    assert json.loads(item.binding_snapshot_json)["provider"] == "s3"
    with pytest.raises(ActiveWriteOperationError):
        application.delete_sql_source(session, environment.id, owner.id)
    with pytest.raises(ActiveWriteOperationError):
        application.update_sql_source(session, environment.id, owner.id, label="blocked")
    original_replace = write_executor.conditional_replace_uri
    def lost_checkpoint(*args, **kwargs):
        original_replace(*args, **kwargs)
        raise TimeoutError("write verified but checkpoint lost")
    monkeypatch.setattr(write_executor, "conditional_replace_uri", lost_checkpoint)
    assert apply_environment_write(session, operation.operation_key, environment_id=environment.id, confirm=True).state == "failed"
    assert obj.data == b"SELECT 2;"
    environment_id = environment.id
    session.close()
    session = create_session()
    try:
        monkeypatch.setattr(write_executor, "conditional_replace_uri", original_replace)
        assert apply_environment_write(session, operation.operation_key, environment_id=environment_id, confirm=True).state == "succeeded"
        assert calls.count(owner.id) == 4  # preview, prepare, first apply, reconciliation
        assert obj.version == 2  # retry recognizes applied bytes, never writes again
    finally:
        session.close()


def test_same_uri_different_owner_scope_conflicts_before_target_io(editor, monkeypatch):
    from datacoolie_studio.domains.metadata import sql_writes

    session, environment, sources, document = editor
    owners = [create_source(session, environment.id, source_kind="sql", uri="s3://bucket/queries", label=None, enabled=True,
                            storage={"provider": "minio", "auth_mode": "anonymous", "options": {"endpoint_url": f"https://endpoint-{index}.example"}}) for index in range(2)]
    session.commit()
    monkeypatch.setattr(sql_writes, "resolve_sql_file_target_with_source", lambda source, *args, **kwargs: ({"file_uri": "s3://bucket/queries/orders.sql", "base_uri": "s3://bucket/queries"}, owners[sources.index(source)]))
    monkeypatch.setattr(sql_writes, "storage_for_source", lambda *args, **kwargs: pytest.fail("target client constructed before collision check"))
    operations = [{"source_id": source.id, "query": "orders.sql", "content": "SELECT 2;"} for source in sources]
    with pytest.raises(MetadataConflictError, match="conflicting origin"):
        sql_writes.prepare_sql_file_writes(session, sources, operations)


def test_real_resolver_rejects_same_uri_at_different_endpoints(editor, monkeypatch):
    from datacoolie_studio.domains.metadata import sql_writes
    from datacoolie_studio.domains.sql.reader import resolve_sql_file_target_with_source, SqlReferenceError

    session, environment, sources, document = editor
    for index in range(2):
        create_source(session, environment.id, source_kind="sql", uri="s3://bucket/queries", label=None, enabled=True,
                      storage={"provider": "minio", "auth_mode": "anonymous", "options": {"endpoint_url": f"https://endpoint-{index}.example"}})
    session.commit()
    with pytest.raises(SqlReferenceError, match="ambiguous"):
        resolve_sql_file_target_with_source(sources[0], "queries/orders.sql", session=session)
    monkeypatch.setattr(sql_writes, "storage_for_source", lambda *args, **kwargs: pytest.fail("ambiguous target client constructed"))
    with pytest.raises(MetadataConflictError, match="ambiguous"):
        sql_writes.prepare_sql_file_writes(session, sources, [{"source_id": sources[0].id, "query": "queries/orders.sql", "content": "SELECT 2;"}])


def test_project_marker_change_conflicts_before_sql_write(editor, tmp_path):
    session, environment, sources, document = editor
    marker = tmp_path / "datacoolie.yml"
    marker.write_text("components:\n  sql: queries_a\n", encoding="utf-8")
    (tmp_path / "queries_a").mkdir()
    target = tmp_path / "queries_a" / "orders.sql"
    target.write_text("SELECT 1;", encoding="utf-8")
    sources[0].source_config_json = json.dumps({"project_uri": str(tmp_path), "discovery_mode": "datacoolie_project"})
    session.commit()
    from datacoolie_studio.domains.sql.reader import resolve_sql_reference
    preview = resolve_sql_reference(session, sources[0], "orders.sql")
    document["sql_files"] = [{"source_id": sources[0].id, "query": "orders.sql", "content": "SELECT 2;", "base_revision": preview["revision"], "config_revision": preview["config_revision"]}]
    operation = prepare_environment_write(session, environment.id, sources, document, document["source"]["revision"])
    marker.write_text("components:\n  sql: queries_b\n", encoding="utf-8")
    applied = apply_environment_write(session, operation.operation_key, environment_id=environment.id, confirm=True)
    assert applied.state == "failed"
    assert operation_items(session, operation.id)[0].state == "conflict"
    assert target.read_text(encoding="utf-8") == "SELECT 1;"


def test_restore_projects_persisted_siblings_after_access_is_lost(editor, monkeypatch):
    from datacoolie_studio.db.models import MetadataBackup, MetadataSaveEvent, MetadataMaterialization
    from datacoolie_studio.domains.metadata import backup_application, service
    from datacoolie_studio.domains.metadata.drafts import save_environment_editor_draft
    from datacoolie_studio.domains.freshness.service import metadata_catalog_version

    session, environment, sources, document = editor
    original_document = deepcopy(document)
    document["sheets"]["connections"]["rows"][0]["description"] = "after"
    operation = prepare_environment_write(session, environment.id, sources, document, document["source"]["revision"])
    assert apply_environment_write(session, operation.operation_key, environment_id=environment.id, confirm=True).state == "succeeded"
    backup_id = session.scalar(select(MetadataBackup.id))
    current = load_environment_editor_workspace(session, environment.id, sources)["document"]
    revision = current["source"]["revision"]["sources"][0]["revision"]
    save_environment_editor_draft(session, environment.id, current, current["source"]["revision"], sources=sources)
    sibling = session.scalar(select(MetadataMaterialization).where(MetadataMaterialization.source_id == sources[1].id))
    sibling_before = (sibling.source_revision_json, sibling.editor_document_json)
    actual_apply = backup_application.apply_environment_write
    def lose_access_after_verified_apply(*args, **kwargs):
        result = actual_apply(*args, **kwargs)
        assert result.state == "succeeded"
        monkeypatch.setattr(service, "storage_for_source", lambda *args, **kwargs: pytest.fail("restore response re-read source"))
        from datacoolie_studio.domains.metadata import write_executor
        monkeypatch.setattr(write_executor, "storage_for_source", lambda *args, **kwargs: pytest.fail("restore response built source client"))
        return result
    monkeypatch.setattr(backup_application, "apply_environment_write", lose_access_after_verified_apply)
    workspace = backup_application.restore_backup_workspace(session, backup_id, revision, True, environment_id=environment.id)
    assert workspace["draft"] is None
    assert workspace["document"]["sheets"]["connections"]["rows"][0].get("description") == original_document["sheets"]["connections"]["rows"][0].get("description")
    assert len(workspace["document"]["source"]["revision"]["sources"]) == 2
    assert workspace["metadata_catalog_version"] == metadata_catalog_version(session, sources)
    assert (sibling.source_revision_json, sibling.editor_document_json) == sibling_before
    assert len(session.scalars(select(MetadataBackup)).all()) == 2
    assert [event.status for event in session.scalars(select(MetadataSaveEvent).order_by(MetadataSaveEvent.id))] == ["saved", "restored"]
    # Restoring already-current bytes is also a confirmed finalization: no new
    # backup/event is invented, and the draft is cleared without source refresh.
    save_environment_editor_draft(session, environment.id, workspace["document"], workspace["document"]["source"]["revision"], sources=sources)
    from datacoolie_studio.domains.metadata import write_executor
    monkeypatch.setattr(write_executor, "storage_for_source", storage_for_source)
    noop = backup_application.restore_backup_workspace(
        session, backup_id, workspace["document"]["source"]["revision"]["sources"][0]["revision"], True,
        environment_id=environment.id,
    )
    assert noop["draft"] is None
    assert len(session.scalars(select(MetadataBackup)).all()) == 2
    assert len(session.scalars(select(MetadataSaveEvent)).all()) == 2


@pytest.mark.parametrize("mutation", ["profile_host", "secret_host", "owner_root", "legacy", "token", "profile_only"])
def test_sql_scope_is_checked_before_clients_and_allows_token_rotation(editor, monkeypatch, mutation):
    from datacoolie_studio.db.models import CredentialProfile
    from datacoolie_studio.domains.metadata import sql_writes, write_executor

    session, environment, sources, document = editor
    profile = CredentialProfile(id="profile", name="profile", provider="dbfs", auth_type="pat",
                                config_json=json.dumps({"host": "https://workspace-a.example"}),
                                secret_ref="test-secret", secret_state="present")
    session.add(profile)
    session.commit()
    if mutation == "profile_only":
        profile.auth_type = "databricks_profile"
        profile.config_json = json.dumps({"profile": "test-profile"})
        session.commit()
    if mutation == "profile_only":
        from datacoolie_studio.domains.storage.errors import StorageConfigurationError
        with pytest.raises(StorageConfigurationError, match="explicit"):
            create_source(session, environment.id, source_kind="sql", uri="dbfs:/queries", label=None, enabled=True,
                          storage={"provider": "dbfs", "auth_mode": "credential_profile", "credential_profile_id": profile.id})
        return
    owner = create_source(session, environment.id, source_kind="sql", uri="dbfs:/queries", label=None, enabled=True,
                          storage={"provider": "dbfs", "auth_mode": "credential_profile", "credential_profile_id": profile.id})
    session.commit()
    secrets = {"token": "test-token-one"}
    store = type("Store", (), {"get": lambda self, ref: dict(secrets)})()
    obj = SqlObject()
    def factory(db, source, **kwargs):
        return MetadataStorage(obj, obj) if source.id == owner.id else storage_for_source(db, source, **kwargs)
    monkeypatch.setattr(sql_writes, "storage_for_source", factory)
    monkeypatch.setattr(write_executor, "storage_for_source", factory)
    from datacoolie_studio.domains.metadata.storage_io import read_storage_bytes
    revision = read_storage_bytes(MetadataStorage(obj), "dbfs:/queries/orders.sql")[1]
    document["sql_files"] = [{"source_id": sources[0].id, "query": "orders.sql", "content": "SELECT 2;", "base_revision": revision}]
    if mutation == "profile_only":
        monkeypatch.setattr(sql_writes, "storage_for_source", lambda *args, **kwargs: pytest.fail("unproven scope created target client"))
        with pytest.raises(MetadataConflictError, match="scope cannot be proven"):
            prepare_environment_write(session, environment.id, sources, document, document["source"]["revision"], secret_store=store)
        return
    operation = prepare_environment_write(session, environment.id, sources, document, document["source"]["revision"], secret_store=store)
    item = operation_items(session, operation.id)[0]
    assert "test-token" not in item.target_descriptor_json
    assert json.loads(item.target_descriptor_json)["effective_scope"]["scope"]["host"] == "https://workspace-a.example"
    if mutation == "profile_host":
        profile.config_json = json.dumps({"host": "https://workspace-b.example"})
    elif mutation == "secret_host":
        secrets["host"] = "https://workspace-b.example"
    elif mutation == "owner_root":
        owner.uri = "dbfs:/another-root"
    elif mutation == "legacy":
        item.target_descriptor_json = None
    else:
        secrets["token"] = "test-token-two"
    session.commit()
    if mutation != "token":
        monkeypatch.setattr(write_executor, "storage_for_source", lambda *args, **kwargs: pytest.fail("client constructed after scope changed"))
    applied = apply_environment_write(session, operation.operation_key, environment_id=environment.id, confirm=True, secret_store=store)
    if mutation == "token":
        assert applied.state == "succeeded"
        assert obj.data == b"SELECT 2;"
    else:
        assert applied.state == "failed"
        assert operation_items(session, operation.id)[0].state == "conflict"
        assert "reprepare" in operation_items(session, operation.id)[0].error_message
        assert obj.data == b"SELECT 1;"
        abandon_environment_write(session, operation.operation_key, environment_id=environment.id)
        assert active_operation(session, environment.id) is None
