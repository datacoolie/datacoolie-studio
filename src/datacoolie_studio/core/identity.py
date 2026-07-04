from __future__ import annotations

import uuid


DATACOOLIE_UUID_NAMESPACE = uuid.UUID("da7ac001-e000-4000-8000-000000000000")


def name_to_uuid(name: str) -> str:
    return str(uuid.uuid5(DATACOOLIE_UUID_NAMESPACE, name))
