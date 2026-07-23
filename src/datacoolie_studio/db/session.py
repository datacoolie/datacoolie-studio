from __future__ import annotations

import hashlib
import json
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
    _migrate_current_materializations(engine)
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
        "environment_read_model_cache_entries",
    }
    existing = legacy_tables.intersection(inspect(engine).get_table_names())
    if not existing:
        return
    with engine.begin() as connection:
        for table_name in sorted(existing):
            connection.execute(text(f"DROP TABLE {table_name}"))


def _migrate_current_materializations(engine) -> None:
    """Keep only the newest legacy payload for each source, then remove history tables."""
    tables = set(inspect(engine).get_table_names())
    legacy_metadata = "metadata_source_snapshots"
    legacy_code = "code_artifact_snapshots"
    if not {legacy_metadata, legacy_code}.intersection(tables):
        return

    with engine.begin() as connection:
        if legacy_metadata in tables:
            rows = connection.execute(
                text(
                    """
                    SELECT source_id, source_revision_json, editor_document_json,
                           normalized_metadata_json, created_at
                    FROM (
                        SELECT source_id, source_revision_json, editor_document_json,
                               normalized_metadata_json, created_at, id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY source_id ORDER BY created_at DESC, id DESC
                               ) AS row_rank
                        FROM metadata_source_snapshots
                    ) ranked
                    WHERE row_rank = 1
                    """
                )
            ).mappings()
            for row in rows:
                exists = connection.execute(
                    text("SELECT 1 FROM metadata_materializations WHERE source_id = :source_id"),
                    {"source_id": row["source_id"]},
                ).first()
                if exists:
                    continue
                normalizer_version, schema_version = _metadata_payload_versions(row["normalized_metadata_json"])
                connection.execute(
                    text(
                        """
                        INSERT INTO metadata_materializations (
                            source_id, source_revision_json, normalizer_version,
                            materialization_fingerprint, editor_document_json,
                            normalized_metadata_json, materialized_at
                        ) VALUES (
                            :source_id, :source_revision_json, :normalizer_version,
                            :materialization_fingerprint, :editor_document_json,
                            :normalized_metadata_json, :materialized_at
                        )
                        """
                    ),
                    {
                        **dict(row),
                        "normalizer_version": normalizer_version,
                        "materialization_fingerprint": _materialization_fingerprint(
                            row["source_revision_json"], normalizer_version, schema_version
                        ),
                        "materialized_at": row["created_at"],
                    },
                )
            _verify_materialization_migration(
                connection, legacy_metadata, "metadata_materializations"
            )
            connection.execute(text(f"DROP TABLE {legacy_metadata}"))

        if legacy_code in tables:
            rows = connection.execute(
                text(
                    """
                    SELECT source_id, source_revision_json, artifact_manifest_json,
                           module_index_json, diagnostics_json, analyzer_version, created_at
                    FROM (
                        SELECT source_id, source_revision_json, artifact_manifest_json,
                               module_index_json, diagnostics_json, analyzer_version,
                               created_at, id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY source_id ORDER BY created_at DESC, id DESC
                               ) AS row_rank
                        FROM code_artifact_snapshots
                    ) ranked
                    WHERE row_rank = 1
                    """
                )
            ).mappings()
            for row in rows:
                exists = connection.execute(
                    text("SELECT 1 FROM code_artifact_materializations WHERE source_id = :source_id"),
                    {"source_id": row["source_id"]},
                ).first()
                if exists:
                    continue
                connection.execute(
                    text(
                        """
                        INSERT INTO code_artifact_materializations (
                            source_id, source_revision_json, materialization_fingerprint,
                            artifact_manifest_json, module_index_json, diagnostics_json,
                            analyzer_version, materialized_at
                        ) VALUES (
                            :source_id, :source_revision_json, :materialization_fingerprint,
                            :artifact_manifest_json, :module_index_json, :diagnostics_json,
                            :analyzer_version, :materialized_at
                        )
                        """
                    ),
                    {
                        **dict(row),
                        "materialization_fingerprint": _materialization_fingerprint(
                            row["source_revision_json"], row["analyzer_version"], "code-artifact.v1"
                        ),
                        "materialized_at": row["created_at"],
                    },
                )
            _verify_materialization_migration(
                connection, legacy_code, "code_artifact_materializations"
            )
            connection.execute(text(f"DROP TABLE {legacy_code}"))


def _metadata_payload_versions(payload_json: str | None) -> tuple[str, str]:
    try:
        payload = json.loads(payload_json or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return (
        str(payload.get("_normalizer_version") or "legacy"),
        str(payload.get("schema_version") or "metadata-materialization.v1"),
    )


def _materialization_fingerprint(revision_json: str, transformer_version: str, schema_version: str) -> str:
    try:
        revision = json.loads(revision_json)
    except json.JSONDecodeError:
        revision = revision_json
    canonical = json.dumps(
        {
            "revision": revision,
            "transformer_version": transformer_version,
            "schema_version": schema_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _verify_materialization_migration(connection, legacy_table: str, target_table: str) -> None:
    legacy_count = connection.execute(
        text(f"SELECT COUNT(DISTINCT source_id) FROM {legacy_table}")
    ).scalar_one()
    target_count = connection.execute(
        text(
            f"SELECT COUNT(*) FROM {target_table} "
            f"WHERE source_id IN (SELECT DISTINCT source_id FROM {legacy_table})"
        )
    ).scalar_one()
    if int(target_count) != int(legacy_count):
        raise RuntimeError(
            f"Materialization migration mismatch for {legacy_table}: "
            f"expected {legacy_count}, found {target_count}"
        )


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
