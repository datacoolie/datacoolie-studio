from __future__ import annotations

import json

from datacoolie_studio.domains.analytics import schema, staging, store
from datacoolie_studio.domains.analytics.contracts import JobFile
from datacoolie_studio.domains.analytics.log_contract import log_validator
from datacoolie_studio.domains.analytics.serving_facts import rebuild_monitoring_serving_facts
from datacoolie_studio.domains.monitoring.diagnostics_repository import diagnostics_read_model
from datacoolie_studio.domains.monitoring.log_repository import monitoring_filter_sql
from datacoolie_studio.domains.monitoring.metrics.investigation import declared_destination_key_sql
from datacoolie_studio.domains.monitoring.read_models.failures import failures_read_model
from datacoolie_studio.domains.monitoring.read_models.jobs import jobs_read_model
from tests.domains.monitoring.test_v6_freshness_contract import connection
from tests.support.v6_records import native_record


def _add_job(conn, path, record):
    assert not list(log_validator().iter_errors(record))
    path.write_text(json.dumps(record), encoding="utf-8")
    item = JobFile(str(path), "job_json", "{}", str(path))
    relations, types, _, _ = staging.stage_job_relations(conn, [item])
    store.insert_job_file(conn, 1, str(path), "job_json", "{}", source_relation=relations[0], source_types=types[0])
    rebuild_monitoring_serving_facts(conn, dataflow_table=schema.DATAFLOW_TABLE,
        job_table=schema.JOB_TABLE, dataflow_column_types=schema.DATAFLOW_COLUMN_TYPES,
        job_column_types=schema.JOB_COLUMN_TYPES)


def test_missing_snapshot_time_is_uncertain_with_explanation_evidence(tmp_path):
    flow = native_record({})
    job = native_record({"_type": "job_run_log", "job_id": flow["job_id"],
        "log_session_id": flow["log_session_id"], "log_records_dropped": 0,
        "total_dataflows": 2, "total_succeeded": 2, "total_failed": 0, "total_skipped": 0})
    job.pop("observed_at", None)
    with connection(tmp_path, [flow]) as conn:
        _add_job(conn, tmp_path / "job.json", job)
        result = jobs_read_model([], {}, grain="day", timezone_name="UTC", analytics_context=(conn, [1], "test"))
        assert result["reconciliation_checks"]
        for check in result["reconciliation_checks"]:
            assert check["scope_coverage"] == "unknown_snapshot"
            assert check["severity"] == "insufficient_coverage"
            assert check["difference"] is None
            assert check["observed_at"] is None
            assert int(check["log_records_dropped"]) == 0
            assert check["latest_child_time"] is not None


def test_native_no_attempt_and_empty_job_population_have_no_false_readiness_issue(tmp_path):
    row = native_record({"status": "skipped", "attempts": [], "read_request": None})
    with connection(tmp_path, [row]) as conn:
        result = diagnostics_read_model([], {}, grain="day", timezone_name="UTC", analytics_context=(conn, [1], "test"))
    groups = result["diagnostics"]["field_completeness"]
    assert result["diagnostics"]["kpis"]["field_readiness_issues"] == 0
    assert result["diagnostics"]["kpis"]["field_readiness_rate"] == 100
    for item in groups:
        if item["record_type"] == "job" or item["group"] in {"source evidence", "transform evidence", "destination evidence", "watermark evidence"}:
            assert item["records"] == 0
            assert item["actionable"] is False
            assert item["completeness_rate"] is None


def test_native_known_record_loss_warns_without_claiming_reconciliation_mismatch(tmp_path):
    flow = native_record({})
    job = native_record({"_type": "job_run_log", "job_id": flow["job_id"],
        "log_session_id": flow["log_session_id"], "log_records_dropped": 1,
        "log_bytes_dropped": 128, "total_dataflows": 2, "total_succeeded": 2,
        "total_failed": 0, "total_skipped": 0, "observed_at": "2026-11-02T00:00:00Z"})
    with connection(tmp_path, [flow]) as conn:
        _add_job(conn, tmp_path / "loss-job.json", job)
        result = diagnostics_read_model([], {}, grain="day", timezone_name="UTC", analytics_context=(conn, [1], "test"))
    diagnostics = result["diagnostics"]
    assert diagnostics["kpis"]["health_status"] == "warning"
    assert diagnostics["kpis"]["reconciliation_mismatches"] == 0
    assert diagnostics["source_coverage"][0]["status"] == "warning"
    assert diagnostics["source_coverage"][0]["warning_count"] == 1
    assert result["reconciliation"]["scope_coverage"] == "insufficient_coverage"
    assert {item["scope_coverage"] for item in result["reconciliation"]["checks"]} == {"lost_detail"}


def test_nullable_phase_members_are_present_schema_fields(tmp_path):
    row = native_record({})
    row["attempts"][-1]["transform"] = None
    row["attempts"][-1]["source"]["rows_read"] = None
    with connection(tmp_path, [row]) as conn:
        result = diagnostics_read_model([], {}, grain="day", timezone_name="UTC", analytics_context=(conn, [1], "test"))
    assert result["diagnostics"]["kpis"]["field_readiness_issues"] == 0
    source = next(item for item in result["diagnostics"]["field_completeness"] if item["group"] == "source evidence")
    assert source["completeness_rate"] == 100


def test_capture_markers_have_bounded_distinct_summary(tmp_path):
    row = native_record({"diagnostic_issues": [
        {"field": "/message", "reason": "truncated"},
        {"field": "/attempts/0/source/details", "reason": "invalid"},
        {"field": "/diagnostic_issues", "reason": "omitted"},
    ]})
    with connection(tmp_path, [row]) as conn:
        result = diagnostics_read_model([], {}, grain="day", timezone_name="UTC", analytics_context=(conn, [1], "test"))
    capture = result["diagnostics"]["diagnostic_capture"]
    assert capture["summary"] == {
        "affected_records": 1,
        "known_markers": 3,
        "omitted_marker_records": 1,
        "overflow_records": 1,
        "evidence_records_count": 3,
        "evidence_limit": 50,
        "truncated": False,
    }
    assert {item["reason"] for item in capture["by_reason"]} == {"truncated", "invalid", "omitted"}
    assert len(capture["evidence"]) == 3
    assert result["diagnostics"]["kpis"]["reconciliation_mismatches"] == 0
    queue_item = next(item for item in result["diagnostics"]["investigation_queue"] if item["category"] == "diagnostic capture")
    assert queue_item["_source_id"] == 1
    assert queue_item["log_session_id"] == row["log_session_id"]
    assert queue_item["dataflow_run_id"] == row["dataflow_run_id"]


def test_recovered_attempt_evidence_does_not_increment_terminal_failure(tmp_path):
    row = native_record({})
    failed = json.loads(json.dumps(row["attempts"][0]))
    failed.update(attempt_number=1, status="failed", message="retryable read failure")
    row["attempts"][-1]["attempt_number"] = 2
    row["attempts"].insert(0, failed)
    with connection(tmp_path, [row]) as conn:
        result = failures_read_model([], {}, timezone_name="UTC", analytics_context=(conn, [1], "test"))
    assert result["summary"]["failed_dataflows"] == 0
    assert result["summary"]["recovered_attempt_count"] == 1
    assert result["summary"]["recovered_run_count"] == 1
    assert result["failed_records"] == []
    assert result["recovered_attempts"][0]["attempt_message"] == "retryable read failure"


def test_exact_entity_predicates_do_not_match_display_aliases(tmp_path):
    first = native_record({"dataflow_id": "exact", "dataflow_run_id": "first"})
    second = native_record({"dataflow_id": "other", "dataflow_run_id": "second", "dataflow_name": "exact"})
    second["destination_schema"] = "different"
    with connection(tmp_path, [first, second]) as conn:
        clause, params = monitoring_filter_sql({"investigateKind": "dataflow_id", "investigateValue": "exact"}, "d", "d")
        assert conn.execute(f"SELECT dataflow_run_id FROM {schema.DATAFLOW_TABLE} d WHERE true {clause}", params).fetchall() == [("first",)]
        target = conn.execute(f"SELECT {declared_destination_key_sql('d')} FROM {schema.DATAFLOW_TABLE} d WHERE dataflow_run_id='first'").fetchone()[0]
        clause, params = monitoring_filter_sql({"investigateKind": "declared_destination", "investigateValue": target}, "d", "d")
        assert conn.execute(f"SELECT dataflow_run_id FROM {schema.DATAFLOW_TABLE} d WHERE true {clause}", params).fetchall() == [("first",)]


def test_absent_native_duration_stays_unknown_across_page_builders(tmp_path):
    from datacoolie_studio.domains.monitoring.page_dispatch import build_monitoring_page
    flow = native_record({"duration_seconds": None})
    job = native_record({"_type": "job_run_log", "job_id": flow["job_id"],
        "log_session_id": flow["log_session_id"], "duration_seconds": None})
    with connection(tmp_path, [flow]) as conn:
        _add_job(conn, tmp_path / "job.json", job)
        context = (conn, [1], "duration-null")
        for page, stats_key in [("overview", "job_duration_stats"), ("jobs", "job_duration_stats"), ("dataflows", "dataflow_duration_stats")]:
            result = build_monitoring_page([], page, {}, analytics_context=context)
            stats = result["operations"][stats_key]
            assert stats["count"] == 0
            assert stats["avg_duration_seconds"] is None
            assert stats["p95_duration_seconds"] is None
        performance = build_monitoring_page([], "performance", {}, analytics_context=context)
        assert performance["performance"]["kpis"]["p95_duration_seconds"] is None
        assert performance["performance"]["kpis"]["duration_pressure_ratio"] is None


def test_native_maintenance_effects_and_detail_contradictions_prevent_false_noop(tmp_path):
    from copy import deepcopy
    from datacoolie_studio.domains.monitoring.read_models.maintenance import maintenance_read_model
    base = native_record({"operation_type": "maintenance"})
    base["metrics"] = {key: 0 for key in base["metrics"]}
    destination = base["attempts"][-1]["destination"]
    destination.update({key: 0 for key in base["metrics"] if key != "rows_read"})
    destination.update(
        operation_type="compact",
        operation_details=[{"operation": "compact", "status": "succeeded"}],
    )
    records = []
    for index in range(4):
        row = deepcopy(base)
        row["dataflow_run_id"] = f"maintenance-{index}"
        if index == 1:
            row["metrics"].update(files_added=2, bytes_added=100)
            row["attempts"][-1]["destination"].update(files_added=2, bytes_added=100)
        elif index == 2:
            row["metrics"]["bytes_removed"] = None
            row["attempts"][-1]["destination"]["bytes_removed"] = None
        elif index == 3:
            row["attempts"][-1]["destination"]["operation_details"].append({
                "operation": "compact", "status": "succeeded",
                "metrics": {"bytes_removed": 1},
            })
        assert not list(log_validator().iter_errors(row))
        records.append(row)
    with connection(tmp_path, records) as conn:
        result = maintenance_read_model([], {}, grain="day", timezone_name="UTC", analytics_context=(conn, [1], "native-noop"))
    assert result["summary"]["no_op_runs"] == 1
    assert result["summary"]["bytes_reclaimed"] is None
