from __future__ import annotations


import pytest

from datacoolie_studio.domains.storage.errors import (
    StorageConfigurationError,
)
from datacoolie_studio.domains.storage.uri import (
    canonical_cloud_uri,
    parse_onelake_location,
    parse_storage_uri,
    transport_storage_uri,
    validate_storage_uri,
)

@pytest.mark.parametrize(
    ("provider", "uri", "expected"),
    [
        (
            "s3",
            "s3://bucket/My%20File.json",
            "s3://bucket/My File.json",
        ),
        (
            "gcs",
            "gs://bucket/My%20File.json",
            "gs://bucket/My File.json",
        ),
        (
            "adls",
            "abfs://container@account.dfs.core.windows.net/My%20File.json",
            "abfs://container@account.dfs.core.windows.net/My File.json",
        ),
        (
            "dbfs",
            "dbfs:/Volumes/catalog/schema/volume/My%20File.json",
            "dbfs:/Volumes/catalog/schema/volume/My File.json",
        ),
        (
            "s3",
            "s3://bucket/My%2520File.json",
            "s3://bucket/My%20File.json",
        ),
    ],
)
def test_transport_storage_uri_decodes_one_path_encoding_layer(
    provider: str, uri: str, expected: str
):
    assert transport_storage_uri(uri, provider) == expected

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
