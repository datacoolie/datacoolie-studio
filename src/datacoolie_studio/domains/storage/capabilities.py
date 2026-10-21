"""Provider capabilities used by source policy, diagnostics, and write flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


WriteSafety = Literal["strong_cas", "cooperative", "verified", "read_only"]
VerificationMode = Literal["provider_receipt", "content_read", "none"]


@dataclass(frozen=True)
class StorageCapabilities:
    """The safety contract a provider exposes to Studio callers."""

    provider: str
    metadata_write_back_supported: bool
    write_safety: WriteSafety
    native_conditional_write: bool
    verification_mode: VerificationMode


_CAPABILITIES: dict[str, StorageCapabilities] = {
    "local": StorageCapabilities(
        provider="local",
        metadata_write_back_supported=True,
        write_safety="cooperative",
        native_conditional_write=False,
        verification_mode="content_read",
    ),
    "s3": StorageCapabilities(
        provider="s3",
        metadata_write_back_supported=True,
        write_safety="strong_cas",
        native_conditional_write=True,
        verification_mode="content_read",
    ),
    "minio": StorageCapabilities(
        provider="minio",
        metadata_write_back_supported=True,
        write_safety="strong_cas",
        native_conditional_write=True,
        verification_mode="content_read",
    ),
    "adls": StorageCapabilities(
        provider="adls",
        metadata_write_back_supported=True,
        write_safety="strong_cas",
        native_conditional_write=True,
        verification_mode="content_read",
    ),
    "onelake": StorageCapabilities(
        provider="onelake",
        metadata_write_back_supported=True,
        write_safety="strong_cas",
        native_conditional_write=True,
        verification_mode="content_read",
    ),
    "gcs": StorageCapabilities(
        provider="gcs",
        metadata_write_back_supported=True,
        write_safety="strong_cas",
        native_conditional_write=True,
        verification_mode="content_read",
    ),
    # DBFS/Volumes has a verified read-check-write writer, not a provider
    # enforced compare-and-swap contract. It is writable, but callers must
    # disclose the residual external-writer race represented by "verified".
    "dbfs": StorageCapabilities(
        provider="dbfs",
        metadata_write_back_supported=True,
        write_safety="verified",
        native_conditional_write=False,
        verification_mode="content_read",
    ),
}

_READ_ONLY_CAPABILITIES = StorageCapabilities(
    provider="unknown",
    metadata_write_back_supported=False,
    write_safety="read_only",
    native_conditional_write=False,
    verification_mode="none",
)


def storage_capabilities(provider: str | None) -> StorageCapabilities:
    """Return the fail-closed capability contract for a provider name."""

    normalized = str(provider or "unknown").strip().lower()
    return _CAPABILITIES.get(normalized, _READ_ONLY_CAPABILITIES)


def metadata_write_back_supported(provider: str | None) -> bool:
    return storage_capabilities(provider).metadata_write_back_supported


def metadata_write_safety(provider: str | None) -> WriteSafety:
    return storage_capabilities(provider).write_safety
