from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar


Value = TypeVar("Value")


def wait_for(
    probe: Callable[[], Value],
    predicate: Callable[[Value], bool],
    *,
    timeout_seconds: float = 2.0,
    interval_seconds: float = 0.02,
    description: str = "condition",
) -> Value:
    """Poll until a predicate succeeds, reporting the last observed value on timeout."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")

    deadline = time.monotonic() + timeout_seconds
    last_value: Value | None = None
    while True:
        last_value = probe()
        if predicate(last_value):
            return last_value
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(
                f"Timed out waiting for {description}; last value: {last_value!r}"
            )
        time.sleep(min(interval_seconds, remaining))
