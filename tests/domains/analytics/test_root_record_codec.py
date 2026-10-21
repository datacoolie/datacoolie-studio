from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from datacoolie_studio.domains.analytics.record_codec import (
    ROOT_MAPPINGS,
    insert_record,
    insert_records,
    reconstruct_record,
    record_json,
    root_column_types,
)
from datacoolie_studio.domains.analytics.sql import quote_identifier


FIXTURES = Path(__file__).parents[2] / "fixtures" / "datacoolie" / "logging" / "v6"


def _fixture(name: str, *, line: int = 0) -> dict[str, Any]:
    payload = (FIXTURES / name).read_text(encoding="utf-8").splitlines()[line]
    return json.loads(payload, parse_float=Decimal)


def _semantic(value: Any) -> Any:
    return json.loads(record_json(value), parse_float=Decimal, parse_int=int)


def _create_typed_table(connection: Any, table: str, kind: str) -> list[str]:
    columns = root_column_types(kind)
    ddl = ", ".join(
        f"{quote_identifier(name)} {type_name}" for name, type_name in columns.items()
    )
    connection.execute(
        f"CREATE TABLE {quote_identifier(table)} ({ddl}, _present_paths VARCHAR[], filename VARCHAR, __record_line BIGINT)"
    )
    return [*columns, "_present_paths", "filename", "__record_line"]


def _typed_row(connection: Any, table: str) -> dict[str, Any]:
    columns = [str(row[0]) for row in connection.execute(f"DESCRIBE {quote_identifier(table)}").fetchall()]
    values = connection.execute(f"SELECT * FROM {quote_identifier(table)}").fetchone()
    return dict(zip(columns, values))


def test_mapping_covers_every_pinned_execution_root_and_fixed_nested_shape() -> None:
    assert set(ROOT_MAPPINGS) == {"dataflow", "job"}
    assert len(ROOT_MAPPINGS["dataflow"]) == 70
    assert len(ROOT_MAPPINGS["job"]) == 45

    dataflow_types = root_column_types("dataflow")
    job_types = root_column_types("job")
    assert dataflow_types["metrics"].startswith("STRUCT(")
    assert dataflow_types["attempts"].endswith("[]")
    assert dataflow_types["read_request"].startswith("STRUCT(")
    assert dataflow_types["duration_seconds"] == "DOUBLE"
    assert job_types["retry_delay"] == "DOUBLE"
    assert job_types["total_rows_read"] == "HUGEINT"
    assert dataflow_types["start_time"] == "VARCHAR"

    metrics = ROOT_MAPPINGS["dataflow"]["metrics"]
    assert [child.name for child in metrics.children] == [
        "rows_read",
        "rows_written",
        "rows_inserted",
        "rows_updated",
        "rows_deleted",
        "files_added",
        "files_removed",
        "bytes_added",
        "bytes_removed",
        "bytes_saved",
    ]
    attempts = ROOT_MAPPINGS["dataflow"]["attempts"]
    assert attempts.item is not None
    assert [child.name for child in attempts.item.children] == [
        "attempt_number",
        "start_time",
        "end_time",
        "duration_seconds",
        "status",
        "message",
        "source",
        "transform",
        "destination",
        "watermark",
    ]


def test_record_json_keeps_decimal_and_unbounded_integer_as_json_numbers() -> None:
    payload = {
        "fraction": Decimal("1.2345678901234567890123456789"),
        "huge": 10**30 + 1,
        "numeric_string": "1.2345678901234567890123456789",
        "tag": {"__datetime__": "2026-10-04T12:34:56+00:00"},
    }
    encoded = record_json(payload)
    assert "\"fraction\":1.2345678901234567890123456789" in encoded
    assert "\"huge\":1000000000000000000000000000001" in encoded
    assert "\"numeric_string\":\"1.2345678901234567890123456789\"" in encoded
    assert _semantic(payload) == _semantic(json.loads(encoded, parse_float=Decimal))


def test_dataflow_roundtrip_preserves_nested_values_large_integer_and_fraction() -> None:
    record = _fixture("dataflow_snapshot.json")
    record["metrics"]["rows_read"] = 10**30 + 1
    record["attempts"][0]["source"]["source_action"]["ratio"] = Decimal(
        "1.2345678901234567890123456789"
    )
    record["attempts"][0]["watermark"]["loaded_watermark"]["arbitrary"] = Decimal(
        "0.000000000000000000000000000123456789"
    )

    connection = duckdb.connect()
    _create_typed_table(connection, "typed_dataflow", "dataflow")
    insert_record(
        connection,
        "typed_dataflow",
        record,
        kind="dataflow",
        metadata={"filename": "fixture.jsonl", "__record_line": 17},
    )
    row = _typed_row(connection, "typed_dataflow")
    reconstructed = reconstruct_record(row, "dataflow")

    assert _semantic(reconstructed) == _semantic({**record, "diagnostic_issues": record.get("diagnostic_issues"), "workspace_id": record.get("workspace_id")})
    assert row["metrics"]["rows_read"] == 10**30 + 1
    assert row["duration_seconds"] == 13.0
    source_action = json.loads(
        row["attempts"][0]["source"]["source_action"],
        parse_float=Decimal,
    )
    assert source_action["ratio"] == Decimal("1.2345678901234567890123456789")


def test_native_evidence_collapses_absent_and_null_but_preserves_empty_lists() -> None:
    record = _fixture("dataflow_snapshot.json")
    record.pop("workspace_id")
    record["diagnostic_issues"] = []
    record["message"] = None
    record["read_request"]["read_range"] = None
    record["attempts"][0]["source"].pop("source_action")

    connection = duckdb.connect()
    _create_typed_table(connection, "sparse_dataflow", "dataflow")
    insert_record(connection, "sparse_dataflow", record, "dataflow")
    reconstructed = reconstruct_record(_typed_row(connection, "sparse_dataflow"), "dataflow")

    assert reconstructed["workspace_id"] is None
    assert reconstructed["diagnostic_issues"] == []
    assert reconstructed["message"] is None
    assert reconstructed["read_request"]["read_range"] is None
    assert reconstructed["attempts"][0]["source"]["source_action"] is None
    assert reconstructed["dataflow_run_id"] == record["dataflow_run_id"]


def test_job_batch_uses_one_typed_layout_and_roundtrips_json_retry_delay() -> None:
    first = _fixture("job_snapshot.json")
    second = copy.deepcopy(first)
    first["retry_delay"] = Decimal("0.123456789012345678901234567890")
    first["total_rows_read"] = 10**30
    second["retry_delay"] = Decimal("100000000000000000000.000000000000000000001")
    second["total_rows_read"] = 2**80 + 1

    connection = duckdb.connect()
    _create_typed_table(connection, "typed_jobs", "job")
    inserted = insert_records(
        connection,
        "typed_jobs",
        [first, second],
        "job",
        metadata=[
            {"filename": "job-a.jsonl", "__record_line": 1},
            {"filename": "job-b.jsonl", "__record_line": 2},
        ],
    )
    assert inserted == 2
    rows = connection.execute("SELECT * FROM typed_jobs ORDER BY __record_line").fetchall()
    columns = [str(row[0]) for row in connection.execute("DESCRIBE typed_jobs").fetchall()]
    reconstructed = [reconstruct_record(dict(zip(columns, row)), "job") for row in rows]

    assert [value["job_id"] for value in reconstructed] == [first["job_id"], second["job_id"]]
    assert [value["retry_delay"] for value in reconstructed] == [float(first["retry_delay"]), float(second["retry_delay"])]
    assert rows[0][columns.index("retry_delay")] == float(first["retry_delay"])
    assert rows[0][columns.index("total_rows_read")] == 10**30
    assert rows[1][columns.index("total_rows_read")] == 2**80 + 1


def test_integer_columns_accept_integral_float_from_standard_json_decoder() -> None:
    record = json.loads((FIXTURES / "job_snapshot.json").read_text(encoding="utf-8").splitlines()[0])
    record["total_rows_read"] = 6.0

    connection = duckdb.connect()
    _create_typed_table(connection, "integral_float_job", "job")
    insert_record(connection, "integral_float_job", record, "job")
    row = _typed_row(connection, "integral_float_job")
    reconstructed = reconstruct_record(row, "job")

    assert row["total_rows_read"] == 6
    assert reconstructed["total_rows_read"] == 6
