from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource, utc_now
from datacoolie_studio.domains.logs.source_config import resolve_log_source_paths
from datacoolie_studio.domains.storage.uri import (
    StorageProviderNotEnabled,
    join_uri,
    require_local_path,
    uri_basename,
)
from datacoolie_studio.domains.credentials.store import CredentialSecretStore
from datacoolie_studio.domains.sources.storage_binding import (
    binding_from_source,
    validate_and_normalize_binding,
)
from datacoolie_studio.domains.storage.errors import StorageError
from datacoolie_studio.domains.storage.concurrency import map_storage_io
from datacoolie_studio.domains.storage.factory import create_storage_adapter
from datacoolie_studio.domains.storage.inventory import (
    StorageInventoryRequest,
    inventory,
)
from datacoolie_studio.domains.storage.redaction import redact_storage_error


EMPTY_LOG_SOURCE_MESSAGE = "No ETL or system log files found"
METADATA_WRITE_BACK_PROVIDERS = frozenset(
    {"local", "s3", "minio", "adls", "onelake", "gcs", "dbfs"}
)


def validate_storage_connection(
    session: Session,
    *,
    uri: str,
    storage: dict | None,
    source_config: dict | None,
    secret_store: CredentialSecretStore,
) -> dict[str, object]:
    try:
        canonical_uri, binding = validate_and_normalize_binding(
            session,
            uri=uri,
            storage=storage,
            source_config=source_config,
        )
        adapter = create_storage_adapter(
            binding,
            uri=canonical_uri,
            session=session,
            secret_store=secret_store,
        )
        if binding.provider == "local":
            path = require_local_path(canonical_uri)
            if path.is_file():
                revision = adapter.stat(canonical_uri)
                return {
                    "status": "ok",
                    "provider": binding.provider,
                    "canonical_uri": revision.canonical_uri,
                    "object_type": "file",
                    "objects_scanned": 1,
                    "provider_revision": revision.provider_revision,
                    "metadata_write_back_supported": True,
                    "message": "Storage file is readable",
                }
            if not path.is_dir():
                raise FileNotFoundError(canonical_uri)
        observed = inventory(
            adapter,
            StorageInventoryRequest(
                uri=canonical_uri,
                purpose="probe",
                object_limit=(
                    1 if binding.provider in {"onelake", "dbfs"} else 100
                ),
                stop_after_match=binding.provider in {"onelake", "dbfs"},
            ),
        )
        return {
            "status": "ok",
            "provider": binding.provider,
            "canonical_uri": adapter.canonical_uri(canonical_uri),
            "object_type": "directory",
            "objects_scanned": len(observed.objects),
            "provider_revision": None,
            "metadata_write_back_supported": (
                binding.provider in METADATA_WRITE_BACK_PROVIDERS
            ),
            "message": (
                "Storage location is readable"
                if observed.completeness == "complete"
                else "Storage location is readable (bounded probe)"
            ),
        }
    except StorageError as exc:
        return {
            "status": "error",
            "provider": getattr(exc, "provider", None)
            or str((storage or {}).get("provider") or "unknown"),
            "message": str(exc),
            "error": {
                "code": exc.code,
                **(
                    {"install_command": exc.install_command}
                    if hasattr(exc, "install_command")
                    else {}
                ),
            },
        }
    except (OSError, ValueError) as exc:
        detail = redact_storage_error(str(exc))
        return {
            "status": "error",
            "provider": str((storage or {}).get("provider") or "local"),
            "message": (
                f"Storage location is not readable: {detail}"
                if detail
                else "Storage location is not readable"
            ),
            "error": {
                "code": "storage_access_failed",
                **({"message": detail} if detail else {}),
            },
        }


def is_validated_empty_log_source(source: EnvironmentSource) -> bool:
    """Whether a completed validation established that a source has no log files."""
    if (
        getattr(source, "source_kind", "logs") != "logs"
        or getattr(source, "read_check_status", None) != "error"
    ):
        return False
    try:
        result = json.loads(getattr(source, "read_check_result_json", None) or "{}")
    except json.JSONDecodeError:
        return False
    return result.get("message") == EMPTY_LOG_SOURCE_MESSAGE


def validate_metadata_source(
    session: Session,
    source: EnvironmentSource,
    *,
    secret_store: CredentialSecretStore | None = None,
) -> dict:
    if source.storage_provider != "local":
        try:
            adapter = create_storage_adapter(
                binding_from_source(source),
                uri=source.uri,
                session=session,
                secret_store=secret_store,
            )
            adapter.stat(source.uri)
        except Exception:
            return record_source_validation(
                session,
                source,
                source_validation_error(
                    source,
                    "Metadata object is not readable",
                    provider=source.storage_provider,
                ),
            )
        return record_metadata_source_readable(session, source)
    try:
        path = require_local_path(source.uri)
    except StorageProviderNotEnabled as exc:
        return record_source_validation(session, source, source_validation_error(source, str(exc), provider=exc.provider))
    if not path.exists():
        return record_source_validation(session, source, source_validation_error(source, f"Metadata file not found: {source.uri}"))
    if not path.is_file():
        return record_source_validation(session, source, source_validation_error(source, f"Metadata source is not a file: {source.uri}"))
    try:
        with path.open("rb") as handle:
            handle.read(1)
    except OSError as exc:
        return record_source_validation(session, source, source_validation_error(source, f"Metadata file is not readable: {exc}"))

    detected_format = _metadata_format(source.uri)
    result = {
        "source_id": source.id,
        "source_kind": "metadata",
        "status": "ok",
        "message": "Metadata source path is readable",
        "detected_provider": "local",
        "detected_format": detected_format,
        "record_counts": {"files": 1},
        "records_scanned": 1,
        "errors": [],
    }
    return record_source_validation(session, source, result)


def record_metadata_source_readable(
    session: Session,
    source: EnvironmentSource,
) -> dict:
    return record_source_validation(
        session,
        source,
        {
            "source_id": source.id,
            "source_kind": "metadata",
            "status": "ok",
            "message": "Metadata source object is readable",
            "detected_provider": source.storage_provider,
            "detected_format": _metadata_format(source.uri),
            "record_counts": {"files": 1},
            "records_scanned": 1,
            "errors": [],
        },
    )


def validate_log_source(
    session: Session,
    source: EnvironmentSource,
    *,
    secret_store: CredentialSecretStore | None = None,
) -> dict:
    log_paths = resolve_log_source_paths(source)
    etl_uri = log_paths.etl_logs_uri or source.uri
    bounded_probe = source.storage_provider == "dbfs"
    try:
        adapter = create_storage_adapter(
            binding_from_source(source),
            uri=source.uri,
            session=session,
            secret_store=secret_store,
        )
        scan_specs = [
            (_stream_uri(etl_uri, "dataflow_run_log"), ".parquet", None),
            (_stream_uri(etl_uri, "job_run_log"), ".jsonl", None),
        ]
        if log_paths.system_logs_uri:
            scan_specs.append(
                (log_paths.system_logs_uri, ".jsonl", "system_log_")
            )
        listings = map_storage_io(
            adapter,
            lambda spec: inventory(
                adapter,
                StorageInventoryRequest(
                    uri=spec[0],
                    purpose="validate",
                    recursive=True,
                    object_types=frozenset({"file"}),
                    suffixes=frozenset({spec[1]}),
                    name_prefix=spec[2],
                    object_limit=1 if bounded_probe else None,
                    stop_after_match=bounded_probe,
                ),
            ),
            scan_specs,
        )
        dataflow_files = list(listings[0].files)
        job_files = list(listings[1].files)
        system_files = list(listings[2].files) if len(listings) > 2 else []
    except Exception:
        message = "Log storage is not accessible"
        return record_source_validation(
            session,
            source,
            source_validation_error(
                source, message, provider=source.storage_provider
            ),
        )
    if not dataflow_files and not job_files and not system_files:
        return record_source_validation(
            session,
            source,
            source_validation_error(source, EMPTY_LOG_SOURCE_MESSAGE),
        )

    counts = {
        "dataflow_parquet_files": len(dataflow_files),
        "job_jsonl_files": len(job_files),
        "system_jsonl_files": len(system_files),
    }
    result = {
        "source_id": source.id,
        "source_kind": "logs",
        "status": "ok",
        "message": (
            "Log source is readable (bounded probe)"
            if bounded_probe
            else "Log source is readable"
        ),
        "detected_provider": source.storage_provider,
        "detected_format": "logs",
        "record_counts": counts,
        "records_scanned": sum(counts.values()),
        "errors": [],
    }
    return record_source_validation(session, source, result)


def _stream_uri(etl_uri: str, stream_name: str) -> str:
    return etl_uri if uri_basename(etl_uri) == stream_name else join_uri(etl_uri, stream_name)


def _metadata_format(uri: str) -> str:
    suffix = uri.rsplit(".", 1)[-1].lower() if "." in uri else "json"
    if suffix in {"yaml", "yml"}:
        return "yaml"
    if suffix in {"xlsx", "xls"}:
        return "xlsx"
    return "json"


def source_validation_error(source: EnvironmentSource, message: str, *, provider: str = "local") -> dict:
    return {
        "source_id": source.id,
        "source_kind": source.source_kind,
        "status": "error",
        "message": message,
        "detected_provider": provider,
        "detected_format": None,
        "record_counts": {},
        "records_scanned": 0,
        "errors": [{"message": message}],
    }


def record_source_validation(
    session: Session,
    source: EnvironmentSource,
    result: dict,
    *,
    checked_at: datetime | None = None,
) -> dict:
    checked_at = checked_at or utc_now()
    source.read_check_status = str(result["status"])
    source.read_checked_at = checked_at
    source.read_check_result_json = json.dumps({**result, "validated_at": checked_at.isoformat()}, sort_keys=True)
    session.commit()
    session.refresh(source)
    return {**result, "validated_at": _as_utc(source.read_checked_at or checked_at)}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
