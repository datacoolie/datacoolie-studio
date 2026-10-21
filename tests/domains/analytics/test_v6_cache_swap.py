from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import pytest

from datacoolie_studio.domains.analytics.access import swap_candidate


def test_successful_swap_retains_closed_old_database_and_hash(tmp_path):
    live, candidate = tmp_path / "analytics.duckdb", tmp_path / "analytics.candidate.duckdb"
    for path, value in ((live, "old"), (candidate, "v6")):
        with duckdb.connect(str(path)) as conn:
            conn.execute("CREATE TABLE evidence AS SELECT ? AS value", [value])
    original = live.read_bytes()
    retained = swap_candidate(candidate, live)
    assert retained
    backup = Path(retained["path"])
    assert backup.read_bytes() == original
    assert hashlib.sha256(original).hexdigest() == retained["sha256"]
    with duckdb.connect(str(live)) as conn:
        assert conn.execute("SELECT value FROM evidence").fetchone()[0] == "v6"
    # Recovery proof uses an isolated restored copy, not the active user cache.
    restored = tmp_path / "restored.duckdb"
    restored.write_bytes(backup.read_bytes())
    with duckdb.connect(str(restored)) as conn:
        assert conn.execute("SELECT value FROM evidence").fetchone()[0] == "old"


def test_rollback_evidence_commit_failure_keeps_live_cache(tmp_path):
    live, candidate = tmp_path / "live.duckdb", tmp_path / "candidate.duckdb"
    for path, value in ((live, "old"), (candidate, "new")):
        with duckdb.connect(str(path)) as conn:
            conn.execute("CREATE TABLE evidence AS SELECT ? AS value", [value])
    def reject_recording(evidence):
        assert Path(evidence["path"]).is_file()
        with duckdb.connect(str(live)) as conn:
            assert conn.execute("SELECT value FROM evidence").fetchone() == ("old",)
        raise RuntimeError("control commit failed")
    with pytest.raises(RuntimeError, match="control commit"):
        swap_candidate(candidate, live, on_rollback_retained=reject_recording)
    assert candidate.is_file()
    with duckdb.connect(str(live)) as conn:
        assert conn.execute("SELECT value FROM evidence").fetchone() == ("old",)
