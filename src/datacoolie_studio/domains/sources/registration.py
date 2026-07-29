from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource, SourceRegistration
from datacoolie_studio.domains.storage.binding import StorageBinding
from datacoolie_studio.domains.storage.uri import canonical_cloud_uri, normalized_source_uri


LOCATION_CONFIG_KEYS = (
    "base_log_uri",
    "etl_logs_uri",
    "system_logs_uri",
    "metadata_root_uri",
    "module_root_uri",
)


def canonicalize_location_config(
    source_config: Mapping[str, object] | None,
    binding: StorageBinding,
) -> tuple[dict[str, object], dict[str, str], dict[str, str]]:
    """Return collector-safe config plus raw and canonical location maps."""
    canonical_config = dict(source_config or {})
    input_locations: dict[str, str] = {}
    canonical_locations: dict[str, str] = {}
    for key in LOCATION_CONFIG_KEYS:
        value = canonical_config.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        raw = value.strip()
        canonical = _canonical_uri(raw, binding.provider)
        input_locations[key] = raw
        canonical_locations[key] = canonical
        canonical_config[key] = canonical
    return canonical_config, input_locations, canonical_locations


def source_registration_identity(
    *,
    provider: str,
    canonical_uri: str,
    canonical_locations: Mapping[str, str] | None = None,
    storage_options: Mapping[str, object] | None = None,
) -> str:
    scope_options = storage_identity_scope(provider, storage_options)
    payload = {
        "provider": provider,
        "canonical_uri": normalized_source_uri(canonical_uri),
        "locations": dict(sorted((canonical_locations or {}).items())),
        "scope": scope_options,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def storage_identity_scope(
    provider: str,
    storage_options: Mapping[str, object] | None = None,
) -> dict[str, object]:
    scope_options: dict[str, object] = {}
    options = storage_options or {}
    if provider == "minio":
        if "endpoint_url" in options:
            scope_options["endpoint_url"] = _canonical_origin(
                str(options["endpoint_url"])
            )
        scope_options["verify_tls"] = options.get("verify_tls", True)
        scope_options["addressing_style"] = options.get("addressing_style", "path")
    elif provider == "dbfs":
        if "host" in options:
            scope_options["host"] = _canonical_origin(str(options["host"]))
        if "profile" in options:
            scope_options["profile"] = str(options["profile"]).strip()
    return scope_options


def get_or_create_registration(
    session: Session,
    *,
    environment_id: int,
    purpose: str,
    input_uri: str,
    canonical_uri: str,
    binding: StorageBinding,
    input_locations: Mapping[str, str] | None = None,
    canonical_locations: Mapping[str, str] | None = None,
) -> tuple[SourceRegistration, bool]:
    identity_key = source_registration_identity(
        provider=binding.provider,
        canonical_uri=canonical_uri,
        canonical_locations=canonical_locations,
        storage_options=binding.options,
    )
    existing = session.scalar(
        select(SourceRegistration).where(
            SourceRegistration.environment_id == environment_id,
            SourceRegistration.purpose == purpose,
            SourceRegistration.identity_key == identity_key,
        )
    )
    if existing is not None:
        return existing, False
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
        return existing, False


def update_registration_input(
    registration: SourceRegistration,
    *,
    input_uri: str,
    input_locations: Mapping[str, str] | None = None,
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


def source_input_locations(source: EnvironmentSource) -> dict[str, str]:
    if source.registration is None:
        return {}
    return {
        str(key): str(value)
        for key, value in _json_object(
            source.registration.input_locations_json
        ).items()
        if isinstance(value, str)
    }


def _canonical_uri(uri: str, provider: str) -> str:
    return (
        canonical_cloud_uri(uri, provider)
        if provider != "local"
        else normalized_source_uri(uri.strip())
    )


def _json_or_none(value: Mapping[str, str] | None) -> str | None:
    return json.dumps(dict(value), sort_keys=True) if value else None


def _json_object(value: str | None) -> dict[str, object]:
    try:
        parsed = json.loads(value or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _canonical_origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), "", "", "")
    )
