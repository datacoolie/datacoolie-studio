from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from datacoolie_studio.domains.storage.errors import StorageConfigurationError


StorageProvider = Literal[
    "local", "s3", "minio", "adls", "onelake", "gcs", "dbfs"
]
StorageAuthMode = Literal["none", "ambient", "anonymous", "credential_profile"]

OPTION_ALLOWLIST: dict[str, frozenset[str]] = {
    "local": frozenset(),
    "s3": frozenset({"region", "requester_pays"}),
    "minio": frozenset(
        {"endpoint_url", "region", "verify_tls", "addressing_style"}
    ),
    "adls": frozenset({"account_name"}),
    "onelake": frozenset(),
    "gcs": frozenset({"project_id", "billing_project"}),
    "dbfs": frozenset({"host", "profile"}),
}


@dataclass(frozen=True)
class StorageBinding:
    provider: StorageProvider
    auth_mode: StorageAuthMode
    credential_profile_id: str | None = None
    options: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        provider = str(self.provider).lower()
        auth_mode = str(self.auth_mode).lower()
        if provider not in OPTION_ALLOWLIST:
            raise StorageConfigurationError(f"Unsupported storage provider: {provider}")
        if auth_mode not in {"none", "ambient", "anonymous", "credential_profile"}:
            raise StorageConfigurationError(
                f"Unsupported storage auth mode: {auth_mode}"
            )
        if provider == "local" and auth_mode != "none":
            raise StorageConfigurationError("Local storage requires auth_mode=none")
        if provider != "local" and auth_mode == "none":
            raise StorageConfigurationError(
                f"{provider} storage does not support auth_mode=none"
            )
        if provider in {"dbfs", "onelake"} and auth_mode == "anonymous":
            raise StorageConfigurationError(
                f"{provider} storage does not support anonymous authentication"
            )
        if auth_mode == "credential_profile" and not self.credential_profile_id:
            raise StorageConfigurationError(
                "credential_profile_id is required for credential_profile auth"
            )
        if auth_mode != "credential_profile" and self.credential_profile_id:
            raise StorageConfigurationError(
                "credential_profile_id is allowed only for credential_profile auth"
            )
        unknown = sorted(set(self.options) - OPTION_ALLOWLIST[provider])
        if unknown:
            raise StorageConfigurationError(
                f"Unsupported {provider} option(s): {', '.join(unknown)}"
            )
        _validate_options(provider, self.options)


def _validate_options(provider: str, options: dict[str, object]) -> None:
    if provider == "dbfs":
        host = options.get("host")
        if host is not None:
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
                raise StorageConfigurationError(
                    "Databricks host must be an HTTPS workspace origin"
                )
        profile = options.get("profile")
        if profile is not None and (
            not isinstance(profile, str) or not profile.strip()
        ):
            raise StorageConfigurationError(
                "Databricks profile must be a non-empty string"
            )
        if options.get("host") and options.get("profile"):
            raise StorageConfigurationError(
                "Use either Databricks host or profile in source options"
            )
    if provider == "minio":
        endpoint = options.get("endpoint_url")
        if not isinstance(endpoint, str) or not endpoint:
            raise StorageConfigurationError("MinIO endpoint_url is required")
        from urllib.parse import urlsplit

        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise StorageConfigurationError(
                "MinIO endpoint_url must be an HTTP(S) origin without userinfo, query, or fragment"
            )
        if parsed.path not in {"", "/"}:
            raise StorageConfigurationError(
                "MinIO endpoint_url must not contain a path"
            )
        verify_tls = options.get("verify_tls", True)
        if not isinstance(verify_tls, bool):
            raise StorageConfigurationError("MinIO verify_tls must be boolean")
        addressing = options.get("addressing_style", "path")
        if addressing not in {"path", "virtual"}:
            raise StorageConfigurationError(
                "MinIO addressing_style must be path or virtual"
            )
    for bool_option in ("requester_pays",):
        if bool_option in options and not isinstance(options[bool_option], bool):
            raise StorageConfigurationError(f"{bool_option} must be boolean")
