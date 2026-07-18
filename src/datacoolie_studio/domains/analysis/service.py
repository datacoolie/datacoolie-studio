from __future__ import annotations

from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.analysis.models import AnalysisResult
from datacoolie_studio.domains.analysis.python import analyze_python_function
from datacoolie_studio.domains.code_artifacts.indexer import ArtifactIndexError
from datacoolie_studio.domains.code_artifacts.service import read_code_artifact_function_source


def analyze_code_artifact_function(
    source: EnvironmentSource,
    function_path: str,
    *,
    context: dict[str, Any] | None = None,
) -> AnalysisResult:
    try:
        content, module_name, relative_path = read_code_artifact_function_source(source, function_path)
    except ArtifactIndexError as exc:
        result = AnalysisResult()
        result.diagnostics.append({
            "severity": "warning",
            "code": "artifact_function_unavailable",
            "message": str(exc),
        })
        return result
    result = analyze_python_function(
        content,
        function_path,
        module_name=module_name,
        source_path=relative_path,
        context=context,
    )
    for evidence in result.inputs:
        evidence.details["code_artifact_source_id"] = source.id
    return result
