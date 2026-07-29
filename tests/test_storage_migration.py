from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from sqlalchemy import text

from datacoolie_studio.db.session import get_engine, init_db


def test_source_observation_hard_cutover_drops_only_superseded_state(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "studio.db"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))
    init_db()
    with get_engine().begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects (id, name, created_at, updated_at) "
                "VALUES (1, 'demo', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO environments "
                "(id, project_id, name, created_at, updated_at) "
                "VALUES (1, 1, 'dev', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO environment_sources (
                    id, environment_id, source_kind, uri, storage_provider,
                    storage_auth_mode, enabled, sync_schedule_enabled,
                    created_at, updated_at
                ) VALUES (
                    1, 1, 'metadata', 'metadata.json', 'local', 'none',
                    1, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text("CREATE TABLE source_revisions (source_id INTEGER PRIMARY KEY)")
        )
        connection.execute(
            text("CREATE TABLE source_check_states (source_id INTEGER PRIMARY KEY)")
        )
        connection.execute(text("INSERT INTO source_revisions VALUES (1)"))
        connection.execute(text("INSERT INTO source_check_states VALUES (1)"))

    init_db()

    with get_engine().connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'table'")
            )
        }
        assert "source_revisions" not in tables
        assert "source_check_states" not in tables
        assert "source_observations" in tables
        assert connection.execute(text("SELECT count(*) FROM projects")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM environments")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM environment_sources")).scalar_one() == 1


def test_legacy_source_storage_config_is_backfilled_idempotently(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "studio.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE environment_sources (
                id INTEGER PRIMARY KEY,
                environment_id INTEGER NOT NULL,
                source_kind VARCHAR(50) NOT NULL,
                uri TEXT NOT NULL,
                source_config_json TEXT,
                label VARCHAR(255),
                enabled BOOLEAN NOT NULL DEFAULT 1,
                read_check_status VARCHAR(50),
                read_checked_at DATETIME,
                read_check_result_json TEXT,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            INSERT INTO environment_sources (
                id, environment_id, source_kind, uri, source_config_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                1,
                1,
                "metadata",
                "s3://analytics/metadata.json",
                json.dumps(
                    {
                        "format": "json",
                        "storage": {
                            "provider": "s3",
                            "auth_mode": "ambient",
                            "options": {"region": "ap-southeast-1"},
                        },
                    }
                ),
            ),
        )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    init_db()
    init_db()

    with get_engine().connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT storage_provider, storage_auth_mode,
                       storage_config_json, source_config_json
                FROM environment_sources
                WHERE id = 1
                """
            )
        ).mappings().one()
        indexes = {
            item["name"]
            for item in connection.execute(
                text("PRAGMA index_list(environment_sources)")
            ).mappings()
        }
        registration = connection.execute(
            text(
                """
                SELECT r.purpose, r.input_uri, r.canonical_uri,
                       s.registration_id
                FROM environment_sources s
                JOIN source_registrations r ON r.id = s.registration_id
                WHERE s.id = 1
                """
            )
        ).mappings().one()
    assert row["storage_provider"] == "s3"
    assert row["storage_auth_mode"] == "ambient"
    assert json.loads(row["storage_config_json"]) == {
        "region": "ap-southeast-1"
    }
    assert json.loads(row["source_config_json"]) == {"format": "json"}
    assert "ix_environment_sources_storage_provider" in indexes
    assert "ix_environment_sources_credential_profile" in indexes
    assert registration["purpose"] == "metadata"
    assert registration["input_uri"] == "s3://analytics/metadata.json"
    assert registration["canonical_uri"] == "s3://analytics/metadata.json"
    assert registration["registration_id"] is not None


def test_log_manifest_duplicates_are_reduced_before_unique_index(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "studio.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE log_file_manifest (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL,
                file_uri TEXT NOT NULL,
                file_kind VARCHAR(50) NOT NULL,
                revision_json TEXT NOT NULL,
                row_count INTEGER NOT NULL DEFAULT 0,
                status VARCHAR(50) NOT NULL,
                first_seen_at DATETIME NOT NULL,
                last_seen_at DATETIME NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO log_file_manifest (
                id, source_id, file_uri, file_kind, revision_json,
                row_count, status, first_seen_at, last_seen_at
            ) VALUES (?, 1, 's3://bucket/log.jsonl', 'job_jsonl', ?,
                      0, 'ok', '2026-07-20', ?)
            """,
            [
                (1, '{"provider_revision":"old"}', "2026-07-21"),
                (2, '{"provider_revision":"new"}', "2026-07-22"),
            ],
        )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    init_db()
    init_db()

    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT id, revision_json
                FROM log_file_manifest
                WHERE source_id = 1
                  AND file_kind = 'job_jsonl'
                  AND file_uri = 's3://bucket/log.jsonl'
                """
            )
        ).all()
        indexes = {
            item["name"]
            for item in connection.execute(
                text("PRAGMA index_list(log_file_manifest)")
            ).mappings()
        }
    assert rows == [(2, '{"provider_revision":"new"}')]
    assert "uq_log_file_manifest_source_kind_uri" in indexes


def test_adls_metadata_uri_is_repaired_from_its_discovery_root(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "studio.db"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))
    init_db()
    with get_engine().begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO environment_sources (
                    id, environment_id, source_kind, uri, source_config_json,
                    storage_provider, storage_auth_mode, enabled,
                    sync_schedule_enabled, created_at, updated_at
                ) VALUES (
                    1, 1, 'metadata', 'abfs://test/metadata/assets.json',
                    :source_config_json, 'adls', 'ambient', 1, 0,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "source_config_json": json.dumps(
                    {
                        "metadata_root_uri": (
                            "abfs://test@datateamtest01.dfs.core.windows.net/metadata"
                        )
                    }
                )
            },
        )

    init_db()

    with get_engine().connect() as connection:
        uri = connection.execute(
            text("SELECT uri FROM environment_sources WHERE id = 1")
        ).scalar_one()
    assert uri == "abfs://test@datateamtest01.dfs.core.windows.net/metadata/assets.json"
