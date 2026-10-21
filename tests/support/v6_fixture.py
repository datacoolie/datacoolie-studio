"""Portable v6 fixture readers and a small coherent monitoring ledger.

The files under ``tests/fixtures/datacoolie/logging/v6`` are producer contract
examples.  They intentionally cover independent cases and are not a
reconciled job ledger.  Tests that need reconciliation use the deep-copied
ledger below so producer provenance and consumer semantics stay separate.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "datacoolie" / "logging" / "v6"

_DATAFLOW_FILES = (
    "dataflow_snapshot.json",
    "dataflow_part_00000001.json",
    "dataflow_part_00000002.json",
)

# These identities are the UUID5 values produced by the checked-in
# ``local_use_cases.json`` metadata fixture.  The producer examples remain
# independent synthetic streams; only the coherent consumer ledger uses the
# metadata identities so API lineage/latest joins have a stable key.
_COHERENT_PROFILES = {
    "read__csv": {
        "dataflow_id": "f024de68-d823-5cca-9e8f-ef268e33e41f",
        "source_id": "bfd6dd36-748c-5345-abca-8e94ac08a296",
        "source_name": "local_csv_source",
        "source_format": "csv",
        "source_path": "./usecase-sim/data/input/csv/sample",
        "destination_id": "764672c6-6221-548e-b665-a07d29a14318",
        "destination_name": "local_parquet_dest",
        "destination_table": "orders_read_csv",
        "destination_path": "./usecase-sim/data/output/parquet/orders_read_csv",
    },
    "read__json": {
        "dataflow_id": "f73ff066-3773-5aa3-8da9-a3c061a63515",
        "source_id": "3dbb0b54-b553-59d9-81ba-b3c537f4fdda",
        "source_name": "local_json_source",
        "source_format": "json",
        "source_path": "./usecase-sim/data/input/json/sample",
        "destination_id": "764672c6-6221-548e-b665-a07d29a14318",
        "destination_name": "local_parquet_dest",
        "destination_table": "orders_read_json",
        "destination_path": "./usecase-sim/data/output/parquet/orders_read_json",
    },
    "read__jsonl": {
        "dataflow_id": "969d6b16-131f-52fa-a948-974d8560135a",
        "source_id": "29852589-a101-57f6-8cd5-0d4650c6f16c",
        "source_name": "local_jsonl_source",
        "source_format": "jsonl",
        "source_path": "./usecase-sim/data/input/jsonl/sample",
        "destination_id": "764672c6-6221-548e-b665-a07d29a14318",
        "destination_name": "local_parquet_dest",
        "destination_table": "orders_read_jsonl",
        "destination_path": "./usecase-sim/data/output/parquet/orders_read_jsonl",
    },
    "read__parquet": {
        "dataflow_id": "db480d74-822f-507f-a2f5-b0809438ac1e",
        "source_id": "54c47f2e-91aa-50ae-a38e-12ff18e25232",
        "source_name": "local_parquet_source",
        "source_format": "parquet",
        "source_path": "./usecase-sim/data/input/parquet/sample",
        "destination_id": "764672c6-6221-548e-b665-a07d29a14318",
        "destination_name": "local_parquet_dest",
        "destination_table": "orders_read_parquet",
        "destination_path": "./usecase-sim/data/output/parquet/orders_read_parquet",
    },
}

_COHERENT_RUN_PROFILES = {
    "fixture-v6-success-run": "read__csv",
    "fixture-v6-retry-success-run": "read__csv",
    "fixture-v6-skipped-run": "read__csv",
    "fixture-v6-failed-run": "read__csv",
    "fixture-v6-transformed-empty-run": "read__json",
    "fixture-v6-maintenance-run": "read__jsonl",
    "fixture-v6-sparse-metrics-run": "read__parquet",
}


def v6_rows(filename: str) -> list[dict[str, Any]]:
    """Read one pinned v6 JSONL stream without importing the producer package."""

    return [
        json.loads(line)
        for line in (FIXTURE_DIR / filename).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def deepcopy_v6_rows(*filenames: str) -> list[dict[str, Any]]:
    """Return independent copies of rows from the portable consumer fixtures."""

    selected = filenames or _DATAFLOW_FILES
    return [copy.deepcopy(row) for filename in selected for row in v6_rows(filename)]


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _retime_row(row: dict[str, Any], *, start: datetime, index: int) -> dict[str, Any]:
    """Retain a producer case while making its root and phase intervals coherent."""

    row["log_schema_version"] = 6
    row["log_session_id"] = "coherent-session"
    row["job_id"] = "coherent-job"
    row["workspace_id"] = "coherent-workspace"
    row["job_num"] = 1
    row["job_index"] = index
    profile_name = _COHERENT_RUN_PROFILES.get(str(row.get("dataflow_run_id")))
    profile = _COHERENT_PROFILES.get(profile_name)
    if profile is not None:
        row.update(
            dataflow_id=profile["dataflow_id"],
            dataflow_name=profile_name,
            stage="read_file",
            source_id=profile["source_id"],
            source_name=profile["source_name"],
            source_connection_type="file",
            source_format=profile["source_format"],
            source_table="sample",
            source_full_table="sample",
            source_path=profile["source_path"],
            destination_id=profile["destination_id"],
            destination_name=profile["destination_name"],
            destination_connection_type="file",
            destination_format="parquet",
            destination_table=profile["destination_table"],
            destination_full_table=profile["destination_table"],
            destination_path=profile["destination_path"],
            destination_load_type="full_load",
        )
    else:
        row["dataflow_id"] = str(row.get("dataflow_id") or "unknown")

    attempts = row.get("attempts") or []
    cursor = start
    for attempt in attempts:
        attempt_start = cursor
        phase_cursor = attempt_start
        source = attempt.get("source")
        if source is not None:
            source_start = phase_cursor
            phase_cursor = source_start + timedelta(seconds=2)
            source.update(
                start_time=_iso(source_start),
                end_time=_iso(phase_cursor),
                duration_seconds=2.0,
            )
        transform = attempt.get("transform")
        if transform is not None:
            transform_start = phase_cursor
            phase_cursor = transform_start + timedelta(seconds=2)
            transform.update(
                start_time=_iso(transform_start),
                end_time=_iso(phase_cursor),
                duration_seconds=2.0,
            )
        destination = attempt.get("destination")
        if destination is not None:
            destination_start = phase_cursor
            phase_cursor = destination_start + timedelta(seconds=3)
            destination.update(
                start_time=_iso(destination_start),
                end_time=_iso(phase_cursor),
                duration_seconds=3.0,
            )
        attempt.update(
            start_time=_iso(attempt_start),
            end_time=_iso(phase_cursor),
            duration_seconds=(phase_cursor - attempt_start).total_seconds(),
        )
        cursor = phase_cursor + timedelta(seconds=1)

    if attempts:
        root_start = _parse_time(attempts[0]["start_time"])
        root_end = _parse_time(attempts[-1]["end_time"])
        row.update(
            start_time=_iso(root_start),
            end_time=_iso(root_end),
            duration_seconds=(root_end - root_start).total_seconds(),
        )
    else:
        row.update(start_time=_iso(start), end_time=_iso(start), duration_seconds=0.0)
    return row


def _nullable_sum(values: list[Any]) -> int | None:
    """Sum a metric only when every row in the selected population is known."""

    if any(value is None for value in values):
        return None
    return sum(int(value) for value in values)


def coherent_v6_ledger() -> dict[str, list[dict[str, Any]]]:
    """Build a deterministic v6 ledger for reconciliation and report tests.

    Rows are copied from the pinned producer examples, then given one source,
    session and job identity.  Phase intervals are retimed to fit each root
    interval.  The job snapshot is derived from those rows, including nullable
    totals when a row does not report a metric.
    """

    rows_by_run = {row["dataflow_run_id"]: row for row in deepcopy_v6_rows()}
    selected_runs = (
        "fixture-v6-success-run",
        "fixture-v6-retry-success-run",
        "fixture-v6-transformed-empty-run",
        "fixture-v6-maintenance-run",
        "fixture-v6-sparse-metrics-run",
        "fixture-v6-skipped-run",
        "fixture-v6-failed-run",
    )
    base = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    dataflow_rows = [
        _retime_row(rows_by_run[run_id], start=base + timedelta(minutes=index), index=index)
        for index, run_id in enumerate(selected_runs)
    ]

    metric_fields = {
        "rows_read": "total_rows_read",
        "rows_written": "total_rows_written",
        "rows_inserted": "total_rows_inserted",
        "rows_updated": "total_rows_updated",
        "rows_deleted": "total_rows_deleted",
        "files_added": "total_files_added",
        "files_removed": "total_files_removed",
        "bytes_added": "total_bytes_added",
        "bytes_removed": "total_bytes_removed",
    }
    job = next(row for row in deepcopy_v6_rows("job_snapshot.json") if row["_type"] == "job_run_log")
    job.update(
        log_schema_version=6,
        log_session_id="coherent-session",
        job_id="coherent-job",
        workspace_id="coherent-workspace",
        job_num=1,
        job_index=0,
        status="failed",
        start_time=min(row["start_time"] for row in dataflow_rows),
        end_time=max(row["end_time"] for row in dataflow_rows),
        duration_seconds=(
            _parse_time(max(row["end_time"] for row in dataflow_rows))
            - _parse_time(min(row["start_time"] for row in dataflow_rows))
        ).total_seconds(),
        observed_at="2026-11-01T12:10:00+00:00",
        total_dataflows=len(dataflow_rows),
        total_succeeded=sum(row["status"] == "succeeded" for row in dataflow_rows),
        total_failed=sum(row["status"] == "failed" for row in dataflow_rows),
        total_skipped=sum(row["status"] == "skipped" for row in dataflow_rows),
        total_running=None,
        total_pending=None,
        log_records_dropped=0,
        log_bytes_dropped=0,
    )
    for metric, total in metric_fields.items():
        job[total] = _nullable_sum([row["metrics"][metric] for row in dataflow_rows])

    system = next(row for row in deepcopy_v6_rows("system_snapshot.json") if row["_type"] == "system_log")
    system.update(
        log_schema_version=6,
        log_session_id="coherent-session",
        job_id="coherent-job",
        job_num=1,
        job_index=0,
        dataflow_id=dataflow_rows[0]["dataflow_id"],
        dataflow_run_id=dataflow_rows[0]["dataflow_run_id"],
        ts="2026-11-01T12:00:00+00:00",
    )
    return {"dataflow": dataflow_rows, "job": [job], "system": [system]}


def write_coherent_ledger(directory: Path) -> dict[str, Path]:
    """Write a coherent ledger as JSONL and return its stream paths."""

    ledger = coherent_v6_ledger()
    directory.mkdir(parents=True, exist_ok=True)
    names = {"dataflow": "dataflow.jsonl", "job": "job.jsonl", "system": "system.jsonl"}
    paths: dict[str, Path] = {}
    for kind, rows in ledger.items():
        path = directory / names[kind]
        path.write_text(
            "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
            encoding="utf-8",
        )
        paths[kind] = path
    return paths


__all__ = [
    "FIXTURE_DIR",
    "coherent_v6_ledger",
    "deepcopy_v6_rows",
    "v6_rows",
    "write_coherent_ledger",
]
