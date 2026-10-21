from __future__ import annotations

from typing import Any


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def sql_string(value: Any) -> str:
    if value is None:
        return "NULL::VARCHAR"
    return "'" + str(value).replace("'", "''") + "'"


def sql_date(value: str | None) -> str:
    if not value:
        return "NULL::DATE"
    return f"DATE {sql_string(value[:10])}"


def sql_number(value: Any) -> str:
    return "NULL" if value is None else str(int(value))
