from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import (
    CredentialProfile,
    EnvironmentSource,
    StudioSetting,
)
from datacoolie_studio.domains.credentials.store import (
    CredentialSecretStore,
    SecretNotFound,
    SecretStoreUnavailable,
)


class CredentialValidationError(ValueError):
    pass


class CredentialProfileNotFound(KeyError):
    pass


class CredentialProfileConflict(RuntimeError):
    pass


class CredentialProfileInUse(RuntimeError):
    pass


@dataclass(frozen=True)
class CredentialSpec:
    config_fields: frozenset[str]
    required_config_fields: frozenset[str]
    secret_fields: frozenset[str]
    required_secret_fields: frozenset[str]


SPECS: dict[tuple[str, str], CredentialSpec] = {
    ("s3", "aws_shared_profile"): CredentialSpec(
        frozenset({"profile_name"}), frozenset({"profile_name"}), frozenset(), frozenset()
    ),
    ("s3", "access_key"): CredentialSpec(
        frozenset({"access_key_id"}),
        frozenset({"access_key_id"}),
        frozenset({"secret_access_key", "session_token"}),
        frozenset({"secret_access_key"}),
    ),
    ("minio", "access_key"): CredentialSpec(
        frozenset({"access_key_id"}),
        frozenset({"access_key_id"}),
        frozenset({"secret_access_key", "session_token"}),
        frozenset({"secret_access_key"}),
    ),
    ("adls", "service_principal"): CredentialSpec(
        frozenset({"tenant_id", "client_id", "account_name"}),
        frozenset({"tenant_id", "client_id", "account_name"}),
        frozenset({"client_secret"}),
        frozenset({"client_secret"}),
    ),
    ("adls", "sas"): CredentialSpec(
        frozenset({"account_name"}),
        frozenset({"account_name"}),
        frozenset({"sas_token"}),
        frozenset({"sas_token"}),
    ),
    ("adls", "account_key"): CredentialSpec(
        frozenset({"account_name"}),
        frozenset({"account_name"}),
        frozenset({"account_key"}),
        frozenset({"account_key"}),
    ),
    ("onelake", "service_principal"): CredentialSpec(
        frozenset({"tenant_id", "client_id"}),
        frozenset({"tenant_id", "client_id"}),
        frozenset({"client_secret"}),
        frozenset({"client_secret"}),
    ),
    ("gcs", "service_account"): CredentialSpec(
        frozenset(), frozenset(), frozenset({"service_account_json"}), frozenset({"service_account_json"})
    ),
    ("dbfs", "databricks_profile"): CredentialSpec(
        frozenset({"profile", "host"}),
        frozenset({"profile"}),
        frozenset(),
        frozenset(),
    ),
    ("dbfs", "pat"): CredentialSpec(
        frozenset({"host"}),
        frozenset({"host"}),
        frozenset({"token"}),
        frozenset({"token"}),
    ),
    ("dbfs", "oauth_m2m"): CredentialSpec(
        frozenset({"host", "client_id"}),
        frozenset({"host", "client_id"}),
        frozenset({"client_secret"}),
        frozenset({"client_secret"}),
    ),
}


def capabilities() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for provider, auth_type in SPECS:
        result.setdefault(provider, []).append(auth_type)
    return {provider: sorted(auth_types) for provider, auth_types in sorted(result.items())}


def list_profiles(session: Session) -> list[dict[str, object]]:
    profiles = session.scalars(
        select(CredentialProfile).order_by(func.lower(CredentialProfile.name))
    ).all()
    return [_profile_view(session, profile) for profile in profiles]


def get_profile(session: Session, profile_id: str) -> dict[str, object]:
    return _profile_view(
        session, _require_profile(session, profile_id), include_config=True
    )


def create_profile(
    session: Session,
    *,
    name: str,
    provider: str,
    auth_type: str,
    config: Mapping[str, object],
    secret: Mapping[str, object] | None,
    secret_store: CredentialSecretStore,
) -> dict[str, object]:
    normalized_name = _validate_name(name)
    normalized_provider = provider.strip().lower()
    normalized_auth_type = auth_type.strip().lower()
    spec = _require_spec(normalized_provider, normalized_auth_type)
    safe_config = _validate_fields(config, spec.config_fields, spec.required_config_fields, "config")
    _validate_provider_config(normalized_provider, safe_config)
    safe_secret = _validate_fields(
        secret or {}, spec.secret_fields, spec.required_secret_fields, "secret"
    )
    masked = _masked_summary(normalized_provider, normalized_auth_type, safe_config, safe_secret)
    _ensure_unique_name(session, normalized_name)

    profile_id = str(uuid4())
    secret_ref = profile_id if safe_secret else None
    if secret_ref:
        if not secret_store.is_available():
            raise SecretStoreUnavailable("No usable OS secret store is available")
        secret_store.set(secret_ref, safe_secret)

    profile = CredentialProfile(
        id=profile_id,
        name=normalized_name,
        provider=normalized_provider,
        auth_type=normalized_auth_type,
        config_json=_json(safe_config),
        secret_ref=secret_ref,
        secret_state="present",
        masked_summary_json=_json(masked),
        version=1,
    )
    session.add(profile)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if secret_ref:
            secret_store.delete(secret_ref)
        raise CredentialProfileConflict(
            f"Credential profile name already exists: {normalized_name}"
        ) from exc
    return _profile_view(session, profile)


def update_profile(
    session: Session,
    profile_id: str,
    *,
    name: str | None,
    config: Mapping[str, object] | None,
    secret: Mapping[str, object] | None,
    secret_store: CredentialSecretStore,
) -> dict[str, object]:
    profile = _require_profile(session, profile_id)
    affected_source_ids = list(
        session.scalars(
            select(EnvironmentSource.id).where(
                EnvironmentSource.credential_profile_id == profile.id
            )
        )
    )
    spec = _require_spec(profile.provider, profile.auth_type)
    normalized_name = _validate_name(name) if name is not None else profile.name
    _ensure_unique_name(session, normalized_name, exclude_id=profile.id)
    current_config = _load_object(profile.config_json)
    safe_config = (
        _validate_fields(config, spec.config_fields, spec.required_config_fields, "config")
        if config is not None
        else current_config
    )
    _validate_provider_config(profile.provider, safe_config)
    name_changed = normalized_name != profile.name
    config_changed = safe_config != current_config
    if not name_changed and not config_changed and secret is None:
        return _profile_view(session, profile)

    old_secret: dict[str, object] = {}
    if secret is not None and profile.secret_ref:
        try:
            old_secret = secret_store.get(profile.secret_ref)
        except SecretNotFound:
            old_secret = {}
    safe_secret = (
        _validate_fields(
            secret, spec.secret_fields, spec.required_secret_fields, "secret"
        )
        if secret is not None
        else {}
    )
    if spec.required_secret_fields and secret is None and not profile.secret_ref:
        raise CredentialValidationError("secret is required for this authentication type")

    secret_ref = profile.secret_ref or (profile.id if safe_secret else None)
    if secret is not None:
        if not secret_store.is_available():
            raise SecretStoreUnavailable("No usable OS secret store is available")
        assert secret_ref is not None
        secret_store.set(secret_ref, safe_secret)

    profile.name = normalized_name
    profile.config_json = _json(safe_config)
    profile.secret_ref = secret_ref
    if secret is not None:
        profile.secret_state = "present"
    summary = _masked_summary(
        profile.provider, profile.auth_type, safe_config, safe_secret
    )
    if secret is None:
        previous_summary = _load_object(profile.masked_summary_json)
        for key in ("secret_fields", "service_account_email", "project_id"):
            if key in previous_summary:
                summary[key] = previous_summary[key]
    profile.masked_summary_json = _json(summary)
    profile.version += 1
    if config_changed or secret is not None:
        session.query(EnvironmentSource).filter(
            EnvironmentSource.credential_profile_id == profile.id
        ).update(
            {
                EnvironmentSource.read_check_status: None,
                EnvironmentSource.read_checked_at: None,
                EnvironmentSource.read_check_result_json: None,
            },
            synchronize_session=False,
        )
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        if secret is not None and secret_ref:
            if old_secret:
                secret_store.set(secret_ref, old_secret)
            else:
                secret_store.delete(secret_ref)
        if isinstance(exc, IntegrityError):
            raise CredentialProfileConflict(
                f"Credential profile name already exists: {normalized_name}"
            ) from exc
        raise
    if config_changed or secret is not None:
        from datacoolie_studio.domains.storage.factory import (
            invalidate_storage_client_caches,
        )
        from datacoolie_studio.domains.source_observation.repository import (
            resume_observation,
        )

        invalidate_storage_client_caches()
        for source_id in affected_source_ids:
            resume_observation(session, source_id)
        if affected_source_ids:
            session.commit()
    return _profile_view(session, profile)


def delete_profile(
    session: Session,
    profile_id: str,
    *,
    secret_store: CredentialSecretStore,
) -> None:
    profile = _require_profile(session, profile_id)
    references = session.scalar(
        select(func.count(EnvironmentSource.id)).where(
            EnvironmentSource.credential_profile_id == profile.id
        )
    )
    if int(references or 0) > 0:
        raise CredentialProfileInUse(
            f"Credential profile is referenced by {references} source(s)"
        )
    secret_ref = profile.secret_ref
    session.delete(profile)
    session.commit()
    from datacoolie_studio.domains.storage.factory import (
        invalidate_storage_client_caches,
    )

    invalidate_storage_client_caches()
    if secret_ref:
        try:
            secret_store.delete(secret_ref)
        except SecretStoreUnavailable:
            session.merge(
                StudioSetting(
                    key=f"credential_secret_cleanup_error:{profile_id}",
                    value=_json(
                        {
                            "profile_id": profile_id,
                            "status": "orphan_cleanup_required",
                        }
                    ),
                )
            )
            session.commit()


def _profile_view(
    session: Session,
    profile: CredentialProfile,
    *,
    include_config: bool = False,
) -> dict[str, object]:
    references = session.scalar(
        select(func.count(EnvironmentSource.id)).where(
            EnvironmentSource.credential_profile_id == profile.id
        )
    )
    result: dict[str, object] = {
        "id": profile.id,
        "name": profile.name,
        "provider": profile.provider,
        "auth_type": profile.auth_type,
        "secret_state": profile.secret_state,
        "masked_summary": _load_object(profile.masked_summary_json),
        "version": profile.version,
        "reference_count": int(references or 0),
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }
    if include_config:
        result["config"] = _load_object(profile.config_json)
    return result


def _require_profile(session: Session, profile_id: str) -> CredentialProfile:
    profile = session.get(CredentialProfile, profile_id)
    if profile is None:
        raise CredentialProfileNotFound(profile_id)
    return profile


def _require_spec(provider: str, auth_type: str) -> CredentialSpec:
    spec = SPECS.get((provider, auth_type))
    if spec is None:
        raise CredentialValidationError(
            f"Unsupported credential type: {provider}/{auth_type}"
        )
    return spec


def _validate_name(name: str) -> str:
    value = name.strip()
    if not value:
        raise CredentialValidationError("name must not be empty")
    if len(value) > 255:
        raise CredentialValidationError("name must be at most 255 characters")
    return value


def _ensure_unique_name(
    session: Session, name: str, *, exclude_id: str | None = None
) -> None:
    query = select(CredentialProfile.id).where(
        func.lower(CredentialProfile.name) == name.lower()
    )
    if exclude_id:
        query = query.where(CredentialProfile.id != exclude_id)
    if session.scalar(query) is not None:
        raise CredentialProfileConflict(
            f"Credential profile name already exists: {name}"
        )


def _validate_fields(
    value: Mapping[str, object],
    allowed: frozenset[str],
    required: frozenset[str],
    field_name: str,
) -> dict[str, object]:
    payload = dict(value)
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise CredentialValidationError(
            f"Unknown {field_name} field(s): {', '.join(unknown)}"
        )
    missing = sorted(
        key for key in required if key not in payload or payload[key] in (None, "")
    )
    if missing:
        raise CredentialValidationError(
            f"Missing {field_name} field(s): {', '.join(missing)}"
        )
    if "service_account_json" in payload:
        payload["service_account_json"] = _validate_service_account(
            payload["service_account_json"]
        )
    return payload


def _validate_service_account(value: object) -> dict[str, object]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise CredentialValidationError(
                "service_account_json must be valid JSON"
            ) from exc
    if not isinstance(value, dict) or value.get("type") != "service_account":
        raise CredentialValidationError(
            "service_account_json must be a Google service account document"
        )
    return dict(value)


def _validate_provider_config(
    provider: str, config: Mapping[str, object]
) -> None:
    if provider != "dbfs":
        return
    host = config.get("host")
    if host:
        from urllib.parse import urlsplit

        parsed = urlsplit(str(host))
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise CredentialValidationError(
                "Databricks host must be an HTTPS workspace origin"
            )


def _masked_summary(
    provider: str,
    auth_type: str,
    config: Mapping[str, object],
    secret: Mapping[str, object],
) -> dict[str, object]:
    summary: dict[str, object] = {"provider": provider, "auth_type": auth_type}
    for key, value in config.items():
        summary[key] = _mask_identifier(value)
    if secret:
        summary["secret_fields"] = sorted(secret)
    service_account = secret.get("service_account_json")
    if isinstance(service_account, dict):
        summary["service_account_email"] = _mask_identifier(
            service_account.get("client_email")
        )
        summary["project_id"] = _mask_identifier(
            service_account.get("project_id")
        )
    return {key: value for key, value in summary.items() if value is not None}


def _mask_identifier(value: object) -> object:
    if not isinstance(value, str) or not value:
        return value
    if "@" in value:
        local, _, domain = value.partition("@")
        return f"{local[:2]}***@{domain}"
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}***{value[-2:]}"


def _load_object(value: str | None) -> dict[str, object]:
    try:
        payload = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _json(value: Mapping[str, object]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"))
