from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import os
from contextlib import ExitStack

import duckdb
from typing import Any


from datacoolie_studio.domains.analytics import schema
from datacoolie_studio.domains.analytics.contracts import DataflowFile, JobFile
from datacoolie_studio.domains.analytics.errors import (
    AnalyticsSchemaIncompatibleError,
)
from datacoolie_studio.domains.analytics.sql import quote_identifier, sql_string


def dataflow_read_uri(item: DataflowFile) -> str:
    return item.read_uri


def dataflow_partition_date(item: DataflowFile) -> str | None:
    return item.partition_date


def dataflow_source_reader(
    file_uri: str,
    file_kind: str,
    *,
    conn=None,
    source_relation: str | None = None,
) -> str:
    if source_relation is None:
        raise AnalyticsSchemaIncompatibleError("A validated v6 staging relation is required")
    return f"(SELECT * FROM {source_relation} WHERE _type = 'dataflow_run_log')"


def _stage_v6_typed(conn, items, *, name: str, expected_type: str):
    from datacoolie_studio.domains.analytics.native_loading import load_file
    from datacoolie_studio.domains.analytics.record_codec import root_column_types

    relation = quote_identifier(name)
    summary_name = quote_identifier(name + "_admission")
    types = root_column_types(expected_type)
    types = {**types, "_record_line": "BIGINT", "filename": "VARCHAR"}
    columns = ", ".join(f"{quote_identifier(column)} {dtype}" for column, dtype in types.items())
    try:
        conn.execute(f"CREATE TEMP TABLE {relation} ({columns})")
        summaries = [(item.read_uri, json.dumps(load_file(conn, item, relation=relation, expected_type=expected_type)))
                     for item in items]
        conn.execute(f"CREATE TEMP TABLE {summary_name} (filename VARCHAR, summary VARCHAR)")
        if summaries:
            conn.executemany(f"INSERT INTO {summary_name} VALUES (?, ?)", summaries)
    except Exception:
        conn.execute(f"DROP TABLE IF EXISTS {relation}")
        conn.execute(f"DROP TABLE IF EXISTS {summary_name}")
        raise
    return relation


def stage_dataflow_relations(conn, items: Sequence[DataflowFile], *, stage_index: int = 0):
    if not items:
        return {}, (), ()
    raw_name = f"__dc_dataflow_batch_{stage_index}"
    raw_relation = _stage_v6_typed(conn, items, name=raw_name, expected_type="dataflow_run_log")
    relations, views = {}, []
    projection = ", ".join(quote_identifier(column) for column in schema.DATAFLOW_COLUMN_TYPES if column not in schema.GENERATED_CACHE_COLUMNS)
    for index, item in enumerate(items):
        name = f"__dc_dataflow_stage_{stage_index}_{index}"
        view = quote_identifier(name)
        conn.execute(f"CREATE TEMP VIEW {view} AS SELECT {projection} FROM {raw_relation} WHERE filename = {sql_string(item.read_uri)}")
        relations[index] = view
        views.append(name)
    return relations, (raw_name, raw_name + "_admission"), tuple(views)


def stage_job_relations(conn, items: Sequence[JobFile], *, stage_index: int = 0):
    if not items:
        return {}, {}, (), ()
    name = f"__dc_job_batch_{stage_index}"
    relation = _stage_v6_typed(conn, items, name=name, expected_type="job_run_log")
    relations, types, views = {}, {}, []
    for index, item in enumerate(items):
        view_name = f"__dc_job_stage_{stage_index}_{index}"
        view = quote_identifier(view_name)
        conn.execute(f"CREATE TEMP VIEW {view} AS SELECT * EXCLUDE(filename) FROM {relation} WHERE filename = {sql_string(item.read_uri)}")
        relations[index] = view
        types[index] = {key: value for key, value in schema.JOB_COLUMN_TYPES.items() if key not in schema.GENERATED_CACHE_COLUMNS}
        views.append(view_name)
    return relations, types, (name, name + "_admission"), tuple(views)


def _is_identifier_column(name: str) -> bool:
    normalized = name.strip().lower()
    return normalized == "id" or normalized.endswith("_id")


def validate_identifier_values(
    rows: Sequence[tuple[str, str, str, dict[str, Any]]],
) -> None:
    """Reject object/array identities before Python values are serialized.

    Null, scalar strings and numeric IDs remain valid authored values. A JSON
    object or array cannot be an exact identity and must not be stringified by
    the generic dynamic-value path, where it would become a publishable but
    malformed ID.
    """

    for file_uri, _file_kind, _revision_json, row in rows:
        for column, value in row.items():
            if _is_identifier_column(str(column)) and isinstance(value, (dict, list)):
                raise AnalyticsSchemaIncompatibleError(
                    f"Identifier column {column!r} in {file_uri} contains an "
                    f"object/array value; expected null, string, or numeric scalar"
                )


def job_stage_source_types(conn, relation: str) -> dict[str, str]:
    return {key: value for key, value in schema.JOB_COLUMN_TYPES.items() if key not in schema.GENERATED_CACHE_COLUMNS}


def drop_staged_relations(
    conn,
    *,
    tables: Sequence[str] = (),
    views: Sequence[str] = (),
) -> None:
    for name in views:
        conn.execute(f"DROP VIEW IF EXISTS {quote_identifier(name)}")
    for name in tables:
        conn.execute(f"DROP TABLE IF EXISTS {quote_identifier(name)}")




@dataclass(frozen=True)
class NativeBatchStage:
    relation: str
    metadata: str
    file_key_column: str
    items: tuple
    summaries: tuple[dict, ...]
    counts: tuple[int, ...]
    tables: tuple[str, ...]
    source_types: dict[str, str] | None = None


def internal_column_name(base: str, names) -> str:
    occupied = {name.casefold() for name in names}
    while base.casefold() in occupied:
        base += "_"
    return base


def stage_native_batch(conn, items, *, kind: str, stage_index: int) -> NativeBatchStage | None:
    """Detach one bounded batch into typed rows and descriptor metadata."""
    if not items:
        return None
    from datacoolie_studio.domains.analytics.native_loading import admit_file, load_file, native_failure_message
    from datacoolie_studio.domains.analytics.record_codec import root_column_types

    expected_type = {"dataflow": "dataflow_run_log", "job": "job_run_log"}[kind]
    mapping = root_column_types(expected_type)
    key_column = internal_column_name("__dc_file_key", mapping)
    name = f"__dc_{kind}_batch_{stage_index}"
    relation = quote_identifier(name)
    metadata_name = name + "_metadata"
    metadata = quote_identifier(metadata_name)
    lines_name = name + "_lines"
    line_relation = quote_identifier(lines_name)
    tables = (name, metadata_name, lines_name)
    definitions = {**mapping, "_record_line": "BIGINT", key_column: "BIGINT"}
    try:
        conn.execute(f"CREATE TEMP TABLE {relation} (" + ", ".join(
            f"{quote_identifier(key)} {dtype}" for key, dtype in definitions.items()) + ")")
        # New/unqualified runtimes and ambiguous repeated read paths keep the
        # established single-file reader. The batch descriptor key still owns rows.
        # DuckDB documents JSON insertion order and WITH ORDINALITY enumeration.
        # Qualification tests cover this exact runtime with preserve_insertion_order.
        preserve_order = conn.execute("SELECT current_setting('preserve_insertion_order')").fetchone()[0]
        read_identities = {os.path.normcase(str(Path(item.read_uri).resolve())) for item in items}
        qualified = duckdb.__version__ == "1.5.3" and preserve_order and len(read_identities) == len(items)
        summaries = []
        with ExitStack() as owned:
            admitted = []
            if qualified:
                admitted = [owned.enter_context(admit_file(item, expected_type=expected_type)) for item in items]
                summaries = [entry.summary for entry in admitted]
            else:
                scratch = quote_identifier(name + "_reader")
                fallback_filename = internal_column_name("__dc_fallback_filename", mapping)
                conn.execute(f"CREATE TEMP TABLE {scratch} (" + ", ".join(
                    f"{quote_identifier(key)} {dtype}" for key, dtype in {**mapping, "_record_line": "BIGINT", fallback_filename: "VARCHAR"}.items()) + ")")
                try:
                    for file_key, item in enumerate(items):
                        summaries.append(load_file(
                            conn,
                            item,
                            relation=scratch,
                            expected_type=expected_type,
                            filename_column=fallback_filename,
                            mapping=mapping,
                        ))
                        roots = ", ".join(quote_identifier(key) for key in mapping)
                        conn.execute(f"INSERT INTO {relation} BY NAME SELECT {roots}, _record_line, ? AS {quote_identifier(key_column)} FROM {scratch}", [file_key])
                        conn.execute(f"DELETE FROM {scratch}")
                finally:
                    conn.execute(f"DROP TABLE {scratch}")
            paths = [str(entry.read_path) for entry in admitted] if qualified else [item.read_uri for item in items]
            lines = [list(entry.lines) for entry in admitted] if qualified else [[] for item in items]
            # Only small descriptor data and physical-line metadata cross Python;
            # payload columns remain native DuckDB JSON scan output.
            conn.execute(f"CREATE TEMP TABLE {metadata} AS SELECT "
                "unnest(?::BIGINT[]) AS file_key, unnest(?::VARCHAR[]) AS canonical_uri, "
                "unnest(?::VARCHAR[]) AS read_path, unnest(?::VARCHAR[]) AS file_kind, "
                "unnest(?::VARCHAR[]) AS revision_json, unnest(?::VARCHAR[]) AS partition_date, "
                "unnest(?::BIGINT[]) AS admitted_count, unnest(?::BIGINT[][]) AS physical_lines",
                [list(range(len(items))), [item.canonical_uri for item in items], paths,
                 [item.file_kind for item in items], [item.revision_json for item in items],
                 [item.partition_date for item in items], [s["admitted"] for s in summaries], lines])
            if qualified:
                conn.execute(f"CREATE TEMP TABLE {line_relation} AS SELECT file_key, unnest(physical_lines) AS physical_line, generate_subscripts(physical_lines, 1) AS admitted_ordinal FROM {metadata}")
                conn.execute(f"ALTER TABLE {metadata} DROP COLUMN physical_lines")
            if qualified and any(entry.lines for entry in admitted):
                reader_filename = internal_column_name("__dc_reader_filename", mapping)
                payload_aliases = [f"__dc_payload_{i}" for i in range(len(mapping))]
                aliases = ", ".join(quote_identifier(key) for key in [*payload_aliases, "__dc_path", "__dc_ordinal"])
                payload = ", ".join(f"r.{quote_identifier(alias)} AS {quote_identifier(key)}" for key, alias in zip(mapping, payload_aliases))
                try:
                    conn.execute(f"INSERT INTO {relation} BY NAME "
                        f"WITH r AS (SELECT *, row_number() OVER (PARTITION BY __dc_path ORDER BY __dc_ordinal) AS __dc_position "
                        "FROM read_json(?, columns=?, auto_detect=false, format='newline_delimited', "
                        "hive_partitioning=false, ignore_errors=false, maximum_object_size=16777216, "
                        f"filename={sql_string(reader_filename)}) WITH ORDINALITY AS input({aliases})) "
                        f"SELECT {payload}, m.file_key AS {quote_identifier(key_column)}, "
                        "CASE WHEN m.file_key IS NULL OR l.physical_line IS NULL "
                        "THEN error('native batch provenance does not reconcile') "
                        "ELSE l.physical_line END AS _record_line "
                        f"FROM r LEFT JOIN {metadata} m ON r.__dc_path = m.read_path LEFT JOIN {line_relation} l ON l.file_key=m.file_key AND l.admitted_ordinal=r.__dc_position",
                        [[str(entry.read_path) for entry in admitted if entry.lines], mapping])
                except duckdb.Error as exc:
                    # All records already passed strict schema and native ranges.
                    # Preserve canonical identities if a native conversion still fails.
                    message = native_failure_message(exc, admitted)
                    raise AnalyticsSchemaIncompatibleError(
                        f"Native batch load failed for {[item.canonical_uri for item in items]}: {message}"
                    ) from exc
                for entry in admitted:
                    entry.assert_stable("native load")
            counted = dict(conn.execute(f"SELECT {quote_identifier(key_column)}, count(*) FROM {relation} GROUP BY {quote_identifier(key_column)}").fetchall())
            counts = tuple(int(counted.get(index, 0)) for index in range(len(items)))
            for index, count in enumerate(counts):
                if count != summaries[index]["admitted"]:
                    raise AnalyticsSchemaIncompatibleError(f"{items[index].canonical_uri}: native row counts do not reconcile")
        return NativeBatchStage(relation, metadata, key_column, tuple(items), tuple(summaries), counts, tables)
    except Exception:
        drop_staged_relations(conn, tables=tables)
        raise
