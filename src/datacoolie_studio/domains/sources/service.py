from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource, utc_now
from datacoolie_studio.domains.logs.contracts import (
    LOG_CATEGORY_SYSTEM,
    LOG_TYPE_DATAFLOW_RUN,
    LOG_TYPE_JOB_RUN,
    LOG_TYPE_SYSTEM,
)
from datacoolie_studio.domains.analytics.errors import AnalyticsSchemaIncompatibleError
from datacoolie_studio.domains.analytics.log_contract import admit_log_record
from datacoolie_studio.domains.logs.source_config import resolve_log_source_paths
from datacoolie_studio.domains.storage.uri import (
    StorageProviderNotEnabled,
    join_uri,
    parse_storage_uri,
    require_local_path,
    uri_basename,
)
from datacoolie_studio.domains.credentials.store import CredentialSecretStore
from datacoolie_studio.domains.sources.storage_binding import (
    binding_from_source,
    validate_and_normalize_binding,
)
from datacoolie_studio.domains.storage.errors import (
    StorageError,
    StorageNotFoundError,
)
from datacoolie_studio.domains.storage.concurrency import map_storage_io
from datacoolie_studio.domains.storage.factory import create_storage_adapter
from datacoolie_studio.domains.storage.inventory import (
    StorageInventoryRequest,
    inventory,
)
from datacoolie_studio.domains.storage.capabilities import (
    metadata_write_back_supported,
)
from datacoolie_studio.domains.storage.redaction import redact_storage_error
from datacoolie_studio.domains.sources.validation_diagnostics import (
    unexpected_storage_validation_result,
    unexpected_validation_result,
)


LEGACY_EMPTY_LOG_SOURCE_MESSAGE = "No execution or system log files found"
EMPTY_LOG_SOURCE_MESSAGE = "Log source is readable but contains no matching log files"
SQL_SUFFIXES = frozenset({".sql"})


def sql_root_uri_for_source(source: EnvironmentSource) -> str:
    """Return the configured root used to resolve SQL files for a source."""

    try:
        config = json.loads(source.source_config_json or "{}")
    except (json.JSONDecodeError, TypeError):
        config = {}
    configured = config.get("sql_root_uri") if isinstance(config, dict) else None
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    if not source.uri.lower().rstrip("/").endswith(".sql"):
        return source.uri
    parsed = parse_storage_uri(source.uri)
    if parsed.is_local and parsed.local_path is not None:
        return str(parsed.local_path.parent)
    return source.uri.rsplit("/", 1)[0]


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
            for_validation=True,
        )
        if binding.provider == "local":
            path = require_local_path(canonical_uri)
            if path.is_file():
                revision = adapter.stat(canonical_uri)
                return _storage_file_validation_result(
                    provider=binding.provider,
                    canonical_uri=revision.canonical_uri,
                    provider_revision=revision.provider_revision,
                )
            if not path.is_dir():
                raise FileNotFoundError(canonical_uri)
        try:
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
        except StorageNotFoundError:
            # A remote URI may identify a file rather than a directory.  Some
            # providers answer a directory-list request for that file with a
            # 404, so fall back to the adapter's file metadata operation.
            stat = getattr(adapter, "stat", None)
            if not callable(stat):
                raise
            revision = stat(canonical_uri)
            return _storage_file_validation_result(
                provider=binding.provider,
                canonical_uri=revision.canonical_uri,
                provider_revision=revision.provider_revision,
            )

        matching_file = next(
            (
                item
                for item in observed.files
                if item.canonical_uri.rstrip("/") == canonical_uri.rstrip("/")
            ),
            None,
        )
        if matching_file is not None:
            return _storage_file_validation_result(
                provider=binding.provider,
                canonical_uri=matching_file.canonical_uri,
                provider_revision=matching_file.provider_revision,
            )
        return {
            "status": "ok",
            "provider": binding.provider,
            "canonical_uri": adapter.canonical_uri(canonical_uri),
            "object_type": "directory",
            "objects_scanned": len(observed.objects),
            "provider_revision": None,
            "metadata_write_back_supported": (
                metadata_write_back_supported(binding.provider)
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
    except Exception as exc:
        return unexpected_storage_validation_result(
            operation="storage-connection-validation",
            provider=str((storage or {}).get("provider") or "local"),
            message="Storage validation failed",
            exc=exc,
        )


def _storage_file_validation_result(
    *,
    provider: str,
    canonical_uri: str,
    provider_revision: str | None,
) -> dict[str, object]:
    return {
        "status": "ok",
        "provider": provider,
        "canonical_uri": canonical_uri,
        "object_type": "file",
        "objects_scanned": 1,
        "provider_revision": provider_revision,
        "metadata_write_back_supported": metadata_write_back_supported(provider),
        "message": "Storage file is readable",
    }


def is_empty_log_validation(source: EnvironmentSource) -> bool:
    """Return whether the persisted validation describes an accessible empty log root.

    The legacy error marker is accepted only as a recovery input. Consumers must
    use the validation status/counts directly rather than excluding a source by
    matching this historical message.
    """
    if getattr(source, "source_kind", "logs") != "logs":
        return False
    try:
        result = json.loads(getattr(source, "read_check_result_json", None) or "{}")
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(result, dict):
        return False
    if result.get("empty") is True:
        return True
    return (
        getattr(source, "read_check_status", None) == "error"
        and result.get("message") == LEGACY_EMPTY_LOG_SOURCE_MESSAGE
    )


def update_empty_log_validation_after_sync(
    source: EnvironmentSource,
    *,
    discovered_file_count: int,
    checked_at: datetime,
) -> None:
    """Replace a legacy empty marker after a successful log synchronization."""
    if not is_empty_log_validation(source):
        return
    if discovered_file_count > 0:
        result = {
            "source_id": source.id,
            "source_kind": "logs",
            "status": "ok",
            "message": "Log source is readable",
            "detected_provider": source.storage_provider,
            "detected_format": "logs",
            "record_counts": {},
            "records_scanned": 0,
            "errors": [],
        }
    else:
        result = {
            "source_id": source.id,
            "source_kind": "logs",
            "status": "warning",
            "message": EMPTY_LOG_SOURCE_MESSAGE,
            "empty": True,
            "detected_provider": source.storage_provider,
            "detected_format": "logs",
            "record_counts": {
                "dataflow_json_files": 0,
                "job_json_files": 0,
                "system_json_files": 0,
            },
            "records_scanned": 0,
            "errors": [],
        }
    source.read_check_status = result["status"]
    source.read_checked_at = checked_at
    source.read_check_result_json = json.dumps(
        {**result, "validated_at": checked_at.isoformat()},
        sort_keys=True,
    )


def stored_source_validation_error(source: EnvironmentSource) -> dict[str, str] | None:
    """Return a persisted validation failure without touching source storage."""
    if getattr(source, "read_check_status", None) != "error":
        return None
    try:
        result = json.loads(getattr(source, "read_check_result_json", None) or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(result, dict):
        return None
    message = result.get("message")
    if not message:
        for error in result.get("errors") or []:
            if isinstance(error, dict) and error.get("message"):
                message = error["message"]
                break
    if not message:
        return None
    return {
        "code": "source_validation_failed",
        "message": str(message),
        **(
            {"provider": str(result["detected_provider"])}
            if result.get("detected_provider")
            else {}
        ),
    }


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
                for_validation=True,
            )
            adapter.stat(source.uri)
        except StorageError as exc:
            return record_source_validation(
                session,
                source,
                source_validation_error(
                    source, str(exc), provider=exc.provider or source.storage_provider
                ),
            )
        except Exception as exc:
            return record_source_validation(
                session,
                source,
                unexpected_validation_result(
                    source,
                    operation="metadata-source-validation",
                    message="Metadata object is not readable",
                    exc=exc,
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
    datacoolie_compatible = detected_format != "xls"
    result = {
        "source_id": source.id,
        "source_kind": "metadata",
        "status": "ok",
        "message": (
            "Metadata source path is readable"
            if datacoolie_compatible
            else "Metadata source is readable, but DataCoolie does not support .xls metadata"
        ),
        "detected_provider": "local",
        "detected_format": detected_format,
        "metadata_write_back_supported": datacoolie_compatible,
        "datacoolie_compatible": datacoolie_compatible,
        "record_counts": {"files": 1},
        "records_scanned": 1,
        "errors": [],
    }
    return record_source_validation(session, source, result)


def record_metadata_source_readable(
    session: Session,
    source: EnvironmentSource,
) -> dict:
    detected_format = _metadata_format(source.uri)
    datacoolie_compatible = detected_format != "xls"
    return record_source_validation(
        session,
        source,
        {
            "source_id": source.id,
            "source_kind": "metadata",
            "status": "ok",
            "message": (
                "Metadata source object is readable"
                if datacoolie_compatible
                else "Metadata source is readable, but DataCoolie does not support .xls metadata"
            ),
            "detected_provider": source.storage_provider,
            "detected_format": detected_format,
            "metadata_write_back_supported": (
                metadata_write_back_supported(source.storage_provider)
                and datacoolie_compatible
            ),
            "datacoolie_compatible": datacoolie_compatible,
            "record_counts": {"files": 1},
            "records_scanned": 1,
            "errors": [],
        },
    )


def validate_sql_source(
    session: Session,
    source: EnvironmentSource,
    *,
    secret_store: CredentialSecretStore | None = None,
) -> dict:
    """Validate a manually registered SQL file or SQL root directory."""

    root_uri = sql_root_uri_for_source(source)
    source_is_file = source.uri.lower().rstrip("/").endswith(".sql")
    sql_file_count = 0
    try:
        adapter = create_storage_adapter(
            binding_from_source(source),
            uri=source.uri,
            session=session,
            secret_store=secret_store,
            for_validation=True,
        )
        if source.storage_provider == "local":
            target_path = require_local_path(source.uri if source_is_file else root_uri)
            if not target_path.exists():
                return record_source_validation(
                    session,
                    source,
                    source_validation_error(
                        source,
                        f"SQL source path not found: {source.uri}",
                    ),
                )
            if source_is_file:
                if not target_path.is_file():
                    return record_source_validation(
                        session,
                        source,
                        source_validation_error(
                            source,
                            f"SQL source is not a file: {source.uri}",
                        ),
                    )
                sql_file_count = 1
            elif not target_path.is_dir():
                return record_source_validation(
                    session,
                    source,
                    source_validation_error(
                        source,
                        f"SQL source root is not a directory: {root_uri}",
                    ),
                )
            else:
                sql_file_count = sum(
                    1
                    for item in target_path.rglob("*")
                    if item.is_file()
                    and not item.is_symlink()
                    and item.suffix.lower() in SQL_SUFFIXES
                )
        elif source_is_file:
            stat = getattr(adapter, "stat", None)
            if not callable(stat):
                raise StorageError("SQL file validation is not supported by this storage provider")
            stat(source.uri)
            sql_file_count = 1
        else:
            observed = inventory(
                adapter,
                StorageInventoryRequest(
                    uri=root_uri,
                    purpose="validate",
                    recursive=True,
                    object_types=frozenset({"file"}),
                    suffixes=SQL_SUFFIXES,
                ),
            )
            sql_file_count = len(observed.files)
    except StorageError as exc:
        return record_source_validation(
            session,
            source,
            source_validation_error(
                source,
                str(exc) or "SQL storage is not accessible",
                provider=exc.provider or source.storage_provider,
            ),
        )
    except (OSError, ValueError, StorageProviderNotEnabled) as exc:
        detail = redact_storage_error(str(exc))
        return record_source_validation(
            session,
            source,
            source_validation_error(
                source,
                detail or "SQL source is not readable",
                provider=source.storage_provider,
            ),
        )

    if sql_file_count == 0:
        return record_source_validation(
            session,
            source,
            {
                "source_id": source.id,
                "source_kind": "sql",
                "status": "warning",
                "message": "SQL source is readable but contains no .sql files",
                "detected_provider": source.storage_provider,
                "detected_format": "sql",
                "metadata_write_back_supported": metadata_write_back_supported(source.storage_provider),
                "record_counts": {"sql_files": 0},
                "records_scanned": 0,
                "errors": [
                    {
                        "code": "sql_files_not_found",
                        "severity": "warning",
                        "message": "No .sql files were found below the configured SQL root",
                    }
                ],
            },
        )

    return record_source_validation(
        session,
        source,
        {
            "source_id": source.id,
            "source_kind": "sql",
            "status": "ok",
            "message": f"SQL source is readable ({sql_file_count} .sql file{'s' if sql_file_count != 1 else ''})",
            "detected_provider": source.storage_provider,
            "detected_format": "sql",
            "metadata_write_back_supported": metadata_write_back_supported(source.storage_provider),
            "record_counts": {"sql_files": sql_file_count},
            "records_scanned": sql_file_count,
            "errors": [],
        },
    )


def validate_log_source(
    session: Session,
    source: EnvironmentSource,
    *,
    secret_store: CredentialSecretStore | None = None,
) -> dict:
    resolved_paths = resolve_log_source_paths(source)
    execution_uri = resolved_paths.execution_logs_uri or source.uri
    bounded_probe = source.storage_provider == "dbfs"
    try:
        adapter = create_storage_adapter(
            binding_from_source(source),
            uri=source.uri,
            session=session,
            secret_store=secret_store,
            for_validation=True,
        )
        scan_specs = [
            (_stream_uri(execution_uri, "dataflow_run_log"), ".json", None),
            (_stream_uri(execution_uri, "job_run_log"), ".json", None),
        ]
        if resolved_paths.system_logs_uri:
            scan_specs.append((resolved_paths.system_logs_uri, ".json", None))
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
    except StorageError as exc:
        message = str(exc) or "Log storage is not accessible"
        return record_source_validation(
            session,
            source,
            source_validation_error(
                source, message, provider=exc.provider or source.storage_provider
            ),
        )
    except Exception as exc:
        message = "Log storage is not accessible"
        return record_source_validation(
            session,
            source,
            unexpected_validation_result(
                source,
                operation="log-source-validation",
                message=message,
                exc=exc,
                provider=source.storage_provider,
            ),
        )
    if not dataflow_files and not job_files and not system_files:
        return record_source_validation(
            session,
            source,
            {
                "source_id": source.id,
                "source_kind": "logs",
                "status": "warning",
                "message": EMPTY_LOG_SOURCE_MESSAGE,
                "empty": True,
                "detected_provider": source.storage_provider,
                "detected_format": "logs",
                "record_counts": {
                    "dataflow_json_files": 0,
                    "job_json_files": 0,
                    "system_json_files": 0,
                },
                "records_scanned": 0,
                "errors": [],
            },
        )

    counts = {
        "dataflow_json_files": len(dataflow_files),
        "job_json_files": len(job_files),
        "system_json_files": len(system_files),
    }
    contract_errors: list[dict[str, object]] = []
    unsupported_samples: list[dict[str, object]] = []
    admitted_samples = 0
    for category, files, expected_type in (
        (LOG_TYPE_DATAFLOW_RUN, dataflow_files, LOG_TYPE_DATAFLOW_RUN),
        (LOG_TYPE_JOB_RUN, job_files, LOG_TYPE_JOB_RUN),
        (LOG_CATEGORY_SYSTEM, system_files, LOG_TYPE_SYSTEM),
    ):
        if not files:
            continue
        issue = _validate_log_sample(
            adapter,
            files[0].canonical_uri,
            expected_type,
            require_job_id=expected_type in {LOG_TYPE_DATAFLOW_RUN, LOG_TYPE_JOB_RUN},
            source=source,
        )
        if issue is not None:
            issue["category"] = category
            if issue.get("code") == "unsupported_log_schema":
                unsupported_samples.append(issue)
            else:
                contract_errors.append(issue)
        else:
            admitted_samples += 1
    if contract_errors:
        return record_source_validation(
            session,
            source,
            {
                "source_id": source.id,
                "source_kind": "logs",
                "status": "error",
                "message": "Log source contains unsupported or invalid log records",
                "detected_provider": source.storage_provider,
                "detected_format": "logs",
                "record_counts": counts,
                "records_scanned": sum(counts.values()),
                "errors": contract_errors,
            },
        )
    if unsupported_samples:
        sampled_counts = {
            **counts,
            "admitted_v6_samples": admitted_samples,
            "unsupported_samples": len(unsupported_samples),
        }
        return record_source_validation(
            session,
            source,
            {
                "source_id": source.id,
                "source_kind": "logs",
                "status": "warning",
                "message": (
                    "Log source is readable but contains no supported v6 log records"
                    if admitted_samples == 0
                    else "Log source is readable but contains unsupported log schema records"
                ),
                "detected_provider": source.storage_provider,
                "detected_format": "logs",
                "record_counts": sampled_counts,
                "records_scanned": sum(counts.values()),
                "errors": unsupported_samples,
            },
        )
    result = {
        "source_id": source.id,
        "source_kind": "logs",
        "status": "ok",
        "message": "Log source is readable (bounded probe)" if bounded_probe else "Log source is readable",
        "detected_provider": source.storage_provider,
        "detected_format": "logs",
        "record_counts": counts,
        "records_scanned": sum(counts.values()),
        "errors": [],
    }
    return record_source_validation(session, source, result)


def _validate_log_sample(
    adapter: object,
    file_uri: str,
    expected_type: str | None,
    *,
    require_job_id: bool = False,
    source: EnvironmentSource,
) -> dict[str, object] | None:
    """Validate one record without turning source validation into a full scan."""

    open_read = getattr(adapter, "open_read", None)
    # StorageAdapter implementations expose open_read.  Keeping this probe
    # optional makes bounded inventory-only test doubles and older integrations
    # remain usable while real providers still get content validation.
    if not callable(open_read):
        return None
    try:
        with open_read(file_uri) as handle:
            line = _first_non_empty_line(handle)
    except Exception as exc:
        unexpected = unexpected_validation_result(
            source,
            operation="log-sample-validation",
            message="Could not read a sample log record",
            exc=exc,
            provider=getattr(source, "storage_provider", None),
        )
        return {
            "code": "log_content_read_failed",
            "message": unexpected["message"],
        }
    if line is None:
        return {
            "uri": file_uri,
            "code": "empty_log_file",
            "message": "Log file contains no JSON records",
        }
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        return {
            "uri": file_uri,
            "code": "invalid_log_json",
            "message": f"First log record is not valid JSON: {exc.msg}",
        }
    if not isinstance(record, dict):
        return {
            "uri": file_uri,
            "code": "invalid_log_record",
            "message": "First log record must be a JSON object",
        }
    if expected_type is not None and record.get("_type") != expected_type:
        return {
            "uri": file_uri,
            "code": "unexpected_log_record_type",
            "message": (
                f"Unexpected log record type {record.get('_type')!r}; "
                f"expected {expected_type!r}"
            ),
            "expected_type": expected_type,
            "actual_type": record.get("_type"),
        }
    if require_job_id and not str(record.get("job_id") or "").strip():
        return {
            "uri": file_uri,
            "code": "missing_job_id",
            "message": "Execution log record must include the raw job_id field",
        }
    try:
        admitted = admit_log_record(
            record,
            expected_type=expected_type or str(record.get("_type") or ""),
            uri=file_uri,
            line=1,
        )
    except AnalyticsSchemaIncompatibleError as exc:
        return {
            "uri": file_uri,
            "code": exc.code,
            "message": str(exc),
        }
    if not admitted:
        return {
            "uri": file_uri,
            "code": "unsupported_log_schema",
            "message": (
                "Log record uses an unsupported schema version; only v6 records are admitted"
            ),
            "schema_version": record.get("log_schema_version"),
        }
    return None


def _first_non_empty_line(handle: object) -> str | None:
    for _ in range(64):
        read_line = getattr(handle, "readline", None)
        if not callable(read_line):
            break
        line = read_line(1024 * 1024)
        if not line:
            return None
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        text = str(line).strip()
        if text:
            return text
    return None


def _stream_uri(root_uri: str, stream_name: str) -> str:
    return root_uri if uri_basename(root_uri) == stream_name else join_uri(root_uri, stream_name)


def _metadata_format(uri: str) -> str:
    suffix = uri.rsplit(".", 1)[-1].lower() if "." in uri else "json"
    if suffix in {"yaml", "yml"}:
        return "yaml"
    if suffix == "xlsx":
        return "xlsx"
    if suffix == "xls":
        return "xls"
    return "json"


def source_validation_error(source: EnvironmentSource, message: str, *, provider: str = "local") -> dict:
    return {
        "source_id": source.id,
        "source_kind": source.source_kind,
        "status": "error",
        "message": message,
        "detected_provider": provider,
        "detected_format": None,
        "metadata_write_back_supported": metadata_write_back_supported(provider),
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
