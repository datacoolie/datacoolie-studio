from __future__ import annotations

import copy
import json

import duckdb
import pytest

from datacoolie_studio.domains.analytics import schema, schema_tables, store, staging
from datacoolie_studio.domains.analytics.serving_facts import MONITORING_JOB_FACTS_TABLE, MONITORING_DATAFLOW_FACTS_TABLE, rebuild_monitoring_serving_facts, validate_monitoring_serving_facts
from tests.support.v6_records import native_record
from datacoolie_studio.domains.analytics.contracts import LogFileDescriptor


def rebuild(conn):
    rebuild_monitoring_serving_facts(conn, dataflow_table=schema.DATAFLOW_TABLE, job_table=schema.JOB_TABLE, dataflow_column_types=schema.DATAFLOW_COLUMN_TYPES, job_column_types=schema.JOB_COLUMN_TYPES)


def test_job_snapshot_prefers_observation_then_file_line_and_keeps_event_time(tmp_path):
    first = native_record({"_type": "job_run_log", "job_id": " shared ", "observed_at": "2026-10-05T12:00:00Z", "end_time": "2026-10-06T12:00:00Z", "engine_name": "older"})
    latest = copy.deepcopy(first)
    latest.update(observed_at="2026-10-06T12:00:00Z", end_time="2026-10-05T12:00:00Z", engine_name="latest")
    tie = copy.deepcopy(latest)
    tie["engine_name"] = "last-line"
    other = copy.deepcopy(tie)
    other.update(log_session_id=None, engine_name="null-session")
    path = tmp_path / "jobs.json"
    path.write_text("\n".join(json.dumps(row) for row in [first, latest, tie, other]))
    with duckdb.connect() as conn:
        schema_tables.ensure_tables(conn)
        relations, types, _, _ = staging.stage_job_relations(conn, [LogFileDescriptor(canonical_uri=str(path), read_uri=str(path), file_kind="job_json", revision_json="{}")])
        store.insert_job_file(conn, 1, str(path), "job_json", "{}", source_relation=next(iter(relations.values())), source_types=types[0])
        rebuild(conn)
        result = conn.execute(f"SELECT engine_name, event_time::DATE::VARCHAR, _record_line FROM {MONITORING_JOB_FACTS_TABLE} WHERE log_session_id IS NOT NULL").fetchone()
        assert result == ("last-line", "2026-10-05", 3)
        assert conn.execute(f"SELECT COUNT(*) FROM {MONITORING_JOB_FACTS_TABLE}").fetchone() == (2,)
        validate_monitoring_serving_facts(conn, dataflow_table=schema.DATAFLOW_TABLE, job_table=schema.JOB_TABLE)
        # Selecting an older valid observation must fail even if all scalars agree
        # with that observation and its provenance exists in the raw table.
        conn.execute(f"UPDATE {MONITORING_JOB_FACTS_TABLE} SET _record_line = 1 WHERE log_session_id IS NOT NULL")
        with pytest.raises(RuntimeError, match="canonical observation winner parity"):
            validate_monitoring_serving_facts(conn, dataflow_table=schema.DATAFLOW_TABLE, job_table=schema.JOB_TABLE)


@pytest.mark.parametrize("column,value", [("source_rows_read", "123456789012345678901234567890"), ("source_status", "failed"), ("dataflow_id", "wrong"), ("source_watermark_columns", "wrong"), ("destination_operation_type", "wrong")])
def test_native_serving_corruption_fails_publish_gate(tmp_path, column, value):
    path = tmp_path / "runs.json"
    path.write_text(json.dumps(native_record({"dataflow_id": "exact"})))
    with duckdb.connect() as conn:
        schema_tables.ensure_tables(conn)
        store.insert_dataflow_file(conn, 1, str(path), "dataflow_json", "{}")
        rebuild(conn)
        from datacoolie_studio.domains.analytics.sql import sql_string
        dtype = next(row[1] for row in conn.execute(f"DESCRIBE {MONITORING_DATAFLOW_FACTS_TABLE}").fetchall() if row[0] == column)
        conn.execute(f'UPDATE {MONITORING_DATAFLOW_FACTS_TABLE} SET "{column}" = CAST({sql_string(value)} AS {dtype})')
        with pytest.raises(RuntimeError, match="parity"):
            validate_monitoring_serving_facts(conn, dataflow_table=schema.DATAFLOW_TABLE, job_table=schema.JOB_TABLE)
