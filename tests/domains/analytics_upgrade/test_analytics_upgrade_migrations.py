from __future__ import annotations




def test_schema_version_has_explicit_replay_path() -> None:
    from datacoolie_studio.domains.analytics.migrations import (
        migration_path,
        validate_registry,
    )

    validate_registry()
    assert [(step.from_version, step.to_version) for step in migration_path(7)] == [
        (7, 8),
        (8, 9),
        (9, 10),
        (10, 11),
        (11, 12),
        (12, 13),
        (13, 14),
        (14, 15),
        (15, 16),
        (16, 17),
        (17, 18),
    ]
    assert all(step.requires_source_replay for step in migration_path(7)[:-1])
    assert migration_path(7)[-1].requires_source_replay is False
    assert migration_path(7)[-1].operation_kind == "schema_migration"
    assert [(step.from_version, step.to_version) for step in migration_path(13)] == [(13, 14), (14, 15), (15, 16), (16, 17), (17, 18)]
    assert migration_path(6) == ()
