from __future__ import annotations

from typing import Literal

from datacoolie_studio.db.models import CredentialProfile
from datacoolie_studio.db.session import create_session

CredentialSecretState = Literal["present", "missing", "unavailable"]


def record_credential_secret_state(
    profile_id: str,
    state: CredentialSecretState,
) -> None:
    """Persist credential health without owning the caller's transaction."""

    session = create_session()
    try:
        profile = session.get(CredentialProfile, profile_id)
        if profile is None or profile.secret_state == state:
            return
        profile.secret_state = state
        session.commit()
    finally:
        session.close()
