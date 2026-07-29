from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol


KEYRING_SERVICE_NAME = "datacoolie-studio"


class SecretStoreUnavailable(RuntimeError):
    pass


class SecretNotFound(KeyError):
    pass


class CredentialSecretStore(Protocol):
    def is_available(self) -> bool: ...

    def set(self, secret_ref: str, secret: Mapping[str, object]) -> None: ...

    def get(self, secret_ref: str) -> dict[str, object]: ...

    def delete(self, secret_ref: str) -> None: ...


class KeyringCredentialSecretStore:
    """OS keyring-backed store; database rows retain only opaque references."""

    def is_available(self) -> bool:
        try:
            keyring = _load_keyring()
            return float(getattr(keyring.get_keyring(), "priority", 0)) > 0
        except Exception:
            return False

    def set(self, secret_ref: str, secret: Mapping[str, object]) -> None:
        keyring = self._available_keyring()
        try:
            keyring.set_password(
                KEYRING_SERVICE_NAME,
                secret_ref,
                json.dumps(dict(secret), sort_keys=True),
            )
        except Exception as exc:
            raise SecretStoreUnavailable("OS secret store rejected the credential") from exc

    def get(self, secret_ref: str) -> dict[str, object]:
        keyring = self._available_keyring()
        try:
            value = keyring.get_password(KEYRING_SERVICE_NAME, secret_ref)
        except Exception as exc:
            raise SecretStoreUnavailable("OS secret store could not read the credential") from exc
        if value is None:
            raise SecretNotFound(secret_ref)
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise SecretStoreUnavailable("Stored credential payload is invalid") from exc
        if not isinstance(payload, dict):
            raise SecretStoreUnavailable("Stored credential payload is invalid")
        return payload

    def delete(self, secret_ref: str) -> None:
        keyring = self._available_keyring()
        try:
            keyring.delete_password(KEYRING_SERVICE_NAME, secret_ref)
        except Exception as exc:
            if exc.__class__.__name__ == "PasswordDeleteError":
                return
            raise SecretStoreUnavailable("OS secret store could not delete the credential") from exc

    @staticmethod
    def _available_keyring():
        try:
            keyring = _load_keyring()
            if float(getattr(keyring.get_keyring(), "priority", 0)) <= 0:
                raise SecretStoreUnavailable("No usable OS secret store is available")
            return keyring
        except SecretStoreUnavailable:
            raise
        except Exception as exc:
            raise SecretStoreUnavailable("No usable OS secret store is available") from exc


def _load_keyring():
    import keyring

    return keyring
