from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource, SourceRegistration
from datacoolie_studio.domains.code_artifacts.service import ensure_code_artifact_materialization
from datacoolie_studio.domains.credentials.store import CredentialSecretStore
from datacoolie_studio.domains.environment_caches import invalidate_environment_derived_caches
from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.domains.metadata.service import ensure_metadata_materialization
from datacoolie_studio.domains.source_observation.repository import (
    reset_observation,
    resume_observation,
)
from datacoolie_studio.domains.sources.discovery import DiscoveredSource
from datacoolie_studio.domains.sources.mutation_support import (
    clear_source_read_check as _clear_source_read_check,
    delete_registration_if_orphan as _delete_registration_if_orphan,
    delete_source_sync_records as _delete_source_sync_records,
    invalidate_code_artifact_state as _invalidate_code_artifact_state,
    is_managed_project_code_source as _is_managed_project_code_source,
    is_managed_project_metadata_source as _is_managed_project_metadata_source,
    is_managed_project_sql_source as _is_managed_project_sql_source,
    json_object as _json_object,
)
from datacoolie_studio.domains.sources.registration import (
    canonicalize_location_config,
    configured_location_dict,
)
from datacoolie_studio.domains.sources.storage_binding import binding_from_source
from datacoolie_studio.domains.sources.persistence import (
    storage_identity_from_binding as _storage_identity_from_binding,
)
from datacoolie_studio.domains.storage.uri import normalized_source_uri
from datacoolie_studio.domains.storage.identity import configured_storage_scope
from datacoolie_studio.domains.storage.binding import StorageBinding
from datacoolie_studio.domains.metadata.write_operations import ensure_source_not_in_active_write
from datacoolie_studio.domains.sync import service as sync


def source_import_item(
    source: EnvironmentSource,
    discovered: DiscoveredSource,
    status: str,
) -> dict:
    return {
        "status": status,
        "id": source.id,
        "source_kind": source.source_kind,
        "uri": source.uri,
        "label": source.label,
        "record_counts": discovered.record_counts,
        "source_config": _json_object(source.source_config_json),
        "configured_location": configured_location_dict(source),
    }


def _refresh_managed_project_metadata_source(
    session: Session,
    source: EnvironmentSource,
    discovered: DiscoveredSource,
    *,
    enabled: bool,
    registration: SourceRegistration,
) -> None:
    current_config = _json_object(source.source_config_json)
    if not _is_managed_project_metadata_source(source, current_config):
        return

    if (source.registration_id != registration.id or discovered.source_config != current_config
            or current_config.get("discovery_disabled_by_scan") is True):
        ensure_source_not_in_active_write(session, source.id, environment_id=source.environment_id)

    if source.registration_id != registration.id:
        previous_registration_id = source.registration_id
        source.registration_id = registration.id
        session.flush()
        if previous_registration_id is not None:
            _delete_registration_if_orphan(session, previous_registration_id)

    binding = binding_from_source(source)
    canonical_config, _, _ = canonicalize_location_config(
        discovered.source_config,
        binding,
    )
    changed = canonical_config != current_config
    was_disabled_by_scan = current_config.get("discovery_disabled_by_scan") is True
    if changed:
        source.source_config_json = json.dumps(canonical_config, sort_keys=True)
        _clear_source_read_check(source)
        _delete_source_sync_records(session, source)
    if was_disabled_by_scan:
        source.enabled = enabled
        if enabled:
            resume_observation(session, source.id)
        else:
            reset_observation(session, source.id)
    if changed:
        reset_observation(session, source.id)
    if changed or was_disabled_by_scan:
        invalidate_environment_derived_caches(
            source.environment_id,
            structural=True,
            identity=source.source_kind in {"metadata", "logs"},
        )


def _mark_missing_project_metadata_sources(
    session: Session,
    registration: SourceRegistration,
    *,
    seen_source_ids: set[int],
    storage: object | None,
) -> list[dict]:
    """Disable only managed metadata sources absent from a complete result.

    Project scans may coexist with manually-added metadata sources.  The
    discovery marker therefore opts a source into reconciliation explicitly;
    a user override sets ``discovery_managed`` to false and keeps it safe from
    a later scan.
    """

    target_provider, _ = _storage_identity_from_binding(
        registration.canonical_uri,
        storage,
    )
    registrations = session.scalars(
        select(SourceRegistration).where(
            SourceRegistration.environment_id == registration.environment_id,
            SourceRegistration.purpose == "project",
        )
    ).all()
    stale: list[dict] = []
    for candidate_registration in registrations:
        if normalized_source_uri(candidate_registration.canonical_uri) != normalized_source_uri(
            registration.canonical_uri
        ):
            continue
        for source in list(candidate_registration.sources):
            if source.source_kind != "metadata" or source.id in seen_source_ids:
                continue
            target_binding = storage if isinstance(storage, StorageBinding) else StorageBinding(
                provider=target_provider,
                auth_mode=str((storage or {}).get("auth_mode") or ("none" if target_provider == "local" else "ambient")),
                credential_profile_id=(storage or {}).get("credential_profile_id"),
                options=dict((storage or {}).get("options") or {}),
            )
            if configured_storage_scope(session, binding_from_source(source), uri=source.uri) != configured_storage_scope(
                session, target_binding, uri=registration.canonical_uri
            ):
                continue
            config = _json_object(source.source_config_json)
            if not _is_managed_project_metadata_source(source, config):
                continue
            already_missing = config.get("discovery_state") == "missing"
            if source.source_kind in {"metadata", "sql"} and (not already_missing or source.enabled):
                ensure_source_not_in_active_write(session, source.id, environment_id=source.environment_id)
            changed = not already_missing
            config["discovery_state"] = "missing"
            if source.enabled:
                source.enabled = False
                config["discovery_disabled_by_scan"] = True
                changed = True
            elif "discovery_disabled_by_scan" not in config:
                config["discovery_disabled_by_scan"] = False
            if changed:
                source.source_config_json = json.dumps(config, sort_keys=True)
                _clear_source_read_check(source)
                _delete_source_sync_records(session, source)
                reset_observation(session, source.id)
                invalidate_environment_derived_caches(
                    source.environment_id,
                    structural=True,
                    identity=source.source_kind in {"metadata", "logs"},
                )
            stale.append(
                source_import_item(
                    source,
                    DiscoveredSource(
                        source_kind="metadata",
                        uri=source.uri,
                        label=source.label,
                        source_config=config,
                    ),
                    "stale",
                )
            )
    return stale


def _refresh_managed_project_sql_source(
    session: Session,
    source: EnvironmentSource,
    discovered: DiscoveredSource,
    *,
    enabled: bool,
    registration: SourceRegistration,
) -> None:
    current_config = _json_object(source.source_config_json)
    if not _is_managed_project_sql_source(source, current_config):
        return

    if (source.registration_id != registration.id or discovered.source_config != current_config
            or current_config.get("discovery_disabled_by_scan") is True):
        ensure_source_not_in_active_write(session, source.id, environment_id=source.environment_id)

    if source.registration_id != registration.id:
        previous_registration_id = source.registration_id
        source.registration_id = registration.id
        session.flush()
        if previous_registration_id is not None:
            _delete_registration_if_orphan(session, previous_registration_id)

    binding = binding_from_source(source)
    canonical_config, _, _ = canonicalize_location_config(
        discovered.source_config,
        binding,
    )
    changed = canonical_config != current_config
    was_disabled_by_scan = current_config.get("discovery_disabled_by_scan") is True
    if changed:
        source.source_config_json = json.dumps(canonical_config, sort_keys=True)
        _clear_source_read_check(source)
        _delete_source_sync_records(session, source)
    if was_disabled_by_scan:
        source.enabled = enabled
        if enabled:
            resume_observation(session, source.id)
        else:
            reset_observation(session, source.id)
    if changed:
        reset_observation(session, source.id)
    if changed or was_disabled_by_scan:
        invalidate_environment_derived_caches(
            source.environment_id,
            structural=True,
            identity=source.source_kind in {"metadata", "logs"},
        )


def _mark_missing_project_sql_sources(
    session: Session,
    registration: SourceRegistration,
    *,
    seen_source_ids: set[int],
    storage: object | None,
) -> list[dict]:
    target_provider, _ = _storage_identity_from_binding(
        registration.canonical_uri,
        storage,
    )
    registrations = session.scalars(
        select(SourceRegistration).where(
            SourceRegistration.environment_id == registration.environment_id,
            SourceRegistration.purpose == "project",
        )
    ).all()
    stale: list[dict] = []
    for candidate_registration in registrations:
        if normalized_source_uri(candidate_registration.canonical_uri) != normalized_source_uri(
            registration.canonical_uri
        ):
            continue
        for source in list(candidate_registration.sources):
            if source.source_kind != "sql" or source.id in seen_source_ids:
                continue
            target_binding = storage if isinstance(storage, StorageBinding) else StorageBinding(
                provider=target_provider,
                auth_mode=str((storage or {}).get("auth_mode") or ("none" if target_provider == "local" else "ambient")),
                credential_profile_id=(storage or {}).get("credential_profile_id"),
                options=dict((storage or {}).get("options") or {}),
            )
            if configured_storage_scope(session, binding_from_source(source), uri=source.uri) != configured_storage_scope(
                session, target_binding, uri=registration.canonical_uri
            ):
                continue
            config = _json_object(source.source_config_json)
            if not _is_managed_project_sql_source(source, config):
                continue
            already_missing = config.get("discovery_state") == "missing"
            if source.source_kind in {"metadata", "sql"} and (not already_missing or source.enabled):
                ensure_source_not_in_active_write(session, source.id, environment_id=source.environment_id)
            changed = not already_missing
            config["discovery_state"] = "missing"
            if source.enabled:
                source.enabled = False
                config["discovery_disabled_by_scan"] = True
                changed = True
            elif "discovery_disabled_by_scan" not in config:
                config["discovery_disabled_by_scan"] = False
            if changed:
                source.source_config_json = json.dumps(config, sort_keys=True)
                _clear_source_read_check(source)
                _delete_source_sync_records(session, source)
                reset_observation(session, source.id)
                invalidate_environment_derived_caches(
                    source.environment_id,
                    structural=True,
                    identity=source.source_kind in {"metadata", "logs"},
                )
            stale.append(
                source_import_item(
                    source,
                    DiscoveredSource(
                        source_kind="sql",
                        uri=source.uri,
                        label=source.label,
                        source_config=config,
                    ),
                    "stale",
                )
            )
    return stale


def _refresh_managed_project_code_source(
    session: Session,
    source: EnvironmentSource,
    discovered: DiscoveredSource,
    *,
    enabled: bool,
    registration: SourceRegistration,
) -> None:
    current_config = _json_object(source.source_config_json)
    if not _is_managed_project_code_source(source, current_config):
        return

    if source.registration_id != registration.id:
        previous_registration_id = source.registration_id
        source.registration_id = registration.id
        session.flush()
        if previous_registration_id is not None:
            _delete_registration_if_orphan(session, previous_registration_id)

    binding = binding_from_source(source)
    canonical_config, _, _ = canonicalize_location_config(
        discovered.source_config,
        binding,
    )
    changed = canonical_config != current_config
    was_disabled_by_scan = current_config.get("discovery_disabled_by_scan") is True
    if changed:
        source.source_config_json = json.dumps(canonical_config, sort_keys=True)
        _invalidate_code_artifact_state(session, source)
    if was_disabled_by_scan:
        source.enabled = enabled
        if enabled:
            resume_observation(session, source.id)
        else:
            reset_observation(session, source.id)
    if changed:
        reset_observation(session, source.id)
    if changed or was_disabled_by_scan:
        invalidate_environment_derived_caches(
            source.environment_id, structural=True, identity=source.source_kind in {"metadata", "logs"}
        )


def _mark_missing_project_code_sources(
    session: Session,
    registration: SourceRegistration,
    *,
    seen_source_ids: set[int],
    storage: object | None,
) -> list[dict]:
    target_provider, _ = _storage_identity_from_binding(
        registration.canonical_uri,
        storage,
    )
    registrations = session.scalars(
        select(SourceRegistration).where(
            SourceRegistration.environment_id == registration.environment_id,
            SourceRegistration.purpose == "project",
        )
    ).all()
    stale: list[dict] = []
    for candidate_registration in registrations:
        if normalized_source_uri(candidate_registration.canonical_uri) != normalized_source_uri(
            registration.canonical_uri
        ):
            continue
        for source in list(candidate_registration.sources):
            if source.source_kind != "code" or source.id in seen_source_ids:
                continue
            target_binding = storage if isinstance(storage, StorageBinding) else StorageBinding(
                provider=target_provider,
                auth_mode=str((storage or {}).get("auth_mode") or ("none" if target_provider == "local" else "ambient")),
                credential_profile_id=(storage or {}).get("credential_profile_id"),
                options=dict((storage or {}).get("options") or {}),
            )
            if configured_storage_scope(session, binding_from_source(source), uri=source.uri) != configured_storage_scope(
                session, target_binding, uri=registration.canonical_uri
            ):
                continue
            config = _json_object(source.source_config_json)
            if not _is_managed_project_code_source(source, config):
                continue
            already_missing = config.get("discovery_state") == "missing"
            if source.source_kind in {"metadata", "sql"} and (not already_missing or source.enabled):
                ensure_source_not_in_active_write(session, source.id, environment_id=source.environment_id)
            changed = not already_missing
            config["discovery_state"] = "missing"
            if source.enabled:
                source.enabled = False
                config["discovery_disabled_by_scan"] = True
                changed = True
            elif "discovery_disabled_by_scan" not in config:
                config["discovery_disabled_by_scan"] = False
            if changed:
                source.source_config_json = json.dumps(config, sort_keys=True)
                _invalidate_code_artifact_state(session, source)
                reset_observation(session, source.id)
                invalidate_environment_derived_caches(
                    source.environment_id, structural=True, identity=False
                )
            stale.append(
                source_import_item(
                    source,
                    DiscoveredSource(
                        source_kind="code",
                        uri=source.uri,
                        label=source.label,
                        source_config=config,
                    ),
                    "stale",
                )
            )
    return stale


def _project_code_reconciliation_allowed(errors: list[dict]) -> bool:
    """Only reconcile missing artifacts after a complete code inventory.

    A provider error or bounded/partial listing cannot distinguish a missing
    artifact from an artifact that was not returned.  Treating either case as
    stale would disable valid sources during a transient cloud failure.
    """

    blocking_codes = {
        "artifact_index_error",
        "code_inventory_error",
        "code_inventory_incomplete",
        "invalid_discovery_mode",
        "namespace_package_manual_mapping",
        "package_root_escape",
        "pyproject_layout_error",
        "pyproject_layout_unrecognized",
        "pyproject_parse_error",
        "pyproject_read_error",
        "pyproject_too_large",
        "empty_code_artifact",
    }
    return not any(
        isinstance(error, dict) and error.get("code") in blocking_codes
        for error in errors
    )


def _project_metadata_reconciliation_allowed(errors: list[dict]) -> bool:
    """Do not mark metadata missing after a failed or partial inventory."""

    return not any(
        isinstance(error, dict)
        and error.get("component") == "metadata"
        and error.get("severity", "error") != "warning"
        for error in errors
    )


def _project_sql_reconciliation_allowed(errors: list[dict]) -> bool:
    """Only reconcile SQL roots when the SQL component scan is healthy."""

    return not any(
        isinstance(error, dict)
        and error.get("component") == "sql"
        and error.get("severity", "error") != "warning"
        for error in errors
    )


def _initialize_discovered_sources(
    session: Session,
    sources: list[EnvironmentSource],
    *,
    secret_store: CredentialSecretStore | None = None,
) -> dict[str, Any]:
    """Validate and materialize the selected Metadata/Code sources."""
    auto_validated = 0
    auto_synced = 0
    errors: list[dict[str, Any]] = []
    for source in sources:
        if not source.enabled:
            continue
        try:
            with sync.source_refresh_guard(source.id) as acquired:
                if not acquired:
                    errors.append(_source_initialization_error(source, "Source refresh is already running"))
                    continue
                if source.source_kind == "metadata":
                    ensure_metadata_materialization(
                        session, source, secret_store=secret_store
                    )
                elif source.source_kind == "code":
                    ensure_code_artifact_materialization(
                        session, source, secret_store=secret_store
                    )
                else:
                    continue
            status = sync.source_sync_status(session, source)
            if source.read_checked_at is not None:
                auto_validated += 1
            if status["status"] == "ok":
                auto_synced += 1
            else:
                errors.append(_source_initialization_error(source, status.get("message") or "Initial sync failed"))
        except MetadataReadError as exc:
            if source.read_checked_at is not None:
                auto_validated += 1
            errors.append(_source_initialization_error(source, str(exc)))
        except Exception as exc:
            session.rollback()
            errors.append(_source_initialization_error(source, str(exc)))
    return {
        "auto_validated": auto_validated,
        "auto_synced": auto_synced,
        "auto_sync_errors": errors,
    }


def _source_initialization_error(source: EnvironmentSource, message: str) -> dict[str, Any]:
    return {
        "source_id": source.id,
        "source_kind": source.source_kind,
        "uri": source.uri,
        "message": message,
    }
