from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from datacoolie_studio.api.v1.contracts.credentials import (
    CredentialCapabilities,
    CredentialProfileCreateRequest,
    CredentialProfileDetail,
    CredentialProfileInfo,
    CredentialProfileUpdateRequest,
)
from datacoolie_studio.db.session import get_session
from datacoolie_studio.domains.credentials import service as credentials
from datacoolie_studio.domains.credentials.store import (
    CredentialSecretStore,
    KeyringCredentialSecretStore,
    SecretStoreUnavailable,
)

router = APIRouter(prefix="/credential-profiles", tags=["credential-profiles"])


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


@router.get("/capabilities", response_model=CredentialCapabilities)
def get_capabilities(
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    available = secret_store.is_available()
    return {
        "providers": credentials.capabilities(),
        "secret_store_available": available,
        "secret_store_backend": "os_keyring",
        "remediation": (
            None
            if available
            else "Install and configure a supported operating-system keyring backend"
        ),
    }


@router.get("", response_model=list[CredentialProfileInfo])
def list_credential_profiles(session: Session = Depends(get_session)):
    return credentials.list_profiles(session)


@router.post(
    "",
    response_model=CredentialProfileInfo,
    status_code=201,
    dependencies=[Depends(require_loopback_client)],
)
def create_credential_profile(
    payload: CredentialProfileCreateRequest,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    try:
        return credentials.create_profile(
            session,
            name=payload.name,
            provider=payload.provider,
            auth_type=payload.auth_type,
            config=payload.config,
            secret=payload.secret,
            secret_store=secret_store,
        )
    except Exception as exc:
        _raise_http_error(exc)


@router.get("/{profile_id}", response_model=CredentialProfileDetail)
def get_credential_profile(
    profile_id: str,
    session: Session = Depends(get_session),
):
    try:
        return credentials.get_profile(session, profile_id)
    except Exception as exc:
        _raise_http_error(exc)


@router.patch(
    "/{profile_id}",
    response_model=CredentialProfileInfo,
    dependencies=[Depends(require_loopback_client)],
)
def update_credential_profile(
    profile_id: str,
    payload: CredentialProfileUpdateRequest,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    try:
        return credentials.update_profile(
            session,
            profile_id,
            name=payload.name,
            config=payload.config,
            secret=payload.secret,
            secret_store=secret_store,
        )
    except Exception as exc:
        _raise_http_error(exc)


@router.delete(
    "/{profile_id}",
    status_code=204,
    dependencies=[Depends(require_loopback_client)],
)
def delete_credential_profile(
    profile_id: str,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
) -> Response:
    try:
        credentials.delete_profile(
            session, profile_id, secret_store=secret_store
        )
    except Exception as exc:
        _raise_http_error(exc)
    return Response(status_code=204)


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, credentials.CredentialProfileNotFound):
        raise HTTPException(status_code=404, detail="Credential profile not found") from exc
    if isinstance(
        exc,
        (credentials.CredentialProfileConflict, credentials.CredentialProfileInUse),
    ):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, credentials.CredentialValidationError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, SecretStoreUnavailable):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    raise exc
