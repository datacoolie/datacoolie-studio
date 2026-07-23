from __future__ import annotations

from typing import Any, ContextManager

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.analytics import access
from datacoolie_studio.domains.sources import service as source_validation


def source_ids(paths: list[EnvironmentSource]) -> list[int]:
    return sorted(
        path.id
        for path in paths
        if path.enabled and not source_validation.is_validated_empty_log_source(path)
    )


def materialization_token(paths: list[EnvironmentSource]) -> str:
    return access.materialization_token(source_ids(paths))


def reader(
    paths: list[EnvironmentSource],
) -> ContextManager[tuple[Any, list[int], str]]:
    return access.reader(source_ids(paths))
