from __future__ import annotations

import time
from threading import Lock

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.credentials.store import (
    CredentialSecretStore,
    KeyringCredentialSecretStore,
)
from datacoolie_studio.domains.logs import control as log_control
from datacoolie_studio.domains.logs.discovery import LogSyncSpec
from datacoolie_studio.domains.logs.file_state import file_revision_from_json
from datacoolie_studio.domains.logs.refresh_planning import (
    planner_state_from_row,
    stream_definitions,
)
from datacoolie_studio.domains.logs.planner import plan_stream_sync
from datacoolie_studio.domains.logs.source_config import resolve_log_source_paths
from datacoolie_studio.domains.sources.storage_binding import binding_from_source
from datacoolie_studio.domains.storage.adapters import StorageRevision
from datacoolie_studio.domains.storage.factory import create_storage_adapter


_pending_changes_lock = Lock()
_pending_changes_cache: dict[int, tuple[float, bool]] = {}


def log_source_has_pending_changes(
    session: Session,
    source: EnvironmentSource,
    *,
    ttl_seconds: float = 0.0,
    secret_store: CredentialSecretStore | None = None,
) -> bool:
    """Return whether a cached Log source has files not represented by its manifest."""

    if source.source_kind != "logs":
        return False
    if ttl_seconds > 0:
        now = time.monotonic()
        with _pending_changes_lock:
            entry = _pending_changes_cache.get(source.id)
            if entry is not None and entry[0] > now:
                return entry[1]
    result = _compute_log_source_pending_changes(
        session,
        source,
        secret_store=secret_store,
    )
    if ttl_seconds > 0:
        with _pending_changes_lock:
            _pending_changes_cache[source.id] = (
                time.monotonic() + ttl_seconds,
                result,
            )
    return result


def invalidate_pending_changes(source_id: int) -> None:
    with _pending_changes_lock:
        _pending_changes_cache.pop(source_id, None)


def _compute_log_source_pending_changes(
    session: Session,
    source: EnvironmentSource,
    *,
    secret_store: CredentialSecretStore | None = None,
) -> bool:
    persisted_states = log_control.stream_states(session, source.id)
    resolved_paths = resolve_log_source_paths(source)
    execution_uri = resolved_paths.execution_logs_uri or source.uri
    system_uri = resolved_paths.system_logs_uri
    adapter = create_storage_adapter(
        binding_from_source(source),
        uri=source.uri,
        session=session,
        secret_store=secret_store or KeyringCredentialSecretStore(),
    )
    persisted_manifest_rows = log_control.manifest_rows(session, source.id)
    manifests_by_kind: dict[str, dict[str, StorageRevision]] = {}
    for row in persisted_manifest_rows:
        parsed_revision = file_revision_from_json(
            row.file_uri,
            row.revision_json,
        )
        if parsed_revision is not None:
            manifests_by_kind.setdefault(row.file_kind, {})[
                row.file_uri
            ] = parsed_revision
    for definition in stream_definitions(execution_uri, system_uri):
        plan = plan_stream_sync(
            adapter,
            definition,
            state=planner_state_from_row(
                persisted_states.get(definition.stream_kind)
            ),
            manifest=manifests_by_kind.get(definition.stream_kind, {}),
            spec=LogSyncSpec(),
        )
        if plan.candidates:
            return True
    return False
