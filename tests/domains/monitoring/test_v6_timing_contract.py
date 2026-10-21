from __future__ import annotations

import copy

import pytest

from datacoolie_studio.domains.monitoring.metrics.v6_semantics import phase_timing
from datacoolie_studio.domains.monitoring.metrics.investigation import enrich_dataflow_run_for_investigation, is_lakehouse_destination
from datacoolie_studio.domains.monitoring.read_models.maintenance import maintenance_read_model
from datacoolie_studio.domains.analytics import schema
from tests.domains.monitoring.test_v6_freshness_contract import connection as native_connection
from datacoolie_studio.domains.analytics.serving_facts import rebuild_monitoring_serving_facts
from datacoolie_studio.domains.monitoring.read_models.performance import performance_read_model
from tests.domains.monitoring.test_v6_freshness_contract import connection
from tests.support.v6_records import native_record


def record():
    row = native_record({"dataflow_id": "timing-flow", "dataflow_run_id": "timing-run", "duration_seconds": 100})
    row["log_schema_version"] = 6.0
    attempt = row["attempts"][-1]
    attempt.update(start_time="2026-10-04T12:00:00Z", end_time="2026-10-04T12:00:10Z", duration_seconds=10)
    for phase, start, end in (("source", 0, 2), ("transform", 2, 4), ("destination", 4, 7)):
        attempt[phase].update(start_time=f"2026-10-04T12:00:{start:02d}Z", end_time=f"2026-10-04T12:00:{end:02d}Z", duration_seconds=end-start)
    return row


def test_final_attempt_overhead_uses_attempt_interval_and_numeric_v6(tmp_path):
    row = record()
    assert phase_timing(row)["available"] is True
    with connection(tmp_path, [row]) as conn:
        result = performance_read_model([], {}, grain="day", timezone_name="UTC", analytics_context=(conn, [1], "test"))
        phases = result["phase_contribution_by_stage_operation"][0]
        assert phases["overhead_duration_seconds"] == 3
        assert phases["total_duration_seconds"] == 10
        assert result["summary"]["avg_duration_seconds"] == 100
        assert result["summary"]["overhead_duration_percent"] == 30


@pytest.mark.parametrize("problem", ["overlap", "duration", "outside", "no_attempt"])
def test_inconsistent_timing_keeps_strict_diagnostics_separate_from_contribution(tmp_path, problem):
    row = copy.deepcopy(record())
    if problem == "overlap":
        row["attempts"][-1]["transform"]["start_time"] = "2026-10-04T12:00:01Z"
    elif problem == "duration":
        row["attempts"][-1]["source"]["duration_seconds"] = 90
    elif problem == "outside":
        row["attempts"][-1]["destination"]["end_time"] = "2026-10-04T12:00:20Z"
    else:
        row["attempts"] = []
    assert phase_timing(row)["available"] is False
    with connection(tmp_path, [row]) as conn:
        result = performance_read_model([], {}, grain="day", timezone_name="UTC", analytics_context=(conn, [1], "test"))
        phases = result["phase_contribution_by_stage_operation"]
        assert len(phases) == 2
        assert all(item["phase_runs_eligible"] == 1 for item in phases)
        if problem == "no_attempt":
            assert all(item["phase_runs_measured"] == 0 and item["total_duration_seconds"] is None for item in phases)
        else:
            expected = 95 if problem == "duration" else 10
            assert all(item["phase_runs_measured"] == 1 and item["total_duration_seconds"] == expected for item in phases)
        assert result["summary"]["overhead_duration_percent"] is None
        assert result["summary"]["bottleneck_phase"] == "unknown"
        assert result["summary"]["avg_duration_seconds"] == 100


def test_no_attempt_failure_preserves_root_message_without_overhead_phase(tmp_path):
    row = record()
    row.update(attempts=[], status="failed", message="preparation failed")
    with native_connection(tmp_path, [row]) as conn:
        projected = dict(zip([item[0] for item in conn.execute(f"SELECT * FROM {schema.DATAFLOW_TABLE}").description], conn.fetchone()))
        result = enrich_dataflow_run_for_investigation(projected)
        assert result["failure_phase"] == "unknown"
        assert result["failure_message"] == "preparation failed"
        assert result["timing_coverage"] == "no_attempt"


def test_declared_destination_identity_keeps_sources_and_null_values_separate(tmp_path):
    row = record()
    row["operation_type"] = "maintenance"
    with native_connection(tmp_path, [row]) as conn:
        conn.execute(f"INSERT INTO {schema.DATAFLOW_TABLE} SELECT * REPLACE (2 AS _source_id) FROM {schema.DATAFLOW_TABLE}")
        rebuild_monitoring_serving_facts(conn, dataflow_table=schema.DATAFLOW_TABLE, job_table=schema.JOB_TABLE, dataflow_column_types=schema.DATAFLOW_COLUMN_TYPES, job_column_types=schema.JOB_COLUMN_TYPES)
        result = maintenance_read_model([], {}, grain="day", timezone_name="UTC", analytics_context=(conn, [1, 2], "test"))
        assert len(result["table_outcome"]) == 2
        assert len({row["target"] for row in result["table_outcome"]}) == 2
    assert not is_lakehouse_destination({"destination_name": "delta", "destination_path": "/lakehouse/table"})
    assert is_lakehouse_destination({"destination_connection_type": "lakehouse"})


@pytest.mark.parametrize("page", ["freshness", "volume", "maintenance"])
def test_evidence_offset_beyond_last_page_retains_total(tmp_path, page):
    from datacoolie_studio.domains.monitoring.read_models.freshness import freshness_evidence_read_model
    from datacoolie_studio.domains.monitoring.read_models.volume import volume_evidence_read_model
    from datacoolie_studio.domains.monitoring.read_models.maintenance import maintenance_evidence_read_model
    row = record()
    row["operation_type"] = "maintenance" if page == "maintenance" else "etl"
    readers = {"freshness": freshness_evidence_read_model, "volume": volume_evidence_read_model, "maintenance": maintenance_evidence_read_model}
    with native_connection(tmp_path, [row]) as conn:
        result = readers[page]([], {}, limit=1, offset=100, sort_by="dataflow_id", sort_dir="asc", analytics_context=(conn, [1], "test"))
        assert result["records"] == []
        assert result["total_records"] == 1


@pytest.mark.parametrize("count", [9007199254740993, 2**80, 10**30])
def test_metrics_above_machine_integer_limits_remain_exact_through_page_wire(tmp_path, count):
    from datacoolie_studio.api.v1.contracts.monitoring_values import exact_metric_transport
    from datacoolie_studio.api.v1.contracts.monitoring import MonitoringPageResponse
    from datacoolie_studio.domains.monitoring.page_dispatch import build_monitoring_page
    from datacoolie_studio.domains.monitoring.page_projection import public_monitoring_page
    row = record()
    row["metrics"]["rows_read"] = count
    row["metrics"]["rows_written"] = count + 1
    with native_connection(tmp_path, [row]) as conn:
        result = build_monitoring_page([], "volume", {}, analytics_context=(conn, [1], "test"))
        wire = MonitoringPageResponse.model_validate(exact_metric_transport(public_monitoring_page("volume", result))).model_dump(exclude_unset=True)
        assert wire["volume"]["kpis"]["total_rows_read"] == str(count)
        assert wire["volume"]["kpis"]["total_rows_written"] == str(count + 1)
        assert wire["volume"]["top_dataflows_by_rows_read"][0]["value"] == count


@pytest.mark.parametrize("flow_id", ["unknown", "none", "null", " "])
def test_failure_and_performance_profiles_preserve_exact_source_session_flow_identity(tmp_path, flow_id):
    from datacoolie_studio.domains.monitoring.read_models.failures import failures_read_model
    row = record()
    row.update(dataflow_id=flow_id, status="failed", message="root error")
    row["attempts"][-1]["source"].update(status="failed", message="source error")
    with native_connection(tmp_path, [row]) as conn:
        conn.execute(f"INSERT INTO {schema.DATAFLOW_TABLE} SELECT * REPLACE (2 AS _source_id) FROM {schema.DATAFLOW_TABLE}")
        rebuild_monitoring_serving_facts(conn, dataflow_table=schema.DATAFLOW_TABLE, job_table=schema.JOB_TABLE, dataflow_column_types=schema.DATAFLOW_COLUMN_TYPES, job_column_types=schema.JOB_COLUMN_TYPES)
        context = (conn, [1, 2], "test")
        failed = failures_read_model([], {}, timezone_name="UTC", analytics_context=context)
        assert failed["summary"]["affected_dataflows"] == 2
        assert failed["summary"]["affected_dataflow_jobs"] == 2
        assert len(failed["top_dataflows"]) == 2
        profiles = performance_read_model([], {}, grain="day", timezone_name="UTC", analytics_context=context)["slowest_dataflow_profiles"]
        assert len(profiles) == 2
        assert {row["dataflow_id"] for row in profiles} == {flow_id}
