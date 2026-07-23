from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

import duckdb
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from datacoolie_studio.core.config import analytics_database_path
from datacoolie_studio.core.time import parse_utc_datetime
from datacoolie_studio.db.models import EnvironmentSource, LogFileManifest, utc_now
from datacoolie_studio.domains.analytics import access as analytics_access
from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.analytics import store as analytics_store
from datacoolie_studio.domains.analytics.connections import analytics_connections
from datacoolie_studio.domains.logs.discovery import (
    DiscoveredLogFile,
    LogStreamCheckpoint,
    LogSyncMode,
    LogSyncSpec,
    deduplicate_candidates,
    discover_partition_files,
    discover_partitions,
    plan_incremental_candidates,
    plan_incremental_partitions,
    plan_lookback_candidates,
    plan_lookback_partitions,
)
from datacoolie_studio.domains.logs.partition import ParsedPartition, PartitionGranularity
from datacoolie_studio.domains.logs.reader import (
    discover_system_jsonl_files,
    parse_system_log_file_metadata,
    read_system_log_file,
)
from datacoolie_studio.domains.logs.source_config import resolve_log_source_paths
from datacoolie_studio.domains.sources import service as source_validation
from datacoolie_studio.domains.storage.uri import StorageProviderNotEnabled, require_local_path
from datacoolie_studio.domains.storage.adapters import FileRevision, LocalStorageAdapter
from datacoolie_studio.domains.sync import service as sync
from datacoolie_studio.domains.environment_caches import invalidate_environment_derived_caches


# Cache of "log source has files not yet synced" results, keyed by source id, so the
# filesystem scan runs at most once per TTL (the source-check interval) instead of on
# every freshness/context read. Invalidated on sync and cache purge.
_pending_changes_lock = Lock()
_pending_changes_cache: dict[int, tuple[float, bool]] = {}


class LogSchemaIncompatibleError(RuntimeError):
    code = "schema_incompatible"


class LogFileChangedDuringSyncError(RuntimeError):
    code = "file_changed_during_sync"

FILTER_VALUE_SOURCES = {
    "operation_type": (analytics_schema.DATAFLOW_TABLE, "operation_type"),
    "status": (analytics_schema.DATAFLOW_TABLE, "status"),
    "stage": (analytics_schema.DATAFLOW_TABLE, "stage"),
    "engine_name": (analytics_schema.JOB_TABLE, "engine_name"),
    "metadata_provider_name": (analytics_schema.JOB_TABLE, "metadata_provider_name"),
    "platform_name": (analytics_schema.JOB_TABLE, "platform_name"),
    "source_connection_type": (analytics_schema.DATAFLOW_TABLE, "source_connection_type"),
    "source_format": (analytics_schema.DATAFLOW_TABLE, "source_format"),
    "source_table": (analytics_schema.DATAFLOW_TABLE, "source_table"),
    "destination_connection_type": (analytics_schema.DATAFLOW_TABLE, "destination_connection_type"),
    "destination_format": (analytics_schema.DATAFLOW_TABLE, "destination_format"),
    "destination_table": (analytics_schema.DATAFLOW_TABLE, "destination_table"),
    "destination_load_type": (analytics_schema.DATAFLOW_TABLE, "destination_load_type"),
    "destination_operation_type": (analytics_schema.DATAFLOW_TABLE, "destination_operation_type"),
}

def log_source_revision(source: EnvironmentSource) -> dict[str, Any]:
    """Return a shallow source revision without walking the log tree."""
    try:
        path = require_local_path(source.uri)
    except StorageProviderNotEnabled as exc:
        return {
            "provider": exc.provider,
            "uri": source.uri,
            "path": source.uri,
            "exists": False,
            "source_kind": source.source_kind,
            "object_type": "provider_not_enabled",
        }
    exists = path.exists()
    state = path.stat() if exists else None
    return {
        "provider": "local",
        "uri": source.uri,
        "path": str(path),
        "exists": exists,
        "source_kind": source.source_kind,
        "object_type": "directory" if exists and path.is_dir() else "file" if exists else "missing",
        "file_count": None,
        "total_size": state.st_size if state and path.is_file() else None,
        "max_mtime_ns": state.st_mtime_ns if state else None,
    }


def cached_source_stats(source_id: int) -> dict[str, int]:
    """Return the Studio-owned analytics rows associated with one log source."""
    stats = {
        "dataflow_row_count": 0,
        "job_row_count": 0,
        "filter_value_count": 0,
    }
    path = analytics_database_path()
    if not path.exists():
        return stats
    _ensure_duckdb_cache_ready(path)
    conn = analytics_access.connect(path, read_only=True)
    try:
        stats["dataflow_row_count"] = _table_source_row_count(conn, analytics_schema.DATAFLOW_TABLE, source_id)
        stats["job_row_count"] = _table_source_row_count(conn, analytics_schema.JOB_TABLE, source_id)
        stats["filter_value_count"] = _table_source_row_count(conn, analytics_schema.FILTER_VALUES_TABLE, source_id)
    finally:
        conn.close()
    return stats


def purge_cached_source_ids(source_ids: list[int]) -> dict[str, int]:
    unique_ids = sorted({int(source_id) for source_id in source_ids if int(source_id) > 0})
    if not unique_ids:
        return {"dataflow_rows_deleted": 0, "job_rows_deleted": 0, "filter_values_deleted": 0}
    for source_id in unique_ids:
        _invalidate_log_pending_changes(source_id)
    path = analytics_database_path()
    if not path.exists():
        return {"dataflow_rows_deleted": 0, "job_rows_deleted": 0, "filter_values_deleted": 0}
    _ensure_duckdb_cache_ready(path)
    conn = analytics_access.connect(path)
    try:
        dataflow_rows = _delete_rows_by_source_ids(conn, analytics_schema.DATAFLOW_TABLE, unique_ids)
        job_rows = _delete_rows_by_source_ids(conn, analytics_schema.JOB_TABLE, unique_ids)
        filter_rows = _delete_rows_by_source_ids(conn, analytics_schema.FILTER_VALUES_TABLE, unique_ids)
        _delete_rows_by_source_ids(conn, analytics_schema.CACHE_SOURCES_TABLE, unique_ids, source_column="source_id")
        _delete_rows_by_source_ids(conn, analytics_schema.INGEST_MANIFEST_TABLE, unique_ids, source_column="source_id")
        _delete_rows_by_source_ids(conn, analytics_schema.INGEST_CHECKPOINT_TABLE, unique_ids, source_column="source_id")
    finally:
        conn.close()
    return {
        "dataflow_rows_deleted": dataflow_rows,
        "job_rows_deleted": job_rows,
        "filter_values_deleted": filter_rows,
    }


def _log_stream_root(etl_path: Path, stream_name: str) -> str:
    if etl_path.name == stream_name:
        return etl_path.as_posix()
    return (etl_path / stream_name).as_posix()


def _checkpoint_from_state(state: dict[str, Any] | None) -> LogStreamCheckpoint | None:
    if not state:
        return None
    boundary = state.get("boundary_last_modified")
    if not isinstance(boundary, datetime):
        boundary = parse_utc_datetime(boundary)
    if boundary is None:
        return None
    partition_value = state.get("partition_value")
    if isinstance(partition_value, datetime):
        partition_value = partition_value.date()
    elif not isinstance(partition_value, date):
        try:
            partition_value = date.fromisoformat(str(partition_value))
        except ValueError:
            return None
    return LogStreamCheckpoint(
        partition_value=partition_value,
        boundary_last_modified=boundary,
        partition_format=str(state.get("partition_format") or "%Y-%m-%d"),
    )


def _plan_stream_sync(
    adapter: LocalStorageAdapter,
    root_uri: str,
    *,
    suffix: str,
    checkpoint: LogStreamCheckpoint | None,
    spec: LogSyncSpec,
    manifest: dict[str, FileRevision],
) -> tuple[list[DiscoveredLogFile], list[DiscoveredLogFile], int]:
    expected_format = checkpoint.partition_format if checkpoint else None
    partitions = discover_partitions(adapter, root_uri, expected_format=expected_format)
    if partitions:
        incremental_partitions = plan_incremental_partitions(partitions, checkpoint)
        incremental_files = discover_partition_files(adapter, incremental_partitions, suffix=suffix)
    else:
        incremental_partitions = []
        incremental_files = _unpartitioned_log_files(adapter, root_uri, suffix)
    incremental = plan_incremental_candidates(incremental_files, checkpoint)
    lookback: list[DiscoveredLogFile] = []
    lookback_partition_count = 0
    if spec.mode is LogSyncMode.INCREMENTAL_WITH_LOOKBACK and spec.lookback is not None:
        if partitions:
            lookback_partitions = plan_lookback_partitions(partitions, spec.lookback)
            lookback_partition_count = len(lookback_partitions)
            lookback_files = discover_partition_files(adapter, lookback_partitions, suffix=suffix)
        else:
            lookback_files = _unpartitioned_log_files(adapter, root_uri, suffix)
        lookback = plan_lookback_candidates(lookback_files, spec.lookback, manifest)
    return incremental, deduplicate_candidates(lookback, incremental), len(incremental_partitions) + lookback_partition_count


def _unpartitioned_log_files(
    adapter: LocalStorageAdapter,
    root_uri: str,
    suffix: str,
) -> list[DiscoveredLogFile]:
    files: list[DiscoveredLogFile] = []
    for file_uri in adapter.list_files(root_uri, suffix):
        revision = adapter.stat(file_uri)
        partition_value = revision.last_modified.date()
        files.append(
            DiscoveredLogFile(
                partition=ParsedPartition(
                    partition_value=partition_value,
                    raw_partition_path=partition_value.isoformat(),
                    partition_granularity=PartitionGranularity.DAY,
                    partition_format="%Y-%m-%d",
                ),
                revision=revision,
            )
        )
    return files


def _revision_json(revision: FileRevision) -> str:
    provider_revision = revision.provider_revision
    mtime_ns = (
        int(provider_revision)
        if provider_revision and provider_revision.isdigit()
        else int(revision.last_modified.timestamp() * 1_000_000_000)
    )
    return json.dumps(
        {
            "size": revision.size,
            "mtime_ns": mtime_ns,
            "last_modified": revision.last_modified.isoformat(),
            "provider_revision": provider_revision,
        },
        sort_keys=True,
    )


def _file_revision_from_json(file_uri: str, revision_json: str) -> FileRevision | None:
    try:
        payload = json.loads(revision_json)
    except (TypeError, json.JSONDecodeError):
        return None
    last_modified = parse_utc_datetime(payload.get("last_modified"))
    if last_modified is None and payload.get("mtime_ns") is not None:
        try:
            last_modified = datetime.fromtimestamp(int(payload["mtime_ns"]) / 1_000_000_000, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None
    if last_modified is None:
        return None
    return FileRevision(
        canonical_uri=file_uri,
        size=int(payload.get("size") or 0),
        last_modified=last_modified,
        provider_revision=str(payload.get("provider_revision") or payload.get("mtime_ns") or "") or None,
    )


def _ingest_file_state(candidate: DiscoveredLogFile, file_kind: str) -> dict[str, Any]:
    return {
        "file_uri": candidate.canonical_uri,
        "file_kind": file_kind,
        "partition_value": candidate.partition.partition_value,
        "partition_format": candidate.partition.partition_format,
        "revision_json": _revision_json(candidate.revision),
        "job_id": None,
        "log_timestamp": None,
        "run_date": candidate.partition.partition_value,
    }


def _checkpoint_update(
    file_kind: str,
    incremental_candidates: list[DiscoveredLogFile],
    previous: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not incremental_candidates:
        return None
    latest_partition = max(
        incremental_candidates,
        key=lambda item: (item.partition.partition_value, item.canonical_uri),
    ).partition
    boundary = max(item.revision.last_modified for item in incremental_candidates)
    previous_boundary = _checkpoint_from_state(previous)
    if previous_boundary is not None:
        boundary = max(boundary, previous_boundary.boundary_last_modified)
    return {
        "file_kind": file_kind,
        "partition_format": latest_partition.partition_format,
        "partition_value": latest_partition.partition_value,
        "boundary_last_modified": boundary,
    }


def refresh_log_source_cache(
    session: Session,
    source: EnvironmentSource,
    *,
    job_type: str = "force_refresh",
    sync_spec: LogSyncSpec | None = None,
) -> dict[str, Any]:
    if source.source_kind != "logs":
        raise ValueError("Source is not a log source")

    job = sync.begin_sync_job(session, source, job_type)
    checked_at = utc_now()
    revision = log_source_revision(source)
    error = _revision_error(source, revision)
    if error:
        sync.record_source_revision(session, source=source, status="error", revision=None, error=error, checked_at=checked_at)
        sync.finish_sync_job(
            session,
            job,
            status="failed",
            message=error["message"],
            result={"status": "error", "message": error["message"], "revision": None, "error": error},
            completed_at=checked_at,
        )
        source_validation.record_source_validation(
            session,
            source,
            source_validation.source_validation_error(source, error["message"]),
            checked_at=checked_at,
        )
        return sync.source_sync_status(session, source, job)

    spec = sync_spec or LogSyncSpec()
    log_paths = resolve_log_source_paths(source)
    try:
        etl_path = require_local_path(log_paths.etl_logs_uri or source.uri)
        system_path = require_local_path(log_paths.system_logs_uri) if log_paths.system_logs_uri else None
    except StorageProviderNotEnabled as exc:
        error = {"message": str(exc), "code": "provider_not_enabled", "provider": exc.provider}
        sync.record_source_revision(session, source=source, status="error", revision=None, error=error, checked_at=checked_at)
        sync.finish_sync_job(
            session,
            job,
            status="failed",
            message=error["message"],
            result={"status": "error", "message": error["message"], "revision": None, "error": error},
            completed_at=checked_at,
        )
        source_validation.record_source_validation(
            session,
            source,
            source_validation.source_validation_error(
                source,
                error["message"],
                provider=exc.provider,
            ),
            checked_at=checked_at,
        )
        return sync.source_sync_status(session, source, job)
    adapter = LocalStorageAdapter()
    checkpoints, ingest_manifest_json = _read_ingest_state(source.id)
    ingest_manifest = {
        file_uri: parsed_revision
        for file_uri, revision_json in ingest_manifest_json.items()
        if (parsed_revision := _file_revision_from_json(file_uri, revision_json)) is not None
    }
    dataflow_incremental, dataflow_candidates, dataflow_partition_count = _plan_stream_sync(
        adapter,
        _log_stream_root(etl_path, "dataflow_run_log"),
        suffix=".parquet",
        checkpoint=_checkpoint_from_state(checkpoints.get("dataflow_parquet")),
        spec=spec,
        manifest=ingest_manifest,
    )
    job_incremental, job_candidates, job_partition_count = _plan_stream_sync(
        adapter,
        _log_stream_root(etl_path, "job_run_log"),
        suffix=".jsonl",
        checkpoint=_checkpoint_from_state(checkpoints.get("job_jsonl")),
        spec=spec,
        manifest=ingest_manifest,
    )
    system_files = discover_system_jsonl_files(system_path.as_posix() if system_path else None)
    existing = _existing_manifest(session, source.id)
    cache_has_source = _analytics_cache_has_source(source.id)
    analytic_candidates = [
        *((candidate, "dataflow_parquet") for candidate in dataflow_candidates),
        *((candidate, "job_jsonl") for candidate in job_candidates),
    ]
    changed_files = [_ingest_file_state(candidate, file_kind) for candidate, file_kind in analytic_candidates]
    system_states = [_manifest_file_state(file_uri, "system_jsonl") for file_uri in system_files]
    revision = _revision_with_known_files(
        revision,
        {
            **existing,
            **ingest_manifest_json,
            **{
                str(state["file_uri"]): str(state["revision_json"])
                for state in [*changed_files, *system_states]
            },
        },
    )
    changed_system_files = [
        state for state in system_states if existing.get(str(state["file_uri"])) != state["revision_json"]
    ]

    errors: list[dict[str, Any]] = []
    parsed_dataflow_files: list[tuple[str, str, str]] = []
    parsed_job_rows: list[tuple[str, str, str, dict[str, Any]]] = []
    file_row_counts: dict[str, int] = {}
    for file_state in changed_files:
        file_uri = str(file_state["file_uri"])
        file_kind = str(file_state["file_kind"])
        revision_json = str(file_state["revision_json"])
        if file_kind == "dataflow_parquet":
            parsed_dataflow_files.append((file_uri, file_kind, revision_json))
            read_errors = []
        elif file_kind == "job_jsonl":
            rows, read_errors = _read_job_file(file_uri)
            parsed_job_rows.extend((file_uri, file_kind, revision_json, row) for row in rows)
            file_row_counts[file_uri] = len(rows)
            file_state["row_count"] = len(rows)
        errors.extend(read_errors)
    for file_state in changed_system_files:
        file_row_counts[str(file_state["file_uri"])] = _count_jsonl_lines(str(file_state["file_uri"]))

    checkpoint_updates = [
        update
        for update in (
            _checkpoint_update("dataflow_parquet", dataflow_incremental, checkpoints.get("dataflow_parquet")),
            _checkpoint_update("job_jsonl", job_incremental, checkpoints.get("job_jsonl")),
        )
        if update is not None
    ]
    needs_publish = not cache_has_source or bool(changed_files)
    changed_file_uris = [str(file_state["file_uri"]) for file_state in changed_files]
    if errors:
        upsert_result = {
            "parsed_dataflow_records": 0,
            "file_row_counts": {},
            "errors": [],
            "published": False,
        }
    elif needs_publish:
        upsert_result = _upsert_duckdb_rows(
            source.id,
            parsed_dataflow_files,
            parsed_job_rows,
            [],
            changed_file_uris,
            ingest_files=changed_files,
            checkpoints=checkpoint_updates,
        )
    else:
        upsert_result = {
            "parsed_dataflow_records": 0,
            "file_row_counts": {},
            "errors": [],
            "published": True,
        }
    file_row_counts.update(upsert_result["file_row_counts"])
    errors.extend(upsert_result["errors"])
    published = bool(upsert_result["published"])
    if published and (changed_files or changed_system_files):
        _upsert_manifest(
            session,
            source.id,
            [*changed_files, *changed_system_files],
            [],
            file_row_counts,
            checked_at,
        )
    if published and needs_publish:
        invalidate_environment_derived_caches(session, source.environment_id, structural=False)

    status = "error" if errors else "ok"
    message = "Log source cache refreshed" if changed_files or changed_system_files else "Log source cache is current"
    if errors:
        message = "Log source analytics were not published; the previous cache was preserved"
    error = errors[0] if errors else None
    sync.record_source_revision(
        session,
        source=source,
        status=status,
        revision=revision,
        error=error,
        checked_at=checked_at,
    )
    sync.finish_sync_job(
        session,
        job,
        status="failed" if errors else "succeeded",
        message=message,
        result={
            "status": status,
            "message": message,
            "revision": revision,
            "error": error,
            "record_counts": {
                "parsed_dataflow_records": upsert_result["parsed_dataflow_records"],
                "parsed_job_records": len(parsed_job_rows),
                "dataflow_parquet_files": len(dataflow_candidates),
                "job_jsonl_files": len(job_candidates),
                "system_jsonl_files": len(system_files),
                "parsed_files": len(changed_files),
                "replaced_files": sum(
                    1 for state in changed_files if str(state["file_uri"]) in ingest_manifest_json
                ),
                "new_files": sum(
                    1 for state in changed_files if str(state["file_uri"]) not in ingest_manifest_json
                ),
                "removed_files": 0,
                "scanned_partitions": dataflow_partition_count + job_partition_count,
            },
            "sync_mode": spec.mode.value,
            "errors": errors,
        },
        completed_at=checked_at,
    )
    _invalidate_log_pending_changes(source.id)
    validation_counts = {
        "dataflow_parquet_files": len(dataflow_candidates),
        "job_jsonl_files": len(job_candidates),
        "system_jsonl_files": len(system_files),
    }
    validation_result = (
        {
            **source_validation.source_validation_error(source, message),
            "errors": errors,
        }
        if errors
        else {
            "source_id": source.id,
            "source_kind": "logs",
            "status": "ok",
            "message": "Log source is readable",
            "detected_provider": "local",
            "detected_format": "logs",
            "record_counts": validation_counts,
            "records_scanned": sum(validation_counts.values()),
            "errors": [],
        }
    )
    source_validation.record_source_validation(
        session,
        source,
        validation_result,
        checked_at=checked_at,
    )
    return sync.source_sync_status(session, source, job)


def log_source_has_pending_changes(
    session: Session,
    source: EnvironmentSource,
    *,
    ttl_seconds: float = 0.0,
) -> bool:
    """Return True when a cached Log source has files that differ from the last sync.

    Read-only: it discovers the log files the sync would pick up (the same discovery,
    so ``debug_json`` and other ignored files are excluded) and compares them to the
    stored manifest, without ingesting anything or touching the cache. Sources that
    have never been synced (no manifest) return False; their empty-cache state is
    already reported as ``not_cached`` by freshness.

    When ``ttl_seconds`` is positive the result is cached for that long so the
    filesystem scan is not repeated on every freshness/context read.
    """
    if source.source_kind != "logs":
        return False
    if ttl_seconds > 0:
        now = time.monotonic()
        with _pending_changes_lock:
            entry = _pending_changes_cache.get(source.id)
            if entry is not None and entry[0] > now:
                return entry[1]
    result = _compute_log_source_pending_changes(session, source)
    if ttl_seconds > 0:
        with _pending_changes_lock:
            _pending_changes_cache[source.id] = (time.monotonic() + ttl_seconds, result)
    return result


def _compute_log_source_pending_changes(session: Session, source: EnvironmentSource) -> bool:
    try:
        log_paths = resolve_log_source_paths(source)
        etl_path = require_local_path(log_paths.etl_logs_uri or source.uri)
        system_path = require_local_path(log_paths.system_logs_uri) if log_paths.system_logs_uri else None
    except StorageProviderNotEnabled:
        return False
    try:
        checkpoints, ingest_manifest_json = _read_ingest_state(source.id)
        if not checkpoints:
            return False
        ingest_manifest = {
            file_uri: parsed_revision
            for file_uri, revision_json in ingest_manifest_json.items()
            if (parsed_revision := _file_revision_from_json(file_uri, revision_json)) is not None
        }
        adapter = LocalStorageAdapter()
        for stream_name, suffix, file_kind in (
            ("dataflow_run_log", ".parquet", "dataflow_parquet"),
            ("job_run_log", ".jsonl", "job_jsonl"),
        ):
            _, candidates, _ = _plan_stream_sync(
                adapter,
                _log_stream_root(etl_path, stream_name),
                suffix=suffix,
                checkpoint=_checkpoint_from_state(checkpoints.get(file_kind)),
                spec=LogSyncSpec(),
                manifest=ingest_manifest,
            )
            if candidates:
                return True
        system_manifest = {
            row.file_uri: row.revision_json
            for row in session.scalars(
                select(LogFileManifest).where(
                    LogFileManifest.source_id == source.id,
                    LogFileManifest.file_kind == "system_jsonl",
                )
            )
        }
        system_files = discover_system_jsonl_files(system_path.as_posix() if system_path else None)
        current_system = {
            file_uri: _file_revision_json(file_uri)
            for file_uri in system_files
        }
    except OSError:
        return False
    if set(system_manifest) != set(current_system):
        return bool(system_manifest or current_system)
    return any(
        not _revision_equivalent(system_manifest.get(file_uri), revision_json)
        for file_uri, revision_json in current_system.items()
    )


def _invalidate_log_pending_changes(source_id: int) -> None:
    with _pending_changes_lock:
        _pending_changes_cache.pop(source_id, None)


def system_log_records(
    session: Session,
    paths: list[EnvironmentSource],
    *,
    job_id: str,
    dataflow_id: str | None = None,
    include_dataflow_logs: bool = False,
    level: str | None = None,
    q: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    enabled_ids = _analytics_source_ids(paths)
    if not enabled_ids or not job_id:
        return {"records": [], "total": 0, "files": [], "errors": []}
    files = list(
        session.scalars(
            select(LogFileManifest)
            .where(
                LogFileManifest.source_id.in_(enabled_ids),
                LogFileManifest.file_kind == "system_jsonl",
                LogFileManifest.job_id == job_id,
            )
            .order_by(LogFileManifest.log_timestamp.desc(), LogFileManifest.id.desc())
        )
    )
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    total = 0
    remaining_offset = offset
    for file in files:
        file_rows, file_total, file_errors = read_system_log_file(
            file.file_uri,
            job_id=job_id,
            dataflow_id=dataflow_id,
            include_dataflow_logs=include_dataflow_logs,
            level=level,
            q=q,
            limit=limit - len(records),
            offset=remaining_offset,
        )
        total += file_total
        errors.extend(file_errors)
        if remaining_offset:
            remaining_offset = max(0, remaining_offset - file_total)
        records.extend(file_rows)
        if len(records) >= limit:
            break
    return {
        "records": records,
        "total": total,
        "files": [
            {
                "source_id": file.source_id,
                "file_uri": file.file_uri,
                "row_count": file.row_count,
                "log_timestamp": file.log_timestamp,
                "run_date": file.run_date,
            }
            for file in files
        ],
        "errors": errors,
    }


def _upsert_duckdb_rows(
    source_id: int,
    dataflow_files: list[tuple[str, str, str]],
    job_rows: list[tuple[str, str, str, dict[str, Any]]],
    removed_files: list[str],
    changed_files: list[str],
    *,
    ingest_files: list[dict[str, Any]] | None = None,
    checkpoints: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    path = analytics_database_path()
    if analytics_access.schema_rebuild_required(path):
        with analytics_access.analytics_maintenance_lock:
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
                    ingest_files=ingest_files,
                    checkpoints=checkpoints,
                )
                if result["published"]:
                    try:
                        _validate_analytics_candidate(candidate_path, source_id)
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
        ingest_files=ingest_files,
        checkpoints=checkpoints,
    )


def _write_duckdb_rows(
    path: Path,
    source_id: int,
    dataflow_files: list[tuple[str, str, str]],
    job_rows: list[tuple[str, str, str, dict[str, Any]]],
    removed_files: list[str],
    changed_files: list[str],
    *,
    ingest_files: list[dict[str, Any]] | None = None,
    checkpoints: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    parsed_dataflow_records = 0
    file_row_counts: dict[str, int] = {}
    errors: list[dict[str, str]] = []
    conn = analytics_access.connect(path)
    try:
        _ensure_duckdb_tables(conn)
        conn.execute("BEGIN TRANSACTION")
        try:
            _preflight_dataflow_schemas(conn, [file_uri for file_uri, _, _ in dataflow_files])
            stale_files = [*removed_files, *changed_files]
            for file_uri in stale_files:
                if analytics_schema.table_exists(conn, analytics_schema.DATAFLOW_TABLE):
                    conn.execute(f"DELETE FROM {analytics_schema.DATAFLOW_TABLE} WHERE _source_id = ? AND _file_uri = ?", [source_id, file_uri])
                if analytics_schema.table_exists(conn, analytics_schema.JOB_TABLE):
                    conn.execute(f"DELETE FROM {analytics_schema.JOB_TABLE} WHERE _source_id = ? AND _file_uri = ?", [source_id, file_uri])
            for file_uri, file_kind, revision_json in dataflow_files:
                row_count = _insert_dataflow_file(conn, source_id, file_uri, file_kind, revision_json)
                parsed_dataflow_records += row_count
                file_row_counts[file_uri] = row_count
            if job_rows:
                _insert_typed_rows(conn, analytics_schema.JOB_TABLE, source_id, job_rows, analytics_schema.JOB_COLUMN_TYPES)
            _assert_ingest_files_stable(ingest_files or [])
            analytics_store.upsert_ingest_control_rows(
                conn,
                source_id,
                ingest_files or [],
                checkpoints or [],
                file_row_counts,
                ingested_at=utc_now(),
            )
            _refresh_filter_values(conn, source_id)
            analytics_store.mark_cache_source(conn, source_id, refreshed_at=utc_now())
            analytics_store.publish_generation(
                conn,
                dataflow_column_types=analytics_schema.DATAFLOW_COLUMN_TYPES,
                job_column_types=analytics_schema.JOB_COLUMN_TYPES,
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


def _upsert_manifest(
    session: Session,
    source_id: int,
    changed_files: list[dict[str, Any]],
    removed_files: list[str],
    file_row_counts: dict[str, int],
    checked_at: datetime,
) -> None:
    if removed_files:
        session.execute(
            delete(LogFileManifest).where(
                LogFileManifest.source_id == source_id,
                LogFileManifest.file_uri.in_(removed_files),
            )
        )
    for file_state in changed_files:
        file_uri = str(file_state["file_uri"])
        session.execute(
            delete(LogFileManifest).where(
                LogFileManifest.source_id == source_id,
                LogFileManifest.file_uri == file_uri,
            )
        )
        session.add(_manifest_row(source_id, file_state, file_row_counts.get(file_uri, 0), checked_at))


def _manifest_row(source_id: int, file_state: dict[str, Any], row_count: int, checked_at: datetime) -> LogFileManifest:
    return LogFileManifest(
        source_id=source_id,
        file_uri=str(file_state["file_uri"]),
        file_kind=str(file_state["file_kind"]),
        revision_json=str(file_state["revision_json"]),
        row_count=row_count,
        job_id=file_state.get("job_id"),
        log_timestamp=file_state.get("log_timestamp"),
        run_date=file_state.get("run_date"),
        status="ok",
        first_seen_at=checked_at,
        last_seen_at=checked_at,
    )


def _existing_manifest(session: Session, source_id: int) -> dict[str, str]:
    rows = session.scalars(select(LogFileManifest).where(LogFileManifest.source_id == source_id)).all()
    return {row.file_uri: row.revision_json for row in rows}


def _manifest_file_state(file_uri: str, file_kind: str) -> dict[str, Any]:
    metadata = parse_system_log_file_metadata(file_uri) if file_kind == "system_jsonl" else {}
    return {
        "file_uri": file_uri,
        "file_kind": file_kind,
        "revision_json": _file_revision_json(file_uri),
        "job_id": metadata.get("job_id"),
        "log_timestamp": metadata.get("log_timestamp"),
        "run_date": metadata.get("run_date"),
    }


def _file_revision_json(file_uri: str) -> str:
    stat = Path(file_uri).stat()
    return json.dumps({"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}, sort_keys=True)


def _assert_ingest_files_stable(files: list[dict[str, Any]]) -> None:
    """Abort the transaction when bytes changed after candidate discovery."""
    for file_state in files:
        file_uri = str(file_state["file_uri"])
        expected = str(file_state["revision_json"])
        try:
            actual = _file_revision_json(file_uri)
        except OSError as exc:
            raise LogFileChangedDuringSyncError(
                f"Log file became unavailable during sync: {file_uri}"
            ) from exc
        if not _revision_equivalent(expected, actual):
            raise LogFileChangedDuringSyncError(
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


def _revision_with_known_files(
    base_revision: dict[str, Any],
    file_revisions: dict[str, str],
) -> dict[str, Any]:
    """Enrich the shallow root revision from bounded discovery and persisted manifests."""
    total_size = 0
    max_mtime_ns = base_revision.get("max_mtime_ns")
    for revision_json in file_revisions.values():
        try:
            payload = json.loads(revision_json)
            total_size += int(payload.get("size") or 0)
            mtime_ns = int(payload.get("mtime_ns") or 0)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        max_mtime_ns = mtime_ns if max_mtime_ns is None else max(int(max_mtime_ns), mtime_ns)
    return {
        **base_revision,
        "file_count": len(file_revisions),
        "total_size": total_size,
        "max_mtime_ns": max_mtime_ns,
    }


def _read_dataflow_file(file_uri: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    conn = duckdb.connect(database=":memory:")
    try:
        escaped = file_uri.replace("'", "''")
        result = conn.execute(f"SELECT * FROM read_parquet('{escaped}', union_by_name=true)")
        names = [desc[0] for desc in result.description]
        return [_json_ready(dict(zip(names, row))) for row in result.fetchall()], []
    except Exception as exc:
        return [], [{"uri": file_uri, "message": str(exc)}]
    finally:
        conn.close()


def _read_job_file(file_uri: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    path = Path(file_uri)
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    rows.append(_json_ready(json.loads(line)))
                except json.JSONDecodeError as exc:
                    errors.append(
                        {
                            "uri": file_uri,
                            "message": f"Invalid JSONL at line {line_number}: {exc}",
                            "code": "invalid_jsonl",
                        }
                    )
    except OSError as exc:
        errors.append({"uri": file_uri, "message": str(exc), "code": "file_read_failed"})
    return rows, errors


def _count_jsonl_lines(file_uri: str) -> int:
    try:
        with Path(file_uri).open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def _json_ready(row: dict[str, Any]) -> dict[str, Any]:
    ready = {}
    for key, value in row.items():
        if isinstance(value, (datetime, date)):
            ready[key] = value.isoformat()
        else:
            ready[key] = value
    return ready


def _ensure_duckdb_tables(conn) -> None:
    recreated = [
        _ensure_dataflow_cache_table(conn),
        _ensure_typed_table(conn, analytics_schema.JOB_TABLE, analytics_schema.JOB_COLUMN_TYPES),
    ]
    _drop_empty_generated_job_columns(conn)
    if any(recreated) and analytics_schema.table_exists(conn, analytics_schema.FILTER_VALUES_TABLE):
        conn.execute(f"DROP TABLE {analytics_schema.FILTER_VALUES_TABLE}")
    analytics_schema.ensure_filter_values_table(conn)
    analytics_schema.ensure_cache_sources_table(conn)
    analytics_schema.ensure_ingest_control_tables(conn)
    analytics_schema.ensure_analytics_meta_table(conn)
    _migrate_legacy_cache(conn)


def _ensure_duckdb_cache_ready(path: Path) -> None:
    """Leave incompatible cache files untouched until a candidate rebuild swaps them."""
    del path


def _validate_analytics_candidate(candidate_path: Path, source_id: int) -> None:
    if not analytics_access.cache_is_ready(candidate_path):
        raise RuntimeError("Analytics rebuild candidate did not create the current typed schema")
    conn = analytics_access.connect(candidate_path, read_only=True)
    try:
        analytics_access.materialization_token_from_connection(conn, [source_id])
    finally:
        conn.close()


def _analytics_source_ids(paths: list[EnvironmentSource]) -> list[int]:
    return sorted(
        path.id
        for path in paths
        if path.enabled and not source_validation.is_validated_empty_log_source(path)
    )


def _cached_analytics_source_ids(source_ids: list[int]) -> set[int]:
    if not source_ids:
        return set()
    path = analytics_database_path()
    if not path.exists() or not analytics_access.cache_is_ready(path):
        return set()
    conn = analytics_access.connect(path, read_only=True)
    try:
        placeholders = ", ".join("?" for _ in source_ids)
        return {
            int(row[0])
            for row in conn.execute(
                f"SELECT source_id FROM {analytics_schema.CACHE_SOURCES_TABLE} WHERE source_id IN ({placeholders})",
                source_ids,
            ).fetchall()
        }
    finally:
        conn.close()


def _analytics_cache_has_source(source_id: int) -> bool:
    path = analytics_database_path()
    if not path.exists() or not analytics_access.cache_is_ready(path):
        return False
    conn = analytics_access.connect(path, read_only=True)
    try:
        meta = analytics_store.analytics_meta(conn)
        return bool(
            meta
            and meta["schema_version"] == analytics_schema.ANALYTICS_SCHEMA_VERSION
            and meta["build_state"] == "ready"
            and source_id in analytics_store.cache_source_ids(conn)
        )
    finally:
        conn.close()


def _read_ingest_state(source_id: int) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    path = analytics_database_path()
    if not path.exists() or not analytics_access.cache_is_ready(path):
        return {}, {}
    conn = analytics_access.connect(path, read_only=True)
    try:
        if not analytics_schema.table_exists(conn, analytics_schema.INGEST_CHECKPOINT_TABLE) or not analytics_schema.table_exists(conn, analytics_schema.INGEST_MANIFEST_TABLE):
            return {}, {}
        checkpoints = {
            str(file_kind): {
                "file_kind": str(file_kind),
                "partition_format": str(partition_format),
                "partition_value": partition_value,
                "boundary_last_modified": boundary_last_modified,
            }
            for file_kind, partition_format, partition_value, boundary_last_modified in conn.execute(
                f"""
                SELECT log_kind, partition_format, partition_value, boundary_last_modified
                FROM {analytics_schema.INGEST_CHECKPOINT_TABLE}
                WHERE source_id = ?
                """,
                [source_id],
            ).fetchall()
        }
        manifests = {
            str(file_uri): str(revision_json)
            for file_uri, revision_json in conn.execute(
                f"SELECT file_uri, revision_json FROM {analytics_schema.INGEST_MANIFEST_TABLE} WHERE source_id = ?",
                [source_id],
            ).fetchall()
        }
        return checkpoints, manifests
    finally:
        conn.close()


def _refresh_filter_values(conn, source_id: int) -> None:
    analytics_schema.ensure_filter_values_table(conn)
    conn.execute(f"DELETE FROM {analytics_schema.FILTER_VALUES_TABLE} WHERE _source_id = ?", [source_id])
    updated_at = utc_now().isoformat()
    for field, (table_name, column_name) in FILTER_VALUE_SOURCES.items():
        if not analytics_schema.table_exists(conn, table_name):
            continue
        if column_name not in analytics_schema.table_columns(conn, table_name):
            continue
        conn.execute(
            f"""
            INSERT INTO {analytics_schema.FILTER_VALUES_TABLE}
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
    if analytics_schema.table_exists(conn, analytics_schema.DATAFLOW_TABLE):
        dataflow_columns = set(analytics_schema.table_columns(conn, analytics_schema.DATAFLOW_TABLE))
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
                FROM {analytics_schema.DATAFLOW_TABLE}
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
            INSERT INTO {analytics_schema.FILTER_VALUES_TABLE}
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
            legacy_table=analytics_schema.LEGACY_DATAFLOW_TABLE,
            target_table=analytics_schema.DATAFLOW_TABLE,
            column_types=analytics_schema.DATAFLOW_COLUMN_TYPES,
            file_kind="legacy_dataflow_json",
        )
    )
    migrated_source_ids.update(
        _migrate_legacy_table(
            conn,
            legacy_table=analytics_schema.LEGACY_JOB_TABLE,
            target_table=analytics_schema.JOB_TABLE,
            column_types=analytics_schema.JOB_COLUMN_TYPES,
            file_kind="legacy_job_json",
        )
    )
    for source_id in sorted(migrated_source_ids):
        _refresh_filter_values(conn, source_id)


def _migrate_legacy_table(
    conn,
    legacy_table: str,
    target_table: str,
    column_types: dict[str, str],
    file_kind: str,
) -> set[int]:
    if not analytics_schema.table_exists(conn, legacy_table):
        return set()
    legacy_columns = set(analytics_schema.table_columns(conn, legacy_table))
    if not {"source_id", "file_uri", "row_json"} <= legacy_columns:
        return set()

    if not analytics_schema.table_exists(conn, target_table):
        _ensure_typed_table(conn, target_table, column_types)

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
            if not analytics_schema.table_exists(conn, target_table):
                _ensure_typed_table(conn, target_table, column_types)
            _insert_typed_rows(conn, target_table, source_id, typed_rows, column_types)
            migrated_source_ids.add(source_id)
    conn.execute(f"DROP TABLE {legacy_table}")
    return migrated_source_ids


def _insert_typed_rows(
    conn,
    table_name: str,
    source_id: int,
    rows: list[tuple[str, str, str, dict[str, Any]]],
    column_types: dict[str, str],
) -> None:
    _ensure_source_columns(conn, table_name, rows, column_types)
    columns = analytics_schema.table_columns(conn, table_name)
    insert_columns = [
        column
        for column in columns
        if (
            column in analytics_schema.STUDIO_CACHE_COLUMNS
            or column in analytics_schema.GENERATED_CACHE_COLUMNS
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


def _insert_dataflow_file(conn, source_id: int, file_uri: str, file_kind: str, revision_json: str) -> int:
    _ensure_dataflow_table_for_parquet(conn, file_uri)
    escaped = file_uri.replace("'", "''")
    row_count = conn.execute(f"SELECT count(*) FROM read_parquet('{escaped}', union_by_name=true)").fetchone()[0]
    conn.execute(
        f"""
        INSERT INTO {analytics_schema.DATAFLOW_TABLE} BY NAME
        SELECT
          {int(source_id)}::BIGINT AS _source_id,
          {_sql_string(file_uri)} AS _file_uri,
          {_sql_string(file_kind)} AS _file_kind,
          {_sql_date(_file_date(file_uri, {}))} AS _file_date,
          {_sql_number(_revision_value(revision_json, "size"))}::BIGINT AS _source_size,
          {_sql_number(_revision_value(revision_json, "mtime_ns"))}::BIGINT AS _source_mtime_ns,
          {_sql_string(utc_now().isoformat())}::TIMESTAMPTZ AS _ingested_at,
          *
        FROM read_parquet('{escaped}', union_by_name=true)
        """
    )
    return int(row_count)


def _select_typed_rows(
    conn,
    table_name: str,
    placeholders: str,
    source_ids: list[int],
    *,
    columns: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    if not analytics_schema.table_exists(conn, table_name):
        return []
    available = set(analytics_schema.table_columns(conn, table_name))
    selected = [column for column in (columns or ()) if column in available]
    column_sql = ", ".join(_quote_identifier(column) for column in selected) if selected else "*"
    result = conn.execute(
        f"SELECT {column_sql} FROM {table_name} WHERE _source_id IN ({placeholders})",
        source_ids,
    )
    names = [desc[0] for desc in result.description]
    rows = []
    for values in result.fetchall():
        row = _json_ready(dict(zip(names, values)))
        rows.append(row)
    return rows


def _ensure_typed_table(conn, table_name: str, column_types: dict[str, str]) -> bool:
    columns = analytics_schema.table_columns(conn, table_name)
    if columns and ("_source_id" not in columns or analytics_schema.has_legacy_raw_json_column(columns) or analytics_schema.has_incompatible_column_types(conn, table_name, column_types)):
        conn.execute(f"DROP TABLE {table_name}")
        columns = []
    if not columns:
        definitions = [
            f"{_quote_identifier(column)} {data_type}"
            for column, data_type in analytics_schema.cache_table_column_types(column_types).items()
        ]
        conn.execute(f"CREATE TABLE {table_name} ({', '.join(definitions)})")
        return True
    _ensure_columns(conn, table_name, analytics_schema.cache_table_column_types(column_types), set(columns))
    _ensure_column_order(conn, table_name, column_types)
    return False


def _ensure_dataflow_cache_table(conn) -> bool:
    columns = analytics_schema.table_columns(conn, analytics_schema.DATAFLOW_TABLE)
    if columns and ("_source_id" not in columns or analytics_schema.has_legacy_raw_json_column(columns)):
        conn.execute(f"DROP TABLE {analytics_schema.DATAFLOW_TABLE}")
        columns = []
    if not columns:
        return _ensure_typed_table(conn, analytics_schema.DATAFLOW_TABLE, {})
    existing = set(columns)
    _ensure_columns(conn, analytics_schema.DATAFLOW_TABLE, analytics_schema.STUDIO_CACHE_COLUMNS, existing)
    _ensure_column_order(conn, analytics_schema.DATAFLOW_TABLE, analytics_schema.actual_source_column_types(conn, analytics_schema.DATAFLOW_TABLE))
    return False


def _ensure_source_columns(
    conn,
    table_name: str,
    rows: list[tuple[str, str, str, dict[str, Any]]],
    column_types: dict[str, str],
) -> None:
    existing = set(analytics_schema.table_columns(conn, table_name))
    actual_types = analytics_schema.table_column_types(conn, table_name)
    inferred: dict[str, set[str]] = {}
    for _, _, _, row in rows:
        for column, value in row.items():
            if value is None or column in analytics_schema.STUDIO_CACHE_COLUMNS:
                continue
            expected = column_types.get(column) or _infer_duckdb_type(value)
            if column in existing:
                actual = actual_types.get(column)
                if actual and not analytics_schema.duckdb_type_matches(actual, expected):
                    raise LogSchemaIncompatibleError(
                        f"Column {column!r} changed datatype from {actual} to {expected}"
                    )
                continue
            inferred.setdefault(column, set()).add(expected)
    conflicts = {column: types for column, types in inferred.items() if len(types) > 1}
    if conflicts:
        column, types = sorted(conflicts.items())[0]
        raise LogSchemaIncompatibleError(
            f"New column {column!r} has conflicting datatypes: {', '.join(sorted(types))}"
        )
    discovered = {column: next(iter(types)) for column, types in inferred.items()}
    if discovered:
        _ensure_columns(conn, table_name, discovered, existing)
        _ensure_column_order(conn, table_name, {**column_types, **discovered})


def _ensure_dataflow_table_for_parquet(conn, file_uri: str) -> None:
    described = _describe_parquet_columns(conn, file_uri)
    parquet_column_types = {
        name: data_type
        for name, data_type in described
        if name not in analytics_schema.STUDIO_CACHE_COLUMNS
    }
    if not analytics_schema.table_exists(conn, analytics_schema.DATAFLOW_TABLE):
        definitions = [
            f"{_quote_identifier(column)} {data_type}"
            for column, data_type in analytics_schema.cache_table_column_types(parquet_column_types).items()
        ]
        conn.execute(f"CREATE TABLE {analytics_schema.DATAFLOW_TABLE} ({', '.join(definitions)})")
        return
    existing = set(analytics_schema.table_columns(conn, analytics_schema.DATAFLOW_TABLE))
    actual_types = analytics_schema.table_column_types(conn, analytics_schema.DATAFLOW_TABLE)
    for column, source_type in parquet_column_types.items():
        actual_type = actual_types.get(column)
        if actual_type and not _source_type_fits_target(actual_type, source_type):
            raise LogSchemaIncompatibleError(
                f"Column {column!r} changed datatype from {actual_type} to {source_type} in {file_uri}"
            )
    discovered = {
        column: data_type
        for column, data_type in parquet_column_types.items()
        if column not in existing
    }
    if discovered:
        _ensure_columns(conn, analytics_schema.DATAFLOW_TABLE, discovered, existing)
        _ensure_column_order(conn, analytics_schema.DATAFLOW_TABLE, parquet_column_types)


def _preflight_dataflow_schemas(conn, file_uris: list[str]) -> None:
    if not file_uris:
        return
    candidate_types: dict[str, str] = {}
    for file_uri in file_uris:
        for column, source_type in _describe_parquet_columns(conn, file_uri):
            if column in analytics_schema.STUDIO_CACHE_COLUMNS:
                continue
            previous = candidate_types.get(column)
            candidate_types[column] = source_type if previous is None else _common_source_type(column, previous, source_type)
    existing = set(analytics_schema.table_columns(conn, analytics_schema.DATAFLOW_TABLE))
    actual_types = analytics_schema.table_column_types(conn, analytics_schema.DATAFLOW_TABLE)
    for column, source_type in candidate_types.items():
        actual_type = actual_types.get(column)
        if actual_type and not _source_type_fits_target(actual_type, source_type):
            raise LogSchemaIncompatibleError(
                f"Column {column!r} changed datatype from {actual_type} to {source_type}"
            )
    discovered = {column: data_type for column, data_type in candidate_types.items() if column not in existing}
    if discovered:
        _ensure_columns(conn, analytics_schema.DATAFLOW_TABLE, discovered, existing)
    _ensure_column_order(conn, analytics_schema.DATAFLOW_TABLE, candidate_types)


def _common_source_type(column: str, left: str, right: str) -> str:
    left_type = left.upper()
    right_type = right.upper()
    if left_type == right_type:
        return left
    if {left_type, right_type} <= {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "FLOAT", "DOUBLE"}:
        return "DOUBLE" if "DOUBLE" in {left_type, right_type} or "FLOAT" in {left_type, right_type} else "BIGINT"
    if left_type.startswith("TIMESTAMP") and right_type.startswith("TIMESTAMP"):
        return "TIMESTAMPTZ"
    raise LogSchemaIncompatibleError(
        f"Column {column!r} has incompatible source datatypes {left} and {right}"
    )


def _source_type_fits_target(target_type: str, source_type: str) -> bool:
    target = target_type.upper()
    source = source_type.upper()
    if analytics_schema.duckdb_type_matches(target, source):
        return True
    if target == "DOUBLE" and source in {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "FLOAT"}:
        return True
    return target in {"TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE"} and source.startswith("TIMESTAMP")


def _describe_parquet_columns(conn, file_uri: str) -> list[tuple[str, str]]:
    escaped = file_uri.replace("'", "''")
    described = conn.execute(f"DESCRIBE SELECT * FROM read_parquet('{escaped}', union_by_name=true)").fetchall()
    return [(str(row[0]), str(row[1])) for row in described]


def _ensure_columns(conn, table_name: str, column_types: dict[str, str], existing: set[str]) -> None:
    for column, data_type in column_types.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {_quote_identifier(column)} {data_type}")
            existing.add(column)


def _ensure_column_order(conn, table_name: str, source_column_types: dict[str, str]) -> None:
    actual_columns = analytics_schema.table_columns(conn, table_name)
    if not actual_columns:
        return
    expected_columns = analytics_schema.expected_column_order(actual_columns, source_column_types)
    if actual_columns == expected_columns:
        return
    actual_types = analytics_schema.table_column_types(conn, table_name)
    expected_types = analytics_schema.cache_table_column_types(source_column_types)
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
    if not analytics_schema.table_exists(conn, analytics_schema.JOB_TABLE):
        return
    columns = set(analytics_schema.table_columns(conn, analytics_schema.JOB_TABLE))
    for column in ("operation_type",):
        if column not in columns:
            continue
        quoted_column = _quote_identifier(column)
        try:
            non_null_count = conn.execute(
                f"SELECT count(*) FROM {analytics_schema.JOB_TABLE} WHERE {quoted_column} IS NOT NULL"
            ).fetchone()[0]
            if int(non_null_count or 0) == 0:
                conn.execute(f"ALTER TABLE {analytics_schema.JOB_TABLE} DROP COLUMN {quoted_column}")
        except duckdb.Error:
            continue


def _table_row_count(conn, table_name: str) -> int:
    if not analytics_schema.table_exists(conn, table_name):
        return 0
    return int(conn.execute(f"SELECT count(*) FROM {table_name}").fetchone()[0])


def _table_source_row_count(conn, table_name: str, source_id: int) -> int:
    if not analytics_schema.table_exists(conn, table_name) or "_source_id" not in analytics_schema.table_columns(conn, table_name):
        return 0
    return int(
        conn.execute(
            f"SELECT count(*) FROM {table_name} WHERE _source_id = ?",
            [source_id],
        ).fetchone()[0]
        or 0
    )


def _table_source_ids(conn, table_name: str) -> list[int]:
    if not analytics_schema.table_exists(conn, table_name):
        return []
    if "_source_id" not in analytics_schema.table_columns(conn, table_name):
        return []
    rows = conn.execute(
        f"SELECT DISTINCT _source_id FROM {table_name} WHERE _source_id IS NOT NULL ORDER BY _source_id"
    ).fetchall()
    return [int(row[0]) for row in rows]


def _table_has_source_rows(conn, table_name: str, source_ids: list[int]) -> bool:
    if not source_ids or not analytics_schema.table_exists(conn, table_name):
        return False
    if "_source_id" not in analytics_schema.table_columns(conn, table_name):
        return False
    placeholders = ", ".join("?" for _ in source_ids)
    count = conn.execute(
        f"SELECT count(*) FROM {table_name} WHERE _source_id IN ({placeholders})",
        source_ids,
    ).fetchone()[0]
    return int(count or 0) > 0


def _delete_rows_by_source_ids(
    conn,
    table_name: str,
    source_ids: list[int],
    *,
    source_column: str = "_source_id",
) -> int:
    if not source_ids or not analytics_schema.table_exists(conn, table_name):
        return 0
    if source_column not in analytics_schema.table_columns(conn, table_name):
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


def _revision_error(source: EnvironmentSource, revision: dict[str, Any]) -> dict[str, Any] | None:
    if revision.get("object_type") == "provider_not_enabled":
        provider = str(revision.get("provider") or "storage")
        return {"message": f"{provider.upper()} storage URI is recognized but not enabled yet: {source.uri}", "code": "provider_not_enabled"}
    if not revision.get("exists"):
        return {"message": f"ETL log path not found: {source.uri}", "code": "not_found"}
    if revision.get("object_type") != "directory":
        return {"message": f"ETL log source must be a directory: {source.uri}", "code": "invalid_type"}
    return None

