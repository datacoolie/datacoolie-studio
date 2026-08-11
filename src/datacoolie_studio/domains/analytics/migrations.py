from __future__ import annotations

from dataclasses import dataclass

from datacoolie_studio.domains.analytics.schema import ANALYTICS_SCHEMA_VERSION


@dataclass(frozen=True)
class AnalyticsMigration:
    from_version: int
    to_version: int
    legacy_queryable: bool
    requires_source_replay: bool


# DuckDB is a disposable projection. Version steps describe how Studio may serve
# the old generation while a fresh, source-complete candidate is reconstructed.
MIGRATIONS: tuple[AnalyticsMigration, ...] = (
    AnalyticsMigration(7, 8, legacy_queryable=False, requires_source_replay=True),
    AnalyticsMigration(8, 9, legacy_queryable=False, requires_source_replay=True),
    AnalyticsMigration(9, 10, legacy_queryable=False, requires_source_replay=True),
)


def migration_path(
    from_version: int | None,
    to_version: int = ANALYTICS_SCHEMA_VERSION,
) -> tuple[AnalyticsMigration, ...]:
    if from_version is None or from_version >= to_version:
        return ()
    by_version = {migration.from_version: migration for migration in MIGRATIONS}
    current = from_version
    path: list[AnalyticsMigration] = []
    while current < to_version:
        migration = by_version.get(current)
        if migration is None or migration.to_version <= current:
            return ()
        path.append(migration)
        current = migration.to_version
    return tuple(path) if current == to_version else ()


def legacy_cache_is_queryable(from_version: int | None) -> bool:
    path = migration_path(from_version)
    return bool(path) and all(step.legacy_queryable for step in path)


def validate_registry() -> None:
    ordered = sorted(MIGRATIONS, key=lambda migration: migration.from_version)
    if not ordered or ordered[-1].to_version != ANALYTICS_SCHEMA_VERSION:
        raise RuntimeError(
            "ANALYTICS_SCHEMA_VERSION must have an explicit migration descriptor"
        )
    if len({migration.from_version for migration in ordered}) != len(ordered):
        raise RuntimeError("Analytics migration descriptors must have unique sources")
    for previous, current in zip(ordered, ordered[1:]):
        if previous.to_version != current.from_version:
            raise RuntimeError("Analytics migration descriptors must be contiguous")
