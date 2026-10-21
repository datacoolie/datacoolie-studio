from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.core.config import (
    analytics_database_path,
)
from datacoolie_studio.db.models import (
    EnvironmentSource,
    SyncJob,
    utc_now,
)
from datacoolie_studio.domains.analytics import access as analytics_access
from datacoolie_studio.domains.analytics import cache_state as analytics_cache_state
from datacoolie_studio.domains.analytics import contracts as analytics_contracts
from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.analytics import store as analytics_store
from datacoolie_studio.domains.analytics import ownership as analytics_ownership
from datacoolie_studio.domains.logs.discovery import LogSyncSpec
from datacoolie_studio.domains.logs import control as log_control
from datacoolie_studio.domains.logs import batching
from datacoolie_studio.domains.logs import control_codec, control_state, pending
from datacoolie_studio.domains.logs import file_state as refresh_file_state
from datacoolie_studio.domains.logs import refresh_planning
from datacoolie_studio.domains.logs import staging as log_staging
from datacoolie_studio.domains.logs import source_snapshot
from datacoolie_studio.domains.logs import upgrade_staging
from datacoolie_studio.domains.logs.planner import (
    StreamDefinition,
    StreamPlan,
    plan_stream_sync,
)
from datacoolie_studio.domains.logs.source_config import resolve_log_source_paths
from datacoolie_studio.domains.sources import service as source_service
from datacoolie_studio.domains.storage.adapters import StorageRevision
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


def refresh_log_source_cache(
    session: Session,
    source: EnvironmentSource,
    *,
    job_type: str = "force_refresh",
    sync_spec: LogSyncSpec | None = None,
    secret_store: CredentialSecretStore | None = None,
    database_path_override: Path | None = None,
    force_analytics_replay: bool = False,
    defer_analytics_generation: bool = False,
    defer_control_commit: bool = False,
    resume_checkpoint: dict[str, Any] | None = None,
    checkpoint_callback: Callable[[int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    operation_started = time.perf_counter()
    try:
        def refresh() -> dict[str, Any]:
            with log_staging.staging_scope(source.id):
                return _refresh_log_source_cache_impl(
                    session,
                    source,
                    job_type=job_type,
                    sync_spec=sync_spec,
                    secret_store=secret_store,
                    database_path_override=database_path_override,
                    force_analytics_replay=force_analytics_replay,
                    defer_analytics_generation=defer_analytics_generation,
                    defer_control_commit=defer_control_commit,
                    resume_checkpoint=resume_checkpoint,
                    checkpoint_callback=checkpoint_callback,
                    operation_started=operation_started,
                )

        if database_path_override is None and job_type not in {
            "analytics_upgrade",
            "analytics_repair",
        }:
            if job_type == "auto_refresh":
                # Source initialization must not be reported as successful while a
                # complete rebuild owns the live-cache lifecycle. Wait for that
                # owner, then let the normal refresh path re-check its state.
                with analytics_access.analytics_global_rebuild_lock:
                    return refresh()
            acquired = analytics_access.analytics_global_rebuild_lock.acquire(
                blocking=False
            )
            if not acquired:
                return sync.source_sync_status(session, source)
            try:
                return refresh()
            finally:
                analytics_access.analytics_global_rebuild_lock.release()
        return refresh()
    except sync.SyncJobOverlapError:
        return sync.source_sync_status(session, source)
    except Exception as exc:
        checked_at = utc_now()
        error = {
            "message": str(exc) if getattr(exc, "code", None) in {"schema_incompatible", "invalid_json"} else "Log storage refresh failed",
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
        pending.invalidate_pending_changes(source.id)
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
    defer_analytics_generation: bool = False,
    defer_control_commit: bool = False,
    resume_checkpoint: dict[str, Any] | None = None,
    checkpoint_callback: Callable[[int, dict[str, Any]], None] | None = None,
    operation_started: float | None = None,
) -> dict[str, Any]:
    if source.source_kind != "logs":
        raise ValueError("Source is not a log source")

    if database_path_override is None:
        # An analytics upgrade owns the live cache while it rebuilds. Skip external
        # syncs cleanly (no failed job) instead of colliding; the upgrade already
        # replays every source, and normal sync resumes once it finishes.
        if analytics_ownership.cache_upgrade_is_building(session):
            return sync.source_sync_status(session, source)

    started = operation_started or time.perf_counter()
    timings_ms: dict[str, float] = {}
    staging_metrics = {
        "downloads": 0,
        "downloaded_bytes": 0,
        "staged_file_count": 0,
        "peak_staged_bytes": 0,
        "cleanup_failures": 0,
    }
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
        if getattr(adapter, "provider", None) == "local":
            revision = source_snapshot.source_revision(source)
            error = _revision_error(source, revision)
        else:
            # Cloud adapters can authenticate and list a prefix without exposing
            # a cheap, provider-neutral directory stat. Their planner remains the
            # source of truth for an empty prefix.
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
    resolved_paths = resolve_log_source_paths(source)
    execution_uri = resolved_paths.execution_logs_uri or source.uri
    system_uri = resolved_paths.system_logs_uri
    persisted_states = log_control.stream_states(session, source.id)
    persisted_manifest_rows = log_control.manifest_rows(session, source.id)
    manifest_json = {
        row.file_uri: row.revision_json
        for row in persisted_manifest_rows
    }
    manifests_by_kind: dict[str, dict[str, StorageRevision]] = {}
    for row in persisted_manifest_rows:
        parsed_revision = refresh_file_state.file_revision_from_json(
            row.file_uri,
            row.revision_json,
        )
        if parsed_revision is not None:
            manifests_by_kind.setdefault(row.file_kind, {})[
                row.file_uri
            ] = parsed_revision
    definitions = refresh_planning.stream_definitions(execution_uri, system_uri)

    def plan_stream(definition: StreamDefinition) -> StreamPlan:
        return plan_stream_sync(
            adapter,
            definition,
              # A complete analytics replay must enumerate the current source
            # inventory, not just the incremental/lookback window. This also
            # prevents stale operational manifest entries from reintroducing an
            # old layout into the no-backward-compatibility replay.
            state=(
                None
                if force_analytics_replay
                else refresh_planning.planner_state_from_row(
                    persisted_states.get(definition.stream_kind)
                )
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
    removed_file_uris = sorted({uri for plan in stream_plans for uri in plan.removed_files})
    removed_analytics_uris = sorted({uri for plan in stream_plans if not plan.definition.manifest_only for uri in plan.removed_files})
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
        for candidate in (
            plan.files if force_analytics_replay else plan.candidates
        )
    ]
    if not cache_has_source and not force_analytics_replay:
        analytic_candidates = refresh_file_state.merge_rebuild_candidates(
            adapter,
            analytic_candidates,
            [row for row in persisted_manifest_rows if row.file_uri not in removed_file_uris],
            persisted_states,
        )
    dataflow_candidates = [
        candidate
        for candidate, file_kind in analytic_candidates
        if file_kind == "dataflow_json"
    ]
    job_candidates = [
        candidate
        for candidate, file_kind in analytic_candidates
        if file_kind == "job_json"
    ]
    system_plan = plans_by_kind.get("system_json")
    # A full analytics replay also replaces the operational Log control state.
    # Re-index every current system-log file in that case; using only changed
    # files would delete unchanged system-log manifest rows when the control
    # state is reset after the candidate swap.
    system_candidates = (
        list(system_plan.files if force_analytics_replay else system_plan.candidates)
        if system_plan
        else []
    )
    system_files = [item.canonical_uri for item in system_candidates]
    existing = manifest_json
    changed_files = [
        refresh_file_state.ingest_file_state(candidate, file_kind)
        for candidate, file_kind in analytic_candidates
    ]
    staging_dir = (
        log_staging.create_staging_dir(source.id)
        if analytic_candidates
        else None
    )
    candidate_by_uri = {
        candidate.canonical_uri: candidate
        for candidate, _ in analytic_candidates
    }

    # System manifests are filename metadata only.  Admission, row counts and
    # event-time bounds remain unknown until a preview or download reads them.
    # In particular, this path must not materialize, inspect, or open a system
    # object during any sync mode.
    system_states = [
        refresh_file_state.manifest_file_state_from_candidate(
            candidate,
            "system_json",
        )
        for candidate in system_candidates
    ]
    persisted_by_uri = {
        row.file_uri: row
        for row in persisted_manifest_rows
        if row.file_kind == "system_json"
    }
    for state in system_states:
        previous = persisted_by_uri.get(str(state["file_uri"]))
        if previous is None or previous.revision_json != state["revision_json"]:
            continue
        # A full/deferred replay may reset the manifest table before applying
        # this payload. Carry forward revision-bound content evidence so an
        # unchanged object does not become falsely unmeasured.
        state["row_count"] = previous.row_count
        state["job_id"] = previous.job_id
        state["log_session_id"] = previous.log_session_id
        state["log_timestamp"] = previous.log_timestamp
        state["event_min_timestamp"] = previous.event_min_timestamp
        state["event_max_timestamp"] = previous.event_max_timestamp
        state["run_date"] = previous.run_date
        if previous.admission_summary_json:
            try:
                state["admission_summary"] = json.loads(
                    previous.admission_summary_json
                )
            except (TypeError, json.JSONDecodeError):
                pass
    timings_ms["planning"] = _elapsed_ms(phase_started)
    revision = refresh_file_state.revision_with_known_files(
        revision,
        {
            **{uri: value for uri, value in existing.items() if uri not in removed_file_uris},
            **{
                str(state["file_uri"]): str(state["revision_json"])
                for state in [*changed_files, *system_states]
            },
        },
    )
    changed_system_files = system_states

    errors: list[dict[str, Any]] = []
    file_row_counts: dict[str, int] = {}

    needs_publish = not cache_has_source or bool(changed_files) or bool(removed_analytics_uris)
    changed_file_uris = [str(file_state["file_uri"]) for file_state in changed_files]

    if defer_analytics_generation:
        phase_started = time.perf_counter()
        if needs_publish:
            upsert_result = upgrade_staging.stage_upgrade_log_batches(
                adapter,
                source.id,
                target_analytics_path,
                changed_files,
                candidate_by_uri=candidate_by_uri,
                staging_dir=staging_dir,
                storage_provider=source.storage_provider,
                resume_checkpoint=resume_checkpoint,
                checkpoint_callback=checkpoint_callback,
                removed_file_uris=removed_analytics_uris,
            )
        else:
            upsert_result = {
                "parsed_dataflow_records": 0,
                "parsed_job_records": 0,
                "file_row_counts": {},
                "errors": [],
                "published": True,
                "batch_count": 0,
            }
        timings_ms["batch_staging"] = _elapsed_ms(phase_started)
    else:
        phase_started = time.perf_counter()
        batch_metrics = {"materialization_ms": 0.0}

        def materialize_analytics_batches():
            for file_batch in batching.file_batches(changed_files):
                materialized_candidates = [
                    candidate_by_uri[str(file_state["file_uri"])]
                    for file_state in file_batch
                ]
                batch_bytes = batching.batch_bytes(file_batch)
                staging_metrics["staged_file_count"] += len(file_batch)
                if source.storage_provider != "local":
                    staging_metrics["downloads"] += len(file_batch)
                    staging_metrics["downloaded_bytes"] += batch_bytes
                staging_metrics["peak_staged_bytes"] = max(
                    staging_metrics["peak_staged_bytes"],
                    batch_bytes,
                )
                with batching.log_byte_budget.acquire(batch_bytes):
                    try:
                        def materialize_file(
                            file_state: dict[str, Any],
                        ) -> tuple[dict[str, Any], str]:
                            candidate = candidate_by_uri[str(file_state["file_uri"])]
                            assert staging_dir is not None
                            return file_state, log_staging.materialize_log_uri(
                                adapter,
                                candidate,
                                staging_dir,
                                source.storage_provider,
                            )

                        materialization_started = time.perf_counter()
                        materialized_files = map_storage_io(
                            adapter,
                            materialize_file,
                            file_batch,
                        )
                        batch_metrics["materialization_ms"] += _elapsed_ms(
                            materialization_started
                        )
                        dataflow_files: list[analytics_contracts.DataflowFile] = []
                        job_files: list[analytics_contracts.JobFile] = []
                        for (file_state, read_uri), candidate in zip(
                            materialized_files,
                            materialized_candidates,
                            strict=True,
                        ):
                            descriptor = analytics_contracts.LogFileDescriptor(
                                canonical_uri=str(file_state["file_uri"]),
                                file_kind=str(file_state["file_kind"]),
                                revision_json=str(file_state["revision_json"]),
                                read_uri=read_uri,
                                partition_date=(
                                    str(file_state["partition_value"])
                                    if file_state.get("partition_value") is not None
                                    else None
                                ),
                            )
                            if descriptor.file_kind == "dataflow_json":
                                dataflow_files.append(descriptor)
                            elif descriptor.file_kind == "job_json":
                                job_files.append(descriptor)
                        yield analytics_contracts.LogPublishBatch(
                            dataflow_files=tuple(dataflow_files),
                            job_files=tuple(job_files),
                            source_files=tuple(file_batch),
                        )
                    finally:
                        for candidate in materialized_candidates:
                            assert staging_dir is not None
                            staging_metrics["cleanup_failures"] += log_staging.cleanup_materialized_log(
                                candidate,
                                staging_dir,
                                source.storage_provider,
                            )

        if needs_publish:
            phase_started = time.perf_counter()
            upsert_result = analytics_store.publish_rows_batched(
                source.id,
                materialize_analytics_batches(),
                removed_analytics_uris,
                changed_file_uris,
                database_path=target_analytics_path,
                finalize_generation=True,
            )
            timings_ms["batch_staging"] = _elapsed_ms(phase_started)
            timings_ms["materialization"] = batch_metrics["materialization_ms"]
            timings_ms["publish"] = float(
                upsert_result.get("publication_duration_ms", 0.0)
            )
            timings_ms["parsing"] = max(
                0.0,
                timings_ms["batch_staging"]
                - timings_ms["materialization"]
                - timings_ms["publish"],
            )
        else:
            upsert_result = {
                "parsed_dataflow_records": 0,
                "parsed_job_records": 0,
                "file_row_counts": {},
                "errors": [],
                "published": True,
                "batch_count": 0,
                "publication_duration_ms": 0.0,
            }
            timings_ms["batch_staging"] = 0.0
            timings_ms["materialization"] = 0.0
            timings_ms["publish"] = 0.0
            timings_ms["parsing"] = 0.0

    file_row_counts.update(upsert_result["file_row_counts"])
    admission_summaries = upsert_result.get("file_admission_summaries", {})
    for state in changed_files:
        uri = str(state["file_uri"])
        if uri in admission_summaries:
            state["admission_summary"] = admission_summaries[uri]
    errors.extend(upsert_result["errors"])
    published = bool(upsert_result["published"])
    deferred_control = (
        control_codec.deferred_control_payload(
            changed_files,
            changed_system_files,
            stream_plans,
            revision,
            file_row_counts,
            needs_publish=needs_publish,
            replace_source_control=force_analytics_replay,
            removed_files=removed_file_uris,
        )
        if published and defer_control_commit
        else None
    )
    phase_started = time.perf_counter()
    if published and not defer_control_commit:
        control_state.upsert_manifest(
            session,
            source.id,
            [*changed_files, *changed_system_files],
            removed_file_uris,
            file_row_counts,
            checked_at,
        )
        log_control.upsert_stream_states(
            session,
            source.id,
            [refresh_planning.stream_state_update(plan) for plan in stream_plans],
            updated_at=checked_at,
        )
    if published and (needs_publish or removed_file_uris) and not defer_control_commit:
        invalidate_environment_derived_caches(source.environment_id, structural=False)
    timings_ms["control_commit"] = _elapsed_ms(phase_started)

    status = "error" if errors else "ok"
    message = "Log source cache refreshed" if changed_files or changed_system_files or removed_file_uris else "Log source cache is current"
    if errors:
        message = "Log source analytics were not published; the previous cache was preserved"
    error = errors[0] if errors else None
    if not (published and defer_control_commit):
        sync.record_source_observation(
            session,
            source=source,
            status=status,
            revision=revision,
            error=error,
            checked_at=checked_at,
        )
        if published and not errors:
            source_service.update_empty_log_validation_after_sync(
                source,
                discovered_file_count=len(changed_files) + len(changed_system_files),
                checked_at=checked_at,
            )
    completed_at = utc_now()
    timings_ms["total"] = _elapsed_ms(started)
    storage_io = storage_diagnostics(adapter)
    storage_io.update(staging_metrics)
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
                "parsed_job_records": upsert_result.get(
                    "parsed_job_records", 0
                ),
                "dataflow_json_files": len(dataflow_candidates),
                "job_json_files": len(job_candidates),
                "system_json_files": len(system_files),
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
                "removed_files": len(removed_file_uris),
                "scanned_partitions": sum(
                    plan.scanned_partition_count for plan in stream_plans
                ),
                "staged_batches": int(upsert_result.get("batch_count") or 0),
                "selected_partitions": {
                    plan.definition.stream_kind: [
                        _partition_value_text(value)
                        for value in (
                            *plan.incremental_partition_values,
                            *plan.lookback_partition_values,
                        )
                    ]
                    for plan in stream_plans
                },
            },
            "discovery_io": {
                plan.definition.stream_kind: {
                    "inventory_requests": plan.inventory_requests,
                    "inventory_pages": plan.inventory_pages,
                    "objects_inspected": plan.objects_inspected,
                    "stat_requests": plan.stat_requests,
                }
                for plan in stream_plans
            },
            "file_admission_summaries": admission_summaries,
            "sync_mode": spec.mode.value,
            "errors": errors,
            "timings_ms": timings_ms,
            "storage_io": storage_io,
        },
        completed_at=completed_at,
    )
    if deferred_control is not None:
        job_result = json.loads(job.result_json or "{}")
        job_result["deferred_control"] = deferred_control
        job.result_json = json.dumps(job_result, sort_keys=True)
        session.commit()
    else:
        pending.invalidate_pending_changes(source.id)
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
    return sync.source_sync_status(session, source, job)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _partition_value_text(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)






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
        meta = analytics_cache_state.analytics_meta(conn)
        return bool(
            meta
            and meta["schema_version"] == analytics_schema.ANALYTICS_SCHEMA_VERSION
            and meta["build_state"] == "ready"
            and source_id in analytics_cache_state.cache_source_ids(conn)
        )
    finally:
        conn.close()


def _revision_error(source: EnvironmentSource, revision: dict[str, Any]) -> dict[str, Any] | None:
    if revision.get("object_type") == "provider_not_enabled":
        provider = str(revision.get("provider") or "storage")
        return {"message": f"{provider.upper()} storage URI is recognized but not enabled yet: {source.uri}", "code": "provider_not_enabled"}
    if not revision.get("exists"):
        return {"message": f"Execution/system log base path not found: {source.uri}", "code": "not_found"}
    if revision.get("object_type") != "directory":
        return {"message": f"Execution/system log source must be a directory: {source.uri}", "code": "invalid_type"}
    return None
