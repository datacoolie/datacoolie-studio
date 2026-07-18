from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session, sessionmaker

from datacoolie_studio.core.config import database_url
from datacoolie_studio.db.models import Base

_engine = None
_engine_url = None
_session_factory: sessionmaker[Session] | None = None


def get_engine():
    global _engine, _engine_url, _session_factory
    url = database_url()
    if _engine is None or _engine_url != url:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args)
        _engine_url = url
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def init_db() -> None:
    engine = get_engine()
    _migrate_project_reference_mappings(engine)
    Base.metadata.create_all(bind=engine)
    _drop_legacy_derived_cache_tables(engine)
    _ensure_scan_run_columns(engine)
    _ensure_environment_source_columns(engine)
    _ensure_log_file_manifest_columns(engine)
    _migrate_environment_sources(engine)
    _migrate_log_sources(engine)
    _migrate_log_file_manifest(engine)


def _drop_legacy_derived_cache_tables(engine) -> None:
    """Discard superseded projections only; all source-of-truth tables remain intact."""
    legacy_tables = {
        "lineage_graph_cache_entries",
        "environment_summary_cache_entries",
    }
    existing = legacy_tables.intersection(inspect(engine).get_table_names())
    if not existing:
        return
    with engine.begin() as connection:
        for table_name in sorted(existing):
            connection.execute(text(f"DROP TABLE {table_name}"))


def create_session() -> Session:
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory()


def _ensure_scan_run_columns(engine) -> None:
    inspector = inspect(engine)
    if "scan_runs" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("scan_runs")}
    statements = []
    if "source_id" not in columns:
        statements.append("ALTER TABLE scan_runs ADD COLUMN source_id INTEGER")
    if "result_json" not in columns:
        statements.append("ALTER TABLE scan_runs ADD COLUMN result_json TEXT")
    if not statements:
        return
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _ensure_environment_source_columns(engine) -> None:
    inspector = inspect(engine)
    if "environment_sources" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("environment_sources")}
    statements = []
    if "source_config_json" not in columns:
        statements.append("ALTER TABLE environment_sources ADD COLUMN source_config_json TEXT")
    if "sync_schedule_enabled" not in columns:
        statements.append("ALTER TABLE environment_sources ADD COLUMN sync_schedule_enabled BOOLEAN NOT NULL DEFAULT 0")
    if "sync_interval_minutes" not in columns:
        statements.append("ALTER TABLE environment_sources ADD COLUMN sync_interval_minutes INTEGER")
    if "last_scheduled_sync_at" not in columns:
        statements.append("ALTER TABLE environment_sources ADD COLUMN last_scheduled_sync_at DATETIME")
    if not statements:
        return
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _ensure_log_file_manifest_columns(engine) -> None:
    inspector = inspect(engine)
    if "log_file_manifest" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("log_file_manifest")}
    statements = []
    if "job_id" not in columns:
        statements.append("ALTER TABLE log_file_manifest ADD COLUMN job_id VARCHAR(100)")
    if "log_timestamp" not in columns:
        statements.append("ALTER TABLE log_file_manifest ADD COLUMN log_timestamp DATETIME")
    if "run_date" not in columns:
        statements.append("ALTER TABLE log_file_manifest ADD COLUMN run_date DATE")
    if not statements:
        return
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _migrate_environment_sources(engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "environment_sources" not in tables:
        return
    with engine.begin() as connection:
        if "metadata_sources" in tables:
            connection.execute(
                text(
                    """
                    INSERT INTO environment_sources (environment_id, source_kind, uri, label, enabled, created_at, updated_at)
                    SELECT m.environment_id, 'metadata', m.uri, m.label, m.enabled, m.created_at, m.created_at
                    FROM metadata_sources m
                    WHERE NOT EXISTS (
                        SELECT 1 FROM environment_sources s
                        WHERE s.environment_id = m.environment_id
                          AND s.source_kind = 'metadata'
                          AND s.uri = m.uri
                    )
                    """
                )
            )
        if "etl_log_paths" in tables:
            connection.execute(
                text(
                    """
                    INSERT INTO environment_sources (environment_id, source_kind, uri, label, enabled, created_at, updated_at)
                    SELECT p.environment_id, 'logs', p.uri, p.label, p.enabled, p.created_at, p.created_at
                    FROM etl_log_paths p
                    WHERE NOT EXISTS (
                        SELECT 1 FROM environment_sources s
                        WHERE s.environment_id = p.environment_id
                          AND s.source_kind = 'logs'
                          AND s.uri = p.uri
                    )
                    """
                )
            )
        if "scan_runs" in tables:
            _migrate_scan_run_read_checks(connection, tables)


def _migrate_log_sources(engine) -> None:
    inspector = inspect(engine)
    if "environment_sources" not in inspector.get_table_names():
        return
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE environment_sources
                SET source_kind = 'logs'
                WHERE source_kind = 'etl_logs'
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE source_revisions
                SET source_kind = 'logs'
                WHERE source_kind = 'etl_logs'
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE sync_jobs
                SET source_kind = 'logs'
                WHERE source_kind = 'etl_logs'
                """
            )
        )


def _migrate_log_file_manifest(engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "etl_log_file_manifest" not in tables or "log_file_manifest" not in tables:
        return
    existing_columns = {column["name"] for column in inspector.get_columns("log_file_manifest")}
    if "source_id" not in existing_columns:
        return
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO log_file_manifest (
                    source_id,
                    file_uri,
                    file_kind,
                    revision_json,
                    row_count,
                    status,
                    first_seen_at,
                    last_seen_at
                )
                SELECT
                    old.log_path_id,
                    old.file_uri,
                    old.file_kind,
                    old.revision_json,
                    old.row_count,
                    old.status,
                    old.first_seen_at,
                    old.last_seen_at
                FROM etl_log_file_manifest old
                WHERE NOT EXISTS (
                    SELECT 1 FROM log_file_manifest new
                    WHERE new.source_id = old.log_path_id
                      AND new.file_uri = old.file_uri
                )
                """
            )
        )
        connection.execute(text("DROP TABLE etl_log_file_manifest"))


def _migrate_project_reference_mappings(engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    legacy_table = "project_asset_mappings"
    target_table = "project_reference_mappings"
    if legacy_table in tables and target_table not in tables:
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE {legacy_table} RENAME TO {target_table}"))
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
    if target_table not in tables:
        return
    columns = {column["name"] for column in inspector.get_columns(target_table)}
    with engine.begin() as connection:
        if "reference_kind" in columns and "reference_type" not in columns:
            connection.execute(text(f"ALTER TABLE {target_table} ADD COLUMN reference_type VARCHAR(50)"))
        if "reference_kind" in columns:
            connection.execute(
                text(
                    f"""
                    UPDATE {target_table}
                    SET reference_type = reference_kind
                    WHERE reference_type IS NULL OR reference_type = ''
                    """
                )
            )
            connection.execute(text(f"ALTER TABLE {target_table} DROP COLUMN reference_kind"))


def _migrate_scan_run_read_checks(connection, tables: set[str]) -> None:
    columns = {column["name"] for column in connection.execute(text("PRAGMA table_info(scan_runs)")).mappings()}
    if not {"source_id", "result_json"}.issubset(columns):
        return
    rows = connection.execute(
        text(
            """
            SELECT id, environment_id, source_id, source_type, status, result_json, created_at
            FROM scan_runs
            WHERE source_id IS NOT NULL
              AND source_type IN ('metadata', 'etl_logs', 'logs')
            ORDER BY created_at DESC, id DESC
            """
        )
    ).mappings()
    seen: set[tuple[str, int]] = set()
    for row in rows:
        source_kind = "logs" if str(row["source_type"]) == "etl_logs" else str(row["source_type"])
        key = (source_kind, int(row["source_id"]))
        if key in seen:
            continue
        seen.add(key)
        legacy_uri = _legacy_source_uri(connection, tables, str(row["source_type"]), int(row["source_id"]))
        if not legacy_uri:
            continue
        connection.execute(
            text(
                """
                UPDATE environment_sources
                SET read_check_status = :status,
                    read_checked_at = :checked_at,
                    read_check_result_json = :result_json,
                    updated_at = :checked_at
                WHERE environment_id = :environment_id
                  AND source_kind = :source_kind
                  AND uri = :uri
                  AND read_checked_at IS NULL
                """
            ),
            {
                "status": row["status"],
                "checked_at": row["created_at"],
                "result_json": row["result_json"],
                "environment_id": row["environment_id"],
                "source_kind": source_kind,
                "uri": legacy_uri,
            },
        )


def _legacy_source_uri(connection, tables: set[str], source_type: str, source_id: int) -> str | None:
    if source_type == "metadata" and "metadata_sources" in tables:
        row = connection.execute(text("SELECT uri FROM metadata_sources WHERE id = :id"), {"id": source_id}).fetchone()
        return str(row[0]) if row else None
    if source_type in {"etl_logs", "logs"} and "etl_log_paths" in tables:
        row = connection.execute(text("SELECT uri FROM etl_log_paths WHERE id = :id"), {"id": source_id}).fetchone()
        return str(row[0]) if row else None
    return None


def get_session() -> Generator[Session, None, None]:
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()
