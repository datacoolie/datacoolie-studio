from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum


class PartitionGranularity(str, Enum):
    YEAR = "year"
    MONTH = "month"
    DAY = "day"


@dataclass(frozen=True, order=True)
class ParsedPartition:
    partition_value: date
    raw_partition_path: str
    partition_granularity: PartitionGranularity
    partition_format: str


@dataclass(frozen=True)
class _PartitionPattern:
    expression: re.Pattern[str]
    granularity: PartitionGranularity
    format_template: str


_PATTERNS = (
    _PartitionPattern(re.compile(r"(?P<year>\d{4})"), PartitionGranularity.YEAR, "%Y"),
    _PartitionPattern(re.compile(r"(?P<year>\d{4})(?P<month>\d{2})"), PartitionGranularity.MONTH, "%Y%m"),
    _PartitionPattern(re.compile(r"(?P<year>\d{4})-(?P<month>\d{2})"), PartitionGranularity.MONTH, "%Y-%m"),
    _PartitionPattern(re.compile(r"(?P<year>\d{4})_(?P<month>\d{2})"), PartitionGranularity.MONTH, "%Y_%m"),
    _PartitionPattern(
        re.compile(r"(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})"),
        PartitionGranularity.DAY,
        "%Y%m%d",
    ),
    _PartitionPattern(
        re.compile(r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"),
        PartitionGranularity.DAY,
        "%Y-%m-%d",
    ),
    _PartitionPattern(
        re.compile(r"(?P<year>\d{4})_(?P<month>\d{2})_(?P<day>\d{2})"),
        PartitionGranularity.DAY,
        "%Y_%m_%d",
    ),
    _PartitionPattern(re.compile(r"(?P<year>\d{4})/(?P<month>\d{2})"), PartitionGranularity.MONTH, "%Y/%m"),
    _PartitionPattern(
        re.compile(r"(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})"),
        PartitionGranularity.DAY,
        "%Y/%m/%d",
    ),
)


def parse_partition_path(raw_path: str, *, expected_format: str | None = None) -> ParsedPartition | None:
    """Parse one relative partition path without guessing dates from surrounding text."""

    normalized = str(raw_path).strip().strip("/\\").replace("\\", "/")
    if not normalized:
        return None

    value_path, prefix_format = _partition_value_and_prefix(normalized)
    for pattern in _PATTERNS:
        match = pattern.expression.fullmatch(value_path)
        if match is None:
            continue
        partition_format = _apply_prefix_format(prefix_format, pattern.format_template)
        if expected_format is not None and partition_format != expected_format:
            continue
        parts = match.groupdict()
        try:
            partition_value = date(
                int(parts["year"]),
                int(parts.get("month") or "1"),
                int(parts.get("day") or "1"),
            )
        except ValueError:
            return None
        return ParsedPartition(
            partition_value=partition_value,
            raw_partition_path=normalized,
            partition_granularity=pattern.granularity,
            partition_format=partition_format,
        )
    return None


def _partition_value_and_prefix(path: str) -> tuple[str, tuple[str | None, ...]]:
    values: list[str] = []
    prefixes: list[str | None] = []
    for segment in path.split("/"):
        if "=" in segment:
            prefix, value = segment.rsplit("=", 1)
            if not prefix or not value:
                return path, ()
            prefixes.append(prefix)
            values.append(value)
        else:
            prefixes.append(None)
            values.append(segment)
    return "/".join(values), tuple(prefixes)


def _apply_prefix_format(prefixes: tuple[str | None, ...], value_format: str) -> str:
    if not prefixes or not any(prefixes):
        return value_format
    format_segments = value_format.split("/")
    if len(prefixes) != len(format_segments):
        return value_format
    return "/".join(
        f"{prefix}={format_segment}" if prefix else format_segment
        for prefix, format_segment in zip(prefixes, format_segments, strict=True)
    )
