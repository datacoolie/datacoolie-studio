"""Revision contracts shared by metadata drafts and save workflows."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.credentials.store import CredentialSecretStore
from datacoolie_studio.domains.metadata.document_lifecycle import document_sheets_as_rows
from datacoolie_studio.domains.metadata.errors import MetadataConflictError
from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.domains.metadata.storage_io import (
    current_revision as storage_current_revision,
    storage_for_source,
)
from datacoolie_studio.domains.storage.revision_contract import same_revision


def source_revision(uri: str) -> dict[str, Any]:
    path = Path(uri).expanduser()
    if not path.exists():
        raise MetadataReadError(f"Metadata file not found: {uri}")
    stat = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "content_hash": digest,
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }


def environment_revision_sources(
    revision: dict[str, Any],
) -> dict[int, dict[str, Any]]:
    sources = revision.get("sources") if isinstance(revision, dict) else []
    result: dict[int, dict[str, Any]] = {}
    if not isinstance(sources, list):
        return result
    for item in sources:
        if not isinstance(item, dict):
            continue
        try:
            source_id = int(item.get("source_id"))
        except (TypeError, ValueError):
            continue
        source_revision_value = item.get("revision")
        result[source_id] = (
            source_revision_value
            if isinstance(source_revision_value, dict)
            else item
        )
    return result


def document_source_ids(document: dict[str, Any]) -> set[int]:
    source_ids: set[int] = set()
    for rows in document_sheets_as_rows(document).values():
        for row in rows:
            source_id = row_source_id(row)
            if source_id is not None:
                source_ids.add(source_id)
    return source_ids


def row_source_id(row: dict[str, Any]) -> int | None:
    value = row.get("__metadata_source_id")
    try:
        source_id = int(value)
    except (TypeError, ValueError):
        return None
    return source_id if source_id > 0 else None


def assert_source_revision_current(
    session: Session,
    source: EnvironmentSource,
    expected_revision: dict[str, Any],
    *,
    secret_store: CredentialSecretStore | None = None,
) -> None:
    storage = storage_for_source(session, source, secret_store=secret_store)
    current = storage_current_revision(storage, source, include_content_hash=True)
    if not same_revision(current, expected_revision):
        raise MetadataConflictError("Source file changed before draft save")


def assert_environment_revision_current(
    session: Session,
    document: dict[str, Any],
    expected_revision: dict[str, Any],
    *,
    secret_store: CredentialSecretStore | None = None,
) -> None:
    source_revision_map = environment_revision_sources(
        expected_revision or document.get("source", {}).get("revision") or {}
    )
    source_ids = document_source_ids(document)
    missing = sorted(source_ids - set(source_revision_map))
    if missing:
        raise MetadataConflictError(
            "Metadata source revision missing for source ids: "
            + ", ".join(str(item) for item in missing)
        )
    for source_id in sorted(source_ids):
        source = session.get(EnvironmentSource, source_id)
        if source is None or source.source_kind != "metadata":
            raise MetadataReadError(f"Metadata source not found: {source_id}")
        source_revision = source_revision_map[source_id]
        storage = storage_for_source(session, source, secret_store=secret_store)
        current = storage_current_revision(storage, source, include_content_hash=True)
        if not same_revision(current, source_revision):
            raise MetadataConflictError(f"Source file changed before save: {source.uri}")
