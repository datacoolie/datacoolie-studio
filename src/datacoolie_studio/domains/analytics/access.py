from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any, Iterator

import duckdb

from datacoolie_studio.core.config import analytics_database_path
from datacoolie_studio.domains.analytics import schema
from datacoolie_studio.domains.analytics import store
from datacoolie_studio.domains.analytics.connections import analytics_connections
from datacoolie_studio.domains.analytics.errors import AnalyticsRebuildRequired


analytics_maintenance_lock = RLock()


def connect(path: Path, *, read_only: bool = False):
    return analytics_connections.connect(path, read_only=read_only)


def cache_is_ready(path: Path) -> bool:
    try:
        conn = connect(path, read_only=True)
    except duckdb.Error:
        return False
    try:
        return schema.typed_cache_schema_is_ready(conn)
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


def swap_candidate(candidate_path: Path, live_path: Path) -> None:
    """Replace a cache only after validation and managed-reader drain."""
    if not candidate_path.exists():
        raise RuntimeError("Analytics rebuild candidate is missing")
    with analytics_connections.exclusive_maintenance():
        candidate_path.replace(live_path)
        live_wal = Path(f"{live_path}.wal")
        candidate_wal = Path(f"{candidate_path}.wal")
        if candidate_wal.exists():
            candidate_wal.replace(live_wal)
        elif live_wal.exists():
            live_wal.unlink()


def materialization_token(source_ids: list[int]) -> str:
    if not source_ids:
        return f"analytics-v{schema.ANALYTICS_SCHEMA_VERSION}:empty"
    path = analytics_database_path()
    if not path.exists():
        return unavailable_token(source_ids, "missing_database")
    try:
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
def reader(source_ids: list[int]) -> Iterator[tuple[Any, list[int], str]]:
    if not source_ids:
        yield None, [], f"analytics-v{schema.ANALYTICS_SCHEMA_VERSION}:empty"
        return
    path = analytics_database_path()
    if not path.exists():
        yield None, [], unavailable_token(source_ids, "missing_database")
        return
    try:
        conn = connect(path, read_only=True)
    except duckdb.Error:
        yield None, [], unavailable_token(source_ids, "database_unavailable")
        return
    try:
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
        conn.close()


def materialization_token_from_connection(
    conn,
    enabled_source_ids: list[int],
) -> str:
    if not schema.typed_cache_schema_is_ready(conn):
        raise AnalyticsRebuildRequired(
            "Monitoring analytics use an incompatible schema; rebuild the Log sources",
            source_ids=enabled_source_ids,
            missing_source_ids=enabled_source_ids,
            reason="schema_mismatch",
        )
    meta = store.analytics_meta(conn)
    cached_source_ids = store.cache_source_ids(conn)
    source_generations = store.cache_source_generations(conn)
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
        + ",".join(
            f"{source_id}:{int(source_generations.get(source_id, 0))}"
            for source_id in enabled_source_ids
        )
    )


def validate_source_complete_candidate(conn, expected_source_ids: list[int]) -> None:
    materialization_token_from_connection(conn, expected_source_ids)
    cached_source_ids = store.cache_source_ids(conn)
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
    from datacoolie_studio.domains.analytics_upgrade.service import (
        current_upgrade_status,
    )

    status = current_upgrade_status()
    if status is None:
        return None
    state = str(status.get("state") or "")
    if state not in {"pending", "building", "validating", "publishing", "failed"}:
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
