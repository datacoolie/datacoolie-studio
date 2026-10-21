"""Request-local diagnostics, excluded from cached response payloads."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter
from typing import Iterator


@dataclass
class RequestTiming:
    started_at: float = field(default_factory=perf_counter)
    durations: dict[str, float] = field(default_factory=dict)
    cache: str = "bypass"

    def server_timing(self) -> str:
        values = [f'{name};dur={duration:.3f}' for name, duration in self.durations.items()]
        values.append(f'cache;desc="{self.cache}"')
        values.append(f'route;dur={(perf_counter() - self.started_at) * 1000:.3f}')
        return ", ".join(values)


_active_timing: ContextVar[RequestTiming | None] = ContextVar("studio_request_timing", default=None)


@contextmanager
def request_timing_scope() -> Iterator[RequestTiming]:
    timing = RequestTiming()
    token = _active_timing.set(timing)
    try:
        yield timing
    finally:
        _active_timing.reset(token)


@contextmanager
def timed_stage(name: str) -> Iterator[None]:
    timing = _active_timing.get()
    if timing is None:
        yield
        return
    started_at = perf_counter()
    try:
        yield
    finally:
        timing.durations[name] = timing.durations.get(name, 0.0) + (perf_counter() - started_at) * 1000


def record_cache_outcome(hit: bool) -> None:
    timing = _active_timing.get()
    if timing is not None:
        timing.cache = "hit" if hit else "miss"
