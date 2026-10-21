from __future__ import annotations

from collections.abc import Generator
import hashlib
import os
from pathlib import Path
from tempfile import gettempdir

from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

from datacoolie_studio.core.config import database_url
from datacoolie_studio.db.migration_runtime import (
    WorkspaceSchemaState,
    upgrade_database,
    upgrade_fresh_database,
    workspace_schema_state,
)

_engine = None
_engine_url = None
_session_factory: sessionmaker[Session] | None = None


class WorkspaceMutationPendingError(RuntimeError):
    """A caller owns uncommitted mutations that a new workflow must preserve."""


@event.listens_for(Session, "after_begin")
def _track_session_transaction(session, transaction, connection):
    if transaction.parent is None and connection.dialect.name == "sqlite":
        session.info["workspace_sqlite_changes"] = connection.connection.driver_connection.total_changes


@event.listens_for(Session, "after_flush")
def _track_session_flush(session, flush_context):
    session.info["workspace_flushed_writes"] = True


@event.listens_for(Session, "after_transaction_end")
def _clear_session_transaction(session, transaction):
    if transaction.parent is None:
        session.info.pop("workspace_flushed_writes", None)
        session.info.pop("workspace_sqlite_changes", None)


def workspace_identity(session: Session) -> str:
    """Canonical database identity from the actual bind, including URL aliases."""
    bind = session.get_bind()
    engine = getattr(bind, "engine", bind)
    database = engine.url.database
    if database and database != ":memory:":
        return os.path.normcase(str(Path(database).expanduser().resolve()))
    return f"memory:{id(engine)}"


def workspace_lock_path(session: Session, resource: str) -> Path:
    identity = workspace_identity(session)
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return Path(gettempdir()) / "datacoolie-studio-locks" / digest / f"{resource}.lock"


def ensure_workspace_transaction(session: Session) -> None:
    """SQLite legacy mode must BEGIN before a first-write SAVEPOINT can commit."""
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        driver = connection.connection.driver_connection
        if not driver.in_transaction:
            connection.exec_driver_sql("BEGIN")


def refresh_workspace_reads(session: Session) -> None:
    """End a clean read snapshot; never discard a caller's pending mutation."""
    if session.new or session.dirty or session.deleted:
        raise WorkspaceMutationPendingError("Commit pending workspace mutations before publication")
    if session.in_transaction():
        connection = session.connection()
        changed = False
        if connection.dialect.name == "sqlite":
            baseline = session.info.get("workspace_sqlite_changes")
            changed = baseline is not None and connection.connection.driver_connection.total_changes != baseline
        if session.info.get("workspace_flushed_writes") or connection.info.get("workspace_transaction_writes") or changed:
            raise WorkspaceMutationPendingError("Commit flushed workspace mutations before publication")
        session.rollback()


def _begin_workspace_transaction(connection):
    connection.info["workspace_transaction_writes"] = False


def _track_workspace_writes(connection, cursor, statement, parameters, context, executemany):
    if statement.lstrip().split(None, 1)[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER"}:
        connection.info["workspace_transaction_writes"] = True


def _configure_sqlite_connection(dbapi_connection, _record) -> None:
    # WAL + a busy timeout let concurrent worker sessions (e.g. the parallel analytics
    # upgrade) serialize writes instead of failing immediately with "database is locked".
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def get_engine():
    global _engine, _engine_url, _session_factory
    url = database_url()
    if _engine is None or _engine_url != url:
        if _engine is not None:
            _dispose_engine()
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args)
        if url.startswith("sqlite"):
            event.listen(_engine, "connect", _configure_sqlite_connection)
            event.listen(_engine, "begin", _begin_workspace_transaction)
            event.listen(_engine, "before_cursor_execute", _track_workspace_writes)
        _engine_url = url
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def _dispose_engine() -> None:
    """Dispose the process-local engine before a test or tool changes database URLs."""

    global _engine, _engine_url, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _engine_url = None
    _session_factory = None


def create_session() -> Session:
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory()


def init_db() -> None:
    engine = get_engine()
    state = workspace_schema_state(engine)
    if state is WorkspaceSchemaState.FRESH:
        upgrade_fresh_database(engine)
    elif state is WorkspaceSchemaState.UNVERSIONED:
        from datacoolie_studio.db.legacy_bootstrap import bootstrap_unversioned_database

        bootstrap_unversioned_database(engine)
    else:
        upgrade_database(engine)


def get_session() -> Generator[Session, None, None]:
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()
