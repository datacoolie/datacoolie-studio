from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
import json

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import LogFileManifest, LogStreamState, utc_now
from datacoolie_studio.domains.logs.partition import PartitionValue


@dataclass(frozen=True)
class StreamStateUpdate:
    stream_kind: str
    root_uri: str
    layout_status: str
    partition_format: str | None
    partition_granularity: str | None
    last_scanned_partition_value: PartitionValue | None


def stream_states(
    session: Session,
    source_id: int,
) -> dict[str, LogStreamState]:
    rows = session.scalars(
        select(LogStreamState).where(LogStreamState.source_id == source_id)
    ).all()
    return {row.stream_kind: row for row in rows}


def manifest_rows(
    session: Session,
    source_id: int,
    *,
    stream_kinds: Iterable[str] | None = None,
) -> list[LogFileManifest]:
    statement = select(LogFileManifest).where(
        LogFileManifest.source_id == source_id
    )
    kinds = tuple(stream_kinds or ())
    if kinds:
        statement = statement.where(LogFileManifest.file_kind.in_(kinds))
    return list(session.scalars(statement))


def upsert_stream_states(
    session: Session,
    source_id: int,
    updates: Iterable[StreamStateUpdate],
    *,
    updated_at: datetime | None = None,
) -> None:
    timestamp = updated_at or utc_now()
    existing = stream_states(session, source_id)
    for update in updates:
        row = existing.get(update.stream_kind)
        if row is None:
            row = LogStreamState(
                source_id=source_id,
                stream_kind=update.stream_kind,
                created_at=timestamp,
            )
            session.add(row)
            existing[update.stream_kind] = row
        row.root_uri = update.root_uri
        row.layout_status = update.layout_status
        row.partition_format = update.partition_format
        row.partition_granularity = update.partition_granularity
        row.last_scanned_partition_key = _partition_key(update.last_scanned_partition_value)
        row.updated_at = timestamp


def upsert_manifest_rows(
    session: Session,
    source_id: int,
    file_states: Iterable[Mapping[str, object]],
    row_counts: Mapping[str, int],
    *,
    seen_at: datetime | None = None,
) -> None:
    timestamp = seen_at or utc_now()
    existing = {
        (row.file_kind, row.file_uri): row
        for row in manifest_rows(session, source_id)
    }
    for state in file_states:
        file_uri = str(state["file_uri"])
        file_kind = str(state["file_kind"])
        row = existing.get((file_kind, file_uri))
        if row is None:
            row = LogFileManifest(
                source_id=source_id,
                file_uri=file_uri,
                file_kind=file_kind,
                first_seen_at=timestamp,
            )
            session.add(row)
            existing[(file_kind, file_uri)] = row
        revision = str(state["revision_json"])
        revision_changed = row.revision_json != revision
        if revision_changed:
            row.admission_summary_json = None
        row.revision_json = revision
        if "admission_summary" in state:
            row.admission_summary_json = json.dumps(state["admission_summary"], ensure_ascii=False)
        row.partition_value = state.get("partition_value")  # type: ignore[assignment]
        row.partition_key = (
            str(state["partition_key"])
            if state.get("partition_key") is not None
            else None
        )
        row.partition_format = (
            str(state["partition_format"])
            if state.get("partition_format") is not None
            else None
        )
        metadata_only_system = (
            file_kind == "system_json"
            and "admission_summary" not in state
            and "row_count" not in state
            and file_uri not in row_counts
        )
        if not metadata_only_system:
            row.row_count = int(row_counts.get(file_uri, state.get("row_count") or 0))
            row.job_id = state.get("job_id")  # type: ignore[assignment]
            row.log_session_id = state.get("log_session_id")  # type: ignore[assignment]
            row.event_min_timestamp = state.get("event_min_timestamp")  # type: ignore[assignment]
            row.event_max_timestamp = state.get("event_max_timestamp")  # type: ignore[assignment]
        elif revision_changed:
            # Filename metadata cannot validate content.  A changed revision
            # invalidates all old content-derived hints and counts; an
            # unchanged revision keeps them useful across full/deferred replay.
            row.row_count = 0
            row.job_id = None
            row.log_session_id = None
            row.event_min_timestamp = None
            row.event_max_timestamp = None
        if file_kind == "system_json" and (
            "filename_job_id" in state or revision_changed
        ):
            row.filename_job_id = state.get("filename_job_id")  # type: ignore[assignment]
            row.filename_job_id_alt = state.get("filename_job_id_alt")  # type: ignore[assignment]
        if "log_timestamp" in state and (
            not metadata_only_system or revision_changed
        ):
            row.log_timestamp = state.get("log_timestamp")  # type: ignore[assignment]
        if "run_date" in state and (
            not metadata_only_system or revision_changed
        ):
            row.run_date = state.get("run_date")  # type: ignore[assignment]
        row.status = str(state.get("status") or "ok")
        row.last_seen_at = timestamp


def delete_manifest_rows(
    session: Session,
    source_id: int,
    file_uris: Iterable[str],
) -> None:
    uris = tuple(str(file_uri) for file_uri in file_uris if file_uri)
    if not uris:
        return
    session.execute(
        delete(LogFileManifest).where(
            LogFileManifest.source_id == source_id,
            LogFileManifest.file_uri.in_(uris),
        )
    )


def _partition_key(value: PartitionValue | None) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return value.isoformat() if value is not None else None


def reset_log_control_state(session: Session, source_id: int) -> None:
    session.execute(
        delete(LogFileManifest).where(LogFileManifest.source_id == source_id)
    )
    session.execute(
        delete(LogStreamState).where(LogStreamState.source_id == source_id)
    )
