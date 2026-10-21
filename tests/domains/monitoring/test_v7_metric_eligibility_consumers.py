from __future__ import annotations

from datacoolie_studio.domains.monitoring.metrics.aggregates import sum_observed
from datacoolie_studio.domains.monitoring.metrics.performance_projections import (
    _performance_page,
)
from datacoolie_studio.domains.monitoring.metrics.volume_projections import _volume_page


def test_observed_mode_sums_known_values_without_applicability_markers() -> None:
    rows = [
        {"destination_rows_written": 100},
        {"destination_rows_written": None},
    ]

    assert sum_observed(rows, "destination_rows_written", mode="observed") == 100
    assert sum_observed(
        [{"destination_rows_written": 0}, {"destination_rows_written": None}],
        "destination_rows_written",
        mode="observed",
    ) == 0
    assert sum_observed(
        [{"destination_rows_written": None}],
        "destination_rows_written",
        mode="observed",
    ) is None
    assert sum_observed([], "destination_rows_written", mode="observed") is None


def test_v6_mode_preserves_complete_and_empty_aggregate_semantics() -> None:
    assert sum_observed(
        [{"destination_rows_written": 100}, {"destination_rows_written": None}],
        "destination_rows_written",
        mode="v6",
    ) is None
    assert sum_observed([], "destination_rows_written", mode="v6") == 0


def test_observed_performance_total_keeps_metric_with_unknown_duration() -> None:
    rows = [
        {
            "dataflow_id": "known",
            "dataflow_run_id": "known-run",
            "operation_type": "etl",
            "status": "succeeded",
            "duration_seconds": 10,
            "source_rows_read": 100,
        },
        {
            "dataflow_id": "unknown-duration",
            "dataflow_run_id": "unknown-duration-run",
            "operation_type": "etl",
            "status": "succeeded",
            "duration_seconds": None,
            "source_rows_read": 50,
        },
    ]

    summary = _performance_page(rows, version_mode="observed")["kpis"]

    assert summary["total_rows_read"] == 150
    assert summary["rows_read_per_second"] is None


def test_status_counters_keep_zero_for_an_empty_observed_population() -> None:
    from datacoolie_studio.domains.monitoring.metrics.overview_projections import (
        _operations_page,
    )

    summary = _operations_page([], [], version_mode="observed")["kpis"]

    assert summary["total_running"] == 0
    assert summary["total_pending"] == 0


def test_observed_volume_mode_reaches_nested_route_top_and_registry_totals() -> None:
    rows = [
        {
            "_source_id": 1,
            "dataflow_id": "flow",
            "dataflow_name": "flow",
            "source_name": "source",
            "destination_name": "destination",
            "source_rows_read": 100,
            "destination_rows_written": 100,
            "destination_bytes_added": 200,
            "destination_bytes_removed": 100,
            "end_time": "2026-10-11T00:00:00+00:00",
        },
        {
            "_source_id": 1,
            "dataflow_id": "flow",
            "dataflow_name": "flow",
            "source_name": "source",
            "destination_name": "destination",
            "source_rows_read": None,
            "destination_rows_written": None,
            "end_time": "2026-10-11T00:01:00+00:00",
        },
    ]

    page = _volume_page(rows, version_mode="observed")

    assert page["kpis"]["total_rows_read"] == 100
    assert page["rows_by_date"][0]["rows_read"] == 100
    assert page["bytes_by_date"][0]["bytes_added"] == 200
    assert page["bytes_by_date"][0]["net_bytes"] == 100
    assert page["volume_by_load_type"][0]["rows_written"] == 100
    assert page["volume_by_workload_type"][0]["rows_read"] == 100
    assert page["route_volume"][0]["rows_read"] == 100
    assert page["top_dataflows_by_rows_read"][0]["value"] == 100
    assert page["top_dataflows_by_net_bytes"][0]["value"] == 100
    assert page["dataflow_registry"][0]["volume_rows_read"] == 100


def test_observed_volume_empty_non_lakehouse_subset_stays_unknown() -> None:
    row = {
        "_source_id": 1,
        "dataflow_id": "lakehouse-only",
        "dataflow_name": "lakehouse-only",
        "source_name": "source",
        "destination_name": "destination",
        "destination_format": "delta",
        "destination_rows_written": 100,
        "end_time": "2026-10-11T00:00:00+00:00",
    }

    page = _volume_page([row], version_mode="observed")

    assert page["kpis"]["total_lakehouse_rows_written"] == 100
    assert page["kpis"]["total_non_lakehouse_rows_written"] is None
