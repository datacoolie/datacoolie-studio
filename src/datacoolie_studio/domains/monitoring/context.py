from __future__ import annotations

from typing import Any, ContextManager

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.db.session import create_session
from datacoolie_studio.domains.analytics import access
from datacoolie_studio.domains.monitoring.metrics.v6_semantics import register_freshness_functions
from datacoolie_studio.domains.source_observation.repository import paused_source_ids


def source_ids(paths: list[EnvironmentSource]) -> list[int]:
    candidate = [
        path.id
        for path in paths
        if path.enabled
    ]
    if not candidate:
        return []
    # Sources whose automatic observation is paused (unreachable/parked) cannot be
    # materialized into the analytics cache, so they are not part of the expected
    # coverage. This keeps Monitoring consistent with the upgrade scope: a parked
    # source neither blocks the cache upgrade nor makes Monitoring report as
    # "incomplete". Un-pausing (validate/retry/re-enable) brings it back in scope.
    session = create_session()
    try:
        excluded = paused_source_ids(session, candidate)
    finally:
        session.close()
    return sorted(source_id for source_id in candidate if source_id not in excluded)


def materialization_token(paths: list[EnvironmentSource]) -> str:
    return access.materialization_token(source_ids(paths))


def reader(
    paths: list[EnvironmentSource],
    *,
    snapshot: bool = False,
) -> ContextManager[tuple[Any, list[int], str]]:
    if snapshot:
        return access.reader(
            source_ids(paths), snapshot=True,
            prepare_connection=register_freshness_functions,
        )
    return access.reader(source_ids(paths))
