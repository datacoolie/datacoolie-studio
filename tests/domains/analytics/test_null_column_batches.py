from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import duckdb
import pytest

from datacoolie_studio.domains.analytics import contracts, schema, store
from tests.support.v6_fixture import deepcopy_v6_rows
from datacoolie_studio.domains.analytics.record_codec import reconstruct_record


def _descriptor(path: Path) -> contracts.LogFileDescriptor:
    return contracts.LogFileDescriptor(
        canonical_uri=str(path),
        file_kind="dataflow_json",
        revision_json=json.dumps({"size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}),
        read_uri=str(path),
    )


def _write_row(path: Path, row: dict) -> Path:
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return path


def _publish(database: Path, files: list[Path]) -> dict:
    return store.publish_rows_batched(
        11,
        [contracts.LogPublishBatch(dataflow_files=tuple(_descriptor(path) for path in files))],
        [],
        [str(path) for path in files],
        database_path=database,
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_v6_null_first_union_preserves_values_presence_and_reconstruction(tmp_path: Path, reverse: bool) -> None:
    sample = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    null_row = deepcopy(sample)
    null_row["dataflow_run_id"] = "v6-null-run"
    null_row["message"] = None
    null_row["metrics"]["rows_read"] = None
    null_row["attempts"][-1]["source"]["rows_read"] = None

    populated_row = deepcopy(sample)
    populated_row["dataflow_run_id"] = "v6-populated-run"
    populated_row["message"] = "authored message"
    populated_row["metrics"]["rows_read"] = 9007199254740993
    populated_row["attempts"][-1]["source"]["rows_read"] = 9007199254740993

    null_file = _write_row(tmp_path / "null.jsonl", null_row)
    populated_file = _write_row(tmp_path / "populated.jsonl", populated_row)
    files = [populated_file, null_file] if reverse else [null_file, populated_file]
    database = tmp_path / "analytics.duckdb"
    result = _publish(database, files)
    assert result["published"], result

    with duckdb.connect(str(database), read_only=True) as connection:
        columns = [row[0] for row in connection.execute(f"DESCRIBE {schema.DATAFLOW_TABLE}").fetchall()]
        rows = {
            row[columns.index("dataflow_run_id")]: dict(zip(columns, row))
            for row in connection.execute(f"SELECT * FROM {schema.DATAFLOW_TABLE}").fetchall()
        }
        source_type = connection.execute(
            f"SELECT typeof(metrics.rows_read) FROM {schema.DATAFLOW_TABLE} LIMIT 1"
        ).fetchone()[0]

    assert source_type == "HUGEINT"
    assert rows["v6-null-run"]["message"] is None
    assert "_present_paths" not in rows["v6-null-run"]
    assert rows["v6-null-run"]["metrics"]["rows_read"] is None
    assert rows["v6-populated-run"]["message"] == "authored message"
    assert "_present_paths" not in rows["v6-populated-run"]
    assert int(rows["v6-populated-run"]["metrics"]["rows_read"]) == 9007199254740993
    assert reconstruct_record(rows["v6-null-run"], "dataflow_run_log") == {**null_row, "diagnostic_issues": None}
    assert reconstruct_record(rows["v6-populated-run"], "dataflow_run_log") == {**populated_row, "diagnostic_issues": None}


def test_v6_omitted_message_collapses_with_explicit_null(tmp_path: Path) -> None:
    sample = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    explicit_null = deepcopy(sample)
    explicit_null["dataflow_run_id"] = "explicit-null"
    explicit_null["message"] = None
    omitted = deepcopy(sample)
    omitted["dataflow_run_id"] = "omitted"
    omitted.pop("message")
    files = [_write_row(tmp_path / "explicit.jsonl", explicit_null), _write_row(tmp_path / "omitted.jsonl", omitted)]
    result = _publish(tmp_path / "analytics.duckdb", files)
    assert result["published"], result
    with duckdb.connect(str(tmp_path / "analytics.duckdb"), read_only=True) as connection:
        rows = connection.execute(
            f"SELECT dataflow_run_id, message, message IS NOT NULL FROM {schema.DATAFLOW_TABLE} ORDER BY dataflow_run_id"
        ).fetchall()
    assert rows == [("explicit-null", None, False), ("omitted", None, False)]


def test_v6_malformed_batch_keeps_previous_generation_and_rows(tmp_path: Path) -> None:
    sample = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    valid_file = _write_row(tmp_path / "valid.jsonl", sample)
    database = tmp_path / "analytics.duckdb"
    first = _publish(database, [valid_file])
    assert first["published"], first
    with duckdb.connect(str(database), read_only=True) as connection:
        before = connection.execute(
            f"SELECT (SELECT generation FROM {schema.ANALYTICS_META_TABLE}), "
            f"(SELECT COUNT(*) FROM {schema.DATAFLOW_TABLE})"
        ).fetchone()

    malformed = deepcopy(sample)
    malformed["dataflow_run_id"] = "malformed"
    malformed.pop("metrics")
    malformed_file = _write_row(tmp_path / "malformed.jsonl", malformed)
    failed = _publish(database, [malformed_file])
    assert not failed["published"]
    assert failed["errors"][0]["code"] == "schema_incompatible"
    with duckdb.connect(str(database), read_only=True) as connection:
        after = connection.execute(
            f"SELECT (SELECT generation FROM {schema.ANALYTICS_META_TABLE}), "
            f"(SELECT COUNT(*) FROM {schema.DATAFLOW_TABLE})"
        ).fetchone()
        run_ids = connection.execute(f"SELECT dataflow_run_id FROM {schema.DATAFLOW_TABLE}").fetchall()
    assert after == before
    assert run_ids == [(sample["dataflow_run_id"],)]
