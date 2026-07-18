from __future__ import annotations

from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.code_artifacts.indexer import ArtifactIndexError
from datacoolie_studio.domains.code_artifacts.service import (
    extract_python_function_source,
    read_code_artifact_function_source,
)


def build_reference_occurrence_source(
    occurrence: dict[str, Any],
    consumer_asset: dict[str, Any] | None,
    code_artifacts: list[EnvironmentSource],
) -> dict[str, Any]:
    observations = [item for item in occurrence.get("observations") or [] if isinstance(item, dict)]
    provenance = str(occurrence.get("provenance") or "")
    views: list[dict[str, Any]] = []
    diagnostics: list[dict[str, str]] = []

    if provenance in {"python", "python_sql"}:
        python_view, python_diagnostics = _python_view(occurrence, observations, code_artifacts)
        diagnostics.extend(python_diagnostics)
        if python_view is not None:
            views.append(python_view)

    if provenance in {"sql", "python_sql"}:
        sql_view = _sql_view(occurrence, observations, consumer_asset, provenance)
        if sql_view is not None:
            views.append(sql_view)
        elif provenance == "sql":
            diagnostics.append({
                "severity": "info",
                "code": "sql_source_unavailable",
                "message": "The SQL text used for this detection is no longer available.",
            })

    return {
        "occurrence_id": str(occurrence.get("id") or ""),
        "consumer_asset_id": str(occurrence.get("consumer_asset_id") or ""),
        "views": views,
        "diagnostics": diagnostics,
    }


def _python_view(
    occurrence: dict[str, Any],
    observations: list[dict[str, Any]],
    code_artifacts: list[EnvironmentSource],
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    location = _source_location(occurrence, observations)
    function_path = _string(location.get("function_path"))
    source_id = _artifact_source_id(observations)
    diagnostics: list[dict[str, str]] = []
    if not function_path:
        return None, [{
            "severity": "info",
            "code": "python_source_locator_missing",
            "message": "The analyzer did not retain a Python function locator for this occurrence.",
        }]
    artifacts = [item for item in code_artifacts if item.enabled and (source_id is None or item.id == source_id)]
    for artifact in artifacts:
        try:
            content, module_name, relative_path = read_code_artifact_function_source(artifact, function_path)
            source, _, _ = extract_python_function_source(content, function_path)
        except ArtifactIndexError as exc:
            diagnostics.append({
                "severity": "info",
                "code": "python_source_unavailable",
                "message": str(exc),
            })
            continue
        return {
            "id": "consumer_source",
            "label": "Python source",
            "language": "python",
            "content": source,
            "path": relative_path,
            "function_path": function_path,
            "module_name": module_name,
            "matches": _python_source_matches(source, occurrence, location),
        }, diagnostics
    if source_id is not None and not artifacts:
        diagnostics.append({
            "severity": "info",
            "code": "code_artifact_missing",
            "message": "The code artifact used during analysis is no longer enabled in this environment.",
        })
    return None, diagnostics or [{
        "severity": "info",
        "code": "python_source_unavailable",
        "message": "The containing Python function could not be resolved from enabled code artifacts.",
    }]


def _sql_view(
    occurrence: dict[str, Any],
    observations: list[dict[str, Any]],
    consumer_asset: dict[str, Any] | None,
    provenance: str,
) -> dict[str, Any] | None:
    observation = next((item for item in observations if _string(item.get("sql"))), None)
    content = _string(observation.get("sql")) if observation else _string((consumer_asset or {}).get("query"))
    if not content:
        return None
    details = observation.get("details") if observation and isinstance(observation.get("details"), dict) else {}
    if provenance == "python_sql":
        location = details.get("resolved_sql_location") if isinstance(details.get("resolved_sql_location"), dict) else None
    else:
        location = _source_location(occurrence, observations)
    matches = [_match_from_location(location, "exact_reference")] if location else _find_reference_matches(content, str(occurrence.get("raw_value") or ""))
    return {
        "id": "evaluated_sql" if provenance == "python_sql" else "query_source",
        "label": "Evaluated SQL" if provenance == "python_sql" else "SQL query",
        "language": "sql",
        "content": content,
        "matches": matches,
    }


def _source_location(occurrence: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    value = occurrence.get("source_location")
    if isinstance(value, dict):
        return value
    for observation in observations:
        location = observation.get("location")
        if isinstance(location, dict):
            return location
    return {}


def _artifact_source_id(observations: list[dict[str, Any]]) -> int | None:
    for observation in observations:
        details = observation.get("details")
        value = details.get("code_artifact_source_id") if isinstance(details, dict) else None
        if isinstance(value, int):
            return value
    return None


def _match_from_location(location: dict[str, Any], precision: str) -> dict[str, Any]:
    return {
        "line": int(location.get("line") or 1),
        "column": int(location.get("column") or 0),
        "end_line": int(location.get("end_line") or location.get("line") or 1),
        "end_column": int(location.get("end_column") or location.get("column") or 0),
        "precision": precision,
    }


def _find_reference_matches(content: str, value: str) -> list[dict[str, Any]]:
    normalized = value.strip()
    if not normalized:
        return []
    lowered = content.lower()
    needle = normalized.lower()
    matches: list[dict[str, Any]] = []
    start = 0
    while True:
        index = lowered.find(needle, start)
        if index < 0:
            return matches
        end = index + len(needle)
        start_line, start_column = _line_column(content, index)
        end_line, end_column = _line_column(content, end)
        matches.append({
            "line": start_line,
            "column": start_column,
            "end_line": end_line,
            "end_column": end_column,
            "precision": "exact_reference",
        })
        start = end


def _python_source_matches(
    content: str,
    occurrence: dict[str, Any],
    location: dict[str, Any],
) -> list[dict[str, Any]]:
    exact_matches = _find_reference_matches(content, str(occurrence.get("raw_value") or ""))
    if exact_matches:
        return exact_matches
    return [_match_from_location(location, "detection_expression")] if location else []


def _line_column(content: str, offset: int) -> tuple[int, int]:
    line = content.count("\n", 0, offset) + 1
    line_start = content.rfind("\n", 0, offset) + 1
    return line, offset - line_start


def _string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
