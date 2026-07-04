from __future__ import annotations

from typing import Any

from sqlglot import errors, exp, parse_one

from datacoolie_studio.domains.analysis.models import AnalysisResult, InputEvidence


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
    seen: set[tuple[str | None, str | None, str | None, str]] = set()
    for table in expression.find_all(exp.Table):
        name = table.name
        if not name or (_normalize_name(name) in cte_names and not table.db and not table.catalog):
            continue
        catalog = _identifier_text(table.args.get("catalog"))
        database_or_schema = _identifier_text(table.args.get("db"))
        parts = [part for part in (catalog, database_or_schema, name) if part]
        key = (catalog, None, database_or_schema, name)
        if key in seen:
            continue
        seen.add(key)
        result.inputs.append(InputEvidence(
            kind="table",
            value=".".join(parts),
            provenance="sql",
            catalog=catalog,
            schema_name=database_or_schema,
            table=name,
            sql=sql,
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
