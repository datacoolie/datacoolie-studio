from __future__ import annotations

import json
from pathlib import Path


def test_same_revision_old_normalizer_is_rematerialized(
    studio_paths,
    studio_project_factory,
    tmp_path: Path,
    monkeypatch,
):
    """The v8 fence refreshes persisted IDs even when source bytes are unchanged."""

    from datacoolie_studio.db.models import EnvironmentSource
    from datacoolie_studio.db.session import create_session
    from datacoolie_studio.domains.metadata import service

    environment = studio_project_factory()
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps({"connections": [], "dataflows": [], "schema_hints": []}),
        encoding="utf-8",
    )
    session = create_session()
    try:
        source = EnvironmentSource(
            environment_id=environment.environment_id,
            source_kind="metadata",
            uri=str(metadata_path),
            label="metadata",
            enabled=True,
        )
        session.add(source)
        session.commit()

        current = service.ensure_metadata_materialization(session, source, force=True)
        revision = json.loads(current.source_revision_json)
        current_version = service.METADATA_NORMALIZER_VERSION
        monkeypatch.setattr(
            service,
            "METADATA_NORMALIZER_VERSION",
            "metadata-normalizer-v7-assets-identity",
        )
        legacy_fingerprint = service._materialization_fingerprint(revision)
        monkeypatch.setattr(service, "METADATA_NORMALIZER_VERSION", current_version)

        current.normalizer_version = "metadata-normalizer-v7-assets-identity"
        current.materialization_fingerprint = legacy_fingerprint
        current.normalized_metadata_json = json.dumps({"stale": True})
        session.commit()

        refreshed = service.ensure_metadata_materialization(session, source)

        assert refreshed.normalizer_version == current_version
        assert refreshed.materialization_fingerprint != legacy_fingerprint
        assert json.loads(refreshed.source_revision_json) == revision
        assert json.loads(refreshed.normalized_metadata_json or "{}") != {"stale": True}
    finally:
        session.close()


def test_normalizer_keeps_inline_connection_and_new_dataflow_metadata_fields():
    from datacoolie_studio.domains.metadata.normalizer import (
        METADATA_NORMALIZER_VERSION,
        enrich_metadata_documents_with_connections,
        normalize_metadata_document,
    )

    document = normalize_metadata_document(
        11,
        "metadata.json",
        {
            "connections": [{
                "name": "lake",
                "connection_id": "lake-id",
                "connection_type": "file",
                "format": "parquet",
                "configure": {"base_path": "s3://bucket"},
            }],
            "dataflows": [{
                "name": "orders",
                "workspace_id": "workspace-1",
                "group_number": 2,
                "execution_order": 8,
                "configure": {"missing_column_policy": "ignore"},
                "source": {
                    "connection_name": "lake",
                    "table": "raw.orders",
                    "filter_expression": "is_active = true",
                },
                "destination": {
                    "connection": {
                        "name": "inline-destination",
                        "connection_type": "file",
                        "format": "json",
                        "configure": {"base_path": "/tmp/output"},
                    },
                    "table": "orders.json",
                },
            }],
            "schema_hints": [{
                "connection_id": "lake-id",
                "table_name": "raw.orders",
                "hints": [{"column_name": "id"}],
            }],
        },
    )

    dataflow = document["dataflows"][0]
    destination = dataflow["destination"]
    assert METADATA_NORMALIZER_VERSION == "metadata-normalizer-v9-workspace-identity"
    assert dataflow["workspace_id"] == "workspace-1"
    assert dataflow["group_number"] == 2
    assert dataflow["execution_order"] == 8
    assert dataflow["configure"] == {"missing_column_policy": "ignore"}
    assert dataflow["source"]["filter_expression"] == "is_active = true"
    assert destination["connection_name"] == "inline-destination"
    assert destination["connection"]["configure"]["base_path"] == "/tmp/output"
    assert document["schema_hints"][0]["connection_id"] == "lake-id"

    enriched = enrich_metadata_documents_with_connections([document])[0]
    enriched_destination = enriched["dataflows"][0]["destination"]
    assert enriched_destination["connection"]["name"] == "inline-destination"
    assert enriched_destination["base_path"] == "/tmp/output"
    assert enriched_destination["connection"]["configure"]["base_path"] == "/tmp/output"
