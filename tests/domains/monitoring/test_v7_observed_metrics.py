"""Observed metrics and native phase evidence agree across Monitoring consumers."""
from datetime import timedelta
import json

import duckdb
import pytest

from datacoolie_studio.domains.analytics import contracts, store
from datacoolie_studio.domains.monitoring.log_repository import query_cached_job_logs
from datacoolie_studio.domains.monitoring.metrics.failure import dataflow_failed_phases
from datacoolie_studio.domains.monitoring.metrics.investigation import enrich_dataflow_run_for_investigation
from datacoolie_studio.domains.monitoring.read_models.maintenance import maintenance_read_model
from datacoolie_studio.domains.monitoring.read_models.jobs import jobs_read_model
from datacoolie_studio.domains.monitoring.read_models.performance import performance_read_model
from datacoolie_studio.domains.monitoring.read_models.volume import volume_read_model
from tests.domains.analytics.test_framework_v7_observed_metrics import STAMP, _entry, _publish
from tests.support.v7_fixture import v7_job_entry


def _duration(entry, seconds=10):
    entry["end_time"] = (STAMP + timedelta(seconds=seconds)).isoformat()
    entry["duration_seconds"] = seconds
    return entry


def test_throughput_duration_uses_measured_population_and_requires_measurements(tmp_path):
    known = _duration(_entry("known", count=100))
    excluded = _duration(_entry("excluded", entered=False))
    excluded["metrics"]["rows_read"] = None
    database = _publish(tmp_path, [known, excluded])
    with duckdb.connect(str(database), read_only=True) as conn:
        result = performance_read_model([], {}, grain="day", timezone_name="UTC",
                                         analytics_context=(conn, [7], "rate-test"))
    assert result["summary"]["total_rows_read"] == 100
    assert result["summary"]["rows_read_eligible"] == 2
    assert result["summary"]["rows_read_per_second"] == 10


def test_disabled_maintenance_preserves_effects_and_measured_rate(tmp_path):
    known = _duration(_entry("effect", operation="maintenance"))
    known["metrics"].update(files_removed=3, bytes_removed=100, bytes_saved=100)
    disabled = _duration(_entry("disabled", operation="maintenance"))
    database = _publish(tmp_path, [known, disabled])
    with duckdb.connect(str(database), read_only=True) as conn:
        result = maintenance_read_model([], {}, grain="day", timezone_name="UTC",
                                        analytics_context=(conn, [7], "maintenance-test"))
    assert result["summary"]["files_removed"] == 3
    assert result["summary"]["bytes_reclaimed"] == 100
    assert result["table_outcome"][0]["bytes_reclaimed_per_second"] == 10


def test_job_drawer_excludes_maintenance_rows_and_retains_physical_effects(tmp_path):
    etl = _entry("etl", count=100)
    etl["metrics"]["bytes_added"] = 10
    maintenance = _entry("maintenance", count=5, operation="maintenance")
    maintenance["metrics"]["bytes_added"] = 20
    database = _publish(tmp_path, [etl, maintenance])
    job = v7_job_entry(
        job_id="v7-job", total_dataflows=2, total_succeeded=2,
        total_rows_written=100, total_bytes_added=30,
    )
    path = tmp_path / "job.jsonl"
    path.write_text(json.dumps(job) + "\n", encoding="utf-8")
    descriptor = contracts.LogFileDescriptor(str(path), "job_json", "{}", str(path))
    store.publish_rows_batched(7, [contracts.LogPublishBatch(job_files=(descriptor,))], [],
                              [str(path)], database_path=database)
    with duckdb.connect(str(database), read_only=True) as conn:
        result = query_cached_job_logs(None, [], {}, analytics_context=(conn, [7], "job-test"))
    rows, total, _ = result
    assert total == 1
    assert rows[0]["child_dataflow_count"] == 2
    assert rows[0]["child_total_rows_written"] == 100
    assert rows[0]["child_total_bytes_added"] == 30
    assert rows[0]["child_metric_eligible_count"] == 1
    assert rows[0]["child_rows_written_measured_count"] == 1
    assert rows[0]["child_bytes_added_measured_count"] == 2


def test_v7_native_timing_and_preflight_failure_remain_native_evidence():
    entry = _duration(_entry("timed", count=100))
    attempt = entry["attempts"][0]
    attempt.update(start_time=STAMP.isoformat(), end_time=entry["end_time"], duration_seconds=10)
    for phase, start, duration in (("source", 0, 2), ("transform", 2, 3), ("destination", 5, 5)):
        attempt.setdefault(phase, {})
        if attempt[phase] is None:
            attempt[phase] = {}
        attempt[phase].update(start_time=(STAMP + timedelta(seconds=start)).isoformat(),
            end_time=(STAMP + timedelta(seconds=start + duration)).isoformat(),
            duration_seconds=duration, status="succeeded")
    assert enrich_dataflow_run_for_investigation(entry)["attempt_timing"]["available"] is True
    assert dataflow_failed_phases({"log_schema_version": 7, "status": "failed", "attempts": []}) == ("unknown",)


def test_unmeasured_non_lakehouse_subset_stays_null(tmp_path):
    database = _publish(tmp_path, [_entry("lakehouse", count=100)])
    with duckdb.connect(str(database), read_only=True) as conn:
        result = volume_read_model([], {}, grain="day", timezone_name="UTC",
                                   analytics_context=(conn, [7], "subset-test"))
    assert result["summary"]["total_lakehouse_rows_written"] == 100
    assert result["summary"]["total_non_lakehouse_rows_written"] is None


@pytest.mark.parametrize("duration, expected", [(None, None), (0, 15)])
def test_measured_rows_require_duration_and_preserve_observed_zero(tmp_path, duration, expected):
    known = _duration(_entry("known", count=100))
    other = _entry("other", count=50)
    other["metrics"]["rows_read"] = 50
    other["duration_seconds"] = duration
    database = _publish(tmp_path, [known, other])
    with duckdb.connect(str(database), read_only=True) as conn:
        result = performance_read_model([], {}, grain="day", timezone_name="UTC",
            analytics_context=(conn, [7], "missing-duration"))
    assert result["summary"]["total_rows_read"] == 150
    assert result["summary"]["rows_read_per_second"] == expected


@pytest.mark.parametrize("version, expected", [(6, 0), (7, None)])
def test_job_only_source_preserves_empty_population_mode(tmp_path, version, expected):
    from datetime import timezone
    from datacoolie_studio.domains.monitoring.page_dispatch import build_monitoring_page
    entry = v7_job_entry(job_id="job-only", status="succeeded")
    entry["log_schema_version"] = version
    path = tmp_path / "jobs.jsonl"
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    descriptor = contracts.LogFileDescriptor(str(path), "job_json", "{}", str(path))
    database = tmp_path / "analytics.duckdb"
    store.publish_rows_batched(7, [contracts.LogPublishBatch(job_files=(descriptor,))], [], [str(path)], database_path=database)
    with duckdb.connect(str(database), read_only=True) as conn:
        result = volume_read_model([], {}, grain="day", timezone_name="UTC",
            analytics_context=(conn, [7], "job-only"))
        page = build_monitoring_page([], "volume", filters={"range": "all"},
            timezone_info=timezone.utc, timezone_label="UTC",
            analytics_context=(conn, [7], "job-only"))
    assert result["summary"]["total_rows_written"] == expected
    assert page["volume"]["kpis"]["total_rows_written"] == expected


def test_mixed_source_mode_survives_empty_lakehouse_subset(tmp_path):
    from datetime import timezone
    from datacoolie_studio.domains.monitoring.page_dispatch import build_monitoring_page
    legacy = _entry("legacy", count=100)
    legacy["log_schema_version"] = 6
    current = _entry("current")
    for row in (legacy, current):
        row["destination_format"] = "csv"
        row["destination_connection_type"] = "file"
    database = _publish(tmp_path, [legacy, current])
    with duckdb.connect(str(database), read_only=True) as conn:
        page = build_monitoring_page([], "volume", filters={"range": "all"},
            timezone_info=timezone.utc, timezone_label="UTC",
            analytics_context=(conn, [7], "mixed-subset"))
    assert page["volume"]["kpis"]["total_rows_written"] == 100
    assert page["volume"]["kpis"]["total_lakehouse_rows_written"] is None


@pytest.mark.parametrize("measured, duration, expected_rows, expected_rate", [
    (None, 10, 100, 10),
    (50, None, 150, None),
])
def test_job_workload_rate_retains_values_and_uses_matching_duration(tmp_path, measured, duration, expected_rows, expected_rate):
    known = _duration(_entry("known", count=100))
    other = _duration(_entry("other"))
    other["metrics"]["rows_read"] = measured
    other["duration_seconds"] = duration
    database = _publish(tmp_path, [known, other])
    job = v7_job_entry(job_id="v7-job", total_dataflows=2)
    path = tmp_path / "jobs.jsonl"
    path.write_text(json.dumps(job) + "\n", encoding="utf-8")
    descriptor = contracts.LogFileDescriptor(str(path), "job_json", "{}", str(path))
    store.publish_rows_batched(7, [contracts.LogPublishBatch(job_files=(descriptor,))], [], [str(path)], database_path=database)
    with duckdb.connect(str(database), read_only=True) as conn:
        result = jobs_read_model([], {}, grain="day", timezone_name="UTC",
            analytics_context=(conn, [7], "job-rate"))
    point = result["workload_efficiency"][0]
    assert point["rows_read"] == expected_rows
    assert point["workload_size"] == expected_rate
