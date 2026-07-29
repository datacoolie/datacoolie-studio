from __future__ import annotations

import json
from collections.abc import Mapping

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import CredentialProfile, EnvironmentSource
from datacoolie_studio.domains.storage.binding import StorageBinding
from datacoolie_studio.domains.storage.errors import StorageConfigurationError
from datacoolie_studio.domains.storage.uri import (
    canonical_cloud_uri,
    normalized_source_uri,
    parse_storage_uri,
    validate_storage_uri,
)


FORBIDDEN_CONFIG_KEYS = {
    "access_key",
    "access_key_id",
    "account_key",
    "authorization",
    "client_secret",
    "credential",
    "credentials",
    "endpoint_url",
    "password",
    "private_key",
    "sas_token",
    "secret",
    "secret_access_key",
    "service_account_json",
    "session_token",
    "token",
}


def validate_and_normalize_binding(
    session: Session,
    *,
    uri: str,
    storage: Mapping[str, object] | None,
    source_config: Mapping[str, object] | None,
) -> tuple[str, StorageBinding]:
    _reject_sensitive_source_config(source_config or {})
    detected = parse_storage_uri(uri)
    if storage is None:
        provider = detected.provider
        if provider not in {
            "local",
            "s3",
            "adls",
            "onelake",
            "gcs",
            "dbfs",
        }:
            raise StorageConfigurationError(
                f"Unsupported storage provider for URI: {provider}"
            )
        binding = StorageBinding(
            provider=provider,
            auth_mode="none" if provider == "local" else "ambient",
        )
    else:
        try:
            binding = StorageBinding(
                provider=str(storage.get("provider") or "").lower(),
                auth_mode=str(storage.get("auth_mode") or "").lower(),
                credential_profile_id=_optional_string(
                    storage.get("credential_profile_id")
                ),
                options=dict(storage.get("options") or {}),
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, StorageConfigurationError):
                raise
            raise StorageConfigurationError("Storage binding is invalid") from exc

    validate_storage_uri(uri, binding.provider)
    _validate_profile(session, binding)
    _validate_secondary_uris(source_config or {}, binding)
    normalized_uri = (
        canonical_cloud_uri(uri, binding.provider)
        if binding.provider != "local"
        else normalized_source_uri(uri.strip())
    )
    return normalized_uri, binding


def apply_binding(source: EnvironmentSource, binding: StorageBinding) -> None:
    source.storage_provider = binding.provider
    source.storage_auth_mode = binding.auth_mode
    source.credential_profile_id = binding.credential_profile_id
    source.storage_config_json = (
        json.dumps(binding.options, sort_keys=True) if binding.options else None
    )


def binding_from_source(source: EnvironmentSource) -> StorageBinding:
    try:
        options = json.loads(source.storage_config_json or "{}")
    except json.JSONDecodeError:
        options = {}
    if not isinstance(options, dict):
        options = {}
    return StorageBinding(
        provider=source.storage_provider,
        auth_mode=source.storage_auth_mode,
        credential_profile_id=source.credential_profile_id,
        options=options,
    )


def binding_to_dict(source: EnvironmentSource) -> dict[str, object]:
    binding = binding_from_source(source)
    return {
        "provider": binding.provider,
        "auth_mode": binding.auth_mode,
        "credential_profile_id": binding.credential_profile_id,
        "options": binding.options,
    }


def _validate_profile(session: Session, binding: StorageBinding) -> None:
    if not binding.credential_profile_id:
        return
    profile = session.get(CredentialProfile, binding.credential_profile_id)
    if profile is None:
        raise StorageConfigurationError("Credential profile was not found")
    if profile.provider != binding.provider:
        raise StorageConfigurationError(
            "Credential profile provider does not match storage provider"
        )


def _reject_sensitive_source_config(value: object, path: str = "source_config") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_CONFIG_KEYS:
                raise StorageConfigurationError(
                    f"{path}.{key} is not allowed; use a Credential Profile or typed storage option"
                )
            _reject_sensitive_source_config(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_sensitive_source_config(nested, f"{path}[{index}]")


def _validate_secondary_uris(
    source_config: Mapping[str, object], binding: StorageBinding
) -> None:
    for key in (
        "etl_logs_uri",
        "system_logs_uri",
        "metadata_root_uri",
        "module_root_uri",
    ):
        value = source_config.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            validate_storage_uri(value, binding.provider)
        except StorageConfigurationError as exc:
            raise StorageConfigurationError(
                f"{key} must use the source storage provider and credential binding"
            ) from exc


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
