"""Portable consumer tests: producer execution is qualified in DataCoolie."""
from __future__ import annotations

import json
from pathlib import Path

from datacoolie_studio.domains.analytics.log_contract import admit_log_record

FIXTURE_DIR = Path(__file__).parents[2] / "fixtures/datacoolie/logging/v6"


def test_attempt_history_is_native_and_terminal_metrics_are_not_retry_sums():
    rows = [json.loads(line) for line in (FIXTURE_DIR / "dataflow_part_00000002.json").read_text().splitlines()]
    recovered = next(row for row in rows if row["status"] == "succeeded" and len(row["attempts"]) > 1)
    assert admit_log_record(recovered, expected_type="dataflow_run_log", uri="fixture", line=1)
    assert isinstance(recovered["attempts"], list)
    assert recovered["attempts"][0]["status"] == "failed"
    final = recovered["attempts"][-1]
    assert final["status"] == "succeeded"
    assert recovered["metrics"]["rows_read"] == final["source"]["rows_read"]
    assert recovered["metrics"]["rows_written"] == final["destination"]["rows_written"]


def test_no_attempt_observations_remain_without_fabricated_pipeline_evidence():
    rows = [json.loads(line) for line in (FIXTURE_DIR / "dataflow_part_00000002.json").read_text().splitlines()]
    early = [row for row in rows if not row["attempts"]]
    assert early
    for row in early:
        assert admit_log_record(row, expected_type="dataflow_run_log", uri="fixture", line=1)
        assert row["attempts"] == []
        # A skipped producer observation may acknowledge zero effect without
        # entering a pipeline. Do not infer a phase from those root values.
        assert "source" not in row
        assert "destination" not in row
