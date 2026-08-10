from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum

PartitionValue = date | datetime


class PartitionGranularity(str, Enum):
    YEAR = "year"
    MONTH = "month"
    DAY = "day"
    HOUR = "hour"
    UNPARTITIONED = "unpartitioned"


@dataclass(frozen=True, order=True)
class ParsedPartition:
    partition_value: PartitionValue
    raw_partition_path: str
    partition_granularity: PartitionGranularity
    partition_format: str


@dataclass(frozen=True)
class PartitionLayout:
    partition_format: str | None
    granularity: PartitionGranularity

    def __post_init__(self) -> None:
        if (
            self.granularity is PartitionGranularity.UNPARTITIONED
            and self.partition_format is not None
        ):
            raise ValueError("Unpartitioned layouts cannot define a partition format")
        if (
            self.granularity is not PartitionGranularity.UNPARTITIONED
            and not self.partition_format
        ):
            raise ValueError("Partitioned layouts require a partition format")
        if (
            self.granularity is not PartitionGranularity.UNPARTITIONED
            and not _valid_partition_format(
                str(self.partition_format),
                self.granularity,
            )
        ):
            raise ValueError("Partition format violates the ordered token contract")

    def normalize(self, value: PartitionValue) -> PartitionValue:
        if self.granularity is PartitionGranularity.YEAR:
            return date(value.year, 1, 1)
        if self.granularity is PartitionGranularity.MONTH:
            return date(value.year, value.month, 1)
        if self.granularity is PartitionGranularity.DAY:
            return date(value.year, value.month, value.day)
        if self.granularity is PartitionGranularity.HOUR:
            value = partition_datetime(value)
            return datetime(value.year, value.month, value.day, value.hour)
        return value.date() if isinstance(value, datetime) else value

    def render(self, value: PartitionValue) -> str:
        if self.granularity is PartitionGranularity.UNPARTITIONED:
            return ""
        return self.normalize(value).strftime(str(self.partition_format))

    def values(
        self,
        from_partition: PartitionValue,
        to_partition: PartitionValue,
    ) -> tuple[PartitionValue, ...]:
        if self.granularity is PartitionGranularity.UNPARTITIONED:
            return (self.normalize(from_partition),)
        current = self.normalize(from_partition)
        end = self.normalize(to_partition)
        if current > end:
            return ()
        values: list[PartitionValue] = []
        while current <= end:
            values.append(current)
            current = _next_partition(current, self.granularity)
        return tuple(values)


@dataclass(frozen=True)
class _PartitionShape:
    expression: re.Pattern[str]
    granularity: PartitionGranularity
    token_names: tuple[str, ...]


_TOKEN_SPECS = (
    ("year", r"\d{4}", "%Y", PartitionGranularity.YEAR),
    ("month", r"\d{2}", "%m", PartitionGranularity.MONTH),
    ("day", r"\d{2}", "%d", PartitionGranularity.DAY),
    ("hour", r"\d{2}", "%H", PartitionGranularity.HOUR),
)


def _partition_shape(length: int) -> _PartitionShape:
    selected = _TOKEN_SPECS[:length]
    expression = r"\D*" + r"\D*".join(
        f"(?P<{name}>{width})" for name, width, _, _ in selected
    ) + r"\D*"
    return _PartitionShape(
        re.compile(expression),
        selected[-1][3],
        tuple(item[0] for item in selected),
    )


_PARTITION_SHAPES = tuple(
    _partition_shape(length) for length in range(len(_TOKEN_SPECS), 0, -1)
)


def parse_partition_path(raw_path: str, *, expected_format: str | None = None) -> ParsedPartition | None:
    """Infer ordered time tokens from one contract-compliant relative path."""

    normalized = str(raw_path).strip().strip("/\\").replace("\\", "/")
    if not normalized:
        return None

    for shape in _PARTITION_SHAPES:
        match = shape.expression.fullmatch(normalized)
        if match is None:
            continue
        partition_format = _partition_format(normalized, match, shape.token_names)
        if not _valid_partition_format(partition_format, shape.granularity):
            return None
        if expected_format is not None and partition_format != expected_format:
            continue
        parts = match.groupdict()
        try:
            value_parts = (
                int(parts["year"]),
                int(parts.get("month") or "1"),
                int(parts.get("day") or "1"),
            )
            partition_value = (
                datetime(*value_parts, int(parts["hour"]))
                if parts.get("hour") is not None
                else date(*value_parts)
            )
        except ValueError:
            return None
        return ParsedPartition(
            partition_value=partition_value,
            raw_partition_path=normalized,
            partition_granularity=shape.granularity,
            partition_format=partition_format,
        )
    return None


def _partition_format(
    path: str,
    match: re.Match[str],
    token_names: tuple[str, ...],
) -> str:
    by_name = {item[0]: item[2] for item in _TOKEN_SPECS}
    parts: list[str] = []
    cursor = 0
    for name in token_names:
        start, end = match.span(name)
        parts.extend((path[cursor:start], by_name[name]))
        cursor = end
    parts.append(path[cursor:])
    return "".join(parts)


def _valid_partition_format(
    partition_format: str,
    granularity: PartitionGranularity,
) -> bool:
    expected_tokens = {
        PartitionGranularity.YEAR: ("%Y",),
        PartitionGranularity.MONTH: ("%Y", "%m"),
        PartitionGranularity.DAY: ("%Y", "%m", "%d"),
        PartitionGranularity.HOUR: ("%Y", "%m", "%d", "%H"),
    }.get(granularity)
    if expected_tokens is None:
        return False
    if tuple(re.findall(r"%[YmdH]", partition_format)) != expected_tokens:
        return False
    literal = partition_format
    for token in expected_tokens:
        literal = literal.replace(token, "", 1)
    if (
        "%" in literal
        or "{" in literal
        or "}" in literal
        or any(character.isdigit() for character in literal)
    ):
        return False
    return all(
        segment
        and any(token in segment for token in expected_tokens)
        for segment in partition_format.split("/")
    )


def _next_partition(
    value: PartitionValue,
    granularity: PartitionGranularity,
) -> PartitionValue:
    if granularity is PartitionGranularity.YEAR:
        return date(value.year + 1, 1, 1)
    if granularity is PartitionGranularity.MONTH:
        if value.month == 12:
            return date(value.year + 1, 1, 1)
        return date(value.year, value.month + 1, 1)
    if granularity is PartitionGranularity.HOUR:
        return partition_datetime(value) + timedelta(hours=1)
    return value + timedelta(days=1)


def partition_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    return datetime(value.year, value.month, value.day)
