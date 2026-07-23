from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
from zoneinfo import ZoneInfo

from benchmarks.monitoring_fixture import build_analytics_fixture
from datacoolie_studio.core.time import parse_utc_datetime
from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.logs import cache as logs_cache
from datacoolie_studio.domains.logs.reader import (
    discover_dataflow_parquet_files,
    discover_job_jsonl_files,
    read_dataflow_logs,
    read_job_logs,
    read_system_log_file,
)
from datacoolie_studio.domains.monitoring.service import (
    _attention_queue,
    _dataflow_operation_type,
    _diagnostics_field_completeness,
    _diagnostics_job_linkage_summary,
    _diagnostics_page,
    _destination_operation_type,
    _enrich_dataflow_run_for_investigation,
    _error_category,
    _failures_page,
    _filter_log_rows,
    _freshness_page,
    _health_page,
    _is_lakehouse_destination,
    _job_key,
    _job_shape_label,
    _job_runs_by_dataflow_operation_type,
    _maintenance_upstream_dataflows,
    _normalize_monitoring_filters_for_timezone,
    _operation_windows,
    _operations_page,
    _performance_page,
    _phase_duration,
    _phase_duration_summary,
    _status_by_date,
    _time_value,
    _trend_context,
    _volume_page,
    _watermark_classification,
    dataflow_logs,
    job_logs,
)
from datacoolie_studio.domains.monitoring.page_service import monitoring_page, public_monitoring_page


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_LOGS = ROOT / "datacoolie" / "usecase-sim" / "logs" / "etl_logs" / "analyst"


class PathRecord:
    def __init__(self, uri: str, enabled: bool = True) -> None:
        self.uri = uri
        self.enabled = enabled


def test_discovers_usecase_parquet_logs():
    files = discover_dataflow_parquet_files(str(SAMPLE_LOGS))
    assert files
    assert all(path.endswith(".parquet") for path in files)


def test_reads_usecase_dataflow_logs():
    rows, errors = read_dataflow_logs([str(SAMPLE_LOGS)], limit=10)
    assert not errors
    assert rows
    assert {"dataflow_name", "stage", "status", "duration_seconds"} <= set(rows[0])


def test_reads_usecase_job_logs():
    files = discover_job_jsonl_files(str(SAMPLE_LOGS))
    assert files
    rows, errors = read_job_logs([str(SAMPLE_LOGS)], limit=10)
    assert not errors
    assert rows
    assert {"job_id", "engine_name", "metadata_provider_name", "status"} <= set(rows[0])


def test_maintenance_upstream_dataflows_preserves_full_aggregate():
    rows = [
        {
            "dataflow_id": "orders",
            "dataflow_name": "Orders",
            "status": "succeeded",
            "end_time": "2026-07-16T10:00:00Z",
            "source_name": "crm",
            "source_table": "orders_raw",
            "destination_load_type": "merge",
            "source_rows_read": 10,
        },
        {
            "dataflow_id": "orders",
            "dataflow_name": "Orders",
            "status": "failed",
            "end_time": "2026-07-16T11:00:00Z",
            "source_name": "crm",
            "source_table": "orders_raw",
            "destination_load_type": "merge",
            "source_rows_read": 5,
        },
    ]

    result = _maintenance_upstream_dataflows(rows)

    assert result == [{
        "dataflow_id": "orders",
        "dataflow_name": "Orders",
        "stage": "unknown",
        "operation_type": "unknown",
        "source": "crm · orders_raw",
        "load_type": "merge",
        "latest_status": "failed",
        "latest_time": "2026-07-16T11:00:00Z",
        "run_count": 2,
        "rows_read": 15.0,
    }]


def test_system_log_scope_defaults_to_job_only_and_can_include_dataflows(tmp_path: Path):
    log_file = tmp_path / "system.jsonl"
    log_file.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {"level": "INFO", "msg": "job missing id"},
                {"level": "INFO", "dataflow_id": None, "msg": "job null id"},
                {"level": "INFO", "dataflow_id": "", "msg": "job empty id"},
                {"level": "INFO", "dataflow_id": "df-orders", "msg": "child"},
            ]
        ),
        encoding="utf-8",
    )

    job_rows, job_total, errors = read_system_log_file(str(log_file), job_id="job-1")
    assert not errors
    assert job_total == 3
    assert [row["msg"] for row in job_rows] == ["job missing id", "job null id", "job empty id"]

    all_rows, all_total, errors = read_system_log_file(
        str(log_file),
        job_id="job-1",
        include_dataflow_logs=True,
    )
    assert not errors
    assert all_total == 4
    assert len(all_rows) == 4

    dataflow_rows, dataflow_total, errors = read_system_log_file(
        str(log_file),
        job_id="job-1",
        dataflow_id="df-orders",
    )
    assert not errors
    assert dataflow_total == 1
    assert [row["msg"] for row in dataflow_rows] == ["child"]


def test_monitoring_performance_page_uses_dataflow_logs(tmp_path: Path, monkeypatch):
    paths = _published_monitoring_paths(tmp_path, monkeypatch)
    performance = monitoring_page(paths, page="performance")
    assert performance["summary"]["dataflow_records"] > 0
    assert performance["performance"]["duration_distribution_by_stage"]


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
    assert "job_success_rate" in report["metric_definitions"]


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
        assert projected["schema_version"] == "monitoring-page.v9"
        assert projected["page"] == page
        assert set(projected) == {"schema_version", "page", "summary", *sections}
        assert "metric_definitions" not in projected

    performance = public_monitoring_page("performance", report)
    assert "investigation_queue" not in performance["performance"]


def _published_monitoring_paths(tmp_path: Path, monkeypatch) -> list[EnvironmentSource]:
    analytics_path = tmp_path / "monitoring.duckdb"
    monkeypatch.setattr(logs_cache, "analytics_database_path", lambda: analytics_path)
    build_analytics_fixture(analytics_path, source_ids=[1], dataflow_rows=50)
    return [EnvironmentSource(
        id=1,
        environment_id=1,
        source_kind="logs",
        uri="benchmark://monitoring-tests/1",
        enabled=True,
    )]


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


def test_performance_page_builds_runtime_optimization_signals():
    rows = [
        {
            "job_id": "job-1",
            "dataflow_id": "df-small-slow",
            "dataflow_run_id": "run-1",
            "dataflow_name": "small_slow",
            "stage": "bronze",
            "operation_type": "etl",
            "status": "succeeded",
            "duration_seconds": 100,
            "source_duration_seconds": 10,
            "transform_duration_seconds": 5,
            "destination_duration_seconds": 5,
            "overhead_duration_seconds": 80,
            "source_rows_read": 1,
            "destination_rows_written": 1,
            "end_time": "2026-06-20T00:00:00+00:00",
        },
        {
            "job_id": "job-2",
            "dataflow_id": "df-phase-skew",
            "dataflow_run_id": "run-2",
            "dataflow_name": "phase_skew",
            "stage": "silver",
            "operation_type": "etl",
            "status": "succeeded",
            "duration_seconds": 80,
            "source_duration_seconds": 0,
            "transform_duration_seconds": 0,
            "destination_duration_seconds": 78,
            "source_rows_read": 10_000,
            "destination_rows_written": 0,
            "end_time": "2026-06-20T00:01:00+00:00",
        },
        {
            "job_id": "job-3",
            "dataflow_id": "df-fast",
            "dataflow_run_id": "run-3",
            "dataflow_name": "fast",
            "stage": "bronze",
            "operation_type": "etl",
            "status": "succeeded",
            "duration_seconds": 1,
            "source_duration_seconds": 0.2,
            "transform_duration_seconds": 0.2,
            "destination_duration_seconds": 0.2,
            "source_rows_read": 10,
            "destination_rows_written": 10,
            "end_time": "2026-06-20T00:02:00+00:00",
        },
    ]

    page = _performance_page(rows)

    assert page["kpis"]["run_count"] == 3
    assert page["kpis"]["optimization_candidate_count"] >= 1
    assert page["kpis"]["high_overhead_count"] >= 1
    assert page["duration_distribution_by_stage"]
    assert page["phase_contribution_by_stage_operation"]
    assert page["workload_efficiency_points"]
    assert page["slowest_dataflow_profiles"]
    assert page["runtime_context_profiles"]
    assert page["performance_trend"]
    assert page["investigation_queue"][0]["performance_candidate_reason"]
    skew = next(row for row in page["investigation_queue"] if row["dataflow_id"] == "df-phase-skew")
    assert skew["overhead_duration_seconds"] == 2
    assert {row["dataflow_id"] for row in page["slowest_dataflow_profiles"]} == {"df-small-slow", "df-phase-skew", "df-fast"}


def test_performance_page_uses_executable_runs_and_operation_specific_workload_rules():
    rows = [
        {
            "dataflow_id": "etl-positive",
            "operation_type": "etl",
            "status": "succeeded",
            "duration_seconds": 10,
            "source_rows_read": 100,
            "destination_rows_written": 100,
            "source_duration_seconds": 2,
            "transform_duration_seconds": 2,
            "destination_duration_seconds": 4,
            "end_time": "2026-06-20T00:00:00+00:00",
        },
        {
            "dataflow_id": "etl-zero",
            "operation_type": "etl",
            "status": "succeeded",
            "duration_seconds": 20,
            "source_rows_read": 0,
            "destination_rows_written": 0,
            "source_duration_seconds": 4,
            "transform_duration_seconds": 4,
            "destination_duration_seconds": 8,
            "end_time": "2026-06-20T01:00:00+00:00",
        },
        {
            "dataflow_id": "maintenance-zero",
            "operation_type": "maintenance",
            "status": "succeeded",
            "duration_seconds": 80,
            "destination_duration_seconds": 80,
            "end_time": "2026-06-20T02:00:00+00:00",
        },
        {
            "dataflow_id": "maintenance-small",
            "operation_type": "maintenance",
            "status": "succeeded",
            "duration_seconds": 100,
            "destination_duration_seconds": 100,
            "destination_bytes_removed": 10,
            "destination_files_removed": 1,
            "end_time": "2026-06-20T03:00:00+00:00",
        },
        {
            "dataflow_id": "maintenance-large",
            "operation_type": "maintenance",
            "status": "succeeded",
            "duration_seconds": 5,
            "destination_duration_seconds": 5,
            "destination_bytes_removed": 1_000,
            "destination_files_removed": 20,
            "end_time": "2026-06-20T04:00:00+00:00",
        },
        {
            "dataflow_id": "skipped-with-duration",
            "operation_type": "etl",
            "status": "skipped",
            "duration_seconds": 999,
            "end_time": "2026-06-20T05:00:00+00:00",
        },
    ]

    page = _performance_page(rows)
    queue = {row["dataflow_id"]: row for row in page["investigation_queue"]}

    assert page["kpis"]["run_count"] == 5
    assert "skipped-with-duration" not in queue
    assert queue["etl-zero"]["performance_candidate_code"] != "slow_small_workload"
    assert queue["maintenance-zero"]["performance_candidate_code"] != "slow_small_workload"
    assert queue["maintenance-zero"]["performance_candidate_code"] != "slow_small_maintenance"
    assert queue["maintenance-small"]["performance_candidate_code"] == "slow_small_maintenance"
    assert "phase_skew" not in queue["maintenance-small"]["performance_candidate_codes"]
    assert {row["operation_type"] for row in page["workload_efficiency_points"]} == {"etl", "maintenance"}


def test_monitoring_job_and_dataflow_logs_include_investigation_fields():
    jobs = job_logs([PathRecord(str(SAMPLE_LOGS))], limit=5)
    dataflows = dataflow_logs([PathRecord(str(SAMPLE_LOGS))], limit=5)

    assert jobs["records"]
    assert dataflows["records"]
    assert {
        "child_dataflow_count",
        "child_failed_count",
        "reconciliation_status",
        "error_preview",
    } <= set(jobs["records"][0])
    assert {
        "source_display",
        "destination_display",
        "phase_health",
        "error_phase",
        "error_preview",
        "movement_state",
        "linked_job_status",
    } <= set(dataflows["records"][0])

    failed_dataflow = _enrich_dataflow_run_for_investigation({
        "dataflow_run_id": "run-1",
        "dataflow_name": "read_orders",
        "status": "failed",
        "source_status": "failed",
        "source_error_message": "Path does not exist: /landing/orders",
    })
    assert failed_dataflow["failure_kind"] == "dataflow"
    assert failed_dataflow["failure_phase"] == "source"
    assert failed_dataflow["failure_category"] == "Missing object"
    assert failed_dataflow["failure_message"] == "Path does not exist: /landing/orders"


def test_dataflow_phase_health_includes_overhead_and_preserves_failed_phase_precedence():
    overhead_dominant = _enrich_dataflow_run_for_investigation({
        "dataflow_run_id": "9ea65b5b-799f-49e6-9de4-c671e9c1d7a5",
        "status": "succeeded",
        "duration_seconds": 0.03766,
        "source_duration_seconds": 0.006128,
        "transform_duration_seconds": 0.004543,
        "destination_duration_seconds": 0.004084,
        "overhead_duration_seconds": 0.022905,
    })
    assert overhead_dominant["phase_health"] == "overhead_bottleneck"

    failed_source = _enrich_dataflow_run_for_investigation({
        **overhead_dominant,
        "status": "failed",
        "source_status": "failed",
        "source_error_message": "Source read failed",
    })
    assert failed_source["phase_health"] == "source_failed"


def test_phase_duration_summary_uses_completed_statuses_and_exact_total_metrics():
    rows = [
        {
            "status": "succeeded",
            "operation_type": "etl",
            "duration_seconds": 100,
            "source_duration_seconds": 20,
            "transform_duration_seconds": 30,
            "destination_duration_seconds": 40,
            "source_status": "succeeded",
            "transform_status": "succeeded",
            "destination_status": "succeeded",
        },
        {
            "status": "failed",
            "operation_type": "etl",
            "duration_seconds": 50,
            "source_duration_seconds": 10,
            "transform_duration_seconds": 10,
            "destination_duration_seconds": 10,
            "source_status": "failed",
        },
        {
            "status": "failed",
            "operation_type": "etl",
            "duration_seconds": 20,
            "source_duration_seconds": 5,
            "transform_duration_seconds": 5,
            "destination_duration_seconds": 5,
        },
        {
            "status": "skipped",
            "operation_type": "etl",
            "duration_seconds": 30,
            "source_duration_seconds": 10,
            "transform_duration_seconds": 10,
            "destination_duration_seconds": 0,
        },
        {
            "status": "succeeded",
            "operation_type": "etl",
            "source_status": "succeeded",
        },
        {
            "status": "pending",
            "operation_type": "etl",
            "duration_seconds": 200,
            "source_duration_seconds": 100,
            "transform_duration_seconds": 50,
            "destination_duration_seconds": 25,
        },
        {
            "status": "running",
            "operation_type": "etl",
            "duration_seconds": 200,
            "source_duration_seconds": 100,
            "transform_duration_seconds": 50,
            "destination_duration_seconds": 25,
        },
    ]

    summary = _phase_duration_summary(rows, "operation_type", _dataflow_operation_type)

    assert [row["operation_type"] for row in summary] == ["Total", "etl"]
    total = summary[0]
    assert total["is_total"] == 1
    assert total["source_duration_seconds"] == 45
    assert total["source_run_count"] == 4
    assert total["source_avg_duration_seconds"] == 11.25
    assert total["source_p95_duration_seconds"] == 20
    assert total["source_failed"] == 1
    assert total["overhead_duration_seconds"] == 45
    assert total["overhead_failed"] == 1


def test_attention_queue_includes_freshness_signals_and_existing_tabs():
    items = _attention_queue(
        rows=[{"dataflow_id": "d1", "status": "succeeded"}],
        jobs=[{"job_id": "j1", "status": "succeeded"}],
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
        rows=[{"dataflow_id": "d1", "status": "succeeded"}],
        jobs=[{"job_id": "j1", "status": "succeeded"}],
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
        rows=[{"dataflow_id": "d1", "status": "succeeded"}],
        jobs=[{"job_id": "j1", "status": "succeeded"}],
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
        rows=[],
        jobs=[],
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
        rows=[{"dataflow_id": "d1", "status": "running"}],
        jobs=[{"job_id": "j1", "status": "succeeded"}],
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
        rows=[{"dataflow_id": "d1", "status": "succeeded"}],
        jobs=[{"job_id": "j1", "status": "succeeded"}],
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
        maintenance={"kpis": {}},
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
        maintenance={"kpis": {}},
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
        maintenance={"kpis": {}},
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
        maintenance={"kpis": {}},
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
    filters = _normalize_monitoring_filters_for_timezone(
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
    filters = _normalize_monitoring_filters_for_timezone(
        {"range": "today"},
        timezone_info=pacific_kiritimati,
        now=reference_now,
    )

    rows = [
        {"status": "succeeded", "end_time": "2026-06-15T23:45:00+00:00"},
        {"status": "failed", "end_time": "2026-06-14T22:00:00+00:00"},
    ]
    filtered = _filter_log_rows(rows, filters, include_dataflow_filters=False)
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

    filtered = _filter_log_rows(rows, filters, include_dataflow_filters=False)

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
    assert _dataflow_operation_type({"operation_type": None}) == "unknown"
    assert _dataflow_operation_type({"operation_type": ""}) == "unknown"
    assert _destination_operation_type({"destination_operation_type": "not_available"}) == "unknown"

    result = _job_runs_by_dataflow_operation_type(
        [],
        [
            {"job_id": "j1", "status": "succeeded", "operation_types": None},
            {"job_id": "j2", "status": "failed", "operation_types": '["etl", "not_available"]'},
        ],
    )

    by_operation = {row["operation_type"]: row for row in result}
    assert by_operation["unknown"]["count"] == 2
    assert by_operation["unknown"]["succeeded"] == 1
    assert by_operation["unknown"]["failed"] == 1
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
    filtered = _filter_log_rows(rows, {"range": "90d"}, include_dataflow_filters=False)
    assert len(filtered) == 1
    assert filtered[0]["end_time"] == recent.isoformat()


def test_error_category_rules_prefer_clear_patterns_and_fallback_to_other():
    assert _error_category("required package 'connectorx' not found. pip install connectorx") == "Dependency"
    assert _error_category("OAuth2 token request failed: [Errno 111] Connection refused") == "Connectivity"
    assert _error_category("authentication failed: invalid token") == "Authentication"
    assert _error_category("table or view `orders` does not exist") == "Missing object"
    assert _error_category("DELTA_FAILED_TO_MERGE_FIELDS: Failed to merge fields 'a' and 'a'") == "Schema / format"
    assert _error_category("replay assertion validation failed") == "Data quality"
    assert _error_category("api_auth__oauth2; api_page__next_link; replay__orders;") == "Other"
    assert _error_category("none") == "Unspecified"


def test_failures_page_groups_by_signature_phase_and_endpoint():
    rows = [
        {
            "job_id": "job-1",
            "dataflow_id": "df-1",
            "dataflow_name": "read_orders",
            "status": "failed",
            "source_status": "failed",
            "stage": "bronze",
            "source_name": "api",
            "destination_name": "lake",
            "source_error_message": "Path does not exist: /landing/orders/2026-06-20",
            "end_time": "2026-06-20T00:00:00+00:00",
        },
        {
            "job_id": "job-2",
            "dataflow_id": "df-1",
            "dataflow_name": "read_orders",
            "status": "failed",
            "source_status": "failed",
            "stage": "bronze",
            "source_name": "api",
            "destination_name": "lake",
            "source_error_message": "Path does not exist: /landing/orders/2026-06-21",
            "end_time": "2026-06-21T00:00:00+00:00",
        },
        {
            "job_id": "job-3",
            "dataflow_id": "df-2",
            "dataflow_name": "write_customers",
            "status": "failed",
            "destination_status": "failed",
            "stage": "silver",
            "source_name": "db",
            "destination_name": "lake",
            "destination_error_message": "DELTA_FAILED_TO_MERGE_FIELDS: failed to merge fields",
            "end_time": "2026-06-21T01:00:00+00:00",
        },
    ]
    jobs = [
        {
            "job_id": "job-1",
            "status": "failed",
            "error_message": "read_orders;",
            "stages": '["read_file", "transform_schema", "load_delta"]',
            "operation_types": "etl",
            "end_time": "2026-06-20T00:01:00+00:00",
        },
        {
            "job_id": "job-4",
            "status": "failed",
            "error_message": "write_customers;",
            "stages": "silver",
            "operation_types": "etl",
            "end_time": "2026-06-21T01:01:00+00:00",
        },
        {
            "job_id": "job-5",
            "status": "failed",
            "error_message": "compact_customers;",
            "stages": "silver",
            "operation_types": "maintenance",
            "end_time": "2026-06-21T02:01:00+00:00",
        }
    ]

    failures = _failures_page(rows, jobs)

    assert failures["kpis"]["failed_dataflows"] == 3
    assert failures["kpis"]["failed_jobs"] == 3
    assert failures["kpis"]["affected_jobs"] == 3
    assert failures["kpis"]["affected_dataflow_jobs"] == 3
    assert failures["kpis"]["affected_job_contexts"] == 3
    assert failures["kpis"]["affected_stages"] == 3
    assert failures["kpis"]["affected_routes"] == 2
    assert failures["kpis"]["repeated_signatures"] == 1
    assert failures["kpis"]["unique_signatures"] == 2
    assert failures["kpis"]["repeated_failure_runs"] == 2
    assert failures["kpis"]["repeated_failure_share"] == 66.67
    assert failures["kpis"]["top_cause_runs"] == 2
    assert failures["kpis"]["top_cause_share"] == 66.67
    assert failures["latest_queue"][0]["failure_phase"] == "destination"
    assert failures["latest_queue"][0]["failure_category"] == "Schema / format"
    category_matrix = {row["category"]: row for row in failures["failure_category_phase_matrix"]}
    assert category_matrix["Missing object"]["source"] == 2
    assert category_matrix["Schema / format"]["destination"] == 1
    stage_matrix = {row["name"]: row for row in failures["failed_by_stage"]}
    assert stage_matrix["bronze"]["count"] == 2
    assert stage_matrix["bronze"]["source"] == 2
    assert stage_matrix["silver"]["destination"] == 1
    top_dataflows = {row["dataflow_name"]: row for row in failures["top_failing_dataflows"]}
    assert top_dataflows["read_orders"]["error_count"] == 2
    assert top_dataflows["read_orders"]["source"] == 2
    assert top_dataflows["write_customers"]["destination"] == 1
    trend_by_date = {row["date"]: row for row in failures["failure_trend_by_date"]}
    assert trend_by_date["2026-06-20"]["failed_jobs"] == 1
    assert trend_by_date["2026-06-20"]["failed_dataflows"] == 1
    assert trend_by_date["2026-06-21"]["failed_dataflows"] == 2

    repeated = failures["repeated_signatures"][0]
    assert repeated["failed_runs"] == 2
    assert repeated["failure_phase"] == "source"
    assert repeated["failure_category"] == "Missing object"
    assert repeated["affected_jobs"] == 2

    endpoint = failures["endpoint_impact"][0]
    assert endpoint["source_name"] == "api"
    assert endpoint["destination_name"] == "lake"
    assert endpoint["failed_runs"] == 2


def test_failure_phase_uses_overhead_when_only_dataflow_error_exists():
    failures = _failures_page(
        [
            {
                "job_id": "job-1",
                "dataflow_id": "df-1",
                "dataflow_name": "orchestrate_orders",
                "status": "failed",
                "stage": "bronze",
                "error_message": "Scheduler timeout while dispatching task",
                "end_time": "2026-06-20T00:00:00+00:00",
            }
        ],
        [
            {
                "job_id": "job-1",
                "status": "failed",
                "error_message": "orchestrate_orders;",
                "end_time": "2026-06-20T00:01:00+00:00",
            }
        ],
    )

    assert failures["latest_queue"][0]["failure_phase"] == "overhead"
    assert failures["latest_queue"][0]["failure_message"] == "Scheduler timeout while dispatching task"
    category_matrix = {row["category"]: row for row in failures["failure_category_phase_matrix"]}
    assert category_matrix["Timeout / throttling"]["overhead"] == 1
    assert failures["kpis"]["top_cause_share"] == 100


def test_failure_phase_does_not_infer_phase_from_top_level_error_message():
    examples = [
        "Failed to read source: table orders was not found",
        "Transformer pipeline failed: invalid expression",
        "Failed to write destination: permission denied",
        "A generic dataflow failure without phase evidence",
    ]

    for index, message in enumerate(examples):
        enriched = _enrich_dataflow_run_for_investigation({
            "dataflow_run_id": f"run-{index}",
            "status": "failed",
            "error_message": message,
        })
        assert enriched["failure_phase"] == "overhead"
        assert enriched["failure_message"] == message


def test_failure_phase_prefers_failed_status_and_its_specific_error():
    specific_error = _enrich_dataflow_run_for_investigation({
        "dataflow_run_id": "run-specific",
        "status": "failed",
        "source_status": "failed",
        "source_error_message": "Source path was not found",
        "error_message": "Failed to write destination: permission denied",
    })
    failed_status = _enrich_dataflow_run_for_investigation({
        "dataflow_run_id": "run-status",
        "status": "failed",
        "transform_status": "failed",
        "error_message": "Failed to write destination: permission denied",
    })

    assert specific_error["failure_phase"] == "source"
    assert specific_error["failure_message"] == "Source path was not found"
    assert failed_status["failure_phase"] == "transform"
    assert failed_status["failure_message"] == "Failed to write destination: permission denied"


def test_volume_page_exposes_workload_kpis_for_overview():
    rows = [
        {
            "dataflow_id": "df-1",
            "dataflow_name": "orders",
            "status": "succeeded",
            "end_time": "2026-06-16T00:00:00+00:00",
            "destination_rows_written": 10,
            "source_rows_read": 12,
            "destination_bytes_added": 300,
            "destination_bytes_removed": 50,
        },
        {
            "dataflow_id": "df-1",
            "dataflow_name": "orders",
            "status": "skipped",
            "end_time": "2026-06-16T01:00:00+00:00",
            "destination_rows_written": 0,
            "source_rows_read": 0,
            "destination_bytes_added": 0,
            "destination_bytes_removed": 20,
        },
    ]
    jobs = [{"total_rows_read": 20, "total_rows_written": 10}]

    volume = _volume_page(rows, jobs)

    assert volume["kpis"]["total_bytes_added"] == 300
    assert volume["kpis"]["total_bytes_removed"] == 70
    assert volume["kpis"]["net_bytes_change"] == 230
    assert volume["kpis"]["skip_count"] == 1
    assert volume["kpis"]["skip_rate"] == 50
    assert volume["top_dataflows_by_bytes_added"]
    assert len(volume["dataflow_registry"]) == 1
    registry_row = volume["dataflow_registry"][0]
    assert registry_row["run_count"] == 2
    assert registry_row["volume_rows_read"] == 12
    assert registry_row["volume_est_rows_written"] == 12
    assert registry_row["volume_net_bytes"] == 230


def test_lakehouse_destination_uses_explicit_metadata_before_path_fallback():
    assert not _is_lakehouse_destination({
        "destination_connection_type": "file",
        "destination_format": "parquet",
        "destination_path": "./output/parquet/orders_read_delta",
    })
    assert _is_lakehouse_destination({
        "destination_connection_type": "lakehouse",
        "destination_format": "delta",
        "destination_path": "./output/orders",
    })
    assert _is_lakehouse_destination({
        "destination_connection_type": "unknown",
        "destination_format": None,
        "destination_path": "./output/delta/orders",
    })


def test_volume_registry_aggregates_by_dataflow_and_preserves_candidate_evidence():
    rows = [
        {
            "dataflow_id": "df-orders",
            "dataflow_name": "orders",
            "status": "succeeded",
            "end_time": "2026-06-16T00:00:00+00:00",
            "source_rows_read": 100,
            "destination_rows_written": 80,
            "destination_files_added": 2,
            "destination_bytes_added": 500,
        },
        {
            "dataflow_id": "df-orders",
            "dataflow_name": "orders",
            "status": "succeeded",
            "end_time": "2026-06-16T01:00:00+00:00",
            "source_rows_read": 200,
            "destination_rows_written": 160,
            "destination_files_removed": 1,
            "destination_bytes_removed": 100,
        },
        {
            "dataflow_id": "df-small",
            "dataflow_name": "small",
            "status": "succeeded",
            "end_time": "2026-06-16T02:00:00+00:00",
            "source_rows_read": 1,
            "destination_rows_written": 1,
        },
    ]

    volume = _volume_page(rows, [])

    assert len(volume["dataflow_registry"]) == 2
    orders = next(row for row in volume["dataflow_registry"] if row["dataflow_id"] == "df-orders")
    assert orders["run_count"] == 2
    assert orders["volume_rows_read"] == 300
    assert orders["volume_lakehouse_rows_written"] == 240
    assert orders["volume_files_changed"] == 3
    assert orders["volume_net_bytes"] == 400
    assert orders["peak_rows_read"] == 200
    assert orders["p95_rows_read"] > 0
    assert orders["volume_candidate_signals"]
    assert volume["kpis"]["high_volume_dataflow_count"] == 1
    assert volume["kpis"]["high_volume_candidate_run_count"] > 0
    assert "investigation_queue" not in volume


def test_volume_registry_handles_empty_population():
    volume = _volume_page([], [])

    assert volume["dataflow_registry"] == []
    assert volume["kpis"]["high_volume_dataflow_count"] == 0
    assert volume["kpis"]["high_volume_candidate_run_count"] == 0


def test_freshness_page_tracks_watermark_movement_and_skipped_patterns():
    rows = [
        {
            "dataflow_id": "d1",
            "dataflow_name": "orders",
            "operation_type": "etl",
            "status": "succeeded",
            "end_time": "2026-06-14T00:00:00+00:00",
            "destination_full_table": "lake.sales.orders",
            "destination_load_type": "merge",
            "stage": "silver",
            "source_watermark_before": '{"updated_at":"2026-06-13T00:00:00Z"}',
            "source_watermark_after": '{"updated_at":"2026-06-14T00:00:00Z"}',
            "source_watermark_effective": '{"updated_at":"2026-06-14T00:00:00Z"}',
        },
        {
            "dataflow_id": "d3",
            "dataflow_name": "payments",
            "operation_type": "etl",
            "status": "succeeded",
            "end_time": "2026-06-14T02:00:00+00:00",
            "destination_full_table": "lake.sales.payments",
            "destination_load_type": "append",
            "stage": "silver",
        },
        {
            "dataflow_id": "d4",
            "dataflow_name": "products",
            "operation_type": "etl",
            "status": "succeeded",
            "end_time": "2026-06-14T03:00:00+00:00",
            "destination_full_table": "lake.sales.products",
            "destination_load_type": "append",
            "stage": "bronze",
            "source_watermark_after": '{"updated_at":"2026-06-14T03:00:00Z"}',
        },
        {
            "dataflow_id": "d5",
            "dataflow_name": "adjusted_orders",
            "operation_type": "etl",
            "status": "succeeded",
            "end_time": "2026-06-14T04:00:00+00:00",
            "destination_full_table": "lake.sales.adjusted_orders",
            "destination_load_type": "merge",
            "stage": "silver",
            "source_watermark_before": '{"updated_at":"2026-06-14T00:00:00Z"}',
            "source_watermark_after": '{"updated_at":"2026-06-14T00:00:00Z"}',
            "source_watermark_effective": '{"updated_at":"2026-06-13T00:00:00Z"}',
        },
        {
            "dataflow_id": "d6",
            "dataflow_name": "orders_copy",
            "operation_type": "etl",
            "status": "succeeded",
            "end_time": "2026-06-14T05:00:00+00:00",
            "destination_full_table": "lake.sales.orders",
            "destination_load_type": "append",
            "stage": "silver",
        },
        {
            "dataflow_id": "d7",
            "dataflow_name": "inventory",
            "operation_type": "etl",
            "status": "succeeded",
            "end_time": "2026-06-14T06:00:00+00:00",
            "destination_full_table": "lake.sales.inventory",
            "destination_load_type": "merge",
            "stage": "silver",
            "source_watermark_before": '{"updated_at":"2026-06-14T00:00:00Z"}',
        },
        {
            "dataflow_id": "d8",
            "dataflow_name": "api_no_new_watermark",
            "operation_type": "etl",
            "status": "skipped",
            "end_time": "2026-06-14T07:00:00+00:00",
            "destination_full_table": "lake.sales.api_orders",
            "destination_load_type": "merge",
            "stage": "bronze",
            "source_watermark_columns": '["updated_at"]',
            "source_watermark_before": '{"updated_at":"2026-06-14T00:00:00Z"}',
            "source_watermark_effective": '{"updated_at":"2026-06-14T00:00:00Z"}',
        },
        {
            "dataflow_id": "d2",
            "dataflow_name": "customers",
            "operation_type": "etl",
            "status": "skipped",
            "end_time": "2026-06-14T01:00:00+00:00",
            "destination_full_table": "lake.sales.customers",
            "stage": "bronze",
            "source_action": "no_new_data",
        },
        {
            "dataflow_id": "d2",
            "dataflow_name": "customers",
            "operation_type": "etl",
            "status": "skipped",
            "end_time": "2026-06-13T01:00:00+00:00",
            "destination_full_table": "lake.sales.customers",
            "stage": "bronze",
            "source_action": "no_new_data",
        },
        {
            "dataflow_id": "d2",
            "dataflow_name": "customers",
            "operation_type": "etl",
            "status": "skipped",
            "end_time": "2026-06-12T01:00:00+00:00",
            "destination_full_table": "lake.sales.customers",
            "stage": "bronze",
            "source_action": "no_new_data",
        },
    ]

    freshness = _freshness_page(rows)

    assert freshness["kpis"]["latest_successful_runs"] == 6
    assert freshness["kpis"]["successful_runs"] == 6
    assert freshness["kpis"]["failed_runs"] == 0
    assert freshness["kpis"]["skipped_runs"] == 4
    assert freshness["kpis"]["observed_dataflows"] == 8
    assert freshness["kpis"]["freshness_runs"] == 10
    assert freshness["kpis"]["dataflows_with_freshness_evidence"] == 8
    assert freshness["kpis"]["latest_status_issue_dataflows"] == 0
    assert freshness["kpis"]["latest_watermark_invalid_dataflows"] == 0
    assert freshness["kpis"]["latest_watermark_incomplete_dataflows"] == 1
    assert freshness["kpis"]["latest_watermark_issue_dataflows"] == 1
    assert freshness["kpis"]["watermark_enabled_dataflows"] == 5
    assert freshness["kpis"]["watermark_advanced_runs"] == 1
    assert freshness["kpis"]["watermark_initialized_runs"] == 1
    assert freshness["kpis"]["watermark_unchanged_runs"] == 2
    assert freshness["kpis"]["watermark_incomplete_runs"] == 1
    assert freshness["kpis"]["watermark_adjusted_runs"] == 2
    assert freshness["kpis"]["watermark_advanced_rate"] == 33.33
    assert freshness["kpis"]["skipped_no_new_data"] == 4
    assert freshness["kpis"]["skipped_streak_threshold"] == 3
    assert freshness["kpis"]["skipped_streak_dataflows"] == 1
    assert freshness["watermark_movement"][0]["movement"] == "advanced"
    assert freshness["watermark_movement"][1]["movement"] == "initialized"
    assert freshness["watermark_movement_by_date"][0]["advanced"] == 1
    assert freshness["watermark_movement_by_date"][0]["initialized"] == 1
    assert freshness["watermark_movement_by_date"][0]["unchanged"] == 2
    assert freshness["watermark_movement_by_date"][0]["adjusted"] == 2
    assert freshness["watermark_coverage_by_stage"][0]["missing"] >= 1
    assert freshness["watermark_coverage_by_stage"][0]["not_configured"] >= 1
    assert {row["dataflow_name"] for row in freshness["latest_freshness_by_dataflow"]} == {"orders", "orders_copy", "payments", "products", "adjusted_orders", "inventory", "customers", "api_no_new_watermark"}
    customers_freshness = next(row for row in freshness["latest_freshness_by_dataflow"] if row["dataflow_name"] == "customers")
    assert customers_freshness["latest_freshness_status"] == "skipped"
    assert freshness["skipped_patterns"][0]["consecutive_skipped"] == 3
    assert freshness["skipped_streak_distribution"][0]["dataflows"] == 1
    assert len(freshness["dataflow_registry"]) == 8
    assert next(row for row in freshness["dataflow_registry"] if row["dataflow_name"] == "customers")["skipped_streak"] == 3
    assert next(row for row in freshness["dataflow_registry"] if row["dataflow_name"] == "customers")["latest_freshness_status"] == "skipped"
    assert next(row for row in freshness["dataflow_registry"] if row["dataflow_name"] == "products")["movement_state"] == "initialized"
    adjusted = next(row for row in freshness["dataflow_registry"] if row["dataflow_name"] == "adjusted_orders")
    assert adjusted["movement_state"] == "unchanged"
    assert adjusted["adjustment_state"] == "adjusted"
    no_new_watermark = next(row for row in freshness["dataflow_registry"] if row["dataflow_name"] == "api_no_new_watermark")
    assert no_new_watermark["movement_state"] == "unchanged"
    assert no_new_watermark["adjustment_state"] == "not_adjusted"
    assert next(row for row in freshness["dataflow_registry"] if row["dataflow_name"] == "inventory")["movement_state"] == "incomplete"
    assert {"advanced", "initialized"}.issubset({row["movement_state"] for row in freshness["dataflow_registry"]})


def test_freshness_dataflow_identity_uses_dataflow_id_only():
    rows = [
        {
            "dataflow_id": "d1",
            "dataflow_name": "orders_to_lake",
            "operation_type": "etl",
            "status": "succeeded",
            "end_time": "2026-06-14T00:00:00+00:00",
            "destination_full_table": "lake.sales.orders",
            "source_watermark_after": '{"updated_at":"2026-06-14T00:00:00Z"}',
        },
        {
            "dataflow_id": "d2",
            "dataflow_name": "orders_copy_to_lake",
            "operation_type": "etl",
            "status": "succeeded",
            "end_time": "2026-06-14T01:00:00+00:00",
            "destination_full_table": "lake.sales.orders",
            "source_watermark_after": '{"updated_at":"2026-06-14T01:00:00Z"}',
        },
        {
            "dataflow_name": "missing_id_should_not_group",
            "operation_type": "etl",
            "status": "succeeded",
            "end_time": "2026-06-14T02:00:00+00:00",
            "destination_full_table": "lake.sales.orders",
            "source_watermark_after": '{"updated_at":"2026-06-14T02:00:00Z"}',
        },
    ]

    freshness = _freshness_page(rows)

    assert freshness["kpis"]["observed_dataflows"] == 2
    assert freshness["kpis"]["missing_dataflow_id_runs"] == 1
    assert freshness["kpis"]["watermark_enabled_dataflows"] == 2
    assert {row["dataflow_id"] for row in freshness["dataflow_registry"]} == {"d1", "d2"}
    assert all("dataflow_key" not in row for row in freshness["latest_freshness_by_dataflow"])
    assert all("dataflow_key" not in row for row in freshness["watermark_movement"])
    assert all("dataflow_key" not in row for row in freshness["dataflow_registry"])


def test_job_key_is_stable_for_normalized_job_shape():
    left = {
        "job_id": "run-1",
        "operation_types": '["maintenance", "etl"]',
        "stages": '["bronze", "silver"]',
    }
    same_shape = {
        "job_id": "run-2",
        "operation_types": "etl, maintenance",
        "stages": '["bronze", "silver"]',
    }
    different_shape = {
        "job_id": "run-3",
        "operation_types": "etl",
        "stages": '["bronze"]',
    }

    assert _job_key(left) == _job_key(same_shape)
    assert _job_key(left) != _job_key(different_shape)
    assert _job_shape_label(left) == "etl, maintenance | bronze, silver"


def test_watermark_movement_status_overrides_runtime_values():
    skipped = _watermark_classification({
        "status": "skipped",
        "source_watermark_before": '{"modified_at": "2024-02-01T09:30:00"}',
        "source_watermark_effective": '{"modified_at": "2024-02-01T09:30:00"}',
        "source_watermark_after": '{"modified_at": null}',
    })
    failed = _watermark_classification({
        "status": "failed",
        "source_watermark_before": '{"modified_at": "2024-02-01T09:30:00"}',
        "source_watermark_after": '{"modified_at": "2024-02-02T09:30:00"}',
    })
    pending = _watermark_classification({
        "status": "pending",
        "source_watermark_before": '{"modified_at": "2024-02-01T09:30:00"}',
        "source_watermark_after": '{"modified_at": "2024-02-02T09:30:00"}',
    })
    running = _watermark_classification({
        "status": "running",
        "source_watermark_before": '{"modified_at": "2024-02-01T09:30:00"}',
        "source_watermark_after": '{"modified_at": "2024-02-02T09:30:00"}',
    })

    assert skipped["movement_state"] == "unchanged"
    assert failed["movement_state"] == "unchanged"
    assert pending["movement_state"] == "incomplete"
    assert running["movement_state"] == "incomplete"


def test_freshness_registry_keeps_actual_skipped_streak_below_alert_threshold():
    rows = [
        {"dataflow_id": "d1", "dataflow_name": "orders", "operation_type": "etl", "status": "succeeded", "end_time": "2026-06-12T00:00:00+00:00"},
        {"dataflow_id": "d1", "dataflow_name": "orders", "operation_type": "etl", "status": "skipped", "end_time": "2026-06-13T00:00:00+00:00"},
        {"dataflow_id": "d1", "dataflow_name": "orders", "operation_type": "etl", "status": "skipped", "end_time": "2026-06-14T00:00:00+00:00"},
    ]

    freshness = _freshness_page(rows)
    registry_row = next(row for row in freshness["dataflow_registry"] if row["dataflow_name"] == "orders")

    assert registry_row["skipped_streak"] == 2
    assert freshness["kpis"]["skipped_streak_dataflows"] == 0
    assert freshness["skipped_patterns"] == []


def test_parse_utc_datetime_normalizes_offsets_and_naive_values():
    assert parse_utc_datetime("2026-06-13T14:00:00+07:00").isoformat() == "2026-06-13T07:00:00+00:00"
    assert parse_utc_datetime("2026-06-13T07:00:00Z").isoformat() == "2026-06-13T07:00:00+00:00"
    assert parse_utc_datetime("2026-06-13T07:00:00").isoformat() == "2026-06-13T07:00:00+00:00"


def test_legacy_duckdb_cache_is_replaced_by_typed_candidate_after_reader_drain(tmp_path: Path, monkeypatch):
    import duckdb

    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setattr(logs_cache, "analytics_database_path", lambda: analytics_path)
    with duckdb.connect(str(analytics_path)) as connection:
        connection.execute("CREATE TABLE etl_dataflow_run_cache (source_id INTEGER, file_uri VARCHAR, row_json VARCHAR)")
        connection.execute("CREATE TABLE etl_job_run_cache (source_id INTEGER, file_uri VARCHAR, row_json VARCHAR)")

    dataflows, jobs = logs_cache._read_duckdb_rows([1])
    assert dataflows == []
    assert jobs == []

    reader = logs_cache.analytics_connections.connect(analytics_path, read_only=True)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                logs_cache._upsert_duckdb_rows,
                1,
                [],
                [
                    (
                        "job.jsonl",
                        "job_jsonl",
                        "{}",
                        {
                            "job_id": "job-1",
                            "status": "succeeded",
                            "engine_name": "polars",
                            "end_time": "2026-06-13T00:01:00+00:00",
                        },
                    )
                ],
                [],
                ["job.jsonl"],
            )
            assert wait([future], timeout=0.2).not_done
            reader.close()
            assert future.result(timeout=5)["published"] is True
    finally:
        reader.close()

    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        tables = {row[0] for row in connection.execute("show tables").fetchall()}
        assert {"etl_dataflow_runs", "etl_job_runs", "etl_monitoring_filter_values"} <= tables
        assert "etl_dataflow_run_cache" not in tables
        assert "etl_job_run_cache" not in tables
        assert len(connection.execute("PRAGMA table_info('etl_dataflow_runs')").fetchall()) > 3
        assert connection.execute("select count(*) from etl_dataflow_runs").fetchone()[0] == 0
        assert connection.execute("select count(*) from etl_job_runs").fetchone()[0] == 1


def test_failed_analytics_publish_preserves_previous_generation(tmp_path: Path, monkeypatch):
    import duckdb

    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setattr(logs_cache, "analytics_database_path", lambda: analytics_path)
    first = logs_cache._upsert_duckdb_rows(
        7,
        [],
        [
            (
                "job.jsonl",
                "job_jsonl",
                "{}",
                {"job_id": "job-1", "status": "succeeded", "end_time": "2026-07-01T00:00:00Z"},
            )
        ],
        [],
        ["job.jsonl"],
    )
    assert first["published"] is True

    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        before = connection.execute(
            "select m.generation, s.generation, (select count(*) from etl_job_runs) "
            "from etl_analytics_meta m cross join etl_cache_sources s "
            "where m.singleton_id = 1 and s.source_id = 7"
        ).fetchone()

    def fail_insert(*_args, **_kwargs):
        raise RuntimeError("invalid parquet")

    monkeypatch.setattr(logs_cache, "_insert_dataflow_file", fail_insert)
    failed = logs_cache._upsert_duckdb_rows(
        7,
        [("bad.parquet", "dataflow_parquet", "{}")],
        [],
        [],
        ["bad.parquet"],
    )
    assert failed["published"] is False
    assert failed["errors"]

    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        after = connection.execute(
            "select m.generation, s.generation, (select count(*) from etl_job_runs) "
            "from etl_analytics_meta m cross join etl_cache_sources s "
            "where m.singleton_id = 1 and s.source_id = 7"
        ).fetchone()
    assert after == before


def test_cached_monitoring_summary_aggregates_overview_window_in_duckdb(tmp_path: Path, monkeypatch):
    import duckdb

    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setattr(logs_cache, "analytics_database_path", lambda: analytics_path)

    with duckdb.connect(str(analytics_path)) as connection:
        logs_cache._ensure_duckdb_tables(connection)
        logs_cache._ensure_typed_table(
            connection,
            analytics_schema.DATAFLOW_TABLE,
            logs_cache.DATAFLOW_COLUMN_TYPES,
        )
        logs_cache._ensure_typed_table(
            connection,
            analytics_schema.JOB_TABLE,
            logs_cache.JOB_COLUMN_TYPES,
        )
        logs_cache._insert_typed_rows(
            connection,
            analytics_schema.DATAFLOW_TABLE,
            7,
            [
                ("outside.parquet", "dataflow_parquet", "{}", {"status": "failed", "end_time": "2026-06-10T02:59:59Z"}),
                ("success.parquet", "dataflow_parquet", "{}", {"status": "succeeded", "end_time": "2026-07-01T22:00:00Z"}),
                ("failed.parquet", "dataflow_parquet", "{}", {"status": "failed", "end_time": "2026-07-02T01:00:00Z"}),
            ],
            logs_cache.DATAFLOW_COLUMN_TYPES,
        )
        logs_cache._insert_typed_rows(
            connection,
            analytics_schema.JOB_TABLE,
            7,
            [
                ("last7.jsonl", "job_jsonl", "{}", {"status": "failed", "engine_name": "polars", "end_time": "2026-07-02T20:00:00Z"}),
                ("last30.jsonl", "job_jsonl", "{}", {"status": "failed", "engine_name": "spark", "end_time": "2026-06-11T20:00:00Z"}),
                ("future.jsonl", "job_jsonl", "{}", {"status": "failed", "engine_name": "", "end_time": "2026-07-11T00:00:00Z"}),
                ("success.jsonl", "job_jsonl", "{}", {"status": "succeeded", "engine_name": "polars", "end_time": "2026-07-10T00:00:00Z"}),
                ("fallback.jsonl", "job_jsonl", "{}", {"status": "succeeded", "engine_name": "spark", "end_time": "not-a-timestamp", "start_time": "2026-07-09T00:00:00Z", "__run_date": "2026-07-09"}),
            ],
            logs_cache.JOB_COLUMN_TYPES,
        )
        logs_cache._mark_cache_source(connection, 7)
        logs_cache._publish_analytics_generation(connection)

    def reject_row_materialization(*_args, **_kwargs):
        raise AssertionError("Overview aggregate must not materialize cached Monitoring rows")

    monkeypatch.setattr(logs_cache, "_read_duckdb_rows", reject_row_materialization)
    source = PathRecord(str(tmp_path))
    source.id = 7
    cached = logs_cache.cached_monitoring_summary(
        object(),
        [source],
        cutoff=datetime(2026, 6, 10, 3, 0, tzinfo=timezone.utc),
        timezone_name="Asia/Saigon",
        utc_offset_seconds=None,
        local_today=datetime(2026, 7, 10, tzinfo=timezone.utc).date(),
    )

    assert cached is not None
    summary, errors = cached
    assert errors == []
    assert summary["dataflow_records"] == 2
    assert summary["dataflow_succeeded"] == 1
    assert summary["dataflow_failed"] == 1
    assert summary["job_records"] == 5
    assert summary["total_failures"] == 3
    assert summary["active_engines"] == 2
    assert summary["failed_last7"] == 1
    assert summary["failed_last30"] == 2
    assert summary["failed_last365"] == 2
    assert parse_utc_datetime(summary["latest_log_at"]).isoformat() == "2026-07-11T00:00:00+00:00"
    assert str(summary["date_min"]) == "2026-06-11"
    assert str(summary["date_max"]) == "2026-07-11"


def test_latest_dataflow_query_has_no_global_row_cap(tmp_path: Path, monkeypatch):
    import duckdb

    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setattr(logs_cache, "analytics_database_path", lambda: analytics_path)
    recent_rows = [
        (
            f"recent-{index}.parquet",
            "dataflow_parquet",
            "{}",
            {
                "dataflow_id": "recent",
                "dataflow_name": "recent flow",
                "dataflow_run_id": f"run-{index:04d}",
                "status": "succeeded",
                "end_time": "2026-07-17T12:00:00Z",
            },
        )
        for index in range(1001)
    ]
    with duckdb.connect(str(analytics_path)) as connection:
        logs_cache._ensure_duckdb_tables(connection)
        logs_cache._ensure_typed_table(
            connection,
            analytics_schema.DATAFLOW_TABLE,
            logs_cache.DATAFLOW_COLUMN_TYPES,
        )
        logs_cache._insert_typed_rows(
            connection,
            analytics_schema.DATAFLOW_TABLE,
            7,
            [
                *recent_rows,
                (
                    "old.parquet",
                    "dataflow_parquet",
                    "{}",
                    {
                        "dataflow_id": "old",
                        "dataflow_name": "old flow",
                        "dataflow_run_id": "old-run",
                        "status": "failed",
                        "end_time": "2025-01-01T00:00:00Z",
                    },
                ),
            ],
            logs_cache.DATAFLOW_COLUMN_TYPES,
        )
        logs_cache._mark_cache_source(connection, 7)
        logs_cache._publish_analytics_generation(connection)

    source = PathRecord(str(tmp_path))
    source.id = 7
    result = logs_cache.query_cached_latest_dataflow_runs(object(), [source])

    assert result is not None
    rows, ambiguous_names, errors = result
    assert errors == []
    assert ambiguous_names == []
    assert {row["dataflow_id"] for row in rows} == {"recent", "old"}
    assert next(row for row in rows if row["dataflow_id"] == "recent")["dataflow_run_id"] == "run-1000"


def test_legacy_dataflow_cache_table_is_recreated_in_one_schema_pass(tmp_path: Path):
    import duckdb

    analytics_path = tmp_path / "analytics.duckdb"
    with duckdb.connect(str(analytics_path)) as connection:
        connection.execute(
            f"CREATE TABLE {analytics_schema.DATAFLOW_TABLE} (_raw_json VARCHAR)"
        )
        logs_cache._ensure_duckdb_tables(connection)
        columns = analytics_schema.table_columns(connection, analytics_schema.DATAFLOW_TABLE)

    assert "_raw_json" not in columns
    assert "_source_id" in columns


def test_duckdb_cache_preserves_job_timestamp_string_and_drops_raw_json(tmp_path: Path, monkeypatch):
    import duckdb

    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setattr(logs_cache, "analytics_database_path", lambda: analytics_path)
    raw_start = "2026-06-18T15:23:08.534826+07:00"
    job_row = {
        "job_id": "job-1",
        "status": "succeeded",
        "engine_name": "PolarsEngine",
        "metadata_provider_name": "file",
        "start_time": raw_start,
        "end_time": raw_start,
    }

    with duckdb.connect(str(analytics_path)) as connection:
        logs_cache._ensure_duckdb_tables(connection)
        logs_cache._insert_typed_rows(
            connection,
            analytics_schema.JOB_TABLE,
            1,
            [("job.jsonl", "job_jsonl", "{}", job_row)],
            logs_cache.JOB_COLUMN_TYPES,
        )

        columns = {str(row[1]): str(row[2]).upper() for row in connection.execute("PRAGMA table_info('etl_job_runs')").fetchall()}
        cached_start = connection.execute("SELECT start_time FROM etl_job_runs").fetchone()[0]

    assert "_raw_json" not in columns
    assert "operation_type" not in columns
    assert columns["start_time"] == "VARCHAR"
    assert cached_start == raw_start


def test_duckdb_cache_uses_parquet_schema_for_dataflow_timestamps(tmp_path: Path, monkeypatch):
    import duckdb

    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setattr(logs_cache, "analytics_database_path", lambda: analytics_path)
    file_uri = discover_dataflow_parquet_files(str(SAMPLE_LOGS))[0]

    with duckdb.connect(str(analytics_path)) as connection:
        logs_cache._ensure_duckdb_tables(connection)
        logs_cache._insert_dataflow_file(connection, 1, file_uri, "dataflow_parquet", "{}")

        escaped = file_uri.replace("'", "''")
        source_types = {
            str(row[0]): _normalize_duckdb_type(str(row[1]))
            for row in connection.execute(f"DESCRIBE SELECT * FROM read_parquet('{escaped}', union_by_name=true)").fetchall()
        }
        cached_types = {
            str(row[1]): _normalize_duckdb_type(str(row[2]))
            for row in connection.execute("PRAGMA table_info('etl_dataflow_runs')").fetchall()
        }

    assert "_raw_json" not in cached_types
    assert cached_types["start_time"] == source_types["start_time"]
    assert cached_types["source_end_time"] == source_types["source_end_time"]


def test_duckdb_expected_column_order_keeps_source_columns_before_studio_columns():
    actual_columns = [
        "_source_id",
        "job_id",
        "new_raw_log_field",
        "operation_types",
        "_file_uri",
        "_ingested_at",
    ]
    source_column_types = {
        "job_id": "VARCHAR",
        "operation_types": "VARCHAR",
    }

    ordered = logs_cache._expected_column_order(actual_columns, source_column_types)

    assert ordered == [
        "job_id",
        "operation_types",
        "new_raw_log_field",
        "_source_id",
        "_file_uri",
        "_ingested_at",
    ]


def _normalize_duckdb_type(value: str) -> str:
    return value.upper().replace("TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE")
