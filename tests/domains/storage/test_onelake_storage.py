from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from datacoolie_studio.domains.storage.adapters import (
    StorageRevision,
)
from datacoolie_studio.domains.storage.binding import StorageBinding
from datacoolie_studio.domains.storage.errors import (
    StorageAuthenticationError,
    StorageConfigurationError,
    StorageConflictError,
)
from datacoolie_studio.domains.storage.factory import (
    _cached_onelake_service_client,
    create_storage_adapter,
    create_storage_writer,
    invalidate_storage_client_caches,
)
from datacoolie_studio.domains.storage.inventory import (
    StorageInventoryRequest,
    inventory,
)
from datacoolie_studio.domains.storage.onelake_adapter import OneLakeStorageAdapter
from datacoolie_studio.domains.storage.writers import (
    OneLakeConditionalStorageWriter,
)

from tests.domains.storage.storage_fakes import (
    _FakeOneLakeService,
    _ConcurrentOneLakeFilesystem,
)

def test_onelake_binding_rejects_unsupported_auth_and_options():
    with pytest.raises(StorageConfigurationError, match="anonymous"):
        StorageBinding(provider="onelake", auth_mode="anonymous")
    with pytest.raises(StorageConfigurationError, match="Unsupported onelake option"):
        StorageBinding(
            provider="onelake",
            auth_mode="ambient",
            options={"endpoint_url": "https://attacker.invalid"},
        )
    assert StorageBinding(provider="onelake", auth_mode="ambient").options == {}

def test_onelake_adapter_uses_bounded_files_inventory_and_revisioned_reads(
    tmp_path: Path,
):
    service = _FakeOneLakeService()
    adapter = OneLakeStorageAdapter(service)
    root = (
        "abfss://workspace@onelake.dfs.fabric.microsoft.com/"
        "lake.Lakehouse/Files/project"
    )

    result = inventory(
        adapter,
        StorageInventoryRequest(
            uri=root,
            purpose="materialize",
            recursive=True,
            object_types=frozenset({"file"}),
            suffixes=frozenset({".json"}),
            exclude_directories=frozenset({"ignored"}),
            object_limit=1,
        ),
    )
    source = f"{root}/metadata/assets.json"
    revision = adapter.stat(source)
    target = tmp_path / "assets.json"
    materialized = adapter.materialize(
        source,
        target,
        expected_revision=revision,
    )

    assert [item.name for item in result.files] == ["assets.json"]
    assert result.completeness == "complete"
    assert service.filesystem.listed == [
        "lake.Lakehouse/Files/project",
        "lake.Lakehouse/Files/project/metadata",
    ]
    assert service.filesystem.page_limits == [2, 2]
    assert not any("ignored" in value for value in service.filesystem.listed)
    assert target.read_bytes() == b'{"onelake":true}'
    assert materialized.provider_revision == "etag-7"
    assert materialized.content_hash
    assert service.workspaces == ["workspace"]
    assert service.filesystem.download_concurrency == [4]

def test_onelake_complete_inventory_lists_sibling_directories_concurrently():
    filesystem = _ConcurrentOneLakeFilesystem(barrier=threading.Barrier(2))
    adapter = OneLakeStorageAdapter(
        SimpleNamespace(
            get_file_system_client=lambda _workspace: filesystem
        )
    )
    root = (
        "abfss://workspace@onelake.dfs.fabric.microsoft.com/"
        "lake.Lakehouse/Files/project"
    )

    result = inventory(
        adapter,
        StorageInventoryRequest(
            uri=root,
            purpose="observe",
            recursive=True,
            object_types=frozenset({"file"}),
            suffixes=frozenset({".py"}),
            exclude_directories=frozenset({"ignored"}),
        ),
    )

    assert filesystem.max_active > 1
    assert filesystem.max_active <= 8
    assert not any(path.endswith("/ignored") for path in filesystem.listed)
    assert [item.canonical_uri for item in result.files] == sorted(
        item.canonical_uri for item in result.files
    )
    assert result.directories_visited == 13

def test_onelake_bounded_inventory_does_not_fan_out_directory_requests():
    filesystem = _ConcurrentOneLakeFilesystem()
    adapter = OneLakeStorageAdapter(
        SimpleNamespace(
            get_file_system_client=lambda _workspace: filesystem
        )
    )

    result = inventory(
        adapter,
        StorageInventoryRequest(
            uri=(
                "abfss://workspace@onelake.dfs.fabric.microsoft.com/"
                "lake.Lakehouse/Files/project"
            ),
            purpose="probe",
            recursive=True,
            object_types=frozenset({"file"}),
            suffixes=frozenset({".py"}),
            object_limit=1,
        ),
    )

    assert result.completeness == "partial"
    assert filesystem.max_active == 1

def test_onelake_service_client_cache_reuses_and_separates_credentials():
    invalidate_storage_client_caches()
    created: list[object] = []

    class Credential:
        def __init__(self, **values):
            self.values = values
            self.closed = False

        def close(self):
            self.closed = True

    class ServiceClient:
        def __init__(self, **values):
            self.values = values
            self.closed = False
            created.append(self)

        def close(self):
            self.closed = True

    identity = SimpleNamespace(
        DefaultAzureCredential=Credential,
        ClientSecretCredential=Credential,
    )
    data_lake = SimpleNamespace(DataLakeServiceClient=ServiceClient)
    first = _cached_onelake_service_client(
        identity,
        data_lake,
        auth_type="service_principal",
        credentials={
            "tenant_id": "tenant",
            "client_id": "client",
            "client_secret": "secret-1",
        },
    )
    repeated = _cached_onelake_service_client(
        identity,
        data_lake,
        auth_type="service_principal",
        credentials={
            "tenant_id": "tenant",
            "client_id": "client",
            "client_secret": "secret-1",
        },
    )
    rotated = _cached_onelake_service_client(
        identity,
        data_lake,
        auth_type="service_principal",
        credentials={
            "tenant_id": "tenant",
            "client_id": "client",
            "client_secret": "secret-2",
        },
    )

    assert first is repeated
    assert rotated is not first
    assert len(created) == 2
    invalidate_storage_client_caches()
    assert all(client.closed for client in created)

def test_onelake_factory_uses_fixed_endpoint_and_default_azure_credential(
    monkeypatch,
):
    captured: dict[str, object] = {}

    class DefaultAzureCredential:
        pass

    class DataLakeServiceClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    modules = {
        "azure.identity": SimpleNamespace(
            DefaultAzureCredential=DefaultAzureCredential,
            ClientSecretCredential=object,
        ),
        "azure.storage.filedatalake": SimpleNamespace(
            DataLakeServiceClient=DataLakeServiceClient
        ),
    }
    monkeypatch.setattr(
        "datacoolie_studio.domains.storage.factory._module_available",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "datacoolie_studio.domains.storage.factory.importlib.import_module",
        lambda name: modules[name],
    )

    adapter = create_storage_adapter(
        StorageBinding(provider="onelake", auth_mode="ambient"),
        uri=(
            "abfss://workspace@onelake.dfs.fabric.microsoft.com/"
            "lake.Lakehouse/Files/project"
        ),
    )

    assert isinstance(adapter, OneLakeStorageAdapter)
    assert captured["account_url"] == "https://onelake.dfs.fabric.microsoft.com"
    assert isinstance(captured["credential"], DefaultAzureCredential)

def test_onelake_factory_routes_service_principal_to_entra_credential(
    monkeypatch,
):
    captured: dict[str, object] = {}

    class ClientSecretCredential:
        def __init__(self, **kwargs):
            self.values = kwargs

    class DataLakeServiceClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    modules = {
        "azure.identity": SimpleNamespace(
            DefaultAzureCredential=object,
            ClientSecretCredential=ClientSecretCredential,
        ),
        "azure.storage.filedatalake": SimpleNamespace(
            DataLakeServiceClient=DataLakeServiceClient
        ),
    }
    credentials = {
        "tenant_id": "tenant",
        "client_id": "client",
        "client_secret": "secret",
    }
    monkeypatch.setattr(
        "datacoolie_studio.domains.storage.factory._module_available",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "datacoolie_studio.domains.storage.factory._resolve_credentials",
        lambda *_args, **_kwargs: ("service_principal", credentials),
    )
    monkeypatch.setattr(
        "datacoolie_studio.domains.storage.factory.importlib.import_module",
        lambda name: modules[name],
    )

    create_storage_adapter(
        StorageBinding(
            provider="onelake",
            auth_mode="credential_profile",
            credential_profile_id="profile-id",
        ),
        uri=(
            "abfss://workspace@onelake.dfs.fabric.microsoft.com/"
            "lake.Lakehouse/Files/project"
        ),
    )

    credential = captured["credential"]
    assert isinstance(credential, ClientSecretCredential)
    assert credential.values == {
        "tenant_id": "tenant",
        "client_id": "client",
        "client_secret": "secret",
    }
    assert credentials == {}

def test_onelake_metadata_writer_uses_etag_conditional_upload(monkeypatch):
    uploads: list[tuple[bytes, dict[str, object]]] = []

    class FileClient:
        def upload_data(self, content: bytes, **kwargs):
            uploads.append((content, kwargs))
            return {"etag": '"etag-8"'}

    service = SimpleNamespace(
        get_file_system_client=lambda workspace: SimpleNamespace(
            get_file_client=lambda path: (
                FileClient()
                if (workspace, path)
                == ("workspace", "lake.Lakehouse/Files/metadata/assets.json")
                else None
            )
        )
    )
    modules = {
        "azure.identity": SimpleNamespace(),
        "azure.storage.filedatalake": SimpleNamespace(),
        "azure.core": SimpleNamespace(
            MatchConditions=SimpleNamespace(IfNotModified="if-not-modified")
        ),
    }
    monkeypatch.setattr(
        "datacoolie_studio.domains.storage.factory._module_available",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "datacoolie_studio.domains.storage.factory._cached_onelake_service_client",
        lambda *_args, **_kwargs: service,
    )
    monkeypatch.setattr(
        "datacoolie_studio.domains.storage.factory.importlib.import_module",
        lambda name: modules[name],
    )
    uri = (
        "abfss://workspace@onelake.dfs.fabric.microsoft.com/"
        "lake.Lakehouse/Files/metadata/assets.json"
    )

    writer = create_storage_writer(
        StorageBinding(provider="onelake", auth_mode="ambient"),
        uri=uri,
    )
    revision = writer.replace(
        uri,
        b'{"version":2}',
        StorageRevision(
            canonical_uri=uri,
            size=13,
            last_modified=datetime.now(timezone.utc),
            provider_revision="etag-7",
        ),
    )
    created_revision = writer.create(uri, b"new")

    assert isinstance(writer, OneLakeConditionalStorageWriter)
    assert revision == "etag-8"
    assert created_revision == "etag-8"
    assert uploads == [
        (
            b'{"version":2}',
            {
                "length": 13,
                "overwrite": True,
                "etag": "etag-7",
                "match_condition": "if-not-modified",
            },
        ),
        (b"new", {"length": 3, "overwrite": False}),
    ]

def test_onelake_metadata_writer_maps_stale_etag_to_conflict():
    class PreconditionFailed(RuntimeError):
        status_code = 412

    file_client = SimpleNamespace(
        upload_data=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PreconditionFailed()
        )
    )
    service = SimpleNamespace(
        get_file_system_client=lambda _workspace: SimpleNamespace(
            get_file_client=lambda _path: file_client
        )
    )
    uri = (
        "abfss://workspace@onelake.dfs.fabric.microsoft.com/"
        "lake.Lakehouse/Files/metadata/assets.json"
    )
    writer = OneLakeConditionalStorageWriter(service, if_not_modified="if-not-modified")

    with pytest.raises(StorageConflictError):
        writer.replace(
            uri,
            b"new",
            StorageRevision(
                canonical_uri=uri,
                size=3,
                last_modified=datetime.now(timezone.utc),
                provider_revision="stale",
            ),
        )

def test_onelake_metadata_writer_rejects_ignored_condition_headers():
    file_client = SimpleNamespace(
        upload_data=lambda *_args, **_kwargs: {
            "x-ms-rejected-headers": "If-Match"
        }
    )
    service = SimpleNamespace(
        get_file_system_client=lambda _workspace: SimpleNamespace(
            get_file_client=lambda _path: file_client
        )
    )
    uri = (
        "abfss://workspace@onelake.dfs.fabric.microsoft.com/"
        "lake.Lakehouse/Files/metadata/assets.json"
    )
    writer = OneLakeConditionalStorageWriter(service, if_not_modified="if-not-modified")

    with pytest.raises(StorageConflictError, match="rejected"):
        writer.replace(
            uri,
            b"new",
            StorageRevision(
                canonical_uri=uri,
                size=3,
                last_modified=datetime.now(timezone.utc),
                provider_revision="etag-7",
            ),
        )

def test_onelake_sdk_errors_are_typed_and_redacted():
    class Unauthorized(RuntimeError):
        status_code = 401

    class Filesystem:
        def get_paths(self, **_kwargs):
            raise Unauthorized(
                "Authorization: Bearer abc.def.ghi client_secret=unsafe"
            )

    service = SimpleNamespace(
        get_file_system_client=lambda _workspace: Filesystem()
    )
    adapter = OneLakeStorageAdapter(service)

    with pytest.raises(StorageAuthenticationError) as captured:
        inventory(
            adapter,
            StorageInventoryRequest(
                uri=(
                    "abfss://workspace@onelake.dfs.fabric.microsoft.com/"
                    "lake.Lakehouse/Files/project"
                ),
                purpose="probe",
                object_limit=1,
            ),
        )

    assert captured.value.code == "storage_authentication_failed"
    assert "abc.def.ghi" not in str(captured.value)
    assert "unsafe" not in str(captured.value)
