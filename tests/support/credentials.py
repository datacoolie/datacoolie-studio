from __future__ import annotations

from collections.abc import Mapping

from datacoolie_studio.domains.credentials.store import SecretNotFound


class MemorySecretStore:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.values: dict[str, dict[str, object]] = {}

    def is_available(self) -> bool:
        return self.available

    def set(self, secret_ref: str, secret: Mapping[str, object]) -> None:
        self.values[secret_ref] = dict(secret)

    def get(self, secret_ref: str) -> dict[str, object]:
        try:
            return dict(self.values[secret_ref])
        except KeyError as exc:
            raise SecretNotFound(secret_ref) from exc

    def delete(self, secret_ref: str) -> None:
        self.values.pop(secret_ref, None)
