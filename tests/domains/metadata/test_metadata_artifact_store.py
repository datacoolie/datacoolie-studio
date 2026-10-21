from pathlib import Path

import pytest


def test_artifact_store_deduplicates_and_verifies_content(tmp_path: Path):
    from datacoolie_studio.domains.metadata.artifact_store import MetadataArtifactStore

    store = MetadataArtifactStore(tmp_path / "artifacts")
    first = store.put(b"secret metadata")
    second = store.put(b"secret metadata")

    assert first == second
    assert store.read(
        first.ref,
        expected_sha256=first.sha256,
        expected_size=first.size,
    ) == b"secret metadata"

    with pytest.raises(RuntimeError, match="integrity"):
        store.read(first.ref, expected_sha256="0" * 64, expected_size=first.size)


def test_artifact_store_rejects_escape_references_and_removes_idempotently(tmp_path: Path):
    from datacoolie_studio.domains.metadata.artifact_store import MetadataArtifactStore

    store = MetadataArtifactStore(tmp_path / "artifacts")
    with pytest.raises(RuntimeError, match="reference"):
        store.read(
            "../outside",
            expected_sha256="0" * 64,
            expected_size=0,
        )
    artifact = store.put(b"payload")
    assert store.remove(artifact.ref) is True
    assert store.remove(artifact.ref) is False
