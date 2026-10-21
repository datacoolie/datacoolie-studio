from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from datacoolie_studio.domains.storage.adapters import (
    LocalStorageAdapter,
    StorageRevision,
)
from datacoolie_studio.domains.storage.binding import StorageBinding
from datacoolie_studio.domains.storage.dbfs_adapter import DbfsStorageAdapter
from datacoolie_studio.domains.storage.errors import (
    ProviderDependencyMissing,
    StorageAccessError,
    StorageConfigurationError,
    StorageConflictError,
    StorageNotFoundError,
)
from datacoolie_studio.domains.storage.factory import (
    _cached_dbfs_client,
    _dbfs_client_cache,
    create_storage_adapter,
    create_storage_writer,
)
from datacoolie_studio.domains.storage.fsspec_adapter import FsspecStorageAdapter
from datacoolie_studio.domains.storage.inventory import (
    StorageInventoryRequest,
    inventory,
    storage_diagnostics,
)
from datacoolie_studio.domains.storage.writers import (
    DatabricksVerifiedStorageWriter,
    GcsConditionalStorageWriter,
    LocalConditionalStorageWriter,
    S3ConditionalStorageWriter,
)

from tests.domains.storage.storage_fakes import (
    _FakeFilesystem,
    _FakeAdlsFilesystem,
    _FakeS3Client,
    _FakeGcsBucket,
    _FakeDatabricksClient,
)

def test_adls_listing_preserves_source_account_authority():
    adapter = FsspecStorageAdapter(_FakeAdlsFilesystem(), provider="adls")
    project_uri = "abfs://test@datateamtest01.dfs.core.windows.net/functions"

    listed = inventory(
        adapter,
        StorageInventoryRequest(
            uri=project_uri,
            purpose="observe",
            recursive=True,
            object_types=frozenset({"file"}),
            suffixes=frozenset({".py"}),
        ),
    ).files
    revision = adapter.stat(
        "abfs://test@datateamtest01.dfs.core.windows.net/functions/sources.py"
    )

    assert [item.canonical_uri for item in listed] == [
        "abfs://test@datateamtest01.dfs.core.windows.net/functions/sources.py"
    ]
    assert revision.canonical_uri == listed[0].canonical_uri

def test_adls_ambient_adapter_derives_account_name_from_source_uri(monkeypatch):
    captured: dict[str, object] = {}

    class FakeFsspec:
        @staticmethod
        def filesystem(protocol: str, **kwargs):
            captured.update({"protocol": protocol, **kwargs})
            return _FakeAdlsFilesystem()

    monkeypatch.setattr(
        "datacoolie_studio.domains.storage.factory._module_available",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "datacoolie_studio.domains.storage.factory.importlib.import_module",
        lambda name: FakeFsspec if name == "fsspec" else __import__(name),
    )

    create_storage_adapter(
        StorageBinding(provider="adls", auth_mode="ambient"),
        uri="abfs://test@datateamtest01.dfs.core.windows.net/project",
    )

    assert captured == {
        "protocol": "abfs",
        "account_name": "datateamtest01",
    }

def test_adls_validation_adapter_disables_provider_backoff(monkeypatch):
    class RetryPolicy:
        total_retries = 3
        connect_retries = 3
        read_retries = 3
        status_retries = 3
        initial_backoff = 15
        increment_base = 3
        random_jitter_range = 3

    class ConnectionConfig:
        timeout = 20
        read_timeout = 60

    class Filesystem:
        def __init__(self) -> None:
            self._strip_protocol = _FakeAdlsFilesystem._strip_protocol
            self.service_client = SimpleNamespace(
                _config=SimpleNamespace(
                    retry_policy=RetryPolicy(),
                    transport=SimpleNamespace(
                        connection_config=ConnectionConfig()
                    ),
                )
            )

    created: list[Filesystem] = []

    class FakeFsspec:
        @staticmethod
        def filesystem(protocol: str, **kwargs):
            assert protocol == "abfs"
            assert kwargs == {
                "account_name": "datateamtest01",
                **({"skip_instance_cache": True} if not created else {}),
            }
            filesystem = Filesystem()
            created.append(filesystem)
            return filesystem

    monkeypatch.setattr(
        "datacoolie_studio.domains.storage.factory._module_available",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "datacoolie_studio.domains.storage.factory.importlib.import_module",
        lambda name: FakeFsspec if name == "fsspec" else __import__(name),
    )

    binding = StorageBinding(provider="adls", auth_mode="ambient")
    create_storage_adapter(
        binding,
        uri="abfs://test@datateamtest01.dfs.core.windows.net/project",
        for_validation=True,
    )
    create_storage_adapter(
        binding,
        uri="abfs://test@datateamtest01.dfs.core.windows.net/project",
    )

    validation_policy = created[0].service_client._config.retry_policy
    assert validation_policy.total_retries == 0
    assert validation_policy.connect_retries == 0
    assert validation_policy.read_retries == 0
    assert validation_policy.status_retries == 0
    assert validation_policy.initial_backoff == 0
    assert validation_policy.increment_base == 0
    assert validation_policy.random_jitter_range == 0
    validation_timeout = created[0].service_client._config.transport.connection_config
    assert validation_timeout.timeout == 10
    assert validation_timeout.read_timeout == 15

    normal_policy = created[1].service_client._config.retry_policy
    assert normal_policy.total_retries == 3
    assert normal_policy.initial_backoff == 15
    normal_timeout = created[1].service_client._config.transport.connection_config
    assert normal_timeout.timeout == 20
    assert normal_timeout.read_timeout == 60

def test_adls_explicit_account_must_match_source_uri(monkeypatch):
    monkeypatch.setattr(
        "datacoolie_studio.domains.storage.factory._module_available",
        lambda _name: True,
    )
    with pytest.raises(StorageConfigurationError, match="does not match"):
        create_storage_adapter(
            StorageBinding(
                provider="adls",
                auth_mode="ambient",
                options={"account_name": "anotheraccount"},
            ),
            uri="abfss://test@datateamtest01.dfs.core.windows.net/project",
        )

def test_missing_provider_extra_has_actionable_typed_error(monkeypatch):
    monkeypatch.setattr(
        "datacoolie_studio.domains.storage.factory.importlib.util.find_spec",
        lambda _name: None,
    )
    binding = StorageBinding(provider="s3", auth_mode="ambient")

    with pytest.raises(ProviderDependencyMissing) as captured:
        create_storage_adapter(binding)

    assert captured.value.code == "provider_dependency_missing"
    assert 'datacoolie-studio[s3]' in captured.value.install_command

def test_dbfs_requires_databricks_sdk(monkeypatch):
    monkeypatch.setattr(
        "datacoolie_studio.domains.storage.factory._module_available",
        lambda _name: False,
    )
    binding = StorageBinding(provider="dbfs", auth_mode="ambient")

    with pytest.raises(ProviderDependencyMissing) as adapter_error:
        create_storage_adapter(binding)
    with pytest.raises(ProviderDependencyMissing) as writer_error:
        create_storage_writer(binding)

    assert adapter_error.value.install_command == (
        'pip install "databricks-sdk>=0.121,<0.122"'
    )
    assert writer_error.value.install_command == (
        'pip install "databricks-sdk>=0.121,<0.122"'
    )

def test_dbfs_metadata_writer_is_available_from_factory(monkeypatch):
    client = SimpleNamespace(files=SimpleNamespace(), dbfs=SimpleNamespace())
    monkeypatch.setattr(
        "datacoolie_studio.domains.storage.factory._module_available",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "datacoolie_studio.domains.storage.factory._create_dbfs_client",
        lambda *_args, **_kwargs: client,
    )

    writer = create_storage_writer(StorageBinding(provider="dbfs", auth_mode="ambient"))

    assert isinstance(writer, DatabricksVerifiedStorageWriter)

def test_dbfs_metadata_writer_routes_volumes_and_legacy_dbfs():
    uploads: list[tuple[str, str, bytes, bool]] = []

    class Endpoint:
        def __init__(self, name: str) -> None:
            self.name = name

        def upload(self, path: str, stream, *, overwrite: bool) -> None:
            uploads.append((self.name, path, stream.read(), overwrite))

    current = b'{"version":1}'
    writer = DatabricksVerifiedStorageWriter(
        SimpleNamespace(files=Endpoint("files"), dbfs=Endpoint("dbfs")),
        lambda _uri: io.BytesIO(current),
    )
    expected = StorageRevision(
        canonical_uri="dbfs:/metadata/assets.json",
        size=len(current),
        last_modified=datetime.now(timezone.utc),
        content_hash=hashlib.sha256(current).hexdigest(),
    )

    writer.replace(
        "dbfs:/Volumes/catalog/schema/volume/assets.json", b"volume", expected
    )
    writer.replace("dbfs:/metadata/assets.json", b"legacy", expected)
    writer.create("dbfs:/Volumes/catalog/schema/volume/new.json", b"new")
    writer.create(
        "dbfs:/Volumes/catalog/schema/volume/My%20File.json", b"spaced"
    )

    assert uploads == [
        ("files", "/Volumes/catalog/schema/volume/assets.json", b"volume", True),
        ("dbfs", "/metadata/assets.json", b"legacy", True),
        ("files", "/Volumes/catalog/schema/volume/new.json", b"new", False),
        (
            "files",
            "/Volumes/catalog/schema/volume/My File.json",
            b"spaced",
            False,
        ),
    ]

def test_dbfs_metadata_writer_rejects_changed_content_before_upload():
    uploads: list[object] = []
    endpoint = SimpleNamespace(upload=lambda *args, **kwargs: uploads.append(args))
    uri = "dbfs:/metadata/assets.json"
    writer = DatabricksVerifiedStorageWriter(
        SimpleNamespace(files=endpoint, dbfs=endpoint),
        lambda _uri: io.BytesIO(b"changed"),
    )

    with pytest.raises(StorageConflictError):
        writer.replace(
            uri,
            b"replacement",
            StorageRevision(
                canonical_uri=uri,
                size=8,
                last_modified=datetime.now(timezone.utc),
                content_hash=hashlib.sha256(b"original").hexdigest(),
            ),
        )

    assert uploads == []

def test_dbfs_metadata_writer_maps_create_collision_to_conflict():
    class ResourceAlreadyExists(RuntimeError):
        error_code = "RESOURCE_ALREADY_EXISTS"

    endpoint = SimpleNamespace(
        upload=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ResourceAlreadyExists()
        )
    )
    writer = DatabricksVerifiedStorageWriter(
        SimpleNamespace(files=endpoint, dbfs=endpoint),
        lambda _uri: io.BytesIO(b""),
    )

    with pytest.raises(StorageConflictError, match="already exists"):
        writer.create("dbfs:/metadata/assets.json", b"new")

def test_fake_provider_revision_survives_list_stat_and_materialize(tmp_path: Path):
    filesystem = _FakeFilesystem()
    adapter = FsspecStorageAdapter(filesystem, provider="s3")

    listed = inventory(
        adapter,
        StorageInventoryRequest(
            uri="s3://bucket/prefix",
            purpose="observe",
            object_types=frozenset({"file"}),
            suffixes=frozenset({".json"}),
        ),
    ).files
    stat = adapter.stat("s3://bucket/prefix/data.json")
    target = tmp_path / "data.json"
    materialized = adapter.materialize(
        "s3://bucket/prefix/data.json",
        target,
        expected_revision=stat,
    )

    assert listed[0].provider_revision == "version-7"
    assert stat.provider_revision == "version-7"
    assert materialized.provider_revision == "version-7"
    assert target.read_bytes() == b'{"ok":true}'

@pytest.mark.parametrize(
    ("provider", "uri"),
    [
        ("s3", "s3://bucket/My%20File.json"),
        ("minio", "s3://bucket/My%20File.json"),
        ("gcs", "gs://bucket/My%20File.json"),
        (
            "adls",
            "abfs://container@account.dfs.core.windows.net/My%20File.json",
        ),
    ],
)
def test_fsspec_adapter_decodes_canonical_transport_paths(
    provider: str, uri: str
):
    adapter = FsspecStorageAdapter(_FakeFilesystem(), provider=provider)

    assert adapter._path(uri).endswith("/My File.json")

def test_fsspec_materialize_prefers_provider_revision_over_timestamp_precision(
    tmp_path: Path,
):
    filesystem = _FakeFilesystem()
    adapter = FsspecStorageAdapter(filesystem, provider="minio")
    expected = StorageRevision(
        canonical_uri="s3://bucket/prefix/data.json",
        size=len(filesystem.payload),
        last_modified=datetime(2026, 7, 22, 1, 2, 3, 456000, tzinfo=timezone.utc),
        provider_revision="version-7",
    )

    materialized = adapter.materialize(
        expected.canonical_uri,
        tmp_path / "data.json",
        expected_revision=expected,
    )

    assert materialized.provider_revision == expected.provider_revision
    assert (tmp_path / "data.json").read_bytes() == filesystem.payload


def test_fsspec_materialize_reuses_expected_revision_without_pre_stat(
    tmp_path: Path,
):
    class CountingFilesystem(_FakeFilesystem):
        def __init__(self) -> None:
            self.info_calls = 0

        def info(self, path: str):
            self.info_calls += 1
            return super().info(path)

    filesystem = CountingFilesystem()
    adapter = FsspecStorageAdapter(filesystem, provider="s3")
    expected = StorageRevision(
        canonical_uri="s3://bucket/prefix/data.json",
        size=len(filesystem.payload),
        last_modified=datetime(2026, 7, 23, tzinfo=timezone.utc),
        provider_revision="version-7",
    )

    adapter.materialize(
        expected.canonical_uri,
        tmp_path / "data.json",
        expected_revision=expected,
    )

    assert filesystem.info_calls == 1


def test_fsspec_missing_path_is_distinct_from_access_failure():
    class MissingFilesystem(_FakeFilesystem):
        def ls(self, path: str, detail: bool = True):
            raise FileNotFoundError(path)

    adapter = FsspecStorageAdapter(MissingFilesystem(), provider="s3")

    with pytest.raises(StorageNotFoundError) as captured:
        inventory(
            adapter,
            StorageInventoryRequest(
                uri="s3://bucket/missing",
                purpose="observe",
                object_types=frozenset({"file"}),
                suffixes=frozenset({".json"}),
            ),
        )

    assert captured.value.code == "storage_object_not_found"

def test_fsspec_access_errors_keep_diagnostics_but_redact_secrets():
    class FailingFilesystem(_FakeFilesystem):
        def ls(self, path: str, detail: bool = True):
            raise RuntimeError(
                "Request failed at https://user:secret@example.test/path?sig=token "
                "with client_secret=top-secret Authorization: Bearer abc.def.ghi"
            )

    adapter = FsspecStorageAdapter(FailingFilesystem(), provider="s3")

    with pytest.raises(StorageAccessError) as captured:
        inventory(
            adapter,
            StorageInventoryRequest(
                uri="s3://bucket/prefix",
                purpose="observe",
                object_types=frozenset({"file"}),
            ),
        )

    assert "example.test/path" in str(captured.value)
    assert "top-secret" not in str(captured.value)
    assert "abc.def.ghi" not in str(captured.value)
    assert "?sig=" not in str(captured.value)

def test_fresh_inventory_invalidates_provider_listing_cache_once():
    class CachedFilesystem(_FakeFilesystem):
        def __init__(self) -> None:
            self.invalidated: list[str] = []
            self.list_calls = 0

        def invalidate_cache(self, path: str) -> None:
            self.invalidated.append(path)

        def ls(self, path: str, detail: bool = True):
            self.list_calls += 1
            return super().ls(path, detail)

    filesystem = CachedFilesystem()
    observed = inventory(
        FsspecStorageAdapter(filesystem, provider="s3"),
        StorageInventoryRequest(
            uri="s3://bucket/prefix",
            purpose="probe",
            object_limit=1,
        ),
    )

    assert filesystem.invalidated == ["bucket/prefix"]
    assert filesystem.list_calls == 1
    assert observed.requests == 1
    assert len(observed.objects) == 1

def test_dbfs_adapter_routes_volumes_and_legacy_paths(tmp_path: Path):
    client = _FakeDatabricksClient()
    adapter = DbfsStorageAdapter(client)

    volume_files = inventory(
        adapter,
        StorageInventoryRequest(
            uri="dbfs:/Volumes/catalog/schema/volume/project",
            purpose="observe",
            recursive=True,
            object_types=frozenset({"file"}),
            suffixes=frozenset({".json"}),
        ),
    ).files
    legacy = adapter.stat("dbfs:/mnt/project/functions/transform.py")
    target = tmp_path / "transform.py"
    materialized = adapter.materialize(
        "dbfs:/mnt/project/functions/transform.py",
        target,
        expected_revision=legacy,
    )

    assert volume_files[0].canonical_uri.endswith("/metadata.json")
    assert client.files.listed
    assert len(client.dbfs.status_paths) == 2
    assert target.read_bytes() == b"def transform():\n    return 1\n"
    assert materialized.provider_revision == legacy.provider_revision

def test_dbfs_adapter_decodes_canonical_transport_path():
    client = _FakeDatabricksClient()
    adapter = DbfsStorageAdapter(client)

    adapter.stat("dbfs:/mnt/My%20File.json")

    assert client.dbfs.status_paths == ["/mnt/My File.json"]

def test_dbfs_volume_materialization_uses_download_revision_without_stat(
    tmp_path: Path,
):
    client = _FakeDatabricksClient()
    adapter = DbfsStorageAdapter(client)
    listed = inventory(
        adapter,
        StorageInventoryRequest(
            uri="dbfs:/Volumes/catalog/schema/volume/project/metadata",
            purpose="observe",
            object_types=frozenset({"file"}),
            suffixes=frozenset({".json"}),
        ),
    ).files[0]
    expected = StorageRevision(
        canonical_uri=listed.canonical_uri,
        size=int(listed.size or 0),
        last_modified=listed.last_modified,
        provider_revision=listed.provider_revision,
    )

    materialized = adapter.materialize(
        listed.canonical_uri,
        tmp_path / listed.name,
        expected_revision=expected,
    )

    assert materialized.same_content_as(expected)
    assert client.dbfs.status_paths == []

def test_dbfs_bounded_probe_and_io_diagnostics_stop_after_first_match():
    client = _FakeDatabricksClient()
    adapter = DbfsStorageAdapter(client)

    observed = inventory(
        adapter,
        StorageInventoryRequest(
            uri="dbfs:/Volumes/catalog/schema/volume/project",
            purpose="validate",
            recursive=True,
            object_types=frozenset({"file"}),
            suffixes=frozenset({".json"}),
            object_limit=1,
            stop_after_match=True,
        ),
    )
    with adapter.open_read(observed.files[0].canonical_uri) as stream:
        assert stream.read() == b'{"ok":true}'

    assert observed.completeness == "partial"
    assert observed.matching_objects == 1
    assert client.files.listed == [
        "/Volumes/catalog/schema/volume/project",
        "/Volumes/catalog/schema/volume/project/metadata",
    ]
    assert storage_diagnostics(adapter) == {
        "transport": "databricks_sdk",
        "provider_requests": 3,
        "bytes_read": 11,
        "objects_inspected": 3,
    }

def test_dbfs_bounded_probe_stops_on_matching_name_prefix():
    class Files:
        def list_directory_contents(self, path: str):
            return [
                {
                    "path": f"{path}/debug.jsonl",
                    "name": "debug.jsonl",
                    "is_directory": False,
                },
                {
                    "path": f"{path}/system_log_20260729.jsonl",
                    "name": "system_log_20260729.jsonl",
                    "is_directory": False,
                },
            ]

    adapter = DbfsStorageAdapter(type("Client", (), {"files": Files()})())

    observed = inventory(
        adapter,
        StorageInventoryRequest(
            uri="dbfs:/Volumes/catalog/schema/volume/system_logs",
            purpose="validate",
            object_types=frozenset({"file"}),
            suffixes=frozenset({".jsonl"}),
            name_prefix="system_log_",
            object_limit=1,
            stop_after_match=True,
        ),
    )

    assert [item.name for item in observed.files] == [
        "system_log_20260729.jsonl"
    ]
    assert observed.matching_objects == 1
    assert observed.objects_inspected == 2

def test_dbfs_recursive_listing_prunes_excluded_directories():
    class Files:
        def __init__(self) -> None:
            self.listed: list[str] = []

        def list_directory_contents(self, path: str):
            self.listed.append(path)
            if path.endswith("/metadata"):
                return [
                    {
                        "path": f"{path}/catalog",
                        "name": "catalog",
                        "is_directory": True,
                    },
                    {
                        "path": f"{path}/watermarks",
                        "name": "watermarks",
                        "is_directory": True,
                    },
                ]
            if path.endswith("/catalog"):
                return [
                    {
                        "path": f"{path}/assets.json",
                        "name": "assets.json",
                        "is_directory": False,
                    }
                ]
            raise AssertionError(f"Excluded directory was traversed: {path}")

    client = type("Client", (), {"files": Files()})()
    adapter = DbfsStorageAdapter(client)

    files = inventory(
        adapter,
        StorageInventoryRequest(
            uri="dbfs:/Volumes/catalog/schema/volume/metadata",
            purpose="observe",
            recursive=True,
            object_types=frozenset({"file"}),
            suffixes=frozenset({".json"}),
            exclude_directories=frozenset({"watermarks"}),
        ),
    ).files

    assert [item.name for item in files] == ["assets.json"]
    assert not any(path.endswith("/watermarks") for path in client.files.listed)

def test_dbfs_workspace_client_is_reused_per_credential_fingerprint():
    created: list[dict[str, object]] = []

    class WorkspaceClient:
        def __init__(self, **kwargs):
            created.append(kwargs)

    sdk = type("Sdk", (), {"WorkspaceClient": WorkspaceClient})()
    _dbfs_client_cache.clear()

    first = _cached_dbfs_client(
        sdk, {"host": "https://workspace.test", "profile": "DEFAULT"}
    )
    second = _cached_dbfs_client(
        sdk, {"host": "https://workspace.test", "profile": "DEFAULT"}
    )
    rotated = _cached_dbfs_client(
        sdk, {"host": "https://workspace.test", "profile": "ROTATED"}
    )

    assert first is second
    assert rotated is not first
    assert len(created) == 2
    _dbfs_client_cache.clear()

def test_conditional_writers_map_provider_preconditions(tmp_path: Path):
    local_path = tmp_path / "metadata.json"
    local_path.write_bytes(b"old")
    adapter = LocalStorageAdapter()
    local_writer = LocalConditionalStorageWriter()
    revision = adapter.stat(str(local_path))
    local_writer.replace(str(local_path), b"new", revision)
    local_path.write_bytes(b"stale-content")
    with pytest.raises(StorageConflictError):
        local_writer.replace(str(local_path), b"stale", revision)

    s3_client = _FakeS3Client()
    s3_writer = S3ConditionalStorageWriter(s3_client)
    token = StorageRevision(
        canonical_uri="s3://bucket/key",
        size=3,
        last_modified=datetime.now(timezone.utc),
        provider_revision="etag-old",
    )
    assert s3_writer.replace("s3://bucket/key", b"new", token) == "version-new"
    assert s3_client.calls[0]["IfMatch"] == "etag-old"
    s3_writer.create("s3://bucket/new", b"created")
    assert s3_client.calls[1]["IfNoneMatch"] == "*"
    s3_writer.create("s3://bucket/My%20File.json", b"spaced")
    assert s3_client.calls[2]["Key"] == "My File.json"

    bucket = _FakeGcsBucket()
    gcs_writer = GcsConditionalStorageWriter(lambda _name: bucket)
    gcs_token = StorageRevision(
        canonical_uri="gs://bucket/key",
        size=3,
        last_modified=datetime.now(timezone.utc),
        provider_revision="17:2",
    )
    assert gcs_writer.replace("gs://bucket/key", b"new", gcs_token) == "18:3"
    assert bucket.last_blob.last_precondition == 17
    gcs_writer.create("gs://bucket/new", b"created")
    assert bucket.last_blob.last_precondition == 0
    gcs_writer.create("gs://bucket/My%20File.json", b"spaced")
    assert bucket.last_key == "My File.json"
