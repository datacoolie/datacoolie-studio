"""Canonical provider scope, independent of authentication and source owners."""

from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select

from datacoolie_studio.db.models import CredentialProfile
from datacoolie_studio.domains.storage.errors import StorageConfigurationError


def storage_identity_scope(provider: str, options: Mapping[str, object] | None = None):
    options = options or {}
    scope = {}
    if provider == "minio":
        if options.get("endpoint_url"):
            scope["endpoint_url"] = canonical_origin(str(options["endpoint_url"]))
        scope["verify_tls"] = options.get("verify_tls", True)
        scope["addressing_style"] = options.get("addressing_style", "path")
    elif provider == "dbfs" and options.get("host"):
        scope["host"] = canonical_origin(str(options["host"]))
    elif provider == "adls" and options.get("account_name"):
        scope["account_name"] = str(options["account_name"]).lower()
    return scope


def canonical_origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), "", "", ""))


def scope_from_config(binding, *, uri, auth_type=None, config=None, require_explicit=True):
    options = dict(binding.options)
    config = config or {}
    if binding.provider == "dbfs":
        if auth_type in {"pat", "oauth_m2m"}:
            options["host"] = config.get("host")
        elif config.get("host"):
            options["host"] = config["host"]
    if binding.provider == "adls":
        options["account_name"] = config.get("account_name") or options.get("account_name") or (
            urlsplit(uri).hostname or ""
        ).split(".")[0]
    scope = storage_identity_scope(binding.provider, options)
    if require_explicit and binding.provider == "dbfs" and not scope.get("host"):
        raise StorageConfigurationError("Provider scope requires an explicit Databricks workspace host")
    return {"provider": binding.provider, "scope": scope}


def configured_storage_scope(session, binding, *, uri, require_explicit=True):
    """Read non-secret identity config without constructing SDKs or secret stores."""
    import json

    profile = None
    if binding.credential_profile_id:
        profile = session.scalar(
            select(CredentialProfile).where(CredentialProfile.id == binding.credential_profile_id)
            .execution_options(populate_existing=True)
        )
        if profile is None or profile.provider != binding.provider:
            raise StorageConfigurationError("Storage credential profile is unavailable or mismatched")
    return scope_from_config(
        binding, uri=uri, auth_type=profile.auth_type if profile else None,
        config=json.loads(profile.config_json or "{}") if profile else None,
        require_explicit=require_explicit,
    )
