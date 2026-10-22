from __future__ import annotations

import pytest

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from datacoolie_studio.core.time import parse_utc_datetime
from datacoolie_studio.domains.monitoring.metrics.row_normalization import (
    dataflow_operation_type as _dataflow_operation_type,
    destination_operation_type as _destination_operation_type,
    job_runs_by_dataflow_operation_type as _job_runs_by_dataflow_operation_type,
)
from datacoolie_studio.domains.monitoring.metrics.investigation import (
    phase_duration as _phase_duration,
)
from datacoolie_studio.domains.monitoring.metrics.time_buckets import time_value as _time_value, trend_context as _trend_context
from datacoolie_studio.domains.monitoring.metrics.filtering import filter_log_rows, normalize_monitoring_filters_for_timezone
from datacoolie_studio.domains.monitoring.metrics.overview_projections import _attention_queue, _health_page, _operation_windows, _operations_page, _status_by_date
from datacoolie_studio.domains.monitoring.page_service import monitoring_page, public_monitoring_page

from tests.domains.monitoring.monitoring_test_support import (
    _published_monitoring_paths,
)

@pytest.mark.usefixtures("monitoring_workspace")
def test_monitoring_overview_page_has_bounded_read_model(tmp_path: Path, monkeypatch):
    paths = _published_monitoring_paths(tmp_path, monkeypatch)
    report = monitoring_page(paths, page="overview")
    assert report["summary"]["dataflow_records"] > 0
    assert report["summary"]["job_records"] > 0
    assert report["summary"]["requested_grain"] == "auto"
    assert report["summary"]["effective_grain"] in {"hour", "day", "week", "month"}
    assert report["summary"]["latest_log_at"].endswith("+00:00")
    assert report["summary"]["latest_job_log_at"].endswith("+00:00")
    assert report["summary"]["latest_dataflow_log_at"].endswith("+00:00")
    assert report["summary"]["latest_log_at"].startswith(report["summary"]["date_range"]["max"])
    assert report["operations"]["kpis"]["total_jobs"] > 0
    assert report["operations"]["dataflows_by_date_status"]
    assert {"bucket", "bucket_start", "bucket_end", "grain"} <= set(report["operations"]["dataflows_by_date_status"][0])
    assert "failed_by_stage" in report["failures"]
    assert report["performance"]["duration_breakdown"] == []
    assert report["volume"]["rows_by_date"]
    assert report["maintenance"]["kpis"] == {}
    assert "freshness" in report
    assert "latest_freshness_by_dataflow" in report["freshness"]
    assert report["health"]["status"] in {"healthy", "warning", "has_issues", "no_log_evidence"}
    assert report["coverage"] == {}
    assert report["diagnostics"]["kpis"] == {}
    assert report["attention"]
    assert report["reconciliation"] == {}
    assert "metric_definitions" not in report

@pytest.mark.usefixtures("monitoring_workspace")
def test_public_monitoring_pages_project_only_consumed_sections(tmp_path: Path, monkeypatch):
    paths = _published_monitoring_paths(tmp_path, monkeypatch)
    report = monitoring_page(paths, page="overview")
    allowed = {
        "overview": {"health", "attention", "operations", "failures", "volume"},
        "jobs": {"operations", "reconciliation"},
        "dataflows": {"operations", "volume"},
        "failures": {"operations", "failures"},
        "freshness": {"freshness"},
        "performance": {"performance"},
        "volume": {"volume"},
        "maintenance": {"maintenance"},
        "diagnostics": {"coverage", "reconciliation", "diagnostics"},
    }

    for page, sections in allowed.items():
        projected = public_monitoring_page(page, report)
        assert projected["schema_version"] == "monitoring-page.v12"
        assert projected["page"] == page
        assert set(projected) == {"schema_version", "page", "summary", *sections}
        assert "metric_definitions" not in projected

    performance = public_monitoring_page("performance", report)
    assert "investigation_queue" not in performance["performance"]

def test_attention_queue_includes_freshness_signals_and_existing_tabs():
    items = _attention_queue(
        failures={},
        performance={},
        maintenance={"kpis": {}},
        coverage={"read_errors": 1},
        reconciliation={"mismatch_count": 2},
        freshness={"kpis": {"stale_candidates": 3, "watermark_unchanged_runs": 1}},
        health={"latest_log_age_days": None, "failed_jobs_last_7_days": 0, "failed_dataflows_last_7_days": 0},
    )

    codes = {item["code"] for item in items}
    assert "log_read_errors" in codes
    assert "stale_dataflows" in codes
    assert "watermark_not_advanced" in codes
    assert "log_reconciliation" in codes
    assert all(item["target"] != "sources" for item in items)

def test_attention_queue_applies_repeated_failure_and_slowest_stage_thresholds():
    below_threshold = _attention_queue(
        failures={"top_failing_dataflows": [{"dataflow_name": "orders", "error_count": 2}]},
        performance={"duration_by_stage": [{"stage": "load_delta", "p95_duration_seconds": 59}]},
        maintenance={"kpis": {}},
        coverage={"read_errors": 0},
        reconciliation={"mismatch_count": 0},
        freshness={"kpis": {}},
        health={"status": "healthy", "latest_log_age_days": 1, "failed_jobs_last_7_days": 0, "failed_dataflows_last_7_days": 0},
    )
    assert "repeated_failure" not in {item["code"] for item in below_threshold}
    assert "slowest_stage" not in {item["code"] for item in below_threshold}

    at_threshold = _attention_queue(
        failures={"top_failing_dataflows": [{"dataflow_name": "orders", "error_count": 3}]},
        performance={"duration_by_stage": [{"stage": "load_delta", "p95_duration_seconds": 60}]},
        maintenance={"kpis": {}},
        coverage={"read_errors": 0},
        reconciliation={"mismatch_count": 0},
        freshness={"kpis": {}},
        health={"status": "healthy", "latest_log_age_days": 1, "failed_jobs_last_7_days": 0, "failed_dataflows_last_7_days": 0},
    )
    assert "repeated_failure" in {item["code"] for item in at_threshold}
    assert "slowest_stage" in {item["code"] for item in at_threshold}

def test_attention_queue_adds_no_log_evidence_signal():
    items = _attention_queue(
        failures={},
        performance={},
        maintenance={"kpis": {}},
        coverage={"read_errors": 0},
        reconciliation={"mismatch_count": 0},
        freshness={"kpis": {}},
        health={"status": "no_log_evidence", "latest_log_age_days": None, "failed_jobs_last_7_days": 0, "failed_dataflows_last_7_days": 0},
    )
    assert "no_log_evidence" in {item["code"] for item in items}

def test_attention_queue_rolls_up_page_health_and_prioritizes_severity():
    items = _attention_queue(
        failures={},
        performance={"kpis": {"duration_pressure_ratio": 10, "p95_duration_seconds": 60, "optimization_candidate_count": 2}},
        maintenance={"kpis": {"lagged_tables": 3, "latest_active_tables": 1}},
        coverage={"read_errors": 0},
        reconciliation={"mismatch_count": 0},
        freshness={"kpis": {}},
        health={"status": "healthy", "latest_log_age_days": 1, "failed_jobs_last_7_days": 0, "failed_dataflows_last_7_days": 0},
        operations={
            "dataflow_kpis": {"running": 1, "pending": 0},
            "jobs_by_engine_provider": [
                {"engine_name": "fabric", "metadata_provider_name": "analyst", "jobs": 5, "failed": 1, "success_rate": 80}
            ],
        },
        diagnostics={"kpis": {"orphan_dataflow_job_ids": 1, "jobs_without_dataflow_records": 1, "cache_warning_count": 2}},
    )

    codes = {item["code"] for item in items}
    assert {
        "performance_pressure",
        "optimization_candidates",
        "maintenance_lag",
        "maintenance_active",
        "job_linkage_gaps",
        "log_cache_warnings",
        "active_dataflows",
        "runtime_context_health",
    } <= codes
    assert items[0]["severity"] == "bad"
    assert len(items) == 8

def test_attention_queue_uses_empty_collection_for_healthy_evidence():
    items = _attention_queue(
        failures={},
        performance={"kpis": {}},
        maintenance={"kpis": {}},
        coverage={"read_errors": 0},
        reconciliation={"mismatch_count": 0},
        freshness={"kpis": {}},
        health={"status": "healthy", "latest_log_age_days": 1, "failed_jobs_last_7_days": 0, "failed_dataflows_last_7_days": 0},
    )

    assert items == []

def test_health_page_uses_failed_3d_and_7d_windows():
    now = datetime.now(timezone.utc)
    jobs = [{"job_id": "j1", "status": "failed", "end_time": (now - timedelta(days=5)).isoformat()}]
    rows = [{"dataflow_id": "d1", "status": "succeeded", "end_time": now.isoformat()}]

    health_warning = _health_page(
        rows=rows,
        jobs=jobs,
        operations={"kpis": {"total_jobs": 1}},
        coverage={"status": "ok"},
        reconciliation={"mismatch_count": 0},
    )

    assert health_warning["status"] == "warning"
    assert "last 7 days" in " ".join(health_warning["reasons"]).lower()
    assert health_warning["failed_jobs_last_3_days"] == 0
    assert health_warning["failed_jobs_last_7_days"] == 1

    jobs_recent = [{"job_id": "j2", "status": "failed", "end_time": (now - timedelta(days=2)).isoformat()}]
    health_issue = _health_page(
        rows=rows,
        jobs=jobs_recent,
        operations={"kpis": {"total_jobs": 1}},
        coverage={"status": "ok"},
        reconciliation={"mismatch_count": 0},
    )
    assert health_issue["status"] == "has_issues"
    assert health_issue["failed_jobs_last_3_days"] == 1

def test_health_page_uses_maintenance_7d_and_14d_windows():
    now = datetime.now(timezone.utc)
    base_rows = [{"dataflow_id": "d1", "status": "succeeded", "end_time": now.isoformat()}]
    jobs = [{"job_id": "j1", "status": "succeeded", "end_time": now.isoformat()}]

    maintenance_10d = {
        "dataflow_id": "m1",
        "operation_type": "maintenance",
        "status": "failed",
        "end_time": (now - timedelta(days=10)).isoformat(),
    }
    health_warning = _health_page(
        rows=[*base_rows, maintenance_10d],
        jobs=jobs,
        operations={"kpis": {"total_jobs": 1}},
        coverage={"status": "ok"},
        reconciliation={"mismatch_count": 0},
    )
    assert health_warning["status"] == "warning"
    assert health_warning["maintenance_failed_last_7_days"] == 0
    assert health_warning["maintenance_failed_last_14_days"] == 1

    maintenance_3d = {
        "dataflow_id": "m2",
        "operation_type": "maintenance",
        "status": "failed",
        "end_time": (now - timedelta(days=3)).isoformat(),
    }
    health_issue = _health_page(
        rows=[*base_rows, maintenance_3d],
        jobs=jobs,
        operations={"kpis": {"total_jobs": 1}},
        coverage={"status": "ok"},
        reconciliation={"mismatch_count": 0},
    )
    assert health_issue["status"] == "has_issues"
    assert health_issue["maintenance_failed_last_7_days"] == 1

def test_operation_windows_today_uses_global_timezone():
    reference_now = datetime(2026, 6, 16, 0, 30, tzinfo=timezone.utc)
    jobs = [{"job_id": "j1", "status": "succeeded", "end_time": "2026-06-15T23:45:00+00:00"}]
    rows = [{"dataflow_id": "d1", "status": "succeeded", "end_time": "2026-06-15T23:45:00+00:00"}]

    utc_windows = _operation_windows(rows, jobs, timezone_info=timezone.utc, now=reference_now)
    assert utc_windows["today"]["job_runs"] == 0
    assert utc_windows["today"]["dataflow_runs"] == 0

    pacific_kiritimati = ZoneInfo("Pacific/Kiritimati")
    local_windows = _operation_windows(rows, jobs, timezone_info=pacific_kiritimati, now=reference_now)
    assert local_windows["today"]["job_runs"] == 1
    assert local_windows["today"]["dataflow_runs"] == 1

def test_today_filter_normalizes_to_custom_range_in_global_timezone():
    reference_now = datetime(2026, 6, 16, 0, 30, tzinfo=timezone.utc)
    pacific_kiritimati = ZoneInfo("Pacific/Kiritimati")
    filters = normalize_monitoring_filters_for_timezone(
        {"range": "today", "status": "failed"},
        timezone_info=pacific_kiritimati,
        now=reference_now,
    )

    assert filters["range"] == "custom"
    assert filters["status"] == "failed"
    assert parse_utc_datetime(filters["startTime"]) == datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)
    assert parse_utc_datetime(filters["endTime"]) == datetime(2026, 6, 16, 9, 59, 59, 999999, tzinfo=timezone.utc)

def test_filter_rows_today_respects_global_timezone_after_normalization():
    reference_now = datetime(2026, 6, 16, 0, 30, tzinfo=timezone.utc)
    pacific_kiritimati = ZoneInfo("Pacific/Kiritimati")
    filters = normalize_monitoring_filters_for_timezone(
        {"range": "today"},
        timezone_info=pacific_kiritimati,
        now=reference_now,
    )

    rows = [
        {"status": "succeeded", "end_time": "2026-06-15T23:45:00+00:00"},
        {"status": "failed", "end_time": "2026-06-14T22:00:00+00:00"},
    ]
    filtered = filter_log_rows(rows, filters, include_dataflow_filters=False)
    assert len(filtered) == 1
    assert filtered[0]["status"] == "succeeded"

def test_filter_rows_custom_range_compares_explicit_timezones_as_same_instant():
    filters = {
        "range": "custom",
        "startTime": "2026-06-18T08:23:00+00:00",
        "endTime": "2026-06-18T08:24:00+00:00",
    }
    rows = [
        {"status": "succeeded", "end_time": "2026-06-18T15:23:30+07:00"},
        {"status": "failed", "end_time": "2026-06-18T08:23:30+07:00"},
    ]

    filtered = filter_log_rows(rows, filters, include_dataflow_filters=False)

    assert [row["status"] for row in filtered] == ["succeeded"]

def test_time_value_sorts_explicit_timezones_by_utc_instant():
    earlier = _time_value("2026-06-18T15:23:30+07:00")
    later = _time_value("2026-06-18T08:24:00+00:00")

    assert earlier < later

def test_monitoring_status_semantics_exclude_skipped_from_execution_rate():
    now = datetime.now(timezone.utc).isoformat()
    jobs = [
        {"job_id": "j1", "status": "succeeded", "duration_seconds": 10, "end_time": now},
        {"job_id": "j2", "status": "failed", "duration_seconds": 20, "end_time": now},
        {"job_id": "j3", "status": "skipped", "duration_seconds": 999},
        {"job_id": "j4", "status": "running", "duration_seconds": 888},
        {"job_id": "j5", "status": "pending", "duration_seconds": 777},
    ]
    rows = [
        {"dataflow_id": "d1", "status": "succeeded", "duration_seconds": 5, "end_time": now},
        {"dataflow_id": "d2", "status": "failed", "duration_seconds": 15, "end_time": now},
        {"dataflow_id": "d3", "status": "skipped", "duration_seconds": 666},
        {"dataflow_id": "d4", "status": "running", "duration_seconds": 555},
        {"dataflow_id": "d5", "status": "pending", "duration_seconds": 444},
    ]

    operations = _operations_page(rows, jobs)

    assert operations["kpis"]["job_success_rate"] == 50
    assert operations["kpis"]["job_failure_rate"] == 50
    assert operations["kpis"]["job_skip_rate"] == 20
    assert operations["kpis"]["job_running_rate"] == 20
    assert operations["kpis"]["job_pending_rate"] == 20
    assert operations["dataflow_kpis"]["success_rate"] == 50
    assert operations["dataflow_kpis"]["failure_rate"] == 50
    assert operations["dataflow_kpis"]["skip_rate"] == 20
    assert operations["dataflow_kpis"]["running_rate"] == 20
    assert operations["dataflow_kpis"]["pending_rate"] == 20
    assert operations["windows"]["today"]["job_runs"] == 2
    assert operations["windows"]["last_7_days"]["dataflow_failed"] == 1
    assert operations["job_duration_stats"]["avg_duration_seconds"] == 15
    assert operations["job_duration_stats"]["p95_duration_seconds"] == 20
    assert operations["job_duration_stats"]["p99_duration_seconds"] == 20
    assert operations["dataflow_duration_stats"]["q1_duration_seconds"] == 5
    assert operations["dataflow_duration_stats"]["q3_duration_seconds"] == 15
    assert operations["dataflow_duration_stats"]["p99_duration_seconds"] == 15
    assert sum(int(row["total"]) for row in operations["jobs_by_date_status"]) == 5
    assert any(int(row["executable_total"]) == 2 and row["success_rate"] == 50 for row in operations["jobs_by_date_status"])
    assert any(int(row["executable_total"]) == 2 and row["failure_rate"] == 50 for row in operations["jobs_by_date_status"])

def test_operations_page_exposes_job_page_visual_metrics():
    jobs = [
        {
            "job_id": "j1",
            "stages": '["bronze", "silver"]',
            "operation_types": '["etl", "load"]',
            "status": "succeeded",
            "duration_seconds": 10,
            "end_time": "2026-06-10T00:00:00+00:00",
            "child_dataflow_count": 2,
            "total_dataflows": 2,
            "total_rows_read": 100,
            "total_rows_written": 90,
        },
        {
            "job_id": "j2",
            "stages": '["bronze", "silver"]',
            "operation_types": '["etl", "load"]',
            "status": "failed",
            "duration_seconds": 20,
            "end_time": "2026-06-11T00:00:00+00:00",
            "child_dataflow_count": 8,
            "total_dataflows": 8,
            "child_failed_count": 2,
            "total_rows_read": 200,
            "total_rows_written": 10,
        },
        {
            "job_id": "j3",
            "stages": '["maintenance"]',
            "operation_types": "maintenance",
            "status": "skipped",
            "duration_seconds": 5,
            "end_time": "2026-06-12T00:00:00+00:00",
            "total_dataflows": 1,
        },
    ]
    rows = [
        {
            "job_id": "j1",
            "operation_type": "etl",
            "status": "succeeded",
            "duration_seconds": 10,
            "source_rows_read": 100,
            "end_time": "2026-06-10T00:00:00+00:00",
        },
        {
            "job_id": "j2",
            "operation_type": "etl",
            "status": "failed",
            "duration_seconds": 20,
            "source_rows_read": 200,
            "end_time": "2026-06-11T00:00:00+00:00",
        },
        {
            "job_id": "j3",
            "operation_type": "maintenance",
            "status": "skipped",
            "duration_seconds": 5,
            "source_rows_read": 0,
            "end_time": "2026-06-12T00:00:00+00:00",
        },
    ]

    operations = _operations_page(rows=rows, jobs=jobs)

    duration_by_operation = {
        row["operation_type"]: row
        for row in operations["job_duration_by_operation_types"]
    }
    assert duration_by_operation["etl, load"]["count"] == 2
    assert duration_by_operation["etl, load"]["p50_duration_seconds"] == 10
    assert duration_by_operation["maintenance"]["skipped"] == 1

    fanout = operations["job_child_fanout_distribution"]
    fanout_by_total = {row["total_dataflows"]: row for row in fanout}
    assert fanout_by_total[1]["jobs"] == 1
    assert fanout_by_total[2]["jobs"] == 1
    assert fanout_by_total[8]["jobs"] == 1

    efficiency = operations["job_workload_efficiency"]
    assert efficiency[0]["job_id"] == "j2"
    assert efficiency[0]["operation_type"] == "etl"
    assert efficiency[0]["failed_child_dataflows"] == 1
    assert efficiency[0]["workload_size"] == 10
    assert efficiency[0]["workload_size_metric"] == "rows_read_per_second"

def test_operations_page_uses_raw_job_summary_fields():
    jobs = [
        {
            "job_id": "j1",
            "status": "succeeded",
            "total_running": 2,
            "total_pending": 3,
            "total_skipped": 5,
            "operation_types": '["etl", "maintenance"]',
        },
        {
            "job_id": "j2",
            "status": "failed",
            "total_running": 7,
            "total_pending": 11,
            "total_skipped": 13,
            "operation_types": "etl",
        },
    ]
    rows = [
        {"job_id": "j1", "operation_type": "ignored_child_value", "status": "succeeded"},
        {"job_id": "j2", "operation_type": "ignored_child_value", "status": "failed"},
    ]

    operations = _operations_page(rows=rows, jobs=jobs)

    assert operations["kpis"]["total_running"] == 9
    assert operations["kpis"]["total_pending"] == 14
    assert operations["kpis"]["total_skipped"] == 18
    by_operation = {
        row["operation_type"]: row
        for row in operations["job_runs_by_dataflow_operation_type"]
    }
    assert set(by_operation) == {"etl", "maintenance"}
    assert by_operation["etl"]["count"] == 2
    assert by_operation["etl"]["failed"] == 1
    assert by_operation["maintenance"]["count"] == 1

def test_job_operation_type_mix_ignores_child_rows_and_uses_job_field():
    rows = [{"job_id": "j1", "operation_type": "maintenance", "status": "succeeded"}]
    jobs = [{"job_id": "j1", "operation_types": "etl", "status": "succeeded"}]

    result = _job_runs_by_dataflow_operation_type(rows, jobs)

    assert result == [{"operation_type": "etl", "count": 1, "succeeded": 1}]

def test_monitoring_dimension_fallbacks_use_unknown():
    from datacoolie_studio.domains.monitoring.dimensions import UNKNOWN_KEY
    assert _dataflow_operation_type({"operation_type": None}) == UNKNOWN_KEY
    assert _dataflow_operation_type({"operation_type": ""}) == UNKNOWN_KEY
    assert _destination_operation_type({"destination_operation_type": "not_available"}) == "not_available"

    result = _job_runs_by_dataflow_operation_type(
        [],
        [
            {"job_id": "j1", "status": "succeeded", "operation_types": None},
            {"job_id": "j2", "status": "failed", "operation_types": '["etl", "not_available"]'},
        ],
    )

    by_operation = {row["operation_type"]: row for row in result}
    assert by_operation[UNKNOWN_KEY]["count"] == 1
    assert by_operation[UNKNOWN_KEY]["succeeded"] == 1
    assert by_operation['not_available']['failed'] == 1
    assert by_operation["etl"]["count"] == 1

def test_phase_duration_derives_overhead_from_total_runtime():
    row = {
        "duration_seconds": 10,
        "source_duration_seconds": 2,
        "transform_duration_seconds": 3,
        "destination_duration_seconds": 4,
        "overhead_duration_seconds": 7,
    }

    assert _phase_duration(row, "overhead", "overhead_duration_seconds") == 1
    assert _phase_duration({**row, "overhead_duration_seconds": -3}, "overhead", "overhead_duration_seconds") == 1
    assert _phase_duration({**row, "duration_seconds": None}, "overhead", "overhead_duration_seconds") is None

def test_status_by_date_returns_none_rate_when_no_executable_runs():
    rows = [
        {"status": "skipped", "end_time": "2026-06-16T00:00:00+00:00"},
        {"status": "running", "end_time": "2026-06-16T00:01:00+00:00"},
        {"status": "pending", "end_time": "2026-06-16T00:02:00+00:00"},
    ]

    by_date = _status_by_date(rows)

    assert by_date[0]["total"] == 3
    assert by_date[0]["executable_total"] == 0
    assert by_date[0]["success_rate"] is None
    assert by_date[0]["failure_rate"] is None

def test_trend_context_auto_grain_and_bucket_metadata_use_timezone():
    filters = {"range": "90d", "grain": "auto"}
    rows = [
        {"status": "succeeded", "end_time": "2026-06-01T01:00:00+00:00"},
        {"status": "failed", "end_time": "2026-06-08T01:00:00+00:00"},
    ]
    context = _trend_context(filters, rows, ZoneInfo("Asia/Saigon"))
    assert context["requested_grain"] == "auto"
    assert context["effective_grain"] == "day"

    by_date = _status_by_date(rows, trend_context=context)
    assert by_date[0]["bucket"] == "2026-06-01"
    assert by_date[0]["bucket_start"].endswith("+07:00")
    assert by_date[0]["grain"] == "day"

    manual_context = _trend_context({"range": "90d", "grain": "day"}, rows, ZoneInfo("Asia/Saigon"))
    assert manual_context["requested_grain"] == "day"
    assert manual_context["effective_grain"] == "day"

    coarser_context = _trend_context({"range": "90d", "grain": "week"}, rows, ZoneInfo("Asia/Saigon"))
    assert coarser_context["requested_grain"] == "week"
    assert coarser_context["effective_grain"] == "week"

def test_trend_context_prevents_too_fine_grain_for_large_ranges():
    rows = [{"status": "succeeded", "end_time": "2026-06-01T01:00:00+00:00"}]

    three_day_hour = _trend_context({"range": "3d", "grain": "hour"}, rows, ZoneInfo("Asia/Saigon"))
    assert three_day_hour["effective_grain"] == "hour"

    seven_day_hour = _trend_context({"range": "7d", "grain": "hour"}, rows, ZoneInfo("Asia/Saigon"))
    assert seven_day_hour["effective_grain"] == "day"

    thirty_day_hour = _trend_context({"range": "30d", "grain": "hour"}, rows, ZoneInfo("Asia/Saigon"))
    assert thirty_day_hour["effective_grain"] == "day"

    one_year_day = _trend_context(
        {"range": "custom", "startTime": "2025-01-01T00:00:00+00:00", "endTime": "2025-12-31T00:00:00+00:00", "grain": "day"},
        rows,
        ZoneInfo("Asia/Saigon"),
    )
    assert one_year_day["effective_grain"] == "week"

    multi_year_auto = _trend_context(
        {"range": "custom", "startTime": "2024-01-01T00:00:00+00:00", "endTime": "2026-01-01T00:00:00+00:00", "grain": "auto"},
        rows,
        ZoneInfo("Asia/Saigon"),
    )
    assert multi_year_auto["effective_grain"] == "month"

def test_filter_rows_supports_90d_range():
    recent = datetime.now(timezone.utc) - timedelta(days=89)
    older = datetime.now(timezone.utc) - timedelta(days=91)
    rows = [
        {"status": "succeeded", "end_time": recent.isoformat()},
        {"status": "succeeded", "end_time": older.isoformat()},
    ]
    filtered = filter_log_rows(rows, {"range": "90d"}, include_dataflow_filters=False)
    assert len(filtered) == 1
    assert filtered[0]["end_time"] == recent.isoformat()
