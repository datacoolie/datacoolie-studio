from __future__ import annotations

import ast
import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import CodeArtifactSnapshot, EnvironmentSource, utc_now
from datacoolie_studio.domains.environment_caches import invalidate_environment_derived_caches
from datacoolie_studio.domains.code_artifacts.indexer import (
    ArtifactIndexError,
    build_artifact_index,
    read_artifact_module,
)
from datacoolie_studio.domains.sources.service import record_source_validation
from datacoolie_studio.domains.sync import service as sync


ANALYZER_VERSION = "artifact-index-v1"


def validate_code_artifact(session: Session, source: EnvironmentSource) -> dict[str, Any]:
    try:
        indexed = _build_index(source)
        status = "warning" if indexed["diagnostics"] else "ok"
        result = _validation_result(source, indexed, status)
    except ArtifactIndexError as exc:
        result = _error_result(source, str(exc))
    record_source_validation(session, source, result)
    return result


def refresh_code_artifact(
    session: Session,
    source: EnvironmentSource,
    *,
    job_type: str = "force_refresh",
) -> dict[str, Any]:
    latest = latest_code_artifact_snapshot(session, source.id)
    job = sync.begin_sync_job(session, source, job_type)
    try:
        indexed = _build_index(source)
        revision = _revision(source, indexed)
        revision_changed = latest is None or not _same_revision_json(revision, latest.source_revision_json)
        analyzer_changed = latest is None or latest.analyzer_version != ANALYZER_VERSION
        if revision_changed or analyzer_changed:
            session.add(CodeArtifactSnapshot(
                source_id=source.id,
                source_revision_json=json.dumps(revision, sort_keys=True),
                artifact_manifest_json=json.dumps(indexed["manifest"], sort_keys=True),
                module_index_json=json.dumps(indexed["modules"], sort_keys=True),
                diagnostics_json=json.dumps(indexed["diagnostics"], sort_keys=True),
                analyzer_version=ANALYZER_VERSION,
            ))
        if revision_changed or analyzer_changed:
            invalidate_environment_derived_caches(session, source.environment_id, structural=True)
        sync.record_source_revision(session, source=source, status="ok", revision=revision, error=None, checked_at=utc_now())
        sync.finish_sync_job(
            session,
            job,
            status="succeeded",
            message="Code artifact index refreshed",
            result={"status": "ok", "message": "Code artifact index refreshed", "revision": revision, "error": None},
        )
        validation_status = "warning" if indexed["diagnostics"] else "ok"
        record_source_validation(
            session,
            source,
            _validation_result(source, indexed, validation_status),
        )
    except ArtifactIndexError as exc:
        error = {"code": "artifact_index_error", "message": str(exc)}
        sync.record_source_revision(session, source=source, status="error", revision=None, error=error, checked_at=utc_now())
        sync.finish_sync_job(
            session,
            job,
            status="failed",
            message=str(exc),
            result={"status": "error", "message": str(exc), "revision": None, "error": error},
        )
        record_source_validation(session, source, _error_result(source, str(exc)))
    return sync.source_sync_status(session, source)


def latest_code_artifact_snapshot(session: Session, source_id: int) -> CodeArtifactSnapshot | None:
    return session.scalars(
        select(CodeArtifactSnapshot)
        .where(CodeArtifactSnapshot.source_id == source_id)
        .order_by(CodeArtifactSnapshot.created_at.desc(), CodeArtifactSnapshot.id.desc())
    ).first()


def ensure_code_artifact_snapshot(
    session: Session,
    source: EnvironmentSource,
) -> CodeArtifactSnapshot | None:
    latest = latest_code_artifact_snapshot(session, source.id)
    config = _source_config(source)
    artifact_type = str(config.get("artifact_type") or _infer_artifact_type(source.uri))
    if artifact_type == "installed_distribution" and latest is not None:
        return latest
    current_stat = sync.stat_source(source, include_content_hash=False)
    if latest is not None:
        try:
            stored_revision = json.loads(latest.source_revision_json)
        except json.JSONDecodeError:
            stored_revision = {}
        if stored_revision.get("source_stat") == current_stat:
            return latest
    refresh_code_artifact(session, source, job_type="auto_refresh")
    return latest_code_artifact_snapshot(session, source.id)


def read_code_artifact_function_source(
    source: EnvironmentSource,
    function_path: str,
) -> tuple[str, str, str]:
    config = _source_config(source)
    artifact_type = str(config.get("artifact_type") or _infer_artifact_type(source.uri))
    module_roots = config.get("module_roots") or []
    module_prefix = config.get("module_prefix")
    if not isinstance(module_roots, list):
        raise ArtifactIndexError("module_roots must be a list")
    module_name, separator, _ = function_path.rpartition(".")
    if not separator or not module_name:
        raise ArtifactIndexError(f"Python function must use a dotted module path: {function_path}")
    content, relative_path = read_artifact_module(
        source.uri,
        artifact_type,
        module_name,
        [str(value) for value in module_roots],
        str(module_prefix) if module_prefix else None,
    )
    return content, module_name, relative_path


def extract_python_function_source(content: str, function_path: str) -> tuple[str, int, int]:
    function_name = function_path.rsplit(".", 1)[-1].strip()
    if not function_name:
        raise ArtifactIndexError(f"Python function must use a dotted module path: {function_path}")
    try:
        module = ast.parse(content)
    except SyntaxError as exc:
        raise ArtifactIndexError(f"Python source cannot be parsed: {exc}") from exc

    candidates = [
        node
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
    ]
    if not candidates:
        raise ArtifactIndexError(f"Python function not found in module source: {function_name}")

    node = candidates[0]
    decorator_lines = [decorator.lineno for decorator in node.decorator_list]
    start_line = min([node.lineno, *decorator_lines])
    end_line = int(getattr(node, "end_lineno", None) or node.lineno)
    lines = content.splitlines()
    return "\n".join(lines[start_line - 1:end_line]), start_line, end_line


def _build_index(source: EnvironmentSource) -> dict[str, Any]:
    config = _source_config(source)
    artifact_type = str(config.get("artifact_type") or _infer_artifact_type(source.uri))
    module_roots = config.get("module_roots") or []
    module_prefix = config.get("module_prefix")
    if not isinstance(module_roots, list):
        raise ArtifactIndexError("module_roots must be a list")
    return build_artifact_index(
        source.uri,
        artifact_type,
        [str(value) for value in module_roots],
        str(module_prefix) if module_prefix else None,
    )


def _source_config(source: EnvironmentSource) -> dict[str, Any]:
    if not source.source_config_json:
        return {}
    try:
        loaded = json.loads(source.source_config_json)
    except json.JSONDecodeError as exc:
        raise ArtifactIndexError("Invalid code artifact source configuration") from exc
    return loaded if isinstance(loaded, dict) else {}


def _infer_artifact_type(uri: str) -> str:
    lowered = uri.lower()
    if lowered.endswith(".whl"):
        return "wheel"
    if lowered.endswith(".zip"):
        return "zip"
    return "directory"


def _revision(source: EnvironmentSource, indexed: dict[str, Any]) -> dict[str, Any]:
    fingerprint_payload = {
        "artifact_type": indexed["artifact_type"],
        "uri": indexed["uri"],
        "files": [(item["path"], item["size"], item["sha256"]) for item in indexed["files"]],
        "modules": sorted(indexed["modules"]),
        "distribution_version": indexed.get("distribution_version"),
        "analyzer_version": ANALYZER_VERSION,
    }
    fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "provider": "installed" if indexed["artifact_type"] == "installed_distribution" else "local",
        "source_kind": "code",
        "artifact_type": indexed["artifact_type"],
        "uri": indexed["uri"],
        "fingerprint": fingerprint,
        "python_files": indexed["manifest"]["python_files"],
        "modules": indexed["manifest"]["modules"],
        "total_size": indexed["manifest"]["total_size"],
        "source_stat": (
            None
            if indexed["artifact_type"] == "installed_distribution"
            else sync.stat_source(source, include_content_hash=False)
        ),
        **({"distribution_version": indexed["distribution_version"]} if indexed.get("distribution_version") else {}),
    }


def _same_revision_json(revision: dict[str, Any], stored_revision_json: str) -> bool:
    try:
        stored_revision = json.loads(stored_revision_json)
    except json.JSONDecodeError:
        return False
    return stored_revision == revision


def _validation_result(source: EnvironmentSource, indexed: dict[str, Any], status: str) -> dict[str, Any]:
    counts = {
        "python_files": indexed["manifest"]["python_files"],
        "modules": indexed["manifest"]["modules"],
    }
    return {
        "source_id": source.id,
        "source_kind": "code",
        "status": status,
        "message": "Code artifact is readable and indexable" if status == "ok" else "Code artifact is readable with warnings",
        "detected_provider": "installed" if indexed["artifact_type"] == "installed_distribution" else "local",
        "detected_format": indexed["artifact_type"],
        "record_counts": counts,
        "records_scanned": counts["python_files"],
        "validated_at": utc_now(),
        "errors": indexed["diagnostics"],
    }


def _error_result(source: EnvironmentSource, message: str) -> dict[str, Any]:
    return {
        "source_id": source.id,
        "source_kind": "code",
        "status": "error",
        "message": message,
        "detected_provider": None,
        "detected_format": None,
        "record_counts": {},
        "records_scanned": 0,
        "validated_at": utc_now(),
        "errors": [{"message": message}],
    }
