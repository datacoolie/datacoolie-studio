from __future__ import annotations

import json

from datacoolie_studio.core.time import parse_utc_datetime
from datacoolie_studio.domains.monitoring.metrics.row_normalization import (
    job_key as _job_key,
    job_shape_label as _job_shape_label,
)
from datacoolie_studio.domains.monitoring.metrics.investigation import (
    is_lakehouse_destination as _is_lakehouse_destination,
    watermark_classification as _watermark_classification,
)
from datacoolie_studio.domains.monitoring.metrics.freshness_projections import _freshness_page
from datacoolie_studio.domains.monitoring.metrics.volume_projections import _volume_page


_MISSING = object()


def _native_watermark_row(row: dict) -> dict:
    """Encode authored freshness evidence in the v6 nested record shape."""

    row = dict(row)
    loaded = row.pop("_v6_loaded", _MISSING)
    merge_base = row.pop("_v6_merge_base", _MISSING)
    candidate = row.pop("_v6_candidate", _MISSING)
    saved = row.pop("_v6_saved", _MISSING)
    to_save = row.pop("_v6_to_save", saved)
    selection = row.pop("_v6_selection", False)

    def decode(value):
        if value is _MISSING:
            return _MISSING
        if isinstance(value, str):
            return json.loads(value)
        return value

    loaded = decode(loaded)
    candidate = decode(candidate)
    saved = decode(saved)
    merge_base = decode(merge_base)
    to_save = decode(to_save)
    configured = selection or any(value is not _MISSING for value in (loaded, candidate, saved, to_save))
    if configured:
        watermark = {}
        for key, value in (
            ("loaded_watermark", loaded),
            ("merge_base", merge_base),
            ("watermark_to_save", to_save),
            ("saved_watermark", saved),
        ):
            if value is not _MISSING:
                watermark[key] = value
        if candidate is not _MISSING:
            watermark["candidate"] = {"values": candidate, "kind": "observed_max"}
        if saved is not _MISSING:
            watermark["save_status"] = "saved"
        record = {
            "log_schema_version": 6,
            "read_request": {"read_range": {"column": "updated_at"}} if selection else None,
            "attempts": [{
                "attempt_number": 1,
                "status": row.get("status"),
                "source": {"status": row.get("status"), "selection": {"mode": "range"}} if selection else None,
                "watermark": watermark,
            }],
        }
    else:
        record = {"log_schema_version": 6, "read_request": None, "attempts": []}
    row["log_schema_version"] = 6
    row.update(record)
    return row


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
    volume = _volume_page(rows)

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
    assert registry_row["volume_rows_written"] == 10
    assert registry_row["volume_net_bytes"] == 230

def test_lakehouse_destination_requires_declared_metadata():
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
    assert not _is_lakehouse_destination({
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

    volume = _volume_page(rows)

    assert len(volume["dataflow_registry"]) == 2
    orders = next(row for row in volume["dataflow_registry"] if row["dataflow_id"] == "df-orders")
    assert orders["run_count"] == 2
    assert orders["volume_rows_read"] == 300
    # The rows omit declared destination connection/format metadata, so they
    # cannot be counted in the declared lakehouse subset.
    assert orders["volume_lakehouse_rows_written"] == 0
    assert orders["volume_files_changed"] is None
    assert orders["volume_net_bytes"] is None
    assert orders["peak_rows_read"] == 200
    assert orders["p95_rows_read"] > 0
    assert orders["volume_candidate_signals"]
    assert volume["kpis"]["high_volume_dataflow_count"] == 1
    assert volume["kpis"]["high_volume_candidate_run_count"] > 0
    assert "investigation_queue" not in volume

def test_volume_registry_handles_empty_population():
    volume = _volume_page([])

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
                "_v6_loaded": '{"__datetime__":"2026-06-13T00:00:00Z"}',
                "_v6_merge_base": '{"__datetime__":"2026-06-13T00:00:00Z"}',
                "_v6_candidate": '{"__datetime__":"2026-06-14T00:00:00Z"}',
                "_v6_saved": '{"__datetime__":"2026-06-14T00:00:00Z"}',
                "_v6_selection": True,
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
                "_v6_candidate": '{"__datetime__":"2026-06-14T03:00:00Z"}',
                "_v6_saved": '{"__datetime__":"2026-06-14T03:00:00Z"}',
                "_v6_merge_base": None,
                "_v6_selection": True,
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
                "_v6_loaded": '{"__datetime__":"2026-06-14T00:00:00Z"}',
                "_v6_merge_base": '{"__datetime__":"2026-06-14T00:00:00Z"}',
                "_v6_candidate": '{"__datetime__":"2026-06-14T00:00:00Z"}',
                "_v6_saved": '{"__datetime__":"2026-06-13T00:00:00Z"}',
                "_v6_selection": True,
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
                        "_v6_loaded": '{"__datetime__":"2026-06-14T00:00:00Z"}',
                    "_v6_merge_base": '{"__datetime__":"2026-06-14T00:00:00Z"}',
                "_v6_selection": True,
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
                        "_v6_loaded": '{"__datetime__":"2026-06-14T00:00:00Z"}',
                        "_v6_merge_base": '{"__datetime__":"2026-06-14T00:00:00Z"}',
                    "_v6_saved": '{"__datetime__":"2026-06-14T00:00:00Z"}',
                "_v6_selection": True,
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
    rows = [_native_watermark_row(row) for row in rows]

    freshness = _freshness_page(rows)

    assert freshness["kpis"]["latest_successful_runs"] == 6
    assert freshness["kpis"]["successful_runs"] == 6
    assert freshness["kpis"]["failed_runs"] == 0
    assert freshness["kpis"]["skipped_runs"] == 4
    assert freshness["kpis"]["observed_dataflows"] == 8
    assert freshness["kpis"]["freshness_runs"] == 5
    assert freshness["kpis"]["dataflows_with_freshness_evidence"] == 5
    assert freshness["kpis"]["latest_status_issue_dataflows"] == 0
    assert freshness["kpis"]["latest_watermark_unknown_dataflows"] == 1
    assert freshness["kpis"]["latest_watermark_changed_dataflows"] == 1
    assert freshness["kpis"]["watermark_enabled_dataflows"] == 5
    assert freshness["kpis"]["watermark_advanced_runs"] == 1
    assert freshness["kpis"]["watermark_initialized_runs"] == 1
    assert freshness["kpis"]["watermark_unchanged_runs"] == 1
    assert freshness["kpis"]["watermark_changed_runs"] == 1
    assert freshness["kpis"]["watermark_unknown_runs"] == 1
    assert freshness["kpis"]["watermark_advanced_rate"] == 10.0
    assert freshness["kpis"]["watermark_rate_runs"] == 10
    assert freshness["kpis"]["skipped_no_new_data"] == 4
    assert freshness["kpis"]["skipped_streak_threshold"] == 3
    assert freshness["kpis"]["skipped_streak_dataflows"] == 1
    assert freshness["watermark_movement"][0]["movement"] == "advanced"
    assert freshness["watermark_movement"][1]["movement"] == "initialized"
    # The trend retains all ETL runs as its denominator, so the earliest
    # skipped-only buckets precede the movement bucket in date order.
    movement_day = next(
        row for row in freshness["watermark_movement_by_date"]
        if str(row["date"]).startswith("2026-06-14")
    )
    assert movement_day["advanced"] == 1
    assert movement_day["initialized"] == 1
    assert movement_day["unchanged"] == 1
    assert movement_day["changed"] == 1
    assert movement_day["unknown"] == 1
    assert freshness["watermark_coverage_by_stage"][0]["missing"] >= 1
    assert freshness["watermark_coverage_by_stage"][0]["not_configured"] >= 1
    assert {row["dataflow_name"] for row in freshness["latest_freshness_by_dataflow"]} == {"orders", "products", "adjusted_orders", "inventory", "api_no_new_watermark"}
    customers_freshness = next(row for row in freshness["dataflow_registry"] if row["dataflow_name"] == "customers")
    assert customers_freshness["latest_freshness_status"] is None
    assert customers_freshness["latest_run_status"] == "skipped"
    assert freshness["skipped_patterns"][0]["consecutive_skipped"] == 3
    assert freshness["skipped_streak_distribution"][0]["dataflows"] == 1
    assert len(freshness["dataflow_registry"]) == 8
    assert next(row for row in freshness["dataflow_registry"] if row["dataflow_name"] == "customers")["skipped_streak"] == 3
    assert next(row for row in freshness["dataflow_registry"] if row["dataflow_name"] == "customers")["latest_freshness_status"] is None
    assert next(row for row in freshness["dataflow_registry"] if row["dataflow_name"] == "products")["movement_state"] == "initialized"
    adjusted = next(row for row in freshness["dataflow_registry"] if row["dataflow_name"] == "adjusted_orders")
    assert adjusted["movement_state"] == "changed"
    assert "adjustment_state" not in adjusted
    no_new_watermark = next(row for row in freshness["dataflow_registry"] if row["dataflow_name"] == "api_no_new_watermark")
    assert no_new_watermark["movement_state"] == "unchanged"
    assert "adjustment_state" not in no_new_watermark
    assert next(row for row in freshness["dataflow_registry"] if row["dataflow_name"] == "inventory")["movement_state"] == "unknown"
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
            "_v6_candidate": '{"updated_at":"2026-06-14T00:00:00Z"}',
            "_v6_selection": True,
        },
        {
            "dataflow_id": "d2",
            "dataflow_name": "orders_copy_to_lake",
            "operation_type": "etl",
            "status": "succeeded",
            "end_time": "2026-06-14T01:00:00+00:00",
            "destination_full_table": "lake.sales.orders",
            "_v6_candidate": '{"updated_at":"2026-06-14T01:00:00Z"}',
            "_v6_selection": True,
        },
        {
            "dataflow_name": "missing_id_should_not_group",
            "operation_type": "etl",
            "status": "succeeded",
            "end_time": "2026-06-14T02:00:00+00:00",
            "destination_full_table": "lake.sales.orders",
            "_v6_candidate": '{"updated_at":"2026-06-14T02:00:00Z"}',
            "_v6_selection": True,
        },
    ]
    rows = [_native_watermark_row(row) for row in rows]

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

def test_watermark_movement_uses_persistence_evidence_separately_from_status():
    skipped = _watermark_classification(_native_watermark_row({
        "status": "skipped",
        "_v6_loaded": '{"__datetime__":"2024-02-01T09:30:00Z"}',
        "_v6_merge_base": '{"__datetime__":"2024-02-01T09:30:00Z"}',
        "_v6_saved": '{"__datetime__":"2024-02-01T09:30:00Z"}',
        "_v6_selection": True,
    }))
    failed = _watermark_classification(_native_watermark_row({
        "status": "failed",
        "_v6_loaded": '{"__datetime__":"2024-02-01T09:30:00Z"}',
        "_v6_merge_base": '{"__datetime__":"2024-02-01T09:30:00Z"}',
        "_v6_saved": '{"__datetime__":"2024-02-02T09:30:00Z"}',
        "_v6_selection": True,
    }))
    pending = _watermark_classification(_native_watermark_row({
        "status": "pending",
        "_v6_loaded": '{"__datetime__":"2024-02-01T09:30:00Z"}',
        "_v6_merge_base": '{"__datetime__":"2024-02-01T09:30:00Z"}',
        "_v6_saved": '{"__datetime__":"2024-02-02T09:30:00Z"}',
        "_v6_selection": True,
    }))
    running = _watermark_classification(_native_watermark_row({
        "status": "running",
        "_v6_loaded": '{"__datetime__":"2024-02-01T09:30:00Z"}',
        "_v6_merge_base": '{"__datetime__":"2024-02-01T09:30:00Z"}',
        "_v6_saved": '{"__datetime__":"2024-02-02T09:30:00Z"}',
        "_v6_selection": True,
    }))

    assert skipped["movement_state"] == "unchanged"
    assert failed["movement_state"] == "advanced"
    assert pending["movement_state"] == "advanced"
    assert running["movement_state"] == "advanced"
    assert all(item["save_status"] == "saved" for item in (failed, pending, running))
    assert all("source_watermark_before" not in item for item in (failed, pending, running))

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
