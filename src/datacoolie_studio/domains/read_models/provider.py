from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from datacoolie_studio.domains.read_models.contracts import ResultBuildCoordinator, ResultCacheStore
from datacoolie_studio.domains.read_models.coordinator import default_result_build_coordinator
from datacoolie_studio.domains.read_models.studio_db import StudioDbResultCacheStore


@dataclass(frozen=True)
class ResultCacheProvider:
    store: ResultCacheStore
    coordinator: ResultBuildCoordinator


def result_cache_provider(session: Session) -> ResultCacheProvider:
    """Composition root for result-cache infrastructure.

    A future Redis-backed deployment replaces this binding without changing
    domain services or cache-key semantics.
    """
    return ResultCacheProvider(
        store=StudioDbResultCacheStore(session),
        coordinator=default_result_build_coordinator,
    )

