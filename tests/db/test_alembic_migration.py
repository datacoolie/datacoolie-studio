from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from datacoolie_studio.db.migration_runtime import (
    BASELINE_REVISION,
    WorkspaceSchemaState,
    migration_status,
    migration_config,
    upgrade_database,
    workspace_schema_state,
    upgrade_fresh_database,
)
from datacoolie_studio.db.models import Base, Environment, EnvironmentSource, Project
from datacoolie_studio.db.session import create_session, get_engine, init_db


def _use_database(tmp_path: Path, monkeypatch) -> Path:
    db_path = tmp_path / "studio.db"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    return db_path


@pytest.mark.parametrize("overlap", [False, True])
def test_recoverable_write_index_upgrade_preserves_rows_and_rejects_overlap(studio_paths, overlap):
    from datetime import datetime, timezone
    from datacoolie_studio.db.models import MetadataWriteOperation, MetadataWriteItem

    init_db()
    engine = get_engine()
    with engine.begin() as connection:
        command.downgrade(migration_config(connection), "f2a6c8b1d903")
    session = create_session()
    environment = Environment(name="dev", project=Project(name="migration-ownership"))
    session.add(environment)
    session.flush()
    for index, state in enumerate(["failed", "prepared"] if overlap else ["failed"]):
        operation = MetadataWriteOperation(environment_id=environment.id, operation_key=f"operation-{index}", state=state,
                                           editor_fingerprint="e" * 64, plan_fingerprint="p" * 64,
                                           expires_at=datetime(2026, 10, 8, tzinfo=timezone.utc))
        session.add(operation)
        session.flush()
        session.add(MetadataWriteItem(operation_id=operation.id, item_order=0, kind="metadata", state="failed",
                                      source_uri="metadata.json", target_uri="metadata.json", payload_ref="objects/retained-payload",
                                      backup_ref="objects/retained-backup"))
    session.commit()
    session.close()
    def snapshot():
        with engine.connect() as connection:
            return (
                connection.execute(text("SELECT * FROM metadata_write_operations ORDER BY id")).all(),
                connection.execute(text("SELECT * FROM metadata_write_items ORDER BY id")).all(),
                connection.execute(text("SELECT sql FROM sqlite_master WHERE name='uq_metadata_write_operations_active_environment'")).scalar_one(),
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one(),
            )
    before = snapshot()
    if overlap:
        with pytest.raises(RuntimeError, match=r"operation-0 \(failed\).*operation-1 \(prepared\)") as error:
            upgrade_database(engine)
        assert snapshot() == before
        backup = error.value.backup_path
        assert backup.is_file()
        manifest = json.loads(backup.with_suffix(".json").read_text(encoding="utf-8"))
        assert manifest["backup_sha256"] == hashlib.sha256(backup.read_bytes()).hexdigest()
        with sqlite3.connect(backup) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert connection.execute("SELECT COUNT(*) FROM metadata_write_items").fetchone() == (2,)
    else:
        upgrade_database(engine)
        after = snapshot()
        assert after[:2] == before[:2]
        assert "'failed'" in after[2]
        assert after[3] == BASELINE_REVISION
        upgrade_database(engine)
        assert snapshot() == after
        with engine.begin() as connection:
            command.check(migration_config(connection))


def test_fresh_workspace_upgrades_to_alembic_head(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _use_database(tmp_path, monkeypatch)

    assert workspace_schema_state(get_engine()) is WorkspaceSchemaState.FRESH
    init_db()

    engine = get_engine()
    connection = engine.connect()
    try:
        tables = set(inspect(connection).get_table_names())
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        connection.close()
    engine.dispose()

    assert set(Base.metadata.tables) <= tables
    assert revision == BASELINE_REVISION
    assert "alembic_version" in tables


def test_migration_status_is_read_only_and_reports_head_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _use_database(tmp_path, monkeypatch)

    fresh = migration_status(get_engine())
    assert fresh.state is WorkspaceSchemaState.FRESH
    assert fresh.pending is True
    assert fresh.target_heads == (BASELINE_REVISION,)

    init_db()
    current = migration_status(get_engine())
    get_engine().dispose()

    assert current.state is WorkspaceSchemaState.VERSIONED
    assert current.current_revisions == (BASELINE_REVISION,)
    assert current.pending is False
    assert current.unsupported_tables == ()


def test_versioned_workspace_startup_is_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _use_database(tmp_path, monkeypatch)
    init_db()

    engine = get_engine()
    connection = engine.connect()
    try:
        before = connection.execute(
            text(
                "SELECT name, type, sql FROM sqlite_master "
                "WHERE type IN ('table', 'index') ORDER BY type, name"
            )
        ).all()
    finally:
        connection.close()

    init_db()

    connection = engine.connect()
    try:
        after = connection.execute(
            text(
                "SELECT name, type, sql FROM sqlite_master "
                "WHERE type IN ('table', 'index') ORDER BY type, name"
            )
        ).all()
    finally:
        connection.close()
    state = workspace_schema_state(engine)
    engine.dispose()

    assert after == before
    assert state is WorkspaceSchemaState.VERSIONED


def test_empty_log_validation_migration_is_bounded_and_reversible(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _use_database(tmp_path, monkeypatch)
    init_db()
    engine = get_engine()

    with engine.connect() as connection:
        command.downgrade(migration_config(connection), "d7b34e91c6a2")

    legacy_result = {
        "source_id": 1,
        "source_kind": "logs",
        "status": "error",
        "message": "No execution or system log files found",
        "record_counts": {},
        "records_scanned": 0,
        "errors": [{"message": "No execution or system log files found"}],
    }
    unrelated_result = {
        "source_id": 2,
        "source_kind": "logs",
        "status": "error",
        "message": "Log storage is not accessible",
        "record_counts": {},
        "records_scanned": 0,
        "errors": [{"code": "storage_access_failed"}],
    }
    session = create_session()
    try:
        project = Project(name="migration-empty-log")
        environment = Environment(name="dev", project=project)
        session.add_all(
            [
                EnvironmentSource(
                    environment=environment,
                    source_kind="logs",
                    uri="C:/logs/empty",
                    label="empty",
                    read_check_status="error",
                    read_check_result_json=json.dumps(legacy_result, sort_keys=True),
                ),
                EnvironmentSource(
                    environment=environment,
                    source_kind="logs",
                    uri="C:/logs/unrelated",
                    label="unrelated",
                    read_check_status="error",
                    read_check_result_json=json.dumps(unrelated_result, sort_keys=True),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    upgrade_database(engine)
    session = create_session()
    try:
        sources = session.query(EnvironmentSource).order_by(EnvironmentSource.id).all()
        assert sources[0].read_check_status == "warning"
        migrated = json.loads(sources[0].read_check_result_json or "{}")
        assert migrated["empty"] is True
        assert migrated["migration_marker"] == "e4c8a1b9d2f0"
        assert sources[1].read_check_status == "error"
        assert sources[1].read_check_result_json == json.dumps(
            unrelated_result, sort_keys=True
        )
    finally:
        session.close()

    init_db()
    session = create_session()
    try:
        repeated = session.get(EnvironmentSource, 1)
        assert repeated is not None
        assert repeated.read_check_status == "warning"
    finally:
        session.close()

    with engine.connect() as connection:
        command.downgrade(migration_config(connection), "d7b34e91c6a2")
    session = create_session()
    try:
        downgraded = session.get(EnvironmentSource, 1)
        assert downgraded is not None
        assert downgraded.read_check_status == "error"
        restored = json.loads(downgraded.read_check_result_json or "{}")
        assert restored["message"] == "No execution or system log files found"
        assert "migration_marker" not in restored
    finally:
        session.close()


def test_versioned_workspace_does_not_run_legacy_operations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _use_database(tmp_path, monkeypatch)
    init_db()

    def fail_legacy_operations(_engine) -> None:
        raise AssertionError("legacy operations must not run for a versioned workspace")

    import datacoolie_studio.db.legacy_operations as legacy_operations

    monkeypatch.setattr(legacy_operations, "run_legacy_startup_migrations", fail_legacy_operations)
    init_db()


def test_known_unversioned_workspace_is_backed_up_and_stamped(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = _use_database(tmp_path, monkeypatch)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY,
                name VARCHAR(255) NOT NULL UNIQUE,
                description TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE TABLE environments (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                name VARCHAR(100) NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            INSERT INTO projects VALUES (1, 'legacy', 'preserve me', '2026-01-01', '2026-01-01');
            INSERT INTO environments VALUES (1, 1, 'dev', '2026-01-01', '2026-01-01');
            """
        )

    init_db()
    engine = get_engine()
    connection = engine.connect()
    try:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        project = connection.execute(
            text("SELECT name, description FROM projects WHERE id = 1")
        ).one()
    finally:
        connection.close()
    backups = list((tmp_path / "backups" / "workspace-migrations").glob("*.db"))
    manifests = list((tmp_path / "backups" / "workspace-migrations").glob("*.json"))

    init_db()
    engine.dispose()

    assert revision == BASELINE_REVISION
    assert project == ("legacy", "preserve me")
    assert len(backups) == 1
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["database_identity"] == str(db_path.resolve())
    assert manifest["source_state"] == "unversioned"
    assert manifest["source_revision"] is None
    assert manifest["target_revision"] == BASELINE_REVISION
    assert manifest["target_head"] == [BASELINE_REVISION]
    assert manifest["backup_path"] == str(backups[0].resolve())
    assert manifest["backup_sha256"] == hashlib.sha256(backups[0].read_bytes()).hexdigest()
    assert manifest["foreign_key_check"] == "ok"
    assert manifest["foreign_key_violation_count"] == 0
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert backup.execute("SELECT name FROM projects WHERE id = 1").fetchone() == ("legacy",)


def test_legacy_reference_tables_are_reconciled_before_stamping(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = _use_database(tmp_path, monkeypatch)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY,
                name VARCHAR(255) NOT NULL UNIQUE,
                description TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            INSERT INTO projects VALUES (1, 'legacy', NULL, '2026-01-01', '2026-01-01');

            CREATE TABLE project_reference_mappings (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                reference_type VARCHAR(50) NOT NULL,
                reference_normalized_value TEXT NOT NULL,
                reference_signature_json TEXT NOT NULL,
                target_identifier_kind VARCHAR(50) NOT NULL,
                target_normalized_value TEXT NOT NULL,
                target_display_value TEXT NOT NULL,
                note TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            INSERT INTO project_reference_mappings VALUES (
                10, 1, 'table_reference', 'already_current', '{}',
                'logical_table', 'curated.already_current', 'curated.already_current',
                NULL, '2026-01-01', '2026-01-02'
            );

            CREATE TABLE project_asset_mappings (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                reference_type VARCHAR(50) NOT NULL,
                reference_normalized_value TEXT NOT NULL,
                reference_namespace TEXT,
                reference_provenance VARCHAR(50),
                reference_signature_json TEXT NOT NULL,
                target_identifier_kind VARCHAR(50) NOT NULL,
                target_normalized_value TEXT NOT NULL,
                target_display_value TEXT NOT NULL,
                note TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            INSERT INTO project_asset_mappings VALUES
                (1, 1, 'table_reference', 'legacy_orders', NULL, NULL, '{}',
                 'logical_table', 'curated.orders', 'curated.orders', 'legacy',
                 '2025-12-01', '2025-12-01'),
                (2, 1, 'table_reference', 'already_current', NULL, NULL, '{}',
                 'logical_table', 'legacy.orders', 'legacy.orders', 'stale duplicate',
                 '2025-12-01', '2025-12-01'),
                (3, 1, 'table_reference', 'legacy_orders', NULL, NULL, '{}',
                 'logical_table', 'curated.orders-duplicate', 'curated.orders-duplicate',
                 'duplicate legacy row', '2025-12-02', '2025-12-02');

            CREATE TABLE project_asset_reference_mappings (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                reference_kind VARCHAR(50) NOT NULL,
                reference_normalized_value TEXT NOT NULL,
                reference_namespace TEXT,
                reference_provenance VARCHAR(50),
                reference_signature_json TEXT NOT NULL,
                target_identifier_kind VARCHAR(50) NOT NULL,
                target_normalized_value TEXT NOT NULL,
                target_display_value TEXT NOT NULL,
                note TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );

            CREATE TABLE source_change_states (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL,
                source_kind VARCHAR(50) NOT NULL,
                status VARCHAR(20) NOT NULL,
                observed_fingerprint VARCHAR(64),
                observed_details_json TEXT,
                observed_at DATETIME,
                applied_fingerprint VARCHAR(64),
                applied_at DATETIME,
                first_changed_at DATETIME,
                error_json TEXT,
                first_error_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                CONSTRAINT uq_source_change_state_source UNIQUE (source_id),
                FOREIGN KEY(source_id) REFERENCES environment_sources (id) ON DELETE CASCADE
            );
            """
        )

    init_db()

    engine = get_engine()
    with engine.connect() as connection:
        mappings = connection.execute(
            text(
                "SELECT reference_normalized_value, target_normalized_value "
                "FROM project_reference_mappings "
                "ORDER BY reference_normalized_value"
            )
        ).all()
        tables = set(inspect(connection).get_table_names())
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    engine.dispose()

    backups = list((tmp_path / "backups" / "workspace-migrations").glob("*.db"))
    assert mappings == [
        ("already_current", "curated.already_current"),
        ("legacy_orders", "curated.orders"),
    ]
    assert "project_asset_mappings" not in tables
    assert "project_asset_reference_mappings" not in tables
    assert "source_change_states" not in tables
    assert revision == BASELINE_REVISION
    assert len(backups) == 1

    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute(
            "SELECT count(*) FROM project_asset_mappings"
        ).fetchone() == (3,)
        assert backup.execute(
            "SELECT count(*) FROM source_change_states"
        ).fetchone() == (0,)


def test_legacy_bootstrap_repairs_orphaned_rows_after_retaining_backup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = _use_database(tmp_path, monkeypatch)
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO source_observations (
                    source_id, last_outcome, pending_changes,
                    unchanged_streak, failure_streak, created_at, updated_at
                ) VALUES (
                    999, 'error', 1, 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )
    engine.dispose()

    init_db()

    engine = get_engine()
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM source_observations WHERE source_id = 999")
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == BASELINE_REVISION
    engine.dispose()

    backups = list((tmp_path / "backups" / "workspace-migrations").glob("*.db"))
    manifests = list((tmp_path / "backups" / "workspace-migrations").glob("*.json"))
    assert len(backups) == 1
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["foreign_key_check"] == "existing_violations"
    assert manifest["foreign_key_violation_count"] == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute(
            "SELECT count(*) FROM source_observations WHERE source_id = 999"
        ).fetchone() == (1,)


def test_unknown_unversioned_workspace_fails_before_stamp(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = _use_database(tmp_path, monkeypatch)
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE unrelated_state (id INTEGER PRIMARY KEY)")

    with pytest.raises(RuntimeError, match="unknown tables"):
        init_db()

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name = 'alembic_version'"
        ).fetchone() == (0,)
    assert not (tmp_path / "backups" / "workspace-migrations").exists()


def test_legacy_bootstrap_failure_keeps_backup_and_does_not_stamp(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = _use_database(tmp_path, monkeypatch)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY,
                name VARCHAR(255) NOT NULL UNIQUE,
                description TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            """
        )

    def fail_legacy_upgrade(_engine) -> None:
        raise RuntimeError("injected legacy bootstrap failure")

    import datacoolie_studio.db.legacy_operations as legacy_operations

    monkeypatch.setattr(legacy_operations, "run_legacy_startup_migrations", fail_legacy_upgrade)
    with pytest.raises(RuntimeError, match="injected legacy bootstrap failure") as error:
        init_db()

    engine = get_engine()
    connection = engine.connect()
    try:
        assert connection.execute(
            text("SELECT count(*) FROM sqlite_master WHERE name = 'alembic_version'")
        ).scalar_one() == 0
    finally:
        connection.close()
        engine.dispose()
    backups = list((tmp_path / "backups" / "workspace-migrations").glob("*.db"))
    assert len(backups) == 1
    assert error.value.__cause__ is not None
    assert str(backups[0]) in str(error.value)


def test_workspace_migration_lock_serializes_startup_threads(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = _use_database(tmp_path, monkeypatch)
    database_url = f"sqlite:///{db_path.as_posix()}"

    def upgrade() -> None:
        engine = create_engine(database_url)
        try:
            upgrade_fresh_database(engine)
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: upgrade(), range(2)))

    assert results == [None, None]
    engine = get_engine()
    assert migration_status(engine).current_revisions == (BASELINE_REVISION,)
    engine.dispose()


def test_versioned_upgrade_failure_reports_retained_backup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = _use_database(tmp_path, monkeypatch)
    init_db()

    import datacoolie_studio.db.migration_runtime as migration_runtime

    engine = get_engine()
    pending = replace(migration_status(engine), pending=True)
    monkeypatch.setattr(migration_runtime, "migration_status", lambda *_args, **_kwargs: pending)

    def fail_upgrade(_config, _revision) -> None:
        raise RuntimeError("injected Alembic upgrade failure")

    monkeypatch.setattr(migration_runtime.command, "upgrade", fail_upgrade)
    with pytest.raises(RuntimeError, match="injected Alembic upgrade failure") as error:
        migration_runtime.upgrade_database(engine)

    backup_path = error.value.backup_path
    assert backup_path is not None
    assert backup_path.exists()
    manifest = json.loads(backup_path.with_suffix(".json").read_text(encoding="utf-8"))
    assert manifest["source_state"] == "versioned"
    assert manifest["source_revision"] == BASELINE_REVISION
    assert manifest["backup_path"] == str(backup_path.resolve())
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            BASELINE_REVISION,
        )
    engine.dispose()
