from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from sqlglot import errors, exp, parse, parse_one

from datacoolie_studio.domains.analysis.models import AnalysisResult, InputEvidence, SourceLocation


SQL_DIALECT_ALIASES = {
    "mssql": "tsql",
    "sql_server": "tsql",
    "sqlserver": "tsql",
    "azure_sql": "tsql",
    "azure_synapse": "tsql",
    "fabric": "tsql",
    "synapse": "tsql",
    "postgresql": "postgres",
    "mariadb": "mysql",
}
SQL_DIALECT_FALLBACKS = ("tsql", "mysql", "postgres", "oracle", "sqlite", "spark")


def normalize_sql_dialect(dialect: str | None) -> str | None:
    value = str(dialect or "").strip().lower()
    if not value:
        return None
    normalized = value.replace("-", "_").replace(" ", "_")
    return SQL_DIALECT_ALIASES.get(normalized, normalized)


def sql_dialect_for_source(source: Mapping[str, Any] | None) -> str | None:
    """Return the SQLGlot dialect declared by a metadata source, when known."""

    if not isinstance(source, Mapping):
        return None
    configure = source.get("configure")
    candidates = [source.get("sql_dialect"), source.get("dialect"), source.get("database_type")]
    if isinstance(configure, Mapping):
        candidates.extend([
            configure.get("sql_dialect"),
            configure.get("dialect"),
            configure.get("database_type"),
        ])
    for candidate in candidates:
        normalized = normalize_sql_dialect(candidate if isinstance(candidate, str) else None)
        if normalized:
            return normalized
    return None


def parse_sql_one(
    sql: str,
    *,
    dialect: str | None = None,
) -> tuple[exp.Expression, str | None]:
    last_error: Exception | None = None
    for candidate in _dialect_candidates(dialect):
        try:
            return parse_one(sql, read=candidate), candidate
        except (errors.ParseError, errors.TokenError, ValueError, TypeError) as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ValueError("SQL could not be parsed")


def parse_sql_statements(
    sql: str,
    *,
    dialect: str | None = None,
) -> tuple[list[exp.Expression], str | None]:
    last_error: Exception | None = None
    for candidate in _dialect_candidates(dialect):
        try:
            expressions = [expression for expression in parse(sql, read=candidate) if expression is not None]
            if expressions:
                return expressions, candidate
        except (errors.ParseError, errors.TokenError, ValueError, TypeError) as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ValueError("SQL could not be parsed")


def _dialect_candidates(dialect: str | None) -> tuple[str | None, ...]:
    normalized = normalize_sql_dialect(dialect)
    if normalized:
        return (normalized,)
    return (None, *SQL_DIALECT_FALLBACKS)


def analyze_sql(sql: str, dialect: str | None = None) -> AnalysisResult:
    result = AnalysisResult()
    if not sql.strip():
        return result
    try:
        expression, used_dialect = parse_sql_one(sql, dialect=dialect)
    except (errors.ParseError, errors.TokenError, ValueError, TypeError) as exc:
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
        details = {
            "match_precision": "exact_reference" if location else "location_only",
            "access_semantics": "direct_sql",
            "identifier_parts": parts,
            "qualification_level": _qualification_level(parts),
        }
        if used_dialect:
            details["sql_dialect"] = used_dialect
        result.inputs.append(InputEvidence(
            kind="table",
            value=".".join(parts),
            provenance="sql",
            catalog=catalog,
            schema_name=database_or_schema,
            table=name,
            sql=sql,
            location=location,
            details=details,
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


def _qualification_level(parts: list[str]) -> str:
    if len(parts) >= 3:
        return "fully_qualified"
    if len(parts) >= 2:
        return "schema_table"
    return "table"


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
