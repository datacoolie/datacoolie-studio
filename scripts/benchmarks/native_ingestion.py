"""Isolated native ingestion qualification against a pre-change source snapshot.

Each process imports exactly one implementation and publishes identical synthetic
fixture-derived files with one thread/512MiB. No live cache or provider is used.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path


def comparison_value(value):
    """Compare reports using the accepted native number/null representation."""

    def normalize(item):
        if item is None or isinstance(item, bool):
            return item
        if isinstance(item, (int, float, Decimal)):
            number = Decimal(str(item))
            native = (
                int(number) if number == number.to_integral_value() else float(number)
            )
            return ["number", str(Decimal(str(native)).normalize())]
        if isinstance(item, dict):
            return {
                str(key): normalize(
                    json.loads(child, parse_float=Decimal)
                    if (
                        key.endswith("raw_record_json")
                        or key in {"watermark_start", "watermark_end"}
                    )
                    and isinstance(child, str)
                    else child
                )
                for key, child in item.items()
                if key
                not in {
                    "generation",
                    "file_uri",
                    "_file_uri",
                    "latest_file_uri",
                    "_present_paths",
                    "_ingested_at",
                    "latest_ingested_at",
                    "_source_mtime_ns",
                }
                and child is not None
            }
        if isinstance(item, (list, tuple)):
            return [normalize(child) for child in item]
        if hasattr(item, "isoformat"):
            return item.isoformat()
        return item

    return normalize(value)


def comparison_digest(value):
    text = json.dumps(comparison_value(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def equivalent_results(before, after):
    """Keep exact string IDs; compare legacy numeric transport with native values."""
    if isinstance(before, dict) and isinstance(after, dict):
        keys = set(before) | set(after)
        return all(equivalent_results(before.get(key), after.get(key)) for key in keys)
    if isinstance(before, list) and isinstance(after, list):
        return len(before) == len(after) and all(
            equivalent_results(left, right) for left, right in zip(before, after)
        )
    if (
        isinstance(before, str)
        and isinstance(after, list)
        and len(after) == 2
        and after[0] == "number"
    ):
        try:
            return Decimal(before) == Decimal(after[1])
        except Exception:
            return False
    return before == after


def child(args):
    sys.path.insert(0, str(args.source))
    from datacoolie_studio.domains.analytics import connections, store
    from datacoolie_studio.domains.analytics.contracts import (
        DataflowFile,
        LogPublishBatch,
    )

    original_connect = connections.duckdb.connect

    def bounded_connect(*positional, **kwargs):
        kwargs.pop("config", None)
        conn = original_connect(*positional, **kwargs)
        conn.execute("SET threads=1")
        conn.execute("SET memory_limit='512MiB'")
        conn.execute("SET timezone='UTC'")
        return conn

    connections.duckdb.connect = bounded_connect
    # The managed connection applies its configured budget after connect().
    connections.analytics_duckdb_threads = lambda: 1
    connections.analytics_duckdb_memory_limit = lambda: "512MiB"
    helper_spec = importlib.util.spec_from_file_location(
        "native_benchmark_read_models", Path(__file__).with_name("root_attributes.py")
    )
    helper = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper)
    helper._semantic_digest = comparison_digest
    record = json.loads(
        (args.fixtures / "dataflow_snapshot.json").read_text().splitlines()[0]
    )
    result = {
        "rows": args.rows,
        "retained_sources": args.sources,
        "samples": [],
        "query_ms": [],
        "resource_budget": {"threads": 1, "memory_limit": "512MiB", "timezone": "UTC"},
    }
    with tempfile.TemporaryDirectory(prefix="dc-native-benchmark-") as temporary:
        directory = Path(temporary)
        spill = directory / "spill"
        connections.analytics_duckdb_temp_directory = lambda: spill
        files = []
        for source in range(args.sources):
            path = directory / f"source-{source}.ndjson"
            with path.open("w", encoding="utf-8") as handle:
                for index in range(args.rows):
                    row = copy.deepcopy(record)
                    row["dataflow_run_id"] = f"source-{source}-run-{index}"
                    row["job_id"] = f"job-{index % 20}"
                    handle.write(json.dumps(row) + "\n")
            files.append(path)
        db = directory / "analytics.duckdb"

        def publish(source):
            path = files[source]
            descriptor = DataflowFile(str(path), "dataflow_json", "{}", str(path))
            value = store.publish_rows_batched(
                source + 1,
                [LogPublishBatch(dataflow_files=(descriptor,))],
                [],
                [str(path)],
                database_path=db,
            )
            if not value["published"]:
                raise RuntimeError(value["errors"])
            return value

        start = time.perf_counter()
        for source in range(args.sources):
            publish(source)
        result["initial_ms"] = (time.perf_counter() - start) * 1000
        result["input_bytes"] = sum(path.stat().st_size for path in files)
        publish(0)  # excluded warmup
        for _ in range(args.repeats):
            start = time.perf_counter()
            value = publish(0)
            result["samples"].append(
                {
                    "total_ms": (time.perf_counter() - start) * 1000,
                    "staging_ms": value["staging_duration_ms"],
                    "publication_ms": value["publication_duration_ms"],
                }
            )
            with original_connect(str(db), read_only=True) as conn:
                conn.execute("SET threads=1")
                query = "SELECT status,count(*),sum(source_rows_read) FROM monitoring_dataflow_facts GROUP BY status"
                conn.execute(query).fetchall()
                queries = []
                for _ in range(9):
                    start = time.perf_counter()
                    rows = conn.execute(query).fetchall()
                    queries.append((time.perf_counter() - start) * 1000)
                result["query_ms"].append(statistics.median(queries))
                result["result"] = [[str(item) for item in row] for row in rows]
                assert (
                    conn.execute("SELECT count(*) FROM etl_dataflow_runs").fetchone()[0]
                    == args.rows * args.sources
                )
        result["read_models"] = helper._actual_read_model_suite(
            db,
            temp_directory=spill,
            record_id="source-0-run-0",
            repeats=args.repeats + 1,
        )
        with helper._open_read(db, spill, fixed_clock=True) as conn:
            result["comparison_values"] = {
                name: comparison_value(
                    helper._actual_read_model(conn, name, "source-0-run-0")
                )
                for name in helper.ACTUAL_READ_MODEL_NAMES
            }
        from datacoolie_studio.domains.analytics.publication import finalize_candidate

        candidate_samples = []
        for _ in range(args.repeats):
            candidate = directory / "candidate.duckdb"
            shutil.copyfile(db, candidate)
            start = time.perf_counter()
            finalize_candidate(candidate, list(range(1, args.sources + 1)))
            candidate_samples.append((time.perf_counter() - start) * 1000)
        result["full_finalization_ms"] = candidate_samples
        result["retained_spill_bytes"] = sum(
            path.stat().st_size for path in spill.rglob("*") if path.is_file()
        )
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    *[
                        (key, ctypes.c_size_t)
                        for key in (
                            "PeakWorkingSetSize",
                            "WorkingSetSize",
                            "QuotaPeakPagedPoolUsage",
                            "QuotaPagedPoolUsage",
                            "QuotaPeakNonPagedPoolUsage",
                            "QuotaNonPagedPoolUsage",
                            "PagefileUsage",
                            "PeakPagefileUsage",
                        )
                    ],
                ]

            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            kernel = ctypes.WinDLL("kernel32")
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            memory_info = ctypes.WinDLL("psapi").GetProcessMemoryInfo
            memory_info.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(Counters),
                wintypes.DWORD,
            ]
            memory_info(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
            result["peak_rss_bytes"] = counters.PeakWorkingSetSize
        result["database_bytes"] = db.stat().st_size
    print(json.dumps(result))



def native_comparison_value(value):
    """Strict native-to-native oracle: no numeric strings, nulls or file stripping."""
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, (int, float, Decimal)):
        return [type(value).__name__, str(value)]
    if isinstance(value, dict):
        return ["object", [[key, native_comparison_value(child)] for key, child in sorted(value.items())]]
    if isinstance(value, (list, tuple)):
        return ["array", [native_comparison_value(child) for child in value]]
    if hasattr(value, "isoformat"):
        return [type(value).__name__, value.isoformat()]
    raise TypeError(f"Unsupported native comparison type: {type(value).__name__}")


def native_digest(value):
    return hashlib.sha256(json.dumps(native_comparison_value(value), separators=(",", ":")).encode()).hexdigest()


def _peak_rss():
    import psutil
    if sys.platform == "win32":
        return psutil.Process().memory_info().peak_wset
    return psutil.Process().memory_info().rss


def native_child(args):
    """Measure the whole current native refresh, including provenance/window costs."""
    import threading
    import psutil
    from collections import Counter
    from datetime import datetime, timedelta, timezone

    sys.path.insert(0, str(args.source))
    from datacoolie_studio.domains.analytics import connections, store, serving_validation, publication
    from datacoolie_studio.domains.analytics.contracts import DataflowFile, JobFile, LogPublishBatch
    from datacoolie_studio.domains.logs.batching import file_batches

    original_connect = connections.duckdb.connect
    counts = Counter()
    measured = False
    phase_times = Counter()
    def timed(module, name, label):
        original = getattr(module, name)
        def wrapper(*a, **kw):
            started = time.perf_counter()
            try:
                return original(*a, **kw)
            finally:
                if measured:
                    phase_times[label] += (time.perf_counter()-started)*1000
        setattr(module, name, wrapper)
    timed(serving_validation, "validate_run_conflicts", "conflicts_ms")
    timed(serving_validation, "validate_serving_parity", "facts_validation_ms")
    timed(publication, "rebuild_monitoring_serving_facts", "facts_build_including_conflicts_ms")
    timed(publication, "refresh_filter_values", "filter_values_ms")

    class CountedConnection:
        def __init__(self, conn):
            self.conn = conn
        def execute(self, sql, parameters=None):
            if measured:
                normalized = sql.upper().lstrip()
                counts["execute"] += 1
                if "READ_JSON(" in normalized:
                    counts["native_reads"] += 1
                if normalized.startswith("INSERT INTO ETL_DATAFLOW_RUNS") or normalized.startswith("INSERT INTO ETL_JOB_RUNS"):
                    counts["base_inserts"] += 1
                if normalized.startswith("DESCRIBE"):
                    counts["describe"] += 1
                if normalized.startswith("CREATE TEMP VIEW"):
                    counts["file_views"] += 1
                if normalized.startswith("BEGIN"):
                    counts["transactions"] += 1
                if normalized.startswith("COMMIT"):
                    counts["commits"] += 1
            self.conn.execute(sql, parameters) if parameters is not None else self.conn.execute(sql)
            return self
        def __getattr__(self, name):
            return getattr(self.conn, name)
        def __enter__(self):
            return self
        def __exit__(self, *_):
            self.conn.close()

    connections.duckdb.connect = lambda *a, **kw: CountedConnection(original_connect(*a, **kw))
    connections.analytics_duckdb_threads = lambda: args.threads
    connections.analytics_duckdb_memory_limit = lambda: "512MiB"
    helper_spec = importlib.util.spec_from_file_location("native_matrix_read_models", Path(__file__).with_name("root_attributes.py"))
    helper = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper)
    dataflow = json.loads((args.fixtures / "dataflow_snapshot.json").read_text().splitlines()[0])
    job = json.loads((args.fixtures / "job_snapshot.json").read_text().splitlines()[0])
    result = {"case": args.case, "files": args.files, "rows": args.rows, "threads": args.threads, "samples": [], "memory_limit": "512MiB"}
    from contextlib import ExitStack
    from unittest.mock import patch
    with tempfile.TemporaryDirectory(prefix="dc-native-matrix-") as temporary, ExitStack() as resource_scope:
        directory = Path(temporary)
        resource_scope.enter_context(patch.object(tempfile, "tempdir", str(directory)))
        spill = directory / "spill"
        spill.mkdir()
        connections.analytics_duckdb_temp_directory = lambda: spill
        db = directory / "analytics.duckdb"
        descriptors = []
        input_hashes = {}
        known_paths = set()
        def make_files(source, total, number, *, duplicate=False, normalize=False, mixed=False):
            selected = []
            for file_index in range(number):
                is_job = mixed and file_index % 2 == 1
                path = directory / f"source-{source}-part-{file_index}.ndjson"
                known_paths.add(path)
                uri = f"canonical://source-{source}/2026-10-01/part-{file_index}.ndjson"
                with path.open("wb") as output:
                    if normalize:
                        output.write(b"\xef\xbb\xbf")
                    for index in range(file_index, total, number):
                        row = copy.deepcopy(job if is_job else dataflow)
                        row["job_id"] = f"job-{index % 20}" if not is_job else f"snapshot-job-{index}"
                        # Distinct event keys make existing partially ordered
                        # read-model arrays deterministic across independent loads.
                        logical_index = index // 2 if duplicate else index
                        moment = datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(seconds=logical_index)
                        row["start_time"] = moment.isoformat()
                        row["end_time"] = (moment + timedelta(seconds=10)).isoformat()
                        if not is_job:
                            logical = index // 2 if duplicate else index
                            row["dataflow_run_id"] = f"source-{source}-run-{logical}"
                            if duplicate:
                                row["job_id"] = f"job-{logical % 20}"
                        if normalize and index % 10 == 0:
                            excluded = {**row, "log_schema_version": 5}
                            output.write(json.dumps(excluded).encode() + b"\r\n\r\n")
                        output.write(json.dumps(row, separators=(",", ":")).encode() + (b"\r\n" if normalize else b"\n"))
                kind = "job_json" if is_job else "dataflow_json"
                item = (JobFile if is_job else DataflowFile)(uri, kind, json.dumps({"size": path.stat().st_size, "mtime_ns": 123456789}, sort_keys=True), str(path))
                selected.append(item)
                input_hashes[uri] = hashlib.sha256(path.read_bytes()).hexdigest()
            return selected
        def batches(items):
            states = [{"file_uri": x.canonical_uri, "revision_json": x.revision_json} for x in items]
            by_uri = {x.canonical_uri: x for x in items}
            for selected in file_batches(states):
                group = [by_uri[x["file_uri"]] for x in selected]
                yield LogPublishBatch(dataflow_files=tuple(x for x in group if x.file_kind == "dataflow_json"), job_files=tuple(x for x in group if x.file_kind == "job_json"))
        def publish(source, items):
            value = store.publish_rows_batched(source, batches(items), [], [x.canonical_uri for x in items], database_path=db)
            if not value["published"]:
                raise RuntimeError(value["errors"])
            return value
        # A second retained source proves isolation and fixes the read-model population.
        retained = make_files(2, min(args.rows, 128), 1)
        publish(2, retained)
        if args.case == "small":
            publish(1, make_files(1, 5000, 16))
            # Replacing a new descriptor leaves all 5000 historical rows retained.
            descriptors = make_files(3, 20, 1)
        else:
            descriptors = make_files(1, args.rows, args.files, duplicate=args.case == "duplicates", normalize=args.case == "normalized", mixed=args.case == "mixed")
        publish(1, descriptors)
        stop = threading.Event()
        peak = {"rss": _peak_rss(), "transient_disk": 0, "spill": 0}
        def monitor():
            while not stop.wait(0.02):
                peak["rss"] = max(peak["rss"], psutil.Process().memory_info().rss)
                def disk(root):
                    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
                try:
                    peak["transient_disk"] = max(peak["transient_disk"], disk(directory) - sum(p.stat().st_size for p in known_paths) - (db.stat().st_size if db.exists() else 0))
                    peak["spill"] = max(peak["spill"], disk(spill))
                except FileNotFoundError:
                    pass
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        try:
            publish(1, descriptors)  # excluded warmup
            for _ in range(args.repeats):
                counts.clear()
                phase_times.clear()
                measured = True
                start = time.perf_counter()
                value = publish(1, descriptors)
                elapsed = (time.perf_counter() - start) * 1000
                measured = False
                result["samples"].append({"total_ms": elapsed, "staging_ms": value["staging_duration_ms"], "publication_ms": value["publication_duration_ms"], "sql_counts": dict(counts), "phase_ms": dict(phase_times)})
        finally:
            measured = False
            stop.set()
            thread.join()
        peak["rss"] = max(peak["rss"], _peak_rss())
        result["versions"] = {"python": sys.version, "duckdb": connections.duckdb.__version__}
        if args.cache_output:
            args.cache_output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(db, args.cache_output)
        result["source_sha256"] = hashlib.sha256("".join(hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((args.source / "datacoolie_studio/domains/analytics").glob("*.py"))).encode()).hexdigest()
        result["batch_count"] = len(list(batches(descriptors)))
        result.update(peak_rss_bytes=peak["rss"], peak_transient_disk_bytes=max(0, peak["transient_disk"]), peak_spill_bytes=peak["spill"], retained_spill_bytes=sum(p.stat().st_size for p in spill.rglob("*") if p.is_file()), database_bytes=db.stat().st_size, input_hashes=input_hashes)
        with original_connect(str(db), read_only=True) as conn:
            bases = {}
            for table in ("etl_dataflow_runs", "etl_job_runs"):
                cursor = conn.execute(f"SELECT * EXCLUDE(_ingested_at) FROM {table} ORDER BY _source_id, _file_uri, _record_line")
                names = [column[0] for column in cursor.description]
                bases[table] = [dict(zip(names, row)) for row in cursor.fetchall()]
            result["base_digest"] = native_digest(bases)
            result["base_counts"] = {table: len(rows) for table, rows in bases.items()}
        # Match the actual read-model connection budget to the writer profile.
        helper.BENCHMARK_THREADS = args.threads
        result["read_models"] = helper._actual_read_model_suite(db, temp_directory=spill, record_id="source-1-run-0", repeats=args.repeats + 1)
        with helper._open_read(db, spill, fixed_clock=True) as conn:
            result["read_model_digests"] = {}
            result["read_model_values"] = {}
            for name in helper.ACTUAL_READ_MODEL_NAMES:
                value = helper._actual_read_model(conn, name, "source-1-run-0")
                def stable(item, *, producer=False):
                    if isinstance(item, dict):
                        values = {}
                        for key, child in item.items():
                            if not producer and key in {"generation", "_ingested_at", "latest_ingested_at", "ingested_at"}:
                                continue
                            if not producer and key.endswith("raw_record_json") and isinstance(child, str):
                                values[key] = stable(json.loads(child), producer=True)
                            else:
                                values[key] = stable(child, producer=producer)
                        return values
                    if isinstance(item, list):
                        return [stable(v, producer=producer) for v in item]
                    return item
                result["read_model_values"][name] = native_comparison_value(stable(value))
                result["read_model_digests"][name] = native_digest(stable(value))
        result.pop("read_model_values", None)
        result["median_total_ms"] = statistics.median(x["total_ms"] for x in result["samples"])
        result["median_staging_ms"] = statistics.median(x["staging_ms"] for x in result["samples"])
        result["median_publication_ms"] = statistics.median(x["publication_ms"] for x in result["samples"])
    print(json.dumps(result))


def native_comparison(args):
    result = {}
    implementations = [("baseline", args.baseline_source), ("native", args.source)]
    if args.reverse_order:
        implementations.reverse()
    for label, source in implementations:
        command = [sys.executable, str(Path(__file__).resolve()), "--comparison", "native", "--source", str(source.resolve()), "--fixtures", str(args.fixtures.resolve()), "--rows", str(args.rows), "--files", str(args.files), "--case", args.case, "--threads", str(args.threads), "--repeats", str(args.repeats)]
        completed = subprocess.run(command, check=False, capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": "0"})
        if completed.returncode:
            raise RuntimeError(completed.stderr)
        result[label] = json.loads(completed.stdout)
    before, after = result["baseline"], result["native"]
    result["checks"] = {"same_input": before["input_hashes"] == after["input_hashes"], "base_parity": before["base_digest"] == after["base_digest"], "read_model_parity": before["read_model_digests"] == after["read_model_digests"], "ingest_gain": 1-after["median_total_ms"]/before["median_total_ms"], "peak_rss_ratio": after["peak_rss_bytes"]/before["peak_rss_bytes"], "query_ratios": {name: after["read_models"][name]["warm_median_ms"]/value["warm_median_ms"] for name,value in before["read_models"].items()}}
    output = json.dumps(result, indent=2)
    if args.receipt:
        args.receipt.write_text(output + "\n", encoding="utf-8")
    print(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--baseline-source", type=Path)
    parser.add_argument("--fixtures", type=Path)
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--sources", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--comparison", choices=("legacy", "native"), default="legacy")
    parser.add_argument("--reverse-order", action="store_true", help="measure optimized native process before baseline to check order effects")
    parser.add_argument("--cache-output", type=Path, help="retain an isolated child cache for paired same-process query profiling")
    parser.add_argument("--files", type=int, default=1)
    parser.add_argument("--threads", type=int, choices=(1, 4), default=1)
    parser.add_argument("--case", choices=("normal", "small", "duplicates", "mixed", "normalized"), default="normal")
    args = parser.parse_args()
    if args.comparison == "native":
        if not args.source or not args.fixtures or args.repeats < 5 or args.files < 1:
            parser.error("native comparison requires source, fixtures, positive files and at least5 repeats")
        native_comparison(args) if args.baseline_source else native_child(args)
        return
    if args.source and not args.baseline_source:
        child(args)
        return
    if (
        not args.source
        or not args.baseline_source
        or not args.fixtures
        or args.repeats < 5
    ):
        parser.error("provide source,baseline-source,fixtures and at least5 repeats")
    results = {}
    for label, source in (("baseline", args.baseline_source), ("native", args.source)):
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--source",
            str(source.resolve()),
            "--fixtures",
            str(args.fixtures.resolve()),
            "--rows",
            str(args.rows),
            "--sources",
            str(args.sources),
            "--repeats",
            str(args.repeats),
        ]
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        if completed.returncode:
            raise RuntimeError(completed.stderr)
        results[label] = json.loads(completed.stdout)
    for result in results.values():
        result["median_total_ms"] = statistics.median(
            x["total_ms"] for x in result["samples"]
        )
        result["median_query_ms"] = statistics.median(result["query_ms"])
    results["thresholds"] = {
        "minimum_total_ingest_gain": 0.20,
        "maximum_query_regression": 0.20,
    }
    results["checks"] = {
        "same_results": results["baseline"]["result"] == results["native"]["result"],
        "ingest_gain": 1
        - results["native"]["median_total_ms"] / results["baseline"]["median_total_ms"],
        "query_ratio": results["native"]["median_query_ms"]
        / results["baseline"]["median_query_ms"],
        "read_model_parity": {
            name: equivalent_results(
                value, results["native"]["comparison_values"][name]
            )
            for name, value in results["baseline"]["comparison_values"].items()
        },
        "read_model_query_ratios": {
            name: results["native"]["read_models"][name]["warm_median_ms"]
            / value["warm_median_ms"]
            for name, value in results["baseline"]["read_models"].items()
        },
    }
    text = json.dumps(results, indent=2)
    if args.receipt:
        args.receipt.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
