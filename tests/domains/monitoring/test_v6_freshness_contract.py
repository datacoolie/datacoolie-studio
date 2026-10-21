from __future__ import annotations

import copy
import json
from decimal import Decimal

import duckdb
import pytest

from datacoolie_studio.domains.analytics import schema, schema_tables, store
from datacoolie_studio.domains.analytics.serving_facts import rebuild_monitoring_serving_facts
from datacoolie_studio.domains.monitoring.metrics.v6_semantics import exact_equal, register_freshness_functions
from datacoolie_studio.domains.monitoring.read_models.freshness import freshness_evidence_read_model, freshness_read_model
from tests.support.v6_records import native_record


@pytest.mark.parametrize("left,right,expected", [
    ({"a": 1, "b": [2]}, {"b": [2], "a": 1}, True),
    ([1, 2], [2, 1], False),
    (True, 1, False),
    (Decimal("1.25"), "1.25", False),
    (9007199254740993, "9007199254740993", False),
])
def test_persistence_equality_retains_json_types(left, right, expected):
    assert exact_equal(left, right) is expected


@pytest.mark.parametrize("loaded,saved,state", [
    ('{"a":1,"b":2}', '{"b":2,"a":1}', "unchanged"),
    ('[1,2]', '[2,1]', "changed"),
    ('1.25', '"1.25"', "changed"),
    ('{"__decimal__":"9.99"}', '{"__decimal__":"10.01"}', "advanced"),
    ('"9"', '"10"', "changed"),
    (None, '1', "initialized"),
])
def test_sql_persistence_comparison_is_lossless(loaded, saved, state):
    with duckdb.connect() as conn:
        register_freshness_functions(conn)
        register_freshness_functions(conn)
        assert conn.execute("SELECT studio_v6_persisted_movement(?, ?, 'saved', true)", [loaded, saved]).fetchone() == (state,)


def test_persistence_function_registration_across_reader_cursors():
    with duckdb.connect() as owner:
        first = owner.cursor()
        second = owner.cursor()
        try:
            register_freshness_functions(first)
            register_freshness_functions(second)
            first.close()
            assert second.execute(
                "SELECT studio_v6_persisted_movement('9', '10', 'saved', true)"
            ).fetchone() == ("advanced",)
        finally:
            first.close()
            second.close()


def connection(tmp_path, records):
    path = tmp_path / "runs.json"
    path.write_text("\n".join(json.dumps(row) for row in records), encoding="utf-8")
    conn = duckdb.connect()
    schema_tables.ensure_tables(conn)
    store.insert_dataflow_file(conn, 1, str(path), "dataflow_json", "{}")
    rebuild_monitoring_serving_facts(conn, dataflow_table=schema.DATAFLOW_TABLE, job_table=schema.JOB_TABLE, dataflow_column_types=schema.DATAFLOW_COLUMN_TYPES, job_column_types=schema.JOB_COLUMN_TYPES)
    return conn


def test_latest_no_attempt_does_not_reuse_older_watermark_or_imply_a_check(tmp_path):
    first = native_record({"_type": "dataflow_run_log", "dataflow_id": "flow", "dataflow_run_id": "first"})
    latest = copy.deepcopy(first)
    latest.update(dataflow_run_id="latest", start_time="2026-10-05T12:00:00Z", end_time="2026-10-05T12:00:01Z", status="skipped", attempts=[], read_request=None)
    with connection(tmp_path, [first, latest]) as conn:
        context = (conn, [1], "test")
        evidence = freshness_evidence_read_model([], {}, limit=10, offset=0, sort_by="dataflow_name", sort_dir="asc", analytics_context=context)["records"][0]
        assert evidence["save_status"] is None
        assert evidence["saved_watermark"] is None
        assert evidence["read_request"] is None
        assert evidence["raw_record_json"] is None
        from datacoolie_studio.domains.monitoring.evidence_hydration import hydrate_rows
        hydrated = hydrate_rows([evidence], context, evidence_fields=("raw_record_json",))[0]
        assert json.loads(hydrated["raw_record_json"])["dataflow_run_id"] == "latest"
        assert evidence["latest_run_status"] == "skipped"
        assert evidence["latest_freshness_at"].startswith("2026-10-04")
        assert evidence["movement_state"] == "not_configured"


def test_no_attempt_null_evidence_has_unknown_age(tmp_path):
    row = native_record({"_type": "dataflow_run_log", "dataflow_id": "flow", "attempts": [], "read_request": None})
    with connection(tmp_path, [row]) as conn:
        context = (conn, [1], "test")
        result = freshness_read_model([], {}, grain="day", timezone_name="UTC", analytics_context=context)
        assert result["summary"]["freshness_runs"] == 0
        assert result["summary"]["watermark_enabled_dataflows"] == 0
        assert result["summary"]["p50_age_days"] is None
        evidence = freshness_evidence_read_model([], {}, limit=10, offset=0, sort_by="dataflow_name", sort_dir="asc", analytics_context=context)["records"][0]
        assert evidence["latest_freshness_at"] is None
        assert evidence["age_days"] is None
        assert evidence["coverage_state"] == "not_configured"


def test_saved_null_merge_baseline_retains_explicit_initialization_basis(tmp_path):
    row = native_record({})
    watermark = row["attempts"][-1]["watermark"]
    watermark.update(merge_base=None, loaded_watermark={"value": 4},
                     save_status="saved", saved_watermark={"value": 5})
    with connection(tmp_path, [row]) as conn:
        evidence = freshness_evidence_read_model([], {}, limit=10, offset=0,
            sort_by="dataflow_name", sort_dir="asc", analytics_context=(conn, [1], "test"))["records"][0]
    assert evidence["comparison_basis"] == "merge_base_null"
    assert evidence["movement_state"] == "initialized"
