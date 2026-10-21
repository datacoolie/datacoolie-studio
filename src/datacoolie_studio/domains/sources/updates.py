from __future__ import annotations

import json

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.environment_caches import (
    invalidate_environment_derived_caches,
)
from datacoolie_studio.domains.logs import pending as log_pending
from datacoolie_studio.domains.source_observation.repository import (
    reset_observation,
    resume_observation,
)
from datacoolie_studio.domains.sources.mutation_support import (
    clear_source_read_check,
    delete_source_log_cache,
    delete_source_sync_records,
    ensure_registration_available,
    has_location_input,
    invalidate_code_artifact_state,
    is_managed_project_code_source,
    is_managed_project_metadata_source,
    json_object,
    replace_source_registration,
    source_input_uri,
    update_schedule,
    UNSET,
    merge_source_config,
)
from datacoolie_studio.domains.sources.persistence import sql_root_for_uri
from datacoolie_studio.domains.sources.registration import (
    canonicalize_location_config,
    get_or_create_registration,
    source_input_locations,
    update_registration_input,
)
from datacoolie_studio.domains.sources.repository import get_source
from datacoolie_studio.domains.sources.storage_binding import (
    apply_binding,
    binding_to_dict,
    validate_and_normalize_binding,
)


def update_code_artifact(
    session: Session,
    environment_id: int,
    source_id: int,
    *,
    uri: str | None = None,
    label=UNSET,
    enabled: bool | None = None,
    source_config: dict | None = None,
    storage: dict | None = None,
) -> EnvironmentSource | None:
    source = get_source(session, environment_id, source_id, "code")
    if source is None:
        return None
    structural_changed = False
    enabled_changed = enabled is not None and source.enabled != enabled
    reenabled = enabled is True and not source.enabled
    current_config = json_object(source.source_config_json)
    user_override = any(
        value is not UNSET and value is not None
        for value in (uri, label, enabled, source_config, storage)
    )
    managed_override = user_override and is_managed_project_code_source(
        source, current_config
    )
    candidate_config = merge_source_config(current_config, source_config)
    if managed_override:
        candidate_config["discovery_managed"] = False
    if uri is not None or source_config is not None or storage is not None:
        normalized_uri, binding = validate_and_normalize_binding(
            session,
            uri=uri if uri is not None else source.uri,
            storage=storage if storage is not None else binding_to_dict(source),
            source_config=candidate_config,
        )
        canonical_config, input_locations, canonical_locations = canonicalize_location_config(
            candidate_config, binding
        )
        if storage is not None or has_location_input(uri, source_config):
            if source_config is None:
                input_locations = source_input_locations(source)
            registration, _ = get_or_create_registration(
                session,
                environment_id=environment_id,
                purpose="code",
                input_uri=uri if uri is not None else source_input_uri(source),
                canonical_uri=normalized_uri,
                binding=binding,
                input_locations=input_locations,
                canonical_locations=canonical_locations,
            )
            ensure_registration_available(registration, source)
            update_registration_input(
                registration,
                input_uri=uri if uri is not None else source_input_uri(source),
                input_locations=input_locations,
            )
            replace_source_registration(session, source, registration.id)
        if normalized_uri != source.uri or binding_to_dict(source) != {
            "provider": binding.provider,
            "auth_mode": binding.auth_mode,
            "credential_profile_id": binding.credential_profile_id,
            "options": binding.options,
        }:
            source.uri = normalized_uri
            apply_binding(source, binding)
            structural_changed = True
    if source_config is not None:
        config_json = json.dumps(canonical_config, sort_keys=True)
        if config_json != (source.source_config_json or "{}"):
            source.source_config_json = config_json
            structural_changed = True
    elif managed_override:
        config_json = json.dumps(candidate_config, sort_keys=True)
        if config_json != (source.source_config_json or "{}"):
            source.source_config_json = config_json
            structural_changed = True
    if structural_changed:
        invalidate_code_artifact_state(session, source)
    if label is not UNSET:
        source.label = label
    if enabled is not None:
        source.enabled = enabled
    if reenabled:
        resume_observation(session, source.id)
    elif structural_changed:
        reset_observation(session, source.id)
    if structural_changed or enabled_changed:
        invalidate_environment_derived_caches(source.environment_id, structural=True, identity=False)
    return source


def update_metadata_source(
    session: Session,
    environment_id: int,
    source_id: int,
    *,
    uri: str | None = None,
    label=UNSET,
    enabled: bool | None = None,
    source_config: dict | None = None,
    storage: dict | None = None,
) -> EnvironmentSource | None:
    source = get_source(session, environment_id, source_id, "metadata")
    if source is None:
        return None
    structural_changed = False
    enabled_changed = enabled is not None and source.enabled != enabled
    reenabled = enabled is True and not source.enabled
    current_config = json_object(source.source_config_json)
    user_override = any(
        value is not UNSET and value is not None
        for value in (uri, label, enabled, source_config, storage)
    )
    managed_override = user_override and is_managed_project_metadata_source(
        source,
        current_config,
    )
    candidate_config = merge_source_config(current_config, source_config)
    if managed_override:
        candidate_config["discovery_managed"] = False
    if uri is not None or source_config is not None or storage is not None:
        normalized_uri, binding = validate_and_normalize_binding(
            session,
            uri=uri if uri is not None else source.uri,
            storage=storage if storage is not None else binding_to_dict(source),
            source_config=candidate_config,
        )
        canonical_config, input_locations, canonical_locations = canonicalize_location_config(
            candidate_config, binding
        )
        if storage is not None or has_location_input(uri, source_config):
            if source_config is None:
                input_locations = source_input_locations(source)
            registration, _ = get_or_create_registration(
                session,
                environment_id=environment_id,
                purpose="metadata",
                input_uri=uri if uri is not None else source_input_uri(source),
                canonical_uri=normalized_uri,
                binding=binding,
                input_locations=input_locations,
                canonical_locations=canonical_locations,
            )
            ensure_registration_available(registration, source)
            update_registration_input(
                registration,
                input_uri=uri if uri is not None else source_input_uri(source),
                input_locations=input_locations,
            )
            replace_source_registration(session, source, registration.id)
        binding_changed = binding_to_dict(source) != {
            "provider": binding.provider,
            "auth_mode": binding.auth_mode,
            "credential_profile_id": binding.credential_profile_id,
            "options": binding.options,
        }
        if normalized_uri != source.uri or binding_changed:
            clear_source_read_check(source)
            delete_source_sync_records(session, source)
            source.uri = normalized_uri
            apply_binding(source, binding)
            structural_changed = True
        if source_config is not None:
            config_json = json.dumps(canonical_config, sort_keys=True)
            if config_json != (source.source_config_json or "{}"):
                source.source_config_json = config_json
                clear_source_read_check(source)
                delete_source_sync_records(session, source)
                structural_changed = True
    elif managed_override:
        config_json = json.dumps(candidate_config, sort_keys=True)
        if config_json != (source.source_config_json or "{}"):
            source.source_config_json = config_json
            structural_changed = True
    if label is not UNSET:
        source.label = label
    if enabled is not None:
        source.enabled = enabled
    if reenabled:
        resume_observation(session, source.id)
    elif structural_changed:
        reset_observation(session, source.id)
    if structural_changed or enabled_changed:
        invalidate_environment_derived_caches(source.environment_id, structural=True)
    return source


def update_sql_source(
    session: Session,
    environment_id: int,
    source_id: int,
    *,
    uri: str | None = None,
    label=UNSET,
    enabled: bool | None = None,
    source_config: dict | None = None,
    storage: dict | None = None,
) -> EnvironmentSource | None:
    source = get_source(session, environment_id, source_id, "sql")
    if source is None:
        return None
    changed = False
    enabled_changed = enabled is not None and source.enabled != enabled
    reenabled = enabled is True and not source.enabled
    current_config = json_object(source.source_config_json)
    if uri is not None or source_config is not None or storage is not None:
        next_uri = uri if uri is not None else source.uri
        next_config = merge_source_config(current_config, source_config)
        if uri is not None and source_config is None:
            next_config["sql_root_uri"] = sql_root_for_uri(next_uri)
        next_config.setdefault("discovery_mode", "manual_sql_path")
        next_config.setdefault("sql_root_uri", sql_root_for_uri(next_uri))
        normalized_uri, binding = validate_and_normalize_binding(
            session,
            uri=next_uri,
            storage=storage if storage is not None else binding_to_dict(source),
            source_config=next_config,
        )
        canonical_config, input_locations, canonical_locations = canonicalize_location_config(
            next_config, binding
        )
        registration, _ = get_or_create_registration(
            session,
            environment_id=environment_id,
            purpose="sql",
            input_uri=uri if uri is not None else source_input_uri(source),
            canonical_uri=normalized_uri,
            binding=binding,
            input_locations=(
                input_locations
                if uri is not None or source_config is not None
                else source_input_locations(source)
            ),
            canonical_locations=canonical_locations,
        )
        ensure_registration_available(registration, source)
        update_registration_input(
            registration,
            input_uri=uri if uri is not None else source_input_uri(source),
            input_locations=(
                input_locations
                if uri is not None or source_config is not None
                else source_input_locations(source)
            ),
        )
        replace_source_registration(session, source, registration.id)
        next_binding = {
            "provider": binding.provider,
            "auth_mode": binding.auth_mode,
            "credential_profile_id": binding.credential_profile_id,
            "options": binding.options,
        }
        binding_changed = binding_to_dict(source) != next_binding
        config_json = json.dumps(canonical_config, sort_keys=True)
        if normalized_uri != source.uri or binding_changed:
            source.uri = normalized_uri
            apply_binding(source, binding)
            changed = True
        if config_json != (source.source_config_json or "{}"):
            source.source_config_json = config_json
            changed = True
    if label is not UNSET:
        source.label = label
    if enabled is not None:
        source.enabled = enabled
    if reenabled:
        resume_observation(session, source.id)
    elif changed:
        clear_source_read_check(source)
        delete_source_sync_records(session, source)
        reset_observation(session, source.id, pending_changes=False)
    if changed or enabled_changed:
        invalidate_environment_derived_caches(source.environment_id, structural=True, identity=False)
    return source


def update_log_source(
    session: Session,
    environment_id: int,
    path_id: int,
    *,
    uri: str | None = None,
    label=UNSET,
    enabled: bool | None = None,
    source_config: dict | None = None,
    storage: dict | None = None,
    sync_schedule_enabled: bool | None = None,
    sync_interval_minutes: int | None = None,
) -> EnvironmentSource | None:
    path = get_source(session, environment_id, path_id, "logs")
    if path is None:
        return None
    changed = False
    material_changed = False
    enabled_changed = enabled is not None and enabled != path.enabled
    reenabled = enabled is True and not path.enabled
    if uri is not None or source_config is not None or storage is not None:
        current_binding = binding_to_dict(path)
        candidate_config = merge_source_config(
            json_object(path.source_config_json), source_config
        )
        normalized_uri, binding = validate_and_normalize_binding(
            session,
            uri=uri if uri is not None else path.uri,
            storage=storage if storage is not None else binding_to_dict(path),
            source_config=candidate_config,
        )
        canonical_config, input_locations, canonical_locations = canonicalize_location_config(
            candidate_config, binding
        )
        if storage is not None or has_location_input(uri, source_config):
            if source_config is None:
                input_locations = source_input_locations(path)
            registration, _ = get_or_create_registration(
                session,
                environment_id=environment_id,
                purpose="logs",
                input_uri=uri if uri is not None else source_input_uri(path),
                canonical_uri=normalized_uri,
                binding=binding,
                input_locations=input_locations,
                canonical_locations=canonical_locations,
            )
            ensure_registration_available(registration, path)
            update_registration_input(
                registration,
                input_uri=uri if uri is not None else source_input_uri(path),
                input_locations=input_locations,
            )
            replace_source_registration(session, path, registration.id)
        next_binding = {
            "provider": binding.provider,
            "auth_mode": binding.auth_mode,
            "credential_profile_id": binding.credential_profile_id,
            "options": binding.options,
        }
        binding_changed = current_binding != next_binding
        if normalized_uri != path.uri or binding_changed:
            material_changed = (
                normalized_uri != path.uri
                or current_binding["provider"] != next_binding["provider"]
                or current_binding["options"] != next_binding["options"]
            )
            path.uri = normalized_uri
            apply_binding(path, binding)
            changed = True
    if source_config is not None:
        config_json = json.dumps(canonical_config, sort_keys=True)
        if config_json != (path.source_config_json or "{}"):
            path.source_config_json = config_json
            changed = True
            material_changed = True
    if changed:
        clear_source_read_check(path)
        delete_source_sync_records(session, path)
        if material_changed:
            delete_source_log_cache(session, path)
        else:
            log_pending.invalidate_pending_changes(path.id)
    elif enabled_changed:
        clear_source_read_check(path)
        delete_source_log_cache(session, path)
    if label is not UNSET:
        path.label = label
    if enabled is not None:
        path.enabled = enabled
    if reenabled:
        resume_observation(session, path.id, pending_changes=False)
    elif changed:
        reset_observation(session, path.id, pending_changes=False)
    update_schedule(path, sync_schedule_enabled, sync_interval_minutes, default_interval_minutes=1)
    invalidate_environment_derived_caches(path.environment_id, structural=False)
    return path
