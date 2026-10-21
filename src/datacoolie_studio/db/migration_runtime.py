from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Iterator

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.script.revision import ResolutionError
from sqlalchemy import inspect
from sqlalchemy.engine import Engine, make_url

from datacoolie_studio.core.process_lock import process_lock
from datacoolie_studio.db.migration_recovery import (
    WorkspaceMigrationError,
    create_workspace_backup,
)
from datacoolie_studio.db.models import Base


class WorkspaceSchemaState(StrEnum):
    FRESH = "fresh"
    VERSIONED = "versioned"
    UNVERSIONED = "unversioned"


MIGRATION_SCRIPT_LOCATION = Path(__file__).with_name("migrations")
# Kept as the public workspace-head constant for existing callers. The immutable
# Alembic history now includes the empty-Log data correction after the metadata
# write-operation revision.
BASELINE_REVISION = "b6d4f8a1c209"
MIGRATION_LOCK_TIMEOUT_SECONDS = 30.0

# These are the only non-model tables accepted by the one-time pre-Alembic
# bridge. Keeping the allow-list beside the runtime classifier makes unknown
# unversioned databases fail before any mutation or backup is attempted.
LEGACY_TABLES = frozenset(
    {
        "project_asset_mappings",
        "project_asset_reference_mappings",
        "metadata_sources",
        "etl_log_paths",
        "scan_runs",
        "source_revisions",
        "source_check_states",
        "source_change_states",
        "metadata_source_snapshots",
        "code_artifact_snapshots",
        "environment_read_model_cache_entries",
        "lineage_graph_cache_entries",
        "environment_summary_cache_entries",
        "etl_log_file_manifest",
    }
)

_migration_lock_guard = RLock()


@dataclass(frozen=True)
class WorkspaceMigrationStatus:
    """Read-only migration state used by startup diagnostics and tests."""

    state: WorkspaceSchemaState
    tables: tuple[str, ...]
    current_revisions: tuple[str, ...]
    target_revisions: tuple[str, ...]
    target_heads: tuple[str, ...]
    pending: bool
    unsupported_tables: tuple[str, ...]
    unknown_revisions: tuple[str, ...]


def workspace_schema_state(engine: Engine) -> WorkspaceSchemaState:
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
    return _schema_state_from_tables(tables)


def migration_status(
    engine: Engine,
    revision: str = "head",
) -> WorkspaceMigrationStatus:
    """Return migration state without changing the workspace database."""

    with engine.connect() as connection:
        tables = tuple(sorted(inspect(connection).get_table_names()))
        table_set = set(tables)
        state = _schema_state_from_tables(table_set)

        script = ScriptDirectory.from_config(migration_config(connection))
        target_revisions = _revision_ids(script.get_revisions(revision))
        target_heads = _revision_ids(script.get_heads())

        current_revisions: tuple[str, ...] = ()
        if state == WorkspaceSchemaState.VERSIONED:
            current_revisions = tuple(
                sorted(MigrationContext.configure(connection).get_current_heads())
            )

        unknown_revisions = tuple(
            current
            for current in current_revisions
            if _script_revision(script, current) is None
        )
        unsupported_tables = tuple(
            sorted(
                table_set.difference(Base.metadata.tables)
                .difference(LEGACY_TABLES)
                .difference({"alembic_version"})
            )
        )
        pending = state != WorkspaceSchemaState.VERSIONED or set(
            current_revisions
        ) != set(target_revisions)

    return WorkspaceMigrationStatus(
        state=state,
        tables=tables,
        current_revisions=current_revisions,
        target_revisions=target_revisions,
        target_heads=target_heads,
        pending=pending,
        unsupported_tables=unsupported_tables,
        unknown_revisions=unknown_revisions,
    )


def migration_preflight(
    engine: Engine,
    revision: str = "head",
) -> WorkspaceMigrationStatus:
    """Validate migration state without writing a stamp or backup."""

    status = migration_status(engine, revision)
    if status.state == WorkspaceSchemaState.VERSIONED:
        if not status.current_revisions:
            raise RuntimeError(
                "Versioned Studio workspace has no Alembic revision; "
                "restore a migration backup before retrying"
            )
        if status.unknown_revisions:
            revisions = ", ".join(status.unknown_revisions)
            raise RuntimeError(
                "Studio workspace references unknown Alembic revisions: "
                f"{revisions}. Restore a migration backup before retrying"
            )
    elif status.state == WorkspaceSchemaState.UNVERSIONED:
        if status.unsupported_tables:
            names = ", ".join(status.unsupported_tables)
            raise RuntimeError(
                "Unsupported unversioned Studio workspace schema; unknown tables: "
                f"{names}. No migration stamp was written."
            )
        if not status.tables:
            raise RuntimeError(
                "Legacy bootstrap requires at least one existing workspace table"
            )
    return status


@contextmanager
def workspace_migration_lock(engine: Engine) -> Iterator[Path | None]:
    """Serialize workspace migrations within and across Studio processes."""

    with _migration_lock_guard:
        lock_path = _migration_lock_path(engine)
        if lock_path is None:
            yield None
            return
        with process_lock(
            lock_path,
            timeout_seconds=MIGRATION_LOCK_TIMEOUT_SECONDS,
            owner_label="Studio workspace migration",
        ):
            yield lock_path


def upgrade_fresh_database(engine: Engine, revision: str = "head") -> None:
    """Create a new workspace through the immutable Alembic history."""

    with workspace_migration_lock(engine):
        status = migration_preflight(engine, revision)
        if status.state == WorkspaceSchemaState.VERSIONED:
            _upgrade_versioned_database(engine, revision, status)
            return
        if status.state != WorkspaceSchemaState.FRESH:
            raise RuntimeError(
                "Fresh workspace migration requires an empty database; "
                "use the legacy bootstrap for an existing unversioned database"
            )
        with engine.begin() as connection:
            command.upgrade(migration_config(connection), revision)


def upgrade_database(engine: Engine, revision: str = "head") -> None:
    """Apply ordered Alembic revisions to an already versioned workspace."""

    with workspace_migration_lock(engine):
        status = migration_preflight(engine, revision)
        if status.state != WorkspaceSchemaState.VERSIONED:
            raise RuntimeError(
                "Versioned workspace migration requires an existing Alembic "
                "version table"
            )
        _upgrade_versioned_database(engine, revision, status)


def migration_config(connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATION_SCRIPT_LOCATION))
    config.attributes["connection"] = connection
    return config


def _upgrade_versioned_database(
    engine: Engine,
    revision: str,
    status: WorkspaceMigrationStatus,
) -> None:
    if not status.pending:
        return

    source_revision = ",".join(status.current_revisions)
    backup_path = create_workspace_backup(
        engine,
        source_revision=source_revision,
        target_revision=revision,
        target_heads=status.target_heads,
        source_state=status.state.value,
    )
    try:
        with engine.begin() as connection:
            command.upgrade(migration_config(connection), revision)
        final_status = migration_preflight(engine, revision)
        if final_status.pending:
            raise RuntimeError(
                "Alembic upgrade completed without reaching the requested revision"
            )
    except Exception as exc:
        database = make_url(str(engine.url)).database or "<in-memory database>"
        backup_detail = (
            f"Backup retained at {backup_path}."
            if backup_path is not None
            else "No persistent backup is available for this in-memory database."
        )
        raise WorkspaceMigrationError(
            f"Workspace migration for {database} failed: {exc}. {backup_detail} "
            "Startup is stopped; restore the retained backup or apply a reviewed "
            "forward fix before retrying.",
            backup_path=backup_path,
        ) from exc


def _migration_lock_path(engine: Engine) -> Path | None:
    if engine.dialect.name != "sqlite":
        raise RuntimeError("Studio workspace migrations support SQLite only")
    database = make_url(str(engine.url)).database
    if database in {None, "", ":memory:"}:
        return None
    database_path = Path(database).expanduser().resolve()
    return database_path.with_name(f"{database_path.name}.migration.lock")


def _schema_state_from_tables(tables: set[str]) -> WorkspaceSchemaState:
    if "alembic_version" in tables:
        return WorkspaceSchemaState.VERSIONED
    if not tables:
        return WorkspaceSchemaState.FRESH
    return WorkspaceSchemaState.UNVERSIONED


def _revision_ids(revisions) -> tuple[str, ...]:
    if revisions is None:
        return ()
    if not isinstance(revisions, (tuple, list, set, frozenset)):
        revisions = (revisions,)
    return tuple(
        sorted(getattr(revision, "revision", revision) for revision in revisions)
    )


def _script_revision(script: ScriptDirectory, revision: str):
    try:
        return script.get_revision(revision)
    except ResolutionError:
        return None
