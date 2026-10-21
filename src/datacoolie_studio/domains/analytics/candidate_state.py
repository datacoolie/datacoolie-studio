from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

from datacoolie_studio.domains.analytics import schema
from datacoolie_studio.domains.analytics import schema_tables
from datacoolie_studio.domains.analytics import cache_state


def clear_candidate_source(candidate_path: Path, source_id: int) -> None:
    """Remove all staged rows for one source before a non-resumable replay."""

    from datacoolie_studio.domains.analytics import access as analytics_access
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    with analytics_access.analytics_maintenance_lock:
        conn = analytics_access.connect(candidate_path)
        try:
            schema_tables.ensure_tables(conn)
            conn.execute("BEGIN TRANSACTION")
            try:
                for table_name in (
                    schema.DATAFLOW_TABLE,
                    schema.JOB_TABLE,
                    schema.FILTER_VALUES_TABLE,
                ):
                    if schema.table_exists(conn, table_name):
                        if "_source_id" in schema.table_columns(conn, table_name):
                            conn.execute(
                                f"DELETE FROM {table_name} WHERE _source_id = ?",
                                [source_id],
                            )
                if schema.table_exists(conn, schema.CACHE_SOURCES_TABLE):
                    conn.execute(
                        f"DELETE FROM {schema.CACHE_SOURCES_TABLE} WHERE source_id = ?",
                        [source_id],
                    )
                cache_state.mark_candidate_building(conn)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()


def candidate_batch_is_complete(
    candidate_path: Path,
    source_id: int,
    files: list[dict[str, Any]],
    file_row_counts: dict[str, Any],
) -> bool:
    """Reconcile a persisted batch checkpoint with candidate base-table rows."""

    if not candidate_path.exists() or not files:
        return False
    from datacoolie_studio.domains.analytics import access as analytics_access

    expected_by_kind: dict[str, dict[str, int]] = {}
    revision_by_uri: dict[str, str] = {}
    for file_state in files:
        file_uri = str(file_state.get("file_uri") or "")
        file_kind = str(file_state.get("file_kind") or "")
        if not file_uri or file_kind not in {"dataflow_json", "job_json"}:
            return False
        revision_by_uri[file_uri] = str(file_state.get("revision_json") or "")
        try:
            expected_count = int(file_row_counts.get(file_uri) or 0)
        except (TypeError, ValueError):
            return False
        expected_by_kind.setdefault(file_kind, {})[file_uri] = expected_count

    with analytics_access.analytics_maintenance_lock:
        conn = analytics_access.connect(candidate_path, read_only=True)
        try:
            for file_kind, expected in expected_by_kind.items():
                table_name = (
                    schema.DATAFLOW_TABLE
                    if file_kind == "dataflow_json"
                    else schema.JOB_TABLE
                )
                if not schema.table_exists(conn, table_name):
                    return False
                placeholders = ", ".join("?" for _ in expected)
                rows = conn.execute(
                    f"""
                    SELECT
                      _file_uri,
                      COUNT(*)::BIGINT,
                      MIN(_source_size),
                      MAX(_source_size),
                      MIN(_source_mtime_ns),
                      MAX(_source_mtime_ns)
                    FROM {table_name}
                    WHERE _source_id = ? AND _file_uri IN ({placeholders})
                    GROUP BY _file_uri
                    """,
                    [source_id, *expected],
                ).fetchall()
                actual = {
                    str(file_uri): (
                        int(count or 0),
                        min_size,
                        max_size,
                        min_mtime_ns,
                        max_mtime_ns,
                    )
                    for (
                        file_uri,
                        count,
                        min_size,
                        max_size,
                        min_mtime_ns,
                        max_mtime_ns,
                    ) in rows
                }
                for file_uri, expected_count in expected.items():
                    observed = actual.get(file_uri)
                    if observed is None:
                        if expected_count != 0:
                            return False
                        observed = (0, None, None, None, None)
                    if observed[0] != expected_count:
                        return False
                    revision_json = revision_by_uri.get(file_uri, "")
                    expected_size = _revision_value(revision_json, "size")
                    expected_mtime_ns = _revision_value(revision_json, "mtime_ns")
                    if expected_size is not None and (
                        observed[1] != expected_size or observed[2] != expected_size
                    ):
                        return False
                    if expected_mtime_ns is not None and (
                        observed[3] != expected_mtime_ns
                        or observed[4] != expected_mtime_ns
                    ):
                        return False
            return True
        except duckdb.Error:
            return False
        finally:
            conn.close()


def _revision_value(revision_json: str, key: str) -> Any:
    try:
        revision = json.loads(revision_json)
    except json.JSONDecodeError:
        return None
    return revision.get(key)
