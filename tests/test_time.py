from datetime import datetime, timezone

from datacoolie_studio.core.time import epoch_value_to_nanoseconds


def test_epoch_value_to_nanoseconds_normalizes_common_provider_units() -> None:
    expected = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)

    assert epoch_value_to_nanoseconds(expected // 1_000_000_000) == expected
    assert epoch_value_to_nanoseconds(expected // 1_000_000) == expected
    assert epoch_value_to_nanoseconds(expected // 1_000) == expected
    assert epoch_value_to_nanoseconds(expected) == expected


def test_epoch_value_to_nanoseconds_rejects_missing_or_invalid_values() -> None:
    assert epoch_value_to_nanoseconds(None) is None
    assert epoch_value_to_nanoseconds(0) is None
    assert epoch_value_to_nanoseconds("not-a-timestamp") is None
