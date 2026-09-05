from datetime import datetime, timezone

from datacoolie_studio.domains.freshness.service import _revision_modified_at


def test_revision_modified_at_accepts_legacy_millisecond_directory_mtime() -> None:
    modified = datetime(2026, 9, 1, 12, 30, tzinfo=timezone.utc)

    actual = _revision_modified_at(
        {
            "object_type": "directory",
            "max_mtime_ns": int(modified.timestamp() * 1000),
        }
    )

    assert actual == modified
