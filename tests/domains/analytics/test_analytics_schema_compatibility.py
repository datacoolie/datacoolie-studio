from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import pytest

from datacoolie_studio.domains.analytics import (
    schema,
    schema_compatibility,
    schema_tables,
    store,
    type_rules,
)
from datacoolie_studio.domains.analytics.contracts import LogFileDescriptor, LogPublishBatch
from tests.support.v6_fixture import deepcopy_v6_rows
from datacoolie_studio.domains.analytics.record_codec import reconstruct_record


def _descriptor(
    path: Path,
    file_kind: str,
    partition_date: str | None = None,
) -> LogFileDescriptor:
    return LogFileDescriptor(
        canonical_uri=str(path),
        file_kind=file_kind,
        revision_json=json.dumps(
            {"size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
        ),
        read_uri=str(path),
        partition_date=partition_date,
    )


def _write_rows(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _publish_dataflow(
    database_path: Path,
    path: Path,
    *,
    batched: bool,
) -> dict:
    descriptor = _descriptor(path, "dataflow_json")
    if batched:
        return store.publish_rows_batched(
            7,
            [LogPublishBatch(dataflow_files=(descriptor,))],
            [],
            [],
            database_path=database_path,
        )
    return store.publish_rows(
        7,
        [descriptor],
        [],
        [],
        [],
        database_path=database_path,
    )


@pytest.mark.parametrize("batched", [False, True])
def test_v6_identifiers_presence_and_nested_values_survive_ingestion(
    tmp_path: Path,
    batched: bool,
):
    success = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    success["dataflow_id"] = "ABCDEFAB-CDEF-ABCD-EFAB-CDEF01234567"
    success["dataflow_run_id"] = "`RUN`-A"
    success["job_id"] = "ABCDEFAB-CDEF-ABCD-EFAB-CDEF01234567"
    success["source_id"] = "ABCDEFAB-CDEF-ABCD-EFAB-CDEF01234567"
    success["workspace_id"] = "Workspace/ABC"
    success["message"] = "informational"
    success["attempts"][-1]["source"]["source_action"].update(
        extra_number=3.0,
        extra_object={"region": "apac"},
    )

    failed = deepcopy_v6_rows("dataflow_part_00000001.json")[0]
    failed["dataflow_run_id"] = "failed-run"
    failed["message"] = "legacy"

    explicit_null = deepcopy_v6_rows("dataflow_part_00000001.json")[0]
    explicit_null["dataflow_run_id"] = "explicit-null-run"
    explicit_null["message"] = None

    omitted = deepcopy_v6_rows("dataflow_part_00000001.json")[0]
    omitted["dataflow_run_id"] = "omitted-message-run"
    omitted.pop("message", None)

    path = _write_rows(tmp_path / "dataflow.jsonl", [success, failed, explicit_null, omitted])
    database_path = tmp_path / ("batched.duckdb" if batched else "direct.duckdb")
    result = _publish_dataflow(database_path, path, batched=batched)

    assert result["published"] is True, result
    assert result["parsed_dataflow_records"] == 4
    with duckdb.connect(str(database_path), read_only=True) as connection:
        columns = set(schema.table_columns(connection, schema.DATAFLOW_TABLE))
        rows = {
            row[0]: row[1:]
            for row in connection.execute(
                """
                SELECT dataflow_run_id, dataflow_id, job_id, source_id,
                       workspace_id, message, message IS NOT NULL AS message_present,
                       _record_line
                FROM etl_dataflow_runs
                ORDER BY dataflow_run_id
                """
            ).fetchall()
        }

    assert "extra_number" not in columns
    assert "extra_object" not in columns
    assert rows["`RUN`-A"][:5] == (
        "ABCDEFAB-CDEF-ABCD-EFAB-CDEF01234567",
        "ABCDEFAB-CDEF-ABCD-EFAB-CDEF01234567",
        "ABCDEFAB-CDEF-ABCD-EFAB-CDEF01234567",
        "Workspace/ABC",
        "informational",
    )
    assert rows["`RUN`-A"][4:6] == ("informational", True)
    assert rows["explicit-null-run"][4:6] == (None, False)
    assert rows["omitted-message-run"][4:6] == (None, False)
    with duckdb.connect(str(database_path), read_only=True) as connection:
        cursor = connection.execute("SELECT * FROM etl_dataflow_runs WHERE dataflow_run_id = '`RUN`-A'")
        physical = dict(zip([c[0] for c in cursor.description], cursor.fetchone()))
        assert reconstruct_record(physical, "dataflow_run_log") == {**success, "diagnostic_issues": None}
    assert "raw_record_json" not in columns


@pytest.mark.parametrize("version", [3, 4, 5])
def test_old_schema_rows_are_excluded_from_v6_publication(tmp_path: Path, version: int):
    row = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    row["log_schema_version"] = version
    path = _write_rows(tmp_path / f"old-v{version}.jsonl", [row])
    result = _publish_dataflow(tmp_path / f"v{version}.duckdb", path, batched=False)

    assert result["published"] is True, result
    assert result["parsed_dataflow_records"] == 0
    summary = result["file_admission_summaries"][str(path)]
    assert summary["admitted"] == 0
    assert summary["excluded"] == 1
    assert summary["versions"] == {str(version): 1}


def test_v6_top_level_extra_is_rejected_without_dynamic_columns(tmp_path: Path):
    row = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    row["unmodeled_extra"] = {"region": "apac"}
    path = _write_rows(tmp_path / "extra.jsonl", [row])
    result = _publish_dataflow(tmp_path / "extra.duckdb", path, batched=False)

    assert result["published"] is False
    assert result["errors"][0]["code"] == "schema_incompatible"
    assert "unmodeled_extra" in result["errors"][0]["message"]


def test_v6_metric_projections_use_exact_bignum_columns(tmp_path: Path):
    row = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    row["dataflow_run_id"] = "bignum-run"
    row["metrics"].update(
        rows_read=9_007_199_254_740_993,
        rows_written=2**80,
        bytes_added=10**30,
    )
    row["attempts"][-1]["source"]["rows_read"] = 9_007_199_254_740_993
    row["attempts"][-1]["destination"].update(
        rows_written=2**80,
        bytes_added=10**30,
    )
    path = _write_rows(tmp_path / "bignum.jsonl", [row])
    result = _publish_dataflow(tmp_path / "bignum.duckdb", path, batched=False)
    assert result["published"] is True, result

    with duckdb.connect(str(tmp_path / "bignum.duckdb"), read_only=True) as connection:
        types = connection.execute(
            """
            SELECT typeof(metrics.rows_read), typeof(metrics.rows_written),
                   typeof(metrics.bytes_added)
            FROM etl_dataflow_runs
            """
        ).fetchone()
        values = connection.execute(
            """
            SELECT metrics.rows_read, metrics.rows_written, metrics.bytes_added
            FROM etl_dataflow_runs
            """
        ).fetchone()

    assert types == ("HUGEINT", "HUGEINT", "HUGEINT")
    assert tuple(int(value) for value in values) == (
        9_007_199_254_740_993,
        2**80,
        10**30,
    )


def test_v6_job_batch_preserves_strings_presence_and_provenance(tmp_path: Path):
    job = deepcopy_v6_rows("job_snapshot.json")[0]
    raw_start = "2026-08-18T15:23:08.534826+07:00"
    job.update(
        job_id="job-v6-provenance",
        job_num=4,
        run_attributes='{"factory_run_id":"run-1"}',
        stages='["extract", "load"]',
        message=None,
        start_time=raw_start,
        end_time=raw_start,
    )
    path = _write_rows(tmp_path / "job_run_log" / "2026-08-18" / "jobs.json", [job])
    result = store.publish_rows(
        7,
        [],
        [],
        [],
        [],
        database_path=tmp_path / "job.duckdb",
        job_files=[_descriptor(path, "job_json", "2026-08-18")],
    )

    assert result["published"] is True, result
    assert result["parsed_job_records"] == 1
    with duckdb.connect(str(tmp_path / "job.duckdb"), read_only=True) as connection:
        row = connection.execute(
            """
            SELECT job_id, job_num, start_time, run_attributes, stages,
                   _source_id, _file_uri, __run_date, message IS NOT NULL
            FROM etl_job_runs
            """
        ).fetchone()

    assert row == (
        "job-v6-provenance",
        4,
        raw_start,
        '{"factory_run_id":"run-1"}',
        '["extract", "load"]',
        7,
        str(path),
        date(2026, 8, 18),
        False,
    )


def test_v6_job_integer_beyond_bigint_preserves_native_value(tmp_path: Path):
    job = deepcopy_v6_rows("job_snapshot.json")[0]
    job["job_num"] = 9_223_372_036_854_775_808
    path = _write_rows(tmp_path / "jobs.json", [job])
    result = store.publish_rows(
        7,
        [],
        [],
        [],
        [],
        database_path=tmp_path / "overflow.duckdb",
        job_files=[_descriptor(path, "job_json", "2026-10-06")],
    )

    assert result["published"] is True, result
    with duckdb.connect(str(tmp_path / "overflow.duckdb"), read_only=True) as connection:
        assert int(connection.execute("SELECT job_num FROM etl_job_runs").fetchone()[0]) == job["job_num"]


@pytest.mark.parametrize("direct", [False, True])
def test_integral_json_number_has_equal_file_and_direct_admission(tmp_path: Path, direct: bool):
    job = deepcopy_v6_rows("job_snapshot.json")[0]
    job["total_rows_read"] = 1.0
    path = _write_rows(tmp_path / "job.jsonl", [job])
    database = tmp_path / "analytics.duckdb"
    result = store.publish_rows(
        7, [], [(str(path), "job_json", "{}", job)] if direct else [], [], [],
        database_path=database,
        job_files=[] if direct else [_descriptor(path, "job_json")],
    )
    assert result["published"], result
    with duckdb.connect(str(database), read_only=True) as connection:
        cursor = connection.execute("SELECT * FROM etl_job_runs")
        record = reconstruct_record(dict(zip([item[0] for item in cursor.description], cursor.fetchone())), "job")
    assert record["total_rows_read"] == 1
    assert type(record["total_rows_read"]) is int


def test_classify_type_requires_lossless_validation_for_integer_narrowing():
    compatibility = schema_compatibility.classify_type("BIGINT", "DOUBLE")

    assert compatibility.normalized_type == "BIGINT"
    assert compatibility.requires_value_validation is True
    assert compatibility.requires_projection_cast is True


def test_classify_type_preserves_unsigned_bigint_range_validation():
    compatibility = schema_compatibility.classify_type("BIGINT", "UBIGINT")

    assert compatibility.normalized_type == "BIGINT"
    assert compatibility.requires_value_validation is True
    assert compatibility.requires_projection_cast is True


def test_type_rules_merge_integer_source_types_without_float_coercion():
    assert type_rules.common_numeric_source_type("custom_number", "UINTEGER", "BIGINT") == "BIGINT"
    assert type_rules.common_numeric_source_type("custom_number", "UBIGINT", "HUGEINT") == "HUGEINT"
    assert type_rules.common_numeric_source_type("custom_number", "UBIGINT", "UBIGINT") == "UBIGINT"


def test_classify_type_keeps_timestamp_family_compatibility():
    compatibility = schema_compatibility.classify_type("TIMESTAMPTZ", "TIMESTAMP")

    assert compatibility.normalized_type == "TIMESTAMPTZ"
    assert compatibility.requires_value_validation is False


def test_lossless_integer_predicate_is_target_aware():
    floating = schema_compatibility.lossless_integer_predicate('"metric"', "DOUBLE")
    unsigned = schema_compatibility.lossless_integer_predicate('"metric"', "UBIGINT")

    assert "floor" in floating
    assert "TRY_CAST" in floating
    assert unsigned == 'TRY_CAST("metric" AS BIGINT) IS NULL'


def test_cache_with_float_integer_column_requires_rebuild():
    conn = duckdb.connect()
    try:
        conn.execute("CREATE TABLE etl_dataflow_runs (group_number DOUBLE, _source_id BIGINT)")

        assert not schema.typed_table_schema_is_current(
            conn,
            schema.DATAFLOW_TABLE,
            schema.DATAFLOW_COLUMN_TYPES,
        )
    finally:
        conn.close()


def test_dataflow_cache_missing_monitoring_columns_requires_rebuild():
    conn = duckdb.connect()
    try:
        conn.execute(
            """
            CREATE TABLE etl_dataflow_runs (
              _source_id BIGINT,
              _file_uri VARCHAR,
              _file_kind VARCHAR,
              _file_date DATE,
              _source_size BIGINT,
              _source_mtime_ns BIGINT,
              _ingested_at TIMESTAMPTZ
            )
            """
        )

        assert not schema.typed_table_schema_is_current(
            conn,
            schema.DATAFLOW_TABLE,
            schema.DATAFLOW_COLUMN_TYPES,
        )
    finally:
        conn.close()


def test_empty_dataflow_cache_has_monitoring_columns():
    conn = duckdb.connect()
    try:
        schema_tables.ensure_tables(conn)

        assert set(schema.DATAFLOW_REQUIRED_COLUMNS) <= set(
            schema.table_columns(conn, schema.DATAFLOW_TABLE)
        )
    finally:
        conn.close()
