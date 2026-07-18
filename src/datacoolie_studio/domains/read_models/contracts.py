from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, TypeVar


Payload = dict[str, Any]
ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class ResultCacheKey:
    environment_id: int
    namespace: str
    parameters_fingerprint: str
    input_fingerprint: str
    producer_version: str

    @property
    def logical_identity(self) -> tuple[int, str, str]:
        return self.environment_id, self.namespace, self.parameters_fingerprint

    @property
    def identity(self) -> tuple[int, str, str, str, str]:
        return (
            self.environment_id,
            self.namespace,
            self.parameters_fingerprint,
            self.input_fingerprint,
            self.producer_version,
        )

    @property
    def lock_identity(self) -> str:
        return ":".join(str(value) for value in self.identity)


@dataclass(frozen=True)
class CachedResult:
    payload: Payload
    computed_at: datetime
    stored: bool = True


class ResultCacheStore(Protocol):
    def get(self, key: ResultCacheKey) -> CachedResult | None: ...

    def put(self, key: ResultCacheKey, payload: Payload) -> CachedResult: ...

    def invalidate(self, environment_id: int, namespaces: set[str] | None = None) -> None: ...


class ResultBuildCoordinator(Protocol):
    def acquire(self, identity: str) -> AbstractContextManager[None]: ...


def get_or_compute(
    store: ResultCacheStore,
    coordinator: ResultBuildCoordinator,
    key: ResultCacheKey,
    producer: Callable[[], Payload],
) -> tuple[CachedResult, bool]:
    cached = store.get(key)
    if cached is not None:
        return cached, True

    with coordinator.acquire(key.lock_identity):
        cached = store.get(key)
        if cached is not None:
            return cached, True
        payload = producer()
        return store.put(key, payload), False
