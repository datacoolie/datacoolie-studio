from __future__ import annotations

from tests.support.v6_records import insert_native_rows

from datetime import datetime, timezone

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.monitoring import page_dispatch, query_service
from datacoolie_studio.domains.monitoring.read_models.overview import overview_read_model
from datacoolie_studio.domains.monitoring.read_models.volume import (
    volume_evidence_read_model,
    volume_read_model,
)
from datacoolie_studio.domains.monitoring.read_models.freshness import (
    freshness_evidence_read_model,
    freshness_read_model,
)
from datacoolie_studio.domains.monitoring.read_models.performance import (
    performance_evidence_read_model,
    performance_read_model,
)
from datacoolie_studio.domains.monitoring.read_models.maintenance import (
    maintenance_evidence_read_model,
    maintenance_read_model,
)

from tests.domains.monitoring.monitoring_read_model_support import (
    _dataflow_metric_connection,
    _rebuild_serving_facts,
)

def test_overview_read_model_returns_only_bounded_visual_metrics() -> None:
    connection = _dataflow_metric_connection()

    result = overview_read_model(
        [],
        {},
        grain="day",
        timezone_name="UTC",
        now=datetime(2026, 7, 21, tzinfo=timezone.utc),
        analytics_context=(connection, [1], "generation-1"),
    )

    assert result["summary"]["job_records"] == 1
    assert result["summary"]["dataflow_records"] == 2
    assert result["summary"]["job_failed"] == 1
    assert result["summary"]["dataflow_failed"] == 1
    assert result["runtime_contexts"][0]["engine_name"] == "duckdb"
    assert result["dataflow_operation_health"][0]["operation_type"] == "etl"
    phases = result["phase_health"][0]
    assert phases["phase_runs_measured"] == phases["phase_runs_eligible"] == 2
    assert phases["source_duration_seconds"] == 30
    assert phases["transform_duration_seconds"] == 50
    assert result["error_categories"] == [{"category": "Schema / format", "count": 1}]
    assert sum(int(row["rows_written"]) for row in result["rows_by_date"]) == 90
    assert result["rows_by_date"][0]["bucket"] == "2026-07-20"
    assert result["bytes_by_date"][0]["bucket"] == "2026-07-20"
    assert result["health"]["mismatch_count"] == 0

def test_volume_read_model_keeps_measured_write_metric_consistent() -> None:
    connection = _dataflow_metric_connection()
    result = volume_read_model(
        [], {}, grain="day", timezone_name="UTC",
        analytics_context=(connection, [1], "generation-1"),
    )
    evidence = volume_evidence_read_model(
        [], {}, limit=100, offset=0,
        sort_by="volume_candidate_priority", sort_dir="desc",
        analytics_context=(connection, [1], "generation-1"),
    )

    assert result["summary"]["total_rows_read"] == 150
    assert result["summary"]["total_rows_written"] == 90
    assert sum(int(row["rows_written"]) for row in result["rows_by_date"]) == 90
    assert result["rows_by_date"][0]["bucket"] == "2026-07-20"
    from datacoolie_studio.domains.monitoring.dimensions import CONTEXT_PREFIX, UNKNOWN_KEY
    import json
    assert result["volume_by_workload_type"][0]["workload_type"] == CONTEXT_PREFIX + json.dumps(['etl', UNKNOWN_KEY], separators=(',', ':'))
    assert result["route_volume"][0]["runs"] == 2
    assert result["top_dataflows_by_rows_read"][0]["value"] == 150
    assert evidence["records"][0]["volume_rows_read"] == 150
    assert evidence["total_records"] == 1
    assert "dataflow_registry" not in result

def test_freshness_read_model_aggregates_at_dataflow_grain() -> None:
    connection = _dataflow_metric_connection(include_asset_metadata=True)
    result = freshness_read_model(
        [], {}, grain="day", timezone_name="UTC",
        analytics_context=(connection, [1], "generation-1"),
    )
    evidence = freshness_evidence_read_model(
        [], {}, limit=100, offset=0,
        sort_by="latest_freshness_at", sort_dir="desc",
        analytics_context=(connection, [1], "generation-1"),
    )

    assert result["summary"]["observed_dataflows"] == 1
    assert result["summary"]["successful_runs"] == 1
    assert result["summary"]["failed_runs"] == 1
    assert result["summary"]["watermark_enabled_dataflows"] == 1
    assert result["age_by_dataflow"][0]["dataflow_name"] == "orders"
    assert result["age_distribution"][0]["dataflows"] == 1
    assert result["watermark_coverage_by_stage"][0]["observed_dataflows"] == 1
    assert result["watermark_coverage_by_stage"][0]["watermark_enabled_dataflows"] == 1
    row = evidence["records"][0]
    assert row["dataflow_name"] == "orders"
    assert row["latest_run_status"] == "failed"
    assert row["latest_run_at"] is not None
    assert [item["status"] for item in row["last_statuses"]] == ["failed", "succeeded"]
    assert row["succeeded_count"] == 1
    assert row["failed_count"] == 1
    assert row["source_table"] == "raw.orders"
    assert row["source_full_table"] == "catalog.raw.orders"
    assert row["destination_table"] == "curated.orders"
    assert row["destination_path"] == "/lakehouse/curated/orders"
    assert row["dataflow_description"] == "Load orders"
    assert row["transform_select_columns"] == '["id", "email"]'
    assert row["transform_rename_columns"] == '{"email": "contact_email"}'
    assert row["transform_configure"] == '{"missing_column_policy": "ignore"}'

    registry = freshness_evidence_read_model(
        [], {}, limit=100, offset=0,
        sort_by="latest_freshness_at", sort_dir="desc",
        view="registry",
        analytics_context=(connection, [1], "generation-1"),
    )
    compact = registry["records"][0]
    assert "raw_record_json" not in compact
    assert "configure" not in compact
    for field in (
        "_source_id", "log_session_id", "dataflow_id", "latest_run_id",
        "dataflow_name", "stage", "source_name", "destination_name",
        "destination_load_type", "latest_run_status", "latest_freshness_at",
        "age_seconds", "age_days", "movement_state", "last_statuses",
        "loaded_watermark", "watermark_to_save", "candidate", "saved_watermark",
    ):
        if field == "age_seconds":
            assert abs(float(compact[field]) - float(row[field])) < 2
        elif field == "age_days":
            assert abs(float(compact[field]) - float(row[field])) < 0.0001
        else:
            assert compact.get(field) == row.get(field)

def test_freshness_read_model_tracks_the_actual_consecutive_skipped_streak() -> None:
    connection = _dataflow_metric_connection()
    skipped_rows = []
    for index in range(4):
        skipped_rows.append((
            f"skipped-{index}.parquet",
            "dataflow_parquet",
            "{}",
            {
                "job_id": "job-1",
                "dataflow_id": "dataflow-1",
                "dataflow_run_id": f"skipped-{index}",
                "dataflow_name": "orders",
                "stage": "silver",
                "operation_type": "etl",
                "source_name": "source",
                "destination_name": "destination",
                "status": "skipped",
                "start_time": f"2026-07-21T0{index}:00:00Z",
                "end_time": f"2026-07-21T0{index}:01:00Z",
            },
        ))
    insert_native_rows(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        1,
        skipped_rows,
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    _rebuild_serving_facts(connection)

    result = freshness_read_model(
        [], {}, grain="day", timezone_name="UTC",
        analytics_context=(connection, [1], "generation-1"),
    )
    evidence = freshness_evidence_read_model(
        [], {}, limit=100, offset=0,
        sort_by="latest_freshness_at", sort_dir="desc",
        analytics_context=(connection, [1], "generation-1"),
    )

    assert result["summary"]["skipped_streak_dataflows"] == 1
    assert result["skipped_streak_distribution"] == [{"bucket": "4–7", "dataflows": 1}]
    assert evidence["records"][0]["skipped_streak"] == 4

def test_performance_read_model_uses_executable_run_population() -> None:
    connection = _dataflow_metric_connection()
    result = performance_read_model(
        [], {}, grain="day", timezone_name="UTC",
        analytics_context=(connection, [1], "generation-1"),
    )
    evidence = performance_evidence_read_model(
        [], {}, limit=100, offset=0,
        sort_by="duration_seconds", sort_dir="desc",
        analytics_context=(connection, [1], "generation-1"),
    )

    assert result["summary"]["run_count"] == 2
    assert result["summary"]["p50_duration_seconds"] == 60
    assert result["summary"]["bottleneck_phase"] == "unknown"
    assert result["duration_distribution_by_stage"][0]["count"] == 2
    assert result["slowest_dataflow_profiles"][0]["p95_duration_seconds"] == 120
    assert result["summary"]["slowest_run_dataflow_run_id"] == "run-2"
    assert result["summary"]["slowest_run_operation_type"] == "etl"
    assert result["summary"]["slowest_run_start_time"] == "2026-07-20T01:00:00+00:00"
    assert result["summary"]["slowest_run_end_time"] == "2026-07-20T01:02:00+00:00"
    assert result["runtime_context_profiles"][0]["run_count"] == 2
    assert result["runtime_context_profiles"][0]["engine_name"] == "duckdb"
    assert result["performance_trend"][0]["run_count"] == 2
    assert evidence["records"][0]["performance_bottleneck_phase"] == "unknown"
    assert "destination_rows_inserted" in evidence["records"][0]
    assert "destination_bytes_saved" in evidence["records"][0]
    assert "slowest_dataflows" not in result
    assert "investigation_queue" not in result
    assert "duration_by_stage" not in result
    assert "slowest_dataflows_by_p95" not in result


def test_performance_efficiency_sample_preserves_maintenance_points() -> None:
    connection = _dataflow_metric_connection()
    connection.execute(f"DELETE FROM {analytics_schema.DATAFLOW_TABLE}")
    def pipeline_row(index: int):
        candidate = index < 205
        duration = 60 if candidate else 30
        source_duration = 10
        transform_duration = 20 if candidate else 10
        destination_duration = 20 if candidate else 5
        start = "2026-07-20T00:00:00Z"
        source_end = f"2026-07-20T00:00:{source_duration:02d}Z"
        transform_end_seconds = source_duration + transform_duration
        transform_end = f"2026-07-20T00:00:{transform_end_seconds:02d}Z"
        destination_end_seconds = transform_end_seconds + destination_duration
        destination_end = f"2026-07-20T00:00:{destination_end_seconds:02d}Z"
        attempt_end = "2026-07-20T00:01:00Z" if candidate else "2026-07-20T00:00:30Z"
        return (
            f"pipeline-{index}.parquet",
            "dataflow_parquet",
            "{}",
            {
                "job_id": "job-1",
                "dataflow_id": f"pipeline-{index}",
                "dataflow_run_id": f"pipeline-run-{index}",
                "dataflow_name": f"pipeline-{index}",
                "stage": "silver",
                "operation_type": "etl",
                "status": "succeeded",
                "start_time": "2026-07-20T00:00:00Z",
                "end_time": "2026-07-20T00:01:00Z",
                "duration_seconds": duration,
                "source_status": "succeeded",
                "source_duration_seconds": source_duration,
                "source_rows_read": 100 if candidate else 10_000,
                "transform_status": "succeeded",
                "transform_duration_seconds": transform_duration,
                "destination_status": "succeeded",
                "destination_duration_seconds": destination_duration,
                "destination_rows_written": 100 if candidate else 10_000,
                "attempts": [{
                    "attempt_number": 1,
                    "start_time": start,
                    "end_time": attempt_end,
                    "duration_seconds": duration,
                    "status": "succeeded",
                    "source": {"start_time": start, "end_time": source_end, "duration_seconds": source_duration, "status": "succeeded"},
                    "transform": {"start_time": source_end, "end_time": transform_end, "duration_seconds": transform_duration, "status": "succeeded"},
                    "destination": {"start_time": transform_end, "end_time": destination_end, "duration_seconds": destination_duration, "status": "succeeded"},
                }],
            },
        )

    pipeline_rows = [pipeline_row(index) for index in range(255)]

    def maintenance_row(index: int, *, measured_bytes: bool):
        fields = {
            "job_id": "job-1",
            "dataflow_id": f"maintenance-flow-{index}",
            "dataflow_run_id": f"maintenance-run-{index}",
            "dataflow_name": f"maintenance-flow-{index}",
            "stage": "silver",
            "operation_type": "maintenance",
            "status": "succeeded",
            "start_time": "2026-07-19T00:00:00Z",
            "end_time": "2026-07-19T00:00:01Z",
            "duration_seconds": 1,
            "destination_files_removed": 0,
            "attempts": [{
                "attempt_number": 1,
                "start_time": "2026-07-19T00:00:00Z",
                "end_time": "2026-07-19T00:00:01Z",
                "duration_seconds": 1,
                "status": "succeeded",
                "source": None,
                "transform": None,
                "destination": {"start_time": "2026-07-19T00:00:00Z", "end_time": "2026-07-19T00:00:01Z", "duration_seconds": 1, "status": "succeeded"},
            }],
        }
        if measured_bytes:
            fields.update({
                "destination_bytes_added": 0,
                "destination_bytes_removed": 0,
                "destination_bytes_saved": 0,
            })
        else:
            fields.update({
                "destination_bytes_added": None,
                "destination_bytes_removed": None,
                "destination_bytes_saved": None,
            })
        return (f"maintenance-{index}.parquet", "dataflow_parquet", "{}", fields)

    insert_native_rows(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        1,
        [*pipeline_rows, maintenance_row(0, measured_bytes=True), maintenance_row(1, measured_bytes=False)],
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    _rebuild_serving_facts(connection)

    result = performance_read_model(
        [], {}, grain="day", timezone_name="UTC",
        analytics_context=(connection, [1], "generation-1"),
    )

    points = result["workload_efficiency_points"]
    pipeline_points = [point for point in points if point[3] != "maintenance"]
    maintenance_points = [point for point in points if point[3] == "maintenance"]
    assert len(points) == 201
    assert len(pipeline_points) == 200
    assert sum(int(point[13] or 0) > 0 for point in pipeline_points) == 150
    assert sum(int(point[13] or 0) == 0 for point in pipeline_points) == 50
    assert maintenance_points == [next(point for point in points if point[0] == "maintenance-run-0")]
    assert maintenance_points[0][6] == 0

    summary = result["summary"]
    assert summary["workload_efficiency_pipeline_eligible"] == 255
    assert summary["workload_efficiency_pipeline_measured"] == 255
    assert summary["workload_efficiency_maintenance_eligible"] == 2
    assert summary["workload_efficiency_maintenance_measured"] == 1

def test_maintenance_read_model_returns_empty_evidence_without_maintenance_runs() -> None:
    result = maintenance_read_model(
        [], {}, grain="day", timezone_name="UTC",
        analytics_context=(_dataflow_metric_connection(), [1], "generation-1"),
    )

    assert result["summary"]["total_maintenance_runs"] == 0
    assert result["summary"]["coverage_missing_tables"] == 0
    assert result["table_outcome"] == []

def test_maintenance_read_model_returns_wide_trends_and_registry_drawer_evidence() -> None:
    connection = _dataflow_metric_connection(include_asset_metadata=True)
    insert_native_rows(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        1,
        [(
            "maintenance.parquet",
            "dataflow_parquet",
            "{}",
            {
                "job_id": "job-2",
                "dataflow_id": "maintenance-flow",
                "dataflow_run_id": "maintenance-run-1",
                "dataflow_name": "compact_orders",
                "stage": "silver",
                "operation_type": "maintenance",
                "status": "succeeded",
                "start_time": "2026-07-21T00:00:00Z",
                "end_time": "2026-07-21T00:02:00Z",
                "duration_seconds": 120,
                "destination_name": "destination",
                "destination_connection_type": "lakehouse",
                "destination_format": "delta",
                "destination_table": "curated.orders",
                "destination_bytes_removed": 4096,
                "destination_bytes_saved": 1024,
                "destination_files_removed": 2,
            },
        )],
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    _rebuild_serving_facts(connection)

    result = maintenance_read_model(
        [], {}, grain="day", timezone_name="UTC",
        analytics_context=(connection, [1], "generation-1"),
    )
    evidence = maintenance_evidence_read_model(
        [], {}, limit=100, offset=0,
        sort_by="attention_priority", sort_dir="desc",
        analytics_context=(connection, [1], "generation-1"),
    )

    assert result["status_by_date"] == [{
        "date": "2026-07-21T00:00:00+00:00",
        "bucket": "2026-07-21T00:00:00+00:00",
        "bucket_start": "2026-07-21T00:00:00+00:00",
        "bucket_end": None,
        "grain": "day",
        "succeeded": 1,
        "failed": 0,
        "skipped": 0,
        "running": 0,
        "pending": 0,
        "unknown": 0,
        "total": 1,
        "success_rate": 100.0,
    }]
    assert result["reclaim_by_date"][0]["bytes_reclaimed"] == 4096
    assert result["reclaim_by_date"][0]["files_removed"] == 2
    registry = next(
        row for row in evidence["records"] if row["latest_status"] == "succeeded"
    )
    assert registry["destination_table"] == "curated.orders"
    assert registry["latest_status"] == "succeeded"
    assert registry["bytes_reclaimed"] == 4096
    assert registry["upstream_run_count"] == 0
    assert registry["upstream_dataflows"] == []

def test_all_monitoring_pages_bypass_legacy_fact_materialization() -> None:
    connection = _dataflow_metric_connection()
    paths = [EnvironmentSource(
        id=1, environment_id=1, source_kind="logs", uri="test://monitoring/1", enabled=True,
    )]

    assert not hasattr(query_service, "_monitoring_rows")
    for page in page_dispatch.MONITORING_PAGES:
        result = page_dispatch.build_monitoring_page(
            paths,
            page,
            filters={"range": "all"},
            timezone_info=timezone.utc,
            timezone_label="UTC",
            timezone_source="configured",
            analytics_context=(connection, [1], "generation-1"),
        )
        assert result["summary"]["dataflow_records"] == 2
