from __future__ import annotations

from pathlib import Path

import pytest


def test_staging_scope_removes_only_owned_sync_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datacoolie_studio.domains.logs import staging

    monkeypatch.setattr(staging, "source_materialization_cache_dir", lambda: tmp_path)
    sibling = staging.create_staging_dir(7)
    try:
        with pytest.raises(RuntimeError):
            with staging.staging_scope(7):
                current = staging.create_staging_dir(7)
                (current / "payload.json").write_text("{}", encoding="utf-8")
                raise RuntimeError("cancelled refresh")

        assert not current.exists()
        assert sibling.exists()
    finally:
        staging.remove_staging_dir(sibling)


def test_orphan_cleanup_is_path_contained_and_preserves_active_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datacoolie_studio.domains.logs import staging

    monkeypatch.setattr(staging, "source_materialization_cache_dir", lambda: tmp_path)
    stale = tmp_path / "logs" / "source-8" / "sync-stale"
    stale.mkdir(parents=True)
    (stale / "payload.json").write_text("{}", encoding="utf-8")
    unrelated = tmp_path / "logs" / "unrelated"
    unrelated.mkdir(parents=True)
    active = staging.create_staging_dir(8)
    try:
        result = staging.cleanup_orphaned_staging()

        assert result == {"cleaned": 1, "errors": 0}
        assert not stale.exists()
        assert active.exists()
        assert unrelated.exists()
    finally:
        staging.remove_staging_dir(active)
