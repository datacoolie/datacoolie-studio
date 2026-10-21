"""Publication gates for observation grain, run identity and native v6 facts."""

from __future__ import annotations

from decimal import Decimal


from datacoolie_studio.domains.analytics.sql import quote_identifier
from datacoolie_studio.domains.analytics.v6_projection import field_expression


def _exact_json(value):
    if isinstance(value, dict):
        return ('object', tuple(sorted((key, _exact_json(item)) for key, item in value.items())))
    if isinstance(value, list):
        return ('array', tuple(_exact_json(item) for item in value))
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return ("number", Decimal(str(value)))
    return (type(value).__name__, value)


def validate_run_conflicts(conn, table: str) -> None:
    from datacoolie_studio.domains.analytics.record_codec import reconstruct_record
    cursor = conn.execute(
        f"SELECT * FROM {table} "
        "QUALIFY count(*) OVER (PARTITION BY _source_id, log_session_id, dataflow_run_id) > 1 "
        "ORDER BY _source_id, log_session_id, dataflow_run_id"
    )
    names = [c[0] for c in cursor.description]
    previous_key, previous = None, None
    while records := cursor.fetchmany(256):
        for values in records:
            row = dict(zip(names, values))
            key = (row['_source_id'], row['log_session_id'], row['dataflow_run_id'])
            record = _exact_json(reconstruct_record(row, "dataflow_run_log"))
            if key == previous_key and record != previous:
                raise RuntimeError(f"Conflicting v6 observations for source/session/run {key!r}")
            previous_key, previous = key, record


def validate_serving_parity(conn, dataflow_table: str, job_table: str, *, dataflow_facts=None, job_facts=None) -> None:
    from datacoolie_studio.domains.analytics import schema
    from datacoolie_studio.domains.analytics.serving_facts import (
        DATAFLOW_FACT_COLUMNS, JOB_FACT_COLUMNS,
        MONITORING_DATAFLOW_FACTS_TABLE, MONITORING_JOB_FACTS_TABLE,
    )
    MONITORING_DATAFLOW_FACTS_TABLE = dataflow_facts or MONITORING_DATAFLOW_FACTS_TABLE
    MONITORING_JOB_FACTS_TABLE = job_facts or MONITORING_JOB_FACTS_TABLE
    for raw_table, facts, columns, types, identity in (
        (dataflow_table, MONITORING_DATAFLOW_FACTS_TABLE, DATAFLOW_FACT_COLUMNS, schema.DATAFLOW_COLUMN_TYPES, "dataflow_run_id"),
        (job_table, MONITORING_JOB_FACTS_TABLE, JOB_FACT_COLUMNS, schema.JOB_COLUMN_TYPES, "job_id"),
    ):
        expected = conn.execute(f"SELECT count(*) FROM (SELECT DISTINCT _source_id, log_session_id, {identity} FROM {raw_table})").fetchone()[0]
        actual = conn.execute(f"SELECT count(*) FROM {facts}").fetchone()[0]
        if expected != actual:
            raise RuntimeError("Monitoring serving-fact identity counts do not reconcile")
        duplicates = conn.execute(f"""
            SELECT count(*) FROM (
                SELECT _source_id, log_session_id, {identity} FROM {facts}
                GROUP BY ALL HAVING count(*) > 1
            )
        """).fetchone()[0]
        if duplicates:
            raise RuntimeError("Monitoring serving-fact identities are not unique")
        provenance_duplicates = conn.execute(f"""
            SELECT count(*) FROM (
                SELECT _source_id, _file_uri, _record_line FROM {raw_table}
                GROUP BY ALL HAVING count(*) > 1
            )
        """).fetchone()[0]
        if provenance_duplicates:
            raise RuntimeError("Monitoring source observation provenance is not unique")
        provenance_invalid = conn.execute(f"""
            SELECT count(*) FROM {facts} f
            LEFT JOIN {raw_table} r ON f._source_id = r._source_id
                AND f.log_session_id IS NOT DISTINCT FROM r.log_session_id
                AND f.{identity} IS NOT DISTINCT FROM r.{identity}
                AND f._file_uri IS NOT DISTINCT FROM r._file_uri
                AND f._record_line IS NOT DISTINCT FROM r._record_line
            WHERE r._source_id IS NULL
                
        """).fetchone()[0]
        if provenance_invalid:
            raise RuntimeError("Monitoring serving raw evidence/provenance parity failed")
        order = (
            "TRY_CAST(observed_at AS TIMESTAMPTZ) DESC NULLS LAST, "
            "__event_time DESC NULLS LAST, _file_uri DESC, _record_line DESC"
            if identity == "job_id" else "_file_uri, _record_line"
        )
        winner_invalid = conn.execute(f"""
            WITH winners AS (
                SELECT * FROM {raw_table}
                QUALIFY row_number() OVER (
                    PARTITION BY _source_id, log_session_id, {identity} ORDER BY {order}
                ) = 1
            )
            SELECT count(*) FROM {facts} f JOIN winners r
                ON f._source_id = r._source_id
                AND f.log_session_id IS NOT DISTINCT FROM r.log_session_id
                AND f.{identity} IS NOT DISTINCT FROM r.{identity}
            WHERE f._file_uri IS DISTINCT FROM r._file_uri
                OR f._record_line IS DISTINCT FROM r._record_line
        """).fetchone()[0]
        if winner_invalid:
            raise RuntimeError("Monitoring canonical observation winner parity failed")
        # Re-derive report values independently from the selected physical row.
        predicates = []
        from datacoolie_studio.domains.analytics.serving_facts import _table_columns
        available = _table_columns(conn, raw_table)
        fact_types = {row[0]: row[1] for row in conn.execute(f"DESCRIBE {facts}").fetchall()}
        for column in columns:
            if column in {"raw_record_json", "_record_line"}:
                continue
            expression = field_expression(column, fact_types.get(column, "VARCHAR"), raw="r", available=available)
            predicates.append(f"f.{quote_identifier(column)} IS DISTINCT FROM ({expression})")
        if predicates:
            invalid = conn.execute(f"""SELECT count(*) FROM {facts} f JOIN {raw_table} r
                ON f._source_id = r._source_id AND f._file_uri IS NOT DISTINCT FROM r._file_uri AND f._record_line IS NOT DISTINCT FROM r._record_line
                WHERE {' OR '.join(predicates)}""").fetchone()[0]
            if invalid:
                raise RuntimeError("Monitoring serving metric/phase/identity parity failed")

    context_invalid = conn.execute(f"""
        SELECT count(*) FROM {MONITORING_DATAFLOW_FACTS_TABLE} d
        LEFT JOIN {MONITORING_JOB_FACTS_TABLE} j
            ON d._source_id = j._source_id
            AND d.log_session_id IS NOT DISTINCT FROM j.log_session_id
            AND d.job_id = j.job_id
        WHERE d.engine_name IS DISTINCT FROM COALESCE(j.engine_name, 'unknown')
           OR d.metadata_provider_name IS DISTINCT FROM COALESCE(j.metadata_provider_name, 'unknown')
           OR d.platform_name IS DISTINCT FROM COALESCE(j.platform_name, 'unknown')
    """).fetchone()[0]
    if context_invalid:
        raise RuntimeError("Monitoring latest job context parity failed")
