from __future__ import annotations

import ipaddress

from fastapi import HTTPException, Request

from datacoolie_studio.db.session import get_session
from datacoolie_studio.domains.credentials.store import (
    CredentialSecretStore,
    KeyringCredentialSecretStore,
)


def get_credential_secret_store() -> CredentialSecretStore:
    return KeyringCredentialSecretStore()


def require_loopback_client(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host == "testclient":
        return
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise HTTPException(
            status_code=403,
            detail="Credential mutations are available only to loopback clients",
        )


__all__ = ["get_credential_secret_store", "get_session", "require_loopback_client"]
