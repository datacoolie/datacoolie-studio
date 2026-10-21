from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import Environment
from datacoolie_studio.domains.freshness.service import source_freshness_statuses
from datacoolie_studio.domains.sources.repository import list_sources
from datacoolie_studio.domains.sources.serialization import source_to_dict
from datacoolie_studio.domains.sync import service as sync


SOURCE_KINDS = ("metadata", "sql", "logs", "code")


def sources_workspace_log_summary(session: Session, environment_id: int) -> dict[str, Any]:
    """Return the current log-source state without retained sync payloads.

    The default Sources workspace remains the owner of the full source
    configuration response.  Diagnostics only needs the configured log sources
    and their current validation, observation, sync and freshness state, so
    this opt-in projection deliberately excludes other source kinds and the
    potentially large sync execution result documents.
    """

    if session.get(Environment, environment_id) is None:
        raise LookupError(f"Environment not found: {environment_id}")
    sources = list_sources(session, environment_id, "logs")
    freshness_by_source, observations = source_freshness_statuses(session, sources)
    statuses = sync.source_sync_statuses_compact(
        session,
        sources,
        observations=observations,
    )
    compact_statuses: list[dict[str, Any]] = []
    for status in statuses:
        item = freshness_by_source.get(status["source_id"])
        if item is not None:
            status["freshness"] = {
                "state": "not_synced" if item["status"] == "not_cached" else item["status"],
                "source_modified_at": item.get("source_modified_at"),
                "cache_source_modified_at": item.get("cache_source_modified_at"),
                "cache_synced_at": item.get("cache_synced_at"),
                "summary": item.get("message"),
            }
        compact_statuses.append(_without_sync_result(status))

    cloud_source_ids = {
        source.id for source in sources if source.storage_provider != "local"
    }
    due_times = [
        (status.get("observation") or {}).get("next_check_at")
        for status in compact_statuses
        if status["source_id"] in cloud_source_ids
        and (status.get("observation") or {}).get("next_check_at") is not None
    ]
    version_payload = {
        "view": "logs-summary",
        "sources": [
            {
                "id": source.id,
                "kind": source.source_kind,
                "uri": source.uri,
                "enabled": source.enabled,
                "config": source.source_config_json,
                "storage_provider": source.storage_provider,
                "storage_auth_mode": source.storage_auth_mode,
                "updated_at": source.updated_at,
            }
            for source in sources
        ],
        "statuses": compact_statuses,
    }
    dependency_version = hashlib.sha256(
        json.dumps(
            version_payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return {
        # Keep the transport contract identical to the full workspace.  The
        # view flag changes the selected projection, not the response shape.
        "schema_version": "sources-workspace.v1",
        "environment_id": environment_id,
        "metadata_sources": [],
        "sql_sources": [],
        "log_sources": [source_to_dict(item) for item in sources],
        "code_artifacts": [],
        "statuses": compact_statuses,
        "earliest_cloud_due_at": min(due_times) if due_times else None,
        "dependency_version": dependency_version,
    }


def _without_sync_result(status: dict[str, Any]) -> dict[str, Any]:
    execution = status.get("sync_execution") or {}
    return {
        **status,
        "sync_execution": {
            key: value
            for key, value in execution.items()
            if key != "result"
        },
    }


def sources_workspace(session: Session, environment_id: int) -> dict[str, Any]:
    if session.get(Environment, environment_id) is None:
        raise LookupError(f"Environment not found: {environment_id}")
    sources = list_sources(session, environment_id)
    by_kind = {
        kind: [source for source in sources if source.source_kind == kind]
        for kind in SOURCE_KINDS
    }
    freshness_by_source, observations = source_freshness_statuses(session, sources)
    statuses = sync.source_sync_statuses(
        session,
        sources,
        observations=observations,
    )
    for status in statuses:
        item = freshness_by_source.get(status["source_id"])
        if item is None:
            continue
        status["freshness"] = {
            "state": "not_synced" if item["status"] == "not_cached" else item["status"],
            "source_modified_at": item.get("source_modified_at"),
            "cache_source_modified_at": item.get("cache_source_modified_at"),
            "cache_synced_at": item.get("cache_synced_at"),
            "summary": item.get("message"),
        }
    cloud_source_ids = {
        source.id for source in sources if source.storage_provider != "local"
    }
    due_times = [
        (status.get("observation") or {}).get("next_check_at")
        for status in statuses
        if status["source_id"] in cloud_source_ids
        and (status.get("observation") or {}).get("next_check_at") is not None
    ]
    version_payload = {
        "sources": [
            {
                "id": source.id,
                "kind": source.source_kind,
                "uri": source.uri,
                "enabled": source.enabled,
                "config": source.source_config_json,
                "storage_provider": source.storage_provider,
                "storage_auth_mode": source.storage_auth_mode,
                "updated_at": source.updated_at,
            }
            for source in sources
        ],
        "statuses": statuses,
    }
    dependency_version = hashlib.sha256(
        json.dumps(
            version_payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": "sources-workspace.v1",
        "environment_id": environment_id,
        "metadata_sources": [source_to_dict(item) for item in by_kind["metadata"]],
        "sql_sources": [source_to_dict(item) for item in by_kind["sql"]],
        "log_sources": [source_to_dict(item) for item in by_kind["logs"]],
        "code_artifacts": [source_to_dict(item) for item in by_kind["code"]],
        "statuses": statuses,
        "earliest_cloud_due_at": min(due_times) if due_times else None,
        "dependency_version": dependency_version,
    }


def list_metadata_sources(session: Session, environment_id: int) -> list[dict]:
    return _serialized_sources(session, environment_id, "metadata")


def list_sql_sources(session: Session, environment_id: int) -> list[dict]:
    return _serialized_sources(session, environment_id, "sql")


def list_code_artifacts(session: Session, environment_id: int) -> list[dict]:
    return _serialized_sources(session, environment_id, "code")


def list_log_sources(session: Session, environment_id: int) -> list[dict]:
    return _serialized_sources(session, environment_id, "logs")


def _serialized_sources(
    session: Session,
    environment_id: int,
    source_kind: str,
) -> list[dict]:
    return [
        source_to_dict(source)
        for source in list_sources(session, environment_id, source_kind)
    ]
