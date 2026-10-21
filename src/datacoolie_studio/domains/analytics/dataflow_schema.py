"""Validate staged observations against the complete pinned base schema."""
from __future__ import annotations
from datacoolie_studio.domains.analytics import schema
from datacoolie_studio.domains.analytics.errors import AnalyticsSchemaIncompatibleError
from datacoolie_studio.domains.analytics.staging import dataflow_source_reader


def describe_source_columns(conn, file_uri, file_kind, *, source_relation=None):
    return [(str(r[0]), str(r[1])) for r in conn.execute(
        f"DESCRIBE SELECT * FROM {dataflow_source_reader(file_uri, file_kind, source_relation=source_relation)}"
    ).fetchall()]


def dataflow_target_types(conn, file_uri, file_kind, *, source_relation=None):
    actual = dict(describe_source_columns(conn, file_uri, file_kind, source_relation=source_relation))
    expected = {k: v for k, v in schema.DATAFLOW_COLUMN_TYPES.items() if k not in schema.GENERATED_CACHE_COLUMNS}
    for column, dtype in expected.items():
        if column not in actual or not schema.duckdb_type_matches(actual[column], dtype):
            raise AnalyticsSchemaIncompatibleError(f"Invalid staged datatype for {column!r} in {file_uri}")
    return {**expected, "__event_time": "TIMESTAMPTZ", "__run_date": "DATE"}


def ensure_dataflow_table_for_source(conn, file_uri, file_kind, *, source_relation=None):
    dataflow_target_types(conn, file_uri, file_kind, source_relation=source_relation)
    if not schema.typed_table_schema_is_current(conn, schema.DATAFLOW_TABLE, schema.DATAFLOW_COLUMN_TYPES):
        raise AnalyticsSchemaIncompatibleError("Dataflow storage requires a complete candidate rebuild")


def preflight_dataflow_schemas(conn, files, *, source_relations=None):
    for index, item in enumerate(files):
        ensure_dataflow_table_for_source(conn, item.read_uri, item.file_kind, source_relation=(source_relations or {}).get(index))
