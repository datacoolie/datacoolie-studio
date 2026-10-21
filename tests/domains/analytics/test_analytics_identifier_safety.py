from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import duckdb
import pytest

from datacoolie_studio.domains.analytics import schema, store
from datacoolie_studio.domains.analytics.contracts import LogFileDescriptor
from tests.support.v6_fixture import deepcopy_v6_rows


def _descriptor(path: Path, file_kind: str) -> LogFileDescriptor:
    return LogFileDescriptor(
        canonical_uri=str(path),
        file_kind=file_kind,
        revision_json=json.dumps(
            {"size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
        ),
        read_uri=str(path),
    )


def _write(path: Path, row: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return path


def _state(path: Path) -> tuple[int, int, str]:
    with duckdb.connect(str(path), read_only=True) as conn:
        generation, row_count, build_state = conn.execute(
            f"""
            SELECT
              (SELECT generation FROM {schema.ANALYTICS_META_TABLE} WHERE singleton_id = 1),
              (SELECT COUNT(*) FROM {schema.JOB_TABLE}),
              (SELECT build_state FROM {schema.ANALYTICS_META_TABLE} WHERE singleton_id = 1)
            """
        ).fetchone()
    return int(generation), int(row_count), str(build_state)


@pytest.mark.parametrize("file_kind", ["dataflow_json", "job_json"])
def test_v6_file_object_and_array_ids_rejected_before_typed_read(
    tmp_path: Path,
    file_kind: str,
):
    database_path = tmp_path / f"{file_kind}.duckdb"
    good_path = tmp_path / f"good-{file_kind}.json"
    bad_path = tmp_path / f"bad-{file_kind}.json"
    if file_kind == "dataflow_json":
        good = deepcopy_v6_rows("dataflow_snapshot.json")[0]
        bad = deepcopy(good)
        bad["dataflow_run_id"] = "bad-dataflow-id"
    else:
        good = deepcopy_v6_rows("job_snapshot.json")[0]
        bad = deepcopy(good)
        bad["job_id"] = {"nested": "J"}
    if file_kind == "dataflow_json":
        bad["dataflow_id"] = ["D"]

    _write(good_path, good)
    _write(bad_path, bad)
    first = store.publish_rows(
        7,
        [_descriptor(good_path, file_kind)] if file_kind == "dataflow_json" else [],
        [],
        [],
        [],
        database_path=database_path,
        job_files=[_descriptor(good_path, file_kind)] if file_kind == "job_json" else None,
    )
    assert first["published"] is True, first
    before = _state(database_path)

    second = store.publish_rows(
        7,
        [_descriptor(bad_path, file_kind)] if file_kind == "dataflow_json" else [],
        [],
        [],
        [],
        database_path=database_path,
        job_files=[_descriptor(bad_path, file_kind)] if file_kind == "job_json" else None,
    )

    assert second["published"] is False
    assert second["errors"][0]["code"] == "schema_incompatible"
    assert any(identifier in second["errors"][0]["message"] for identifier in ("dataflow_id", "job_id"))
    assert _state(database_path) == before


def test_v6_numeric_identifier_values_remain_authored_scalars(tmp_path: Path):
    database_path = tmp_path / "numeric-identifiers.duckdb"
    row = deepcopy_v6_rows("job_snapshot.json")[0]
    row["job_id"] = "12345678901234567890"
    path = _write(tmp_path / "numeric-id.json", row)
    result = store.publish_rows(
        7,
        [],
        [],
        [],
        [],
        database_path=database_path,
        job_files=[_descriptor(path, "job_json")],
    )

    assert result["published"] is True, result
    with duckdb.connect(str(database_path), read_only=True) as conn:
        value = conn.execute("SELECT job_id FROM etl_job_runs").fetchone()[0]
    assert value == "12345678901234567890"
