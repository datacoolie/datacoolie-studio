from __future__ import annotations

from datacoolie_studio.domains.storage.capabilities import (
    metadata_write_back_supported,
    metadata_write_safety,
    storage_capabilities,
)
from datacoolie_studio.domains.storage.revision_contract import (
    same_content_revision,
    same_provider_version,
    same_revision,
)


def test_revision_contract_prefers_content_identity_when_available():
    left = {"content_hash": "same", "size": 3, "provider_revision": "v1"}
    right = {"content_hash": "same", "size": 3, "provider_revision": "v2"}

    assert same_content_revision(left, right)
    assert same_revision(left, right)
    assert not same_provider_version(left, right)


def test_revision_contract_uses_non_empty_provider_token_without_hashes():
    left = {"size": 3, "provider_revision": "v1"}
    right = {"size": 3, "provider_revision": "v1"}

    assert same_provider_version(left, right)
    assert same_revision(left, right)


def test_revision_contract_never_equates_missing_provider_tokens():
    left = {"size": 3}
    right = {"size": 3}

    assert not same_provider_version(left, right)
    assert not same_revision(left, right)


def test_revision_contract_rejects_same_size_different_content():
    left = {"content_hash": "left", "size": 3, "provider_revision": "v1"}
    right = {"content_hash": "right", "size": 3, "provider_revision": "v1"}

    assert not same_content_revision(left, right)
    assert not same_revision(left, right)


def test_storage_capabilities_fail_closed_and_classify_write_safety():
    assert metadata_write_back_supported("s3") is True
    assert storage_capabilities("s3").native_conditional_write is True
    assert metadata_write_safety("local") == "cooperative"
    dbfs = storage_capabilities("dbfs")
    assert dbfs.metadata_write_back_supported is True
    assert dbfs.write_safety == "verified"
    assert dbfs.native_conditional_write is False
    assert dbfs.verification_mode == "content_read"
    assert metadata_write_safety("dbfs") != "strong_cas"
    assert metadata_write_back_supported("future-provider") is False
    assert metadata_write_safety("future-provider") == "read_only"
