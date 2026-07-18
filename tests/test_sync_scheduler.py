from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from datacoolie_studio.domains.sync.scheduler import _is_due
from datacoolie_studio.domains.sync.service import source_refresh_guard


def test_log_schedule_uses_one_minute_for_legacy_null_interval():
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    log_source = SimpleNamespace(
        source_kind="logs",
        sync_interval_minutes=None,
        last_scheduled_sync_at=now - timedelta(minutes=2),
    )
    assert _is_due(log_source, now) is True


def test_source_refresh_guard_rejects_overlap_and_releases_afterwards():
    with source_refresh_guard(987654) as first:
        assert first is True
        with source_refresh_guard(987654) as overlapping:
            assert overlapping is False

    with source_refresh_guard(987654) as after_release:
        assert after_release is True
