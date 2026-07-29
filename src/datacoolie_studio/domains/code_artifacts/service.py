from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import zipfile
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import CodeArtifactMaterialization, EnvironmentSource, utc_now
from datacoolie_studio.domains.environment_caches import invalidate_environment_derived_caches
from datacoolie_studio.domains.code_artifacts.indexer import (
    ArtifactIndexError,
    build_artifact_index,
    read_artifact_module,
)
from datacoolie_studio.domains.code_artifacts.materializer import (
    current_remote_snapshot,
    materialize_remote_artifact,
)
from datacoolie_studio.domains.credentials.store import CredentialSecretStore
from datacoolie_studio.domains.sources.service import record_source_validation
from datacoolie_studio.domains.sources.storage_binding import binding_from_source
from datacoolie_studio.domains.storage.factory import create_storage_adapter
from datacoolie_studio.domains.storage.inventory import (
    StorageInventoryRequest,
    inventory,
)
from datacoolie_studio.domains.storage.uri import require_local_path
from datacoolie_studio.domains.sync import service as sync


ANALYZER_VERSION = "artifact-index-v2"
CODE_MATERIALIZATION_SCHEMA_VERSION = "code-artifact.v1"


def validate_code_artifact(
    session: Session,
    source: EnvironmentSource,
    *,
    secret_store: CredentialSecretStore | None = None,
) -> dict[str, Any]:
    try:
        result = _validation_result(
            source,
            _code_validation_statistics(
                session,
                source,
                secret_store=secret_store,
            ),
        )
    except Exception as exc:
        result = _error_result(source, str(exc))
    return record_source_validation(session, source, result)


def refresh_code_artifact(
    session: Session,
    source: EnvironmentSource,
    *,
    job_type: str = "force_refresh",
    secret_store: CredentialSecretStore | None = None,
    prepared_artifact: tuple[str, dict[str, object]] | None = None,
) -> dict[str, Any]:
    materialization = code_artifact_materialization(session, source.id)
    job = sync.begin_sync_job(session, source, job_type)
    try:
        indexed = _build_index(
            session,
            source,
            secret_store=secret_store,
            prepared_artifact=prepared_artifact,
        )
        revision = _revision(source, indexed)
        revision_changed = materialization is None or not _same_revision_json(
            revision, materialization.source_revision_json
        )
        analyzer_changed = materialization is None or materialization.analyzer_version != ANALYZER_VERSION
        if revision_changed or analyzer_changed:
            revision_json = json.dumps(revision, sort_keys=True)
            fingerprint = _materialization_fingerprint(revision)
            if materialization is None:
                materialization = CodeArtifactMaterialization(
                    source_id=source.id,
                    source_revision_json=revision_json,
                    materialization_fingerprint=fingerprint,
                    artifact_manifest_json=json.dumps(indexed["manifest"], sort_keys=True),
                    module_index_json=json.dumps(indexed["modules"], sort_keys=True),
                    diagnostics_json=json.dumps(indexed["diagnostics"], sort_keys=True),
                    analyzer_version=ANALYZER_VERSION,
                )
                session.add(materialization)
            else:
                materialization.source_revision_json = revision_json
                materialization.materialization_fingerprint = fingerprint
                materialization.artifact_manifest_json = json.dumps(indexed["manifest"], sort_keys=True)
                materialization.module_index_json = json.dumps(indexed["modules"], sort_keys=True)
                materialization.diagnostics_json = json.dumps(indexed["diagnostics"], sort_keys=True)
                materialization.analyzer_version = ANALYZER_VERSION
                materialization.materialized_at = utc_now()
        if revision_changed or analyzer_changed:
            invalidate_environment_derived_caches(session, source.environment_id, structural=True)
        sync.record_source_observation(session, source=source, status="ok", revision=revision, error=None, checked_at=utc_now())
        sync.finish_sync_job(
            session,
            job,
            status="succeeded",
            message="Code artifact index refreshed",
            result={"status": "ok", "message": "Code artifact index refreshed", "revision": revision, "error": None},
        )
    except ArtifactIndexError as exc:
        error = {"code": "artifact_index_error", "message": str(exc)}
        sync.record_source_observation(session, source=source, status="error", revision=None, error=error, checked_at=utc_now())
        sync.finish_sync_job(
            session,
            job,
            status="failed",
            message=str(exc),
            result={"status": "error", "message": str(exc), "revision": None, "error": error},
        )
    return sync.source_sync_status(session, source, job)


def code_artifact_materialization(session: Session, source_id: int) -> CodeArtifactMaterialization | None:
    return session.scalar(
        select(CodeArtifactMaterialization).where(CodeArtifactMaterialization.source_id == source_id)
    )


def ensure_code_artifact_materialization(
    session: Session,
    source: EnvironmentSource,
    *,
    secret_store: CredentialSecretStore | None = None,
) -> CodeArtifactMaterialization | None:
    materialization, _error = ensure_code_artifact_materialization_result(
        session,
        source,
        secret_store=secret_store,
    )
    return materialization


def ensure_code_artifact_materialization_result(
    session: Session,
    source: EnvironmentSource,
    *,
    secret_store: CredentialSecretStore | None = None,
) -> tuple[CodeArtifactMaterialization | None, dict[str, Any] | None]:
    materialization = code_artifact_materialization(session, source.id)
    config = _source_config(source)
    artifact_type = str(config.get("artifact_type") or _infer_artifact_type(source.uri))
    if (
        artifact_type == "installed_distribution"
        and materialization is not None
        and materialization.analyzer_version == ANALYZER_VERSION
    ):
        return materialization, None
    if _storage_provider(source) != "local":
        prepared_artifact: tuple[str, dict[str, object]] | None = None
        if materialization is not None and materialization.analyzer_version == ANALYZER_VERSION:
            try:
                artifact_uri, current_stat = materialize_remote_artifact(
                    session,
                    source,
                    artifact_type,
                    secret_store=secret_store,
                )
                prepared_artifact = (artifact_uri, current_stat)
            except ArtifactIndexError:
                raise
            except Exception as exc:
                raise ArtifactIndexError(
                    "Remote code artifact could not be materialized"
                ) from exc
            if _stored_source_stat(materialization.source_revision_json) == current_stat:
                return materialization, None
        status = refresh_code_artifact(
            session,
            source,
            job_type="auto_refresh",
            secret_store=secret_store,
            prepared_artifact=prepared_artifact,
        )
        current = code_artifact_materialization(session, source.id)
        return current, _refresh_error(status)
    current_stat = sync.stat_source(source, include_content_hash=False)
    if materialization is not None:
        try:
            stored_revision = json.loads(materialization.source_revision_json)
        except json.JSONDecodeError:
            stored_revision = {}
        if stored_revision.get("source_stat") == current_stat:
            if materialization.analyzer_version == ANALYZER_VERSION:
                return materialization, None
    status = refresh_code_artifact(session, source, job_type="auto_refresh")
    current = code_artifact_materialization(session, source.id)
    return current, _refresh_error(status)


def _refresh_error(status: dict[str, Any] | None) -> dict[str, Any] | None:
    if not status or status.get("status") != "error":
        return None
    error = status.get("error")
    return error if isinstance(error, dict) else None


def _stored_source_stat(stored_revision_json: str) -> dict[str, Any] | None:
    try:
        stored_revision = json.loads(stored_revision_json)
    except json.JSONDecodeError:
        return None
    source_stat = stored_revision.get("source_stat")
    return source_stat if isinstance(source_stat, dict) else None


def read_code_artifact_function_source(
    source: EnvironmentSource,
    function_path: str,
    *,
    session: Session | None = None,
    secret_store: CredentialSecretStore | None = None,
) -> tuple[str, str, str]:
    config = _source_config(source)
    artifact_type = str(config.get("artifact_type") or _infer_artifact_type(source.uri))
    module_roots = config.get("module_roots") or []
    module_prefix = config.get("module_prefix")
    if not isinstance(module_roots, list):
        raise ArtifactIndexError("module_roots must be a list")
    artifact_uri = source.uri
    if (
        _storage_provider(source) != "local"
        and artifact_type != "installed_distribution"
    ):
        try:
            artifact_uri = current_remote_snapshot(source, artifact_type)
        except ArtifactIndexError:
            if session is None:
                raise
            artifact_uri, _ = materialize_remote_artifact(
                session,
                source,
                artifact_type,
                secret_store=secret_store,
            )
    module_name, separator, _ = function_path.rpartition(".")
    if not separator or not module_name:
        raise ArtifactIndexError(f"Python function must use a dotted module path: {function_path}")
    content, relative_path = read_artifact_module(
        artifact_uri,
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


def _build_index(
    session: Session,
    source: EnvironmentSource,
    *,
    secret_store: CredentialSecretStore | None = None,
    prepared_artifact: tuple[str, dict[str, object]] | None = None,
) -> dict[str, Any]:
    config = _source_config(source)
    artifact_type = str(config.get("artifact_type") or _infer_artifact_type(source.uri))
    module_roots = config.get("module_roots") or []
    module_prefix = config.get("module_prefix")
    if not isinstance(module_roots, list):
        raise ArtifactIndexError("module_roots must be a list")
    artifact_uri = source.uri
    source_stat = None
    if (
        _storage_provider(source) != "local"
        and artifact_type != "installed_distribution"
    ):
        try:
            if prepared_artifact is not None:
                artifact_uri, source_stat = prepared_artifact
            else:
                artifact_uri, source_stat = materialize_remote_artifact(
                    session,
                    source,
                    artifact_type,
                    secret_store=secret_store,
                )
        except ArtifactIndexError:
            raise
        except Exception as exc:
            raise ArtifactIndexError(
                "Remote code artifact could not be materialized"
            ) from exc
    indexed = build_artifact_index(
        artifact_uri,
        artifact_type,
        [str(value) for value in module_roots],
        str(module_prefix) if module_prefix else None,
    )
    indexed["uri"] = source.uri
    indexed["_source_stat"] = source_stat
    return indexed


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
    if lowered.endswith(".py"):
        return "python_file"
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
        "provider": (
            "installed"
            if indexed["artifact_type"] == "installed_distribution"
            else _storage_provider(source)
        ),
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
            else indexed.get("_source_stat")
            or sync.stat_source(source, include_content_hash=False)
        ),
        **({"distribution_version": indexed["distribution_version"]} if indexed.get("distribution_version") else {}),
    }


def _same_revision_json(revision: dict[str, Any], stored_revision_json: str) -> bool:
    try:
        stored_revision = json.loads(stored_revision_json)
    except json.JSONDecodeError:
        return False
    return stored_revision == revision


def _materialization_fingerprint(revision: dict[str, Any]) -> str:
    payload = {
        "revision": revision,
        "analyzer_version": ANALYZER_VERSION,
        "schema_version": CODE_MATERIALIZATION_SCHEMA_VERSION,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validation_result(
    source: EnvironmentSource,
    record_counts: dict[str, int],
) -> dict[str, Any]:
    artifact_type = str(
        _source_config(source).get("artifact_type")
        or _infer_artifact_type(source.uri)
    )
    return {
        "source_id": source.id,
        "source_kind": "code",
        "status": "ok",
        "message": "Code artifact inventory scanned",
        "detected_provider": (
            "installed"
            if artifact_type == "installed_distribution"
            else _storage_provider(source)
        ),
        "detected_format": artifact_type,
        "record_counts": record_counts,
        "records_scanned": sum(record_counts.values()),
        "validated_at": utc_now(),
        "errors": [],
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


def _storage_provider(source: EnvironmentSource) -> str:
    return str(getattr(source, "storage_provider", None) or "local")


def _code_validation_statistics(
    session: Session,
    source: EnvironmentSource,
    *,
    secret_store: CredentialSecretStore | None,
) -> dict[str, int]:
    artifact_type = str(
        _source_config(source).get("artifact_type")
        or _infer_artifact_type(source.uri)
    )
    if artifact_type == "installed_distribution":
        indexed = _build_index(session, source, secret_store=secret_store)
        return {
            "python_files": int(indexed["manifest"].get("python_files", 0)),
        }

    if _storage_provider(source) == "local":
        return _local_code_validation_statistics(
            require_local_path(source.uri),
            artifact_type,
        )

    adapter = create_storage_adapter(
        binding_from_source(source),
        uri=source.uri,
        session=session,
        secret_store=secret_store,
    )
    if artifact_type == "directory":
        observed = inventory(
            adapter,
            StorageInventoryRequest(
                uri=source.uri,
                purpose="validate",
                recursive=True,
                object_types=frozenset({"file"}),
                suffixes=frozenset({".py"}),
            ),
        )
        return {"python_files": len(observed.files)}

    with adapter.open_read(source.uri) as handle:
        handle.read(1)
    return {
        "python_files" if artifact_type == "python_file" else "artifact_files": 1,
    }


def _local_code_validation_statistics(
    path: Path,
    artifact_type: str,
) -> dict[str, int]:
    if artifact_type == "directory":
        if not path.is_dir():
            raise ArtifactIndexError(f"Code artifact directory not found: {path}")
        return {
            "python_files": sum(
                1 for item in path.rglob("*.py") if item.is_file()
            )
        }
    if not path.is_file():
        raise ArtifactIndexError(f"Code artifact file not found: {path}")
    if artifact_type == "python_file":
        return {"python_files": 1}
    if artifact_type in {"zip", "wheel"}:
        try:
            with zipfile.ZipFile(path) as archive:
                return {
                    "python_files": sum(
                        1
                        for name in archive.namelist()
                        if name.endswith(".py") and not name.endswith("/")
                    )
                }
        except (OSError, zipfile.BadZipFile) as exc:
            raise ArtifactIndexError(
                f"Code artifact archive is not readable: {path}"
            ) from exc
    return {"artifact_files": 1}
