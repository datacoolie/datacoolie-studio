from __future__ import annotations

import copy
import json
from datetime import timezone
from decimal import Decimal

import duckdb
import pytest

from datacoolie_studio.domains.analytics import schema, schema_tables, store
from datacoolie_studio.domains.analytics.serving_facts import rebuild_monitoring_serving_facts
from datacoolie_studio.domains.monitoring.metrics.v6_semantics import (
    freshness_evidence,
    persisted_movement,
    read_boundary_adjustment,
    register_freshness_functions,
)
from datacoolie_studio.domains.monitoring.metrics.freshness_projections import _freshness_page
from datacoolie_studio.domains.monitoring.metrics.investigation import enrich_dataflow_run_for_investigation
from datacoolie_studio.domains.monitoring.metrics.overview_projections import _dataflow_watermark_summary
from datacoolie_studio.domains.monitoring.read_models.freshness import (
    freshness_evidence_read_model,
    freshness_read_model,
)
from datacoolie_studio.domains.monitoring.page_builders.freshness import build_freshness_page
from tests.support.v6_records import native_record


def _connection(tmp_path, groups):
    conn = duckdb.connect()
    schema_tables.ensure_tables(conn)
    for source_id, records in groups:
        path = tmp_path / f"runs-{source_id}.json"
        path.write_text(
            "\n".join(json.dumps(row) for row in records),
            encoding="utf-8",
        )
        store.insert_dataflow_file(conn, source_id, str(path), "dataflow_json", "{}")
    rebuild_monitoring_serving_facts(
        conn,
        dataflow_table=schema.DATAFLOW_TABLE,
        job_table=schema.JOB_TABLE,
        dataflow_column_types=schema.DATAFLOW_COLUMN_TYPES,
        job_column_types=schema.JOB_COLUMN_TYPES,
    )
    return conn


@pytest.mark.parametrize(
    ("loaded", "saved", "expected"),
    [
        ({"__date__": "2026-10-01"}, {"__date__": "2026-10-02"}, "advanced"),
        (
            {"__datetime__": "2026-10-01T00:00:00+00:00"},
            {"__datetime__": "2026-10-02T00:00:00+00:00"},
            "advanced",
        ),
        ({"__time__": "01:00:00.000000"}, {"__time__": "02:00:00.000000"}, "advanced"),
        (
            {"__date__": "2026-10-01"},
            {"__datetime__": "2026-10-02T00:00:00+00:00"},
            "changed",
        ),
        (
            {"payload": {"z": 1, "a": [True]}},
            {"payload": {"a": [True], "z": 1}},
            "unchanged",
        ),
        (
            {"payload": {"items": [1, 2]}},
            {"payload": {"items": [2, 1]}},
            "changed",
        ),
        ("9", "10", "changed"),
    ],
)
def test_tagged_and_opaque_watermarks_keep_their_expected_state(loaded, saved, expected):
    with duckdb.connect() as conn:
        register_freshness_functions(conn)
        actual = conn.execute(
            "SELECT studio_v6_persisted_movement(?, ?, 'saved', true)",
            [json.dumps(loaded), json.dumps(saved)],
        ).fetchone()[0]
    assert actual == expected
    assert persisted_movement(loaded, saved, "saved", True) == expected


@pytest.mark.parametrize(
    ("baseline_value", "saved_value", "candidate_kind", "expected"),
    [
        ("2024-01-02", "2024-01-03", "observed_max", "advanced"),
        ("2024-01-02", "2024-01-03", "request_end", "advanced"),
        (
            "2024-01-03T00:30:00Z",
            "2024-01-02T23:30:00-02:00",
            "observed_max",
            "advanced",
        ),
        (
            "2024-01-02T10:00:00",
            "2024-01-02T11:00:00",
            "observed_max",
            "advanced",
        ),
        (
            "2024-01-02T10:00:00",
            "2024-01-02T12:00:00Z",
            "observed_max",
            "changed",
        ),
        ("9", "10", "observed_max", "changed"),
        ("cursor-9", "cursor-10", "observed_max", "changed"),
        ("2024-02-30", "2024-03-01", "observed_max", "changed"),
        ("2024-01-02", "2024-01-03T00:00:00Z", "observed_max", "changed"),
        ("2024-01-02", "2024-01-03", "metadata", "changed"),
    ],
)
def test_source_ordered_iso_temporal_watermarks_use_engine_ordering(
    baseline_value, saved_value, candidate_kind, expected
):
    baseline = {"event_date": baseline_value}
    saved = {"event_date": saved_value}
    candidate_values = {"event_date": saved_value}
    assert persisted_movement(
        baseline,
        saved,
        "saved",
        True,
        baseline_present=True,
        candidate_kind=candidate_kind,
        candidate_values=candidate_values,
    ) == expected

    with duckdb.connect() as conn:
        register_freshness_functions(conn)
        actual = conn.execute(
            """SELECT studio_v6_persisted_movement_with_presence_kind_and_candidate(
                ?, ?, 'saved', true, true, ?, ?
            )""",
            [
                json.dumps(baseline),
                json.dumps(saved),
                candidate_kind,
                json.dumps(candidate_values),
            ],
        ).fetchone()[0]
    assert actual == expected


def test_api_date_saved_watermark_is_advanced_in_freshness_read_model(tmp_path):
    row = native_record({
        "dataflow_id": "28151c10-14c9-4e5b-8d4f-823622847559",
        "dataflow_name": "api-date-watermark",
        "dataflow_run_id": "28151c10-14c9-4e5b-8d4f-823622847559",
    })
    row["attempts"][-1]["watermark"].update({
        "load_status": "loaded",
        "loaded_watermark": {"event_date": "2024-01-02"},
        "merge_base": {"event_date": "2024-01-02"},
        "candidate": {
            "values": {"event_date": "2024-01-03"},
            "kind": "observed_max",
        },
        "watermark_to_save": {"event_date": "2024-01-03"},
        "save_status": "saved",
        "saved_watermark": {"event_date": "2024-01-03"},
    })

    assert freshness_evidence(row)["movement_state"] == "advanced"
    with _connection(tmp_path, [(1, [row])]) as conn:
        context = (conn, [1], "api-date-watermark")
        summary = freshness_read_model(
            [], {}, grain="day", timezone_name="UTC", analytics_context=context,
        )["summary"]
        evidence = freshness_evidence_read_model(
            [], {}, limit=10, offset=0, sort_by="dataflow_name", sort_dir="asc",
            analytics_context=context,
        )["records"][0]
    assert summary["watermark_advanced_runs"] == 1
    assert summary["watermark_rate_runs"] == 1
    assert summary["watermark_advanced_rate"] == 100.0
    assert evidence["latest_run_id"] == "28151c10-14c9-4e5b-8d4f-823622847559"
    assert evidence["movement_state"] == "advanced"


def _empty_incremental_api_record():
    row = native_record({
        "dataflow_id": "empty-api-flow",
        "dataflow_name": "empty-api-flow",
        "dataflow_run_id": "empty-api-run",
        "source_format": "api",
        "source_connection_type": "api",
        "source_configure": json.dumps({
            "range_param_mapping": {
                "modified_at": {
                    "lower": {"location": "params", "name": "modified_since"},
                },
            },
        }),
        "read_request": {
            "watermark_start": None,
            "watermark_end": None,
            "watermark_start_operator": None,
            "watermark_end_operator": None,
            "read_range": None,
            "reference_time": "2024-01-03T00:00:00+00:00",
        },
    })
    attempt = row["attempts"][-1]
    attempt["status"] = "succeeded"
    attempt["source"].update({
        "status": "succeeded",
        "rows_read": 0,
        "selection": {
            "mode": "incremental",
            "lower": {"modified_at": "2024-01-02"},
            "upper": None,
            "lower_operator": ">=",
            "upper_operator": "<",
            "combine_operator": "OR",
            "upper_origin": "generated",
        },
    })
    attempt["watermark"] = {
        "load_status": "loaded",
        "loaded_watermark": {"modified_at": "2024-01-02"},
        "merge_base": None,
        "candidate": None,
        "watermark_to_save": None,
        "save_status": "no_candidate",
        "saved_watermark": None,
    }
    return row


def test_read_boundary_adjustment_is_shared_by_python_sql_and_registry(tmp_path):
    row = _empty_incremental_api_record()
    row["read_request"]["watermark_start"] = {"modified_at": "2024-01-01"}
    expected = read_boundary_adjustment(row)
    assert expected["adjustment_state"] == "adjusted"
    assert expected["adjustment_basis"] == "request"

    with duckdb.connect() as conn:
        register_freshness_functions(conn)
        sql_result = conn.execute(
            "SELECT studio_v6_read_boundary_adjustment(?)",
            [json.dumps({
                "operation_type": row["operation_type"],
                "read_request": row["read_request"],
                "attempts": row["attempts"],
                "source_watermark_columns": row["source_watermark_columns"],
                "source_configure": row["source_configure"],
                "source_format": row["source_format"],
                "source_connection_type": row["source_connection_type"],
            })],
        ).fetchone()[0]
    assert json.loads(sql_result)["adjustment_state"] == expected["adjustment_state"]

    with _connection(tmp_path, [(1, [row])]) as conn:
        context = (conn, [1], "read-boundary-adjustment")
        aggregate = freshness_read_model(
            [], {}, grain="day", timezone_name="UTC", analytics_context=context,
        )
        evidence = freshness_evidence_read_model(
            [], {}, limit=10, offset=0, sort_by="dataflow_name", sort_dir="asc",
            analytics_context=context,
        )["records"][0]

    visual_row = aggregate["read_boundary_adjustment_by_dataflow"][0]
    assert visual_row["adjustment_state"] == "adjusted"
    assert visual_row["dataflow_id"] == row["dataflow_id"]
    assert evidence["adjustment_state"] == "adjusted"
    assert evidence["adjustment_input_json"] == '{"modified_at":"2024-01-01"}'
    assert evidence["adjustment_effective_json"] == '{"modified_at":"2024-01-02"}'


def test_read_boundary_adjustment_chart_rows_aggregate_each_dataflow_run(tmp_path):
    adjusted = _empty_incremental_api_record()
    adjusted["read_request"]["watermark_start"] = {"modified_at": "2024-01-01"}
    unchanged = copy.deepcopy(adjusted)
    unchanged["dataflow_run_id"] = "empty-api-run-2"
    unchanged["read_request"]["watermark_start"] = {"modified_at": "2024-01-02"}
    unchanged["start_time"] = "2026-10-05T12:01:00Z"
    unchanged["end_time"] = "2026-10-05T12:01:01Z"

    with _connection(tmp_path, [(1, [adjusted, unchanged])]) as conn:
        aggregate = freshness_read_model(
            [], {}, grain="day", timezone_name="UTC",
            analytics_context=(conn, [1], "read-boundary-counts"),
        )

    visual_row = aggregate["read_boundary_adjustment_by_dataflow"][0]
    assert visual_row["adjustment_adjusted_runs"] == 1
    assert visual_row["adjustment_unchanged_runs"] == 1
    assert visual_row["adjustment_comparable_runs"] == 2
    assert visual_row["adjustment_total_runs"] == 2


def test_read_boundary_adjustment_compares_supported_temporal_representations_losslessly():
    row = _empty_incremental_api_record()
    row["read_request"]["watermark_start"] = {"modified_at": "2024-01-02T00:00:00+02:00"}
    row["attempts"][-1]["source"]["selection"]["lower"] = {
        "modified_at": {"__datetime__": "2024-01-01T22:00:00+00:00"},
    }
    assert read_boundary_adjustment(row)["adjustment_state"] == "unchanged"

    row["attempts"][-1]["source"]["selection"]["lower"] = {
        "modified_at": {"__datetime__": "2024-01-02T00:00:00"},
    }
    row["read_request"]["watermark_start"] = {"modified_at": "2024-01-02"}
    assert read_boundary_adjustment(row)["adjustment_state"] == "unchanged"

    row["attempts"][-1]["source"]["selection"]["lower"] = {
        "modified_at": {"__datetime__": "2024-01-01T22:00:00+00:00"},
    }
    row["read_request"]["watermark_start"] = {"modified_at": "2024-01-02T00:00:00"}
    assert read_boundary_adjustment(row)["adjustment_state"] == "unknown"

    row["read_request"]["watermark_start"] = {"modified_at": "2024-01-02T00:00:00.1234567+00:00"}
    assert read_boundary_adjustment(row)["adjustment_state"] == "unknown"

    row["source_configure"] = json.dumps({
        "range_param_mapping": {
            "cursor": {"lower": {"location": "params", "name": "after"}},
        },
    })
    row["read_request"]["watermark_start"] = {"cursor": "abc"}
    row["attempts"][-1]["source"]["selection"]["lower"] = {"cursor": "abd"}
    assert read_boundary_adjustment(row)["adjustment_state"] == "adjusted"


def test_read_boundary_adjustment_allows_file_cursor_string_to_tagged_datetime():
    row = _empty_incremental_api_record()
    row["source_format"] = "parquet"
    row["source_connection_type"] = "file"
    row["read_request"]["watermark_start"] = {"__date_folder_partition__": "2024-01-02"}
    row["attempts"][-1]["source"]["selection"]["lower"] = {
        "__date_folder_partition__": {"__datetime__": "2024-01-02T00:00:00"},
    }
    assert read_boundary_adjustment(row)["adjustment_state"] == "unchanged"


def test_read_boundary_adjustment_preserves_large_and_decimal_bound_text():
    row = _empty_incremental_api_record()
    row["read_request"]["watermark_start"] = {"id": 9007199254740993, "ratio": Decimal("1E-3")}
    row["attempts"][-1]["source"]["selection"]["lower"] = {"id": 9007199254740994, "ratio": Decimal("2E-3")}

    evidence = read_boundary_adjustment(row)

    assert evidence["adjustment_state"] == "adjusted"
    assert evidence["adjustment_input_json"] == '{"id":9007199254740993,"ratio":0.001}'
    assert evidence["adjustment_effective_json"] == '{"id":9007199254740994,"ratio":0.002}'


def test_zero_row_api_incremental_read_is_empty_across_freshness_models(tmp_path):
    row = _empty_incremental_api_record()

    assert freshness_evidence(row)["movement_state"] == "empty"
    dataflow_run = enrich_dataflow_run_for_investigation(row)
    overview_watermark = _dataflow_watermark_summary([row])[0]
    assert dataflow_run["movement_state"] == "empty"
    assert overview_watermark["empty"] == 1
    projection = _freshness_page([row])
    assert projection["kpis"]["watermark_empty_runs"] == 1
    assert projection["kpis"]["watermark_rate_runs"] == 1
    assert projection["kpis"]["watermark_advanced_rate"] == 0.0

    with _connection(tmp_path, [(1, [row])]) as conn:
        context = (conn, [1], "empty-api")
        summary = freshness_read_model(
            [], {}, grain="day", timezone_name="UTC", analytics_context=context,
        )["summary"]
        trend = freshness_read_model(
            [], {}, grain="day", timezone_name="UTC", analytics_context=context,
        )["watermark_movement_by_date"]
        evidence = freshness_evidence_read_model(
            [], {}, limit=10, offset=0, sort_by="dataflow_name", sort_dir="asc",
            analytics_context=context,
        )["records"][0]

    assert summary["watermark_empty_runs"] == 1
    assert summary["watermark_rate_runs"] == 1
    assert summary["watermark_advanced_rate"] == 0.0
    assert trend[0]["empty"] == 1
    assert trend[0]["watermark_rate_runs"] == 1
    assert trend[0]["advanced_rate"] == 0.0
    assert evidence["movement_state"] == "empty"
    assert evidence["persistence_state"] == "no_candidate"


def test_serialized_selection_bounds_keep_empty_state_consistent(tmp_path):
    row = _empty_incremental_api_record()
    row["attempts"][-1]["source"]["selection"]["lower"] = json.dumps(
        {"modified_at": "2024-01-02"}
    )

    assert freshness_evidence(row)["movement_state"] == "empty"
    assert enrich_dataflow_run_for_investigation(row)["movement_state"] == "empty"

    with _connection(tmp_path, [(1, [row])]) as conn:
        context = (conn, [1], "serialized-empty-api")
        summary = freshness_read_model(
            [], {}, grain="day", timezone_name="UTC", analytics_context=context,
        )["summary"]
        evidence = freshness_evidence_read_model(
            [], {}, limit=10, offset=0, sort_by="dataflow_name", sort_dir="asc",
            analytics_context=context,
        )["records"][0]

    assert summary["watermark_empty_runs"] == 1
    assert summary["watermark_rate_runs"] == 1
    assert evidence["movement_state"] == "empty"


def test_zero_row_database_incremental_read_uses_selected_watermark_column(tmp_path):
    row = _empty_incremental_api_record()
    row.update({
        "dataflow_id": "empty-db-flow",
        "dataflow_name": "empty-db-flow",
        "dataflow_run_id": "empty-db-run",
        "source_format": "sql",
        "source_connection_type": "database",
        "source_configure": None,
        "source_watermark_columns": json.dumps(["modified_at"]),
    })
    assert freshness_evidence(row)["movement_state"] == "empty"

    with _connection(tmp_path, [(1, [row])]) as conn:
        context = (conn, [1], "empty-db")
        report = freshness_read_model(
            [], {}, grain="day", timezone_name="UTC", analytics_context=context,
        )
        page = build_freshness_page(
            [], {}, timezone.utc, "UTC", "test", analytics_context=context,
        )
        evidence = freshness_evidence_read_model(
            [], {}, limit=10, offset=0, sort_by="dataflow_name", sort_dir="asc",
            analytics_context=context,
        )["records"][0]

    assert report["summary"]["watermark_empty_runs"] == 1
    assert report["summary"]["watermark_advanced_rate"] == 0.0
    assert report["watermark_movement_by_date"][0]["empty"] == 1
    assert page["freshness"]["kpis"]["watermark_empty_runs"] == 1
    assert page["freshness"]["kpis"]["watermark_rate_runs"] == 1
    assert evidence["movement_state"] == "empty"


@pytest.mark.parametrize(
    "missing_proof",
    [
        "no_bound",
        "no_selection",
        "full_selection",
        "range_selection",
        "read_range",
        "unrecognized_reader",
        "unbound_api_parameter",
        "load_error",
        "candidate_exists",
        "nonzero_rows",
        "boolean_row_count",
        "failed_attempt",
        "running_attempt",
        "cancelled_attempt",
        "failed_source",
        "failed_root",
    ],
)
def test_empty_requires_a_successful_incremental_bound_and_no_persisted_movement(missing_proof):
    row = _empty_incremental_api_record()
    attempt = row["attempts"][-1]
    if missing_proof == "no_bound":
        attempt["source"]["selection"]["lower"] = {}
    elif missing_proof == "no_selection":
        attempt["source"]["selection"] = None
    elif missing_proof == "full_selection":
        attempt["source"]["selection"]["mode"] = "full"
    elif missing_proof == "range_selection":
        attempt["source"]["selection"]["mode"] = "range"
    elif missing_proof == "read_range":
        row["read_request"]["read_range"] = {
            "column": "modified_at",
            "start": "2024-01-02",
            "end": "2024-01-03",
            "lower_operator": ">=",
            "upper_operator": "<",
        }
    elif missing_proof == "unrecognized_reader":
        row["source_format"] = "csv"
        row["source_connection_type"] = "file"
    elif missing_proof == "unbound_api_parameter":
        source_configure = json.loads(row["source_configure"])
        source_configure["range_param_mapping"]["modified_at"]["lower"]["name"] = ""
        row["source_configure"] = json.dumps(source_configure)
    elif missing_proof == "load_error":
        attempt["watermark"]["load_status"] = "load_error"
    elif missing_proof == "candidate_exists":
        attempt["watermark"]["candidate"] = {
            "values": {"modified_at": "2024-01-03"},
            "kind": "observed_max",
        }
    elif missing_proof == "nonzero_rows":
        attempt["source"]["rows_read"] = 1
    elif missing_proof == "boolean_row_count":
        attempt["source"]["rows_read"] = False
    elif missing_proof == "failed_attempt":
        attempt["status"] = "failed"
    elif missing_proof == "running_attempt":
        attempt["status"] = "running"
    elif missing_proof == "cancelled_attempt":
        attempt["status"] = "cancelled"
    elif missing_proof == "failed_source":
        attempt["source"]["status"] = "failed"
    elif missing_proof == "failed_root":
        row["status"] = "failed"

    assert freshness_evidence(row)["movement_state"] == "unknown"


def test_empty_api_movement_accepts_zero_and_false_bounds():
    for bound in (0, False):
        row = _empty_incremental_api_record()
        row["attempts"][-1]["source"]["selection"]["lower"]["modified_at"] = bound
        row["attempts"][-1]["watermark"]["loaded_watermark"]["modified_at"] = bound
        assert freshness_evidence(row)["movement_state"] == "empty"


def test_empty_database_movement_requires_the_selected_key_to_be_a_watermark_column():
    row = _empty_incremental_api_record()
    row.update({
        "source_format": "sql",
        "source_connection_type": "database",
        "source_configure": None,
        "source_watermark_columns": ["modified_at"],
    })
    assert freshness_evidence(row)["movement_state"] == "empty"

    row["source_watermark_columns"] = ["id"]
    row["attempts"][-1]["source"]["selection"]["lower"] = {"other": 0}
    row["attempts"][-1]["watermark"]["loaded_watermark"] = {"id": 0}
    assert freshness_evidence(row)["movement_state"] == "unknown"

    row["attempts"][-1]["source"]["selection"]["lower"] = {"id": None}
    assert freshness_evidence(row)["movement_state"] == "unknown"


def test_empty_movement_does_not_require_exact_loaded_value_after_backward_offset():
    row = _empty_incremental_api_record()
    row["attempts"][-1]["source"]["selection"]["lower"]["modified_at"] = "2023-12-31"
    assert freshness_evidence(row)["movement_state"] == "empty"


def test_skipped_terminal_run_with_successful_zero_row_source_can_be_empty():
    row = _empty_incremental_api_record()
    row["status"] = "skipped"
    row["attempts"][-1]["status"] = "skipped"
    assert freshness_evidence(row)["movement_state"] == "empty"


def test_metadata_configuration_without_persisted_watermark_evidence_is_not_empty():
    row = _empty_incremental_api_record()
    row["attempts"][-1]["watermark"] = None
    assert freshness_evidence(row)["movement_state"] == "not_configured"


def test_failed_root_keeps_affirmative_persisted_movement_priority():
    row = _empty_incremental_api_record()
    row["status"] = "failed"
    row["attempts"][-1]["watermark"].update({
        "merge_base": {"modified_at": "2024-01-02"},
        "candidate": {
            "values": {"modified_at": "2024-01-03"},
            "kind": "observed_max",
        },
        "watermark_to_save": {"modified_at": "2024-01-03"},
        "save_status": "saved",
        "saved_watermark": {"modified_at": "2024-01-03"},
    })

    assert freshness_evidence(row)["movement_state"] == "advanced"


@pytest.mark.parametrize(
    ("merge_base", "candidate_value", "candidate_kind", "expected"),
    [
        ("2024-01-02", "2024-01-03", "observed_max", "advanced"),
        ("2024-01-02", "2024-01-02", "observed_max", "unchanged"),
        ("cursor-a", "cursor-b", "opaque", "changed"),
        (None, "2024-01-03", "observed_max", "initialized"),
    ],
)
def test_saved_movement_has_priority_over_zero_row_read(
    merge_base, candidate_value, candidate_kind, expected
):
    row = _empty_incremental_api_record()
    watermark = row["attempts"][-1]["watermark"]
    candidate = {"modified_at": candidate_value}
    watermark.update({
        "merge_base": None if merge_base is None else {"modified_at": merge_base},
        "candidate": {"values": candidate, "kind": candidate_kind},
        "watermark_to_save": candidate,
        "save_status": "saved",
        "saved_watermark": candidate,
    })

    assert freshness_evidence(row)["movement_state"] == expected


def test_opaque_candidate_kind_never_claims_monotonic_advance():
    with duckdb.connect() as conn:
        register_freshness_functions(conn)
        assert conn.execute(
            "SELECT studio_v6_persisted_movement_with_presence_kind(?, ?, 'saved', true, true, 'opaque')",
            ["1", "2"],
        ).fetchone() == ("changed",)
        assert conn.execute(
            "SELECT studio_v6_persisted_movement_with_presence_kind(?, ?, 'saved', true, true, 'opaque')",
            ["1", "1"],
        ).fetchone() == ("unchanged",)
    assert persisted_movement(
        1, 2, "saved", True, candidate_kind="metadata"
    ) == "changed"


@pytest.mark.parametrize("save_status", ["save_error", "not_attempted"])
def test_save_error_and_not_attempted_are_unknown_movement_with_native_state(tmp_path, save_status):
    row = native_record({
        "dataflow_id": "save-error-flow",
        "dataflow_name": "save-error-flow",
        "dataflow_run_id": "save-error-run",
    })
    row["attempts"][-1]["watermark"]["save_status"] = save_status
    with _connection(tmp_path, [(1, [row])]) as conn:
        evidence = freshness_evidence_read_model(
            [], {}, limit=10, offset=0, sort_by="dataflow_name", sort_dir="asc",
            analytics_context=(conn, [1], "review"),
        )["records"][0]
    assert evidence["movement_state"] == "unknown"
    assert evidence["persistence_state"] == save_status
    assert evidence["save_status"] == save_status


def test_native_full_read_is_check_evidence_without_watermark_configuration(tmp_path):
    row = native_record({
        "dataflow_id": "full-read-flow",
        "dataflow_name": "full-read-flow",
        "dataflow_run_id": "full-read-run",
        "read_request": None,
    })
    attempt = row["attempts"][-1]
    attempt["source"]["selection"] = None
    attempt["watermark"] = None
    evidence = freshness_evidence(row)
    assert evidence["configuration_state"] == "not_configured"
    assert evidence["selection_state"] == "not_selected"
    assert evidence["persistence_state"] == "not_requested"
    with _connection(tmp_path, [(1, [row])]) as conn:
        summary = freshness_read_model(
            [], {}, grain="day", timezone_name="UTC",
            analytics_context=(conn, [1], "review"),
        )["summary"]
    assert summary["freshness_runs"] == 1
    assert summary["watermark_enabled_dataflows"] == 0


def test_native_not_requested_watermark_object_does_not_configure_full_read(tmp_path):
    row = native_record({
        "dataflow_id": "full-read-runtime-flow",
        "dataflow_name": "full-read-runtime-flow",
        "dataflow_run_id": "full-read-runtime-run",
        "read_request": None,
    })
    attempt = row["attempts"][-1]
    attempt["source"]["selection"] = None
    attempt["watermark"] = {
        "load_status": "not_requested",
        "loaded_watermark": None,
        "merge_base": None,
        "candidate": None,
        "watermark_to_save": None,
        "save_status": "not_requested",
        "saved_watermark": None,
    }
    evidence = freshness_evidence(row)
    assert evidence["configuration_state"] == "not_configured"
    assert evidence["persistence_state"] == "not_requested"
    assert evidence["movement_state"] == "not_configured"
    with _connection(tmp_path, [(1, [row])]) as conn:
        summary = freshness_read_model(
            [], {}, grain="day", timezone_name="UTC",
            analytics_context=(conn, [1], "review"),
        )["summary"]
    assert summary["watermark_enabled_dataflows"] == 0


def test_native_no_attempt_is_not_attempted_and_excluded_from_check_age(tmp_path):
    row = native_record({
        "dataflow_id": "preflight-flow",
        "dataflow_name": "preflight-flow",
        "dataflow_run_id": "preflight-run",
        "status": "skipped",
        "message": "no attempt after preflight",
        "read_request": None,
        "attempts": [],
    })
    evidence = freshness_evidence(row)
    assert evidence["persistence_state"] == "not_attempted"
    with _connection(tmp_path, [(1, [row])]) as conn:
        context = (conn, [1], "review")
        summary = freshness_read_model(
            [], {}, grain="day", timezone_name="UTC", analytics_context=context,
        )["summary"]
        detail = freshness_evidence_read_model(
            [], {}, limit=10, offset=0, sort_by="dataflow_name", sort_dir="asc",
            analytics_context=context,
        )["records"][0]
    assert summary["freshness_runs"] == 0
    assert detail["latest_freshness_at"] is None
    assert detail["persistence_state"] == "not_attempted"


def test_saved_watermark_compares_to_explicit_null_merge_base_and_missing_is_unknown():
    row = native_record({"dataflow_id": "merge-base-flow"})
    watermark = row["attempts"][-1]["watermark"]
    loaded = watermark["loaded_watermark"]
    saved = watermark["saved_watermark"]
    watermark.update(
        merge_base=None,
        loaded_watermark=loaded,
        saved_watermark=saved,
        save_status="saved",
    )
    initialized = freshness_evidence(row)
    assert initialized["loaded_watermark"] == loaded
    assert initialized["comparison_basis"] == "merge_base_null"
    assert initialized["movement_state"] == "initialized"

    missing = copy.deepcopy(row)
    del missing["attempts"][-1]["watermark"]["merge_base"]
    evidence = freshness_evidence(missing)
    assert evidence["comparison_basis"] == "missing_merge_base"
    assert evidence["movement_state"] == "unknown"


def test_stage_coverage_uses_source_nullable_session_and_flow_identity(tmp_path):
    rows = [
        native_record({
            "dataflow_id": "same-flow",
            "dataflow_name": "same-flow",
            "dataflow_run_id": "source-one-run",
            "log_session_id": None,
            "stage": "bronze",
        }),
        native_record({
            "dataflow_id": "same-flow",
            "dataflow_name": "same-flow",
            "dataflow_run_id": "source-two-run",
            "log_session_id": None,
            "stage": "bronze",
        }),
    ]
    with _connection(tmp_path, [(1, [rows[0]]), (2, [rows[1]])]) as conn:
        result = freshness_read_model(
            [], {}, grain="day", timezone_name="UTC",
            analytics_context=(conn, [1, 2], "review"),
        )
    coverage = result["watermark_coverage_by_stage"]
    assert coverage == [{
        "stage": "bronze",
        "observed_dataflows": 2,
        "watermark_enabled_dataflows": 2,
        "not_configured_dataflows": 0,
        "coverage_rate": 100.0,
    }]


def test_latest_freshness_status_uses_canonical_eligible_run_order(tmp_path):
    first = native_record({
        "dataflow_id": "canonical-flow",
        "dataflow_name": "canonical-flow",
        "dataflow_run_id": "run-a",
        "log_session_id": "session",
        "start_time": "2026-10-05T12:00:00Z",
        "end_time": "2026-10-05T12:00:01Z",
        "status": "succeeded",
    })
    latest = copy.deepcopy(first)
    latest.update(dataflow_run_id="run-z", status="skipped")
    with _connection(tmp_path, [(1, [first, latest])]) as conn:
        evidence = freshness_evidence_read_model(
            [], {}, limit=10, offset=0, sort_by="dataflow_name", sort_dir="asc",
            analytics_context=(conn, [1], "review"),
        )["records"][0]
    assert evidence["latest_freshness_status"] == "skipped"
    assert evidence["latest_run_id"] == "run-z"


def test_evidence_and_age_sorting_have_identity_tiebreakers(tmp_path):
    rows = [
        native_record({
            "dataflow_id": "same-name-flow",
            "dataflow_name": "same-name",
            "dataflow_run_id": "run-one",
            "log_session_id": None,
            "start_time": "2026-10-05T12:00:00Z",
            "end_time": "2026-10-05T12:00:01Z",
        }),
        native_record({
            "dataflow_id": "same-name-flow",
            "dataflow_name": "same-name",
            "dataflow_run_id": "run-two",
            "log_session_id": None,
            "start_time": "2026-10-05T12:00:00Z",
            "end_time": "2026-10-05T12:00:01Z",
        }),
    ]
    with _connection(tmp_path, [(1, [rows[0]]), (2, [rows[1]])]) as conn:
        context = (conn, [1, 2], "review")
        page = freshness_evidence_read_model(
            [], {}, limit=10, offset=0, sort_by="dataflow_name", sort_dir="asc",
            analytics_context=context,
        )
        result = freshness_read_model(
            [], {}, grain="day", timezone_name="UTC", analytics_context=context,
        )
    assert [(row["_source_id"], row["latest_run_id"]) for row in page["records"]] == [
        (1, "run-one"),
        (2, "run-two"),
    ]
    assert [(row["_source_id"], row["latest_run_id"]) for row in result["age_by_dataflow"]] == [
        (1, "run-one"),
        (2, "run-two"),
    ]
