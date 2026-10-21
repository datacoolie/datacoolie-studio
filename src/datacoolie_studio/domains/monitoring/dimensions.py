"""Canonical missing members and lossless Monitoring dimension selection keys."""
from __future__ import annotations

from typing import Any

UNKNOWN_KEY = "__monitoring_unknown__"
VALUE_PREFIX = "__monitoring_value__:"
CONTEXT_PREFIX = "__monitoring_context__:"
DIMENSION_COLUMNS = frozenset({
    "operation_type", "status", "stage", "engine_name", "metadata_provider_name",
    "platform_name", "source_name", "destination_name", "source_connection_type",
    "destination_connection_type", "source_format", "destination_format",
    "source_table", "destination_table", "destination_load_type", "destination_operation_type",
})
# These columns also participate in exact declared-target investigation identity.
RAW_IDENTITY_DIMENSIONS = frozenset({"destination_name", "destination_connection_type", "destination_format", "destination_table", "source_name", "source_table"})
PROJECTED_DIMENSIONS = DIMENSION_COLUMNS - RAW_IDENTITY_DIMENSIONS - {"status"}
WHITESPACE = " \t\n\r\v\f\x1c\x1d\x1e\x1f\x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"


def dimension_key(value: Any) -> str:
    text = str(value).strip(WHITESPACE) if value is not None else ""
    if not text:
        return UNKNOWN_KEY
    if text == "all" or text == UNKNOWN_KEY or text.startswith((VALUE_PREFIX, CONTEXT_PREFIX)) or "|" in text:
        return VALUE_PREFIX + text.encode('utf-8').hex()
    return text


def dimension_label(key: str) -> str:
    if key == UNKNOWN_KEY:
        return "Unknown"
    if key.startswith(VALUE_PREFIX):
        try:
            return bytes.fromhex(key[len(VALUE_PREFIX):]).decode('utf-8')
        except (ValueError, UnicodeDecodeError):
            return key
    return f"{key} (value)" if key.lower() == 'unknown' else key


def dimension_text_sql(expression: str) -> str:
    # DuckDB's TRIM with a long Unicode character set is costly per row;
    # RE2 checks only the boundaries and preserves the same character policy.
    pattern = f"^[{WHITESPACE}]+|[{WHITESPACE}]+$".replace("'", "''")
    return f"NULLIF(regexp_replace(CAST({expression} AS VARCHAR), '{pattern}', '', 'g'), '')"


def known_dimension_sql(expression: str) -> str:
    """Exclude the missing member from distinct known-dimension metrics."""
    return f"NULLIF({expression}, '{UNKNOWN_KEY}')"


def dimension_key_sql(expression: str) -> str:
    text = dimension_text_sql(expression)
    return (f"CASE WHEN {text} IS NULL THEN '{UNKNOWN_KEY}' "
            f"WHEN {text} IN ('all', '{UNKNOWN_KEY}') OR starts_with({text}, '{VALUE_PREFIX}') OR starts_with({text}, '{CONTEXT_PREFIX}') OR contains({text}, '|') "
            f"THEN '{VALUE_PREFIX}' || lower(hex({text})) ELSE {text} END")


def dimension_predicate_sql(expression: str, selected: list[str]) -> tuple[str, list[Any]]:
    """Bound predicates compare raw normalized values; special keys never collide."""
    text = dimension_text_sql(expression)
    values, missing = [], UNKNOWN_KEY in selected
    for key in selected:
        if key == UNKNOWN_KEY:
            continue
        if key.startswith(VALUE_PREFIX):
            try:
                key = bytes.fromhex(key[len(VALUE_PREFIX):]).decode('utf-8')
            except (ValueError, UnicodeDecodeError):
                pass
        values.append(key)
    clauses = [f"{text} IS NULL"] if missing else []
    if values:
        clauses.append(f"{text} IN ({', '.join('?' for _ in values)})")
    return '(' + ' OR '.join(clauses) + ')', values
