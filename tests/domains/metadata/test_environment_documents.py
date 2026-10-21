from __future__ import annotations

import json
from pathlib import Path

import pytest

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.metadata.environment_documents import (
    create_metadata_source_for_routing,
    metadata_source_uri_for_new_name,
    safe_metadata_object_name,
)
from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.domains.metadata.source_policy import source_is_read_only


@pytest.mark.parametrize(
    "source_name",
    [
        "../outside.json",
        "nested/../../outside.json",
        "/absolute/outside.json",
        "C:/absolute/outside.json",
        "nested/",
        "",
    ],
)
def test_local_dynamic_metadata_target_rejects_escape_names(
    tmp_path: Path, source_name: str
):
    with pytest.raises(MetadataReadError, match="relative path|configured metadata root"):
        metadata_source_uri_for_new_name(source_name, tmp_path / "metadata")


def test_local_dynamic_metadata_target_is_resolved_under_root(tmp_path: Path):
    root = tmp_path / "metadata"
    target = Path(metadata_source_uri_for_new_name("nested/source", root))

    assert target == (root / "nested/source.json").resolve()
    assert target.is_relative_to(root.resolve())


@pytest.mark.parametrize(
    "source_name",
    ["../outside.json", "/absolute.json", "C:/absolute.json", "nested/"],
)
def test_cloud_dynamic_metadata_object_name_rejects_escape_names(source_name: str):
    with pytest.raises(MetadataReadError):
        safe_metadata_object_name(source_name)


def test_new_metadata_source_rejects_ambiguous_cloud_roots(tmp_path: Path):
    sources = [
        EnvironmentSource(
            environment_id=1,
            source_kind="metadata",
            uri=f"s3://bucket-{index}/metadata.json",
            storage_provider="s3",
            source_config_json='{"metadata_root_uri": "s3://bucket/metadata"}',
        )
        for index in (1, 2)
    ]

    with pytest.raises(MetadataReadError, match="multiple cloud metadata roots"):
        create_metadata_source_for_routing(
            session=None,  # type: ignore[arg-type]
            environment_id=1,
            source_name="new.json",
            existing_sources=sources,
        )


def test_dbfs_metadata_source_can_be_created_under_explicit_volume_root(
    monkeypatch: pytest.MonkeyPatch,
):
    source = EnvironmentSource(
        environment_id=1,
        source_kind="metadata",
        uri="dbfs:/Volumes/catalog/schema/volume/metadata.json",
        storage_provider="dbfs",
        storage_auth_mode="ambient",
        source_config_json='{"metadata_root_uri": "dbfs:/Volumes/catalog/schema/volume"}',
    )

    class FakeSession:
        def add(self, added_source):
            added_source.id = 2

        def flush(self):
            return None

        def commit(self):
            return None

        def rollback(self):
            return None

        def refresh(self, _source):
            return None

    created: list[tuple[str, bytes]] = []

    monkeypatch.setattr(
        "datacoolie_studio.domains.metadata.environment_documents.storage_for_source",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "datacoolie_studio.domains.metadata.environment_documents.conditional_create",
        lambda _storage, created_source, content: created.append(
            (created_source.uri, content)
        ),
    )

    created_source = create_metadata_source_for_routing(
        FakeSession(),
        environment_id=1,
        source_name="new.json",
        existing_sources=[source],
    )

    assert created_source.uri == (
        "dbfs:/Volumes/catalog/schema/volume/new.json"
    )
    assert created[0][0] == "dbfs:/Volumes/catalog/schema/volume/new.json"
    assert json.loads(created[0][1]) == {
        "connections": [],
        "dataflows": [],
        "schema_hints": [],
    }
    assert source_is_read_only(created_source) is False


def test_dbfs_source_read_only_override_still_blocks_dynamic_creation():
    source = EnvironmentSource(
        environment_id=1,
        source_kind="metadata",
        uri="dbfs:/Volumes/catalog/schema/volume/metadata.json",
        storage_provider="dbfs",
        storage_auth_mode="ambient",
        source_config_json=(
            '{"metadata_root_uri": '
            '"dbfs:/Volumes/catalog/schema/volume", "read_only": true}'
        ),
    )

    with pytest.raises(MetadataReadError, match="read-only"):
        create_metadata_source_for_routing(
            session=None,  # type: ignore[arg-type]
            environment_id=1,
            source_name="new.json",
            existing_sources=[source],
        )
