"""Indexed candidate selection shared by System log preview and export."""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import quote, unquote

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import LogFileManifest
from datacoolie_studio.db.session import ensure_workspace_transaction
from datacoolie_studio.domains.logs.system_log_filename import normalize_filename_job_id
from datacoolie_studio.domains.storage.uri import uri_basename


def system_log_manifest_statement(source_ids: list[int], job_id: str) -> Select:
    """Select indexed job files; sessions stay record-scoped."""

    lookup = normalize_filename_job_id(job_id)
    # Separate indexable branches avoid an OR that SQLite can turn into a
    # source-wide scan. UNION removes duplicate file IDs from ambiguous names.
    scope = (
        LogFileManifest.source_id.in_(source_ids),
        LogFileManifest.file_kind == "system_json",
    )
    ids = select(LogFileManifest.id).where(
        *scope, LogFileManifest.filename_job_id == lookup
    ).union(
        select(LogFileManifest.id).where(
            *scope, LogFileManifest.filename_job_id_alt == lookup
        ),
    )
    return (
        select(LogFileManifest)
        .where(LogFileManifest.id.in_(ids))
        .order_by(
            func.coalesce(
                LogFileManifest.event_min_timestamp, LogFileManifest.log_timestamp
            ).asc().nulls_last(),
            LogFileManifest.id.asc(),
        )
    )


def system_log_manifests(
    session: Session, source_ids: list[int], job_id: str, *, page_size: int = 256,
) -> Iterator[LogFileManifest]:
    """Use filename containment only when the indexed job has no files."""

    # Probe and all manifest pages must see one inventory even if sync publishes
    # concurrently. Keep the caller's transaction and pending mutations intact.
    ensure_workspace_transaction(session)
    statement = system_log_manifest_statement(source_ids, job_id)
    indexed = session.scalar(
        select(statement.with_only_columns(LogFileManifest.id).order_by(None).exists())
    )
    if not indexed:
        statement = select(LogFileManifest).where(
            LogFileManifest.source_id.in_(source_ids),
            LogFileManifest.file_kind == "system_json",
            LogFileManifest.filename_job_id.is_(None),
        ).order_by(
            func.coalesce(
                LogFileManifest.event_min_timestamp, LogFileManifest.log_timestamp
            ).asc().nulls_last(),
            LogFileManifest.id.asc(),
        )
    size = max(1, int(page_size))
    offset = 0
    while True:
        page = list(session.scalars(statement.limit(size).offset(offset)))
        if not page:
            break
        for manifest in page:
            if indexed or filename_contains_job(manifest.file_uri, job_id):
                yield manifest
        offset += len(page)


def filename_contains_job(file_uri: str, job_id: str) -> bool:
    """Literal, case-sensitive basename matching without storage access."""

    try:
        basename = uri_basename(file_uri)
    except (TypeError, ValueError, OSError):
        return False
    lookup = normalize_filename_job_id(job_id)
    tokens = {lookup, quote(lookup, safe="-_.~", encoding="utf-8", errors="strict")}
    if job_id.strip():
        tokens.add(job_id)
    # file:// extraction has already decoded URI escaping; token decoding here
    # also supports producer escapes on cloud/plain filenames, exactly once.
    names = (basename, unquote(basename, encoding="utf-8", errors="replace"))
    return any(token in name for token in tokens if token for name in names)


def filename_job_candidates(manifest: LogFileManifest) -> tuple[str, ...]:
    return tuple(
        candidate
        for candidate in (manifest.filename_job_id, manifest.filename_job_id_alt)
        if candidate is not None
    )


def record_matches_filename(row: dict, candidates: tuple[str, ...]) -> bool:
    return not candidates or normalize_filename_job_id(row.get("job_id")) in candidates
