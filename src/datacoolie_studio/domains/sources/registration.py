from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource, SourceRegistration
from datacoolie_studio.domains.storage.binding import StorageBinding
from datacoolie_studio.domains.storage.identity import configured_storage_scope, storage_identity_scope
from datacoolie_studio.domains.storage.errors import StorageConfigurationError
from datacoolie_studio.domains.storage.uri import (
    canonical_cloud_uri,
    normalized_source_uri,
    validate_storage_uri,
)


LOCATION_CONFIG_KEYS = (
    "log_base_path",
    "execution_logs_uri",
    "system_logs_uri",
    "metadata_root_uri",
    "sql_root_uri",
    "module_root_uri",
)
LOCATION_CONFIG_INPUT_KEYS = frozenset(LOCATION_CONFIG_KEYS)


LocationValue = str | list[str]


def location_config_value(
    source_config: Mapping[str, object], key: str
) -> tuple[str, LocationValue] | None:
    value = source_config.get(key)
    if isinstance(value, str) and value.strip():
        return key, value.strip()
    if isinstance(value, list):
        values = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        if len(values) == len(value) and values:
            return key, values
    return None


def canonicalize_location_config(
    source_config: Mapping[str, object] | None,
    binding: StorageBinding,
) -> tuple[
    dict[str, object],
    dict[str, LocationValue],
    dict[str, LocationValue],
]:
    """Return collector-safe config plus raw and canonical location maps."""
    canonical_config = dict(source_config or {})
    input_locations: dict[str, LocationValue] = {}
    canonical_locations: dict[str, LocationValue] = {}
    for key in LOCATION_CONFIG_KEYS:
        configured = location_config_value(canonical_config, key)
        if configured is None:
            continue
        input_key, raw = configured
        raw_values = [raw] if isinstance(raw, str) else raw
        canonical_values = [
            _canonical_uri(value, binding.provider) for value in raw_values
        ]
        canonical_values = sorted(set(canonical_values))
        canonical: LocationValue = (
            canonical_values[0]
            if isinstance(raw, str)
            else canonical_values
        )
        # The canonical key is also the only accepted input key. Persist the
        # same name so the source boundary has one unambiguous contract.
        input_locations[input_key] = raw
        canonical_locations[key] = canonical
        canonical_config[key] = canonical
    return canonical_config, input_locations, canonical_locations


def source_registration_identity(
    *,
    provider: str,
    canonical_uri: str,
    canonical_locations: Mapping[str, LocationValue] | None = None,
    storage_options: Mapping[str, object] | None = None,
    effective_scope: dict | None = None,
) -> str:
    scope_options = effective_scope if effective_scope is not None else storage_identity_scope(provider, storage_options)
    payload = {
        "provider": provider,
        "canonical_uri": normalized_source_uri(canonical_uri),
        "locations": {
            key: _identity_location(value)
            for key, value in sorted((canonical_locations or {}).items())
        },
        "scope": scope_options,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def get_or_create_registration(
    session: Session,
    *,
    environment_id: int,
    purpose: str,
    input_uri: str,
    canonical_uri: str,
    binding: StorageBinding,
    input_locations: Mapping[str, LocationValue] | None = None,
    canonical_locations: Mapping[str, LocationValue] | None = None,
) -> tuple[SourceRegistration, bool]:
    effective = configured_storage_scope(session, binding, uri=canonical_uri)
    identity_key = source_registration_identity(
        provider=binding.provider, canonical_uri=canonical_uri,
        canonical_locations=canonical_locations, effective_scope=effective["scope"],
    )
    candidates = session.scalars(select(SourceRegistration).where(
        SourceRegistration.environment_id == environment_id,
        SourceRegistration.purpose == purpose,
    )).all()
    matches = []
    for candidate in candidates:
        if normalized_source_uri(candidate.canonical_uri) != normalized_source_uri(canonical_uri):
            continue
        if _json_object(candidate.canonical_locations_json) != dict(canonical_locations or {}):
            continue
        if not candidate.sources:
            if candidate.identity_key == identity_key:
                matches.append(candidate)
            continue
        if registration_scope(session, candidate) == effective:
            matches.append(candidate)
    if len(matches) > 1:
        raise StorageConfigurationError(f"Ambiguous source registrations: {[row.id for row in matches]}")
    if matches:
        matches[0].identity_key = identity_key
        return matches[0], False
    registration = SourceRegistration(
        environment_id=environment_id,
        purpose=purpose,
        input_uri=input_uri.strip(),
        canonical_uri=canonical_uri,
        input_locations_json=_json_or_none(input_locations),
        canonical_locations_json=_json_or_none(canonical_locations),
        identity_key=identity_key,
    )
    try:
        with session.begin_nested():
            session.add(registration)
            session.flush()
        return registration, True
    except IntegrityError:
        existing = session.scalar(
            select(SourceRegistration).where(
                SourceRegistration.environment_id == environment_id,
                SourceRegistration.purpose == purpose,
                SourceRegistration.identity_key == identity_key,
            )
        )
        if existing is None:
            raise
        if normalized_source_uri(existing.canonical_uri) != normalized_source_uri(canonical_uri) or _json_object(existing.canonical_locations_json) != dict(canonical_locations or {}) or registration_scope(session, existing) != effective:
            raise StorageConfigurationError("Registration identity collides with a different provider scope")
        return existing, False



def registration_scope(session, registration):
    from datacoolie_studio.domains.sources.storage_binding import binding_from_source

    scopes = [configured_storage_scope(session, binding_from_source(source), uri=source.uri)
              for source in registration.sources]
    if not scopes or any(scope != scopes[0] for scope in scopes):
        raise StorageConfigurationError(f"Ambiguous provider scopes in registration {registration.id}")
    return scopes[0]


def rekey_profile_registrations(session, profile_id):
    registrations = session.scalars(
        select(SourceRegistration).join(EnvironmentSource).where(
            EnvironmentSource.credential_profile_id == profile_id
        ).distinct()
    ).all()
    updates = []
    for registration in registrations:
        scope = registration_scope(session, registration)
        identity = source_registration_identity(
            provider=scope["provider"], canonical_uri=registration.canonical_uri,
            canonical_locations=_json_object(registration.canonical_locations_json),
            effective_scope=scope["scope"],
        )
        peers = session.scalars(select(SourceRegistration).where(
            SourceRegistration.environment_id == registration.environment_id,
            SourceRegistration.purpose == registration.purpose,
            SourceRegistration.id != registration.id,
        )).all()
        for peer in peers:
            if normalized_source_uri(peer.canonical_uri) != normalized_source_uri(registration.canonical_uri):
                continue
            if _json_object(peer.canonical_locations_json) != _json_object(registration.canonical_locations_json):
                continue
            if peer.sources and registration_scope(session, peer) == scope:
                raise StorageConfigurationError(f"Provider scope collides with registration {peer.id}")
        updates.append((registration, identity))
    for registration, identity in updates:
        registration.identity_key = identity

def update_registration_input(
    registration: SourceRegistration,
    *,
    input_uri: str,
    input_locations: Mapping[str, LocationValue] | None = None,
) -> None:
    registration.input_uri = input_uri.strip()
    registration.input_locations_json = _json_or_none(input_locations)


def configured_location_dict(source: EnvironmentSource) -> dict[str, object] | None:
    registration = source.registration
    if registration is None:
        return None
    return {
        "registration_id": registration.id,
        "purpose": registration.purpose,
        "input_uri": registration.input_uri,
        "canonical_uri": registration.canonical_uri,
        "input_locations": _json_object(registration.input_locations_json),
        "canonical_locations": _json_object(registration.canonical_locations_json),
    }


def source_input_locations(source: EnvironmentSource) -> dict[str, LocationValue]:
    if source.registration is None:
        return {}
    return {
        str(key): value
        for key, value in _json_object(
            source.registration.input_locations_json
        ).items()
        if isinstance(value, str)
        or (
            isinstance(value, list)
            and all(isinstance(item, str) for item in value)
        )
    }


def _canonical_uri(uri: str, provider: str) -> str:
    validate_storage_uri(uri, provider)
    return (
        canonical_cloud_uri(uri, provider)
        if provider != "local"
        else normalized_source_uri(uri.strip())
    )


def _json_or_none(value: Mapping[str, LocationValue] | None) -> str | None:
    return json.dumps(dict(value), sort_keys=True) if value else None


def _identity_location(value: LocationValue) -> list[str]:
    values = [value] if isinstance(value, str) else value
    return sorted({normalized_source_uri(item) for item in values})


def _json_object(value: str | None) -> dict[str, object]:
    try:
        parsed = json.loads(value or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
