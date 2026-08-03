from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from datacoolie_studio.core.time import parse_utc_datetime
from datacoolie_studio.db.models import utc_now
from datacoolie_studio.domains.analytics import schema
from datacoolie_studio.domains.analytics.errors import (
    AnalyticsFileChangedDuringPublishError,
    AnalyticsSchemaIncompatibleError,
)
from datacoolie_studio.domains.analytics.serving_facts import (
    rebuild_monitoring_serving_facts,
    validate_monitoring_serving_facts,
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


def publish_rows(
    source_id: int,
    dataflow_files: list[tuple[str, str, str] | tuple[str, str, str, str]],
    job_rows: list[tuple[str, str, str, dict[str, Any]]],
    removed_files: list[str],
    changed_files: list[str],
    *,
    database_path: Path,
    source_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from datacoolie_studio.domains.analytics import access as analytics_access

    with analytics_access.analytics_maintenance_lock:
        return _publish_rows_locked(
            source_id,
            dataflow_files,
            job_rows,
            removed_files,
            changed_files,
            database_path=database_path,
            source_files=source_files,
        )


def _publish_rows_locked(
    source_id: int,
    dataflow_files: list[tuple[str, str, str] | tuple[str, str, str, str]],
    job_rows: list[tuple[str, str, str, dict[str, Any]]],
    removed_files: list[str],
    changed_files: list[str],
    *,
    database_path: Path,
    source_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from datacoolie_studio.domains.analytics import access as analytics_access

    path = database_path
    blocked_status = _blocking_upgrade_status(path)
    if blocked_status is not None:
        return {
            "parsed_dataflow_records": 0,
            "file_row_counts": {},
            "errors": [
                {
                    "uri": str(path),
                    "message": "A complete analytics cache upgrade is in progress",
                    "code": "analytics_upgrade_in_progress",
                }
            ],
            "published": False,
        }
    if analytics_access.schema_rebuild_required(path):
        candidate_path = analytics_access.candidate_path(path)
        analytics_access.discard_candidate(candidate_path)
        result = _write_duckdb_rows(
            candidate_path,
            source_id,
            dataflow_files,
            job_rows,
            removed_files,
            changed_files,
            source_files=source_files,
        )
        if result["published"]:
            try:
                _validate_analytics_candidate(candidate_path, [source_id])
                analytics_access.swap_candidate(candidate_path, path)
            except Exception as exc:
                result["errors"].append(
                    {
                        "uri": str(candidate_path),
                        "message": str(exc),
                        "code": getattr(exc, "code", "publish_failed"),
                    }
                )
                result["published"] = False
        return result
    return _write_duckdb_rows(
        path,
        source_id,
        dataflow_files,
        job_rows,
        removed_files,
        changed_files,
        source_files=source_files,
    )


def _blocking_upgrade_status(path: Path) -> dict[str, Any] | None:
    from datacoolie_studio.domains.analytics_upgrade.service import (
        current_upgrade_status,
    )

    status = current_upgrade_status()
    if status is None or status.get("state") not in {
        "pending",
        "building",
        "validating",
        "publishing",
        "failed",
    }:
        return None
    candidate = status.get("candidate_path")
    return None if candidate and Path(str(candidate)) == path else status


def _write_duckdb_rows(
    path: Path,
    source_id: int,
    dataflow_files: list[tuple[str, str, str] | tuple[str, str, str, str]],
    job_rows: list[tuple[str, str, str, dict[str, Any]]],
    removed_files: list[str],
    changed_files: list[str],
    *,
    source_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from datacoolie_studio.domains.analytics import access as analytics_access

    path.parent.mkdir(parents=True, exist_ok=True)
    parsed_dataflow_records = 0
    file_row_counts: dict[str, int] = {}
    errors: list[dict[str, str]] = []
    conn = analytics_access.connect(path)
    try:
        ensure_tables(conn)
        conn.execute("BEGIN TRANSACTION")
        try:
            _preflight_dataflow_schemas(
                conn, [_dataflow_read_uri(item) for item in dataflow_files]
            )
            stale_files = [*removed_files, *changed_files]
            for file_uri in stale_files:
                if schema.table_exists(conn, schema.DATAFLOW_TABLE):
                    conn.execute(f"DELETE FROM {schema.DATAFLOW_TABLE} WHERE _source_id = ? AND _file_uri = ?", [source_id, file_uri])
                if schema.table_exists(conn, schema.JOB_TABLE):
                    conn.execute(f"DELETE FROM {schema.JOB_TABLE} WHERE _source_id = ? AND _file_uri = ?", [source_id, file_uri])
            for item in dataflow_files:
                file_uri, file_kind, revision_json = item[:3]
                read_uri = item[3] if len(item) == 4 else file_uri
                row_count = insert_dataflow_file(
                    conn,
                    source_id,
                    file_uri,
                    file_kind,
                    revision_json,
                    read_uri=read_uri,
                )
                parsed_dataflow_records += row_count
                file_row_counts[file_uri] = row_count
            if job_rows:
                insert_typed_rows(conn, schema.JOB_TABLE, source_id, job_rows, schema.JOB_COLUMN_TYPES)
            _assert_ingest_files_stable(source_files or [])
            refresh_filter_values(conn, source_id)
            mark_cache_source(conn, source_id, refreshed_at=utc_now())
            publish_generation(
                conn,
                dataflow_column_types=schema.DATAFLOW_COLUMN_TYPES,
                job_column_types=schema.JOB_COLUMN_TYPES,
                published_at=utc_now(),
            )
            conn.execute("COMMIT")
        except Exception as exc:
            conn.execute("ROLLBACK")
            errors.append(
                {
                    "uri": str(path),
                    "message": str(exc),
                    "code": getattr(exc, "code", "publish_failed"),
                }
            )
            parsed_dataflow_records = 0
            file_row_counts.clear()
    finally:
        conn.close()
    return {
        "parsed_dataflow_records": parsed_dataflow_records,
        "file_row_counts": file_row_counts,
        "errors": errors,
        "published": not errors,
    }


def _file_revision_json(file_uri: str) -> str:
    stat = Path(file_uri).stat()
    return json.dumps({"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}, sort_keys=True)


def _assert_ingest_files_stable(files: list[dict[str, Any]]) -> None:
    """Abort the transaction when bytes changed after candidate discovery."""
    for file_state in files:
        if file_state.get("staged_path"):
            continue
        file_uri = str(file_state["file_uri"])
        expected = str(file_state["revision_json"])
        try:
            actual = _file_revision_json(file_uri)
        except OSError as exc:
            raise AnalyticsFileChangedDuringPublishError(
                f"Log file became unavailable during sync: {file_uri}"
            ) from exc
        if not _revision_equivalent(expected, actual):
            raise AnalyticsFileChangedDuringPublishError(
                f"Log file changed during sync and was not published: {file_uri}"
            )


def _revision_equivalent(left_json: str | None, right_json: str | None) -> bool:
    if left_json is None or right_json is None:
        return left_json == right_json
    try:
        left = json.loads(left_json)
        right = json.loads(right_json)
    except (TypeError, json.JSONDecodeError):
        return left_json == right_json
    return left.get("size") == right.get("size") and left.get("mtime_ns") == right.get("mtime_ns")


def ensure_tables(conn) -> None:
    recreated = [
        _ensure_dataflow_cache_table(conn),
        ensure_typed_table(conn, schema.JOB_TABLE, schema.JOB_COLUMN_TYPES),
    ]
    _drop_empty_generated_job_columns(conn)
    if any(recreated) and schema.table_exists(conn, schema.FILTER_VALUES_TABLE):
        conn.execute(f"DROP TABLE {schema.FILTER_VALUES_TABLE}")
    schema.ensure_filter_values_table(conn)
    schema.ensure_cache_sources_table(conn)
    schema.ensure_analytics_meta_table(conn)
    _migrate_legacy_cache(conn)


def _validate_analytics_candidate(
    candidate_path: Path,
    source_ids: list[int],
) -> None:
    from datacoolie_studio.domains.analytics import access as analytics_access

    if not analytics_access.cache_is_ready(candidate_path):
        raise RuntimeError("Analytics rebuild candidate did not create the current typed schema")
    conn = analytics_access.connect(candidate_path, read_only=True)
    try:
        analytics_access.validate_source_complete_candidate(conn, source_ids)
    finally:
        conn.close()


def validate_analytics_candidate(
    candidate_path: Path,
    source_ids: list[int],
) -> None:
    _validate_analytics_candidate(candidate_path, source_ids)


def refresh_filter_values(conn, source_id: int) -> None:
    schema.ensure_filter_values_table(conn)
    conn.execute(f"DELETE FROM {schema.FILTER_VALUES_TABLE} WHERE _source_id = ?", [source_id])
    updated_at = utc_now().isoformat()
    for field, (table_name, column_name) in schema.FILTER_VALUE_SOURCES.items():
        if not schema.table_exists(conn, table_name):
            continue
        if column_name not in schema.table_columns(conn, table_name):
            continue
        conn.execute(
            f"""
            INSERT INTO {schema.FILTER_VALUES_TABLE}
            SELECT
              ?::BIGINT AS _source_id,
              ?::VARCHAR AS field,
              TRIM(CAST({_quote_identifier(column_name)} AS VARCHAR)) AS value,
              COUNT(*)::BIGINT AS record_count,
              ?::TIMESTAMPTZ AS _updated_at
            FROM {table_name}
            WHERE _source_id = ?
              AND {_quote_identifier(column_name)} IS NOT NULL
              AND TRIM(CAST({_quote_identifier(column_name)} AS VARCHAR)) <> ''
            GROUP BY TRIM(CAST({_quote_identifier(column_name)} AS VARCHAR))
            """,
            [source_id, field, updated_at, source_id],
        )
    if schema.table_exists(conn, schema.DATAFLOW_TABLE):
        dataflow_columns = set(schema.table_columns(conn, schema.DATAFLOW_TABLE))
        connection_selects = []
        identity_sql = ", ".join(
            [
                "_source_id",
                "_file_uri",
                "dataflow_run_id" if "dataflow_run_id" in dataflow_columns else "NULL::VARCHAR AS dataflow_run_id",
                "job_id" if "job_id" in dataflow_columns else "NULL::VARCHAR AS job_id",
            ]
        )
        for column_name in ("source_name", "destination_name"):
            if column_name not in dataflow_columns:
                continue
            quoted_column = _quote_identifier(column_name)
            connection_selects.append(
                f"""
                SELECT {identity_sql}, TRIM(CAST({quoted_column} AS VARCHAR)) AS connection_name
                FROM {schema.DATAFLOW_TABLE}
                WHERE _source_id = ?
                  AND {quoted_column} IS NOT NULL
                  AND TRIM(CAST({quoted_column} AS VARCHAR)) <> ''
                """
            )
        if not connection_selects:
            return
        union_sql = "\nUNION ALL\n".join(connection_selects)
        conn.execute(
            f"""
            INSERT INTO {schema.FILTER_VALUES_TABLE}
            SELECT
              ?::BIGINT AS _source_id,
              'connection'::VARCHAR AS field,
              connection_name AS value,
              COUNT(*)::BIGINT AS record_count,
              ?::TIMESTAMPTZ AS _updated_at
            FROM (
              SELECT DISTINCT _source_id, _file_uri, dataflow_run_id, job_id, connection_name
              FROM (
                {union_sql}
              ) raw_connection_names
            ) connection_names
            GROUP BY connection_name
            """,
            [source_id, updated_at, *([source_id] * len(connection_selects))],
        )


def _migrate_legacy_cache(conn) -> None:
    migrated_source_ids = set()
    migrated_source_ids.update(
        _migrate_legacy_table(
            conn,
            legacy_table=schema.LEGACY_DATAFLOW_TABLE,
            target_table=schema.DATAFLOW_TABLE,
            column_types=schema.DATAFLOW_COLUMN_TYPES,
            file_kind="legacy_dataflow_json",
        )
    )
    migrated_source_ids.update(
        _migrate_legacy_table(
            conn,
            legacy_table=schema.LEGACY_JOB_TABLE,
            target_table=schema.JOB_TABLE,
            column_types=schema.JOB_COLUMN_TYPES,
            file_kind="legacy_job_json",
        )
    )
    for source_id in sorted(migrated_source_ids):
        refresh_filter_values(conn, source_id)


def _migrate_legacy_table(
    conn,
    legacy_table: str,
    target_table: str,
    column_types: dict[str, str],
    file_kind: str,
) -> set[int]:
    if not schema.table_exists(conn, legacy_table):
        return set()
    legacy_columns = set(schema.table_columns(conn, legacy_table))
    if not {"source_id", "file_uri", "row_json"} <= legacy_columns:
        return set()

    if not schema.table_exists(conn, target_table):
        ensure_typed_table(conn, target_table, column_types)

    migrated_source_ids = set()
    source_ids = [
        int(row[0])
        for row in conn.execute(
            f"SELECT DISTINCT source_id FROM {legacy_table} WHERE source_id IS NOT NULL ORDER BY source_id"
        ).fetchall()
    ]
    for source_id in source_ids:
        legacy_count = conn.execute(
            f"SELECT count(*) FROM {legacy_table} WHERE source_id = ?",
            [source_id],
        ).fetchone()[0]
        target_count = conn.execute(f"SELECT count(*) FROM {target_table} WHERE _source_id = ?", [source_id]).fetchone()[0]
        if target_count == legacy_count:
            migrated_source_ids.add(source_id)
            continue
        conn.execute(f"DELETE FROM {target_table} WHERE _source_id = ?", [source_id])
        legacy_rows = conn.execute(
            f"SELECT file_uri, row_json FROM {legacy_table} WHERE source_id = ?",
            [source_id],
        ).fetchall()
        typed_rows = []
        for file_uri, row_json in legacy_rows:
            try:
                row = json.loads(row_json) if row_json else {}
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(row, dict):
                continue
            typed_rows.append((str(file_uri or ""), file_kind, "{}", row))
        if typed_rows:
            if not schema.table_exists(conn, target_table):
                ensure_typed_table(conn, target_table, column_types)
            insert_typed_rows(conn, target_table, source_id, typed_rows, column_types)
            migrated_source_ids.add(source_id)
    conn.execute(f"DROP TABLE {legacy_table}")
    return migrated_source_ids


def insert_typed_rows(
    conn,
    table_name: str,
    source_id: int,
    rows: list[tuple[str, str, str, dict[str, Any]]],
    column_types: dict[str, str],
) -> None:
    _ensure_source_columns(conn, table_name, rows, column_types)
    columns = schema.table_columns(conn, table_name)
    insert_columns = [
        column
        for column in columns
        if (
            column in schema.STUDIO_CACHE_COLUMNS
            or column in schema.GENERATED_CACHE_COLUMNS
            or any(column in row for _, _, _, row in rows)
        )
    ]
    placeholders = ", ".join("?" for _ in insert_columns)
    column_sql = ", ".join(_quote_identifier(column) for column in insert_columns)
    values = [
        [
            _cache_value(column, source_id, file_uri, file_kind, revision_json, row, column_types.get(column))
            for column in insert_columns
        ]
        for file_uri, file_kind, revision_json, row in rows
    ]
    conn.executemany(f"INSERT INTO {table_name} ({column_sql}) VALUES ({placeholders})", values)


def insert_dataflow_file(
    conn,
    source_id: int,
    file_uri: str,
    file_kind: str,
    revision_json: str,
    *,
    read_uri: str | None = None,
) -> int:
    source_read_uri = read_uri or file_uri
    _ensure_dataflow_table_for_parquet(conn, source_read_uri)
    escaped = source_read_uri.replace("'", "''")
    source_projection = _dataflow_parquet_source_projection(conn, source_read_uri)
    row_count = conn.execute(f"SELECT count(*) FROM read_parquet('{escaped}', union_by_name=true)").fetchone()[0]
    conn.execute(
        f"""
        INSERT INTO {schema.DATAFLOW_TABLE} BY NAME
        SELECT
          {int(source_id)}::BIGINT AS _source_id,
          {_sql_string(file_uri)} AS _file_uri,
          {_sql_string(file_kind)} AS _file_kind,
          {_sql_date(_file_date(file_uri, {}))} AS _file_date,
          {_sql_number(_revision_value(revision_json, "size"))}::BIGINT AS _source_size,
          {_sql_number(_revision_value(revision_json, "mtime_ns"))}::BIGINT AS _source_mtime_ns,
          {_sql_string(utc_now().isoformat())}::TIMESTAMPTZ AS _ingested_at,
          {source_projection}
        FROM read_parquet('{escaped}', union_by_name=true)
        """
    )
    return int(row_count)


def _dataflow_read_uri(
    item: tuple[str, str, str] | tuple[str, str, str, str]
) -> str:
    return item[3] if len(item) == 4 else item[0]


def ensure_typed_table(conn, table_name: str, column_types: dict[str, str]) -> bool:
    columns = schema.table_columns(conn, table_name)
    if columns and ("_source_id" not in columns or schema.has_legacy_raw_json_column(columns) or schema.has_incompatible_column_types(conn, table_name, column_types)):
        conn.execute(f"DROP TABLE {table_name}")
        columns = []
    if not columns:
        definitions = [
            f"{_quote_identifier(column)} {data_type}"
            for column, data_type in schema.cache_table_column_types(column_types).items()
        ]
        conn.execute(f"CREATE TABLE {table_name} ({', '.join(definitions)})")
        return True
    _ensure_columns(conn, table_name, schema.cache_table_column_types(column_types), set(columns))
    _ensure_column_order(conn, table_name, column_types)
    return False


def _ensure_dataflow_cache_table(conn) -> bool:
    columns = schema.table_columns(conn, schema.DATAFLOW_TABLE)
    if columns and ("_source_id" not in columns or schema.has_legacy_raw_json_column(columns)):
        conn.execute(f"DROP TABLE {schema.DATAFLOW_TABLE}")
        columns = []
    if not columns:
        return ensure_typed_table(conn, schema.DATAFLOW_TABLE, {})
    existing = set(columns)
    _ensure_columns(conn, schema.DATAFLOW_TABLE, schema.STUDIO_CACHE_COLUMNS, existing)
    _ensure_column_order(conn, schema.DATAFLOW_TABLE, schema.actual_source_column_types(conn, schema.DATAFLOW_TABLE))
    return False


def _ensure_source_columns(
    conn,
    table_name: str,
    rows: list[tuple[str, str, str, dict[str, Any]]],
    column_types: dict[str, str],
) -> None:
    existing = set(schema.table_columns(conn, table_name))
    actual_types = schema.table_column_types(conn, table_name)
    inferred: dict[str, set[str]] = {}
    for _, _, _, row in rows:
        for column, value in row.items():
            if value is None or column in schema.STUDIO_CACHE_COLUMNS:
                continue
            expected = column_types.get(column) or _infer_duckdb_type(value)
            if column in existing:
                actual = actual_types.get(column)
                if actual and not schema.duckdb_type_matches(actual, expected):
                    raise AnalyticsSchemaIncompatibleError(
                        f"Column {column!r} changed datatype from {actual} to {expected}"
                    )
                continue
            inferred.setdefault(column, set()).add(expected)
    conflicts = {column: types for column, types in inferred.items() if len(types) > 1}
    if conflicts:
        column, types = sorted(conflicts.items())[0]
        raise AnalyticsSchemaIncompatibleError(
            f"New column {column!r} has conflicting datatypes: {', '.join(sorted(types))}"
        )
    discovered = {column: next(iter(types)) for column, types in inferred.items()}
    if discovered:
        _ensure_columns(conn, table_name, discovered, existing)
        _ensure_column_order(conn, table_name, {**column_types, **discovered})


def _ensure_dataflow_table_for_parquet(conn, file_uri: str) -> None:
    parquet_column_types = _dataflow_parquet_target_types(conn, file_uri)
    if not schema.table_exists(conn, schema.DATAFLOW_TABLE):
        definitions = [
            f"{_quote_identifier(column)} {data_type}"
            for column, data_type in schema.cache_table_column_types(parquet_column_types).items()
        ]
        conn.execute(f"CREATE TABLE {schema.DATAFLOW_TABLE} ({', '.join(definitions)})")
        return
    existing = set(schema.table_columns(conn, schema.DATAFLOW_TABLE))
    actual_types = schema.table_column_types(conn, schema.DATAFLOW_TABLE)
    for column, source_type in parquet_column_types.items():
        actual_type = actual_types.get(column)
        if actual_type and not _source_type_fits_target(actual_type, source_type):
            raise AnalyticsSchemaIncompatibleError(
                f"Column {column!r} changed datatype from {actual_type} to {source_type} in {file_uri}"
            )
    discovered = {
        column: data_type
        for column, data_type in parquet_column_types.items()
        if column not in existing
    }
    if discovered:
        _ensure_columns(conn, schema.DATAFLOW_TABLE, discovered, existing)
        _ensure_column_order(conn, schema.DATAFLOW_TABLE, parquet_column_types)


def _preflight_dataflow_schemas(conn, file_uris: list[str]) -> None:
    if not file_uris:
        return
    candidate_types: dict[str, str] = {}
    for file_uri in file_uris:
        for column, source_type in _describe_parquet_columns(conn, file_uri):
            if column in schema.STUDIO_CACHE_COLUMNS or column == "__event_time":
                continue
            previous = candidate_types.get(column)
            candidate_types[column] = source_type if previous is None else _common_source_type(column, previous, source_type)
    candidate_types["__event_time"] = "TIMESTAMPTZ"
    existing = set(schema.table_columns(conn, schema.DATAFLOW_TABLE))
    actual_types = schema.table_column_types(conn, schema.DATAFLOW_TABLE)
    for column, source_type in candidate_types.items():
        actual_type = actual_types.get(column)
        if actual_type and not _source_type_fits_target(actual_type, source_type):
            raise AnalyticsSchemaIncompatibleError(
                f"Column {column!r} changed datatype from {actual_type} to {source_type}"
            )
    discovered = {column: data_type for column, data_type in candidate_types.items() if column not in existing}
    if discovered:
        _ensure_columns(conn, schema.DATAFLOW_TABLE, discovered, existing)
    _ensure_column_order(conn, schema.DATAFLOW_TABLE, candidate_types)


def _common_source_type(column: str, left: str, right: str) -> str:
    left_type = left.upper()
    right_type = right.upper()
    if left_type == right_type:
        return left
    if {left_type, right_type} <= {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "FLOAT", "DOUBLE"}:
        return "DOUBLE" if "DOUBLE" in {left_type, right_type} or "FLOAT" in {left_type, right_type} else "BIGINT"
    if left_type.startswith("TIMESTAMP") and right_type.startswith("TIMESTAMP"):
        return "TIMESTAMPTZ"
    raise AnalyticsSchemaIncompatibleError(
        f"Column {column!r} has incompatible source datatypes {left} and {right}"
    )


def _source_type_fits_target(target_type: str, source_type: str) -> bool:
    target = target_type.upper()
    source = source_type.upper()
    if schema.duckdb_type_matches(target, source):
        return True
    if target == "DOUBLE" and source in {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "FLOAT"}:
        return True
    return target in {"TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE"} and source.startswith("TIMESTAMP")


def _describe_parquet_columns(conn, file_uri: str) -> list[tuple[str, str]]:
    escaped = file_uri.replace("'", "''")
    described = conn.execute(f"DESCRIBE SELECT * FROM read_parquet('{escaped}', union_by_name=true)").fetchall()
    return [(str(row[0]), str(row[1])) for row in described]


def _dataflow_parquet_target_types(conn, file_uri: str) -> dict[str, str]:
    target_types = {
        name: data_type
        for name, data_type in _describe_parquet_columns(conn, file_uri)
        if name not in schema.STUDIO_CACHE_COLUMNS and name != "__event_time"
    }
    target_types["__event_time"] = "TIMESTAMPTZ"
    return target_types


def _dataflow_parquet_source_projection(conn, file_uri: str) -> str:
    source_columns = {
        name for name, _data_type in _describe_parquet_columns(conn, file_uri)
    }
    passthrough = (
        '* EXCLUDE ("__event_time")'
        if "__event_time" in source_columns
        else "*"
    )

    def timestamp_value(column: str) -> str:
        return (
            f"TRY_CAST({_quote_identifier(column)} AS TIMESTAMPTZ)"
            if column in source_columns
            else "NULL::TIMESTAMPTZ"
        )

    event_time = ", ".join(
        timestamp_value(column)
        for column in ("__event_time", "end_time", "start_time")
    )
    return f"{passthrough}, COALESCE({event_time}) AS __event_time"


def _ensure_columns(conn, table_name: str, column_types: dict[str, str], existing: set[str]) -> None:
    for column, data_type in column_types.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {_quote_identifier(column)} {data_type}")
            existing.add(column)


def _ensure_column_order(conn, table_name: str, source_column_types: dict[str, str]) -> None:
    actual_columns = schema.table_columns(conn, table_name)
    if not actual_columns:
        return
    expected_columns = schema.expected_column_order(actual_columns, source_column_types)
    if actual_columns == expected_columns:
        return
    actual_types = schema.table_column_types(conn, table_name)
    expected_types = schema.cache_table_column_types(source_column_types)
    temp_table = f"{table_name}__column_order"
    conn.execute(f"DROP TABLE IF EXISTS {temp_table}")
    definitions = [
        f"{_quote_identifier(column)} {actual_types.get(column) or expected_types.get(column) or 'VARCHAR'}"
        for column in expected_columns
    ]
    conn.execute(f"CREATE TABLE {temp_table} ({', '.join(definitions)})")
    common_columns = [column for column in expected_columns if column in actual_columns]
    if common_columns:
        column_sql = ", ".join(_quote_identifier(column) for column in common_columns)
        conn.execute(f"INSERT INTO {temp_table} ({column_sql}) SELECT {column_sql} FROM {table_name}")
    conn.execute(f"DROP TABLE {table_name}")
    conn.execute(f"ALTER TABLE {temp_table} RENAME TO {table_name}")


def _drop_empty_generated_job_columns(conn) -> None:
    """Remove columns that older studio cache versions generated for jobs."""
    if not schema.table_exists(conn, schema.JOB_TABLE):
        return
    columns = set(schema.table_columns(conn, schema.JOB_TABLE))
    for column in ("operation_type",):
        if column not in columns:
            continue
        quoted_column = _quote_identifier(column)
        try:
            non_null_count = conn.execute(
                f"SELECT count(*) FROM {schema.JOB_TABLE} WHERE {quoted_column} IS NOT NULL"
            ).fetchone()[0]
            if int(non_null_count or 0) == 0:
                conn.execute(f"ALTER TABLE {schema.JOB_TABLE} DROP COLUMN {quoted_column}")
        except duckdb.Error:
            continue


def table_source_row_count(conn, table_name: str, source_id: int) -> int:
    if not schema.table_exists(conn, table_name) or "_source_id" not in schema.table_columns(conn, table_name):
        return 0
    return int(
        conn.execute(
            f"SELECT count(*) FROM {table_name} WHERE _source_id = ?",
            [source_id],
        ).fetchone()[0]
        or 0
    )


def _table_source_ids(conn, table_name: str) -> list[int]:
    if not schema.table_exists(conn, table_name):
        return []
    if "_source_id" not in schema.table_columns(conn, table_name):
        return []
    rows = conn.execute(
        f"SELECT DISTINCT _source_id FROM {table_name} WHERE _source_id IS NOT NULL ORDER BY _source_id"
    ).fetchall()
    return [int(row[0]) for row in rows]


def _table_has_source_rows(conn, table_name: str, source_ids: list[int]) -> bool:
    if not source_ids or not schema.table_exists(conn, table_name):
        return False
    if "_source_id" not in schema.table_columns(conn, table_name):
        return False
    placeholders = ", ".join("?" for _ in source_ids)
    count = conn.execute(
        f"SELECT count(*) FROM {table_name} WHERE _source_id IN ({placeholders})",
        source_ids,
    ).fetchone()[0]
    return int(count or 0) > 0


def delete_rows_by_source_ids(
    conn,
    table_name: str,
    source_ids: list[int],
    *,
    source_column: str = "_source_id",
) -> int:
    if not source_ids or not schema.table_exists(conn, table_name):
        return 0
    if source_column not in schema.table_columns(conn, table_name):
        return 0
    quoted_source_column = _quote_identifier(source_column)
    placeholders = ", ".join("?" for _ in source_ids)
    row_count = int(
        conn.execute(
            f"SELECT count(*) FROM {table_name} WHERE {quoted_source_column} IN ({placeholders})",
            source_ids,
        ).fetchone()[0]
    )
    if row_count:
        conn.execute(
            f"DELETE FROM {table_name} WHERE {quoted_source_column} IN ({placeholders})",
            source_ids,
        )
    return row_count


def _cache_value(
    column: str,
    source_id: int,
    file_uri: str,
    file_kind: str,
    revision_json: str,
    row: dict[str, Any],
    data_type: str | None,
) -> Any:
    if column == "_source_id":
        return source_id
    if column == "_file_uri":
        return file_uri
    if column == "_file_kind":
        return file_kind
    if column == "_file_date":
        return _file_date(file_uri, row)
    if column == "_source_size":
        return _revision_value(revision_json, "size")
    if column == "_source_mtime_ns":
        return _revision_value(revision_json, "mtime_ns")
    if column == "_ingested_at":
        return utc_now().isoformat()
    if column == "__event_time":
        return (
            parse_utc_datetime(row.get(column))
            or parse_utc_datetime(row.get("end_time"))
            or parse_utc_datetime(row.get("start_time"))
        )
    if column == "__run_date":
        return row.get(column) or _file_date(file_uri, row)
    return _typed_value(row.get(column), data_type)


def _typed_value(value: Any, data_type: str | None) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    if data_type == "VARCHAR":
        return str(value)
    if data_type == "DATE" and isinstance(value, str):
        return value[:10]
    return value


def _revision_value(revision_json: str, key: str) -> Any:
    try:
        revision = json.loads(revision_json)
    except json.JSONDecodeError:
        return None
    return revision.get(key)


def _file_date(file_uri: str, row: dict[str, Any]) -> str | None:
    if row.get("__run_date"):
        return str(row["__run_date"])[:10]
    for key in ("end_time", "start_time"):
        if row.get(key):
            return str(row[key])[:10]
    match = re.search(r"(20\d{2}[-_/]\d{2}[-_/]\d{2})", file_uri)
    return match.group(1).replace("_", "-").replace("/", "-") if match else None


def _infer_duckdb_type(value: Any) -> str:
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int) and not isinstance(value, bool):
        return "BIGINT"
    if isinstance(value, float):
        return "DOUBLE"
    if isinstance(value, (datetime, date)):
        return "TIMESTAMPTZ" if isinstance(value, datetime) else "DATE"
    return "VARCHAR"


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _sql_string(value: Any) -> str:
    if value is None:
        return "NULL::VARCHAR"
    return "'" + str(value).replace("'", "''") + "'"


def _sql_date(value: str | None) -> str:
    if not value:
        return "NULL::DATE"
    return f"DATE {_sql_string(value[:10])}"


def _sql_number(value: Any) -> str:
    return "NULL" if value is None else str(int(value))
