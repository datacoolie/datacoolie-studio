from __future__ import annotations

from tests.support.v6_records import insert_native_rows

import copy
import json

import duckdb

from datacoolie_studio.domains.analytics import schema, schema_tables
from datacoolie_studio.domains.analytics.serving_facts import (
    rebuild_monitoring_serving_facts,
)
from datacoolie_studio.domains.monitoring.read_models.dataflows import dataflows_read_model
from datacoolie_studio.domains.monitoring.read_models.performance import performance_read_model
from datacoolie_studio.domains.monitoring.read_models.runtime_phase import v6_timing_complete_sql
from datacoolie_studio.domains.monitoring.read_models.volume import (
    volume_evidence_read_model,
    volume_read_model,
)
from tests.support.v6_records import native_record


def _connection(records: list[dict]) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    schema_tables.ensure_tables(connection)
    schema_tables.ensure_typed_table(
        connection, schema.DATAFLOW_TABLE, schema.DATAFLOW_COLUMN_TYPES,
    )
    schema_tables.ensure_typed_table(
        connection, schema.JOB_TABLE, schema.JOB_COLUMN_TYPES,
    )
    insert_native_rows(
        connection,
        schema.DATAFLOW_TABLE,
        1,
        [
            (f"round2-{index}.json", "dataflow_json", json.dumps(record), record)
            for index, record in enumerate(records)
        ],
        schema.DATAFLOW_COLUMN_TYPES,
    )
    rebuild_monitoring_serving_facts(
        connection,
        dataflow_table=schema.DATAFLOW_TABLE,
        job_table=schema.JOB_TABLE,
        dataflow_column_types=schema.cache_table_column_types(schema.DATAFLOW_COLUMN_TYPES),
        job_column_types=schema.cache_table_column_types(schema.JOB_COLUMN_TYPES),
    )
    return connection


def test_attempt_duration_mismatch_keeps_diagnostics_strict_and_contribution_observed():
    record = native_record({
        "dataflow_id": "round2-timing",
        "dataflow_run_id": "round2-timing-run",
        "duration_seconds": 100,
    })
    record["attempts"][-1]["duration_seconds"] = 1
    connection = _connection([record])

    result = performance_read_model(
        [], {}, grain="day", timezone_name="UTC",
        analytics_context=(connection, [1], "round2"),
    )

    assert result["summary"]["avg_duration_seconds"] == 100
    assert result["summary"]["phase_runs_eligible"] == 1
    assert result["summary"]["phase_runs_measured"] == 0
    assert result["summary"]["phase_coverage_percent"] == 0
    assert result["summary"]["overhead_duration_percent"] is None
    assert result["summary"]["optimization_candidate_count"] == 0
    assert all(row["total_duration_seconds"] is not None and row["phase_runs_measured"] == 1 for row in result["phase_contribution_by_stage_operation"])
    assert result["phase_contribution_by_stage_operation"][0]["overhead_duration_seconds"] == 0
    assert "abs(epoch" in v6_timing_complete_sql()


def test_volume_distinguishes_all_output_from_declared_lakehouse_subset():
    lakehouse = native_record({
        "dataflow_id": "round2-output",
        "dataflow_run_id": "round2-lakehouse",
        "destination_rows_written": 40,
        "destination_connection_type": "lakehouse",
        "destination_format": "delta",
    })
    warehouse = copy.deepcopy(lakehouse)
    warehouse.update({
        "dataflow_run_id": "round2-warehouse",
        "metrics": {**warehouse["metrics"], "rows_written": 20},
        "destination_connection_type": "warehouse",
        "destination_format": "table",
    })
    connection = _connection([lakehouse, warehouse])

    result = volume_read_model(
        [], {}, grain="day", timezone_name="UTC",
        analytics_context=(connection, [1], "round2"),
    )
    summary = result["summary"]

    assert summary["total_rows_written"] == 60
    assert summary["total_lakehouse_rows_written"] == 40
    assert summary["total_non_lakehouse_rows_written"] == 20
    assert summary["rows_written_measured"] == 2
    assert summary["rows_written_eligible"] == 2
    assert summary["lakehouse_rows_written_measured"] == 1


def test_flow_registries_consolidate_sessions_within_source_and_keep_latest_provenance():
    from tests.domains.monitoring.monitoring_read_model_support import _dataflow_metric_connection

    connection = _dataflow_metric_connection()
    connection.execute(
        f"INSERT INTO {schema.DATAFLOW_TABLE} "
        "SELECT * REPLACE ("
        "'session-a' AS log_session_id, "
        "__event_time + INTERVAL 1 DAY AS __event_time, "
        "CAST(TRY_CAST(end_time AS TIMESTAMPTZ) + INTERVAL 1 DAY AS VARCHAR) AS end_time, "
        "CAST(TRY_CAST(start_time AS TIMESTAMPTZ) + INTERVAL 1 DAY AS VARCHAR) AS start_time, "
        "CASE WHEN dataflow_run_id = 'run-2' THEN 'round2-latest-run' ELSE 'round2-earlier-run' END AS dataflow_run_id, "
        "'round2-latest-name' AS dataflow_name, "
        "_file_uri || '-later' AS _file_uri"
        f") FROM {schema.DATAFLOW_TABLE}"
    )

    from datacoolie_studio.domains.analytics.publication import publish_generation
    from datacoolie_studio.db.models import utc_now
    publish_generation(connection, dataflow_column_types=schema.DATAFLOW_COLUMN_TYPES,
                       job_column_types=schema.JOB_COLUMN_TYPES, published_at=utc_now())

    context = (connection, [1], "round2")
    volume = volume_evidence_read_model(
        [], {}, limit=20, offset=0, sort_by="dataflow_name", sort_dir="asc",
        analytics_context=context,
    )
    dataflows = dataflows_read_model(
        [], {}, grain="day", timezone_name="UTC", analytics_context=context,
    )
    profiles = performance_read_model(
        [], {}, grain="day", timezone_name="UTC", analytics_context=context,
    )

    assert len(volume["records"]) == 1
    assert volume["records"][0]["run_count"] == 4
    assert volume["records"][0]["log_session_id"] == "session-a"
    assert volume["records"][0]["dataflow_run_id"] == "round2-latest-run"
    assert len(dataflows["name_status_health"]) == 1
    assert dataflows["name_status_health"][0]["log_session_id"] == "session-a"
    assert len(profiles["slowest_dataflow_profiles"]) == 1
    assert profiles["slowest_dataflow_profiles"][0]["log_session_id"] == "session-a"
