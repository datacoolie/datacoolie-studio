from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
import shutil
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterator

import duckdb

from datacoolie_studio.core.config import analytics_database_path
from datacoolie_studio.core.process_lock import (
    ProcessLockTimeoutError,
    process_lock,
)
from datacoolie_studio.core.request_timing import timed_stage
from datacoolie_studio.domains.analytics import cache_state, ownership, schema
from datacoolie_studio.domains.analytics.connections import analytics_connections
from datacoolie_studio.domains.analytics.errors import AnalyticsRebuildRequired


analytics_maintenance_lock = RLock()
# A complete candidate rebuild owns the cache lifecycle from assessment through
# atomic swap. External source refreshes take the same lock; per-source repairs do
# not, so a repair can continue alongside unrelated source refreshes.
analytics_global_rebuild_lock = RLock()


class AnalyticsProcessOwnershipError(RuntimeError):
    """Raised when another Studio process owns the analytics cache lifecycle."""

    code = "analytics_process_owned"


@contextmanager
def analytics_process_ownership(path: Path | None = None) -> Iterator[Path]:
    """Hold an OS-backed lock for the live/candidate analytics database.

    DuckDB coordinates writers inside one process, but it does not make the
    live/candidate swap safe across independent Studio processes.  The lock file
    descriptor stays open for the entire application lifetime, so the operating
    system releases ownership automatically if the process exits unexpectedly.
    """

    live_path = path or analytics_database_path()
    live_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = live_path.with_name(f"{live_path.name}.owner.lock")
    try:
        with process_lock(
            lock_path,
            timeout_seconds=0,
            owner_label="analytics cache",
        ):
            yield lock_path
    except ProcessLockTimeoutError as exc:
        raise AnalyticsProcessOwnershipError(
            "Another DataCoolie Studio process owns the analytics cache "
            f"(lock: {lock_path})"
        ) from exc


def connect(path: Path, *, read_only: bool = False):
    return analytics_connections.connect(path, read_only=read_only)


def cache_is_ready(path: Path) -> bool:
    try:
        conn = connect(path, read_only=True)
    except duckdb.Error:
        return False
    try:
        return (
            schema.typed_cache_schema_is_ready(conn)
            and schema.analytics_publication_is_ready(conn)
        )
    finally:
        conn.close()


def schema_rebuild_required(path: Path) -> bool:
    return path.exists() and not cache_is_ready(path)


def candidate_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.candidate{path.suffix}")


def discard_candidate(path: Path) -> None:
    for candidate in (path, Path(f"{path}.wal")):
        if candidate.exists():
            candidate.unlink()


def swap_candidate(
    candidate_path: Path,
    live_path: Path,
    *,
    on_rollback_retained: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any] | None:
    """Replace a cache only after validation and managed-reader drain."""
    if not candidate_path.exists():
        raise RuntimeError("Analytics rebuild candidate is missing")
    with analytics_connections.exclusive_maintenance():
        rollback = None
        if live_path.exists():
            # Managed connections wait for maintenance to finish. This owner
            # already drained them and must open its checkpoint connection directly.
            old = duckdb.connect(str(live_path))
            try:
                old.execute("CHECKPOINT")
                meta = cache_state.analytics_meta(old) or {}
            finally:
                old.close()
            with live_path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            retained = live_path.with_name(f"{live_path.stem}.rollback-v{meta.get('schema_version', 'unknown')}-g{meta.get('generation', 0)}-{digest[:12]}{live_path.suffix}")
            if not retained.exists():
                with retained.open("xb") as output, live_path.open("rb") as source:
                    shutil.copyfileobj(source, output)
                    output.flush()
                    os.fsync(output.fileno())
            with retained.open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() != digest:
                    raise RuntimeError("Retained analytics rollback copy failed hash verification")
            rollback = {"path": str(retained), "sha256": digest, **meta}
            if on_rollback_retained is not None:
                on_rollback_retained(rollback)
        candidate_path.replace(live_path)
        live_wal = Path(f"{live_path}.wal")
        candidate_wal = Path(f"{candidate_path}.wal")
        if candidate_wal.exists():
            candidate_wal.replace(live_wal)
        elif live_wal.exists():
            live_wal.unlink()
        return rollback


def materialization_token(source_ids: list[int]) -> str:
    if not source_ids:
        return f"analytics-v{schema.ANALYTICS_SCHEMA_VERSION}:empty"
    path = analytics_database_path()
    if not path.exists():
        return unavailable_token(source_ids, "missing_database")
    try:
        with timed_stage("reader_connect"):
            conn = connect(path, read_only=True)
    except duckdb.Error:
        return unavailable_token(source_ids, "database_unavailable")
    try:
        try:
            return materialization_token_from_connection(conn, source_ids)
        except AnalyticsRebuildRequired as exc:
            return unavailable_token(source_ids, exc.reason)
    finally:
        conn.close()


@contextmanager
def reader(
    source_ids: list[int],
    *,
    snapshot: bool = False,
    prepare_connection: Callable[[Any], None] | None = None,
) -> Iterator[tuple[Any, list[int], str]]:
    if not source_ids:
        yield None, [], f"analytics-v{schema.ANALYTICS_SCHEMA_VERSION}:empty"
        return
    path = analytics_database_path()
    if not path.exists():
        yield None, [], unavailable_token(source_ids, "missing_database")
        return
    try:
        with timed_stage("reader_connect"):
            conn = connect(path, read_only=True)
    except duckdb.Error:
        yield None, [], unavailable_token(source_ids, "database_unavailable")
        return
    transaction_started = False
    try:
        try:
            # Catalog setup must commit before the request's read snapshot.
            # The caller owns domain-specific initialization.
            if prepare_connection is not None:
                with timed_stage("reader_prepare"):
                    prepare_connection(conn)
            if snapshot:
                conn.execute("BEGIN TRANSACTION")
                transaction_started = True
            try:
                token = materialization_token_from_connection(conn, source_ids)
            except AnalyticsRebuildRequired as exc:
                upgrade_error = _upgrade_error(source_ids)
                if upgrade_error is not None:
                    raise upgrade_error from exc
                yield None, [], unavailable_token(source_ids, exc.reason)
            else:
                yield conn, source_ids, token
        finally:
            if transaction_started:
                conn.execute("ROLLBACK")
    finally:
        conn.close()


def materialization_token_from_connection(
    conn,
    enabled_source_ids: list[int],
) -> str:
    from datacoolie_studio.domains.analytics.schema_compiler import contract_fingerprint
    with timed_stage("reader_schema"):
        if not schema.typed_cache_schema_is_ready(conn):
            raise AnalyticsRebuildRequired(
                "Monitoring analytics use an incompatible schema; rebuild the Log sources",
                source_ids=enabled_source_ids,
                missing_source_ids=enabled_source_ids,
                reason="schema_mismatch",
            )
    with timed_stage("reader_metadata"):
        meta = cache_state.analytics_meta(conn)
        cached_source_ids = cache_state.cache_source_ids(conn)
        source_generations = cache_state.cache_source_generations(conn)
        missing_source_ids = sorted(set(enabled_source_ids) - cached_source_ids)
        if (
            meta is None
            or meta["schema_version"] != schema.ANALYTICS_SCHEMA_VERSION
            or meta["build_state"] != "ready"
            or missing_source_ids
        ):
            raise AnalyticsRebuildRequired(
                "Monitoring analytics are incomplete; sync the Log sources to rebuild them",
                source_ids=enabled_source_ids,
                missing_source_ids=missing_source_ids or enabled_source_ids,
                reason="incomplete_sources" if missing_source_ids else "not_ready",
            )
        return (
            f"analytics-v{schema.ANALYTICS_SCHEMA_VERSION}:"
            + contract_fingerprint()[:16]
            + ":"
            + ",".join(
                f"{source_id}:{int(source_generations.get(source_id, 0))}"
                for source_id in enabled_source_ids
            )
        )


def validate_source_complete_candidate(conn, expected_source_ids: list[int]) -> None:
    materialization_token_from_connection(conn, expected_source_ids)
    cached_source_ids = cache_state.cache_source_ids(conn)
    if cached_source_ids != set(expected_source_ids):
        raise AnalyticsRebuildRequired(
            "Monitoring analytics candidate has unexpected source coverage",
            source_ids=expected_source_ids,
            missing_source_ids=sorted(set(expected_source_ids) - cached_source_ids),
            reason="source_scope_changed",
        )


def unavailable_token(source_ids: list[int], reason: str) -> str:
    source_key = ",".join(str(source_id) for source_id in source_ids)
    return (
        f"analytics-v{schema.ANALYTICS_SCHEMA_VERSION}:"
        f"unavailable:{reason}:{source_key}"
    )


def _upgrade_error(source_ids: list[int]) -> AnalyticsRebuildRequired | None:
    status = ownership.cache_upgrade_status()
    if status is None:
        return None
    state = str(status.get("state") or "")
    if state not in {
        "pending",
        "building",
        "staging",
        "repairing",
        "finalizing",
        "validating",
        "publishing",
        "failed",
    }:
        return None
    failed = state == "failed"
    return AnalyticsRebuildRequired(
        (
            "Monitoring analytics upgrade failed and will be retried"
            if failed
            else "Monitoring analytics are being upgraded"
        ),
        source_ids=source_ids,
        missing_source_ids=source_ids,
        reason="analytics_upgrade_failed" if failed else "analytics_upgrade_in_progress",
    )
