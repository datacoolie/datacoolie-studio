from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import Environment
from datacoolie_studio.domains.read_models.contracts import CachedResult, ResultCacheKey
from datacoolie_studio.domains.read_models.coordinator import default_result_build_coordinator
from datacoolie_studio.domains.read_models.provider import result_cache_provider
from datacoolie_studio.domains.read_models.studio_db import (
    clear_memory_cache,
    memory_cache_stats,
)


CachedReadModel = CachedResult


def fingerprint(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def empty_parameters_fingerprint() -> str:
    return fingerprint({})


def cached_read_model(
    session: Session,
    *,
    environment_id: int,
    model_key: str,
    parameters_fingerprint: str,
    input_fingerprint: str,
    producer_version: str,
) -> CachedReadModel | None:
    return result_cache_provider(session).store.get(
        _key(
            environment_id,
            model_key,
            parameters_fingerprint,
            input_fingerprint,
            producer_version,
        )
    )


def replace_read_model(
    session: Session,
    *,
    environment_id: int,
    model_key: str,
    parameters_fingerprint: str,
    input_fingerprint: str,
    producer_version: str,
    payload: dict[str, Any],
) -> CachedReadModel:
    return result_cache_provider(session).store.put(
        _key(
            environment_id,
            model_key,
            parameters_fingerprint,
            input_fingerprint,
            producer_version,
        ),
        payload,
    )


def invalidate_environment_read_models(
    session: Session,
    environment_id: int,
    *,
    model_keys: set[str] | None = None,
) -> None:
    result_cache_provider(session).store.invalidate(environment_id, model_keys)


def invalidate_project_read_models(
    session: Session,
    project_id: int,
    *,
    model_keys: set[str] | None = None,
) -> None:
    environment_ids = list(
        session.scalars(select(Environment.id).where(Environment.project_id == project_id))
    )
    store = result_cache_provider(session).store
    for environment_id in environment_ids:
        store.invalidate(int(environment_id), model_keys)


@contextmanager
def read_model_build_lock(cache_key: str) -> Iterator[None]:
    with default_result_build_coordinator.acquire(cache_key):
        yield


def _key(
    environment_id: int,
    model_key: str,
    parameters_fingerprint: str,
    input_fingerprint: str,
    producer_version: str,
) -> ResultCacheKey:
    return ResultCacheKey(
        environment_id=environment_id,
        namespace=model_key,
        parameters_fingerprint=parameters_fingerprint,
        input_fingerprint=input_fingerprint,
        producer_version=producer_version,
    )
