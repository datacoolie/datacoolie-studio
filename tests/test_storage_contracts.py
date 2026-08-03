from __future__ import annotations

import hashlib
import io
import threading
import time
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
    StorageAuthenticationError,
    StorageConfigurationError,
    StorageConflictError,
    StorageNotFoundError,
)
from datacoolie_studio.domains.storage.factory import (
    _cached_dbfs_client,
    _cached_onelake_service_client,
    _dbfs_client_cache,
    create_storage_adapter,
    create_storage_writer,
    invalidate_storage_client_caches,
)
from datacoolie_studio.domains.storage.fsspec_adapter import FsspecStorageAdapter
from datacoolie_studio.domains.storage.inventory import (
    StorageInventoryRequest,
    inventory,
    storage_diagnostics,
)
from datacoolie_studio.domains.storage.onelake_adapter import OneLakeStorageAdapter
from datacoolie_studio.domains.storage.uri import (
    canonical_cloud_uri,
    parse_onelake_location,
    parse_storage_uri,
    validate_storage_uri,
)
from datacoolie_studio.domains.storage.writers import (
    DatabricksVerifiedStorageWriter,
    GcsConditionalStorageWriter,
    LocalConditionalStorageWriter,
    OneLakeConditionalStorageWriter,
    S3ConditionalStorageWriter,
)


def test_local_adapter_final_contract_and_stable_materialization(tmp_path: Path):
    source = tmp_path / "source" / "payload.py"
    source.parent.mkdir()
    source.write_bytes(b"print('safe static input')\n")
    adapter = LocalStorageAdapter()

    objects = inventory(
        adapter,
        StorageInventoryRequest(
            uri=str(source.parent),
            purpose="materialize",
            object_types=frozenset({"file"}),
            suffixes=frozenset({".py"}),
        ),
    ).files
    revision = adapter.stat(str(source))
    target = tmp_path / "cache" / "payload.py"
    materialized = adapter.materialize(
        str(source), target, expected_revision=revision
    )

    assert objects[0].provider_revision == revision.provider_revision
    assert target.read_bytes() == source.read_bytes()
    assert materialized.provider_revision == revision.provider_revision
    assert materialized.content_hash


def test_local_inventory_prunes_excluded_subtrees_and_stops_at_matching_limit(
    tmp_path: Path,
):
    root = tmp_path / "project"
    (root / "metadata").mkdir(parents=True)
    (root / "ignored").mkdir()
    (root / "metadata" / "a.json").write_text("{}", encoding="utf-8")
    (root / "metadata" / "b.json").write_text("{}", encoding="utf-8")
    (root / "metadata" / "skip.txt").write_text("skip", encoding="utf-8")
    (root / "ignored" / "hidden.json").write_text("{}", encoding="utf-8")

    result = inventory(
        LocalStorageAdapter(),
        StorageInventoryRequest(
            uri=str(root),
            purpose="validate",
            recursive=True,
            object_types=frozenset({"file"}),
            suffixes=frozenset({".json"}),
            exclude_directories=frozenset({"ignored"}),
            object_limit=1,
        ),
    )

    assert len(result.files) == 1
    assert result.completeness == "partial"
    assert result.early_stop_reason == "object_limit"
    assert result.matching_objects == 2
    assert result.directories_visited == 2
    assert all("ignored" not in item.canonical_uri for item in result.objects)


def test_storage_binding_rejects_untyped_or_sensitive_options():
    with pytest.raises(StorageConfigurationError, match="Unsupported s3 option"):
        StorageBinding(
            provider="s3",
            auth_mode="ambient",
            options={"secret_access_key": "must-not-be-accepted"},
        )
    with pytest.raises(StorageConfigurationError, match="userinfo"):
        StorageBinding(
            provider="minio",
            auth_mode="ambient",
            options={"endpoint_url": "https://user:pass@minio.example"},
        )
    with pytest.raises(StorageConfigurationError, match="path"):
        StorageBinding(
            provider="minio",
            auth_mode="ambient",
            options={"endpoint_url": "https://minio.example/api"},
        )

    valid = StorageBinding(
        provider="minio",
        auth_mode="ambient",
        options={
            "endpoint_url": "https://minio.example",
            "verify_tls": True,
            "addressing_style": "path",
        },
    )
    assert valid.options["verify_tls"] is True


def test_uri_validation_rejects_embedded_credentials_and_signed_queries():
    with pytest.raises(StorageConfigurationError, match="userinfo"):
        validate_storage_uri("s3://user:pass@bucket/path", "s3")
    with pytest.raises(StorageConfigurationError, match="query"):
        validate_storage_uri("abfs://container@account/path?sig=sensitive", "adls")
    assert (
        canonical_cloud_uri("gcs://BUCKET/some path/", "gcs")
        == "gs://bucket/some%20path"
    )
    assert (
        canonical_cloud_uri(
            "dbfs:/Volumes/catalog/schema/volume/some file/",
            "dbfs",
        )
        == "dbfs:/Volumes/catalog/schema/volume/some%20file"
    )
    assert (
        canonical_cloud_uri(
            "/Volumes/catalog/schema/volume/some file/",
            "dbfs",
        )
        == "dbfs:/Volumes/catalog/schema/volume/some%20file"
    )
    assert (
        canonical_cloud_uri(
            "Volumes/catalog/schema/volume/some file/",
            "dbfs",
        )
        == "dbfs:/Volumes/catalog/schema/volume/some%20file"
    )
    assert validate_storage_uri(
        "/Volumes/catalog/schema/volume/project", "dbfs"
    ).provider == "dbfs"
    with pytest.raises(StorageConfigurationError, match="workspace host"):
        validate_storage_uri("dbfs://workspace/Volumes/catalog/schema/volume", "dbfs")
    with pytest.raises(StorageConfigurationError, match="parent traversal"):
        validate_storage_uri("dbfs:/Volumes/catalog/../secret", "dbfs")
    with pytest.raises(StorageConfigurationError, match="parent traversal"):
        validate_storage_uri("/Volumes/catalog/schema/../secret", "dbfs")
    with pytest.raises(StorageConfigurationError, match="does not match"):
        validate_storage_uri("/mnt/project", "dbfs")


def test_adls_uri_variants_normalize_to_one_canonical_form():
    expected = "abfs://test@datateamtest01.dfs.core.windows.net/project/functions"

    assert canonical_cloud_uri(
        "abfss://test@datateamtest01.dfs.core.windows.net/project/functions/", "adls"
    ) == expected
    assert canonical_cloud_uri(
        "https://datateamtest01.dfs.core.windows.net/test/project/functions/", "adls"
    ) == expected
    assert parse_storage_uri(
        "https://datateamtest01.dfs.core.windows.net/test/project/functions"
    ).provider == "adls"
    with pytest.raises(StorageConfigurationError, match="container"):
        validate_storage_uri("https://datateamtest01.dfs.core.windows.net", "adls")


def test_onelake_files_uri_variants_normalize_to_one_canonical_form():
    expected = (
        "abfss://Analytics%20Workspace@onelake.dfs.fabric.microsoft.com/"
        "Telemetry.Lakehouse/Files/project/functions"
    )

    assert canonical_cloud_uri(
        "abfs://Analytics%20Workspace@onelake.dfs.fabric.microsoft.com/"
        "Telemetry.lakehouse/Files/project/functions/",
        "onelake",
    ) == expected
    assert canonical_cloud_uri(
        "https://onelake.dfs.fabric.microsoft.com/Analytics%20Workspace/"
        "Telemetry.Lakehouse/Files/project/functions/",
        "onelake",
    ) == expected
    assert parse_storage_uri(expected).provider == "onelake"
    location = parse_onelake_location(expected)
    assert location.workspace == "Analytics Workspace"
    assert location.item == "Telemetry.Lakehouse"
    assert location.relative_path == "project/functions"
    assert location.sdk_path == "Telemetry.Lakehouse/Files/project/functions"


def test_onelake_guid_form_and_files_root_are_supported():
    workspace = "A50F9CE1-19F7-4B28-AF64-62A540A67F03"
    item = "7D9A1D49-1FE2-4A82-9E32-29535478FA51"

    canonical = canonical_cloud_uri(
        f"https://onelake.dfs.fabric.microsoft.com/{workspace}/{item}/Files/",
        "onelake",
    )

    assert canonical == (
        "abfss://a50f9ce1-19f7-4b28-af64-62a540a67f03"
        "@onelake.dfs.fabric.microsoft.com/"
        "7d9a1d49-1fe2-4a82-9e32-29535478fa51/Files"
    )
    assert parse_onelake_location(canonical).relative_path == ""


@pytest.mark.parametrize(
    ("uri", "message"),
    [
        (
            "abfss://workspace@onelake.dfs.fabric.microsoft.com/lake.Lakehouse/Tables/table",
            "Files",
        ),
        (
            "abfss://workspace@onelake.dfs.fabric.microsoft.com/lake.Warehouse/Files/data",
            ".Lakehouse",
        ),
        (
            "https://other.dfs.fabric.microsoft.com/workspace/lake.Lakehouse/Files/data",
            "must use",
        ),
        (
            "abfss://workspace@onelake.dfs.fabric.microsoft.com/lake.Lakehouse/Files/%2e%2e/secret",
            "Invalid",
        ),
        (
            "abfss://workspace@onelake.dfs.fabric.microsoft.com/lake.Lakehouse/Files/a%2Fb",
            "encoded separators",
        ),
        (
            "abfss://a50f9ce1-19f7-4b28-af64-62a540a67f03@onelake.dfs.fabric.microsoft.com/"
            "named.Lakehouse/Files/data",
            "GUIDs for both",
        ),
    ],
)
def test_onelake_rejects_non_files_or_unsafe_locations(uri: str, message: str):
    with pytest.raises(StorageConfigurationError, match=message):
        validate_storage_uri(uri, "onelake")


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
    filesystem = _ConcurrentOneLakeFilesystem()
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

    assert uploads == [
        ("files", "/Volumes/catalog/schema/volume/assets.json", b"volume", True),
        ("dbfs", "/metadata/assets.json", b"legacy", True),
        ("files", "/Volumes/catalog/schema/volume/new.json", b"new", False),
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


class _FakeFilesystem:
    payload = b'{"ok":true}'

    @staticmethod
    def _strip_protocol(uri: str) -> str:
        return uri.split("://", 1)[-1]

    def ls(self, path: str, detail: bool = True):
        assert detail
        return [self.info(f"{path.rstrip('/')}/data.json")]

    def find(self, path: str, detail: bool = True, withdirs: bool = True):
        assert detail and withdirs
        child = f"{path.rstrip('/')}/data.json"
        return {child: self.info(child)}

    def info(self, path: str):
        return {
            "name": path,
            "type": "file",
            "size": len(self.payload),
            "LastModified": datetime(2026, 7, 23, tzinfo=timezone.utc),
            "VersionId": "version-7",
            "ETag": '"etag-fallback"',
        }

    def open(self, path: str, mode: str):
        assert path == "bucket/prefix/data.json"
        assert mode == "rb"
        return io.BytesIO(self.payload)


class _FakeAdlsFilesystem:
    @staticmethod
    def _strip_protocol(uri: str) -> str:
        return uri.split("://", 1)[-1]

    def ls(self, path: str, detail: bool = True):
        return [self.info("test/functions/sources.py")]

    def find(self, path: str, detail: bool = True, withdirs: bool = True):
        return {"test/functions/sources.py": self.info("test/functions/sources.py")}

    @staticmethod
    def info(path: str):
        return {
            "name": path,
            "type": "file",
            "size": 32,
            "last_modified": datetime(2026, 7, 27, tzinfo=timezone.utc),
            "etag": "adls-etag",
        }


class _FakeS3Client:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def put_object(self, **kwargs):
        self.calls.append(kwargs)
        return {"VersionId": "version-new", "ETag": '"etag-new"'}


class _FakeGcsBlob:
    generation = 18
    metageneration = 3

    def __init__(self) -> None:
        self.last_precondition = None

    def upload_from_string(self, _content: bytes, *, if_generation_match: int):
        self.last_precondition = if_generation_match


class _FakeGcsBucket:
    def __init__(self) -> None:
        self.last_blob = _FakeGcsBlob()

    def blob(self, _key: str):
        self.last_blob = _FakeGcsBlob()
        return self.last_blob


class _FakeDatabricksFiles:
    def __init__(self) -> None:
        self.listed: list[str] = []

    def list_directory_contents(self, path: str):
        self.listed.append(path)
        if path.endswith("/project"):
            return [
                {
                    "path": f"{path}/metadata",
                    "is_directory": True,
                    "last_modified": 1_753_248_000_000,
                }
            ]
        return [
            {
                "path": f"{path}/metadata.json",
                "is_directory": False,
                "file_size": 11,
                "last_modified": 1_753_248_000_000,
                "etag": "volume-etag",
            }
        ]

    def download(self, _path: str):
        return type(
            "Download",
            (),
            {
                "contents": io.BytesIO(b'{"ok":true}'),
                "content_length": 11,
                "last_modified": "Wed, 23 Jul 2025 05:20:00 GMT",
            },
        )()


class _FakeDatabricksDbfs:
    payload = b"def transform():\n    return 1\n"

    def __init__(self) -> None:
        self.status_paths: list[str] = []

    def get_status(self, path: str):
        self.status_paths.append(path)
        return {
            "path": path,
            "is_dir": False,
            "file_size": len(self.payload),
            "modification_time": 1_753_248_000_000,
        }

    def list(self, _path: str):
        return []

    def download(self, _path: str):
        return io.BytesIO(self.payload)


class _FakeDatabricksClient:
    def __init__(self) -> None:
        self.files = _FakeDatabricksFiles()
        self.dbfs = _FakeDatabricksDbfs()


class _FakeOneLakePager:
    def __init__(self, pages):
        self._pages = pages

    def by_page(self):
        return iter(self._pages)


class _FakeOneLakeDownloader:
    def __init__(self, payload: bytes, properties) -> None:
        self._payload = payload
        self.properties = properties

    def chunks(self):
        yield self._payload[:5]
        yield self._payload[5:]


class _FakeOneLakeFileClient:
    def __init__(self, filesystem, path: str) -> None:
        self._filesystem = filesystem
        self._path = path

    def get_file_properties(self):
        return self._filesystem.properties(self._path)

    def download_file(self, *, max_concurrency: int):
        self._filesystem.download_concurrency.append(max_concurrency)
        return _FakeOneLakeDownloader(
            self._filesystem.payload,
            self._filesystem.properties(self._path),
        )


class _FakeOneLakeFilesystem:
    payload = b'{"onelake":true}'

    def __init__(self) -> None:
        self.listed: list[str] = []
        self.page_limits: list[int | None] = []
        self.download_concurrency: list[int] = []

    def get_paths(
        self, *, path: str, recursive: bool, max_results: int | None
    ):
        assert recursive is False
        self.listed.append(path)
        self.page_limits.append(max_results)
        if path.endswith("/project"):
            return _FakeOneLakePager(
                [
                    [
                        SimpleNamespace(
                            name=f"{path}/ignored",
                            is_directory=True,
                            content_length=None,
                            last_modified=None,
                            etag=None,
                        ),
                        SimpleNamespace(
                            name=f"{path}/metadata",
                            is_directory=True,
                            content_length=None,
                            last_modified=None,
                            etag=None,
                        ),
                    ]
                ]
            )
        if path.endswith("/metadata"):
            return _FakeOneLakePager(
                [
                    [
                        SimpleNamespace(
                            name=f"{path}/assets.json",
                            is_directory=False,
                            content_length=len(self.payload),
                            last_modified=datetime(
                                2026, 7, 28, tzinfo=timezone.utc
                            ),
                            etag='"etag-7"',
                        )
                    ]
                ]
            )
        raise AssertionError(path)

    def get_file_client(self, path: str):
        return _FakeOneLakeFileClient(self, path)

    def properties(self, path: str):
        return SimpleNamespace(
            name=path,
            size=len(self.payload),
            content_length=len(self.payload),
            last_modified=datetime(2026, 7, 28, tzinfo=timezone.utc),
            etag='"etag-7"',
        )


class _FakeOneLakeService:
    def __init__(self) -> None:
        self.filesystem = _FakeOneLakeFilesystem()
        self.workspaces: list[str] = []

    def get_file_system_client(self, workspace: str):
        self.workspaces.append(workspace)
        return self.filesystem


class _ConcurrentOneLakeFilesystem:
    def __init__(self) -> None:
        self.listed: list[str] = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def get_paths(
        self, *, path: str, recursive: bool, max_results: int | None
    ):
        assert recursive is False
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.listed.append(path)
        try:
            time.sleep(0.01)
            if path.endswith("/project"):
                entries = [
                    SimpleNamespace(
                        name=f"{path}/directory-{index:02d}",
                        is_directory=True,
                        content_length=None,
                        last_modified=None,
                        etag=None,
                    )
                    for index in range(12)
                ]
                entries.append(
                    SimpleNamespace(
                        name=f"{path}/ignored",
                        is_directory=True,
                        content_length=None,
                        last_modified=None,
                        etag=None,
                    )
                )
            elif "/directory-" in path:
                entries = [
                    SimpleNamespace(
                        name=f"{path}/module.py",
                        is_directory=False,
                        content_length=1,
                        last_modified=datetime(
                            2026, 7, 28, tzinfo=timezone.utc
                        ),
                        etag=f'"{path}"',
                    )
                ]
            else:
                raise AssertionError(path)
            return _FakeOneLakePager([entries])
        finally:
            with self._lock:
                self.active -= 1
