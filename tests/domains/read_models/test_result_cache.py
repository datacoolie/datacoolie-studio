from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock

from sqlalchemy import func, select

from datacoolie_studio.domains.read_models.contracts import (
    CachedResult,
    ResultCacheKey,
    get_or_compute,
)
from datacoolie_studio.domains.read_models.coordinator import InProcessResultBuildCoordinator
from datacoolie_studio.domains.read_models.database import (
    ResultCacheEntry,
    create_result_cache_session,
    reset_result_cache_engine,
)
from datacoolie_studio.domains.read_models.sqlite_store import (
    ResultCachePolicy,
    SqliteResultCacheStore,
    clear_memory_cache,
)
from datacoolie_studio.domains.monitoring.page_service import _canonical_parameters


class _MemoryStore:
    def __init__(self) -> None:
        self.values: dict[tuple[int, str, str, str, str], CachedResult] = {}
        self.lock = Lock()

    def get(self, key: ResultCacheKey) -> CachedResult | None:
        with self.lock:
            return self.values.get(key.identity)

    def generation(self, key: ResultCacheKey) -> str:
        return "0:0:0"

    def put(
        self,
        key: ResultCacheKey,
        payload: dict,
        *,
        expected_generation: str | None = None,
    ) -> CachedResult:
        result = CachedResult(payload=payload, computed_at=datetime.now(timezone.utc))
        with self.lock:
            self.values[key.identity] = result
        return result

    def invalidate(self, environment_id: int, namespaces: set[str] | None = None) -> None:
        with self.lock:
            self.values = {
                identity: value
                for identity, value in self.values.items()
                if identity[0] != environment_id or (namespaces and identity[1] not in namespaces)
            }


def _key(parameter: str = "default", input_version: str = "source-v1") -> ResultCacheKey:
    return ResultCacheKey(
        environment_id=1,
        namespace="monitoring.page.performance",
        parameters_fingerprint=parameter,
        input_fingerprint=input_version,
        producer_version="v1",
    )


def test_get_or_compute_coalesces_concurrent_misses():
    store = _MemoryStore()
    coordinator = InProcessResultBuildCoordinator()
    calls = 0
    calls_lock = Lock()

    def producer() -> dict:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.03)
        return {"value": 7}

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(lambda _: get_or_compute(store, coordinator, _key(), producer), range(5)))

    assert calls == 1
    assert [result.payload for result, _ in results] == [{"value": 7}] * 5
    assert sum(1 for _, cache_hit in results if not cache_hit) == 1
    assert coordinator.active_keys() == 0


def test_sqlite_store_coalesces_concurrent_requests(tmp_path: Path, monkeypatch):
    clear_memory_cache()
    monkeypatch.setenv(
        "DATACOOLIE_STUDIO_RESULT_CACHE_URL",
        f"sqlite:///{(tmp_path / 'cache.db').as_posix()}",
    )
    reset_result_cache_engine()
    coordinator = InProcessResultBuildCoordinator()
    calls = 0
    calls_lock = Lock()

    def request() -> dict:
        nonlocal calls
        store = SqliteResultCacheStore()

        def producer() -> dict:
            nonlocal calls
            with calls_lock:
                calls += 1
            time.sleep(0.03)
            return {"value": 9}

        result, _ = get_or_compute(store, coordinator, _key(), producer)
        return result.payload

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(lambda _: request(), range(5)))

    assert results == [{"value": 9}] * 5
    assert calls == 1


def test_get_or_compute_releases_failed_build_for_retry():
    store = _MemoryStore()
    coordinator = InProcessResultBuildCoordinator()

    def fail() -> dict:
        raise RuntimeError("build failed")

    try:
        get_or_compute(store, coordinator, _key(), fail)
    except RuntimeError:
        pass
    else:
        raise AssertionError("failed producer must propagate")

    result, cache_hit = get_or_compute(store, coordinator, _key(), lambda: {"value": 8})
    assert result.payload == {"value": 8}
    assert cache_hit is False
    assert coordinator.active_keys() == 0


def test_clear_during_build_prevents_stale_repopulation(monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_RESULT_CACHE_URL", "memory://")
    reset_result_cache_engine()
    clear_memory_cache()
    store = SqliteResultCacheStore()
    coordinator = InProcessResultBuildCoordinator()
    started = Event()
    release = Event()

    def producer() -> dict:
        started.set()
        assert release.wait(timeout=2)
        return {"value": "stale"}

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(get_or_compute, store, coordinator, _key(), producer)
        assert started.wait(timeout=2)
        store.clear(environment_id=1, namespaces={"monitoring.page.performance"})
        release.set()
        result, cache_hit = future.result(timeout=2)

    assert cache_hit is False
    assert result.stored is False
    assert store.get(_key()) is None


def test_relative_window_anchor_uses_studio_timezone():
    now = datetime(2026, 7, 18, 18, 0, tzinfo=timezone.utc)

    utc = _canonical_parameters("overview", {"range": "30d"}, "UTC", now=now)
    saigon = _canonical_parameters("overview", {"range": "30d"}, "Asia/Saigon", now=now)

    assert utc["window_anchor"] == "2026-07-18"
    assert saigon["window_anchor"] == "2026-07-19"


def test_freshness_window_anchor_tracks_the_current_minute():
    now = datetime(2026, 7, 18, 18, 23, 45, tzinfo=timezone.utc)

    freshness = _canonical_parameters("freshness", {"range": "all"}, "Asia/Saigon", now=now)

    assert freshness["window_anchor"] == "2026-07-19T01:23:00+07:00"


def test_sqlite_store_reuses_versions_and_bounds_persisted_entries(monkeypatch):
    clear_memory_cache()
    monkeypatch.setenv("DATACOOLIE_STUDIO_RESULT_CACHE_URL", "memory://")
    reset_result_cache_engine()
    policy = ResultCachePolicy(
        max_memory_entries=2,
        max_memory_bytes=1_024,
        max_persisted_variants_per_environment=3,
        max_payload_bytes=256,
    )
    store = SqliteResultCacheStore(policy)
    store.put(_key("one"), {"value": 1})
    store.put(_key("two"), {"value": 2})
    store.put(replace(_key("catalog"), namespace="assets.catalog"), {"value": "assets"})
    store.put(_key("three"), {"value": 3})
    with create_result_cache_session() as session:
        assert session.scalar(select(func.count()).select_from(ResultCacheEntry)) == 3

    clear_memory_cache()
    assert store.get(_key("one")) is None
    assert store.get(_key("two")) is not None
    assert store.get(_key("three")) is not None

    replacement = store.put(_key("three", "source-v2"), {"value": 4})
    assert replacement.stored is True
    assert store.get(_key("three")) is None
    assert store.get(_key("three", "source-v2")) is not None

    oversized = store.put(_key("large"), {"value": "x" * 300})
    assert oversized.stored is False
    assert store.get(_key("large")) is None

    store.invalidate(1, {"monitoring.page.performance"})
    with create_result_cache_session() as session:
        assert session.scalar(select(func.count()).select_from(ResultCacheEntry)) == 1


def test_cache_admin_optimize_reclaims_space_on_first_call(tmp_path: Path, monkeypatch):
    clear_memory_cache()
    cache_path = tmp_path / "cache.db"
    monkeypatch.setenv(
        "DATACOOLIE_STUDIO_RESULT_CACHE_URL",
        f"sqlite:///{cache_path.as_posix()}",
    )
    reset_result_cache_engine()
    store = SqliteResultCacheStore()

    for index in range(8):
        store.put(_key(f"entry-{index}"), {"value": "x" * (1024 * 1024)})
    store.clear()
    size_before = cache_path.stat().st_size

    from datacoolie_studio.domains.cache_admin import service as cache_admin

    optimized = cache_admin.compact_cache()["read_models"]
    pruned = optimized["prune"]

    assert pruned["deleted_entries"] == 0
    assert optimized["file_bytes_before"] == size_before
    assert optimized["file_bytes_after"] < size_before
    assert cache_path.stat().st_size == optimized["file_bytes_after"]
