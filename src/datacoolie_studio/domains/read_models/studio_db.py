from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentReadModelCacheEntry
from datacoolie_studio.domains.read_models.contracts import CachedResult, ResultCacheKey


@dataclass(frozen=True)
class StudioDbCachePolicy:
    max_memory_entries: int = 64
    max_memory_bytes: int = 32 * 1024 * 1024
    max_persisted_entries_per_environment: int = 256
    max_payload_bytes: int = 8 * 1024 * 1024


@dataclass(frozen=True)
class _MemoryEntry:
    result: CachedResult
    size_bytes: int


DEFAULT_POLICY = StudioDbCachePolicy()
_memory_lock = RLock()
_memory_cache: OrderedDict[tuple[int, str, str, str, str], _MemoryEntry] = OrderedDict()
_memory_bytes = 0


class StudioDbResultCacheStore:
    def __init__(self, session: Session, policy: StudioDbCachePolicy = DEFAULT_POLICY) -> None:
        self._session = session
        self._policy = policy

    def get(self, key: ResultCacheKey) -> CachedResult | None:
        memory_result = _memory_get(key.identity)
        if memory_result is not None:
            return memory_result

        entry = self._session.scalars(
            select(EnvironmentReadModelCacheEntry).where(
                EnvironmentReadModelCacheEntry.environment_id == key.environment_id,
                EnvironmentReadModelCacheEntry.model_key == key.namespace,
                EnvironmentReadModelCacheEntry.parameters_fingerprint == key.parameters_fingerprint,
                EnvironmentReadModelCacheEntry.input_fingerprint == key.input_fingerprint,
                EnvironmentReadModelCacheEntry.producer_version == key.producer_version,
            )
        ).first()
        if entry is None:
            return None
        result = CachedResult(payload=json.loads(entry.payload_json), computed_at=entry.computed_at)
        _memory_put(key.identity, result, len(entry.payload_json.encode("utf-8")), self._policy)
        return result

    def put(self, key: ResultCacheKey, payload: dict[str, Any]) -> CachedResult:
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        payload_size = len(payload_json.encode("utf-8"))
        if payload_size > self._policy.max_payload_bytes:
            return CachedResult(payload=payload, computed_at=datetime.now(timezone.utc), stored=False)

        self._session.execute(
            delete(EnvironmentReadModelCacheEntry).where(
                EnvironmentReadModelCacheEntry.environment_id == key.environment_id,
                EnvironmentReadModelCacheEntry.model_key == key.namespace,
                EnvironmentReadModelCacheEntry.parameters_fingerprint == key.parameters_fingerprint,
            )
        )
        entry = EnvironmentReadModelCacheEntry(
            environment_id=key.environment_id,
            model_key=key.namespace,
            parameters_fingerprint=key.parameters_fingerprint,
            input_fingerprint=key.input_fingerprint,
            producer_version=key.producer_version,
            payload_json=payload_json,
        )
        self._session.add(entry)
        self._session.flush()
        self._evict_excess(key.environment_id, key.namespace)
        self._session.commit()
        self._session.refresh(entry)

        result = CachedResult(payload=payload, computed_at=entry.computed_at)
        _memory_delete(environment_id=key.environment_id, namespaces={key.namespace})
        _memory_put(key.identity, result, payload_size, self._policy)
        return result

    def invalidate(self, environment_id: int, namespaces: set[str] | None = None) -> None:
        statement = delete(EnvironmentReadModelCacheEntry).where(
            EnvironmentReadModelCacheEntry.environment_id == environment_id,
        )
        if namespaces:
            statement = statement.where(EnvironmentReadModelCacheEntry.model_key.in_(namespaces))
        self._session.execute(statement)
        _memory_delete(environment_id=environment_id, namespaces=namespaces)

    def _evict_excess(self, environment_id: int, namespace: str) -> None:
        limit = max(1, self._policy.max_persisted_entries_per_environment)
        scope = EnvironmentReadModelCacheEntry.model_key == namespace
        if namespace.startswith("monitoring.page."):
            scope = EnvironmentReadModelCacheEntry.model_key.like("monitoring.page.%")
        stale_ids = list(
            self._session.scalars(
                select(EnvironmentReadModelCacheEntry.id)
                .where(
                    EnvironmentReadModelCacheEntry.environment_id == environment_id,
                    scope,
                )
                .order_by(
                    EnvironmentReadModelCacheEntry.computed_at.desc(),
                    EnvironmentReadModelCacheEntry.id.desc(),
                )
                .offset(limit)
            )
        )
        if stale_ids:
            self._session.execute(
                delete(EnvironmentReadModelCacheEntry).where(EnvironmentReadModelCacheEntry.id.in_(stale_ids))
            )


def clear_memory_cache() -> None:
    global _memory_bytes
    with _memory_lock:
        _memory_cache.clear()
        _memory_bytes = 0


def memory_cache_stats() -> dict[str, int]:
    with _memory_lock:
        return {"entries": len(_memory_cache), "bytes": _memory_bytes}


def _memory_get(identity: tuple[int, str, str, str, str]) -> CachedResult | None:
    with _memory_lock:
        entry = _memory_cache.get(identity)
        if entry is None:
            return None
        _memory_cache.move_to_end(identity)
        return entry.result


def _memory_put(
    identity: tuple[int, str, str, str, str],
    result: CachedResult,
    size_bytes: int,
    policy: StudioDbCachePolicy,
) -> None:
    global _memory_bytes
    if size_bytes > policy.max_memory_bytes:
        return
    with _memory_lock:
        previous = _memory_cache.pop(identity, None)
        if previous is not None:
            _memory_bytes -= previous.size_bytes
        _memory_cache[identity] = _MemoryEntry(result=result, size_bytes=size_bytes)
        _memory_bytes += size_bytes
        while len(_memory_cache) > policy.max_memory_entries or _memory_bytes > policy.max_memory_bytes:
            _, removed = _memory_cache.popitem(last=False)
            _memory_bytes -= removed.size_bytes


def _memory_delete(*, environment_id: int, namespaces: set[str] | None) -> None:
    global _memory_bytes
    with _memory_lock:
        identities = [
            identity
            for identity in _memory_cache
            if identity[0] == environment_id and (not namespaces or identity[1] in namespaces)
        ]
        for identity in identities:
            _memory_bytes -= _memory_cache.pop(identity).size_bytes
