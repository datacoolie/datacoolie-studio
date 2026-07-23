from __future__ import annotations

import json
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
from datacoolie_studio.domains.analytics.errors import (
    AnalyticsFileChangedDuringPublishError as LogFileChangedDuringSyncError,
)
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
        upsert_result = analytics_store.publish_rows(
            source.id,
            parsed_dataflow_files,
            parsed_job_rows,
            [],
            changed_file_uris,
            database_path=analytics_database_path(),
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
    invalidate_pending_changes(source.id)
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


def invalidate_pending_changes(source_id: int) -> None:
    with _pending_changes_lock:
        _pending_changes_cache.pop(source_id, None)


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


def _revision_error(source: EnvironmentSource, revision: dict[str, Any]) -> dict[str, Any] | None:
    if revision.get("object_type") == "provider_not_enabled":
        provider = str(revision.get("provider") or "storage")
        return {"message": f"{provider.upper()} storage URI is recognized but not enabled yet: {source.uri}", "code": "provider_not_enabled"}
    if not revision.get("exists"):
        return {"message": f"ETL log path not found: {source.uri}", "code": "not_found"}
    if revision.get("object_type") != "directory":
        return {"message": f"ETL log source must be a directory: {source.uri}", "code": "invalid_type"}
    return None
