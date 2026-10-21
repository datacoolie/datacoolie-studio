from __future__ import annotations

from pathlib import Path

import pytest

from datacoolie_studio.domains.storage.adapters import (
    LocalStorageAdapter,
)
from datacoolie_studio.domains.storage.binding import StorageBinding
from datacoolie_studio.domains.storage.errors import (
    StorageConfigurationError,
)
from datacoolie_studio.domains.storage.inventory import (
    StorageInventoryRequest,
    inventory,
)
from datacoolie_studio.domains.storage.uri import (
    canonical_cloud_uri,
    validate_storage_uri,
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
