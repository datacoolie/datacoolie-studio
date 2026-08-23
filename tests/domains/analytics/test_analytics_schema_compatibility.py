from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from datacoolie_studio.domains.analytics import schema, schema_compatibility, store
from datacoolie_studio.domains.analytics.errors import AnalyticsSchemaIncompatibleError


pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")


def _write_dataflow_file(path: Path, **columns) -> None:
    pq.write_table(pa.table(columns), path)


def _insert(path: Path):
    conn = duckdb.connect(str(path.parent / "analytics.duckdb"))
    store.ensure_tables(conn)
    count = store.insert_dataflow_file(
        conn,
        7,
        str(path),
        "dataflow_parquet",
        json.dumps({"size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}),
    )
    return conn, count


def test_classify_type_requires_lossless_validation_for_integer_narrowing():
    compatibility = schema_compatibility.classify_type("BIGINT", "DOUBLE")

    assert compatibility.normalized_type == "BIGINT"
    assert compatibility.requires_value_validation is True
    assert compatibility.requires_projection_cast is True


def test_classify_type_keeps_timestamp_family_compatibility():
    compatibility = schema_compatibility.classify_type(
        "TIMESTAMPTZ",
        "TIMESTAMP",
    )

    assert compatibility.normalized_type == "TIMESTAMPTZ"
    assert compatibility.requires_value_validation is False


def test_cache_with_legacy_float_integer_column_requires_rebuild():
    conn = duckdb.connect()
    try:
        conn.execute(
            "CREATE TABLE etl_dataflow_runs (group_number DOUBLE, _source_id BIGINT)"
        )

        assert not schema.typed_table_schema_is_current(
            conn,
            schema.DATAFLOW_TABLE,
            schema.DATAFLOW_COLUMN_TYPES,
        )
    finally:
        conn.close()


def test_legacy_float_integer_columns_are_published_as_bigint(tmp_path):
    path = tmp_path / "legacy.parquet"
    integer_columns = [
        column
        for column, data_type in schema.DATAFLOW_COLUMN_TYPES.items()
        if data_type == "BIGINT"
    ]
    selected_columns = ", ".join(f'"{column}"' for column in integer_columns)
    column_literals = ", ".join(f"'{column}'" for column in integer_columns)
    _write_dataflow_file(
        path,
        _type=pa.array(["dataflow_run_log", "dataflow_run_log"]),
        dataflow_id=pa.array(["a", "b"]),
        status=pa.array(["succeeded", "failed"]),
        **{
            column: pa.array([float(index + 1), None], type=pa.float64())
            for index, column in enumerate(integer_columns)
        },
    )

    conn, count = _insert(path)
    try:
        types = dict(
            conn.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = 'etl_dataflow_runs'
                  AND column_name IN ({placeholders})
                """
                .format(placeholders=column_literals)
            ).fetchall()
        )
        values = conn.execute(
            f"""
            SELECT {selected_columns}
            FROM etl_dataflow_runs
            ORDER BY dataflow_id
            """
        ).fetchall()
    finally:
        conn.close()

    assert count == 2
    assert types == {column: "BIGINT" for column in integer_columns}
    assert values == [
        tuple(range(1, len(integer_columns) + 1)),
        (None,) * len(integer_columns),
    ]


def test_fractional_integer_drift_fails_before_insert(tmp_path):
    path = tmp_path / "fractional.parquet"
    _write_dataflow_file(
        path,
        group_number=pa.array([2.5], type=pa.float64()),
    )

    conn = duckdb.connect(str(tmp_path / "analytics.duckdb"))
    try:
        store.ensure_tables(conn)
        with pytest.raises(AnalyticsSchemaIncompatibleError, match="cannot be safely cast"):
            store.insert_dataflow_file(
                conn,
                7,
                str(path),
                "dataflow_parquet",
                "{}",
            )
        assert not schema.table_exists(conn, schema.DATAFLOW_TABLE) or conn.execute(
            f"SELECT count(*) FROM {schema.DATAFLOW_TABLE}"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_timezone_naive_timestamp_remains_compatible(tmp_path):
    path = tmp_path / "timestamp.parquet"
    _write_dataflow_file(
        path,
        source_end_time=pa.array(
            [datetime(2026, 8, 18, 0, 0, 0)],
            type=pa.timestamp("us"),
        ),
        transform_end_time=pa.array(
            [datetime(2026, 8, 18, 0, 1, 0)],
            type=pa.timestamp("us"),
        ),
    )

    conn, count = _insert(path)
    try:
        types = dict(
            conn.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = 'etl_dataflow_runs'
                  AND column_name IN ('source_end_time', 'transform_end_time')
                """
            ).fetchall()
        )
    finally:
        conn.close()

    assert count == 1
    assert types == {
        "source_end_time": "TIMESTAMP WITH TIME ZONE",
        "transform_end_time": "TIMESTAMP WITH TIME ZONE",
    }
