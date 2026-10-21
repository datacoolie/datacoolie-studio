from __future__ import annotations

import json
from pathlib import Path

from datacoolie_studio.db.models import EnvironmentSource, MetadataMaterialization
from datacoolie_studio.domains.lineage.service import _load_sql_reference_fingerprints


def test_lineage_input_tracks_external_sql_file_revision(tmp_path: Path) -> None:
    sql_root = tmp_path / "sql"
    sql_root.mkdir()
    sql_path = sql_root / "orders.sql"
    sql_path.write_text("select 1", encoding="utf-8")

    source = EnvironmentSource(
        id=11,
        environment_id=7,
        source_kind="metadata",
        uri=str(tmp_path / "metadata.json"),
        source_config_json=json.dumps({
            "project_uri": str(tmp_path),
            "sql_root_uri": str(sql_root),
        }),
        storage_provider="local",
        storage_auth_mode="none",
    )
    materialization = MetadataMaterialization(
        source_id=source.id,
        editor_document_json=json.dumps({
            "sheets": {
                "dataflows": {
                    "rows": [{"source_query": "orders.sql"}],
                },
            },
        }),
    )

    first = _load_sql_reference_fingerprints(
        object(), [source], {source.id: materialization}
    )
    sql_path.write_text("select 2", encoding="utf-8")
    second = _load_sql_reference_fingerprints(
        object(), [source], {source.id: materialization}
    )

    assert first[0]["kind"] == "file"
    assert first[0]["revision"] != second[0]["revision"]
