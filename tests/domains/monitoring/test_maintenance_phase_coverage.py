from __future__ import annotations

import copy

import pytest

from datacoolie_studio.domains.monitoring.metrics.investigation import enrich_dataflow_run_for_investigation
from datacoolie_studio.domains.monitoring.metrics.v6_semantics import phase_timing, final_overhead_duration
from datacoolie_studio.domains.monitoring.read_models.overview import overview_read_model
from datacoolie_studio.domains.monitoring.read_models.performance import performance_read_model, performance_evidence_read_model
from tests.domains.monitoring.test_v6_timing_contract import record
from tests.domains.monitoring.test_v6_freshness_contract import connection


def maintenance_record():
    row = record()
    row["operation_type"] = "maintenance"
    attempt = row["attempts"][-1]
    attempt["source"] = attempt["transform"] = None
    attempt["destination"].update(start_time=attempt["start_time"], duration_seconds=7)
    return row


def test_native_maintenance_uses_destination_and_final_attempt_overhead(tmp_path):
    row = maintenance_record()
    assert phase_timing(row)["available"] is True
    assert set(phase_timing(row)["phases"]) == {"destination"}
    detail = enrich_dataflow_run_for_investigation(row)
    assert detail["timing_coverage"] == "complete"
    assert detail["overhead_duration_seconds"] == 3
    with connection(tmp_path, [row]) as conn:
        context = (conn, [1], "test")
        overview = overview_read_model([], {}, grain="day", timezone_name="UTC", analytics_context=context)
        performance = performance_read_model([], {}, grain="day", timezone_name="UTC", analytics_context=context)
        evidence = performance_evidence_read_model([], {}, limit=100, offset=0, sort_by="duration_seconds", sort_dir="desc", analytics_context=context)
    phase = next(item for item in overview["phase_health"] if item["operation_type"] == "maintenance")
    assert phase["phase_runs_measured"] == phase["phase_runs_eligible"] == 1
    assert phase["source_run_count"] == phase["transform_run_count"] == 0
    assert phase["destination_duration_seconds"] == 7
    assert phase["overhead_duration_seconds"] == detail["overhead_duration_seconds"]
    assert phase["total_duration_seconds"] == 10
    assert performance["summary"]["avg_duration_seconds"] == 100
    assert performance["summary"]["bottleneck_phase"] == "destination"
    assert evidence["records"][0]["performance_bottleneck_phase"] == "destination"


@pytest.mark.parametrize("problem", ["pipeline_missing", "present_invalid", "missing_destination", "overlap", "outside", "attempt_mismatch"])
def test_strict_diagnostics_reject_invalid_timing_but_contribution_uses_recorded_values(tmp_path, problem):
    row = maintenance_record()
    attempt = row["attempts"][-1]
    if problem == "pipeline_missing":
        row["operation_type"] = "etl"
    elif problem == "present_invalid":
        attempt["source"] = copy.deepcopy(record()["attempts"][-1]["source"])
        attempt["source"].update(start_time=None, end_time=None, duration_seconds=None, status=None)
    elif problem == "missing_destination":
        attempt["destination"].update(start_time=None, end_time=None, duration_seconds=None)
    elif problem == "overlap":
        attempt["source"] = copy.deepcopy(record()["attempts"][-1]["source"])
    elif problem == "outside":
        attempt["destination"]["end_time"] = "2026-10-04T12:00:20Z"
    else:
        attempt["duration_seconds"] = 100
    assert phase_timing(row)["available"] is False
    assert final_overhead_duration(row) is None
    with connection(tmp_path, [row]) as conn:
        result = performance_read_model([], {}, grain="day", timezone_name="UTC", analytics_context=(conn, [1], "test"))
    assert result["summary"]["phase_runs_measured"] == 0
    assert result["summary"]["bottleneck_phase"] == "unknown"
    contribution = result["phase_contribution_by_stage_operation"][0]
    if problem == "missing_destination":
        assert contribution["phase_runs_measured"] == 0
        assert contribution["total_duration_seconds"] is None
    else:
        assert contribution["phase_runs_measured"] == 1
        assert contribution["destination_duration_seconds"] == 7
        assert contribution["total_duration_seconds"] is not None


def test_mixed_coverage_statuses_and_zero_time_groups(tmp_path):
    good = maintenance_record()
    good["status"] = "failed"  # No failed explicit phase: Overhead failure.
    good["attempts"][-1]["destination"]["status"] = None
    invalid = copy.deepcopy(good)
    invalid.update(dataflow_run_id="invalid", operation_type="etl")
    zero = maintenance_record()
    zero.update(dataflow_run_id="zero", stage="zero")
    attempt = zero["attempts"][-1]
    attempt.update(end_time=attempt["start_time"], duration_seconds=0)
    attempt["destination"].update(end_time=attempt["start_time"], duration_seconds=0)
    with connection(tmp_path, [good, invalid, zero]) as conn:
        result = overview_read_model([], {}, grain="day", timezone_name="UTC", analytics_context=(conn, [1], "test"))
    total = result["phase_health"][0]
    assert total["phase_runs_measured"] == 3 and total["phase_runs_eligible"] == 3
    maintenance = next(item for item in result["phase_health"] if item["operation_type"] == "maintenance")
    assert maintenance["overhead_failed"] == 1
    assert maintenance["destination_unknown"] == 1
    assert sum(maintenance[f"destination_{status}"] for status in ("succeeded", "failed", "skipped", "running", "pending", "unknown")) == maintenance["destination_run_count"]
    with connection(tmp_path, [zero]) as conn:
        result = performance_read_model([], {}, grain="day", timezone_name="UTC", analytics_context=(conn, [1], "zero"))
    assert result["phase_contribution_by_stage_operation"][0]["total_duration_seconds"] == 0
    assert result["phase_contribution_by_stage_operation"][0]["phase_runs_measured"] == 1
    assert result["summary"]["bottleneck_phase"] == "unknown"


def test_missing_groups_aggregate_and_literal_unknown_stays_separate(tmp_path):
    from datacoolie_studio.domains.monitoring.dimensions import UNKNOWN_KEY
    from datacoolie_studio.domains.monitoring.read_models.dataflows import dataflows_read_model
    good = maintenance_record()
    good["stage"] = None
    missing = copy.deepcopy(good)
    missing.update(dataflow_run_id="missing", stage="unknown", attempts=[])
    empty = copy.deepcopy(missing)
    empty.update(dataflow_run_id="empty", stage="")
    with connection(tmp_path, [good, missing, empty]) as conn:
        result = dataflows_read_model([], {}, grain="day", timezone_name="UTC", analytics_context=(conn, [1], "test"))
    phase = next(item for item in result["phase_health_by_stage"] if item["stage"] == UNKNOWN_KEY)
    literal = next(item for item in result["phase_health_by_stage"] if item["stage"] == 'unknown')
    assert literal['phase_runs_eligible'] == 1 and literal['phase_runs_measured'] == 0
    assert phase["phase_runs_measured"] == 1 and phase["phase_runs_eligible"] == 2
    assert phase["total_duration_seconds"] == 10
    assert phase["destination_run_count"] == 1
