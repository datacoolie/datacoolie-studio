from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path

import openpyxl
import pytest
from sqlalchemy import select

from datacoolie_studio.db.models import (
    Environment,
    EnvironmentSource,
    MetadataBackup,
    MetadataMaterialization,
    Project,
    SyncJob,
)
from datacoolie_studio.db.session import create_session, init_db
from datacoolie_studio.domains.metadata.editor import (
    MetadataConflictError,
    load_editor_document_from_raw,
    restore_backup,
    save_editor_document,
)
from datacoolie_studio.domains.metadata.reader import read_metadata_bytes
from datacoolie_studio.domains.metadata.storage_io import MetadataStorage
from datacoolie_studio.domains.sources.initialization import (
    queue_source_initializations,
    run_source_initialization_jobs,
)
from datacoolie_studio.domains.storage.adapters import (
    StorageObject,
    StorageRevision,
)
from datacoolie_studio.domains.storage.errors import StorageConflictError
from datacoolie_studio.domains.storage.inventory import StorageInventory


@pytest.fixture
def cloud_source(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))
    monkeypatch.setattr(
        "datacoolie_studio.domains.metadata.storage_io.backup_dir",
        lambda: tmp_path / "backups",
    )
    init_db()
    session = create_session()
    project = Project(name="project")
    environment = Environment(name="dev", project=project)
    source = EnvironmentSource(
        environment=environment,
        source_kind="metadata",
        uri="s3://bucket/metadata.json",
        storage_provider="s3",
        storage_auth_mode="ambient",
        source_config_json="{}",
    )
    session.add(source)
    session.commit()
    try:
        yield session, source
    finally:
        session.close()


def test_cloud_save_is_conditional_backed_up_and_timeout_reconciled(
    cloud_source, monkeypatch
):
    session, source = cloud_source
    adapter = _MemoryAdapter(_metadata_json("before"))
    writer = _MemoryWriter(adapter, timeout_after_success=True)
    storage = MetadataStorage(adapter=adapter, writer=writer)
    monkeypatch.setattr(
        "datacoolie_studio.domains.metadata.editor.storage_for_source",
        lambda *_args, **_kwargs: storage,
    )
    raw = read_metadata_bytes(source.uri, adapter.data)
    revision = _revision(adapter)
    document = load_editor_document_from_raw(source, raw, revision)
    document["sheets"]["connections"]["rows"][0]["description"] = "after"

    saved = save_editor_document(
        session,
        source,
        document,
        revision,
        confirm_overwrite=True,
    )

    assert saved["source"]["revision"]["content_hash"]
    assert b'"after"' in adapter.data
    backups = list((Path(session.bind.url.database).parent / "backups").rglob("*metadata.json"))
    assert len(backups) == 1


def test_external_cloud_change_returns_conflict_without_overwrite(
    cloud_source, monkeypatch
):
    session, source = cloud_source
    adapter = _MemoryAdapter(_metadata_json("original"))
    storage = MetadataStorage(adapter=adapter, writer=_MemoryWriter(adapter))
    monkeypatch.setattr(
        "datacoolie_studio.domains.metadata.editor.storage_for_source",
        lambda *_args, **_kwargs: storage,
    )
    revision = _revision(adapter)
    document = load_editor_document_from_raw(
        source, read_metadata_bytes(source.uri, adapter.data), revision
    )
    document["sheets"]["connections"]["rows"][0]["description"] = "studio"
    adapter.data = _metadata_json("external")
    adapter.version += 1

    with pytest.raises(MetadataConflictError):
        save_editor_document(
            session,
            source,
            document,
            revision,
            confirm_overwrite=True,
        )

    assert b'"external"' in adapter.data
    assert b'"studio"' not in adapter.data


def test_cloud_backup_restore_is_conditional(cloud_source, monkeypatch):
    session, source = cloud_source
    adapter = _MemoryAdapter(_metadata_json("before"))
    storage = MetadataStorage(adapter=adapter, writer=_MemoryWriter(adapter))
    monkeypatch.setattr(
        "datacoolie_studio.domains.metadata.editor.storage_for_source",
        lambda *_args, **_kwargs: storage,
    )
    revision = _revision(adapter)
    document = load_editor_document_from_raw(
        source, read_metadata_bytes(source.uri, adapter.data), revision
    )
    document["sheets"]["connections"]["rows"][0]["description"] = "after"
    saved = save_editor_document(
        session, source, document, revision, confirm_overwrite=True
    )
    backup_id = session.execute(select(MetadataBackup.id)).scalar_one()

    restored = restore_backup(
        session,
        backup_id,
        saved["source"]["revision"],
        True,
    )

    assert b'"before"' in adapter.data
    assert restored["source"]["revision"]["provider_revision"]


def test_metadata_bytes_parse_json_yaml_and_xlsx():
    json_value = read_metadata_bytes("s3://bucket/meta.json", _metadata_json("json"))
    yaml_value = read_metadata_bytes(
        "gs://bucket/meta.yaml",
        b"connections:\n  - name: yaml\ndataflows: []\nschema_hints: []\n",
    )
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "connections"
    sheet.append(["name", "description"])
    sheet.append(["xlsx", "from bytes"])
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    xlsx_value = read_metadata_bytes(
        "abfs://container@account/meta.xlsx", stream.getvalue()
    )

    assert json_value["connections"][0]["description"] == "json"
    assert yaml_value["connections"][0]["name"] == "yaml"
    assert xlsx_value["connections"][0]["name"] == "xlsx"


def test_revisioned_metadata_read_uses_download_metadata_without_stat():
    from datacoolie_studio.domains.metadata.storage_io import read_source_bytes

    data = _metadata_json("revisioned")
    revision = StorageRevision(
        canonical_uri="dbfs:/Volumes/catalog/schema/volume/metadata.json",
        size=len(data),
        last_modified=datetime(2026, 7, 28, tzinfo=timezone.utc),
        provider_revision=None,
    )

    class RevisionedAdapter(_MemoryAdapter):
        def stat(self, _uri: str) -> StorageRevision:
            raise AssertionError("revisioned download must not call stat")

        def open_read_with_revision(self, _uri: str):
            return io.BytesIO(self.data), revision

    source = type("Source", (), {"uri": revision.canonical_uri})()
    content, observed = read_source_bytes(
        MetadataStorage(adapter=RevisionedAdapter(data)),
        source,
    )

    assert content == data
    assert observed["content_hash"]
    assert observed["size"] == len(data)


def test_metadata_initialization_reads_and_parses_cloud_object_once(
    cloud_source, monkeypatch
):
    session, source = cloud_source

    class CountingAdapter(_MemoryAdapter):
        def __init__(self, data: bytes) -> None:
            super().__init__(data)
            self.read_count = 0

        def open_read(self, uri: str):
            self.read_count += 1
            return super().open_read(uri)

    adapter = CountingAdapter(_metadata_json("single pass"))
    monkeypatch.setattr(
        "datacoolie_studio.domains.metadata.service.storage_for_source",
        lambda *_args, **_kwargs: MetadataStorage(adapter=adapter),
    )
    job_ids = queue_source_initializations(session, [source])

    assert run_source_initialization_jobs(job_ids) == 1

    session.expire_all()
    assert adapter.read_count == 1
    assert source.read_check_status is None
    assert session.scalar(
        select(MetadataMaterialization).where(
            MetadataMaterialization.source_id == source.id
        )
    ) is not None
    outer_job = session.get(SyncJob, job_ids[0])
    assert outer_job is not None
    assert outer_job.status == "succeeded"


def test_invalid_metadata_fails_initial_sync_without_formal_validation(
    cloud_source, monkeypatch
):
    session, source = cloud_source

    class CountingAdapter(_MemoryAdapter):
        def __init__(self) -> None:
            super().__init__(b"not valid json")
            self.read_count = 0

        def open_read(self, uri: str):
            self.read_count += 1
            return super().open_read(uri)

    adapter = CountingAdapter()
    monkeypatch.setattr(
        "datacoolie_studio.domains.metadata.service.storage_for_source",
        lambda *_args, **_kwargs: MetadataStorage(adapter=adapter),
    )
    job_ids = queue_source_initializations(session, [source])

    assert run_source_initialization_jobs(job_ids) == 1

    session.expire_all()
    assert adapter.read_count == 1
    assert source.read_check_status is None
    assert session.scalar(
        select(MetadataMaterialization).where(
            MetadataMaterialization.source_id == source.id
        )
    ) is None
    outer_job = session.get(SyncJob, job_ids[0])
    assert outer_job is not None
    assert outer_job.status == "failed"


class _MemoryAdapter:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.version = 1

    def stat(self, uri: str) -> StorageRevision:
        return StorageRevision(
            canonical_uri=uri,
            size=len(self.data),
            last_modified=datetime(2026, 7, 23, 10, self.version, tzinfo=timezone.utc),
            provider_revision=f"version-{self.version}",
        )

    def open_read(self, _uri: str):
        return io.BytesIO(self.data)

    def canonical_uri(self, uri: str) -> str:
        return uri

    def inventory(self, _request) -> StorageInventory:
        return StorageInventory(
            objects=(),
            completeness="complete",
            requests=1,
            pages=1,
            directories_visited=1,
            objects_inspected=0,
            matching_objects=0,
            retries=0,
            throttles=0,
            bytes_read=0,
            duration_ms=1,
        )

    def materialize(self, *_args, **_kwargs):
        raise NotImplementedError

class _MemoryWriter:
    def __init__(
        self, adapter: _MemoryAdapter, *, timeout_after_success: bool = False
    ) -> None:
        self.adapter = adapter
        self.timeout_after_success = timeout_after_success

    def replace(
        self, _uri: str, content: bytes, expected_revision: StorageRevision
    ) -> str:
        if expected_revision.provider_revision != f"version-{self.adapter.version}":
            raise StorageConflictError(_uri)
        self.adapter.data = content
        self.adapter.version += 1
        if self.timeout_after_success:
            raise TimeoutError("simulated timeout")
        return f"version-{self.adapter.version}"

    def create(self, _uri: str, _content: bytes) -> str:
        raise StorageConflictError(_uri)


def _revision(adapter: _MemoryAdapter) -> dict[str, object]:
    from datacoolie_studio.domains.metadata.storage_io import read_source_bytes

    source = type("Source", (), {"uri": "s3://bucket/metadata.json"})()
    return read_source_bytes(MetadataStorage(adapter=adapter), source)[1]


def _metadata_json(description: str) -> bytes:
    return (
        '{"connections":[{"name":"warehouse","description":"'
        + description
        + '"}],"dataflows":[],"schema_hints":[]}'
    ).encode()
