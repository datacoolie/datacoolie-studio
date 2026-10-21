from __future__ import annotations

import json
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from datacoolie_studio.core.time import parse_utc_datetime
from datacoolie_studio.db.models import EnvironmentSource, LogFileManifest
from datacoolie_studio.domains.credentials.store import (
    CredentialSecretStore,
    KeyringCredentialSecretStore,
)
from datacoolie_studio.domains.logs import file_state, staging as log_staging
from datacoolie_studio.domains.logs import batching
from datacoolie_studio.domains.logs.reader import SystemLogScanStats, read_system_log_file
from datacoolie_studio.domains.logs.system_log_selection import (
    filename_job_candidates,
    system_log_manifests,
)
from datacoolie_studio.domains.sources.storage_binding import binding_from_source
from datacoolie_studio.domains.storage.factory import create_storage_adapter
from datacoolie_studio.domains.storage.concurrency import map_storage_io
from datacoolie_studio.domains.storage.uri import uri_basename


def system_log_records(
    session: Session,
    paths: list[EnvironmentSource],
    *,
    job_id: str,
    source_id: int | None = None,
    log_session_id: str | None = None,
    log_session_scope: str = "all",
    dataflow_id: str | None = None,
    dataflow_run_id: str | None = None,
    event_name: str | None = None,
    include_dataflow_logs: bool = False,
    level: str | None = None,
    q: str | None = None,
    limit: int = 500,
    offset: int = 0,
    secret_store: CredentialSecretStore | None = None,
) -> dict[str, Any]:
    enabled_ids = _source_ids(paths)
    if source_id is not None:
        enabled_ids = [candidate for candidate in enabled_ids if candidate == source_id]
    if not enabled_ids or not job_id:
        return {"records": [], "total": 0, "files": [], "errors": []}

    files = list(system_log_manifests(session, enabled_ids, job_id))
    sources = {source.id: source for source in paths}
    adapters: dict[int, object] = {}
    records: list[tuple[dict[str, Any], datetime | None, int, str, int]] = []
    errors: list[dict[str, str]] = []
    file_counts: dict[int, int] = {}

    with ExitStack() as scopes:
        staging_dirs: dict[int, Path] = {}

        def staging_dir_for(source: EnvironmentSource) -> Path:
            directory = staging_dirs.get(source.id)
            if directory is None:
                scopes.enter_context(log_staging.staging_scope(source.id))
                directory = log_staging.create_staging_dir(source.id)
                staging_dirs[source.id] = directory
            return directory

        files_by_source: dict[int, list[LogFileManifest]] = {}
        for manifest in files:
            if manifest.source_id in sources:
                files_by_source.setdefault(manifest.source_id, []).append(manifest)

        # A batch file is one member of the logical UNION ALL stream.  Materialize
        # and consume bounded groups, then apply one global sort/page below so a
        # page boundary never depends on the provider's file listing order.
        for source_id, source_files in files_by_source.items():
            source = sources[source_id]
            adapter = None
            if source.storage_provider != "local":
                adapter = adapters.get(source.id)
                if adapter is None:
                    adapter = create_storage_adapter(
                        binding_from_source(source),
                        uri=source.uri,
                        session=session,
                        secret_store=secret_store
                        or KeyringCredentialSecretStore(),
                    )
                    adapters[source.id] = adapter
            for file_batch in batching.sized_batches(
                source_files,
                size_of=lambda manifest: _manifest_size(manifest),
            ):
                staging_dir = staging_dir_for(source) if adapter is not None else None
                with batching.log_byte_budget.acquire(
                    sum(_manifest_size(manifest) for manifest in file_batch)
                ):
                    materialized_files: list[tuple[LogFileManifest, str]] = []
                    try:
                        def materialize_manifest(
                            manifest: LogFileManifest,
                        ) -> tuple[LogFileManifest, str]:
                            if source.storage_provider == "local":
                                return manifest, manifest.file_uri
                            expected_revision = file_state.file_revision_from_json(
                                manifest.file_uri,
                                manifest.revision_json,
                            )
                            if expected_revision is None:
                                expected_revision = adapter.stat(manifest.file_uri)
                            assert staging_dir is not None
                            target = staging_dir / (
                                f"{_uri_digest(manifest.file_uri)}-"
                                f"{uri_basename(manifest.file_uri)}"
                            )
                            adapter.materialize(
                                manifest.file_uri,
                                target,
                                expected_revision=expected_revision,
                            )
                            return manifest, target.as_posix()

                        materialized_files = map_storage_io(
                            adapter or source,
                            materialize_manifest,
                            file_batch,
                        )
                    except Exception as exc:
                        errors.append(
                            {
                                "uri": ", ".join(
                                    manifest.file_uri for manifest in file_batch
                                ),
                                "message": "System log object could not be read",
                                "code": getattr(exc, "code", "storage_read_failed"),
                            }
                        )
                    else:
                        for manifest, read_uri in materialized_files:
                            try:
                                scan_stats = SystemLogScanStats()
                                file_rows, _file_total, file_errors = read_system_log_file(
                                    read_uri,
                                    job_id=job_id,
                                    log_session_id=log_session_id,
                                    log_session_scope=log_session_scope,
                                    dataflow_id=dataflow_id,
                                    dataflow_run_id=dataflow_run_id,
                                    event_name=event_name,
                                    include_dataflow_logs=include_dataflow_logs,
                                    level=level,
                                    q=q,
                                    limit=None,
                                    offset=0,
                                    scan_stats=scan_stats,
                                    filename_job_ids=filename_job_candidates(manifest),
                                )
                                file_counts[manifest.id] = scan_stats.admitted_records
                                for item in file_errors:
                                    item["uri"] = manifest.file_uri
                                errors.extend(file_errors)
                                for row in file_rows:
                                    # Keep source/file provenance on every record.
                                    enriched = {
                                        **row,
                                        "_source_id": manifest.source_id,
                                        "_file_uri": manifest.file_uri,
                                        "_file_kind": manifest.file_kind,
                                        "_log_session_id": row.get("log_session_id"),
                                    }
                                    records.append(
                                        (
                                            enriched,
                                            _record_timestamp(row),
                                            manifest.source_id,
                                            manifest.file_uri,
                                            row["_record_line"],
                                        )
                                    )
                            except Exception as exc:
                                errors.append(
                                    {
                                        "uri": manifest.file_uri,
                                        "message": "System log object could not be read",
                                        "code": getattr(exc, "code", "storage_read_failed"),
                                    }
                                )
                    finally:
                        for manifest in file_batch:
                            if source.storage_provider != "local":
                                assert staging_dir is not None
                                target = staging_dir / (
                                    f"{_uri_digest(manifest.file_uri)}-"
                                    f"{uri_basename(manifest.file_uri)}"
                                )
                                log_staging.cleanup_materialized_path(
                                    target.as_posix(),
                                    staging_dir,
                                )

    records.sort(key=_record_sort_key)
    total = len(records)
    page = [item[0] for item in records[offset : offset + limit]]
    return {
        "records": page,
        "total": total,
        "files": [
            _manifest_response(file, row_count=file_counts.get(file.id, 0))
            for file in files
        ],
        "errors": errors,
    }


def _source_ids(paths: list[EnvironmentSource]) -> list[int]:
    return sorted(
        path.id
        for path in paths
        if path.enabled
    )


def _manifest_response(file: LogFileManifest, *, row_count: int) -> dict[str, Any]:
    return {
        "source_id": file.source_id,
        "file_uri": file.file_uri,
        "file_kind": file.file_kind,
        "log_session_id": file.log_session_id,
        "row_count": row_count,
        "log_timestamp": _timestamp_text(file.log_timestamp),
        "event_min_timestamp": _timestamp_text(file.event_min_timestamp),
        "event_max_timestamp": _timestamp_text(file.event_max_timestamp),
        "run_date": file.run_date.isoformat() if file.run_date else None,
    }


def _timestamp_text(value: datetime | None) -> str | None:
    timestamp = parse_utc_datetime(value)
    return timestamp.isoformat() if timestamp else None


def _uri_digest(uri: str) -> str:
    import hashlib

    return hashlib.sha256(uri.encode("utf-8")).hexdigest()[:16]


def _manifest_size(manifest: LogFileManifest) -> int:
    try:
        return max(
            0,
            int(json.loads(manifest.revision_json or "{}").get("size") or 0),
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0


def _record_timestamp(row: dict[str, Any]) -> datetime | None:
    value = row.get("ts") or row.get("timestamp")
    parsed = parse_utc_datetime(value)
    return parsed.astimezone(timezone.utc) if parsed else None


def _record_sort_key(
    item: tuple[dict[str, Any], datetime | None, int, str, int],
) -> tuple[bool, datetime, int, str, int]:
    _row, timestamp, source_id, file_uri, line_number = item
    return (
        timestamp is None,
        timestamp or datetime.max.replace(tzinfo=timezone.utc),
        source_id,
        file_uri,
        line_number,
    )
