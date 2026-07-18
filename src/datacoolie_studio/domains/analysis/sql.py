from __future__ import annotations

import re
from typing import Any

from sqlglot import errors, exp, parse_one

from datacoolie_studio.domains.analysis.models import AnalysisResult, InputEvidence, SourceLocation


def analyze_sql(sql: str, dialect: str | None = None) -> AnalysisResult:
    result = AnalysisResult()
    if not sql.strip():
        return result
    try:
        expression = parse_one(sql, read=dialect)
    except (errors.ParseError, ValueError) as exc:
        result.diagnostics.append({
            "severity": "warning",
            "code": "sql_parse_error",
            "message": str(exc),
        })
        return result

    cte_names = {
        _normalize_name(cte.alias_or_name)
        for cte in expression.find_all(exp.CTE)
        if cte.alias_or_name
    }
    search_start = 0
    for table in expression.find_all(exp.Table):
        name = table.name
        if not name or (_normalize_name(name) in cte_names and not table.db and not table.catalog):
            continue
        catalog = _identifier_text(table.args.get("catalog"))
        database_or_schema = _identifier_text(table.args.get("db"))
        parts = [part for part in (catalog, database_or_schema, name) if part]
        location, search_start = _table_location(sql, parts, search_start)
        result.inputs.append(InputEvidence(
            kind="table",
            value=".".join(parts),
            provenance="sql",
            catalog=catalog,
            schema_name=database_or_schema,
            table=name,
            sql=sql,
            location=location,
            details={"match_precision": "exact_reference"} if location else {},
        ))
    return result


def _identifier_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, exp.Identifier):
        return value.name
    text = str(value).strip()
    return text or None


def _normalize_name(value: str) -> str:
    return value.strip().strip('"`[]').lower()


def _table_location(sql: str, parts: list[str], search_start: int) -> tuple[SourceLocation | None, int]:
    if not parts:
        return None, search_start
    pattern = r"\s*\.\s*".join(_quoted_identifier_pattern(part) for part in parts)
    matcher = re.compile(rf"(?<![\w$]){pattern}(?![\w$])", re.IGNORECASE)
    match = matcher.search(sql, pos=search_start)
    if match is None:
        match = matcher.search(sql)
    if match is None:
        return None, search_start
    return _offset_location(sql, match.start(), match.end()), match.end()


def _quoted_identifier_pattern(value: str) -> str:
    escaped = re.escape(value)
    return rf"(?:{escaped}|\"{escaped}\"|`{escaped}`|\[{escaped}\])"


def _offset_location(sql: str, start: int, end: int) -> SourceLocation:
    start_line, start_column = _line_column(sql, start)
    end_line, end_column = _line_column(sql, end)
    return SourceLocation(
        line=start_line,
        column=start_column,
        end_line=end_line,
        end_column=end_column,
        coordinate_space="query_source",
    )


def _line_column(text: str, offset: int) -> tuple[int, int]:
    line = text.count("\n", 0, offset) + 1
    line_start = text.rfind("\n", 0, offset) + 1
    return line, offset - line_start
