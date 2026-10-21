from __future__ import annotations

from tests.support.v6_records import insert_native_rows

import duckdb

from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.analytics import schema_tables as analytics_schema_tables
from datacoolie_studio.domains.analytics.serving_facts import rebuild_monitoring_serving_facts
from datacoolie_studio.domains.monitoring.metrics.v6_semantics import register_freshness_functions


_V6_SUCCESS_EVIDENCE = (
    '{"log_schema_version":6,"read_request":{"read_range":{"column":"updated_at"}},'
    '"attempts":[{"attempt_number":1,"status":"succeeded",'
    '"start_time":"2026-07-20T00:00:00Z","end_time":"2026-07-20T00:01:00Z",'
    '"source":{"status":"succeeded","start_time":"2026-07-20T00:00:00Z",'
    '"end_time":"2026-07-20T00:00:10Z","selection":{"mode":"range"}},'
    '"transform":{"status":"succeeded","start_time":"2026-07-20T00:00:10Z",'
    '"end_time":"2026-07-20T00:00:30Z"},'
    '"destination":{"status":"succeeded","start_time":"2026-07-20T00:00:30Z",'
    '"end_time":"2026-07-20T00:00:50Z"},'
    '"watermark":{"loaded_watermark":{"updated_at":"2026-07-19T00:00:00Z"},'
    '"candidate":{"values":{"updated_at":"2026-07-20T00:00:00Z"},"kind":"observed_max"},'
    '"watermark_to_save":{"updated_at":"2026-07-20T00:00:00Z"},'
    '"save_status":"saved","saved_watermark":{"updated_at":"2026-07-20T00:00:00Z"}}}]} '
).strip()

_V6_FAILED_EVIDENCE = (
    '{"log_schema_version":6,"attempts":[{"attempt_number":1,"status":"failed",'
    '"start_time":"2026-07-20T01:00:00Z","end_time":"2026-07-20T01:02:00Z",'
    '"source":{"status":"succeeded","start_time":"2026-07-20T01:00:00Z",'
    '"end_time":"2026-07-20T01:00:20Z"},'
    '"transform":{"status":"failed","message":"column type mismatch",'
    '"start_time":"2026-07-20T01:00:20Z","end_time":"2026-07-20T01:00:50Z"},'
    '"destination":{"status":"pending","start_time":"2026-07-20T01:00:50Z",'
    '"end_time":"2026-07-20T01:01:30Z"},'
    '"message":"column type mismatch"}]}'
)


def _dataflow_metric_connection(*, include_asset_metadata: bool = False):
    connection = duckdb.connect(":memory:")
    # Supplied read-model contexts bypass Analytics' reader owner. Prepare the
    # shared scalar-function catalog while the connection is still cold so
    # later snapshot/read-model calls never mutate an active transaction.
    register_freshness_functions(connection)
    analytics_schema_tables.ensure_tables(connection)
    analytics_schema_tables.ensure_typed_table(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    analytics_schema_tables.ensure_typed_table(
        connection,
        analytics_schema.JOB_TABLE,
        analytics_schema.JOB_COLUMN_TYPES,
    )
    insert_native_rows(
        connection,
        analytics_schema.JOB_TABLE,
        1,
        [
            (
                "job.jsonl",
                "job_jsonl",
                "{}",
                {
                    "job_id": "job-1",
                    "status": "failed",
                    "end_time": "2026-07-20T01:00:00Z",
                    "duration_seconds": 180,
                    "engine_name": "duckdb",
                    "metadata_provider_name": "file",
                    "platform_name": "local",
                    "operation_types": '["etl", "maintenance"]',
                    "total_dataflows": 2,
                    "total_succeeded": 1,
                    "total_failed": 1,
                    "total_skipped": 0,
                    "observed_at": "2026-07-20T01:03:00Z",
                    "log_records_dropped": 0,
                    "log_bytes_dropped": 0,
                },
            )
        ],
        analytics_schema.JOB_COLUMN_TYPES,
    )
    common = {
        "destination_load_type": None, "destination_full_table": None, "destination_table": None, "destination_path": None,
        "destination_connection_type": None, "destination_format": None,
        "job_id": "job-1",
        "dataflow_id": "dataflow-1",
        "dataflow_name": "orders",
        "stage": "silver",
        "operation_type": "etl",
        "source_name": "source",
        "destination_name": "destination",
    }
    if include_asset_metadata:
        common.update({
            "dataflow_description": "Load orders",
            "source_format": "delta",
            "source_table": "raw.orders",
            "source_full_table": "catalog.raw.orders",
            "destination_format": "delta",
            "destination_table": "curated.orders",
            "destination_path": "/lakehouse/curated/orders",
            "transform_select_columns": '["id", "email"]',
            "transform_drop_columns": "[]",
            "transform_rename_columns": '{"email": "contact_email"}',
            "transform_value_rules": '[{"operation": "trim", "columns": ["email"]}]',
            "transform_hash_columns": '[{"target_column": "email_hash", "columns": ["email"]}]',
            "transform_masking_rules": '[{"method": "redact", "columns": ["email"]}]',
            "transform_configure": '{"missing_column_policy": "ignore"}',
        })
    insert_native_rows(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        1,
        [
            (
                "success.parquet",
                "dataflow_parquet",
                _V6_SUCCESS_EVIDENCE,
                {
                    **common,
                    "dataflow_run_id": "run-1",
                    "log_schema_version": 6,
                    "raw_record_json": _V6_SUCCESS_EVIDENCE,
                    "status": "succeeded",
                    "start_time": "2026-07-20T00:00:00Z",
                    "end_time": "2026-07-20T00:01:00Z",
                    "duration_seconds": 60,
                    "source_status": "succeeded",
                    "source_duration_seconds": 10,
                    "transform_status": "succeeded",
                    "transform_duration_seconds": 20,
                    "destination_status": "succeeded",
                    "destination_duration_seconds": 20,
                    "source_rows_read": 100,
                    "destination_rows_written": 90,
                },
            ),
            (
                "failed.parquet",
                "dataflow_parquet",
                _V6_FAILED_EVIDENCE,
                {
                    **common,
                    "dataflow_run_id": "run-2",
                    "log_schema_version": 6,
                    "raw_record_json": _V6_FAILED_EVIDENCE,
                    "status": "failed",
                    "start_time": "2026-07-20T01:00:00Z",
                    "end_time": "2026-07-20T01:02:00Z",
                    "duration_seconds": 120,
                    "source_status": "succeeded",
                    "source_duration_seconds": 20,
                    "transform_status": "failed",
                    "transform_message": "column type mismatch",
                    "transform_duration_seconds": 30,
                    "destination_status": "pending",
                    "destination_duration_seconds": 40,
                    "source_rows_read": 50,
                    "destination_rows_written": 0,
                },
            ),
        ],
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    _rebuild_serving_facts(connection)
    return connection


def _rebuild_serving_facts(connection) -> None:
    rebuild_monitoring_serving_facts(
        connection,
        dataflow_table=analytics_schema.DATAFLOW_TABLE,
        job_table=analytics_schema.JOB_TABLE,
        dataflow_column_types=analytics_schema.cache_table_column_types(analytics_schema.DATAFLOW_COLUMN_TYPES),
        job_column_types=analytics_schema.cache_table_column_types(analytics_schema.JOB_COLUMN_TYPES),
    )
