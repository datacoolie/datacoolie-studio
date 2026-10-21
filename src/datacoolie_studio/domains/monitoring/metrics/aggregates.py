from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Literal


AggregateMode = Literal["v6", "observed"]


def status(row: dict[str, Any]) -> str:
    return str(row.get("status") or "unknown")


def sum_values(rows: list[dict[str, Any]], key: str) -> int | float:
    """Return a compatibility sum without narrowing integer metrics to float.

    Native HUGEINT projections arrive as Python integers.  Decimal strings and
    Decimal values remain supported for compatibility, while float conversion
    is avoided for integral metrics above 2**53.  This helper intentionally
    keeps the historical missing-as-zero behavior; callers that publish a
    completeness-sensitive total should use :func:`sum_observed`.
    """
    values = [numeric_value(row, key) for row in rows]
    if not values:
        return 0
    total: int | float = 0
    for value in values:
        if value is None:
            continue
        total += value
    return _rounded_number(total)


def sum_observed(
    rows: list[dict[str, Any]],
    key: str,
    *,
    mode: AggregateMode = "v6",
) -> int | float | None:
    """Reduce a metric using an explicitly resolved source-version mode.

    Historical v6 totals require every row in the selected population to have
    a value. Revised v7 and mixed populations sum observed numeric values;
    empty or all-null populations remain unknown.
    """
    values = [numeric_value(row, key) for row in rows]
    if mode == "v6":
        if not rows:
            return 0
        if any(value is None for value in values):
            return None
    observed = [value for value in values if value is not None]
    if not observed:
        return None
    return _rounded_number(sum(observed))


def average(values: list[int | float | None]) -> float:
    clean = [value for value in values if value is not None]
    return round(sum(clean) / len(clean), 3) if clean else 0


def rate(part: int | float, whole: int | float) -> float:
    return round((part / whole) * 100, 2) if whole else 0


def duration_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [numeric_value(row, "duration_seconds") for row in rows]
    clean = [value for value in values if value is not None]
    return {
        "count": len(clean),
        "avg_duration_seconds": average(values),
        "q1_duration_seconds": percentile(clean, 0.25),
        "p50_duration_seconds": percentile(clean, 0.50),
        "q3_duration_seconds": percentile(clean, 0.75),
        "p95_duration_seconds": percentile(clean, 0.95),
        "p99_duration_seconds": percentile(clean, 0.99),
        "max_duration_seconds": round(max(clean), 3) if clean else 0,
    }


def percentile_clean(values: list[int | float | None], percentile_value: float) -> float:
    return percentile(
        [value for value in values if value is not None], percentile_value
    )


def percentile(values: list[int | float], percentile_value: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, round((len(ordered) - 1) * percentile_value)),
    )
    return round(ordered[index], 3)


def numeric_value(row: dict[str, Any], key: str) -> int | float | None:
    """Parse a numeric projection while preserving exact integral values.

    Native HUGEINT values are already Python integers.  Treat a decimal token
    with no fractional component as an integer when it has no decimal/exponent
    marker; ordinary floating values remain floats for duration/rate
    calculations.
    """
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            if text[0] in {"+", "-"}:
                digits = text[1:]
            else:
                digits = text
            if digits.isdigit():
                return int(text)
            parsed_decimal = Decimal(text)
            return (
                int(parsed_decimal)
                if parsed_decimal == parsed_decimal.to_integral_value()
                and not any(mark in text.lower() for mark in (".", "e"))
                else float(parsed_decimal)
            )
        except (InvalidOperation, ValueError, OverflowError):
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def metric_value(row: dict[str, Any], key: str) -> int | None:
    """Return a nullable exact integer metric from a row."""
    value = numeric_value(row, key)
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def metric_sum(rows: list[dict[str, Any]], key: str) -> int | None:
    """Return a complete exact metric sum, preserving unknown as ``None``."""
    values = [metric_value(row, key) for row in rows]
    if not values:
        return 0
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _rounded_number(value: int | float) -> int | float:
    if isinstance(value, int):
        return value
    return round(value, 3)
