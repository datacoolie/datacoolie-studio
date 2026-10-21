from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from datacoolie_studio.domains.monitoring import page_cache


@pytest.mark.parametrize("page,previous_version", [
    ("jobs", "monitoring-page-sql-v29.complete-workload-operations-v1"),
    ("diagnostics", "monitoring-page-sql-v29.diagnostics-findings-v1"),
])
def test_corrected_reconciliation_and_capture_pages_invalidate_old_cache(
    monkeypatch, page, previous_version,
):
    monkeypatch.setattr(page_cache, "monitoring_input_fingerprint", lambda *_: "unchanged-input")
    monkeypatch.setattr(page_cache, "monitoring_admission", lambda *_: {"admitted": 1})
    current = page_cache.monitoring_page_cache_key(
        object(), environment_id=1, paths=[], page=page,
        filters={"range": "all"}, timezone_label="UTC",
        now=datetime(2026, 10, 21, tzinfo=timezone.utc),
    )
    previous = replace(current, producer_version=previous_version)

    assert current.identity != previous.identity
    assert page_cache.monitoring_page_etag(current) != page_cache.monitoring_page_etag(previous)
