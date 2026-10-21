"""Range counts preserve the published source-record and connection grains."""
from __future__ import annotations

from typing import Any

from datacoolie_studio.domains.analytics import schema
from datacoolie_studio.domains.analytics.sql import quote_identifier
from datacoolie_studio.domains.monitoring.dimensions import UNKNOWN_KEY, dimension_key_sql, dimension_text_sql
from datacoolie_studio.domains.monitoring.log_repository import monitoring_filter_sql
from datacoolie_studio.domains.monitoring.read_models.common import rows


def range_filter_counts(conn: Any, source_ids: list[int], filters: dict[str, str]) -> dict[tuple[str, str], int]:
    placeholders = ", ".join("?" for _ in source_ids)
    fields_by_table: dict[str, list[tuple[str, str]]] = {}
    for field, (table, column) in schema.FILTER_VALUE_SOURCES.items():
        fields_by_table.setdefault(table, []).append((field, column))
    ctes, selects, params = [], [], []
    for index, (table, fields) in enumerate(fields_by_table.items()):
        name = f"filter_source_{index}"
        projected = []
        for field, column in fields:
            expression = "r.attempts[-1].destination.operation_type" if field == "destination_operation_type" else f"r.{quote_identifier(column)}"
            projected.append(f"CAST({expression} AS VARCHAR) AS {quote_identifier(field)}")
        is_dataflow = table == schema.DATAFLOW_TABLE
        if is_dataflow:
            projected.extend([
                "r._source_id", "r._file_uri", "r.dataflow_run_id", "r.job_id",
                f"{dimension_text_sql('r.source_name')} AS source_name",
                f"{dimension_text_sql('r.destination_name')} AS destination_name",
            ])
        where, bound = monitoring_filter_sql(filters, "r", "r", include_dataflow_filters=is_dataflow)
        # A shared wide materialization can spill on broad ranges. Let DuckDB
        # prune each scalar/count branch independently against columnar source.
        ctes.append(f"{name} AS NOT MATERIALIZED (SELECT {', '.join(projected)} FROM {table} r WHERE r._source_id IN ({placeholders}){where})")
        params.extend([*source_ids, *bound])
        expanded = f"filter_expanded_{index}"
        ctes.append(f"{expanded} AS (SELECT field, value FROM {name} UNPIVOT INCLUDE NULLS (value FOR field IN ({', '.join(quote_identifier(f) for f, _ in fields)})))")
        # Normalize distinct aggregate values, rather than every expanded row.
        selects.append(f"""SELECT field, {dimension_key_sql('value')} AS value, SUM(record_count) AS record_count
            FROM (SELECT field, value, COUNT(*) AS record_count FROM {expanded} GROUP BY field, value)
            GROUP BY field, {dimension_key_sql('value')}""")
        if is_dataflow:
            selects.append(f"""SELECT 'connection' AS field, {dimension_key_sql('connection_name')} AS value, COUNT(*) AS record_count
                FROM (SELECT DISTINCT _source_id, _file_uri, dataflow_run_id, job_id, connection_name
                      FROM (SELECT _source_id, _file_uri, dataflow_run_id, job_id, connection_name FROM {name} UNPIVOT INCLUDE NULLS (connection_name FOR direction IN (source_name, destination_name))))
                GROUP BY connection_name""")
    result = conn.execute(f"WITH {', '.join(ctes)} {' UNION ALL '.join(selects)}", params)
    return {(str(row["field"]), str(row["value"])): int(row["record_count"]) for row in rows(result)}


def all_time_missing_counts(conn: Any, source_ids: list[int], catalog: dict[str, dict[str, int]]) -> dict[tuple[str, str], int]:
    """Published nonmissing counts complement native totals at source-record grain."""
    placeholders = ', '.join('?' for _ in source_ids)
    totals = {}
    for table in dict.fromkeys(table for table, _ in schema.FILTER_VALUE_SOURCES.values()):
        totals[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE _source_id IN ({placeholders})", source_ids).fetchone()[0])
    return {(field, UNKNOWN_KEY): totals[table] - sum(catalog.get(field, {}).values())
            for field, (table, _) in schema.FILTER_VALUE_SOURCES.items()}
