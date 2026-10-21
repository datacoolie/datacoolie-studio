"""Read SQL and Python asset definitions for presentation."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session
from sqlglot import errors as sqlglot_errors

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.analysis.sql import parse_sql_statements, sql_dialect_for_source
from datacoolie_studio.domains.code_artifacts.indexer import ArtifactIndexError
from datacoolie_studio.domains.code_artifacts.service import (
    extract_python_function_source,
    read_code_artifact_function_source,
)
from datacoolie_studio.domains.assets.normalization import string_or_none


def read_asset_definition(
    asset: dict[str, Any],
    code_artifacts: list[EnvironmentSource],
    *,
    session: Session | None = None,
) -> dict[str, Any] | None:
    asset_type = str(asset.get("asset_type") or "")
    query = string_or_none(asset.get("query"))
    python_function = string_or_none(asset.get("python_function"))
    if asset_type == "sql_query" or query:
        return _sql_definition(query, dialect=sql_dialect_for_source(asset))
    if asset_type == "python_function" or python_function:
        return _python_definition(
            python_function, code_artifacts, session=session
        )
    return None

def describe_asset_definition(asset: dict[str, Any]) -> dict[str, Any] | None:
    asset_type = str(asset.get("asset_type") or "")
    query = string_or_none(asset.get("query"))
    python_function = string_or_none(asset.get("python_function"))
    if asset_type == "sql_query" or query:
        return {
            "kind": "sql_query", "language": "sql", "status": "available" if query else "empty",
            "title": "SQL query", "line_count": len(query.splitlines()) if query else 0,
        }
    if asset_type == "python_function" or python_function:
        return {
            "kind": "python_function", "language": "python", "status": "available" if python_function else "empty",
            "title": "Python function", "function_path": python_function, "line_count": 0,
        }
    return None

def _sql_definition(query: str | None, *, dialect: str | None = None) -> dict[str, Any]:
    raw = (query or "").strip()
    if not raw:
        return {
            "kind": "sql_query",
            "language": "sql",
            "status": "empty",
            "title": "SQL query",
            "raw": "",
            "formatted": "",
            "line_count": 0,
            "diagnostics": [_definition_diagnostic("info", "sql_query_empty", "SQL query metadata is empty.")],
        }

    diagnostics: list[dict[str, Any]] = []
    formatted = raw
    try:
        expressions, used_dialect = parse_sql_statements(raw, dialect=dialect)
        if expressions:
            formatted = ";\n\n".join(
                expression.sql(pretty=True, dialect=used_dialect)
                if used_dialect
                else expression.sql(pretty=True)
                for expression in expressions
            )
            if len(expressions) > 1:
                formatted = f"{formatted};"
    except (sqlglot_errors.ParseError, sqlglot_errors.TokenError, ValueError, TypeError) as exc:
        diagnostics.append(_definition_diagnostic("warning", "sql_format_failed", str(exc)))

    return {
        "kind": "sql_query",
        "language": "sql",
        "status": "available",
        "title": "SQL query",
        "raw": raw,
        "formatted": formatted,
        "line_count": _line_count(formatted),
        "diagnostics": diagnostics,
    }

def _python_definition(
    function_path: str | None,
    code_artifacts: list[EnvironmentSource],
    *,
    session: Session | None = None,
) -> dict[str, Any]:
    normalized_function_path = (function_path or "").strip()
    if not normalized_function_path:
        return {
            "kind": "python_function",
            "language": "python",
            "status": "empty",
            "title": "Python function",
            "function_path": None,
            "source": "",
            "line_count": 0,
            "diagnostics": [_definition_diagnostic("info", "python_function_empty", "Python function metadata is empty.")],
        }

    enabled_artifacts = [item for item in code_artifacts if bool(getattr(item, "enabled", True))]
    if not enabled_artifacts:
        return {
            "kind": "python_function",
            "language": "python",
            "status": "unavailable",
            "title": "Python function",
            "function_path": normalized_function_path,
            "source": "",
            "line_count": 0,
            "diagnostics": [_definition_diagnostic("info", "code_artifact_missing", "No enabled code artifact is linked to this environment.")],
        }

    diagnostics: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []
    for artifact in enabled_artifacts:
        try:
            content, module_name, relative_path = read_code_artifact_function_source(
                artifact, normalized_function_path, session=session
            )
            source, start_line, end_line = extract_python_function_source(content, normalized_function_path)
        except ArtifactIndexError as exc:
            diagnostics.append(_definition_diagnostic(
                "info",
                "python_source_not_found",
                str(exc),
                source_id=artifact.id,
                source_uri=artifact.uri,
            ))
            continue
        matches.append({
            "source_id": artifact.id,
            "source_uri": artifact.uri,
            "module_name": module_name,
            "relative_path": relative_path,
            "source": source,
            "start_line": start_line,
            "end_line": end_line,
        })

    if len(matches) == 1:
        match = matches[0]
        source = str(match["source"])
        return {
            "kind": "python_function",
            "language": "python",
            "status": "available",
            "title": "Python function",
            "function_path": normalized_function_path,
            "module_name": match["module_name"],
            "relative_path": match["relative_path"],
            "source_id": match["source_id"],
            "source_uri": match["source_uri"],
            "start_line": match["start_line"],
            "end_line": match["end_line"],
            "source": source,
            "line_count": _line_count(source),
            "diagnostics": diagnostics,
        }

    if len(matches) > 1:
        return {
            "kind": "python_function",
            "language": "python",
            "status": "ambiguous",
            "title": "Python function",
            "function_path": normalized_function_path,
            "source": "",
            "line_count": 0,
            "matches": [
                {
                    "source_id": match["source_id"],
                    "source_uri": match["source_uri"],
                    "module_name": match["module_name"],
                    "relative_path": match["relative_path"],
                    "start_line": match["start_line"],
                    "end_line": match["end_line"],
                }
                for match in matches
            ],
            "diagnostics": [
                _definition_diagnostic("warning", "python_source_ambiguous", "Python function resolves in multiple enabled code artifacts."),
                *diagnostics,
            ],
        }

    return {
        "kind": "python_function",
        "language": "python",
        "status": "unavailable",
        "title": "Python function",
        "function_path": normalized_function_path,
        "source": "",
        "line_count": 0,
        "diagnostics": diagnostics or [_definition_diagnostic("info", "python_source_unavailable", "Python source could not be resolved.")],
    }

def _definition_diagnostic(
    severity: str,
    code: str,
    message: str,
    **details: Any,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "details": {key: value for key, value in details.items() if value is not None},
    }

def _line_count(value: str) -> int:
    return len(value.splitlines()) if value else 0

__all__ = ["describe_asset_definition", "read_asset_definition"]
