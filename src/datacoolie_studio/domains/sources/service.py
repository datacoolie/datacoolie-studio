from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource, utc_now
from datacoolie_studio.domains.logs.reader import discover_dataflow_parquet_files, discover_job_jsonl_files, discover_system_jsonl_files
from datacoolie_studio.domains.logs.source_config import resolve_log_source_paths
from datacoolie_studio.domains.storage.uri import StorageProviderNotEnabled, require_local_path


def validate_metadata_source(session: Session, source: EnvironmentSource) -> dict:
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


def validate_log_source(session: Session, source: EnvironmentSource) -> dict:
    log_paths = resolve_log_source_paths(source)
    etl_uri = log_paths.etl_logs_uri or source.uri
    try:
        etl_path = require_local_path(etl_uri)
        system_path = require_local_path(log_paths.system_logs_uri) if log_paths.system_logs_uri else None
    except StorageProviderNotEnabled as exc:
        return record_source_validation(session, source, source_validation_error(source, str(exc), provider=exc.provider))
    dataflow_files = discover_dataflow_parquet_files(etl_path.as_posix())
    job_files = discover_job_jsonl_files(etl_path.as_posix())
    system_files = discover_system_jsonl_files(system_path.as_posix() if system_path else None)
    if not dataflow_files and not job_files and not system_files:
        return record_source_validation(
            session,
            source,
            source_validation_error(source, "No ETL or system log files found"),
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
        "message": "Log source is readable",
        "detected_provider": "local",
        "detected_format": "logs",
        "record_counts": counts,
        "records_scanned": sum(counts.values()),
        "errors": [],
    }
    return record_source_validation(session, source, result)


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
