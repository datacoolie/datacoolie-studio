"""Compare schema16 serving views with schema17 physical Monitoring facts.

The benchmark writes one generated v6 source byte stream into an isolated
DuckDB file.  The target keeps the current physical serving facts; an optional
external ``serving_facts.py`` snapshot is applied to a checkpointed clone of
the same source database for the old schema16 view layout.  Both layouts then
run the same bounded micro probes and actual Monitoring read models under one
DuckDB thread, 512 MiB, UTC, and a fixed SQL ``current_timestamp``.  Receipts
are local qualification evidence, not production performance claims.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import statistics
import tempfile
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


BENCHMARK_THREADS = 1
BENCHMARK_MEMORY_LIMIT = "512MiB"
BENCHMARK_TIMEZONE = "UTC"
BENCHMARK_NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
BASELINE_SCHEMA_VERSION = 16
TARGET_SCHEMA_VERSION = 17
BASELINE_LABEL = "schema16_view"
TARGET_LABEL = "schema17_physical_facts"
# Keep these aliases for callers that imported the original diagnostic labels.
LEGACY_LABEL = BASELINE_LABEL
MICRO_QUERY_NAMES = (
    "Failures_physical_sql",
    "Freshness_physical_sql",
    "Performance_physical_metrics_sql",
    "Diagnostics_source_evidence_sql",
    "identity_page_probe",
    "record_roundtrip",
)
ACTUAL_READ_MODEL_NAMES = (
    "Failures",
    "Freshness",
    "Performance",
    "Diagnostics",
    "latest_page",
    "record",
)


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rows",
        type=int,
        default=300,
        help="physical dataflow observations in the modest benchmark profile",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help="unique job IDs; each receives an earlier and later snapshot",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=4,
        help="cold plus warm executions per query (at least three)",
    )
    parser.add_argument(
        "--baseline-serving-source",
        type=Path,
        default=None,
        help=(
            "optional schema16 serving_facts.py snapshot; when omitted, the "
            "benchmark reports schema17 target-only measurements"
        ),
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=None,
        help="optional JSON receipt path; omitted receipts are printed only",
    )
    return parser.parse_args(argv)


def _validate_arguments(arguments: argparse.Namespace) -> None:
    if arguments.rows < 250:
        raise SystemExit("--rows must be at least 250")
    if arguments.jobs is not None and arguments.jobs < 1:
        raise SystemExit("--jobs must be at least 1")
    if arguments.repeats < 3:
        raise SystemExit("--repeats must be at least 3")
    baseline_source = getattr(arguments, "baseline_serving_source", None)
    if baseline_source is not None and not Path(baseline_source).is_file():
        raise SystemExit(
            f"--baseline-serving-source must name a serving_facts.py file: "
            f"{baseline_source}"
        )


def _fixture_record(filename: str) -> dict[str, Any]:
    fixture = Path(__file__).parents[2] / "tests/fixtures/datacoolie/logging/v6" / filename
    return copy.deepcopy(
        json.loads(next(line for line in fixture.read_text(encoding="utf-8").splitlines() if line.strip()))
    )


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _record_text(record: Mapping[str, Any]) -> str:
    return json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_attempt(template: Mapping[str, Any], *, index: int, status: str, start: datetime) -> dict[str, Any]:
    attempt = copy.deepcopy(dict(template))
    attempt["attempt_number"] = index
    attempt["start_time"] = _iso(start)
    attempt["end_time"] = _iso(start + timedelta(seconds=5 + index))
    attempt["duration_seconds"] = float(5 + index)
    attempt["status"] = status
    attempt["message"] = f"benchmark {status}" if status == "failed" else None
    source = attempt.get("source") or {}
    transform = attempt.get("transform") or {}
    destination = attempt.get("destination") or {}
    for phase in (source, transform, destination):
        phase["status"] = status
        phase["message"] = f"benchmark {status}" if status == "failed" else None
    source["start_time"] = _iso(start)
    source["end_time"] = _iso(start + timedelta(seconds=2))
    source["duration_seconds"] = 2.0
    source["rows_read"] = 100 + index
    transform["start_time"] = _iso(start + timedelta(seconds=2))
    transform["end_time"] = _iso(start + timedelta(seconds=4))
    transform["duration_seconds"] = 2.0
    destination["start_time"] = _iso(start + timedelta(seconds=4))
    destination["end_time"] = _iso(start + timedelta(seconds=5 + index))
    destination["duration_seconds"] = float(1 + index)
    destination["rows_written"] = 90 + index
    destination["rows_inserted"] = 80 + index
    destination["rows_updated"] = index
    destination["rows_deleted"] = 0
    destination["files_added"] = 1
    destination["files_removed"] = 0
    destination["bytes_added"] = 1_000 + index
    destination["bytes_removed"] = 0
    destination["bytes_saved"] = 0
    attempt["source"] = source
    attempt["transform"] = transform
    attempt["destination"] = destination
    return attempt


def _make_dataflow_records(count: int, jobs: int) -> list[dict[str, Any]]:
    templates = [
        _fixture_record("dataflow_snapshot.json"),
    ]
    attempt_template = templates[0]["attempts"][0]
    rows: list[dict[str, Any]] = []
    logical_index = 0
    while len(rows) < count:
        record = copy.deepcopy(templates[logical_index % len(templates)])
        status = ("succeeded", "failed", "skipped")[logical_index % 3]
        start = datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(minutes=logical_index)
        record.update(
            {
                "log_schema_version": 6,
                "_type": "dataflow_run_log",
                "log_session_id": f"benchmark-session-{logical_index % 3}",
                "job_id": f"benchmark-job-{logical_index % jobs}",
                "dataflow_id": f"benchmark-flow-{logical_index % max(1, jobs // 2)}",
                "dataflow_run_id": f"benchmark-run-{logical_index:06d}",
                "status": status,
                "message": f"benchmark {status}" if status == "failed" else None,
                "start_time": _iso(start),
                "end_time": _iso(start + timedelta(seconds=10 + logical_index % 11)),
                "duration_seconds": float(10 + logical_index % 11) + 0.25,
                "retry_attempts": logical_index % 4,
                "metrics": {
                    "rows_read": 1_000 + logical_index * 3,
                    "rows_written": 900 + logical_index * 3,
                    "rows_inserted": 850 + logical_index * 3,
                    "rows_updated": logical_index % 17,
                    "rows_deleted": logical_index % 5,
                    "files_added": 1,
                    "files_removed": logical_index % 2,
                    "bytes_added": 10_000 + logical_index,
                    "bytes_removed": logical_index,
                    "bytes_saved": logical_index * 2,
                },
            }
        )
        if logical_index % 9 == 0:
            record["attempts"] = []
        else:
            attempts = [_make_attempt(attempt_template, index=1, status=status, start=start)]
            if logical_index % 7 == 0:
                attempts.append(
                    _make_attempt(
                        attempt_template,
                        index=2,
                        status=status,
                        start=start + timedelta(seconds=20),
                    )
                )
            record["attempts"] = attempts
        if logical_index % 13 == 0:
            record["diagnostic_issues"] = [
                {"field": "metrics.rows_read", "reason": "invalid"},
                {"field": "attempts", "reason": "omitted"},
            ]
        else:
            record.pop("diagnostic_issues", None)
        rows.append(record)
        if len(rows) < count and logical_index % 10 == 0:
            # Equal observations keep the same canonical identity and payload;
            # the target therefore retains both physical rows and one view row.
            rows.append(copy.deepcopy(record))
        logical_index += 1
    return rows[:count]


def _make_job_records(count: int) -> list[dict[str, Any]]:
    template = _fixture_record("job_snapshot.json")
    rows: list[dict[str, Any]] = []
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for index in range(count):
        job_id = f"benchmark-job-{index}"
        earlier = copy.deepcopy(template)
        earlier.update(
            {
                "log_schema_version": 6,
                "_type": "job_run_log",
                "log_session_id": f"benchmark-session-{index % 3}",
                "job_id": job_id,
                "status": "running",
                "start_time": _iso(base + timedelta(minutes=index)),
                "end_time": _iso(base + timedelta(minutes=index, seconds=30)),
                "observed_at": _iso(base + timedelta(minutes=index, seconds=31)),
                "duration_seconds": 30.5,
                "retry_delay": 0.125 + index / 1000,
                "total_dataflows": 10 + index,
                "total_succeeded": 7 + index,
                "total_failed": 1,
                "total_rows_read": 1000 + index * 3,
                "total_rows_written": 900 + index * 3,
            }
        )
        later = copy.deepcopy(earlier)
        later.update(
            {
                "status": "failed" if index % 4 == 0 else "succeeded",
                "end_time": _iso(base + timedelta(minutes=index, seconds=60)),
                "observed_at": _iso(base + timedelta(minutes=index, seconds=61)),
                "duration_seconds": 60.75,
                "message": "benchmark failure" if index % 4 == 0 else None,
            }
        )
        rows.extend((earlier, later))
    return rows


def _write_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(_record_text(record) for record in records) + "\n"
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def _semantic(value: Any) -> Any:
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, (int, float, Decimal)):
        return ("number", str(Decimal(str(value))))
    if isinstance(value, (datetime,)):
        return ("datetime", value.isoformat())
    if isinstance(value, dict):
        return ("object", tuple(sorted((str(key), _semantic(item)) for key, item in value.items())))
    if isinstance(value, (tuple, list)):
        return ("array", tuple(_semantic(item) for item in value))
    return (type(value).__name__, str(value))


def _public(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_public(item) for item in value]
    if isinstance(value, list):
        return [_public(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _public(item) for key, item in value.items()}
    return value


def _semantic_digest(value: Any) -> str:
    encoded = json.dumps(_public(_semantic(value)), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _configure_connection(connection: Any, temp_directory: Path) -> None:
    connection.execute(f"SET threads = {BENCHMARK_THREADS}")
    connection.execute(f"SET memory_limit = '{BENCHMARK_MEMORY_LIMIT}'")
    connection.execute(f"SET TimeZone = '{BENCHMARK_TIMEZONE}'")
    connection.execute(f"SET temp_directory = '{str(temp_directory).replace(chr(39), chr(39) * 2)}'")


def _load_serving_source(path: Path) -> Any:
    """Load an old serving-facts implementation without replacing the live module."""

    module_name = (
        "_root_attributes_baseline_serving_"
        f"{hashlib.sha256(str(path).encode()).hexdigest()[:12]}"
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load baseline serving source: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "rebuild_monitoring_serving_facts", None)):
        raise RuntimeError(
            f"Baseline serving source has no rebuild_monitoring_serving_facts: {path}"
        )
    return module


def _drop_serving_facts(connection: Any) -> None:
    for relation in ("monitoring_dataflow_facts", "monitoring_job_facts"):
        table_type = connection.execute(
            "SELECT table_type FROM information_schema.tables WHERE table_name = ?",
            [relation],
        ).fetchone()
        if table_type is None:
            continue
        if table_type[0] == "VIEW":
            connection.execute(f"DROP VIEW {relation}")
        else:
            connection.execute(f"DROP TABLE {relation}")


def _compact_database(path: Path) -> float:
    """Rewrite a temporary database after replacing physical facts with views."""

    import duckdb

    compacted = path.with_name(f"{path.stem}-compacted{path.suffix}")
    if compacted.exists():
        compacted.unlink()
    started = time.perf_counter()
    try:
        with duckdb.connect(str(path)) as connection:
            source_name = str(connection.execute("PRAGMA database_list").fetchone()[1])
            compacted_sql = str(compacted).replace("'", "''")
            source_sql = source_name.replace('"', '""')
            connection.execute(f"ATTACH '{compacted_sql}' AS compacted")
            connection.execute(
                f'COPY FROM DATABASE "{source_sql}" TO compacted'
            )
            connection.execute("DETACH compacted")
        path.unlink()
        compacted.replace(path)
    except Exception:
        if compacted.exists():
            compacted.unlink()
        raise
    return (time.perf_counter() - started) * 1_000


def _copy_database(source: Path, destination: Path) -> float:
    """Clone a checkpointed source database for the alternate serving layout."""

    if destination.exists():
        destination.unlink()
    started = time.perf_counter()
    shutil.copyfile(source, destination)
    return (time.perf_counter() - started) * 1_000


def _schema16_view_layout(
    path: Path,
    *,
    temp_directory: Path,
    dataflow_path: Path,
    job_path: Path,
    baseline_serving_source: Path,
    source_database: Path | None = None,
) -> dict[str, Any]:
    """Use shared source observations, then rebuild schema16 views externally."""

    # The source database is cloned after target checkpointing when supplied.
    # This keeps admission metadata such as _ingested_at identical across
    # layouts; only the derived serving relations are replaced below.
    source_clone_ms = None
    if source_database is None:
        publication = _typed_layout(
            path,
            temp_directory=temp_directory,
            dataflow_path=dataflow_path,
            job_path=job_path,
        )
    else:
        source_clone_ms = _copy_database(source_database, path)
        publication = {
            "result": {
                "published": True,
                "source_clone_duration_ms": round(source_clone_ms, 3),
            },
            "elapsed_ms": round(source_clone_ms, 3),
        }
    import duckdb

    from datacoolie_studio.domains.analytics import schema

    serving = _load_serving_source(baseline_serving_source)
    with duckdb.connect(str(path)) as connection:
        _configure_connection(connection, temp_directory)
        _drop_serving_facts(connection)
        started = time.perf_counter()
        serving.rebuild_monitoring_serving_facts(
            connection,
            dataflow_table=schema.DATAFLOW_TABLE,
            job_table=schema.JOB_TABLE,
            dataflow_column_types=schema.cache_table_column_types(schema.DATAFLOW_COLUMN_TYPES),
            job_column_types=schema.cache_table_column_types(schema.JOB_COLUMN_TYPES),
        )
        view_rebuild_ms = (time.perf_counter() - started) * 1_000
        connection.execute(
            "UPDATE etl_analytics_meta SET schema_version = ?, build_state = 'ready' "
            "WHERE singleton_id = 1",
            [BASELINE_SCHEMA_VERSION],
        )
        connection.execute("CHECKPOINT")
    view_compaction_ms = _compact_database(path)
    return {
        **publication,
        "view_rebuild_ms": round(view_rebuild_ms, 3),
        "view_compaction_ms": round(view_compaction_ms, 3),
        "source_clone_ms": round(source_clone_ms, 3) if source_clone_ms is not None else None,
        "serving_source": str(baseline_serving_source),
    }


def _typed_layout(
    path: Path,
    *,
    temp_directory: Path,
    dataflow_path: Path,
    job_path: Path,
) -> dict[str, Any]:
    from datacoolie_studio.domains.analytics import contracts, store

    def from_file(p: Path, kind: str) -> contracts.LogFileDescriptor:
        return contracts.LogFileDescriptor(
            canonical_uri=f"benchmark://{p.name}",
            file_kind=kind,
            revision_json=json.dumps({"size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}),
            read_uri=str(p),
            partition_date="2026-10-01",
        )
    dataflow_file = from_file(dataflow_path, "dataflow_json")
    job_file = from_file(job_path, "job_json")
    started = time.perf_counter()
    result = store.publish_rows_batched(
        1,
        [
            contracts.LogPublishBatch(
                dataflow_files=(dataflow_file,),
                job_files=(job_file,),
            )
        ],
        [],
        [],
        database_path=path,
    )
    elapsed_ms = (time.perf_counter() - started) * 1_000
    if not result.get("published"):
        raise RuntimeError(json.dumps(result, default=str, sort_keys=True))
    return {"result": result, "elapsed_ms": round(elapsed_ms, 3)}


class _FixedClockConnection:
    """Proxy DuckDB execution so both layouts use one SQL current_timestamp."""

    _CURRENT_TIMESTAMP = re.compile(r"\bcurrent_timestamp\b", re.IGNORECASE)

    def __init__(self, connection: Any, now: datetime) -> None:
        self._connection = connection
        utc_now = now.astimezone(timezone.utc).replace(tzinfo=None)
        self._literal = (
            "TIMESTAMPTZ '"
            f"{utc_now.isoformat(sep=' ', timespec='microseconds')}+00'"
        )

    def execute(self, query: str, parameters: Any = None) -> Any:
        rewritten = self._CURRENT_TIMESTAMP.sub(self._literal, query)
        if parameters is None:
            return self._connection.execute(rewritten)
        return self._connection.execute(rewritten, parameters)

    def __enter__(self) -> "_FixedClockConnection":
        return self

    def __exit__(self, *exc: Any) -> None:
        self._connection.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def _open_read(path: Path, temp_directory: Path, *, fixed_clock: bool = False):
    import duckdb

    connection = duckdb.connect(str(path), read_only=True)
    _configure_connection(connection, temp_directory)
    return _FixedClockConnection(connection, BENCHMARK_NOW) if fixed_clock else connection


def _inventory(path: Path, temp_directory: Path) -> dict[str, str]:
    with _open_read(path, temp_directory) as connection:
        return {
            str(name): str(table_type)
            for name, table_type in connection.execute(
                "SELECT table_name, table_type FROM information_schema.tables "
                "WHERE table_schema = 'main' ORDER BY table_name"
            ).fetchall()
        }


def _record_from_row(connection: Any, layout: str, record_id: str) -> dict[str, Any]:
    del layout
    from datacoolie_studio.domains.analytics.record_codec import reconstruct_record

    cursor = connection.execute(
        "SELECT * FROM etl_dataflow_runs WHERE dataflow_run_id = ? "
        "ORDER BY _file_uri, _record_line LIMIT 1",
        [record_id],
    )
    names = [description[0] for description in cursor.description]
    values = cursor.fetchone()
    if values is None:
        raise RuntimeError(f"Source record {record_id!r} is missing")
    return reconstruct_record(dict(zip(names, values)), "dataflow_run_log")


def _execute_query(connection: Any, layout: str, name: str, record_id: str) -> Any:
    facts = "monitoring_dataflow_facts"
    if name == "Failures_physical_sql":
        return connection.execute(
            f"SELECT status, COUNT(*)::BIGINT FROM {facts} "
            "WHERE status = 'failed' GROUP BY status ORDER BY status"
        ).fetchall()
    if name == "Freshness_physical_sql":
        return connection.execute(
            f"SELECT COUNT(*)::BIGINT, MIN(TRY_CAST(start_time AS TIMESTAMPTZ)), "
            f"MAX(TRY_CAST(end_time AS TIMESTAMPTZ)), SUM(CAST(duration_seconds AS DOUBLE)) FROM {facts}"
        ).fetchone()
    if name == "Performance_physical_metrics_sql":
        return connection.execute(
            f"SELECT SUM(CAST(source_rows_read AS BIGNUM)), "
            f"SUM(CAST(destination_rows_written AS BIGNUM)) FROM {facts}"
        ).fetchone()
    if name == "Diagnostics_source_evidence_sql":
        # Diagnostic arrays remain source evidence in schema17.  Query the
        # exhaustive source table for this nested member in both layouts so
        # the comparison measures the same bytes and semantics.
        del layout
        return connection.execute(
            "SELECT SUM(COALESCE(array_length(diagnostic_issues), 0)), COUNT(*)::BIGINT "
            "FROM etl_dataflow_runs"
        ).fetchone()
    if name == "identity_page_probe":
        return connection.execute(
            f"SELECT _source_id, log_session_id, dataflow_id, dataflow_run_id, status, end_time "
            f"FROM {facts} ORDER BY end_time DESC NULLS LAST, dataflow_run_id DESC LIMIT 25 OFFSET 10"
        ).fetchall()
    if name == "record_roundtrip":
        return _record_from_row(connection, layout, record_id)
    raise ValueError(f"Unknown benchmark query {name!r}")


def _query_suite(
    path: Path,
    *,
    layout: str,
    temp_directory: Path,
    record_id: str,
    repeats: int,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for name in MICRO_QUERY_NAMES:
        samples: list[float] = []
        first_result: Any = None
        with _open_read(path, temp_directory) as connection:
            for index in range(repeats):
                started = time.perf_counter()
                result = _execute_query(connection, layout, name, record_id)
                samples.append((time.perf_counter() - started) * 1_000)
                if index == 0:
                    first_result = result
        result_receipt: Any
        if name == "record_roundtrip":
            result_receipt = {
                "semantic_sha256": _semantic_digest(first_result),
                "root_keys": sorted(first_result),
                "attempt_count": len(first_result.get("attempts") or []),
                "diagnostic_count": len(first_result.get("diagnostic_issues") or []),
            }
        else:
            result_receipt = _public(first_result)
        results[name] = {
            "cold_ms": round(samples[0], 3),
            "warm_median_ms": round(statistics.median(samples[1:]), 3),
            "samples_ms": [round(sample, 3) for sample in samples],
            "result": result_receipt,
            "semantic_sha256": _semantic_digest(first_result),
        }
    return results


def _actual_read_model(connection: Any, name: str, record_id: str) -> Any:
    """Run one bounded current read model against the supplied layout."""

    from datacoolie_studio.domains.monitoring.diagnostics_repository import diagnostics_read_model
    from datacoolie_studio.domains.monitoring.log_repository import query_cached_dataflow_logs
    from datacoolie_studio.domains.monitoring.read_models.failures import failures_read_model
    from datacoolie_studio.domains.monitoring.read_models.freshness import freshness_read_model
    from datacoolie_studio.domains.monitoring.read_models.performance import performance_read_model

    generation = str(
        connection.execute(
            "SELECT generation FROM etl_analytics_meta WHERE singleton_id = 1"
        ).fetchone()[0]
    )
    analytics_context = (connection, [1], generation)
    if name == "Failures":
        return failures_read_model(
            [], {}, timezone_name=BENCHMARK_TIMEZONE,
            now=BENCHMARK_NOW,
            analytics_context=analytics_context,
        )
    if name == "Freshness":
        return freshness_read_model(
            [], {}, grain="day", timezone_name=BENCHMARK_TIMEZONE,
            analytics_context=analytics_context,
        )
    if name == "Performance":
        return performance_read_model(
            [], {}, grain="day", timezone_name=BENCHMARK_TIMEZONE,
            analytics_context=analytics_context,
        )
    if name == "Diagnostics":
        return diagnostics_read_model(
            [], {}, grain="day", timezone_name=BENCHMARK_TIMEZONE,
            analytics_context=analytics_context,
        )
    if name == "latest_page":
        rows, total, _warnings, _generation = query_cached_dataflow_logs(
            None,
            [],
            {},
            limit=25,
            offset=10,
            sort_by="end_time",
            sort_dir="desc",
            analytics_context=analytics_context,
        )
        return {"records": rows, "total_records": total}
    if name == "record":
        rows, total, _warnings, _generation = query_cached_dataflow_logs(
            None,
            [],
            {
                "investigateKind": "dataflow_run_id",
                "investigateValue": record_id,
            },
            limit=1,
            offset=0,
            sort_by="start_time",
            sort_dir="desc",
            analytics_context=analytics_context,
        )
        return {"records": rows, "total_records": total}
    raise ValueError(f"Unknown target read model {name!r}")


def _actual_result_receipt(name: str, result: Any) -> dict[str, Any]:
    """Keep target read-model evidence bounded while retaining parity hashes."""

    if name in {"latest_page", "record"}:
        records = result.get("records") or []
        hydrated = sum(
            isinstance(row, Mapping) and row.get("raw_record_json") is not None
            for row in records
        )
        receipt: dict[str, Any] = {
            "returned_records": len(records),
            "total_records": int(result.get("total_records") or 0),
            "hydrated_records": hydrated,
            "hydration_bound": len(records) <= 25,
        }
        if name == "record" and records:
            raw = records[0].get("raw_record_json")
            parsed = (
                json.loads(raw, parse_float=Decimal)
                if isinstance(raw, str)
                else raw
            )
            receipt["record_semantic_sha256"] = _semantic_digest(parsed)
            receipt["root_keys"] = sorted(parsed) if isinstance(parsed, dict) else []
        return receipt
    summary = result.get("summary") if isinstance(result, Mapping) else None
    return {
        "top_level_keys": sorted(result) if isinstance(result, Mapping) else [],
        "summary_keys": sorted(summary) if isinstance(summary, Mapping) else [],
        "summary": _public(summary) if isinstance(summary, Mapping) else None,
    }


def _actual_read_model_suite(
    path: Path,
    *,
    temp_directory: Path,
    record_id: str,
    repeats: int,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for name in ACTUAL_READ_MODEL_NAMES:
        samples: list[float] = []
        first_result: Any = None
        with _open_read(path, temp_directory, fixed_clock=True) as connection:
            for index in range(repeats):
                started = time.perf_counter()
                result = _actual_read_model(connection, name, record_id)
                samples.append((time.perf_counter() - started) * 1_000)
                if index == 0:
                    first_result = result
        results[name] = {
            "cold_ms": round(samples[0], 3),
            "warm_median_ms": round(statistics.median(samples[1:]), 3),
            "samples_ms": [round(sample, 3) for sample in samples],
            "result": _actual_result_receipt(name, first_result),
            "semantic_sha256": _semantic_digest(first_result),
        }
    return results


def _explain_metric_projection(path: Path, *, layout: str, temp_directory: Path) -> dict[str, Any]:
    del layout
    metric = "source_rows_read"
    with _open_read(path, temp_directory) as connection:
        rows = connection.execute(
            f"EXPLAIN SELECT SUM(CAST({metric} AS BIGNUM)) FROM monitoring_dataflow_facts"
        ).fetchall()
    text = "\n".join(str(value) for row in rows for value in row)
    lowered = text.lower()
    return {
        "plan": text[:12_000],
        "metric_expression": metric,
        "contains_whole_record_parse": any(
            token in lowered for token in ("json_extract", "json_transform", "raw_record_json")
        ),
    }


def _measure_peak(operation: Callable[[], Any]) -> tuple[Any, int, float]:
    was_tracing = tracemalloc.is_tracing()
    if not was_tracing:
        tracemalloc.start()
    tracemalloc.reset_peak()
    started = time.perf_counter()
    try:
        result = operation()
        elapsed_ms = (time.perf_counter() - started) * 1_000
        _current, peak = tracemalloc.get_traced_memory()
        return result, peak, elapsed_ms
    finally:
        if not was_tracing:
            tracemalloc.stop()


def _measure_untraced(operation: Callable[[], Any]) -> tuple[Any, float]:
    """Measure wall time without tracemalloc instrumentation overhead."""

    started = time.perf_counter()
    result = operation()
    return result, (time.perf_counter() - started) * 1_000


def _checkpoint_size(path: Path, temp_directory: Path) -> int:
    import duckdb

    with duckdb.connect(str(path)) as connection:
        _configure_connection(connection, temp_directory)
        connection.execute("CHECKPOINT")
    return path.stat().st_size


def _run(arguments: argparse.Namespace) -> dict[str, Any]:
    _validate_arguments(arguments)
    jobs = arguments.jobs or max(1, arguments.rows // 6)
    baseline_serving_source = getattr(arguments, "baseline_serving_source", None)
    environment_keys = (
        "DATACOOLIE_STUDIO_DATA_DIR",
        "DATACOOLIE_STUDIO_DATABASE_URL",
        "DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_THREADS",
        "DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_MEMORY_LIMIT",
        "DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_TEMP_DIRECTORY",
    )
    previous_environment = {key: os.environ.get(key) for key in environment_keys}
    root: Path | None = None
    try:
        root = Path(tempfile.mkdtemp(prefix="datacoolie-studio-root-attributes-"))
        temp_directory = root / "duckdb-tmp"
        temp_directory.mkdir(parents=True, exist_ok=True)
        os.environ["DATACOOLIE_STUDIO_DATA_DIR"] = str(root)
        os.environ["DATACOOLIE_STUDIO_DATABASE_URL"] = f"sqlite:///{(root / 'studio.db').as_posix()}"
        os.environ["DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_THREADS"] = str(BENCHMARK_THREADS)
        os.environ["DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_MEMORY_LIMIT"] = BENCHMARK_MEMORY_LIMIT
        os.environ["DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_TEMP_DIRECTORY"] = str(temp_directory)

        dataflow_records = _make_dataflow_records(arguments.rows, jobs)
        job_records = _make_job_records(jobs)
        dataflow_path = root / "source" / "dataflow.json"
        job_path = root / "source" / "job.json"
        dataflow_bytes = _write_jsonl(dataflow_path, dataflow_records)
        job_bytes = _write_jsonl(job_path, job_records)
        source_bytes = dataflow_path.read_bytes() + b"\n" + job_path.read_bytes()
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        baseline_serving_sha256 = (
            _file_sha256(baseline_serving_source)
            if baseline_serving_source is not None
            else None
        )

        target_path = root / "target.duckdb"
        target_result, target_peak, target_wall_ms = _measure_peak(
            lambda: _typed_layout(
                target_path,
                temp_directory=temp_directory,
                dataflow_path=dataflow_path,
                job_path=job_path,
            )
        )
        target_untraced, target_untraced_wall_ms = _measure_untraced(
            lambda: _typed_layout(
                root / "target-untraced.duckdb",
                temp_directory=temp_directory,
                dataflow_path=dataflow_path,
                job_path=job_path,
            )
        )
        target_size = _checkpoint_size(target_path, temp_directory)

        baseline_path: Path | None = None
        baseline_result: dict[str, Any] | None = None
        baseline_peak = baseline_wall_ms = baseline_untraced_wall_ms = None
        baseline_untraced: dict[str, Any] | None = None
        baseline_size: int | None = None
        if baseline_serving_source is not None:
            baseline_path = root / "baseline.duckdb"
            baseline_result, baseline_peak, baseline_wall_ms = _measure_peak(
                lambda: _schema16_view_layout(
                    baseline_path,
                    temp_directory=temp_directory,
                    dataflow_path=dataflow_path,
                    job_path=job_path,
                    baseline_serving_source=baseline_serving_source,
                    source_database=target_path,
                )
            )
            baseline_untraced, baseline_untraced_wall_ms = _measure_untraced(
                lambda: _schema16_view_layout(
                    root / "baseline-untraced.duckdb",
                    temp_directory=temp_directory,
                    dataflow_path=dataflow_path,
                    job_path=job_path,
                    baseline_serving_source=baseline_serving_source,
                    source_database=target_path,
                )
            )
            baseline_size = _checkpoint_size(baseline_path, temp_directory)

        record_id = str(dataflow_records[0]["dataflow_run_id"])
        target_queries = _query_suite(
            target_path,
            layout="target",
            temp_directory=temp_directory,
            record_id=record_id,
            repeats=arguments.repeats,
        )
        target_actual_read_models = _actual_read_model_suite(
            target_path,
            temp_directory=temp_directory,
            record_id=record_id,
            repeats=arguments.repeats,
        )
        baseline_queries: dict[str, Any] = {}
        baseline_actual_read_models: dict[str, Any] = {}
        if baseline_path is not None:
            baseline_queries = _query_suite(
                baseline_path,
                layout="baseline",
                temp_directory=temp_directory,
                record_id=record_id,
                repeats=arguments.repeats,
            )
            baseline_actual_read_models = _actual_read_model_suite(
                baseline_path,
                temp_directory=temp_directory,
                record_id=record_id,
                repeats=arguments.repeats,
            )
        target_explain = _explain_metric_projection(
            target_path, layout="target", temp_directory=temp_directory
        )
        baseline_explain = (
            _explain_metric_projection(
                baseline_path, layout="baseline", temp_directory=temp_directory
            )
            if baseline_path is not None
            else None
        )
        micro_mismatches = [
            name
            for name in MICRO_QUERY_NAMES
            if baseline_path is not None
            and baseline_queries[name]["semantic_sha256"] != target_queries[name]["semantic_sha256"]
        ]
        actual_mismatches = [
            name
            for name in ACTUAL_READ_MODEL_NAMES
            if baseline_path is not None
            and baseline_actual_read_models[name]["semantic_sha256"]
            != target_actual_read_models[name]["semantic_sha256"]
        ]
        mismatches = [*micro_mismatches, *actual_mismatches]
        physical_counts = {
            "dataflow_rows": len(dataflow_records),
            "job_rows": len(job_records),
            "dataflow_unique_identities": len(
                {(row.get("log_session_id"), row.get("dataflow_run_id")) for row in dataflow_records}
            ),
            "job_unique_identities": len(
                {(row.get("log_session_id"), row.get("job_id")) for row in job_records}
            ),
            "duplicate_dataflow_observations": len(dataflow_records)
            - len({(row.get("log_session_id"), row.get("dataflow_run_id")) for row in dataflow_records}),
            "job_snapshot_rows": len(job_records) - jobs,
            "multiattempt_dataflow_rows": sum(
                len(row.get("attempts") or []) > 1 for row in dataflow_records
            ),
            "empty_attempt_dataflow_rows": sum(
                not row.get("attempts") for row in dataflow_records
            ),
            "diagnostic_dataflow_rows": sum(
                bool(row.get("diagnostic_issues")) for row in dataflow_records
            ),
        }
        parity = {
            "available": baseline_path is not None,
            "all_queries": baseline_path is not None and not mismatches,
            "mismatches": mismatches,
            "micro_sql": baseline_path is not None and not micro_mismatches,
            "actual_full_bundle": baseline_path is not None and not actual_mismatches,
        }
        baseline_layout = None
        if baseline_result is not None and baseline_path is not None:
            baseline_layout = {
                "label": BASELINE_LABEL,
                "schema_version": BASELINE_SCHEMA_VERSION,
                "serving_source": str(baseline_serving_source),
                "serving_source_sha256": baseline_serving_sha256,
                "serving_source_kind": "external path",
                "checkpoint_bytes": baseline_size,
                "python_peak_bytes": baseline_peak,
                "wall_ms_tracemalloc": round(baseline_wall_ms or 0.0, 3),
                "wall_ms_untraced": round(baseline_untraced_wall_ms or 0.0, 3),
                "publication_duration_ms": baseline_result["result"].get("publication_duration_ms"),
                "view_rebuild_ms": baseline_result.get("view_rebuild_ms"),
                "view_compaction_ms": baseline_result.get("view_compaction_ms"),
                "source_clone_ms": baseline_result.get("source_clone_ms"),
                "untraced_publication_duration_ms": baseline_untraced["result"].get("publication_duration_ms") if baseline_untraced else None,
                "wall_measurement_note": (
                    "Checkpointed source clone plus schema16 view rebuild and compaction; "
                    "source ingestion is shared with the target and is not a cross-layout timing."
                ),
                "inventory": _inventory(baseline_path, temp_directory),
            }
        return {
            "benchmark": "monitoring-schema16-views-vs-schema17-physical-facts",
            "baseline": {
                "label": BASELINE_LABEL,
                "schema_version": BASELINE_SCHEMA_VERSION,
                "source_revision_replayed": baseline_path is not None,
                "source_database_cloned": baseline_path is not None,
                "serving_source": str(baseline_serving_source) if baseline_serving_source else None,
                "serving_source_sha256": baseline_serving_sha256,
                "serving_source_kind": "external path" if baseline_serving_source else None,
                "physical_facts": False,
            },
            "target": {
                "label": TARGET_LABEL,
                "schema_version": TARGET_SCHEMA_VERSION,
                "ingress": "datacoolie_studio.domains.analytics.store.publish_rows_batched",
                "physical_facts": True,
            },
            "profile": {
                **physical_counts,
                "requested_rows": arguments.rows,
                "unique_jobs": jobs,
                "query_repeats": arguments.repeats,
                "threads": BENCHMARK_THREADS,
                "memory_limit": BENCHMARK_MEMORY_LIMIT,
                "timezone": BENCHMARK_TIMEZONE,
                "fixed_sql_current_timestamp": _iso(BENCHMARK_NOW),
                "filters": {},
                "scale_note": "Modest local profile only; no production-scale performance claim.",
            },
            "source_and_staging": {
                "source_bytes": dataflow_bytes + job_bytes,
                "materialized_input_bytes": dataflow_bytes + job_bytes,
                "staged_bytes_measured": dataflow_bytes + job_bytes,
                "source_sha256": source_sha256,
                "measurement_note": "Both layouts publish the same generated JSONL bytes.",
            },
            "comparison_scope": {
                "baseline": (
                    "Exact schema16 serving_facts.py loaded from the external path "
                    "over a checkpointed clone of the target source database."
                    if baseline_path is not None
                    else "No external schema16 serving source supplied; baseline comparison was skipped."
                ),
                "actual_read_models": "The same current Failures, Freshness, Performance, Diagnostics, latest_page, and record consumers ran against both layouts.",
                "filters": "Empty filter set, source scope [1], day grain, UTC timezone, fixed SQL current_timestamp.",
                "bounded_hydration": "latest_page is limited to 25 rows and record to 1 row; source evidence is hydrated through the bounded repository path.",
                "full_bundle_parity": "Exact semantic digest comparison covers every actual read-model result when a baseline is supplied.",
                "ingest_comparison": (
                    "Target publication_duration_ms is the real store.publish_rows_batched "
                    "ingest path. The baseline clones the target checkpoint before replacing "
                    "facts with views, so baseline wall time is not an ingest comparison."
                ),
                "performance_conclusion": "Local qualification evidence only; repeated cold/warm timings are not production SLO claims.",
            },
            "layouts": {
                "baseline": baseline_layout,
                "target": {
                    "label": TARGET_LABEL,
                    "schema_version": TARGET_SCHEMA_VERSION,
                    "path_kind": "temporary DuckDB file",
                    "checkpoint_bytes": target_size,
                    "python_peak_bytes": target_peak,
                    "wall_ms_tracemalloc": round(target_wall_ms, 3),
                    "wall_ms_untraced": round(target_untraced_wall_ms, 3),
                    "publication_duration_ms": target_result["result"].get("publication_duration_ms"),
                    "wall_measurement_note": "Untraced real store.publish_rows_batched publication over the shared JSONL bytes.",
                    "untraced_ingest_timings_ms": {
                        "wall": round(target_untraced["elapsed_ms"], 3),
                        "staging": target_untraced["result"].get("staging_duration_ms"),
                        "publication": target_untraced["result"].get("publication_duration_ms"),
                    },
                    "publish_result": target_result["result"],
                    "inventory": _inventory(target_path, temp_directory),
                },
            },
            "queries": {
                "baseline_micro_sql": baseline_queries,
                "target_micro_sql": target_queries,
                "baseline_actual_read_models": baseline_actual_read_models,
                "target_actual_read_models": target_actual_read_models,
                "freshness_summary_baseline": (
                    baseline_actual_read_models.get("Freshness", {}).get("result", {}).get("summary")
                    if baseline_actual_read_models else None
                ),
                "freshness_summary_target": target_actual_read_models.get("Freshness", {}).get("result", {}).get("summary"),
            },
            "parity": parity,
            "explain_metric_projection": {"baseline": baseline_explain, "target": target_explain},
        }
    finally:
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)
        for key, value in previous_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    receipt = _run(arguments)
    if arguments.receipt is not None:
        arguments.receipt.parent.mkdir(parents=True, exist_ok=True)
        arguments.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
