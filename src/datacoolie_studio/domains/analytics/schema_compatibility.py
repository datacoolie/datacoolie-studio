"""Schema compatibility rules for source log artifacts and analytics tables."""

from __future__ import annotations

from dataclasses import dataclass


INTEGRAL_TYPES = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
    }
)
FLOATING_TYPES = frozenset({"FLOAT", "DOUBLE"})


@dataclass(frozen=True)
class TypeCompatibility:
    """The target-aware handling required for one source column."""

    target_type: str
    source_type: str
    normalized_type: str
    requires_value_validation: bool = False
    requires_projection_cast: bool = False


def normalize_type(data_type: str) -> str:
    value = str(data_type).strip().upper()
    if value == "TIMESTAMP WITH TIME ZONE":
        return "TIMESTAMPTZ"
    return value


def types_match(actual_type: str, expected_type: str) -> bool:
    actual = normalize_type(actual_type)
    expected = normalize_type(expected_type)
    return actual == expected or actual.startswith(f"{expected}(")


def classify_type(target_type: str, source_type: str) -> TypeCompatibility:
    """Classify one source type against its canonical analytics target.

    The only narrowing conversion allowed here is a value-validated conversion
    to BIGINT. Value validation happens against the actual Parquet file before
    the projection is executed.
    """

    target = normalize_type(target_type)
    source = normalize_type(source_type)
    if types_match(source, target):
        return TypeCompatibility(target, source, target)

    if target == "BIGINT" and source in FLOATING_TYPES:
        return TypeCompatibility(
            target,
            source,
            target,
            requires_value_validation=True,
            requires_projection_cast=True,
        )

    if target == "BIGINT" and source == "HUGEINT":
        return TypeCompatibility(
            target,
            source,
            target,
            requires_value_validation=True,
            requires_projection_cast=True,
        )

    if target == "DOUBLE" and source in INTEGRAL_TYPES | FLOATING_TYPES:
        return TypeCompatibility(target, source, target)

    if target == "TIMESTAMPTZ" and source.startswith("TIMESTAMP"):
        return TypeCompatibility(target, source, target)

    raise ValueError(f"source type {source} is not compatible with target type {target}")


def lossless_integer_predicate(quoted_column: str, source_type: str) -> str:
    """Return a DuckDB predicate for values safely representable as BIGINT."""

    source = normalize_type(source_type)
    if source in FLOATING_TYPES:
        return (
            f"NOT isfinite({quoted_column}) "
            f"OR {quoted_column} != floor({quoted_column}) "
            f"OR TRY_CAST({quoted_column} AS BIGINT) IS NULL"
        )
    if source == "HUGEINT":
        return f"TRY_CAST({quoted_column} AS BIGINT) IS NULL"
    raise ValueError(f"lossless BIGINT validation is not defined for {source}")


def normalized_cast_expression(
    quoted_column: str,
    target_type: str,
    *,
    alias: str,
) -> str:
    """Build a named SQL cast for a validated compatible source column."""

    target = normalize_type(target_type)
    return f"CAST({quoted_column} AS {target}) AS {alias}"
