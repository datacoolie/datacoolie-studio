from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import CodeArtifactSnapshot, EnvironmentSource, utc_now
from datacoolie_studio.domains.code_artifacts.indexer import (
    ArtifactIndexError,
    build_artifact_index,
    read_artifact_module,
)
from datacoolie_studio.domains.sync import service as sync


ANALYZER_VERSION = "artifact-index-v1"


def validate_code_artifact(session: Session, source: EnvironmentSource) -> dict[str, Any]:
    try:
        indexed = _build_index(source)
        status = "warning" if indexed["diagnostics"] else "ok"
        result = _validation_result(source, indexed, status)
    except ArtifactIndexError as exc:
        result = _error_result(source, str(exc))
    _save_read_check(session, source, result)
    return result


def refresh_code_artifact(session: Session, source: EnvironmentSource) -> dict[str, Any]:
    job = sync.begin_sync_job(session, source, "force_refresh")
    try:
        indexed = _build_index(source)
        revision = _revision(source, indexed)
        snapshot = CodeArtifactSnapshot(
            source_id=source.id,
            source_revision_json=json.dumps(revision, sort_keys=True),
            artifact_manifest_json=json.dumps(indexed["manifest"], sort_keys=True),
            module_index_json=json.dumps(indexed["modules"], sort_keys=True),
            diagnostics_json=json.dumps(indexed["diagnostics"], sort_keys=True),
            analyzer_version=ANALYZER_VERSION,
        )
        session.add(snapshot)
        sync.record_source_revision(session, source=source, status="ok", revision=revision, error=None, checked_at=utc_now())
        sync.finish_sync_job(
            session,
            job,
            status="succeeded",
            message="Code artifact index refreshed",
            result={"status": "ok", "message": "Code artifact index refreshed", "revision": revision, "error": None},
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
    refresh_code_artifact(session, source)
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


def _save_read_check(session: Session, source: EnvironmentSource, result: dict[str, Any]) -> None:
    source.read_check_status = str(result["status"])
    source.read_checked_at = result["validated_at"]
    stored = {**result, "validated_at": result["validated_at"].isoformat()}
    source.read_check_result_json = json.dumps(stored, sort_keys=True)
    session.commit()
