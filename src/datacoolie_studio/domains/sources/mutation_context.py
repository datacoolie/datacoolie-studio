"""Application-owned source mutation context and transaction rollback."""

from functools import wraps
from inspect import signature

from sqlalchemy import select

from datacoolie_studio.core.process_lock import ProcessLockTimeoutError, process_lock_owned
from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.db.session import (
    WorkspaceMutationPendingError, ensure_workspace_transaction, refresh_workspace_reads, workspace_lock_path,
)
from datacoolie_studio.domains.credentials.store import credential_profile_guards
from datacoolie_studio.domains.metadata.errors import MetadataConflictError
from datacoolie_studio.domains.metadata.write_operations import environment_publication_guard


def source_mutation(function):
    parameters = signature(function)

    @wraps(function)
    def mutate(*args, **kwargs):
        values = parameters.bind(*args, **kwargs).arguments
        session, environment_id = values["session"], values["environment_id"]
        mutation_started = False
        try:
            nested = process_lock_owned(workspace_lock_path(session, f"environment-{environment_id}"))
            with environment_publication_guard(session, environment_id):
                if not nested:
                    refresh_workspace_reads(session)
                profiles = set(session.scalars(select(EnvironmentSource.credential_profile_id).where(
                    EnvironmentSource.environment_id == environment_id
                )))
                storage = values.get("storage")
                if storage:
                    profiles.add(storage.get("credential_profile_id"))
                with credential_profile_guards(session, profiles):
                    ensure_workspace_transaction(session)
                    mutation_started = True
                    return function(*args, **kwargs)
        except WorkspaceMutationPendingError as exc:
            raise MetadataConflictError(str(exc)) from exc
        except ProcessLockTimeoutError as exc:
            if mutation_started:
                session.rollback()
            raise MetadataConflictError("Credential profile is busy; retry source mutation") from exc
        except Exception:
            if mutation_started:
                session.rollback()
            raise

    return mutate
