"""Prepare staged SQL file writes for the metadata write saga."""

from __future__ import annotations

from typing import Any
import json

from sqlalchemy.orm import Session
from sqlalchemy import select
from datacoolie_studio.domains.credentials.store import credential_profile_guards

from datacoolie_studio.db.models import EnvironmentSource, MetadataWriteItem
from datacoolie_studio.domains.credentials.store import CredentialSecretStore, KeyringCredentialSecretStore
from datacoolie_studio.domains.metadata.document_primitives import validation_issue
from datacoolie_studio.domains.metadata.errors import (
    MetadataConflictError,
    MetadataValidationError,
)
from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.domains.storage.revision_contract import same_revision
from datacoolie_studio.domains.metadata.storage_io import (
    read_storage_bytes,
    storage_for_source,
)
from datacoolie_studio.domains.sql.reader import (
    SqlReferenceError,
    resolve_sql_file_target_with_source,
)
from datacoolie_studio.domains.storage.errors import StorageNotFoundError, StorageConfigurationError, StorageAuthenticationError
from datacoolie_studio.domains.storage.factory import effective_storage_scope
from datacoolie_studio.domains.sources.storage_binding import binding_from_source, binding_to_dict
from datacoolie_studio.domains.metadata.write_operations import fingerprint



def prepare_sql_file_writes(session, sources, operations, *, secret_store=None, reserved_target_uris=None):
    environment_ids = {source.environment_id for source in sources}
    profiles = session.scalars(select(EnvironmentSource.credential_profile_id).where(
        EnvironmentSource.environment_id.in_(environment_ids)
    )).all()
    with credential_profile_guards(session, profiles):
        return _prepare_sql_file_writes(session, sources, operations,
            secret_store=secret_store, reserved_target_uris=reserved_target_uris)

def _prepare_sql_file_writes(
    session: Session,
    sources: list[EnvironmentSource],
    operations: list[dict[str, Any]],
    *,
    secret_store: CredentialSecretStore | None = None,
    reserved_target_uris: set[str] | None = None,
) -> list[dict[str, Any]]:
    if not operations:
        return []
    source_by_id = {source.id: source for source in sources}
    secret_store = secret_store or KeyringCredentialSecretStore()
    storage_by_id: dict[tuple[int, str], Any] = {}
    plans: list[dict[str, Any]] = []
    seen_targets: dict[str, dict[str, Any]] = {}
    resolved: list[dict[str, Any]] = []
    for operation in operations:
        source_id = int(operation["source_id"])
        source = source_by_id.get(source_id)
        if source is None:
            raise MetadataConflictError(
                "SQL file operation references unavailable metadata source: "
                f"{source_id}"
            )
        try:
            target, owner = resolve_sql_file_target_with_source(
                source,
                str(operation["query"]),
                session=session,
                secret_store=secret_store,
            )
        except SqlReferenceError as exc:
            if "ambiguous" in str(exc):
                raise MetadataConflictError(str(exc)) from exc
            raise MetadataValidationError(
                "SQL file target is invalid",
                {
                    "status": "error",
                    "summary": {"errors": 1, "warnings": 0, "issues": 1},
                    "issues": [
                        validation_issue("error", "sql_files", 0, "query", str(exc))
                    ],
                },
            ) from exc
        target_uri = target["file_uri"]
        expected_base_uri = operation.get("base_uri")
        if expected_base_uri is not None and str(expected_base_uri) != str(
            target["base_uri"]
        ):
            raise MetadataConflictError(
                f"SQL root changed before save; reload the draft: {target_uri}"
            )
        expected_config_revision = operation.get("config_revision")
        current_config_revision = target.get("config_revision")
        if expected_config_revision is not None and (
            not isinstance(current_config_revision, dict)
            or not same_revision(current_config_revision, expected_config_revision)
        ):
            raise MetadataConflictError(
                "Project configuration changed before SQL save; reload the draft: "
                f"{target_uri}"
            )
        scope = _publication_scope(session, owner, target_uri, secret_store)
        descriptor = {
            **target, "storage_source_id": owner.id, "storage_source_uri": owner.uri,
            "storage_config_fingerprint": fingerprint(owner.source_config_json),
            "origin_config_fingerprint": fingerprint(source.source_config_json),
            "effective_scope": scope,
            "declared_query": str(operation["query"]),
        }
        identity = (source_id, owner.id, fingerprint(scope), str(operation["content"]))
        if target_uri in (reserved_target_uris or set()):
            raise MetadataConflictError(f"SQL and metadata operations share a target URI: {target_uri}")
        duplicate = seen_targets.get(target_uri)
        if duplicate is not None:
            if duplicate["identity"] != identity:
                raise MetadataConflictError(
                    "SQL target URI has conflicting origin, owner, scope or payload: "
                    f"{target_uri} (sources {duplicate['source'].id}, {source_id})"
                )
            continue
        candidate = {"source": source, "owner": owner, "operation": operation,
                     "target": target, "descriptor": descriptor, "identity": identity}
        seen_targets[target_uri] = candidate
        resolved.append(candidate)

    # Resolve every target before constructing target clients or reading bytes.
    for candidate in resolved:
        source, owner, operation, target = (candidate[key] for key in ("source", "owner", "operation", "target"))
        target_uri = target["file_uri"]
        storage_key = (owner.id, fingerprint(candidate["descriptor"]["effective_scope"]))
        if storage_key not in storage_by_id:
            storage_by_id[storage_key] = storage_for_source(
                session, owner, secret_store=secret_store, writable=True
            )
        storage = storage_by_id[storage_key]
        try:
            storage.adapter.stat(target_uri)
        except (FileNotFoundError, StorageNotFoundError):
            exists = False
            current = None
            original = None
        except Exception as exc:
            raise MetadataReadError(f"Cannot inspect SQL file: {target_uri}") from exc
        else:
            try:
                original, current = read_storage_bytes(storage, target_uri)
            except MetadataReadError as exc:
                raise MetadataReadError(
                    f"Cannot read SQL file: {target_uri}"
                ) from exc
            exists = True

        expected = operation.get("base_revision")
        content = str(operation["content"]).encode("utf-8")
        already_satisfied = False
        if exists:
            # Matching content is the durable idempotency signal for retrying a
            # successful create/replace with its original base revision.
            if original == content:
                already_satisfied = True
            elif not isinstance(expected, dict):
                raise MetadataConflictError(
                    "SQL file already exists; reload it before saving: "
                    f"{target_uri}"
                )
            elif current is None or not same_revision(current, expected):
                raise MetadataConflictError(
                    f"SQL file changed before save: {target_uri}"
                )
        elif expected is not None:
            raise MetadataConflictError(f"SQL file no longer exists: {target_uri}")
        plan = {
            "source": source,
            "descriptor": candidate["descriptor"],
            "binding_snapshot": binding_to_dict(owner),
            "operation_query": str(operation["query"]),
            "storage": storage,
            "uri": target_uri,
            "content": content,
            "exists": exists,
            "original": original,
            "current": current,
            "expected": expected,
            "already_satisfied": already_satisfied,
            "applied": False,
        }
        plans.append(plan)
    return plans


def sql_storage_source_for_item(
    session: Session,
    item: MetadataWriteItem,
    environment_id: int,
    *,
    secret_store: CredentialSecretStore | None = None,
) -> EnvironmentSource:
    """Validate frozen resolution before any target client is constructed."""
    descriptor = json.loads(item.target_descriptor_json or "{}")
    binding = json.loads(item.binding_snapshot_json or "{}")
    if not isinstance(descriptor, dict) or not descriptor.get("effective_scope"):
        raise MetadataConflictError("SQL operation has no proven storage scope; abandon and reprepare")
    owner = session.get(EnvironmentSource, descriptor.get("storage_source_id"))
    origin = session.get(EnvironmentSource, item.source_id)
    if (
        owner is None or origin is None or owner.environment_id != environment_id
        or origin.environment_id != environment_id or not owner.enabled or not origin.enabled
        or owner.uri != descriptor.get("storage_source_uri")
        or origin.uri != item.source_uri
        or item.target_uri != descriptor.get("file_uri")
        or binding_to_dict(owner) != binding
        or fingerprint(owner.source_config_json) != descriptor.get("storage_config_fingerprint")
        or fingerprint(origin.source_config_json) != descriptor.get("origin_config_fingerprint")
    ):
        raise MetadataConflictError("SQL storage owner or root changed; abandon and reprepare")
    scope = _publication_scope(
        session, owner, item.target_uri, secret_store or KeyringCredentialSecretStore()
    )
    if scope != descriptor["effective_scope"]:
        raise MetadataConflictError("SQL provider scope changed; abandon and reprepare")
    try:
        current_target, current_owner = resolve_sql_file_target_with_source(
            origin, descriptor["declared_query"], session=session,
            secret_store=secret_store,
        )
    except (SqlReferenceError, KeyError) as exc:
        raise MetadataConflictError("SQL target is no longer resolvable; abandon and reprepare") from exc
    if (
        current_owner.id != owner.id
        or current_target["file_uri"] != item.target_uri
        or current_target["base_uri"] != descriptor.get("base_uri")
        or current_target.get("config_revision") != descriptor.get("config_revision")
    ):
        raise MetadataConflictError("SQL project configuration or root changed; abandon and reprepare")
    return owner


def _publication_scope(session, owner, target_uri, secret_store):
    try:
        return effective_storage_scope(
            binding_from_source(owner), uri=target_uri, session=session, secret_store=secret_store
        )
    except (StorageConfigurationError, StorageAuthenticationError) as exc:
        raise MetadataConflictError(
            "SQL storage scope cannot be proven; configure an explicit provider "
            "host/account and available credentials before preparing"
        ) from exc
