from __future__ import annotations

from datetime import datetime
from typing import Any

from datacoolie_studio.domains.analytics import schema
from datacoolie_studio.domains.analytics.serving_facts import (
    rebuild_monitoring_serving_facts,
    validate_monitoring_serving_facts,
)


def upsert_ingest_control_rows(
    conn,
    source_id: int,
    files: list[dict[str, Any]],
    checkpoints: list[dict[str, Any]],
    file_row_counts: dict[str, int],
    *,
    ingested_at: datetime,
) -> None:
    schema.ensure_ingest_control_tables(conn)
    ingested_at_value = ingested_at.isoformat()
    for item in files:
        file_uri = str(item["file_uri"])
        file_kind = str(item["file_kind"])
        conn.execute(
            f"DELETE FROM {schema.INGEST_MANIFEST_TABLE} "
            "WHERE source_id = ? AND log_kind = ? AND file_uri = ?",
            [source_id, file_kind, file_uri],
        )
        conn.execute(
            f"""
            INSERT INTO {schema.INGEST_MANIFEST_TABLE} (
              source_id, log_kind, file_uri, partition_value, partition_format,
              revision_json, row_count, ingested_at
            ) VALUES (?, ?, ?, ?::DATE, ?, ?, ?, ?::TIMESTAMPTZ)
            """,
            [
                source_id,
                file_kind,
                file_uri,
                str(item["partition_value"]),
                str(item["partition_format"]),
                str(item["revision_json"]),
                int(file_row_counts.get(file_uri, item.get("row_count") or 0)),
                ingested_at_value,
            ],
        )
    for checkpoint in checkpoints:
        file_kind = str(checkpoint["file_kind"])
        conn.execute(
            f"DELETE FROM {schema.INGEST_CHECKPOINT_TABLE} "
            "WHERE source_id = ? AND log_kind = ?",
            [source_id, file_kind],
        )
        conn.execute(
            f"""
            INSERT INTO {schema.INGEST_CHECKPOINT_TABLE} (
              source_id, log_kind, partition_format, partition_value,
              boundary_last_modified, updated_at
            ) VALUES (?, ?, ?, ?::DATE, ?::TIMESTAMPTZ, ?::TIMESTAMPTZ)
            """,
            [
                source_id,
                file_kind,
                str(checkpoint["partition_format"]),
                str(checkpoint["partition_value"]),
                str(checkpoint["boundary_last_modified"]),
                ingested_at_value,
            ],
        )


def publish_generation(
    conn,
    *,
    dataflow_column_types: dict[str, str],
    job_column_types: dict[str, str],
    published_at: datetime,
) -> None:
    rebuild_monitoring_serving_facts(
        conn,
        dataflow_table=schema.DATAFLOW_TABLE,
        job_table=schema.JOB_TABLE,
        dataflow_column_types=schema.cache_table_column_types(dataflow_column_types),
        job_column_types=schema.cache_table_column_types(job_column_types),
    )
    validate_monitoring_serving_facts(
        conn,
        dataflow_table=schema.DATAFLOW_TABLE,
        job_table=schema.JOB_TABLE,
    )
    schema.ensure_analytics_meta_table(conn)
    conn.execute(
        f"""
        UPDATE {schema.ANALYTICS_META_TABLE}
        SET schema_version = ?,
            generation = generation + 1,
            build_state = 'ready',
            published_at = ?::TIMESTAMPTZ
        WHERE singleton_id = 1
        """,
        [schema.ANALYTICS_SCHEMA_VERSION, published_at.isoformat()],
    )


def analytics_meta(conn) -> dict[str, Any] | None:
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


def cache_source_generations(conn) -> dict[int, int]:
    if not schema.table_exists(conn, schema.CACHE_SOURCES_TABLE):
        return {}
    return {
        int(source_id): int(generation or 0)
        for source_id, generation in conn.execute(
            f"SELECT source_id, generation FROM {schema.CACHE_SOURCES_TABLE}"
        ).fetchall()
    }
