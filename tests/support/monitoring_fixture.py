from __future__ import annotations

from datetime import datetime
from pathlib import Path

import duckdb

from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.analytics import publication as analytics_publication
from datacoolie_studio.domains.analytics import schema_tables as analytics_schema_tables


def build_analytics_fixture(
    path: Path,
    *,
    source_ids: list[int],
    dataflow_rows: int,
) -> dict[str, int]:
    """Create a published native analytics generation from deterministic scenario rows."""
    if not source_ids:
        raise ValueError("At least one source id is required")
    if dataflow_rows < 0:
        raise ValueError("dataflow_rows must be non-negative")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    job_rows = dataflow_rows // 5
    source_values = ", ".join(f"({int(source_id)})" for source_id in source_ids)
    connection = duckdb.connect(str(path))
    try:
        analytics_schema_tables.ensure_typed_table(connection, analytics_schema.DATAFLOW_TABLE, analytics_schema.DATAFLOW_COLUMN_TYPES)
        analytics_schema_tables.ensure_typed_table(connection, analytics_schema.JOB_TABLE, analytics_schema.JOB_COLUMN_TYPES)
        analytics_schema.ensure_filter_values_table(connection)
        analytics_schema.ensure_cache_sources_table(connection)
        analytics_schema.ensure_analytics_meta_table(connection)
        from datacoolie_studio.domains.analytics import store
        from tests.support.v6_records import native_record
        from datacoolie_studio.domains.analytics.sql import quote_identifier
        for table, types in (("fixture_jobs", analytics_schema.HISTORICAL_JOB_COLUMN_TYPES), ("fixture_dataflows", analytics_schema.HISTORICAL_DATAFLOW_COLUMN_TYPES)):
            columns = analytics_schema.cache_table_column_types(types)
            connection.execute(f"CREATE TEMP TABLE {table} ({', '.join(quote_identifier(k) + ' ' + v for k, v in columns.items())})")
        connection.execute(
            f"""
            INSERT INTO fixture_jobs (
              _source_id, _file_uri, _file_kind, _file_date, _source_size,
              _source_mtime_ns, _ingested_at, _type, job_id, platform_name,
              engine_name, metadata_provider_name, start_time, end_time,
              duration_seconds, status, stages, operation_types, total_dataflows,
              total_succeeded, total_failed, total_skipped, total_running,
              total_pending, total_rows_read, total_rows_written,
              __event_time, __run_date
            )
            SELECT
              source_ids[(index % {len(source_ids)}) + 1],
              CONCAT('benchmark://job/', index), 'job',
              CAST(TIMESTAMPTZ '2026-07-21 00:00:00+00' - index * INTERVAL 1 MINUTE AS DATE),
              1024, index, TIMESTAMPTZ '2026-07-21 00:00:00+00', 'job',
              CONCAT('job-', index), 'LocalPlatform',
              CASE index % 2 WHEN 0 THEN 'DuckDBEngine' ELSE 'SparkEngine' END,
              'FileProvider',
              CAST(TIMESTAMPTZ '2026-07-21 00:00:00+00' - index * INTERVAL 1 MINUTE - INTERVAL 30 SECOND AS VARCHAR),
              CAST(TIMESTAMPTZ '2026-07-21 00:00:00+00' - index * INTERVAL 1 MINUTE AS VARCHAR),
              30 + index % 120,
              CASE index % 10 WHEN 0 THEN 'failed' WHEN 1 THEN 'skipped' ELSE 'succeeded' END,
              'extract|load', 'etl', 5,
              CASE WHEN index % 10 IN (0, 1) THEN 4 ELSE 5 END,
              CASE WHEN index % 10 = 0 THEN 1 ELSE 0 END,
              CASE WHEN index % 10 = 1 THEN 1 ELSE 0 END,
              0, 0, 5000 + index * 10, 4500 + index * 10,
              TIMESTAMPTZ '2026-07-21 00:00:00+00' - index * INTERVAL 1 MINUTE,
              CAST(TIMESTAMPTZ '2026-07-21 00:00:00+00' - index * INTERVAL 1 MINUTE AS DATE)
            FROM range({job_rows}) generated(index)
            CROSS JOIN (SELECT list(source_id ORDER BY source_id) AS source_ids FROM (VALUES {source_values}) sources(source_id)) ids
            """
        )
        connection.execute(
            f"""
            INSERT INTO fixture_dataflows (
              _source_id, _file_uri, _file_kind, _file_date, _source_size,
              _source_mtime_ns, _ingested_at, _type, job_id, job_num, job_index,
              dataflow_id, dataflow_run_id, dataflow_name, stage, operation_type, status,
              start_time, end_time, duration_seconds, source_name,
              source_connection_type, source_format, source_table,
              source_status, source_duration_seconds, source_rows_read,
              transform_status, transform_duration_seconds, destination_name,
              destination_connection_type, destination_format, destination_table,
              destination_load_type, destination_status,
              destination_duration_seconds, destination_rows_written,
              destination_rows_inserted, destination_rows_updated,
              destination_rows_deleted, destination_bytes_added,
              destination_bytes_removed, destination_bytes_saved,
              destination_files_added, destination_files_removed,
              overhead_duration_seconds, error_message, __event_time, __run_date
            )
            SELECT
              source_ids[(index % {len(source_ids)}) + 1],
              CONCAT('benchmark://dataflow/', index), 'dataflow',
              CAST(TIMESTAMPTZ '2026-07-21 00:00:00+00' - index * INTERVAL 12 SECOND AS DATE),
              2048, index, TIMESTAMPTZ '2026-07-21 00:00:00+00', 'dataflow',
              CONCAT('job-', index // 5), index // 5, index,
              CONCAT('dataflow-', index % 130), CONCAT('run-', index), CONCAT('dataflow-', index % 130),
              CASE index % 5 WHEN 0 THEN 'extract' WHEN 1 THEN 'transform' WHEN 2 THEN 'load' WHEN 3 THEN 'quality' ELSE 'publish' END,
              CASE index % 20 WHEN 0 THEN 'maintenance' ELSE 'etl' END,
              CASE index % 20 WHEN 0 THEN 'failed' WHEN 1 THEN 'skipped' ELSE 'succeeded' END,
              TIMESTAMPTZ '2026-07-21 00:00:00+00' - index * INTERVAL 12 SECOND - (10 + index % 300) * INTERVAL 1 SECOND,
              TIMESTAMPTZ '2026-07-21 00:00:00+00' - index * INTERVAL 12 SECOND,
              10 + index % 300, CONCAT('source-', index % 12), 'database', 'parquet',
              CONCAT('source_table_', index % 20),
              CASE WHEN index % 20 = 0 THEN 'failed' ELSE 'succeeded' END,
              2 + index % 30, 1000 + index % 10000,
              'succeeded', 3 + index % 40, CONCAT('destination-', index % 9),
              CASE index % 3 WHEN 0 THEN 'lakehouse' ELSE 'database' END,
              CASE index % 3 WHEN 0 THEN 'delta' ELSE 'table' END,
              CONCAT('destination_table_', index % 25),
              CASE index % 4 WHEN 0 THEN 'merge' ELSE 'append' END,
              CASE WHEN index % 20 = 0 THEN 'failed' ELSE 'succeeded' END,
              4 + index % 50,
              CASE WHEN index % 3 = 0 THEN 900 + index % 9000 ELSE NULL END,
              800 + index % 8000, index % 100, index % 10,
              4096 + index % 100000, index % 2048, index % 1024,
              1 + index % 8, index % 3, 1 + index % 20,
              CASE WHEN index % 20 = 0 THEN 'schema mismatch' ELSE NULL END,
              TIMESTAMPTZ '2026-07-21 00:00:00+00' - index * INTERVAL 12 SECOND,
              CAST(TIMESTAMPTZ '2026-07-21 00:00:00+00' - index * INTERVAL 12 SECOND AS DATE)
            FROM range({dataflow_rows}) generated(index)
            CROSS JOIN (SELECT list(source_id ORDER BY source_id) AS source_ids FROM (VALUES {source_values}) sources(source_id)) ids
            """
        )
        for fixture, table, kind in (("fixture_jobs", analytics_schema.JOB_TABLE, "job_run_log"), ("fixture_dataflows", analytics_schema.DATAFLOW_TABLE, "dataflow_run_log")):
            cursor = connection.execute(f"SELECT * FROM {fixture}")
            names = [column[0] for column in cursor.description]
            scenario_rows = [dict(zip(names, values)) for values in cursor.fetchall()]
            grouped = {}
            for values in scenario_rows:
                source_id = values.pop("_source_id")
                uri = values.pop("_file_uri")
                values.pop("_file_kind")
                values = {key: value.isoformat() if isinstance(value, datetime) else value for key, value in values.items() if key not in analytics_schema.STUDIO_CACHE_COLUMNS and value is not None}
                values['_type'] = kind
                record = native_record(values)
                grouped.setdefault(source_id, []).append((uri, kind, '{}', record))
            for source_id, rows in grouped.items():
                store.insert_typed_rows(connection, table, source_id, rows, analytics_schema.DATAFLOW_COLUMN_TYPES if kind == 'dataflow_run_log' else analytics_schema.JOB_COLUMN_TYPES)
        now = "2026-07-21T00:00:00+00:00"
        connection.execute(
            f"INSERT INTO {analytics_schema.CACHE_SOURCES_TABLE} "
            f"SELECT source_id, ?::TIMESTAMPTZ, 1 FROM (VALUES {source_values}) sources(source_id)",
            [now],
        )
        for source_id in source_ids:
            analytics_publication.refresh_filter_values(connection, source_id)
        analytics_publication.publish_generation(
            connection,
            dataflow_column_types=analytics_schema.DATAFLOW_COLUMN_TYPES,
            job_column_types=analytics_schema.JOB_COLUMN_TYPES,
            published_at=datetime.fromisoformat(now),
        )
    finally:
        connection.close()
    return {"sources": len(source_ids), "dataflow_rows": dataflow_rows, "job_rows": job_rows}
