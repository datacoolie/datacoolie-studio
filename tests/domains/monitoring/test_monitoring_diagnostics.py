from __future__ import annotations


from datacoolie_studio.domains.monitoring.metrics.diagnostics_projections import _diagnostics_field_completeness, _diagnostics_job_linkage_summary, _diagnostics_page


def test_diagnostics_treats_conditional_evidence_as_informational():
    row = {
        "job_id": "job-1",
        "dataflow_id": "df-1",
        "dataflow_run_id": "run-1",
        "dataflow_name": "orders",
        "status": "succeeded",
        "start_time": "2026-07-01T00:00:00+00:00",
        "end_time": "2026-07-01T00:01:00+00:00",
        "duration_seconds": 60,
        "source_duration_seconds": 20,
        "transform_duration_seconds": 20,
        "destination_duration_seconds": 20,
        "source_name": "src",
        "source_connection_type": "lakehouse",
        "source_rows_read": 10,
        "destination_name": "dst",
        "destination_connection_type": "lakehouse",
        "destination_load_type": "append",
    }
    job = {
        "job_id": "job-1",
        "status": "succeeded",
        "start_time": "2026-07-01T00:00:00+00:00",
        "end_time": "2026-07-01T00:01:00+00:00",
        "duration_seconds": 60,
        "total_dataflows": 1,
        "total_succeeded": 1,
        "total_failed": 0,
        "total_skipped": 0,
        "engine_name": "PolarsEngine",
        "metadata_provider_name": "FileProvider",
        "platform_name": "LocalPlatform",
    }

    diagnostics = _diagnostics_page([row], [job], [], {"mismatch_count": 0, "checks": []})
    conditional = [item for item in diagnostics["field_completeness"] if item["applicability"] == "conditional"]

    assert {item["group"] for item in conditional} == {"watermark evidence", "maintenance evidence"}
    assert all(item["actionable"] is False for item in conditional)
    assert diagnostics["kpis"]["field_readiness_issues"] == 0
    assert diagnostics["kpis"]["conditional_evidence_groups"] == 2
    assert not any(item["target"] in {"dataflow · watermark evidence", "dataflow · maintenance evidence"} for item in diagnostics["investigation_queue"])

def test_diagnostics_keeps_universal_evidence_actionable():
    completeness = _diagnostics_field_completeness(
        [{"job_id": "job-1", "dataflow_id": "df-1", "dataflow_run_id": "run-1", "dataflow_name": "orders"}],
        [{"job_id": "job-1"}],
    )
    runtime = next(item for item in completeness if item["record_type"] == "dataflow" and item["group"] == "runtime duration")

    assert runtime["actionable"] is True
    assert runtime["applicability"] == "universal"
    assert runtime["severity"] == "bad"

    diagnostics = _diagnostics_page(
        [{"job_id": "job-1", "dataflow_id": "df-1", "dataflow_run_id": "run-1", "dataflow_name": "orders"}],
        [{"job_id": "job-1"}],
        [],
        {"mismatch_count": 0, "checks": []},
    )
    assert diagnostics["kpis"]["field_readiness_issues"] > 0

def test_diagnostics_linkage_zero_problem_counts_are_clearable():
    rows = _diagnostics_job_linkage_summary({
        "matched_ids": {"job-1"},
        "orphan_job_ids": set(),
        "job_only_ids": set(),
        "all_job_ids": {"job-1"},
    })

    assert rows[1]["count"] == 0 and rows[1]["severity"] == "good"
    assert rows[2]["count"] == 0 and rows[2]["severity"] == "good"
