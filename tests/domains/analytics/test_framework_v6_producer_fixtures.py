from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from datacoolie_studio.domains.analytics import contracts, schema, store
from datacoolie_studio.domains.analytics.log_contract import admit_log_record
from tests.support.v6_fixture import (
    FIXTURE_DIR,
    coherent_v6_ledger,
    deepcopy_v6_rows,
    v6_rows,
)


DATAFLOW_FILES = (
    "dataflow_snapshot.json",
    "dataflow_part_00000001.json",
    "dataflow_part_00000002.json",
)
DIAGNOSTIC_FILES = ("dataflow_diagnostics_snapshot.json",)
JOB_FILES = ("job_snapshot.json", "job_bounded_snapshot.json")
SYSTEM_FILES = ("system_snapshot.json", "system_part_00000001.json")


def _descriptor(path: Path, kind: str) -> contracts.LogFileDescriptor:
    return contracts.LogFileDescriptor(
        canonical_uri=str(path),
        file_kind=kind,
        revision_json=json.dumps({"size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}),
        read_uri=str(path),
    )


def _publish_fixture(
    database: Path,
    *,
    dataflow_files: tuple[Path, ...] = tuple(FIXTURE_DIR / name for name in DATAFLOW_FILES),
    job_files: tuple[Path, ...] = (FIXTURE_DIR / "job_snapshot.json",),
) -> dict:
    return store.publish_rows_batched(
        7,
        [
            contracts.LogPublishBatch(
                dataflow_files=tuple(_descriptor(path, "dataflow_json") for path in dataflow_files),
                job_files=tuple(_descriptor(path, "job_json") for path in job_files),
            )
        ],
        [],
        [str(path) for path in (*dataflow_files, *job_files)],
        database_path=database,
    )


def _utc(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _exact_metric(value: object) -> int | None:
    return None if value is None else int(value)


def test_framework_v6_fixture_manifest_hashes_and_exact_shape() -> None:
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 6
    assert manifest["producer_version"] == "0.2.0"
    assert manifest["fixture_profile"] == "canonical-execution-v6-t6-terminal-cases"

    expected_types = {"dataflow": "dataflow_run_log", "job": "job_run_log", "system": "system_log"}
    stream_rows: dict[str, list[dict]] = {}
    for name, details in manifest["streams"].items():
        payload = (FIXTURE_DIR / name).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == details["sha256"]
        rows = [json.loads(line) for line in payload.splitlines() if line.strip()]
        assert len(rows) == details["rows"]
        assert {row["_type"] for row in rows} == {expected_types[details["kind"]]}
        assert all(row["log_schema_version"] == 6 for row in rows)
        stream_rows[name] = rows

    dataflows = [row for name in DATAFLOW_FILES for row in stream_rows[name]]
    assert len(dataflows) == 17
    assert {row["status"] for row in dataflows} == {"succeeded", "skipped", "failed"}
    assert {row["log_session_id"] for row in dataflows} == {"fixture-v6-session"}
    assert {row["job_id"] for row in dataflows} == {"fixture-v6-job"}
    replay = [row for row in dataflows if row["dataflow_id"] == "replay-chunks"]
    assert {row["dataflow_run_id"] for row in replay} == {
        "chunk-success",
        "chunk-skipped",
        "chunk-failed",
    }
    diagnostics = [row for name in DIAGNOSTIC_FILES for row in stream_rows[name]]
    assert len(diagnostics) == 4
    assert all(row["diagnostic_issues"] for row in diagnostics)

    success = next(row for row in dataflows if row["dataflow_run_id"] == "fixture-v6-success-run")
    assert success["metrics"] == {
        "rows_read": 42,
        "rows_written": 40,
        "rows_inserted": 40,
        "rows_updated": 0,
        "rows_deleted": 0,
        "files_added": 2,
        "files_removed": 1,
        "bytes_added": 2048,
        "bytes_removed": 512,
        "bytes_saved": 0,
    }
    assert success["attempts"][0]["source"]["selection"]["lower"]["event_ts"] == {
        "__datetime__": "2026-10-01T00:00:00+00:00"
    }
    watermark = success["attempts"][0]["watermark"]
    assert watermark["candidate"]["values"]["amount"] == {"__decimal__": "12.50"}
    assert watermark["candidate"]["values"]["items"] == [
        {"__date__": "2026-10-04"},
        {"__decimal__": "0.25"},
    ]
    assert watermark["candidate"]["values"]["payload"] == {"__binary__": "djYA/w=="}
    assert stream_rows["system_snapshot.json"][0]["dataflow_run_id"] == "fixture-v6-success-run"


def test_framework_v6_records_are_admitted_without_mutating_exact_values() -> None:
    for filename in (*DATAFLOW_FILES, *JOB_FILES, *SYSTEM_FILES):
        expected_type = {
            "dataflow": "dataflow_run_log",
            "job": "job_run_log",
            "system": "system_log",
        }["dataflow" if filename.startswith("dataflow") else "job" if filename.startswith("job") else "system"]
        for line_number, row in enumerate(v6_rows(filename), 1):
            original = deepcopy(row)
            assert admit_log_record(row, expected_type=expected_type, uri=filename, line=line_number)
            assert row == original


def test_coherent_v6_ledger_has_matching_nullable_job_totals() -> None:
    ledger = coherent_v6_ledger()
    dataflows = ledger["dataflow"]
    job = ledger["job"][0]
    assert {row["job_id"] for row in dataflows} == {job["job_id"]} == {"coherent-job"}
    assert {row["log_session_id"] for row in dataflows} == {job["log_session_id"]} == {
        "coherent-session"
    }
    assert job["total_dataflows"] == len(dataflows)
    assert job["total_succeeded"] == sum(row["status"] == "succeeded" for row in dataflows)
    assert job["total_failed"] == sum(row["status"] == "failed" for row in dataflows)
    assert job["total_skipped"] == sum(row["status"] == "skipped" for row in dataflows)
    assert job["total_rows_read"] is None
    assert job["total_rows_written"] is None
    assert job["total_files_added"] is None
    assert job["total_bytes_added"] is None
    assert ledger["system"][0]["dataflow_run_id"] == dataflows[0]["dataflow_run_id"]
    for row in dataflows:
        if row["attempts"]:
            assert row["start_time"] <= row["end_time"]
            assert row["duration_seconds"] >= 0
            for attempt in row["attempts"]:
                assert attempt["start_time"] <= attempt["end_time"]
                assert attempt["duration_seconds"] >= 0


def test_framework_v6_ingestion_preserves_rows_nested_evidence_and_temporal_values(tmp_path: Path) -> None:
    database = tmp_path / "analytics.duckdb"
    result = _publish_fixture(database)
    assert result["published"], result
    assert result["parsed_dataflow_records"] == 17
    assert result["parsed_job_records"] == 1

    expected = {
        row["dataflow_run_id"]: row
        for filename in DATAFLOW_FILES
        for row in v6_rows(filename)
    }
    with duckdb.connect(str(database), read_only=True) as connection:
        from datacoolie_studio.domains.analytics.record_codec import reconstruct_record
        cursor = connection.execute(f"SELECT * FROM {schema.DATAFLOW_TABLE} ORDER BY dataflow_run_id")
        names = [column[0] for column in cursor.description]
        stored = [dict(zip(names, values)) for values in cursor.fetchall()]
        assert "raw_record_json" not in names
        assert "metric_eligibility" not in names
        for physical in stored:
            original = expected[physical["dataflow_run_id"]]
            assert reconstruct_record(physical, "dataflow_run_log") == {**{key: None for key in schema.DATAFLOW_COLUMN_TYPES if key not in schema.GENERATED_CACHE_COLUMNS and key != "_record_line"}, **original}
            assert physical["start_time"] == original.get("start_time")
            assert physical["end_time"] == original.get("end_time")
        rows = connection.execute("SELECT f.dataflow_run_id, f.status, f.source_rows_read, f.destination_rows_written, f.start_time, f.end_time, f.source_start_time, f.source_end_time, CAST(r.attempts[-1].destination.operation_details AS VARCHAR) AS destination_operation_details FROM monitoring_dataflow_facts f JOIN etl_dataflow_runs r USING (_source_id, _file_uri, _record_line) ORDER BY f.dataflow_run_id").fetchall()
    assert len(rows) == len(expected)
    for run_id, status, source_rows_read, rows_written, start_time, end_time, source_start, source_end, details in rows:
        original = expected[run_id]
        assert status == original["status"]
        # Typed phase columns describe evidence, while the root metrics are
        # the authoritative run totals.  BIGNUM values may be returned by
        # DuckDB as strings, so compare after an exact integer conversion.
        assert _exact_metric(source_rows_read) == original["metrics"]["rows_read"]
        assert _exact_metric(rows_written) == original["metrics"]["rows_written"]
        assert _utc(start_time) == _utc(original.get("start_time"))
        assert _utc(end_time) == _utc(original.get("end_time"))
        final_attempt = original["attempts"][-1] if original["attempts"] else None
        source = final_attempt.get("source") if final_attempt else None
        destination = final_attempt.get("destination") if final_attempt else None
        assert _utc(source_start) == _utc((source or {}).get("start_time"))
        assert _utc(source_end) == _utc((source or {}).get("end_time"))
        if details is not None:
            assert json.loads(details) == destination["operation_details"]


def test_framework_v6_large_integer_metrics_remain_exact_in_projection(tmp_path: Path) -> None:
    row = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    large = 9_007_199_254_740_993
    wider = 2**80
    widest = 10**30
    row["dataflow_run_id"] = "large-v6-run"
    row["metrics"].update(rows_read=large, rows_written=wider, bytes_added=widest)
    destination = row["attempts"][-1]["destination"]
    destination.update(rows_written=wider, bytes_added=widest)
    row["attempts"][-1]["source"]["rows_read"] = large
    assert admit_log_record(row, expected_type="dataflow_run_log", uri="large.json", line=1)
    path = tmp_path / "large.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    database = tmp_path / "analytics.duckdb"
    result = _publish_fixture(database, dataflow_files=(path,), job_files=())
    assert result["published"], result
    with duckdb.connect(str(database), read_only=True) as connection:
        actual = connection.execute(
            f"SELECT metrics.rows_read, metrics.rows_written, metrics.bytes_added, attempts[-1].destination.bytes_added FROM {schema.DATAFLOW_TABLE}"
        ).fetchone()
    assert tuple(int(value) for value in actual[:3]) == (large, wider, widest)
    assert int(actual[3]) == widest
