"""Actual producer v7 records retain observation meaning through native storage."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import duckdb
import pytest

from datacoolie.core.models.connection import Connection
from datacoolie.core.models.dataflow import DataFlow
from datacoolie.core.models.destination import Destination
from datacoolie.core.models.source import Source
from datacoolie.destinations.runtime import DestinationRuntimeInfo
from datacoolie.logging.persistence.projection import build_dataflow_entry
from datacoolie.orchestration.execution.runtime import AttemptRuntimeInfo, DataFlowRuntimeInfo
from datacoolie.orchestration.runtime import JobRuntimeInfo
from datacoolie.sources.runtime import SourceRuntimeInfo
from datacoolie_studio.domains.analytics import contracts, schema, store
from datacoolie_studio.domains.analytics.log_contract import admit_log_record
from datacoolie_studio.domains.monitoring.read_models.dataflows import dataflows_read_model


STAMP = datetime(2026, 10, 11, 10, tzinfo=timezone.utc)


def _entry(identifier, *, count=None, entered=True, operation="etl"):
    connection = Connection(name="observed", format="delta", configure={"base_path": "/data"})
    flow = DataFlow(dataflow_id="v7-parity", source=Source(connection=connection, table="in"),
                    destination=Destination(connection=connection, table="out", load_type="append"))
    destination = DestinationRuntimeInfo(status="succeeded", rows_written=count,
                                         operation_type="append" if operation == "etl" else "maintenance") if entered else None
    attempt = AttemptRuntimeInfo(status="succeeded", start_time=STAMP, end_time=STAMP,
                                 source=SourceRuntimeInfo(status="succeeded", rows_read=100),
                                 destination=destination)
    runtime = DataFlowRuntimeInfo(dataflow_id=flow.dataflow_id, dataflow_run_id=identifier,
                                  status="succeeded", operation_type=operation, start_time=STAMP,
                                  end_time=STAMP, attempts=[attempt])
    return build_dataflow_entry(flow, runtime, job_info=JobRuntimeInfo(job_id="v7-job"))


def _publish(tmp_path, entries):
    path = tmp_path / "runs.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in entries), encoding="utf-8")
    descriptor = contracts.LogFileDescriptor(str(path), "dataflow_json", "{}", str(path))
    database = tmp_path / "analytics.duckdb"
    store.publish_rows_batched(7, [contracts.LogPublishBatch(dataflow_files=(descriptor,))], [],
                               [str(path)], database_path=database)
    return database


@pytest.mark.parametrize("entered", [False, True])
def test_missing_contributions_preserve_native_observed_total(tmp_path, entered):
    known = _entry("known", count=100)
    other = _entry("other", entered=entered)
    for row in (known, other):
        assert admit_log_record(row, expected_type="dataflow_run_log", uri="fixture", line=1)
    database = _publish(tmp_path, [known, other])
    with duckdb.connect(str(database), read_only=True) as conn:
        persisted = conn.execute(f"SELECT metrics.rows_written FROM {schema.DATAFLOW_TABLE} WHERE dataflow_run_id='other'").fetchone()
        assert persisted == (None,)
        assert "metric_eligibility" not in {row[0] for row in conn.execute(f"DESCRIBE {schema.DATAFLOW_TABLE}").fetchall()}
        result = dataflows_read_model([], {}, grain="day", timezone_name="UTC",
                                     analytics_context=(conn, [7], "v7-test"))
        from datacoolie_studio.domains.monitoring.page_dispatch import build_monitoring_page
        page = build_monitoring_page([], "dataflows", filters={"range": "all"},
            timezone_info=timezone.utc, timezone_label="UTC",
            analytics_context=(conn, [7], "v7-test"))
    assert result["summary"]["total_rows_written"] == 100
    assert result["summary"]["observed_rows_written"] == 100
    assert result["summary"]["rows_written_measured"] == 1
    assert result["summary"]["rows_written_eligible"] == 2
    assert page["volume"]["kpis"]["total_rows_written"] == 100
    assert page["volume"]["kpis"]["rows_written_eligible"] == 2


def test_only_unentered_and_measured_zero_are_distinct(tmp_path):
    database = _publish(tmp_path, [_entry("unentered", entered=False), _entry("zero", count=0)])
    with duckdb.connect(str(database), read_only=True) as conn:
        # Both raw observations retain their different values; combined total is measured0.
        values = conn.execute(f"SELECT dataflow_run_id, metrics.rows_written FROM {schema.DATAFLOW_TABLE} ORDER BY dataflow_run_id").fetchall()
        combined = dataflows_read_model([], {}, grain="day", timezone_name="UTC",
                                        analytics_context=(conn, [7], "v7-test"))
    assert values == [("unentered", None), ("zero", 0)]
    assert combined["summary"]["total_rows_written"] == 0


@pytest.mark.parametrize("numeric, entered, expected, measured", [
    (None, False, None, 0),
    (None, True, None, 0),
    (0, True, 0, 1),
])
def test_no_observations_and_zero_have_distinct_coverage(
        tmp_path, numeric, entered, expected, measured):
    entry = _entry("coverage", count=numeric, entered=entered)
    database = _publish(tmp_path, [entry])
    with duckdb.connect(str(database), read_only=True) as conn:
        result = dataflows_read_model([], {}, grain="day", timezone_name="UTC",
                                      analytics_context=(conn, [7], "coverage-test"))
    assert result["summary"]["total_rows_written"] == expected
    assert result["summary"]["rows_written_measured"] == measured
    assert result["summary"]["rows_written_eligible"] == 1


def test_producer_and_consumer_share_exact_v7_schema():
    from pathlib import Path
    from datacoolie_studio.domains.analytics.schema_compiler import V7_SCHEMA_DOCUMENT
    root = Path(__file__).resolve().parents[4]
    producer = json.loads((root / "datacoolie/tests/fixtures/logging/v7/schema.json").read_text())
    assert V7_SCHEMA_DOCUMENT == producer


def test_admission_accepts_nullable_metrics_and_rejects_obsolete_v7_without_rewriting(tmp_path):
    current = _entry("current", count=0)
    assert "metric_eligibility" not in current
    invalid = deepcopy(current)
    invalid["metric_eligibility"] = {key: True for key in current["metrics"]}
    path = tmp_path / "obsolete-v7.jsonl"
    original = json.dumps(invalid).encode() + b"\n"
    path.write_bytes(original)
    with pytest.raises(Exception, match="invalid v7"):
        admit_log_record(invalid, expected_type="dataflow_run_log", uri="fixture", line=1)
    assert path.read_bytes() == original
    legacy = deepcopy(current)
    legacy["log_schema_version"] = 6
    assert admit_log_record(legacy, expected_type="dataflow_run_log", uri="fixture", line=1)
    assert admit_log_record(current, expected_type="dataflow_run_log", uri="fixture", line=1)


def test_historical_v6_values_survive_mixed_native_publication(tmp_path):
    current = _entry("v7-unentered", entered=False)
    legacy = _entry("v6-zero", count=0)
    legacy["log_schema_version"] = 6
    database = _publish(tmp_path, [legacy, current])
    with duckdb.connect(str(database), read_only=True) as conn:
        stored = conn.execute(f"SELECT log_schema_version, metrics.rows_written FROM {schema.DATAFLOW_TABLE} ORDER BY log_schema_version").fetchall()
        result = dataflows_read_model([], {}, grain="day", timezone_name="UTC",
                                      analytics_context=(conn, [7], "mixed-test"))
    assert stored == [(6, 0), (7, None)]
    assert result["summary"]["total_rows_written"] == 0


@pytest.mark.parametrize("version, expected", [(6, 0), (7, None)])
def test_empty_filter_uses_unfiltered_admitted_version_context(tmp_path, version, expected):
    entry = _entry("filtered-out", count=0)
    if version == 6:
        entry["log_schema_version"] = 6
    database = _publish(tmp_path, [entry])
    with duckdb.connect(str(database), read_only=True) as conn:
        result = dataflows_read_model([], {"search": "no-such-flow"}, grain="day",
            timezone_name="UTC", analytics_context=(conn, [7], "empty-filter-test"))
    assert result["summary"]["total_dataflows"] == 0
    assert result["summary"]["total_rows_written"] == expected


@pytest.mark.parametrize("missing_version, expected", [(6, None), (7, 100)])
def test_legacy_only_and_mixed_populations_keep_their_aggregate_contract(tmp_path, missing_version, expected):
    known = _entry("legacy-known", count=100)
    known["log_schema_version"] = 6
    missing = _entry("missing")
    missing["log_schema_version"] = missing_version
    database = _publish(tmp_path, [known, missing])
    with duckdb.connect(str(database), read_only=True) as conn:
        result = dataflows_read_model([], {}, grain="day", timezone_name="UTC",
            analytics_context=(conn, [7], "mixed-context"))
        from datacoolie_studio.domains.monitoring.page_dispatch import build_monitoring_page
        page = build_monitoring_page([], "volume", filters={"range": "all"},
            timezone_info=timezone.utc, timezone_label="UTC",
            analytics_context=(conn, [7], "mixed-context"))
    assert result["summary"]["total_rows_written"] == expected
    assert page["volume"]["kpis"]["total_rows_written"] == expected
