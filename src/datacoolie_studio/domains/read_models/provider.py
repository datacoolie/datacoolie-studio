from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from datacoolie_studio.domains.read_models.contracts import (
    CachedResult,
    ResultBuildCoordinator,
    ResultCacheKey,
    ResultCacheStore,
)
from datacoolie_studio.domains.read_models.coordinator import default_result_build_coordinator
from datacoolie_studio.domains.read_models.sqlite_store import SqliteResultCacheStore


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResultCacheProvider:
    store: ResultCacheStore
    coordinator: ResultBuildCoordinator


def result_cache_provider() -> ResultCacheProvider:
    """Composition root for result-cache infrastructure.

    A future Redis-backed deployment replaces this binding without changing
    domain services or cache-key semantics.
    """
    return ResultCacheProvider(
        store=ResilientResultCacheStore(SqliteResultCacheStore()),
        coordinator=default_result_build_coordinator,
    )


class ResilientResultCacheStore:
    """Keep disposable-cache I/O failures from failing the domain response."""

    def __init__(self, store: ResultCacheStore) -> None:
        self._store = store

    def get(self, key: ResultCacheKey) -> CachedResult | None:
        try:
            return self._store.get(key)
        except Exception:
            logger.exception("Result cache read failed for %s", key.lock_identity)
            return None

    def generation(self, key: ResultCacheKey) -> str:
        try:
            return self._store.generation(key)
        except Exception:
            logger.exception("Result cache generation read failed for %s", key.lock_identity)
            return "unavailable"

    def put(
        self,
        key: ResultCacheKey,
        payload: dict,
        *,
        expected_generation: str | None = None,
    ) -> CachedResult:
        try:
            return self._store.put(
                key,
                payload,
                expected_generation=expected_generation,
            )
        except Exception:
            logger.exception("Result cache write failed for %s", key.lock_identity)
            return CachedResult(
                payload=payload,
                computed_at=datetime.now(timezone.utc),
                stored=False,
            )

    def invalidate(self, environment_id: int, namespaces: set[str] | None = None) -> None:
        try:
            self._store.invalidate(environment_id, namespaces)
        except Exception:
            logger.exception("Result cache invalidation failed for environment %s", environment_id)
