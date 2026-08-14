"""Reset the stuck DuckDB analytics-upgrade record so the fixed code rebuilds cleanly.

Run this AFTER restarting the Studio backend with the updated code (the old running
process would otherwise re-wedge the record). Broken log sources stay parked by design;
fix their storage then use Validate/Retry on the source to bring them back in scope.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

db = Path(os.path.expanduser("~/.datacoolie/datacoolie-studio/db/studio.db"))
cache = Path(os.path.expanduser("~/.datacoolie/datacoolie-studio/cache"))

con = sqlite3.connect(str(db))
try:
    row = con.execute(
        "SELECT state, attempt_count, error_message FROM analytics_upgrades WHERE id = 1"
    ).fetchone()
    print("before:", row)
    con.execute(
        """
        UPDATE analytics_upgrades
        SET state = 'pending',
            attempt_count = 0,
            error_code = NULL,
            error_message = NULL,
            next_retry_at = NULL,
            candidate_path = NULL,
            completed_source_ids_json = '[]',
            started_at = NULL,
            completed_at = NULL
        WHERE id = 1
        """
    )
    con.commit()
    print("after: reset to pending")
finally:
    con.close()

for leftover in (cache / "analytics.candidate.duckdb", cache / "analytics.candidate.duckdb.wal"):
    if leftover.exists():
        leftover.unlink()
        print("removed leftover candidate:", leftover.name)

print("Done. The upgrade loop will rebuild the healthy log sources on its next cycle.")
