from __future__ import annotations

import json
import hashlib
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any

import duckdb
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from datacoolie_studio.core.config import (
    analytics_database_path,
    source_materialization_cache_dir,
)
from datacoolie_studio.core.time import parse_utc_datetime
from datacoolie_studio.db.models import (
    EnvironmentSource,
    LogFileManifest,
    LogStreamState,
    SyncJob,
    utc_now,
)
from datacoolie_studio.domains.analytics import access as analytics_access
from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.analytics import store as analytics_store
from datacoolie_studio.domains.analytics.errors import (
    AnalyticsFileChangedDuringPublishError as LogFileChangedDuringSyncError,
)
from datacoolie_studio.domains.logs.discovery import (
    DiscoveredLogFile,
    LogStreamCheckpoint,
    LogSyncSpec,
)
from datacoolie_studio.domains.logs import control as log_control
from datacoolie_studio.domains.logs.control import StreamStateUpdate
from datacoolie_studio.domains.logs.partition import (
    ParsedPartition,
    PartitionGranularity,
    PartitionValue,
    partition_datetime,
    parse_partition_path,
)
from datacoolie_studio.domains.logs.planner import (
    PlannerState,
    StreamDefinition,
    StreamPlan,
    plan_stream_sync,
)
from datacoolie_studio.domains.logs.reader import (
    parse_system_log_file_metadata,
)
from datacoolie_studio.domains.logs.source_config import resolve_log_source_paths
from datacoolie_studio.domains.sources import service as source_validation
from datacoolie_studio.domains.storage.uri import (
    StorageProviderNotEnabled,
    join_uri,
    require_local_path,
    uri_basename,
)
from datacoolie_studio.domains.storage.adapters import (
    StorageRevision,
    StorageAdapter,
)
from datacoolie_studio.domains.storage.concurrency import map_storage_io
from datacoolie_studio.domains.sources.storage_binding import binding_from_source
from datacoolie_studio.domains.storage.factory import create_storage_adapter
from datacoolie_studio.domains.storage.inventory import storage_diagnostics
from datacoolie_studio.domains.credentials.store import (
    CredentialSecretStore,
    KeyringCredentialSecretStore,
)
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


def _log_stream_root(etl_uri: str, stream_name: str) -> str:
    if uri_basename(etl_uri) == stream_name:
        return etl_uri
    return join_uri(etl_uri, stream_name)


def _stream_definitions(
    etl_uri: str,
    system_uri: str | None,
) -> tuple[StreamDefinition, ...]:
    definitions = [
        StreamDefinition(
            stream_kind="dataflow_parquet",
            root_uri=_log_stream_root(etl_uri, "dataflow_run_log"),
            suffix=".parquet",
        ),
        StreamDefinition(
            stream_kind="job_jsonl",
            root_uri=_log_stream_root(etl_uri, "job_run_log"),
            suffix=".jsonl",
        ),
    ]
    if system_uri:
        definitions.append(
            StreamDefinition(
                stream_kind="system_jsonl",
                root_uri=system_uri,
                suffix=".jsonl",
                name_prefix="system_log_",
                manifest_only=True,
            )
        )
    return tuple(definitions)


def _planner_state_from_row(row: LogStreamState | None) -> PlannerState | None:
    if row is None:
        return None
    granularity = (
        PartitionGranularity(row.partition_granularity)
        if row.partition_granularity
        else None
    )
    return PlannerState(
        stream_kind=row.stream_kind,
        root_uri=row.root_uri,
        layout_status=row.layout_status,  # type: ignore[arg-type]
        partition_format=row.partition_format,
        partition_granularity=granularity,
        checkpoint_partition_value=_stored_partition_key(
            row.checkpoint_partition_key,
            row.checkpoint_partition_value,
            granularity,
        ),
        boundary_last_modified=_as_aware_utc(row.boundary_last_modified),
        last_scanned_partition_value=_stored_partition_key(
            row.last_scanned_partition_key,
            row.last_scanned_partition_value,
            granularity,
        ),
    )


def _stream_state_update(plan: StreamPlan) -> StreamStateUpdate:
    state = plan.state
    return StreamStateUpdate(
        stream_kind=state.stream_kind,
        root_uri=state.root_uri,
        layout_status=state.layout_status,
        partition_format=state.partition_format,
        partition_granularity=(
            state.partition_granularity.value
            if state.partition_granularity is not None
            else None
        ),
        checkpoint_partition_value=state.checkpoint_partition_value,
        boundary_last_modified=state.boundary_last_modified,
        last_scanned_partition_value=state.last_scanned_partition_value,
    )


def _merge_rebuild_candidates(
    adapter: StorageAdapter,
    planned: list[tuple[DiscoveredLogFile, str]],
    manifest_rows: list[LogFileManifest],
    states: dict[str, LogStreamState],
) -> list[tuple[DiscoveredLogFile, str]]:
    by_identity = {
        (file_kind, candidate.canonical_uri): (candidate, file_kind)
        for candidate, file_kind in planned
    }
    for row in manifest_rows:
        if row.file_kind not in {"dataflow_parquet", "job_jsonl"}:
            continue
        identity = (row.file_kind, row.file_uri)
        if identity in by_identity:
            # The planner observed this object during the current sync. Keep that
            # fresh revision instead of replacing it with a stale manifest value.
            continue
        revision = _file_revision_from_json(
            row.file_uri,
            row.revision_json,
        )
        if revision is None:
            revision = adapter.stat(row.file_uri)
        state = states.get(row.file_kind)
        granularity = (
            PartitionGranularity(state.partition_granularity)
            if state is not None and state.partition_granularity
            else PartitionGranularity.DAY
        )
        partition_value = _stored_partition_key(
            getattr(row, "partition_key", None),
            row.partition_value or row.run_date,
            granularity,
        ) or partition_datetime(
            revision.last_modified
        )
        partition_format = (
            row.partition_format
            or (state.partition_format if state is not None else None)
            or ""
        )
        by_identity[identity] = (
            DiscoveredLogFile(
                partition=ParsedPartition(
                    partition_value=partition_value,
                    raw_partition_path=(
                        partition_value.strftime(partition_format)
                        if partition_format
                        else ""
                    ),
                    partition_granularity=granularity,
                    partition_format=partition_format,
                ),
                revision=revision,
            ),
            row.file_kind,
        )
    return sorted(
        by_identity.values(),
        key=lambda item: (
            item[0].partition.partition_value,
            item[1],
            item[0].canonical_uri,
        ),
    )


def _as_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stored_partition_key(
    key: str | None,
    fallback: date | None,
    granularity: PartitionGranularity | None,
) -> PartitionValue | None:
    if key:
        try:
            parsed = datetime.fromisoformat(key)
            if granularity is PartitionGranularity.HOUR:
                return partition_datetime(parsed)
            return fallback or parsed.date()
        except ValueError:
            pass
    return fallback


def _partition_date(value: PartitionValue) -> date:
    return value.date() if isinstance(value, datetime) else value


def _partition_key_text(value: PartitionValue) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return value.isoformat()


def _plan_stream_sync(
    adapter: StorageAdapter,
    root_uri: str,
    *,
    suffix: str,
    checkpoint: LogStreamCheckpoint | None,
    spec: LogSyncSpec,
    manifest: dict[str, StorageRevision],
) -> tuple[list[DiscoveredLogFile], list[DiscoveredLogFile], int]:
    state = None
    if checkpoint is not None:
        parsed = parse_partition_path(
            checkpoint.partition_value.strftime(checkpoint.partition_format),
            expected_format=checkpoint.partition_format,
        )
        granularity = (
            parsed.partition_granularity
            if parsed is not None
            else PartitionGranularity.DAY
        )
        state = PlannerState(
            stream_kind="compatibility",
            root_uri=root_uri,
            layout_status="learned",
            partition_format=checkpoint.partition_format,
            partition_granularity=granularity,
            checkpoint_partition_value=checkpoint.partition_value,
            boundary_last_modified=checkpoint.boundary_last_modified,
            last_scanned_partition_value=checkpoint.partition_value,
        )
    plan = plan_stream_sync(
        adapter,
        StreamDefinition(
            stream_kind="compatibility",
            root_uri=root_uri,
            suffix=suffix,
        ),
        state=state,
        manifest=manifest,
        spec=spec,
    )
    return list(plan.files), list(plan.candidates), plan.scanned_partition_count


def _revision_json(revision: StorageRevision) -> str:
    provider_revision = revision.provider_revision
    local_mtime_token = (
        provider_revision.split(":", 1)[0] if provider_revision else ""
    )
    mtime_ns = (
        int(local_mtime_token)
        if local_mtime_token.isdigit()
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


def _file_revision_from_json(file_uri: str, revision_json: str) -> StorageRevision | None:
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
    return StorageRevision(
        canonical_uri=file_uri,
        size=int(payload.get("size") or 0),
        last_modified=last_modified,
        provider_revision=str(payload.get("provider_revision") or payload.get("mtime_ns") or "") or None,
    )


def _ingest_file_state(candidate: DiscoveredLogFile, file_kind: str) -> dict[str, Any]:
    partition_key = candidate.partition.partition_value
    return {
        "file_uri": candidate.canonical_uri,
        "file_kind": file_kind,
        "partition_value": _partition_date(partition_key),
        "partition_key": _partition_key_text(partition_key),
        "partition_format": candidate.partition.partition_format,
        "revision_json": _revision_json(candidate.revision),
        "job_id": None,
        "log_timestamp": None,
        "run_date": _partition_date(partition_key),
    }


def refresh_log_source_cache(
    session: Session,
    source: EnvironmentSource,
    *,
    job_type: str = "force_refresh",
    sync_spec: LogSyncSpec | None = None,
    secret_store: CredentialSecretStore | None = None,
    database_path_override: Path | None = None,
    force_analytics_replay: bool = False,
) -> dict[str, Any]:
    operation_started = time.perf_counter()
    try:
        return _refresh_log_source_cache_impl(
            session,
            source,
            job_type=job_type,
            sync_spec=sync_spec,
            secret_store=secret_store,
            database_path_override=database_path_override,
            force_analytics_replay=force_analytics_replay,
            operation_started=operation_started,
        )
    except sync.SyncJobOverlapError:
        return sync.source_sync_status(session, source)
    except Exception as exc:
        checked_at = utc_now()
        error = {
            "message": "Log storage refresh failed",
            "code": getattr(exc, "code", "log_storage_error"),
        }
        job = session.scalar(
            select(SyncJob)
            .where(
                SyncJob.source_id == source.id,
                SyncJob.status == "running",
            )
            .order_by(SyncJob.id.desc())
        )
        sync.record_source_observation(
            session,
            source=source,
            status="error",
            revision=None,
            error=error,
            checked_at=checked_at,
        )
        if job is not None:
            completed_at = utc_now()
            sync.finish_sync_job(
                session,
                job,
                status="failed",
                message=error["message"],
                result={
                    "status": "error",
                    "message": error["message"],
                    "revision": None,
                    "error": error,
                    "timings_ms": {
                        "total": _elapsed_ms(operation_started),
                    },
                },
                completed_at=completed_at,
            )
        _clear_log_staging(source.id)
        invalidate_pending_changes(source.id)
        return sync.source_sync_status(session, source, job)


def _refresh_log_source_cache_impl(
    session: Session,
    source: EnvironmentSource,
    *,
    job_type: str = "force_refresh",
    sync_spec: LogSyncSpec | None = None,
    secret_store: CredentialSecretStore | None = None,
    database_path_override: Path | None = None,
    force_analytics_replay: bool = False,
    operation_started: float | None = None,
) -> dict[str, Any]:
    if source.source_kind != "logs":
        raise ValueError("Source is not a log source")

    if database_path_override is None:
        # An analytics upgrade owns the live cache while it rebuilds. Skip external
        # syncs cleanly (no failed job) instead of colliding; the upgrade already
        # replays every source, and normal sync resumes once it finishes.
        from datacoolie_studio.domains.analytics_upgrade.service import (
            analytics_upgrade_is_building,
        )

        if analytics_upgrade_is_building(session):
            return sync.source_sync_status(session, source)

    started = operation_started or time.perf_counter()
    timings_ms: dict[str, float] = {}
    job = sync.begin_sync_job(session, source, job_type)
    checked_at = utc_now()
    phase_started = time.perf_counter()
    try:
        adapter = create_storage_adapter(
            binding_from_source(source),
            uri=source.uri,
            session=session,
            secret_store=secret_store or KeyringCredentialSecretStore(),
        )
        revision = {
            "provider": source.storage_provider,
            "uri": source.uri,
            "path": source.uri,
            "exists": True,
            "source_kind": source.source_kind,
            "object_type": "directory",
            "file_count": None,
            "total_size": None,
            "max_mtime_ns": None,
        }
        error = None
    except Exception as exc:
        revision = {
            "provider": source.storage_provider,
            "uri": source.uri,
            "exists": False,
            "source_kind": source.source_kind,
            "object_type": "provider_error",
        }
        error = {
            "message": "Log storage is not accessible",
            "code": getattr(exc, "code", "storage_access_failed"),
        }
    timings_ms["adapter_init"] = _elapsed_ms(phase_started)
    if error:
        completed_at = utc_now()
        timings_ms["total"] = _elapsed_ms(started)
        sync.record_source_observation(session, source=source, status="error", revision=None, error=error, checked_at=checked_at)
        sync.finish_sync_job(
            session,
            job,
            status="failed",
            message=error["message"],
            result={
                "status": "error",
                "message": error["message"],
                "revision": None,
                "error": error,
                "timings_ms": timings_ms,
            },
            completed_at=completed_at,
        )
        return sync.source_sync_status(session, source, job)

    phase_started = time.perf_counter()
    spec = sync_spec or LogSyncSpec()
    log_paths = resolve_log_source_paths(source)
    etl_uri = log_paths.etl_logs_uri or source.uri
    system_uri = log_paths.system_logs_uri
    persisted_states = log_control.stream_states(session, source.id)
    persisted_manifest_rows = log_control.manifest_rows(session, source.id)
    manifest_json = {
        row.file_uri: row.revision_json
        for row in persisted_manifest_rows
    }
    manifests_by_kind: dict[str, dict[str, StorageRevision]] = {}
    for row in persisted_manifest_rows:
        parsed_revision = _file_revision_from_json(
            row.file_uri,
            row.revision_json,
        )
        if parsed_revision is not None:
            manifests_by_kind.setdefault(row.file_kind, {})[
                row.file_uri
            ] = parsed_revision
    definitions = _stream_definitions(etl_uri, system_uri)

    def plan_stream(definition: StreamDefinition) -> StreamPlan:
        return plan_stream_sync(
            adapter,
            definition,
            state=_planner_state_from_row(
                persisted_states.get(definition.stream_kind)
            ),
            manifest=manifests_by_kind.get(definition.stream_kind, {}),
            spec=spec,
        )

    if getattr(adapter, "provider", None) == "dbfs" and len(definitions) > 1:
        with ThreadPoolExecutor(
            max_workers=len(definitions),
            thread_name_prefix="dbfs-log-plan",
        ) as executor:
            stream_plans = list(executor.map(plan_stream, definitions))
    else:
        stream_plans = [plan_stream(definition) for definition in definitions]
    plans_by_kind = {
        plan.definition.stream_kind: plan
        for plan in stream_plans
    }
    target_analytics_path = database_path_override or analytics_database_path()
    cache_has_source = (
        False
        if force_analytics_replay
        else _analytics_cache_has_source(
            source.id,
            database_path=target_analytics_path,
        )
    )
    analytic_candidates = [
        (candidate, plan.definition.stream_kind)
        for plan in stream_plans
        if not plan.definition.manifest_only
        for candidate in plan.candidates
    ]
    if not cache_has_source:
        analytic_candidates = _merge_rebuild_candidates(
            adapter,
            analytic_candidates,
            persisted_manifest_rows,
            persisted_states,
        )
    dataflow_candidates = [
        candidate
        for candidate, file_kind in analytic_candidates
        if file_kind == "dataflow_parquet"
    ]
    job_candidates = [
        candidate
        for candidate, file_kind in analytic_candidates
        if file_kind == "job_jsonl"
    ]
    system_plan = plans_by_kind.get("system_jsonl")
    system_candidates = list(system_plan.candidates) if system_plan else []
    system_files = [item.canonical_uri for item in system_candidates]
    existing = manifest_json
    changed_files = [_ingest_file_state(candidate, file_kind) for candidate, file_kind in analytic_candidates]
    system_states = [
        _manifest_file_state_from_candidate(
            candidate,
            "system_jsonl",
        )
        for candidate in system_candidates
    ]
    timings_ms["planning"] = _elapsed_ms(phase_started)
    revision = _revision_with_known_files(
        revision,
        {
            **existing,
            **{
                str(state["file_uri"]): str(state["revision_json"])
                for state in [*changed_files, *system_states]
            },
        },
    )
    changed_system_files = system_states

    errors: list[dict[str, Any]] = []
    parsed_dataflow_files: list[analytics_store.DataflowFile] = []
    parsed_job_rows: list[tuple[str, str, str, dict[str, Any]]] = []
    file_row_counts: dict[str, int] = {}
    staging_dir = _log_staging_dir(source.id)
    candidate_by_uri = {
        candidate.canonical_uri: candidate
        for candidate, _ in analytic_candidates
    }

    phase_started = time.perf_counter()

    def materialize_file(
        file_state: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        file_uri = str(file_state["file_uri"])
        candidate = candidate_by_uri[file_uri]
        read_uri = _materialized_log_uri(
            adapter, candidate, staging_dir, source.storage_provider
        )
        return file_state, read_uri

    materialized_files = map_storage_io(
        adapter,
        materialize_file,
        changed_files,
    )
    timings_ms["materialization"] = _elapsed_ms(phase_started)

    phase_started = time.perf_counter()
    for file_state, read_uri in materialized_files:
        file_uri = str(file_state["file_uri"])
        file_kind = str(file_state["file_kind"])
        revision_json = str(file_state["revision_json"])
        if read_uri != file_uri:
            file_state["staged_path"] = read_uri
        if file_kind == "dataflow_parquet":
            parsed_dataflow_files.append(
                (
                    file_uri,
                    file_kind,
                    revision_json,
                    read_uri,
                    str(file_state["partition_value"])
                    if file_state.get("partition_value") is not None
                    else None,
                )
            )
            read_errors = []
        elif file_kind == "job_jsonl":
            rows, read_errors = _read_job_file(read_uri, display_uri=file_uri)
            partition_date = file_state.get("partition_value")
            if partition_date is not None:
                for row in rows:
                    row["__run_date"] = str(partition_date)
            parsed_job_rows.extend((file_uri, file_kind, revision_json, row) for row in rows)
            file_row_counts[file_uri] = len(rows)
            file_state["row_count"] = len(rows)
        errors.extend(read_errors)
    timings_ms["parsing"] = _elapsed_ms(phase_started)
    needs_publish = not cache_has_source or bool(changed_files)
    changed_file_uris = [str(file_state["file_uri"]) for file_state in changed_files]
    phase_started = time.perf_counter()
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
            database_path=target_analytics_path,
            source_files=changed_files,
        )
    else:
        upsert_result = {
            "parsed_dataflow_records": 0,
            "file_row_counts": {},
            "errors": [],
            "published": True,
        }
    timings_ms["publish"] = _elapsed_ms(phase_started)
    file_row_counts.update(upsert_result["file_row_counts"])
    errors.extend(upsert_result["errors"])
    published = bool(upsert_result["published"])
    phase_started = time.perf_counter()
    if published:
        _upsert_manifest(
            session,
            source.id,
            [*changed_files, *changed_system_files],
            [],
            file_row_counts,
            checked_at,
        )
        log_control.upsert_stream_states(
            session,
            source.id,
            [_stream_state_update(plan) for plan in stream_plans],
            updated_at=checked_at,
        )
    if published and needs_publish:
        invalidate_environment_derived_caches(session, source.environment_id, structural=False)
    timings_ms["control_commit"] = _elapsed_ms(phase_started)

    status = "error" if errors else "ok"
    message = "Log source cache refreshed" if changed_files or changed_system_files else "Log source cache is current"
    if errors:
        message = "Log source analytics were not published; the previous cache was preserved"
    error = errors[0] if errors else None
    sync.record_source_observation(
        session,
        source=source,
        status=status,
        revision=revision,
        error=error,
        checked_at=checked_at,
    )
    completed_at = utc_now()
    timings_ms["total"] = _elapsed_ms(started)
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
                    1
                    for state in changed_files
                    if str(state["file_uri"]) in manifest_json
                ),
                "new_files": sum(
                    1
                    for state in changed_files
                    if str(state["file_uri"]) not in manifest_json
                ),
                "removed_files": 0,
                "scanned_partitions": sum(
                    plan.scanned_partition_count for plan in stream_plans
                ),
            },
            "sync_mode": spec.mode.value,
            "errors": errors,
            "timings_ms": timings_ms,
            "storage_io": storage_diagnostics(adapter),
        },
        completed_at=completed_at,
    )
    invalidate_pending_changes(source.id)
    from datacoolie_studio.domains.source_observation.repository import (
        reset_observation,
    )
    from datacoolie_studio.domains.studio_settings.service import (
        source_check_interval_seconds,
    )

    reset_observation(
        session,
        source.id,
        due_at=utc_now()
        + timedelta(seconds=source_check_interval_seconds(session)),
        pending_changes=False,
    )
    session.commit()
    shutil.rmtree(staging_dir, ignore_errors=True)
    return sync.source_sync_status(session, source, job)


def log_source_has_pending_changes(
    session: Session,
    source: EnvironmentSource,
    *,
    ttl_seconds: float = 0.0,
    secret_store: CredentialSecretStore | None = None,
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
    result = _compute_log_source_pending_changes(
        session, source, secret_store=secret_store
    )
    if ttl_seconds > 0:
        with _pending_changes_lock:
            _pending_changes_cache[source.id] = (time.monotonic() + ttl_seconds, result)
    return result


def _compute_log_source_pending_changes(
    session: Session,
    source: EnvironmentSource,
    *,
    secret_store: CredentialSecretStore | None = None,
) -> bool:
    persisted_states = log_control.stream_states(session, source.id)
    if not persisted_states:
        return False
    log_paths = resolve_log_source_paths(source)
    etl_uri = log_paths.etl_logs_uri or source.uri
    system_uri = log_paths.system_logs_uri
    adapter = create_storage_adapter(
        binding_from_source(source),
        uri=source.uri,
        session=session,
        secret_store=secret_store or KeyringCredentialSecretStore(),
    )
    persisted_manifest_rows = log_control.manifest_rows(session, source.id)
    manifests_by_kind: dict[str, dict[str, StorageRevision]] = {}
    for row in persisted_manifest_rows:
        parsed_revision = _file_revision_from_json(
            row.file_uri,
            row.revision_json,
        )
        if parsed_revision is not None:
            manifests_by_kind.setdefault(row.file_kind, {})[
                row.file_uri
            ] = parsed_revision
    for definition in _stream_definitions(etl_uri, system_uri):
        plan = plan_stream_sync(
            adapter,
            definition,
            state=_planner_state_from_row(
                persisted_states.get(definition.stream_kind)
            ),
            manifest=manifests_by_kind.get(definition.stream_kind, {}),
            spec=LogSyncSpec(),
        )
        if plan.candidates:
            return True
    return False


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
    log_control.upsert_manifest_rows(
        session,
        source_id,
        changed_files,
        file_row_counts,
        seen_at=checked_at,
    )


def _manifest_file_state_from_candidate(
    candidate: DiscoveredLogFile,
    file_kind: str,
) -> dict[str, Any]:
    revision = candidate.revision
    partition_key = candidate.partition.partition_value
    metadata = (
        parse_system_log_file_metadata(revision.canonical_uri)
        if file_kind == "system_jsonl"
        else {}
    )
    return {
        "file_uri": revision.canonical_uri,
        "file_kind": file_kind,
        "partition_value": _partition_date(partition_key),
        "partition_key": _partition_key_text(partition_key),
        "partition_format": candidate.partition.partition_format,
        "revision_json": _revision_json(revision),
        "job_id": metadata.get("job_id"),
        "log_timestamp": metadata.get("log_timestamp"),
        "run_date": metadata.get("run_date"),
    }


def _log_staging_dir(source_id: int) -> Path:
    root = source_materialization_cache_dir() / "logs" / f"source-{source_id}"
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="sync-", dir=root))


def _clear_log_staging(source_id: int) -> None:
    root = source_materialization_cache_dir() / "logs" / f"source-{source_id}"
    if not root.is_dir():
        return
    for child in root.iterdir():
        if child.is_dir() and child.name.startswith("sync-"):
            shutil.rmtree(child, ignore_errors=True)


def _materialized_log_uri(
    adapter: StorageAdapter,
    candidate: DiscoveredLogFile,
    staging_dir: Path,
    provider: str,
) -> str:
    if provider == "local":
        return candidate.canonical_uri
    digest = hashlib.sha256(candidate.canonical_uri.encode("utf-8")).hexdigest()[:16]
    target = staging_dir / f"{digest}-{uri_basename(candidate.canonical_uri)}"
    adapter.materialize(
        candidate.canonical_uri,
        target,
        expected_revision=candidate.revision,
    )
    return target.as_posix()


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


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _read_dataflow_file(file_uri: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    conn = duckdb.connect(database=":memory:")
    try:
        escaped = file_uri.replace("'", "''")
        result = conn.execute(
            f"SELECT * FROM read_parquet('{escaped}', "
            "union_by_name=true, hive_partitioning=false)"
        )
        names = [desc[0] for desc in result.description]
        return [_json_ready(dict(zip(names, row))) for row in result.fetchall()], []
    except Exception as exc:
        return [], [{"uri": file_uri, "message": str(exc)}]
    finally:
        conn.close()


def _read_job_file(
    file_uri: str, *, display_uri: str | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
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
                            "uri": display_uri or file_uri,
                            "message": f"Invalid JSONL at line {line_number}: {exc}",
                            "code": "invalid_jsonl",
                        }
                    )
    except OSError:
        errors.append(
            {
                "uri": display_uri or file_uri,
                "message": "Log object could not be read",
                "code": "file_read_failed",
            }
        )
    return rows, errors


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


def _analytics_cache_has_source(
    source_id: int,
    *,
    database_path: Path | None = None,
) -> bool:
    path = database_path or analytics_database_path()
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


def _revision_error(source: EnvironmentSource, revision: dict[str, Any]) -> dict[str, Any] | None:
    if revision.get("object_type") == "provider_not_enabled":
        provider = str(revision.get("provider") or "storage")
        return {"message": f"{provider.upper()} storage URI is recognized but not enabled yet: {source.uri}", "code": "provider_not_enabled"}
    if not revision.get("exists"):
        return {"message": f"ETL log path not found: {source.uri}", "code": "not_found"}
    if revision.get("object_type") != "directory":
        return {"message": f"ETL log source must be a directory: {source.uri}", "code": "invalid_type"}
    return None
