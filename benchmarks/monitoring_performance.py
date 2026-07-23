from __future__ import annotations

from statistics import median
from time import perf_counter
from typing import Any, Callable


def benchmark_call(
    call: Callable[[], Any],
    *,
    samples: int,
    warmups: int = 1,
) -> dict[str, Any]:
    for _ in range(warmups):
        call()
    measurements: list[dict[str, float | int]] = []
    for _ in range(samples):
        started_at = perf_counter()
        response = call()
        duration_ms = (perf_counter() - started_at) * 1_000
        content = getattr(response, "content", b"")
        measurements.append({
            "duration_ms": duration_ms,
            "payload_bytes": len(content),
        })
    return {"samples": measurements, "summary": _summarize(measurements)}


def _summarize(samples: list[dict[str, float | int]]) -> dict[str, Any]:
    if not samples:
        return {}
    result: dict[str, Any] = {}
    for key in samples[0]:
        values = [float(sample[key]) for sample in samples]
        result[key] = {
            "median": median(values),
            "p95": _nearest_rank(values, 0.95),
            "min": min(values),
            "max": max(values),
        }
    return result


def _nearest_rank(values: list[float], percentile: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * percentile + 0.999999) - 1))
    return ordered[index]
