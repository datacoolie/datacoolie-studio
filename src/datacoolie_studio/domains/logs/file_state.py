from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from datacoolie_studio.core.time import epoch_value_to_nanoseconds, parse_utc_datetime
from datacoolie_studio.db.models import LogFileManifest, LogStreamState
from datacoolie_studio.domains.logs import refresh_planning
from datacoolie_studio.domains.logs.discovery import DiscoveredLogFile
from datacoolie_studio.domains.logs.partition import ParsedPartition, PartitionGranularity, partition_datetime
from datacoolie_studio.domains.logs.system_log_filename import parse_system_log_filename
from datacoolie_studio.domains.storage.adapters import StorageAdapter, StorageRevision
from datacoolie_studio.domains.storage.uri import parse_storage_uri


def revision_json(revision: StorageRevision) -> str:
    provider_revision = revision.provider_revision
    local_mtime_token = (
        provider_revision.split(":", 1)[0] if provider_revision else ""
    )
    mtime_ns = (
        int(local_mtime_token)
        if parse_storage_uri(revision.canonical_uri).is_local and local_mtime_token.isdigit()
        else int(revision.last_modified.timestamp() * 1_000_000_000)
    )
    return json.dumps(
        {
            "size": revision.size,
            "mtime_ns": mtime_ns,
            "last_modified": revision.last_modified.isoformat(),
            "provider_revision": provider_revision,
        },
        sort_keys=True,
    )

def file_revision_from_json(
    file_uri: str,
    stored_revision_json: str,
) -> StorageRevision | None:
    try:
        payload = json.loads(stored_revision_json)
    except (TypeError, json.JSONDecodeError):
        return None
    last_modified = parse_utc_datetime(payload.get("last_modified"))
    if last_modified is None and payload.get("mtime_ns") is not None:
        try:
            normalized = epoch_value_to_nanoseconds(payload["mtime_ns"])
            if normalized is None:
                return None
            last_modified = datetime.fromtimestamp(
                normalized / 1_000_000_000,
                tz=timezone.utc,
            )
        except (TypeError, ValueError, OSError, OverflowError):
            return None
    if last_modified is None:
        return None
    return StorageRevision(
        canonical_uri=file_uri,
        size=int(payload.get("size") or 0),
        last_modified=last_modified,
        provider_revision=str(
            payload.get("provider_revision") or payload.get("mtime_ns") or ""
        )
        or None,
    )

def ingest_file_state(candidate: DiscoveredLogFile, file_kind: str) -> dict[str, Any]:
    partition_key = candidate.partition.partition_value
    filename_metadata = _filename_metadata(candidate.canonical_uri, file_kind)
    return {
        "file_uri": candidate.canonical_uri,
        "file_kind": file_kind,
        "partition_value": refresh_planning.partition_date(partition_key),
        "partition_key": refresh_planning.partition_key_text(partition_key),
        "partition_format": candidate.partition.partition_format,
        "revision_json": revision_json(candidate.revision),
        "job_id": None,
        "filename_job_id": filename_metadata["filename_job_id"],
        "filename_job_id_alt": filename_metadata["filename_job_id_alt"],
        "log_timestamp": filename_metadata["log_timestamp"],
        "run_date": filename_metadata["run_date"]
        or refresh_planning.partition_date(partition_key),
    }

def manifest_file_state_from_candidate(
    candidate: DiscoveredLogFile,
    file_kind: str,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    revision = candidate.revision
    partition_key = candidate.partition.partition_value
    parsed_metadata = _filename_metadata(revision.canonical_uri, file_kind)
    if metadata is not None:
        parsed_metadata = {**parsed_metadata, **metadata}
    return {
        "file_uri": revision.canonical_uri,
        "file_kind": file_kind,
        "partition_value": refresh_planning.partition_date(partition_key),
        "partition_key": refresh_planning.partition_key_text(partition_key),
        "partition_format": candidate.partition.partition_format,
        "revision_json": revision_json(revision),
        "job_id": parsed_metadata.get("job_id"),
        "filename_job_id": parsed_metadata.get("filename_job_id"),
        "filename_job_id_alt": parsed_metadata.get("filename_job_id_alt"),
        "log_session_id": parsed_metadata.get("log_session_id"),
        "log_timestamp": parsed_metadata.get("log_timestamp"),
        "event_min_timestamp": parsed_metadata.get("event_min_timestamp"),
        "event_max_timestamp": parsed_metadata.get("event_max_timestamp"),
        "run_date": parsed_metadata.get("run_date"),
    }


def _filename_metadata(file_uri: str, file_kind: str) -> dict[str, Any]:
    if file_kind != "system_json":
        return {
            "job_id": None,
            "filename_job_id": None,
            "filename_job_id_alt": None,
            "log_timestamp": None,
            "run_date": None,
        }
    parsed = parse_system_log_filename(file_uri)
    return {
        "job_id": None,
        "filename_job_id": parsed.job_id,
        "filename_job_id_alt": parsed.job_id_alt,
        "log_timestamp": parsed.log_timestamp,
        "run_date": parsed.log_timestamp.date() if parsed.log_timestamp else None,
    }

def revision_with_known_files(
    base_revision: dict[str, Any],
    file_revisions: dict[str, str],
) -> dict[str, Any]:
    """Enrich the shallow root revision from bounded discovery and manifests."""

    total_size = 0
    max_mtime_ns = base_revision.get("max_mtime_ns")
    for stored_revision_json in file_revisions.values():
        try:
            payload = json.loads(stored_revision_json)
            total_size += int(payload.get("size") or 0)
            mtime_ns = int(payload.get("mtime_ns") or 0)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        max_mtime_ns = (
            mtime_ns
            if max_mtime_ns is None
            else max(int(max_mtime_ns), mtime_ns)
        )
    return {
        **base_revision,
        "file_count": len(file_revisions),
        "total_size": total_size,
        "max_mtime_ns": max_mtime_ns,
    }

def merge_rebuild_candidates(
    adapter: StorageAdapter,
    planned: list[tuple[DiscoveredLogFile, str]],
    manifest_rows: list[LogFileManifest],
    states: dict[str, LogStreamState],
) -> list[tuple[DiscoveredLogFile, str]]:
    by_identity = {
        (file_kind, candidate.canonical_uri): (candidate, file_kind)
        for candidate, file_kind in planned
    }
    for row in manifest_rows:
        if row.file_kind not in {"dataflow_json", "job_json"}:
            continue
        identity = (row.file_kind, row.file_uri)
        if identity in by_identity:
            # The planner observed this object during the current sync. Keep that
            # fresh revision instead of replacing it with a stale manifest value.
            continue
        revision = file_revision_from_json(row.file_uri, row.revision_json)
        if revision is None:
            revision = adapter.stat(row.file_uri)
        state = states.get(row.file_kind)
        granularity = (
            PartitionGranularity(state.partition_granularity)
            if state is not None and state.partition_granularity
            else PartitionGranularity.DAY
        )
        partition_value = refresh_planning.stored_partition_key(
            getattr(row, "partition_key", None),
            row.partition_value or row.run_date,
            granularity,
        ) or partition_datetime(revision.last_modified)
        partition_format = (
            row.partition_format
            or (state.partition_format if state is not None else None)
            or ""
        )
        by_identity[identity] = (
            DiscoveredLogFile(
                partition=ParsedPartition(
                    partition_value=partition_value,
                    raw_partition_path=(
                        partition_value.strftime(partition_format)
                        if partition_format
                        else ""
                    ),
                    partition_granularity=granularity,
                    partition_format=partition_format,
                ),
                revision=revision,
            ),
            row.file_kind,
        )
    return sorted(
        by_identity.values(),
        key=lambda item: (
            item[0].partition.partition_value,
            item[1],
            item[0].canonical_uri,
        ),
    )
