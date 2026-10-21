"""The Job table and drawer count result requires a reported expected total."""
import pytest

from datacoolie_studio.domains.monitoring.metrics.investigation import enrich_job_run_for_investigation


@pytest.mark.parametrize("expected", [None, ""])
@pytest.mark.parametrize("observed", [0, 2])
def test_missing_expected_count_is_unavailable_even_with_children(expected, observed):
    row = enrich_job_run_for_investigation({"child_scope_coverage": "full", "total_dataflows": expected, "child_dataflow_count": observed})
    assert row["reconciliation_status"] == "not_available"
    assert row["reconciliation_mismatch_count"] == 0


@pytest.mark.parametrize("expected,observed,result", [(0, 0, "matched"), (2, 2, "matched"), (2, 1, "mismatch"), (0, 1, "mismatch")])
def test_known_expected_counts_match_exactly(expected, observed, result):
    row = enrich_job_run_for_investigation({"child_scope_coverage": "full", "total_dataflows": expected, "child_dataflow_count": observed})
    assert row["reconciliation_status"] == result
    assert row["reconciliation_mismatch_count"] == int(result == "mismatch")


@pytest.mark.parametrize("scope", ["subset", "lost_detail", "unknown_loss", "unknown_snapshot", "stale_snapshot"])
def test_incomplete_scope_takes_precedence_over_count_comparison(scope):
    row = enrich_job_run_for_investigation({"child_scope_coverage": scope, "total_dataflows": None, "child_dataflow_count": 2})
    assert row["reconciliation_status"] == "insufficient_coverage"
    assert row["reconciliation_mismatch_count"] == 0
