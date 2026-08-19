from __future__ import annotations

import hashlib
import json
from collections.abc import Generator
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import create_engine
from sqlalchemy import event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from datacoolie_studio.core.config import database_url
from datacoolie_studio.db.models import Base
from datacoolie_studio.domains.storage.errors import StorageConfigurationError

_engine = None
_engine_url = None
_session_factory: sessionmaker[Session] | None = None


def _configure_sqlite_connection(dbapi_connection, _record) -> None:
    # WAL + a busy timeout let concurrent worker sessions (e.g. the parallel analytics
    # upgrade) serialize writes instead of failing immediately with "database is locked".
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def get_engine():
    global _engine, _engine_url, _session_factory
    url = database_url()
    if _engine is None or _engine_url != url:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args)
        if url.startswith("sqlite"):
            event.listen(_engine, "connect", _configure_sqlite_connection)
        _engine_url = url
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def init_db() -> None:
    engine = get_engine()
    _migrate_project_reference_mappings(engine)
    _replace_source_observation_schema(engine)
    Base.metadata.create_all(bind=engine)
    _ensure_source_observation_pause_column(engine)
    _ensure_sync_job_running_index(engine)
    _migrate_current_materializations(engine)
    _drop_legacy_derived_cache_tables(engine)
    _ensure_scan_run_columns(engine)
    _ensure_environment_source_columns(engine)
    _ensure_log_file_manifest_columns(engine)
    _ensure_log_stream_state_columns(engine)
    _migrate_environment_sources(engine)
    _migrate_storage_settings(engine)
    _migrate_adls_discovered_source_uris(engine)
    _migrate_source_registrations(engine)
    _migrate_log_sources(engine)
    _migrate_log_file_manifest(engine)
    _backfill_last_successful_sync_at(engine)
    _ensure_log_file_manifest_unique_index(engine)


def _replace_source_observation_schema(engine) -> None:
    """Discard superseded operational observation state during hard cutover."""

    replaced_tables = {"source_revisions", "source_check_states"}
    existing = replaced_tables.intersection(inspect(engine).get_table_names())
    if not existing:
        return
    with engine.begin() as connection:
        for table_name in sorted(existing):
            connection.execute(text(f"DROP TABLE {table_name}"))


def _ensure_source_observation_pause_column(engine) -> None:
    """Add hard-pause state without inheriting pre-policy failure debt."""

    inspector = inspect(engine)
    if "source_observations" not in inspector.get_table_names():
        return
    columns = {
        column["name"]
        for column in inspector.get_columns("source_observations")
    }
    if "automatic_observation_paused_at" in columns:
        return
    timestamp_type = (
        "TIMESTAMP WITH TIME ZONE"
        if engine.dialect.name == "postgresql"
        else "DATETIME"
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE source_observations "
                f"ADD COLUMN automatic_observation_paused_at {timestamp_type}"
            )
        )
        connection.execute(
            text("UPDATE source_observations SET failure_streak = 0")
        )


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


def _ensure_sync_job_running_index(engine) -> None:
    """Enforce at most one running sync job per source on supported databases."""
    inspector = inspect(engine)
    if "sync_jobs" not in inspector.get_table_names():
        return
    index_name = "uq_sync_jobs_running_source"
    if any(index["name"] == index_name for index in inspector.get_indexes("sync_jobs")):
        return
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        raise RuntimeError(
            f"Database dialect {engine.dialect.name} cannot enforce non-overlapping sync jobs"
        )
    with engine.begin() as connection:
        duplicate_ids = list(
            connection.execute(
                text(
                    """
                    SELECT id
                    FROM (
                        SELECT id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY source_id
                                   ORDER BY started_at DESC, id DESC
                               ) AS running_rank
                        FROM sync_jobs
                        WHERE status = 'running'
                    ) ranked
                    WHERE running_rank > 1
                    """
                )
            ).scalars()
        )
        for job_id in duplicate_ids:
            connection.execute(
                text(
                    """
                    UPDATE sync_jobs
                    SET status = 'failed',
                        message = 'Closed while enabling non-overlapping sync jobs',
                        result_json = '{"status":"error","message":"Superseded duplicate running job"}',
                        completed_at = CURRENT_TIMESTAMP
                    WHERE id = :job_id
                    """
                ),
                {"job_id": job_id},
            )
        connection.execute(
            text(
                """
                CREATE UNIQUE INDEX uq_sync_jobs_running_source
                ON sync_jobs (source_id)
                WHERE status = 'running'
                """
            )
        )


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
    if "storage_provider" not in columns:
        statements.append(
            "ALTER TABLE environment_sources "
            "ADD COLUMN storage_provider VARCHAR(20) NOT NULL DEFAULT 'local'"
        )
    if "storage_auth_mode" not in columns:
        statements.append(
            "ALTER TABLE environment_sources "
            "ADD COLUMN storage_auth_mode VARCHAR(30) NOT NULL DEFAULT 'none'"
        )
    if "credential_profile_id" not in columns:
        statements.append(
            "ALTER TABLE environment_sources ADD COLUMN credential_profile_id VARCHAR(36)"
        )
    if "storage_config_json" not in columns:
        statements.append(
            "ALTER TABLE environment_sources ADD COLUMN storage_config_json TEXT"
        )
    if "registration_id" not in columns:
        statements.append(
            "ALTER TABLE environment_sources ADD COLUMN registration_id INTEGER"
        )
    if "sync_schedule_enabled" not in columns:
        statements.append("ALTER TABLE environment_sources ADD COLUMN sync_schedule_enabled BOOLEAN NOT NULL DEFAULT 0")
    if "sync_interval_minutes" not in columns:
        statements.append("ALTER TABLE environment_sources ADD COLUMN sync_interval_minutes INTEGER")
    if "last_scheduled_sync_at" not in columns:
        statements.append("ALTER TABLE environment_sources ADD COLUMN last_scheduled_sync_at DATETIME")
    if "last_successful_sync_at" not in columns:
        timestamp_type = (
            "TIMESTAMP WITH TIME ZONE"
            if engine.dialect.name == "postgresql"
            else "DATETIME"
        )
        statements.append(
            "ALTER TABLE environment_sources "
            f"ADD COLUMN last_successful_sync_at {timestamp_type}"
        )
    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
    _ensure_environment_source_storage_indexes(engine)


def _backfill_last_successful_sync_at(engine) -> None:
    """Backfill durable sync history without overwriting newer values."""

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "environment_sources" not in tables:
        return
    columns = {column["name"] for column in inspector.get_columns("environment_sources")}
    if "last_successful_sync_at" not in columns:
        return

    # Keep the qualifying job list in the sync domain; importing lazily avoids a
    # session -> sync -> session import cycle during application startup.
    from datacoolie_studio.domains.sync.service import QUALIFYING_SYNC_JOB_TYPES

    job_types = sorted(QUALIFYING_SYNC_JOB_TYPES)
    placeholders = ", ".join(f":job_type_{index}" for index, _ in enumerate(job_types))
    params = {f"job_type_{index}": value for index, value in enumerate(job_types)}
    candidates: dict[int, object] = {}

    if "sync_jobs" in tables and job_types:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT source_id, MAX(completed_at) AS completed_at "
                    "FROM sync_jobs "
                    "WHERE status = 'succeeded' "
                    f"AND job_type IN ({placeholders}) "
                    "GROUP BY source_id"
                ),
                params,
            ).mappings()
            for row in rows:
                if row["completed_at"] is not None:
                    candidates[int(row["source_id"])] = row["completed_at"]

    fallback_queries = (
        ("metadata_materializations", "materialized_at"),
        ("code_artifact_materializations", "materialized_at"),
        ("log_file_manifest", "last_seen_at"),
    )
    with engine.begin() as connection:
        for table_name, timestamp_column in fallback_queries:
            if table_name not in tables:
                continue
            rows = connection.execute(
                text(
                    f"SELECT source_id, MAX({timestamp_column}) AS latest_at "
                    f"FROM {table_name} GROUP BY source_id"
                )
            ).mappings()
            for row in rows:
                source_id = int(row["source_id"])
                if source_id in candidates or row["latest_at"] is None:
                    continue
                candidates[source_id] = row["latest_at"]

        for source_id, timestamp in candidates.items():
            connection.execute(
                text(
                    "UPDATE environment_sources "
                    "SET last_successful_sync_at = :timestamp "
                    "WHERE id = :source_id AND last_successful_sync_at IS NULL"
                ),
                {"source_id": source_id, "timestamp": timestamp},
            )


def _ensure_environment_source_storage_indexes(engine) -> None:
    inspector = inspect(engine)
    if "environment_sources" not in inspector.get_table_names():
        return
    existing = {
        index["name"] for index in inspector.get_indexes("environment_sources")
    }
    statements = []
    if "ix_environment_sources_storage_provider" not in existing:
        statements.append(
            "CREATE INDEX ix_environment_sources_storage_provider "
            "ON environment_sources (storage_provider)"
        )
    if "ix_environment_sources_credential_profile" not in existing:
        statements.append(
            "CREATE INDEX ix_environment_sources_credential_profile "
            "ON environment_sources (credential_profile_id)"
        )
    if "ix_environment_sources_registration" not in existing:
        statements.append(
            "CREATE INDEX ix_environment_sources_registration "
            "ON environment_sources (registration_id)"
        )
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _migrate_storage_settings(engine) -> None:
    """Backfill normalized storage fields without requiring cloud connectivity."""
    inspector = inspect(engine)
    if "environment_sources" not in inspector.get_table_names():
        return
    required = {
        "id",
        "uri",
        "source_config_json",
        "storage_provider",
        "storage_auth_mode",
        "storage_config_json",
    }
    columns = {
        column["name"] for column in inspector.get_columns("environment_sources")
    }
    if not required.issubset(columns):
        return

    from datacoolie_studio.domains.storage.uri import parse_storage_uri

    with engine.begin() as connection:
        rows = connection.execute(
            text(
                """
                SELECT id, uri, source_config_json, storage_provider,
                       storage_auth_mode, storage_config_json
                FROM environment_sources
                """
            )
        ).mappings()
        for row in rows:
            try:
                source_config = json.loads(row["source_config_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                source_config = {}
            if not isinstance(source_config, dict):
                source_config = {}
            embedded = source_config.get("storage")
            if not isinstance(embedded, dict):
                embedded = {}

            inferred_provider = parse_storage_uri(str(row["uri"])).provider
            provider = str(
                embedded.get("provider")
                or row["storage_provider"]
                or inferred_provider
            ).lower()
            if provider == "local" and inferred_provider != "local":
                provider = inferred_provider
            auth_mode = str(
                embedded.get("auth_mode")
                or row["storage_auth_mode"]
                or ("none" if provider == "local" else "ambient")
            ).lower()
            if provider != "local" and auth_mode == "none":
                auth_mode = "ambient"

            options = embedded.get("options")
            if options is None and row["storage_config_json"]:
                try:
                    options = json.loads(row["storage_config_json"])
                except (json.JSONDecodeError, TypeError):
                    options = {}
            if not isinstance(options, dict):
                options = {}

            if "storage" in source_config:
                source_config = dict(source_config)
                source_config.pop("storage", None)
            connection.execute(
                text(
                    """
                    UPDATE environment_sources
                    SET storage_provider = :provider,
                        storage_auth_mode = :auth_mode,
                        storage_config_json = :storage_config_json,
                        source_config_json = :source_config_json
                    WHERE id = :source_id
                    """
                ),
                {
                    "provider": provider,
                    "auth_mode": auth_mode,
                    "storage_config_json": (
                        json.dumps(options, sort_keys=True) if options else None
                    ),
                    "source_config_json": (
                        json.dumps(source_config, sort_keys=True)
                        if source_config
                        else None
                    ),
                    "source_id": row["id"],
                },
            )


def _migrate_adls_discovered_source_uris(engine) -> None:
    """Repair metadata URIs created from protocol-less adlfs object names."""
    inspector = inspect(engine)
    if "environment_sources" not in inspector.get_table_names():
        return
    required = {"id", "uri", "source_config_json", "storage_provider", "source_kind"}
    columns = {column["name"] for column in inspector.get_columns("environment_sources")}
    if not required.issubset(columns):
        return

    from datacoolie_studio.domains.storage.uri import canonical_cloud_uri

    with engine.begin() as connection:
        rows = connection.execute(
            text(
                """
                SELECT id, uri, source_config_json
                FROM environment_sources
                WHERE source_kind = 'metadata' AND storage_provider = 'adls'
                """
            )
        ).mappings()
        for row in rows:
            try:
                config = json.loads(row["source_config_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            metadata_root_uri = config.get("metadata_root_uri") if isinstance(config, dict) else None
            if not isinstance(metadata_root_uri, str) or not metadata_root_uri.strip():
                continue
            try:
                source = urlsplit(str(row["uri"]))
                root = urlsplit(canonical_cloud_uri(metadata_root_uri, "adls"))
            except StorageConfigurationError:
                continue
            if "@" in source.netloc or "@" not in root.netloc:
                continue
            if source.netloc.lower() != root.netloc.split("@", 1)[0].lower():
                continue
            repaired = canonical_cloud_uri(
                urlunsplit(("abfs", root.netloc, source.path, "", "")), "adls"
            )
            connection.execute(
                text(
                    """
                    UPDATE environment_sources
                    SET uri = :uri, updated_at = CURRENT_TIMESTAMP
                    WHERE id = :source_id
                    """
                ),
                {"uri": repaired, "source_id": row["id"]},
            )


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
    if "partition_value" not in columns:
        statements.append(
            "ALTER TABLE log_file_manifest ADD COLUMN partition_value DATE"
        )
    if "partition_key" not in columns:
        statements.append(
            "ALTER TABLE log_file_manifest ADD COLUMN partition_key VARCHAR(32)"
        )
    if "partition_format" not in columns:
        statements.append(
            "ALTER TABLE log_file_manifest ADD COLUMN partition_format VARCHAR(100)"
        )
    if not statements:
        return
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _ensure_log_stream_state_columns(engine) -> None:
    inspector = inspect(engine)
    if "log_stream_states" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("log_stream_states")}
    statements = []
    if "checkpoint_partition_key" not in columns:
        statements.append(
            "ALTER TABLE log_stream_states ADD COLUMN checkpoint_partition_key VARCHAR(32)"
        )
    if "last_scanned_partition_key" not in columns:
        statements.append(
            "ALTER TABLE log_stream_states ADD COLUMN last_scanned_partition_key VARCHAR(32)"
        )
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


def _migrate_source_registrations(engine) -> None:
    """Backfill the additive raw/canonical registration contract."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if not {"environment_sources", "source_registrations"}.issubset(tables):
        return
    columns = {column["name"] for column in inspector.get_columns("environment_sources")}
    if "registration_id" not in columns:
        return

    from datacoolie_studio.domains.sources.registration import (
        source_registration_identity,
    )

    with engine.begin() as connection:
        rows = connection.execute(
            text(
                """
                SELECT id, environment_id, source_kind, uri,
                       storage_provider, storage_config_json
                FROM environment_sources
                WHERE registration_id IS NULL
                ORDER BY id
                """
            )
        ).mappings()
        for row in rows:
            try:
                options = json.loads(row["storage_config_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                options = {}
            if not isinstance(options, dict):
                options = {}
            identity_key = source_registration_identity(
                provider=str(row["storage_provider"] or "local"),
                canonical_uri=str(row["uri"]),
                storage_options=options,
            )
            registration_id = connection.execute(
                text(
                    """
                    SELECT id
                    FROM source_registrations
                    WHERE environment_id = :environment_id
                      AND purpose = :purpose
                      AND identity_key = :identity_key
                    """
                ),
                {
                    "environment_id": row["environment_id"],
                    "purpose": row["source_kind"],
                    "identity_key": identity_key,
                },
            ).scalar_one_or_none()
            if registration_id is None:
                result = connection.execute(
                    text(
                        """
                        INSERT INTO source_registrations (
                            environment_id, purpose, input_uri, canonical_uri,
                            identity_key, created_at, updated_at
                        ) VALUES (
                            :environment_id, :purpose, :uri, :uri,
                            :identity_key, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """
                    ),
                    {
                        "environment_id": row["environment_id"],
                        "purpose": row["source_kind"],
                        "uri": row["uri"],
                        "identity_key": identity_key,
                    },
                )
                registration_id = result.lastrowid
            connection.execute(
                text(
                    """
                    UPDATE environment_sources
                    SET registration_id = :registration_id
                    WHERE id = :source_id
                    """
                ),
                {
                    "registration_id": registration_id,
                    "source_id": row["id"],
                },
            )


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


def _ensure_log_file_manifest_unique_index(engine) -> None:
    inspector = inspect(engine)
    table_name = "log_file_manifest"
    if table_name not in inspector.get_table_names():
        return
    index_name = "uq_log_file_manifest_source_kind_uri"
    existing_names = {
        item.get("name")
        for item in (
            *inspector.get_indexes(table_name),
            *inspector.get_unique_constraints(table_name),
        )
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                DELETE FROM {table_name}
                WHERE id IN (
                    SELECT id
                    FROM (
                        SELECT
                            id,
                            ROW_NUMBER() OVER (
                                PARTITION BY source_id, file_kind, file_uri
                                ORDER BY last_seen_at DESC, id DESC
                            ) AS duplicate_rank
                        FROM {table_name}
                    ) ranked
                    WHERE duplicate_rank > 1
                )
                """
            )
        )
        if index_name not in existing_names:
            connection.execute(
                text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} "
                    f"ON {table_name} (source_id, file_kind, file_uri)"
                )
            )


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
