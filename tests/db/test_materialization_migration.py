from __future__ import annotations

import sqlite3
from pathlib import Path


def test_legacy_snapshots_migrate_latest_payload_only(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from datacoolie_studio.db.session import get_engine, init_db

    init_db()
    with get_engine().begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO projects (id, name, created_at, updated_at) "
            "VALUES (1, 'migration', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.exec_driver_sql(
            "INSERT INTO environments (id, project_id, name, created_at, updated_at) "
            "VALUES (1, 1, 'dev', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.exec_driver_sql(
            """
            INSERT INTO environment_sources (
                id, environment_id, source_kind, uri, storage_provider,
                storage_auth_mode, enabled, sync_schedule_enabled,
                created_at, updated_at
            ) VALUES
                (10, 1, 'metadata', 'metadata.json', 'local', 'none', 1, 0,
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (20, 1, 'code', 'functions', 'local', 'none', 1, 0,
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        )

    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE metadata_source_snapshots (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL,
                source_revision_json TEXT NOT NULL,
                editor_document_json TEXT NOT NULL,
                normalized_metadata_json TEXT,
                created_at DATETIME NOT NULL
            );
            CREATE TABLE code_artifact_snapshots (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL,
                source_revision_json TEXT NOT NULL,
                artifact_manifest_json TEXT NOT NULL,
                module_index_json TEXT NOT NULL,
                diagnostics_json TEXT NOT NULL,
                analyzer_version VARCHAR(50) NOT NULL,
                created_at DATETIME NOT NULL
            );
            CREATE TABLE environment_read_model_cache_entries (
                id INTEGER PRIMARY KEY,
                environment_id INTEGER NOT NULL,
                model_key VARCHAR(100) NOT NULL,
                payload_json TEXT NOT NULL
            );
            INSERT INTO metadata_source_snapshots VALUES
                (1, 10, '{"version": 1}', '{"document": 1}', '{"_normalizer_version": "old"}', '2026-01-01'),
                (2, 10, '{"version": 2}', '{"document": 2}', '{"_normalizer_version": "metadata-normalizer-v2"}', '2026-01-01'),
                (3, 999, '{"orphan": true}', '{"orphan": true}', '{}', '2026-01-01');
            INSERT INTO code_artifact_snapshots VALUES
                (1, 20, '{"version": 1}', '{"files": 1}', '{"module": 1}', '[]', 'v1', '2026-01-01'),
                (2, 20, '{"version": 2}', '{"files": 2}', '{"module": 2}', '[]', 'v2', '2026-01-02'),
                (3, 999, '{"orphan": true}', '{}', '{}', '[]', 'v2', '2026-01-02');
            INSERT INTO environment_read_model_cache_entries VALUES (1, 1, 'overview', '{}');
            """
        )

    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TABLE alembic_version")
    init_db()
    init_db()

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type = 'table'"
            )
        }
        assert "metadata_source_snapshots" not in tables
        assert "code_artifact_snapshots" not in tables
        assert "environment_read_model_cache_entries" not in tables
        metadata = connection.execute(
            "select source_id, source_revision_json, editor_document_json, materialization_fingerprint "
            "from metadata_materializations"
        ).fetchall()
        code = connection.execute(
            "select source_id, source_revision_json, module_index_json, materialization_fingerprint "
            "from code_artifact_materializations"
        ).fetchall()

    assert len(metadata) == 1
    assert metadata[0][0] == 10
    assert metadata[0][1] == '{"version": 2}'
    assert metadata[0][2] == '{"document": 2}'
    assert len(metadata[0][3]) == 64
    assert len(code) == 1
    assert code[0][0] == 20
    assert code[0][1] == '{"version": 2}'
    assert code[0][2] == '{"module": 2}'
    assert len(code[0][3]) == 64
