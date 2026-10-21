"""Coordinate analytics-cache upgrades without owning collaborator policy."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from datacoolie_studio.core.config import analytics_database_path
from datacoolie_studio.db.models import AnalyticsUpgrade, utc_now
from datacoolie_studio.db.session import create_session
from datacoolie_studio.domains.analytics import (
    access,
    cache_state,
    candidate_state,
    publication,
    schema_migration,
)
from datacoolie_studio.domains.analytics.migrations import validate_registry
from datacoolie_studio.domains.analytics_upgrade.assessment import (
    assess_cache,
    cache_is_current,
    eligible_sources,
    external_sync_in_progress,
    park_unbuildable_source,
    resumable_candidate_sources,
    source_context_map,
    source_snapshot_map,
)
from datacoolie_studio.domains.analytics_upgrade.constants import (
    NON_PARKING_SOURCE_CODES,
    UPGRADE_ID,
)
from datacoolie_studio.domains.analytics_upgrade.errors import (
    AnalyticsUpgradeScopeChangedError,
    AnalyticsUpgradeSourceError,
)
from datacoolie_studio.domains.analytics_upgrade.recovery import (
    analytics_swap_completed,
    deferred_control_for_source,
    mark_control_recovery_pending,
    recover_deferred_log_control,
    source_replay_has_warning,
    source_replay_succeeded,
)
from datacoolie_studio.domains.analytics_upgrade.source_repair import run_source_repair_once
from datacoolie_studio.domains.analytics_upgrade.source_replay import build_sources_parallel
from datacoolie_studio.domains.analytics_upgrade.state import (
    as_utc,
    begin_attempt,
    defer_upgrade,
    get_or_create_upgrade,
    json_checkpoints,
    json_source_snapshot,
    mark_completed_with_warnings,
    mark_current_state_succeeded,
    mark_failed,
    mark_source_completed,
    mark_succeeded,
)
from datacoolie_studio.domains.analytics_upgrade.status import (
    current_upgrade_status,
    upgrade_status,
)
from datacoolie_studio.domains.logs.control_state import apply_deferred_log_control
from datacoolie_studio.domains.logs.pending import log_source_has_pending_changes


logger = logging.getLogger(__name__)


def _migration_source_ids(live_path: Path, _fallback: list[int]) -> list[int]:
    """Use cached coverage for SQL-only migration, including paused sources."""

    cached = list(assessment_cached_source_ids(live_path))
    # A valid migration cache has an explicit coverage table. An empty table is
    # meaningful, so do not replace it with current registrations and request
    # replay for rows that were never cached.
    return cached


def assessment_cached_source_ids(live_path: Path) -> tuple[int, ...]:
    """Read cache coverage without consulting source registrations/storage."""

    try:
        conn = access.connect(live_path, read_only=True)
    except Exception:  # noqa: BLE001 - assessment falls back to registration scope
        return ()
    try:
        return tuple(sorted(cache_state.cache_source_ids(conn)))
    finally:
        conn.close()


def _record_unsupported_migration(
    session,
    *,
    assessment,
    source_ids: list[int],
    source_context: dict[str, dict[str, Any]],
    checked_at: datetime,
) -> dict[str, Any]:
    """Persist an explicit blocked upgrade without scheduling source replay."""

    upgrade = get_or_create_upgrade(
        session,
        source_version=assessment.source_version,
        source_ids=source_ids,
        source_context=source_context,
        operation_kind="unsupported",
        reason_code=assessment.reason_code,
    )
    upgrade.state = "failed"
    upgrade.error_code = "unsupported_schema_change"
    upgrade.error_message = (
        assessment.migration_reason or assessment.reason_code
    )[:500]
    upgrade.next_retry_at = None
    upgrade.updated_at = checked_at
    session.commit()
    return upgrade_status(upgrade, session)


def _run_sql_migration_once(
    *,
    session,
    upgrade: AnalyticsUpgrade,
    assessment,
    source_ids: list[int],
    live_path: Path,
    checked_at: datetime,
    publication_module,
) -> dict[str, Any]:
    """Run a candidate-only migration and complete without deferred controls."""

    access.analytics_global_rebuild_lock.acquire()
    candidate = access.candidate_path(live_path)
    try:
        # Hold the same in-process writer fence used by direct cache stores for
        # the complete checkpoint/candidate/swap lifecycle.  The global rebuild
        # lock serialises upgrade coordinators; the maintenance lock prevents a
        # direct ingestion writer from changing the bytes after our snapshot.
        with access.analytics_maintenance_lock:
            source_ids = _migration_source_ids(live_path, source_ids)
            begin_attempt(
                upgrade,
                source_ids,
                candidate,
                checked_at,
                source_snapshot={},
            )
            session.commit()

            # Copy and rewrite only the fenced DuckDB cache.  No replay worker or
            # storage provider is reachable from this path.
            schema_migration.migrate_database_copy(
                live_path,
                candidate,
                source_version=assessment.source_version,
            )
            upgrade.state = "finalizing"
            upgrade.updated_at = utc_now()
            session.commit()
            publication_module.finalize_candidate(candidate, source_ids)

            upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID) or upgrade
            upgrade.state = "validating"
            upgrade.updated_at = utc_now()
            session.commit()
            publication_module.validate_analytics_candidate(candidate, source_ids)

            upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID) or upgrade
            upgrade.state = "publishing"
            upgrade.updated_at = utc_now()
            session.commit()

            def record_rollback(rollback):
                checkpoints = json.loads(upgrade.batch_checkpoint_json or "{}")
                checkpoints["rollback"] = rollback
                upgrade.batch_checkpoint_json = json.dumps(checkpoints, default=str)
                session.commit()

            access.swap_candidate(
                candidate,
                live_path,
                on_rollback_retained=record_rollback,
            )
            completed_at = utc_now()
            # SQL-only operations intentionally skip apply_control.  A migration
            # does not discover files, advance watermarks or manufacture controls.
            mark_succeeded(upgrade, completed_at)
            session.commit()
            return upgrade_status(upgrade, session)
    except Exception as exc:
        session.rollback()
        upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID)
        if upgrade is not None:
            if analytics_swap_completed(live_path, upgrade):
                mark_control_recovery_pending(upgrade, exc, checked_at)
            else:
                mark_failed(upgrade, exc, checked_at)
                if isinstance(exc, schema_migration.SchemaMigrationError):
                    upgrade.error_code = exc.reason_code
            session.commit()
            return upgrade_status(upgrade, session)
        return {"state": "failed", "error_code": getattr(exc, "code", "analytics_schema_migration_failed")}
    finally:
        access.analytics_global_rebuild_lock.release()


def run_upgrade_once(
    *,
    now: datetime | None = None,
    database_path_factory=analytics_database_path,
    build_sources_parallel_fn=build_sources_parallel,
    publication_module=publication,
    apply_control=apply_deferred_log_control,
) -> dict[str, Any]:
    """Maintain the analytics cache using the smallest safe operation."""

    validate_registry()
    checked_at = now or utc_now()
    session = create_session()
    candidate: Path | None = None
    live_path = database_path_factory()
    global_rebuild_lock_acquired = False
    try:
        sources = eligible_sources(session)
        source_ids = [source.id for source in sources]
        existing_upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID)
        if analytics_swap_completed(live_path, existing_upgrade):
            recovered = recover_deferred_log_control(
                session,
                existing_upgrade,
                checked_at=checked_at,
                apply_control=apply_control,
            )
            if recovered is not None:
                return recovered
            mark_control_recovery_pending(
                existing_upgrade,
                RuntimeError(
                    "The published analytics generation has not committed its Log control state"
                ),
                checked_at,
            )
            session.commit()
            return upgrade_status(existing_upgrade, session)
        assessment = assess_cache(live_path, source_ids)
        # A cache can contain only paused/offline sources.  SQL-only migration
        # must still run for those rows, whereas ordinary source replay has no
        # eligible source scope and remains a no-op.
        if not source_ids and assessment.action not in {"schema_migration", "unsupported"}:
            mark_current_state_succeeded(session, [], checked_at)
            return {"state": "not_required", "source_ids": []}
        if assessment.action == "current":
            mark_current_state_succeeded(session, source_ids, checked_at)
            return {"state": "current", "source_ids": source_ids}

        operation_source_ids = (
            _migration_source_ids(live_path, source_ids)
            if assessment.action in {"schema_migration", "unsupported"}
            else list(
                assessment.missing_source_ids
                if assessment.action == "source_repair"
                else source_ids
            )
        )
        if assessment.action == "unsupported":
            return _record_unsupported_migration(
                session,
                assessment=assessment,
                source_ids=operation_source_ids,
                source_context=source_context_map(sources),
                checked_at=checked_at,
            )
        if assessment.action != "schema_migration" and external_sync_in_progress(
            session, operation_source_ids
        ):
            # A user/scheduled sync is currently writing the cache. Defer the
            # upgrade to the next cycle so the two writers never collide.
            return current_upgrade_status(session) or {
                "state": "pending",
                "source_ids": operation_source_ids,
            }

        upgrade = get_or_create_upgrade(
            session,
            source_version=assessment.source_version,
            source_ids=operation_source_ids,
            source_context=source_context_map(sources),
            operation_kind=assessment.operation_kind,
            reason_code=assessment.reason_code,
        )
        if (
            upgrade.state == "failed"
            and upgrade.next_retry_at is not None
            and as_utc(upgrade.next_retry_at) > checked_at
        ):
            return upgrade_status(upgrade, session)

        if assessment.action == "schema_migration":
            return _run_sql_migration_once(
                session=session,
                upgrade=upgrade,
                assessment=assessment,
                source_ids=operation_source_ids,
                live_path=live_path,
                checked_at=checked_at,
                publication_module=publication_module,
            )

        if assessment.action == "source_repair":
            return run_source_repair_once(
                session,
                upgrade,
                source_ids=list(assessment.missing_source_ids),
                live_path=live_path,
                checked_at=checked_at,
                build_sources_parallel_fn=build_sources_parallel_fn,
            )

        # Keep the complete rebuild claim across assessment, candidate writes and
        # atomic swap. Normal source refreshes use this lock as well; source repair
        # intentionally does not, so it remains source-scoped.
        access.analytics_global_rebuild_lock.acquire()
        global_rebuild_lock_acquired = True
        candidate = access.candidate_path(live_path)
        current_snapshot = source_snapshot_map(sources)
        previous_snapshot = json_source_snapshot(upgrade.source_snapshot_json)
        previous_checkpoints = json_checkpoints(upgrade.batch_checkpoint_json)
        begin_attempt(
            upgrade,
            source_ids,
            candidate,
            checked_at,
            source_snapshot=current_snapshot,
        )
        session.commit()

        if cache_is_current(live_path, source_ids):
            if candidate.exists():
                access.discard_candidate(candidate)
            mark_succeeded(upgrade, checked_at)
            session.commit()
            return upgrade_status(upgrade, session)

        # Resume a compatible partial candidate from a previous attempt instead of
        # rebuilding every already-completed source; only discard an incompatible one.
        already_built = resumable_candidate_sources(
            candidate,
            source_ids,
            previous_snapshot=previous_snapshot,
            current_snapshot=current_snapshot,
            checkpoints=previous_checkpoints,
        )
        if candidate.exists():
            out_of_scope_source_ids = cache_state.candidate_source_ids(candidate) - set(source_ids)
            for source_id in sorted(out_of_scope_source_ids):
                candidate_state.clear_candidate_source(candidate, source_id)
        for source in sources:
            if source.id not in already_built:
                continue
            try:
                source_changed = log_source_has_pending_changes(
                    session,
                    source,
                    ttl_seconds=0,
                )
            except Exception:
                # If the source cannot be inspected, do not trust a source-level
                # completion marker. The batch replay will surface the precise
                # materialization error and keep the live cache untouched.
                source_changed = True
            if source_changed:
                already_built.discard(source.id)
        completed_ids: list[int] = sorted(already_built)
        for source_id in completed_ids:
            mark_source_completed(upgrade, source_id)
        if completed_ids:
            session.commit()

        from datacoolie_studio.domains.logs.batching import materialization_fingerprint
        resume_checkpoints = {
            source_key: checkpoint
            for source_key, checkpoint in previous_checkpoints.items()
            if (
                source_key in current_snapshot
                and checkpoint.get("config_fingerprint")
                == current_snapshot[source_key].get("config_fingerprint")
                and checkpoint.get("materialization_fingerprint") == materialization_fingerprint()
            )
        } if candidate.exists() else {}
        upgrade.batch_checkpoint_json = json.dumps(
            resume_checkpoints,
            sort_keys=True,
        )
        session.commit()
        for source_id in source_ids:
            if source_id in already_built:
                continue
            if str(source_id) not in resume_checkpoints:
                candidate_state.clear_candidate_source(candidate, source_id)

        # Build the remaining sources concurrently. Each source stages base rows into the
        # candidate; global filters, serving facts, and generation publication happen once
        # below after the complete source set is present.
        pending = [source.id for source in sources if source.id not in already_built]
        build_results = build_sources_parallel_fn(
            pending,
            candidate,
            resume_checkpoints=resume_checkpoints,
        )

        # Worker checkpoint callbacks use independent SQLAlchemy sessions. Refresh the
        # coordinator object before it writes source completion so its commit cannot
        # overwrite a newer batch checkpoint with the pre-worker JSON value.
        session.expire(upgrade)
        upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID) or upgrade

        busy_error: AnalyticsUpgradeSourceError | None = None
        hard_error: AnalyticsUpgradeSourceError | None = None
        warning_source_ids: list[int] = []
        for source_id in sorted(build_results):
            result = build_results[source_id]
            status = result.get("status")
            if source_replay_succeeded(result):
                mark_source_completed(upgrade, source_id, result=result)
                completed_ids.append(source_id)
                if source_replay_has_warning(result):
                    warning_source_ids.append(source_id)
                session.commit()
                continue
            execution_result = (result.get("sync_execution") or {}).get("result") or {}
            error = result.get("error") or execution_result.get("error") or next(iter(execution_result.get("errors") or []), {})
            code = str(
                error.get("code")
                or (
                    "source_sync_busy"
                    if status == "running"
                    else "source_replay_failed"
                )
            )
            message = str(
                error.get("message")
                or (
                    "Another source sync is still running"
                    if status == "running"
                    else "Log source could not be replayed"
                )
            )
            if code in NON_PARKING_SOURCE_CODES:
                source_error = AnalyticsUpgradeSourceError(
                    source_id, code=code, message=message
                )
                if code == "source_sync_busy":
                    busy_error = busy_error or source_error
                else:
                    hard_error = hard_error or source_error
                continue
            # The source cannot be materialized (missing storage/dependency). Park it
            # so it drops out of the upgrade scope and Monitoring coverage instead of
            # wedging every future upgrade, then keep building the remaining sources.
            park_unbuildable_source(session, source_id, code, message, utc_now())
            warning_source_ids.append(source_id)

        # A real dependency/environment failure aborts (retry with backoff); a transient
        # sync collision defers softly. Both are raised after parking the rest.
        if hard_error is not None:
            raise hard_error
        if busy_error is not None:
            raise busy_error

        completed_ids = sorted(set(completed_ids))
        if not completed_ids:
            # Nothing could be rebuilt; leave the live cache untouched and settle.
            access.discard_candidate(candidate)
            upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID) or upgrade
            upgrade.source_ids_json = json.dumps(source_ids)
            upgrade.completed_source_ids_json = json.dumps([])
            completed_at = utc_now()
            mark_succeeded(upgrade, completed_at)
            if warning_source_ids:
                mark_completed_with_warnings(
                    upgrade,
                    warning_source_ids=warning_source_ids,
                    completed_at=completed_at,
                )
            session.commit()
            return upgrade_status(upgrade, session)

        upgrade.state = "finalizing"
        upgrade.updated_at = utc_now()
        session.commit()
        publication_module.finalize_candidate(candidate, completed_ids)

        upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID) or upgrade
        upgrade.state = "validating"
        upgrade.updated_at = utc_now()
        session.commit()
        publication_module.validate_analytics_candidate(candidate, completed_ids)

        session.expire_all()
        current_sources = eligible_sources(session)
        current_source_ids = sorted(source.id for source in current_sources)
        expected_snapshot = {
            str(source_id): current_snapshot[str(source_id)]
            for source_id in completed_ids
            if str(source_id) in current_snapshot
        }
        if (
            current_source_ids != completed_ids
            or source_snapshot_map(current_sources) != expected_snapshot
        ):
            raise AnalyticsUpgradeScopeChangedError()
        # The replay workers deliberately defer workspace control commits until
        # after the candidate is swapped.  Checking the live manifest here would
        # therefore report every successfully replayed source as changed when it
        # has no prior stream state (the normal case for an initial upgrade).
        # Source-file arrivals remain safe: the published deferred control is
        # based on the replay snapshot, so the next observation sees a newer
        # revision.  Scope validation above is the check that belongs at this
        # boundary: the enabled source set and source configuration must still
        # match the candidate that was built.

        deferred_controls: dict[int, dict[str, Any]] = {}
        for source_id in completed_ids:
            control = deferred_control_for_source(
                session,
                source_id,
                build_results.get(source_id),
            )
            if control is None:
                raise AnalyticsUpgradeSourceError(
                    source_id,
                    code="control_state_missing",
                    message="The staged Log control state is unavailable for publication",
                )
            deferred_controls[source_id] = control

        upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID) or upgrade
        upgrade.source_ids_json = json.dumps(
            source_ids if warning_source_ids else completed_ids
        )
        upgrade.state = "publishing"
        upgrade.updated_at = utc_now()
        session.commit()
        def record_rollback(rollback):
            checkpoints = json.loads(upgrade.batch_checkpoint_json or "{}")
            checkpoints["rollback"] = rollback
            upgrade.batch_checkpoint_json = json.dumps(checkpoints, default=str)
            session.commit()
        access.swap_candidate(candidate, live_path, on_rollback_retained=record_rollback)
        completed_at = utc_now()
        for source_id, control in deferred_controls.items():
            source = next(
                (item for item in current_sources if item.id == source_id),
                None,
            )
            if source is None:
                raise AnalyticsUpgradeScopeChangedError()
            apply_control(
                session,
                source,
                control,
                checked_at=completed_at,
            )
        mark_succeeded(upgrade, completed_at)
        if warning_source_ids:
            mark_completed_with_warnings(
                upgrade,
                warning_source_ids=warning_source_ids,
                completed_at=completed_at,
            )
        session.commit()
        return upgrade_status(upgrade, session)
    except Exception as exc:
        session.rollback()
        transient_collision = (
            isinstance(exc, AnalyticsUpgradeSourceError)
            and exc.code == "source_sync_busy"
        )
        # Keep a structurally valid partial candidate for the next retry. The next
        # attempt reconciles its schema, source/config fingerprints, batch checkpoints,
        # and candidate row counts before reusing anything. An unreadable or
        # incompatible candidate is discarded by that classifier.
        upgrade = session.get(AnalyticsUpgrade, UPGRADE_ID)
        if upgrade is not None:
            if analytics_swap_completed(live_path, upgrade):
                mark_control_recovery_pending(upgrade, exc, checked_at)
            elif transient_collision:
                # A user/scheduled sync started mid-build. This is not a failure —
                # defer and retry shortly instead of flashing a scary "failed" state.
                defer_upgrade(upgrade, checked_at)
            else:
                mark_failed(upgrade, exc, checked_at)
            session.commit()
            status = upgrade_status(upgrade, session)
        else:
            status = {
                "state": "failed",
                "error_code": getattr(exc, "code", "analytics_upgrade_failed"),
            }
        if transient_collision:
            logger.info("Analytics cache upgrade deferred: %s", exc)
        else:
            logger.warning("Analytics cache upgrade failed: %s", exc)
        return status
    finally:
        if global_rebuild_lock_acquired:
            access.analytics_global_rebuild_lock.release()
        session.close()

__all__ = ["run_upgrade_once"]
