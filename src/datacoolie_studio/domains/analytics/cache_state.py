from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from datacoolie_studio.domains.analytics import schema


def analytics_meta(conn) -> dict[str, Any] | None:
    """Read the singleton publication state for an analytics database."""

    if not schema.table_exists(conn, schema.ANALYTICS_META_TABLE):
        return None
    row = conn.execute(
        f"""
        SELECT schema_version, generation, build_state, published_at
        FROM {schema.ANALYTICS_META_TABLE}
        WHERE singleton_id = 1
        """
    ).fetchone()
    if row is None:
        return None
    return {
        "schema_version": int(row[0]),
        "generation": int(row[1]),
        "build_state": str(row[2]),
        "published_at": row[3].isoformat() if row[3] is not None else None,
    }


def candidate_is_resumable(conn) -> bool:
    """Return whether a partial candidate has a safe base-table contract."""

    meta = analytics_meta(conn)
    if meta is None or meta["schema_version"] != schema.ANALYTICS_SCHEMA_VERSION:
        return False
    if meta["build_state"] not in {"building", "rebuild_required", "ready"}:
        return False
    for table_name in (
        schema.DATAFLOW_TABLE,
        schema.JOB_TABLE,
        schema.CACHE_SOURCES_TABLE,
    ):
        if not schema.table_exists(conn, table_name):
            return False
    return all(
        schema.typed_table_schema_is_current(conn, table_name, column_types)
        for table_name, column_types in (
            (schema.DATAFLOW_TABLE, schema.DATAFLOW_COLUMN_TYPES),
            (schema.JOB_TABLE, schema.JOB_COLUMN_TYPES),
        )
    )


def mark_cache_source(conn, source_id: int, *, refreshed_at: datetime) -> None:
    schema.ensure_cache_sources_table(conn)
    current = conn.execute(
        f"SELECT generation FROM {schema.CACHE_SOURCES_TABLE} WHERE source_id = ?",
        [source_id],
    ).fetchone()
    generation = int(current[0] or 0) + 1 if current is not None else 1
    conn.execute(
        f"DELETE FROM {schema.CACHE_SOURCES_TABLE} WHERE source_id = ?",
        [source_id],
    )
    conn.execute(
        f"INSERT INTO {schema.CACHE_SOURCES_TABLE} "
        "(source_id, refreshed_at, generation) VALUES (?, ?::TIMESTAMPTZ, ?)",
        [source_id, refreshed_at.isoformat(), generation],
    )


def cache_source_ids(conn) -> set[int]:
    if not schema.table_exists(conn, schema.CACHE_SOURCES_TABLE):
        return set()
    return {
        int(row[0])
        for row in conn.execute(
            f"SELECT source_id FROM {schema.CACHE_SOURCES_TABLE}"
        ).fetchall()
    }


def candidate_source_ids(candidate_path: Path) -> set[int]:
    """Return every source represented in a partial candidate."""

    if not candidate_path.exists():
        return set()
    from datacoolie_studio.domains.analytics import access as analytics_access

    with analytics_access.analytics_maintenance_lock:
        conn = analytics_access.connect(candidate_path, read_only=True)
        try:
            source_ids: set[int] = set()
            for table_name in (
                schema.DATAFLOW_TABLE,
                schema.JOB_TABLE,
                schema.CACHE_SOURCES_TABLE,
            ):
                if schema.table_exists(conn, table_name):
                    column = "source_id" if table_name == schema.CACHE_SOURCES_TABLE else "_source_id"
                    if column in schema.table_columns(conn, table_name):
                        source_ids.update(
                            int(row[0])
                            for row in conn.execute(
                                f"SELECT DISTINCT {column} FROM {table_name} "
                                f"WHERE {column} IS NOT NULL"
                            ).fetchall()
                        )
            return source_ids
        finally:
            conn.close()


def cache_source_generations(conn) -> dict[int, int]:
    if not schema.table_exists(conn, schema.CACHE_SOURCES_TABLE):
        return {}
    return {
        int(source_id): int(generation or 0)
        for source_id, generation in conn.execute(
            f"SELECT source_id, generation FROM {schema.CACHE_SOURCES_TABLE}"
        ).fetchall()
    }


def mark_candidate_building(conn) -> None:
    """Mark a partially loaded candidate as incomplete and non-queryable."""

    schema.ensure_analytics_meta_table(conn)
    conn.execute(
        f"""
        UPDATE {schema.ANALYTICS_META_TABLE}
        SET schema_version = ?,
            build_state = 'building',
            published_at = NULL
        WHERE singleton_id = 1
        """,
        [schema.ANALYTICS_SCHEMA_VERSION],
    )
