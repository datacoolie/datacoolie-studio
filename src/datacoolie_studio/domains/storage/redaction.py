from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from datacoolie_studio.domains.storage.binding import StorageBinding


def redact_uri(uri: str) -> str:
    parsed = urlsplit(uri)
    if not parsed.scheme:
        return uri
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit((parsed.scheme, f"{host}{port}", parsed.path, "", ""))


def redacted_binding(binding: StorageBinding) -> dict[str, object]:
    options = dict(binding.options)
    if "endpoint_url" in options:
        options["endpoint_url"] = redact_uri(str(options["endpoint_url"]))
    return {
        "provider": binding.provider,
        "auth_mode": binding.auth_mode,
        "credential_profile_id": (
            f"{binding.credential_profile_id[:4]}***"
            if binding.credential_profile_id
            else None
        ),
        "options": options,
    }


def redact_storage_error(message: str) -> str:
    """Keep actionable provider diagnostics without returning credentials."""
    redacted = re.sub(
        r"https?://[^\s'\"]+",
        lambda match: redact_uri(match.group(0)),
        str(message),
    )
    redacted = re.sub(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer ***",
        redacted,
    )
    return re.sub(
        r"(?i)(authorization|account[_-]?key|client[_-]?secret|secret|token|password|sig|signature)"
        r"(\s*[:=]\s*)([^\s,;]+)",
        r"\1\2***",
        redacted,
    )
