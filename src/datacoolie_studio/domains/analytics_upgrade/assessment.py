"""Cache assessment and source-scope rules for analytics upgrades."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from datacoolie_studio.db.models import Environment, EnvironmentSource, SourceObservation, SyncJob
from datacoolie_studio.domains.analytics import access, cache_state, schema
from datacoolie_studio.domains.analytics import schema_migration
from datacoolie_studio.domains.analytics_upgrade.models import CacheAssessment
from datacoolie_studio.domains.source_observation.repository import (
    MAX_CONSECUTIVE_OBSERVATION_FAILURES,
    paused_source_ids,
)


def _target_contract_document() -> dict[str, Any]:
    from datacoolie_studio.domains.analytics.record_codec import mapping_document

    return mapping_document()


def _target_contract_fingerprint() -> str:
    from datacoolie_studio.domains.analytics.record_codec import contract_fingerprint

    return contract_fingerprint()


def external_sync_in_progress(session: Session, source_ids: list[int]) -> bool:
    """Return whether another source sync owns any of these sources."""
    if not source_ids:
        return False
    return (
        session.scalar(
            select(SyncJob.id)
            .where(
                SyncJob.source_id.in_(source_ids),
                SyncJob.status.in_({"queued", "initializing", "running"}),
                SyncJob.job_type != "analytics_upgrade",
            )
            .limit(1)
        )
        is not None
    )

def source_context_map(sources: list[EnvironmentSource]) -> dict[str, dict[str, Any]]:
    return {str(source.id): source_context(source) for source in sources}

def source_snapshot_map(sources: list[EnvironmentSource]) -> dict[str, dict[str, Any]]:
    return {
        str(source.id): {
            "config_fingerprint": source_config_fingerprint(source),
            "uri": source.uri,
            "source_kind": source.source_kind,
        }
        for source in sources
    }

def source_config_fingerprint(source: EnvironmentSource) -> str:
    payload = {
        "environment_id": source.environment_id,
        "source_kind": source.source_kind,
        "uri": source.uri,
        "source_config_json": source.source_config_json,
        "storage_provider": source.storage_provider,
        "storage_auth_mode": source.storage_auth_mode,
        "credential_profile_id": source.credential_profile_id,
        "storage_config_json": source.storage_config_json,
        "enabled": source.enabled,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

def source_context(source: EnvironmentSource) -> dict[str, Any]:
    environment = source.environment
    project = environment.project if environment is not None else None
    return {
        "project_id": project.id if project is not None else None,
        "project_name": project.name if project is not None else None,
        "environment_id": environment.id if environment is not None else None,
        "environment_name": environment.name if environment is not None else None,
        "source_kind": source.source_kind,
        # Preserve the existing display-label contract while also returning the
        # raw URI for explicit source identification in the drawer.
        "label": source.label or source.uri,
        "uri": source.uri,
    }

def eligible_sources(session: Session) -> list[EnvironmentSource]:
    sources = list(
        session.scalars(
            select(EnvironmentSource)
            .options(
                selectinload(EnvironmentSource.environment).selectinload(
                    Environment.project
                )
            )
            .where(
                EnvironmentSource.source_kind == "logs",
                EnvironmentSource.enabled.is_(True),
            )
            .order_by(EnvironmentSource.id)
        )
    )
    if not sources:
        return []
    # A parked (paused) source cannot be rebuilt from its storage, so it is out of
    # scope for the complete-candidate upgrade. Excluding it keeps this scope aligned
    # with Monitoring coverage (monitoring/context.source_ids) and lets the upgrade
    # publish the healthy sources instead of failing on one unreachable source.
    paused = paused_source_ids(session, [source.id for source in sources])
    return [
        source
        for source in sources
        if source.id not in paused
    ]

def park_unbuildable_source(
    session: Session,
    source_id: int,
    code: str,
    message: str,
    when: datetime,
) -> None:
    """Pause automatic observation for a source that cannot be materialized."""
    state = session.get(SourceObservation, source_id)
    if state is None:
        state = SourceObservation(source_id=source_id)
        session.add(state)
    state.last_outcome = "error"
    state.error_json = json.dumps(
        {"code": code, "message": message},
        sort_keys=True,
        separators=(",", ":"),
    )
    state.failure_streak = max(
        state.failure_streak or 0, MAX_CONSECUTIVE_OBSERVATION_FAILURES
    )
    state.automatic_observation_paused_at = when
    state.next_observation_at = None
    state.last_attempted_at = when
    state.lease_owner = None
    state.lease_expires_at = None
    session.commit()

def resumable_candidate_sources(
    candidate: Path,
    source_ids: list[int],
    *,
    previous_snapshot: dict[str, dict[str, Any]] | None = None,
    current_snapshot: dict[str, dict[str, Any]] | None = None,
    checkpoints: dict[str, dict[str, Any]] | None = None,
) -> set[int]:
    """Return in-scope sources already fully built into a compatible partial candidate.

    Lets a retried upgrade skip work it already finished. An incompatible or unreadable
    candidate is discarded so the build starts clean.
    """
    from datacoolie_studio.domains.logs.batching import materialization_fingerprint
    if not candidate.exists():
        return set()
    try:
        conn = access.connect(candidate, read_only=True)
    except duckdb.Error:
        access.discard_candidate(candidate)
        return set()
    resumable = False
    cached_source_ids: set[int] = set()
    try:
        resumable = cache_state.candidate_is_resumable(conn)
        if resumable:
            cached_source_ids = cache_state.cache_source_ids(conn) & set(source_ids)
    except duckdb.Error:
        resumable = False
    finally:
        conn.close()
    if not resumable:
        access.discard_candidate(candidate)
        return set()
    resumable_ids = cached_source_ids
    if previous_snapshot and current_snapshot:
        resumable_ids = {
            source_id
            for source_id in resumable_ids
            if previous_snapshot.get(str(source_id), {}).get("config_fingerprint")
            == current_snapshot.get(str(source_id), {}).get("config_fingerprint")
        }
    if checkpoints and current_snapshot:
        resumable_ids = {
            source_id
            for source_id in resumable_ids
            if (
                checkpoints.get(str(source_id), {}).get("status") == "completed"
                and checkpoints.get(str(source_id), {}).get("config_fingerprint")
                == current_snapshot.get(str(source_id), {}).get("config_fingerprint")
            )
        }
    return {
        source_id for source_id in resumable_ids
        if (checkpoints or {}).get(str(source_id), {}).get("materialization_fingerprint")
        == materialization_fingerprint()
    }

def cache_is_current(path: Path, source_ids: list[int]) -> bool:
    return assess_cache(path, source_ids).action == "current"


def _meta_with_contract(conn) -> dict[str, Any] | None:
    """Read analytics metadata while tolerating pre-contract schema17 rows."""

    if not schema.table_exists(conn, schema.ANALYTICS_META_TABLE):
        return None
    columns = set(schema.table_columns(conn, schema.ANALYTICS_META_TABLE))
    selected = ["schema_version", "generation", "build_state", "published_at"]
    selected.extend(
        name for name in ("contract_fingerprint", "contract_json") if name in columns
    )
    try:
        row = conn.execute(
            f"SELECT {', '.join(selected)} FROM {schema.ANALYTICS_META_TABLE} "
            "WHERE singleton_id = 1"
        ).fetchone()
    except duckdb.Error:
        return None
    if row is None:
        return None
    value = dict(zip(selected, row))
    try:
        value["schema_version"] = int(value["schema_version"])
    except (TypeError, ValueError):
        value["schema_version"] = None
    value["contract_fingerprint"] = value.get("contract_fingerprint")
    raw_contract = value.get("contract_json")
    if isinstance(raw_contract, str) and raw_contract:
        try:
            value["contract_document"] = json.loads(raw_contract)
        except json.JSONDecodeError:
            value["contract_document"] = None
            value["contract_document_invalid"] = True
    else:
        value["contract_document"] = None
        value["contract_document_invalid"] = False
    return value


def cached_source_ids(path: Path) -> tuple[int, ...]:
    """Read source coverage for a SQL-only migration, including paused sources."""

    if not path.exists():
        return ()
    try:
        conn = access.connect(path, read_only=True)
    except duckdb.Error:
        return ()
    try:
        return tuple(sorted(cache_state.cache_source_ids(conn)))
    finally:
        conn.close()


def _unknown_current_cache_columns(conn) -> dict[str, tuple[str, ...]]:
    """Return columns outside the compiler-owned schema18 projection."""

    unknown: dict[str, tuple[str, ...]] = {}
    for table_name, column_types in (
        (schema.DATAFLOW_TABLE, schema.DATAFLOW_COLUMN_TYPES),
        (schema.JOB_TABLE, schema.JOB_COLUMN_TYPES),
    ):
        columns = set(schema.table_columns(conn, table_name))
        if not columns:
            continue
        expected = set(schema.cache_table_column_types(column_types))
        extra = tuple(sorted(columns - expected))
        if extra:
            unknown[table_name] = extra
    return unknown


def assess_cache(path: Path, source_ids: list[int]) -> CacheAssessment:
    """Classify cache maintenance without starting a rebuild.

    Schema incompatibility and an unreadable cache require a complete candidate.
    A structurally compatible cache with only missing source coverage can be repaired
    directly in the live cache, preserving healthy source generations.
    """

    if not path.exists():
        return CacheAssessment(
            action="initialize",
            source_version=None,
            reason_code="missing_database",
            missing_source_ids=tuple(source_ids),
        )
    try:
        conn = access.connect(path, read_only=True)
    except duckdb.Error:
        return CacheAssessment(
            action="cache_rebuild",
            source_version=None,
            reason_code="cache_unavailable",
            missing_source_ids=tuple(source_ids),
        )
    try:
        meta = _meta_with_contract(conn)
        source_version = meta.get("schema_version") if meta is not None else None

        # Schema17 is a complete, known typed cache and can be migrated from
        # its own fenced rows.  Keep all cached source IDs in this operation;
        # ``source_ids`` intentionally excludes paused/offline registrations.
        if source_version == schema_migration.LEGACY_SCHEMA_VERSION:
            migration_reason = schema_migration.schema17_migration_reason(conn)
            if migration_reason == "schema17_native_rewrite":
                return CacheAssessment(
                    action="schema_migration",
                    source_version=source_version,
                    reason_code=migration_reason,
                    missing_source_ids=(),
                    extra_source_ids=(),
                    migration_kind="schema17_native_rewrite",
                    migration_reason=migration_reason,
                    target_fingerprint=_target_contract_fingerprint(),
                )
            if migration_reason is not None:
                return CacheAssessment(
                    action="unsupported",
                    source_version=source_version,
                    reason_code=migration_reason,
                    migration_kind="unsupported",
                    migration_reason=migration_reason,
                )

        # A current-version cache with a stale/missing contract document may be
        # an additive storage revision.  Classify it before falling through to
        # the historical source-replay rebuild path.
        target_document = _target_contract_document()
        unknown_columns = _unknown_current_cache_columns(conn)
        if source_version == schema.ANALYTICS_SCHEMA_VERSION and unknown_columns:
            return CacheAssessment(
                action="unsupported",
                source_version=source_version,
                reason_code="unknown_cache_columns",
                migration_kind="unsupported",
                migration_reason="unknown_cache_columns",
                target_fingerprint=_target_contract_fingerprint(),
            )
        if (
            source_version == schema.ANALYTICS_SCHEMA_VERSION
            and meta is not None
            and meta.get("contract_document_invalid")
        ):
            return CacheAssessment(
                action="unsupported",
                source_version=source_version,
                reason_code="contract_metadata_invalid",
                migration_kind="unsupported",
                migration_reason="contract_metadata_invalid",
                target_fingerprint=_target_contract_fingerprint(),
            )
        if (
            source_version == schema.ANALYTICS_SCHEMA_VERSION
            and meta is not None
            and meta.get("contract_document") is not None
        ):
            diff = schema_migration.diff_mapping_documents(
                meta.get("contract_document"),
                target_document,
                previous_fingerprint=meta.get("contract_fingerprint"),
                target_fingerprint=_target_contract_fingerprint(),
            )
            if diff.kind == "unsupported":
                return CacheAssessment(
                    action="unsupported",
                    source_version=source_version,
                    reason_code=diff.reason_code,
                    migration_kind="unsupported",
                    migration_reason=diff.reason_code,
                    previous_fingerprint=diff.previous_fingerprint,
                    target_fingerprint=diff.target_fingerprint,
                )
            if diff.kind in {"additive_ddl", "stored_column_rewrite"}:
                return CacheAssessment(
                    action="schema_migration",
                    source_version=source_version,
                    reason_code=diff.reason_code,
                    migration_kind=diff.kind,
                    migration_reason=diff.reason_code,
                    previous_fingerprint=diff.previous_fingerprint,
                    target_fingerprint=diff.target_fingerprint,
                )
            if meta.get("contract_fingerprint") != _target_contract_fingerprint():
                # The stored document is semantically compatible, but the
                # published fingerprint is absent/stale. Repair metadata from
                # the candidate lifecycle instead of falling through to a
                # source replay when readiness checks reject the stale token.
                return CacheAssessment(
                    action="schema_migration",
                    source_version=source_version,
                    reason_code="contract_revision",
                    migration_kind="additive_ddl",
                    migration_reason="contract_revision",
                    previous_fingerprint=diff.previous_fingerprint,
                    target_fingerprint=diff.target_fingerprint,
                )
            if not (
                schema.typed_table_schema_is_current(
                    conn, schema.DATAFLOW_TABLE, schema.DATAFLOW_COLUMN_TYPES
                )
                and schema.typed_table_schema_is_current(
                    conn, schema.JOB_TABLE, schema.JOB_COLUMN_TYPES
                )
            ):
                return CacheAssessment(
                    action="unsupported",
                    source_version=source_version,
                    reason_code="cache_schema_incompatible",
                    migration_kind="unsupported",
                    migration_reason="cache_schema_incompatible",
                    previous_fingerprint=diff.previous_fingerprint,
                    target_fingerprint=diff.target_fingerprint,
                )
        if (
            source_version == schema.ANALYTICS_SCHEMA_VERSION
            and meta is not None
            and meta.get("contract_document") is None
            and schema.typed_table_schema_is_current(
                conn, schema.DATAFLOW_TABLE, schema.DATAFLOW_COLUMN_TYPES
            )
            and schema.typed_table_schema_is_current(
                conn, schema.JOB_TABLE, schema.JOB_COLUMN_TYPES
            )
        ):
            # A pre-contract schema18 cache can be repaired from its own rows;
            # source files are irrelevant to metadata publication.
            return CacheAssessment(
                action="schema_migration",
                source_version=source_version,
                reason_code="contract_metadata_missing",
                migration_kind="additive_ddl",
                migration_reason="contract_metadata_missing",
                target_fingerprint=_target_contract_fingerprint(),
            )
        if not schema.typed_cache_schema_is_ready(conn):
            return CacheAssessment(
                action="cache_rebuild",
                source_version=source_version,
                reason_code=(
                    "schema_mismatch"
                    if source_version != schema.ANALYTICS_SCHEMA_VERSION
                    else "cache_schema_incompatible"
                ),
                missing_source_ids=tuple(source_ids),
            )
        if meta is None or source_version != schema.ANALYTICS_SCHEMA_VERSION:
            return CacheAssessment(
                action="cache_rebuild",
                source_version=source_version,
                reason_code="schema_mismatch",
                missing_source_ids=tuple(source_ids),
            )
        if meta["build_state"] != "ready":
            return CacheAssessment(
                action="cache_rebuild",
                source_version=source_version,
                reason_code="cache_not_ready",
                missing_source_ids=tuple(source_ids),
            )
        cached_source_ids = cache_state.cache_source_ids(conn)
        expected_source_ids = set(source_ids)
        missing = tuple(sorted(expected_source_ids - cached_source_ids))
        extra = tuple(sorted(cached_source_ids - expected_source_ids))
        # Cached paused/offline generations remain valid coverage. Their source
        # registrations are intentionally absent from ``source_ids`` and are
        # purged only by the source lifecycle owner; treating them as a scope
        # mismatch would trigger an unrelated historical replay after every
        # SQL-only migration.
        if not missing:
            return CacheAssessment(
                action="current",
                source_version=source_version,
                reason_code="current",
                extra_source_ids=extra,
            )
        if missing:
            return CacheAssessment(
                action="source_repair",
                source_version=source_version,
                reason_code="missing_source_coverage",
                missing_source_ids=missing,
                extra_source_ids=extra,
            )
        return CacheAssessment(
            action="cache_rebuild",
            source_version=source_version,
            reason_code="source_scope_changed",
            missing_source_ids=missing,
            extra_source_ids=extra,
        )
    except duckdb.Error:
        return CacheAssessment(
            action="cache_rebuild",
            source_version=None,
            reason_code="cache_unavailable",
            missing_source_ids=tuple(source_ids),
        )
    finally:
        conn.close()

__all__ = [
    "assess_cache",
    "cache_is_current",
    "eligible_sources",
    "external_sync_in_progress",
    "park_unbuildable_source",
    "resumable_candidate_sources",
    "source_config_fingerprint",
    "source_context",
    "source_context_map",
    "source_snapshot_map",
]
