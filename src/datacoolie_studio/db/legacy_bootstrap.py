from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from datacoolie_studio.db.migration_runtime import (
    BASELINE_REVISION,
    WorkspaceSchemaState,
    migration_config,
    migration_preflight,
    workspace_migration_lock,
)
from datacoolie_studio.db.migration_recovery import (
    WorkspaceMigrationError,
    create_workspace_backup,
)
from datacoolie_studio.db.models import Base


def bootstrap_unversioned_database(engine: Engine) -> Path | None:
    """Normalize a known pre-Alembic database and stamp the verified baseline."""

    with workspace_migration_lock(engine):
        status = migration_preflight(engine)
        if status.state == WorkspaceSchemaState.VERSIONED:
            return None
        if status.state != WorkspaceSchemaState.UNVERSIONED:
            raise RuntimeError(
                "Legacy bootstrap requires an existing unversioned workspace database"
            )

        backup_path = create_workspace_backup(
            engine,
            source_revision=None,
            target_revision=BASELINE_REVISION,
            target_heads=status.target_heads,
            source_state=status.state.value,
            allow_existing_foreign_key_violations=True,
        )

        from datacoolie_studio.db.legacy_operations import run_legacy_startup_migrations

        try:
            run_legacy_startup_migrations(engine)
            _verify_baseline(engine)
            with engine.begin() as connection:
                from alembic import command

                command.stamp(migration_config(connection), BASELINE_REVISION)
        except Exception as exc:
            database = str(engine.url)
            backup_detail = (
                f"Backup retained at {backup_path}."
                if backup_path is not None
                else "No persistent backup is available for this in-memory database."
            )
            raise WorkspaceMigrationError(
                f"Legacy workspace bootstrap for {database} failed: {exc}. "
                f"{backup_detail} Startup is stopped; restore the retained backup "
                "or apply a reviewed forward fix before retrying.",
                backup_path=backup_path,
            ) from exc
        return backup_path


def _verify_baseline(engine: Engine) -> None:
    inspector = inspect(engine)
    actual_tables = set(inspector.get_table_names())
    required_tables = set(Base.metadata.tables)
    missing_tables = required_tables.difference(actual_tables)
    if missing_tables:
        raise RuntimeError(
            "Legacy workspace bootstrap did not produce the baseline tables: "
            + ", ".join(sorted(missing_tables))
        )

    missing_columns: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name not in actual_columns:
                missing_columns.append(f"{table_name}.{column.name}")
    if missing_columns:
        raise RuntimeError(
            "Legacy workspace bootstrap did not produce baseline columns: "
            + ", ".join(sorted(missing_columns))
        )

    with engine.connect() as connection:
        integrity = connection.execute(text("PRAGMA integrity_check")).scalar_one()
        foreign_keys = connection.execute(text("PRAGMA foreign_key_check")).all()
    if integrity != "ok":
        raise RuntimeError(f"SQLite integrity check failed after legacy bootstrap: {integrity}")
    if foreign_keys:
        raise RuntimeError(
            "SQLite foreign-key check failed after legacy bootstrap: "
            f"{foreign_keys[:3]}"
        )
