from __future__ import annotations

from datacoolie_studio.domains.analytics.errors import AnalyticsSchemaIncompatibleError


_INTEGRAL_TYPES = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "UHUGEINT",
    }
)
_NUMERIC_TYPES = _INTEGRAL_TYPES | {"FLOAT", "DOUBLE"}


def common_numeric_source_type(column: str, left: str, right: str) -> str:
    left_type = left.upper()
    right_type = right.upper()
    if left_type == right_type:
        return left
    if {left_type, right_type} <= _NUMERIC_TYPES:
        if "DOUBLE" in {left_type, right_type} or "FLOAT" in {left_type, right_type}:
            return "DOUBLE"
        if "UHUGEINT" in {left_type, right_type}:
            return "UHUGEINT"
        if "HUGEINT" in {left_type, right_type}:
            return "HUGEINT"
        if "UBIGINT" in {left_type, right_type}:
            return "UBIGINT"
        return "BIGINT"
    raise AnalyticsSchemaIncompatibleError(
        f"Column {column!r} has incompatible source datatypes {left} and {right}"
    )
