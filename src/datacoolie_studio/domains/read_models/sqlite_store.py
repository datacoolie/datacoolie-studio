from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from datacoolie_studio.domains.read_models.contracts import CachedResult, ResultCacheKey
from datacoolie_studio.domains.read_models.database import (
    ResultCacheEntry,
    ResultCacheGeneration,
    create_result_cache_session,
    get_result_cache_engine,
)


@dataclass(frozen=True)
class ResultCachePolicy:
    max_memory_entries: int = 64
    max_memory_bytes: int = 32 * 1024 * 1024
    max_persisted_variants_per_environment: int = 128
    max_payload_bytes: int = 8 * 1024 * 1024
    max_persisted_payload_bytes: int = 256 * 1024 * 1024


@dataclass(frozen=True)
class _MemoryEntry:
    result: CachedResult
    size_bytes: int
    generation_token: str


DEFAULT_POLICY = ResultCachePolicy()
_memory_lock = RLock()
_memory_cache: OrderedDict[tuple[int, str, str, str, str], _MemoryEntry] = OrderedDict()
_memory_bytes = 0


class SqliteResultCacheStore:
    def __init__(self, policy: ResultCachePolicy = DEFAULT_POLICY) -> None:
        self._policy = policy
        get_result_cache_engine()

    def get(self, key: ResultCacheKey) -> CachedResult | None:
        with create_result_cache_session() as session:
            generation_token = _generation_token(session, key)
            memory_result = _memory_get(key.identity, generation_token)
            if memory_result is not None:
                return memory_result
            entry = session.scalar(
                select(ResultCacheEntry).where(
                    ResultCacheEntry.environment_id == key.environment_id,
                    ResultCacheEntry.namespace == key.namespace,
                    ResultCacheEntry.parameters_fingerprint == key.parameters_fingerprint,
                    ResultCacheEntry.input_fingerprint == key.input_fingerprint,
                    ResultCacheEntry.producer_version == key.producer_version,
                    ResultCacheEntry.generation_token == generation_token,
                )
            )
            if entry is None:
                return None
            result = CachedResult(payload=json.loads(entry.payload_json), computed_at=entry.computed_at)
            _memory_put(key.identity, result, entry.payload_bytes, generation_token, self._policy)
            return result

    def generation(self, key: ResultCacheKey) -> str:
        with create_result_cache_session() as session:
            return _generation_token(session, key)

    def put(
        self,
        key: ResultCacheKey,
        payload: dict[str, Any],
        *,
        expected_generation: str | None = None,
    ) -> CachedResult:
        computed_at = datetime.now(timezone.utc)
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        payload_bytes = len(payload_json.encode("utf-8"))
        if payload_bytes > self._policy.max_payload_bytes:
            return CachedResult(payload=payload, computed_at=computed_at, stored=False)

        with create_result_cache_session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            try:
                generation_token = _generation_token(session, key)
                if expected_generation is not None and expected_generation != generation_token:
                    session.rollback()
                    return CachedResult(payload=payload, computed_at=computed_at, stored=False)
                session.execute(
                    delete(ResultCacheEntry).where(
                        ResultCacheEntry.environment_id == key.environment_id,
                        ResultCacheEntry.namespace == key.namespace,
                        ResultCacheEntry.parameters_fingerprint == key.parameters_fingerprint,
                    )
                )
                entry = ResultCacheEntry(
                    environment_id=key.environment_id,
                    namespace=key.namespace,
                    namespace_family=namespace_family(key.namespace),
                    parameters_fingerprint=key.parameters_fingerprint,
                    input_fingerprint=key.input_fingerprint,
                    producer_version=key.producer_version,
                    generation_token=generation_token,
                    payload_json=payload_json,
                    payload_bytes=payload_bytes,
                    computed_at=computed_at,
                )
                session.add(entry)
                session.flush()
                self._prune_environment(session, key.environment_id)
                self._prune_total_bytes(session)
                persisted = session.scalar(
                    select(ResultCacheEntry.id).where(ResultCacheEntry.id == entry.id)
                ) is not None
                session.commit()
            except Exception:
                session.rollback()
                raise

        if not persisted:
            return CachedResult(payload=payload, computed_at=computed_at, stored=False)

        _memory_delete(
            environment_id=key.environment_id,
            namespaces={key.namespace},
            parameters_fingerprint=key.parameters_fingerprint,
        )
        _memory_put(key.identity, CachedResult(payload, computed_at), payload_bytes, generation_token, self._policy)
        return CachedResult(payload=payload, computed_at=computed_at)

    def invalidate(self, environment_id: int, namespaces: set[str] | None = None) -> None:
        with create_result_cache_session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            try:
                if namespaces:
                    families = {namespace_family(namespace) for namespace in namespaces}
                    for family in families:
                        _advance_generation(session, _family_scope(environment_id, family))
                    session.execute(
                        delete(ResultCacheEntry).where(
                            ResultCacheEntry.environment_id == environment_id,
                            ResultCacheEntry.namespace_family.in_(families),
                        )
                    )
                else:
                    _advance_generation(session, _environment_scope(environment_id))
                    session.execute(
                        delete(ResultCacheEntry).where(ResultCacheEntry.environment_id == environment_id)
                    )
                session.commit()
            except Exception:
                session.rollback()
                raise
        _memory_delete(environment_id=environment_id, namespaces=namespaces)

    def clear(
        self,
        *,
        environment_id: int | None = None,
        namespaces: set[str] | None = None,
    ) -> dict[str, int]:
        with create_result_cache_session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            statement = select(
                func.count(ResultCacheEntry.id),
                func.coalesce(func.sum(ResultCacheEntry.payload_bytes), 0),
            )
            delete_statement = delete(ResultCacheEntry)
            if environment_id is not None:
                statement = statement.where(ResultCacheEntry.environment_id == environment_id)
                delete_statement = delete_statement.where(ResultCacheEntry.environment_id == environment_id)
            if namespaces:
                families = {namespace_family(namespace) for namespace in namespaces}
                statement = statement.where(ResultCacheEntry.namespace_family.in_(families))
                delete_statement = delete_statement.where(ResultCacheEntry.namespace_family.in_(families))
            count, payload_bytes = session.execute(statement).one()
            try:
                if environment_id is None:
                    _advance_generation(session, "global")
                elif namespaces:
                    for family in {namespace_family(namespace) for namespace in namespaces}:
                        _advance_generation(session, _family_scope(environment_id, family))
                else:
                    _advance_generation(session, _environment_scope(environment_id))
                session.execute(delete_statement)
                session.commit()
            except Exception:
                session.rollback()
                raise
        if environment_id is None:
            clear_memory_cache()
        else:
            _memory_delete(environment_id=environment_id, namespaces=namespaces)
        return {"deleted_entries": int(count), "deleted_payload_bytes": int(payload_bytes)}

    def stats(self) -> dict[str, Any]:
        with create_result_cache_session() as session:
            entries, payload_bytes = session.execute(
                select(
                    func.count(ResultCacheEntry.id),
                    func.coalesce(func.sum(ResultCacheEntry.payload_bytes), 0),
                )
            ).one()
        path = _configured_database_path()
        return {
            "backend": "sqlite",
            "path": str(path) if path is not None else ":memory:",
            "entries": int(entries),
            "payload_bytes": int(payload_bytes),
            "file_bytes": path.stat().st_size if path is not None and path.exists() else 0,
            "limits": {
                "max_payload_bytes": self._policy.max_payload_bytes,
                "max_persisted_payload_bytes": self._policy.max_persisted_payload_bytes,
                "max_persisted_variants_per_environment": self._policy.max_persisted_variants_per_environment,
                "max_memory_entries": self._policy.max_memory_entries,
                "max_memory_bytes": self._policy.max_memory_bytes,
            },
            "memory": memory_cache_stats(),
        }

    def prune(self) -> dict[str, int]:
        with create_result_cache_session() as session:
            before_entries, before_bytes = _stored_totals(session)
            environment_ids = session.scalars(
                select(ResultCacheEntry.environment_id).distinct()
            ).all()
            with session.begin_nested():
                for environment_id in environment_ids:
                    self._prune_environment(session, int(environment_id))
                self._prune_total_bytes(session)
            session.commit()
            after_entries, after_bytes = _stored_totals(session)
        clear_memory_cache()
        return {
            "deleted_entries": before_entries - after_entries,
            "deleted_payload_bytes": before_bytes - after_bytes,
        }

    def compact(self) -> dict[str, int]:
        path = _configured_database_path()
        before = path.stat().st_size if path is not None and path.exists() else 0
        engine = get_result_cache_engine()
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            connection.execute(text("VACUUM"))
            connection.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
        after = path.stat().st_size if path is not None and path.exists() else 0
        return {"file_bytes_before": before, "file_bytes_after": after}

    def entry_count(self, environment_id: int, namespace: str) -> int:
        with create_result_cache_session() as session:
            return int(
                session.scalar(
                    select(func.count(ResultCacheEntry.id)).where(
                        ResultCacheEntry.environment_id == environment_id,
                        ResultCacheEntry.namespace == namespace,
                    )
                )
                or 0
            )

    def _prune_environment(self, session: Session, environment_id: int) -> None:
        limit = max(1, self._policy.max_persisted_variants_per_environment)
        stale_ids = list(
            session.scalars(
                select(ResultCacheEntry.id)
                .where(ResultCacheEntry.environment_id == environment_id)
                .order_by(ResultCacheEntry.computed_at.desc(), ResultCacheEntry.id.desc())
                .offset(limit)
            )
        )
        if stale_ids:
            session.execute(delete(ResultCacheEntry).where(ResultCacheEntry.id.in_(stale_ids)))

    def _prune_total_bytes(self, session: Session) -> None:
        total_bytes = int(session.scalar(select(func.coalesce(func.sum(ResultCacheEntry.payload_bytes), 0))) or 0)
        excess = total_bytes - self._policy.max_persisted_payload_bytes
        if excess <= 0:
            return
        removed = 0
        stale_ids: list[int] = []
        for entry_id, payload_bytes in session.execute(
            select(ResultCacheEntry.id, ResultCacheEntry.payload_bytes).order_by(
                ResultCacheEntry.computed_at,
                ResultCacheEntry.id,
            )
        ):
            stale_ids.append(int(entry_id))
            removed += int(payload_bytes)
            if removed >= excess:
                break
        if stale_ids:
            session.execute(delete(ResultCacheEntry).where(ResultCacheEntry.id.in_(stale_ids)))


def namespace_family(namespace: str) -> str:
    if namespace.startswith("monitoring.page."):
        return "monitoring.page"
    return namespace


def _configured_database_path() -> Path | None:
    database = get_result_cache_engine().url.database
    return Path(database) if database else None


def clear_memory_cache() -> None:
    global _memory_bytes
    with _memory_lock:
        _memory_cache.clear()
        _memory_bytes = 0


def memory_cache_stats() -> dict[str, int]:
    with _memory_lock:
        return {"entries": len(_memory_cache), "bytes": _memory_bytes}


def _generation_token(session: Session, key: ResultCacheKey) -> str:
    scopes = (
        "global",
        _environment_scope(key.environment_id),
        _family_scope(key.environment_id, namespace_family(key.namespace)),
    )
    generations = {
        scope: generation
        for scope, generation in session.execute(
            select(ResultCacheGeneration.scope_key, ResultCacheGeneration.generation).where(
                ResultCacheGeneration.scope_key.in_(scopes)
            )
        )
    }
    return ":".join(str(int(generations.get(scope, 0))) for scope in scopes)


def _advance_generation(session: Session, scope_key: str) -> None:
    generation = session.get(ResultCacheGeneration, scope_key)
    if generation is None:
        session.add(ResultCacheGeneration(scope_key=scope_key, generation=1))
    else:
        generation.generation += 1


def _environment_scope(environment_id: int) -> str:
    return f"environment:{environment_id}"


def _family_scope(environment_id: int, family: str) -> str:
    return f"environment:{environment_id}:family:{family}"


def _stored_totals(session: Session) -> tuple[int, int]:
    count, payload_bytes = session.execute(
        select(
            func.count(ResultCacheEntry.id),
            func.coalesce(func.sum(ResultCacheEntry.payload_bytes), 0),
        )
    ).one()
    return int(count), int(payload_bytes)


def _memory_get(
    identity: tuple[int, str, str, str, str],
    generation_token: str,
) -> CachedResult | None:
    with _memory_lock:
        entry = _memory_cache.get(identity)
        if entry is None or entry.generation_token != generation_token:
            return None
        _memory_cache.move_to_end(identity)
        return entry.result


def _memory_put(
    identity: tuple[int, str, str, str, str],
    result: CachedResult,
    size_bytes: int,
    generation_token: str,
    policy: ResultCachePolicy,
) -> None:
    global _memory_bytes
    if size_bytes > policy.max_memory_bytes:
        return
    with _memory_lock:
        previous = _memory_cache.pop(identity, None)
        if previous is not None:
            _memory_bytes -= previous.size_bytes
        _memory_cache[identity] = _MemoryEntry(result, size_bytes, generation_token)
        _memory_bytes += size_bytes
        while len(_memory_cache) > policy.max_memory_entries or _memory_bytes > policy.max_memory_bytes:
            _, removed = _memory_cache.popitem(last=False)
            _memory_bytes -= removed.size_bytes


def _memory_delete(
    *,
    environment_id: int,
    namespaces: set[str] | None,
    parameters_fingerprint: str | None = None,
) -> None:
    global _memory_bytes
    families = {namespace_family(namespace) for namespace in namespaces} if namespaces else None
    with _memory_lock:
        identities = [
            identity
            for identity in _memory_cache
            if identity[0] == environment_id
            and (families is None or namespace_family(identity[1]) in families)
            and (parameters_fingerprint is None or identity[2] == parameters_fingerprint)
        ]
        for identity in identities:
            _memory_bytes -= _memory_cache.pop(identity).size_bytes
