from __future__ import annotations

from datetime import datetime, timezone

import duckdb

from datacoolie_studio.domains.monitoring.read_models.jobs import _WORKLOAD_EFFICIENCY_SQL


def test_workload_efficiency_keeps_every_operation_and_uses_matching_rate_units():
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("CREATE TABLE all_log_versions (log_schema_version INTEGER)")
        connection.execute("INSERT INTO all_log_versions VALUES (6)")
        connection.execute("""
            CREATE TABLE filtered_jobs (
                _source_id INTEGER, log_session_id VARCHAR, job_id VARCHAR,
                event_time TIMESTAMP, normalized_status VARCHAR, engine_name VARCHAR,
                metadata_provider_name VARCHAR, platform_name VARCHAR
            )
        """)
        connection.execute("""
            CREATE TABLE filtered_dataflows (
                _source_id INTEGER, log_session_id VARCHAR, job_id VARCHAR,
                operation_type VARCHAR, destination_operation_type VARCHAR,
                normalized_status VARCHAR, duration_seconds DOUBLE,
                source_rows_read BIGINT, destination_rows_written BIGINT,
                destination_bytes_added BIGINT, destination_bytes_removed BIGINT
            )
        """)

        job_rows = []
        dataflow_rows = []
        start = datetime(2026, 10, 20, tzinfo=timezone.utc).replace(tzinfo=None)
        for index in range(500):
            job_id = f"pipeline-{index:03}"
            duration = 1000 - index
            job_rows.append((1, None, job_id, start, "succeeded", "duckdb", "file", "local"))
            dataflow_rows.append((1, None, job_id, "etl", None, "succeeded", duration, 100, 95, None, None))

        job_rows.extend([
            (1, None, "job-compact", start, "succeeded", "duckdb", "file", "local"),
            (1, None, "job-maintenance", start, "succeeded", "duckdb", "file", "local"),
        ])
        dataflow_rows.extend([
            (1, None, "job-compact", "etl", "compact", "succeeded", 4, None, None, 180, 20),
            (1, None, "job-maintenance", "maintenance", None, "succeeded", 2, None, None, 80, 20),
        ])
        connection.executemany("INSERT INTO filtered_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", job_rows)
        connection.executemany("INSERT INTO filtered_dataflows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", dataflow_rows)

        query = f"WITH query_context AS (SELECT 1) {_WORKLOAD_EFFICIENCY_SQL}"
        columns = [column[0] for column in connection.execute(query).description]
        result = [dict(zip(columns, row, strict=True)) for row in connection.fetchall()]

        assert len(result) == 502
        assert {row["operation_type"] for row in result} == {"etl", "compact", "maintenance"}
        compact = next(row for row in result if row["operation_type"] == "compact")
        maintenance = next(row for row in result if row["operation_type"] == "maintenance")
        assert compact["workload_size_metric"] == "bytes_per_second"
        assert compact["workload_size"] == 50
        assert maintenance["workload_size_metric"] == "bytes_per_second"
        assert maintenance["workload_size"] == 50
        assert next(row for row in result if row["operation_type"] == "etl")["workload_size_metric"] == "rows_per_second"
    finally:
        connection.close()
