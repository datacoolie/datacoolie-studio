from __future__ import annotations

import json
import uuid
from typing import Any


DATACOOLIE_UUID_NAMESPACE = uuid.UUID("da7ac001-e000-4000-8000-000000000000")


def normalize_workspace_id(value: Any) -> str | None:
    """Return the canonical workspace scope used by metadata identity.

    Workspace values are deliberately trimmed at the identity boundary.  A
    blank value has the same meaning as an absent scope; display names use a
    different rule and are kept byte-for-byte when they are non-blank.
    """

    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("workspace_id must be a string or null")
    value = value.strip()
    return value or None


def identity_name(value: Any) -> str | None:
    """Return an exact non-blank metadata name for UUID generation."""

    if value is None:
        return None
    if not isinstance(value, str):
        return None
    return value if value.strip() else None


def name_to_uuid(name: str, workspace_id: str | None = None) -> str:
    """Derive the shared metadata UUIDv5 identity.

    The unscoped form intentionally remains the historical exact-name input.
    A scoped identity uses a compact JSON tuple so the same display name can
    be generated independently in two workspaces without changing the UUID
    namespace or introducing a connection/dataflow discriminator.
    """

    exact_name = identity_name(name)
    if exact_name is None:
        raise ValueError("metadata identity requires a non-blank name")
    workspace = normalize_workspace_id(workspace_id)
    identity_input = (
        json.dumps(
            ["workspace", workspace, exact_name],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if workspace
        else exact_name
    )
    return str(uuid.uuid5(DATACOOLIE_UUID_NAMESPACE, identity_input))


__all__ = [
    "DATACOOLIE_UUID_NAMESPACE",
    "identity_name",
    "name_to_uuid",
    "normalize_workspace_id",
]
