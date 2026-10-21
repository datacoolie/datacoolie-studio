"""Read-only T0/T1 diagnostics for the Monitoring dataflow registry.

The command is intentionally a developer diagnostic, rather than a Studio
runtime command.  It measures the existing read model against the live
analytics cache without writing to that cache.  Synthetic databases are
created separately for scale and duplicate-observation qualification.

Examples are documented in ``scripts/benchmarks/README.md``.  The parent
process orchestrates isolated worker processes so every thread profile is
selected at connection construction time; a request never changes the
threads setting on a shared connection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
STUDIO_ROOT = ROOT / "datacoolie-studio"
CURRENT_SOURCE_ROOT = STUDIO_ROOT / "src"
BUDGET_ROOT = ROOT / ".scratch" / "registry-cold-load" / "budget"
THREAD_ENV = "DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_THREADS"
MEMORY_ENV = "DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_MEMORY_LIMIT"
TEMP_ENV = "DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_TEMP_DIRECTORY"
CACHE_ENV = "DATACOOLIE_STUDIO_RESULT_CACHE_URL"
AS_OF = "2026-10-16T00:00:00Z"
RELATIVE_START = "2026-09-16T00:00:00Z"
DEFAULT_TARGET = "c6f9ef91-7eb4-5203-bd00-fe6d1b951b8a"
SCRIPT = Path(__file__).resolve()


WORKLOADS: dict[str, dict[str, Any]] = {
    "drawer-all": {
        "description": "full-model all-facet entity drawer",
        "facet": "all",
        "sort_by": "latest_run_at",
        "limit": 1,
        "target": True,
    },
    "table-freshness": {
        "description": "freshness facet table",
        "facet": "freshness",
        "sort_by": "latest_freshness_at",
        "limit": 100,
        "target": False,
    },
    "table-performance": {
        "description": "performance facet table",
        "facet": "performance",
        "sort_by": "p95_duration_seconds",
        "limit": 100,
        "target": False,
    },
    "table-volume": {
        "description": "volume facet table",
        "facet": "volume",
        "sort_by": "volume_rows_read",
        "limit": 100,
        "target": False,
    },
}


def _package_root(value: Path) -> Path:
    """Resolve either a source checkout root or a datacoolie_studio package root."""

    path = value.expanduser().resolve()
    candidates = [path]
    if path.name != "src":
        candidates.append(path / "src")
    for candidate in candidates:
        if (candidate / "domains").is_dir() and candidate.name == "datacoolie_studio":
            return candidate
        package = candidate / "datacoolie_studio"
        if (package / "domains").is_dir():
            return package
    raise RuntimeError(
        "source root must contain datacoolie_studio/domains: "
        f"{value}"
    )


def _import_root(value: Path) -> Path:
    """Resolve a path suitable for importing the datacoolie_studio package."""

    package = _package_root(value)
    return package.parent


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _canonical(value: Any) -> Any:
    """Canonical digest representation retaining Decimal versus float identity."""

    if isinstance(value, Decimal):
        return {"__type__": "decimal", "value": format(value, "f")}
    if isinstance(value, float):
        if math.isnan(value):
            return {"__type__": "float", "value": "nan"}
        if math.isinf(value):
            return {"__type__": "float", "value": "-inf" if value < 0 else "inf"}
        return {"__type__": "float", "value": repr(value)}
    if isinstance(value, (datetime, date)):
        return {"__type__": type(value).__name__, "value": value.isoformat()}
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _digest(value: Any) -> str:
    payload = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), default=_json_default)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _equal(left: Any, right: Any) -> bool:
    """Compare read-model outputs with exact Decimals and 1e-12 float tolerance."""

    if isinstance(left, Decimal) or isinstance(right, Decimal):
        if not isinstance(left, Decimal) or not isinstance(right, Decimal):
            return False
        try:
            return left == right
        except Exception:
            return False
    if isinstance(left, float) or isinstance(right, float):
        try:
            left_float = float(left)
            right_float = float(right)
        except Exception:
            return False
        if math.isnan(left_float) or math.isnan(right_float):
            return math.isnan(left_float) and math.isnan(right_float)
        return math.isclose(left_float, right_float, rel_tol=1e-12, abs_tol=1e-12)
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _difference_paths(left: Any, right: Any, path: str = "$", limit: int = 32) -> list[dict[str, str]]:
    """Return bounded type/path diagnostics without retaining production values."""

    if isinstance(left, Decimal) or isinstance(right, Decimal):
        if not isinstance(left, Decimal) or not isinstance(right, Decimal) or left != right:
            return [{"path": path, "left_type": type(left).__name__, "right_type": type(right).__name__}]
        return []
    if isinstance(left, float) or isinstance(right, float):
        return [] if _equal(left, right) else [{"path": path, "left_type": type(left).__name__, "right_type": type(right).__name__}]
    if isinstance(left, dict) and isinstance(right, dict):
        items: list[dict[str, str]] = []
        for key in sorted(set(left) | set(right), key=str):
            if key not in left or key not in right:
                items.append({"path": f"{path}.{key}", "left_type": type(left.get(key)).__name__, "right_type": type(right.get(key)).__name__})
            else:
                items.extend(_difference_paths(left[key], right[key], f"{path}.{key}", limit))
            if len(items) >= limit:
                break
        return items[:limit]
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        items = []
        if len(left) != len(right):
            items.append({"path": path, "left_type": f"list[{len(left)}]", "right_type": f"list[{len(right)}]"})
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            items.extend(_difference_paths(left_item, right_item, f"{path}[{index}]", limit))
            if len(items) >= limit:
                break
        return items[:limit]
    if type(left) is not type(right) or left != right:
        return [{"path": path, "left_type": type(left).__name__, "right_type": type(right).__name__}]
    return []


def _parse_csv(value: str, *, name: str) -> list[str]:
    result = [part.strip() for part in value.split(",") if part.strip()]
    if not result:
        raise SystemExit(f"--{name} must contain at least one value")
    return result


def _set_thread_profile(profile: str) -> None:
    profile = profile.strip().lower()
    if profile == "auto":
        os.environ.pop(THREAD_ENV, None)
    elif profile in {"1", "2", "4", "8", "16"}:
        os.environ[THREAD_ENV] = profile
    else:
        raise SystemExit(f"unsupported thread profile {profile!r}; use 1, 2, 4, 8, 16 or auto")


def _configure_worker_environment(arguments: argparse.Namespace, *, cell_name: str) -> Path:
    """Configure only this disposable worker process."""

    _set_thread_profile(arguments.thread_profile)
    if arguments.memory_limit:
        os.environ[MEMORY_ENV] = arguments.memory_limit
    temp_root = BUDGET_ROOT / "temp" / f"{cell_name}-{os.getpid()}-{time.time_ns()}"
    temp_root.mkdir(parents=True, exist_ok=False)
    ownership_marker = temp_root.parent / f".{temp_root.name}.registry-benchmark-owned"
    ownership_marker.write_text("registry-cold-load\n", encoding="utf-8")
    os.environ[TEMP_ENV] = str(temp_root)
    # Live output must never be persisted in the benchmark cache.  Synthetic
    # output is also kept in memory by default; this is an explicit cache
    # phase, not an application cache migration.
    os.environ[CACHE_ENV] = "sqlite://"
    return temp_root


def _load_runtime(
    source_root: Path | None = None,
    *,
    baseline_root: Path | None = None,
) -> dict[str, Any]:
    """Load the selected source root and optionally overlay captured Monitoring modules."""

    import_root = _import_root(source_root or CURRENT_SOURCE_ROOT)
    baseline_package_root = _package_root(baseline_root) if baseline_root is not None else None
    if baseline_package_root is not None and not baseline_package_root.exists():
        raise RuntimeError(f"source baseline is missing: {baseline_package_root}")
    started = time.perf_counter()
    sys.path.insert(0, str(import_root))
    import importlib

    # The root package and analytics modules remain the current checkout.  The
    # monitoring package paths are pinned to the captured baseline so a worker
    # remains comparable while another agent edits current v6/freshness files.
    import datacoolie_studio.domains.monitoring as monitoring_package

    if baseline_package_root is not None:
        monitoring_package.__path__ = [str(baseline_package_root / "domains" / "monitoring")]
        read_models_package = importlib.import_module("datacoolie_studio.domains.monitoring.read_models")
        read_models_package.__path__ = [str(baseline_package_root / "domains" / "monitoring" / "read_models")]
        metrics_package = importlib.import_module("datacoolie_studio.domains.monitoring.metrics")
        metrics_package.__path__ = [str(baseline_package_root / "domains" / "monitoring" / "metrics")]

    from datacoolie_studio.core import config
    from datacoolie_studio.domains.analytics import schema
    from datacoolie_studio.domains.analytics.connections import analytics_connections
    from datacoolie_studio.domains.monitoring.metrics.v6_semantics import register_freshness_functions
    from datacoolie_studio.domains.monitoring.read_models import dataflow_registry
    from datacoolie_studio.domains.read_models.cache import fingerprint
    from datacoolie_studio.domains.read_models.contracts import ResultCacheKey, get_or_compute
    from datacoolie_studio.domains.read_models.provider import result_cache_provider
    from datacoolie_studio.domains.read_models.sqlite_store import clear_memory_cache

    manifest = None
    manifest_root = baseline_package_root.parent if baseline_package_root is not None else import_root.parent
    candidate_manifest = manifest_root / "manifest.json"
    if candidate_manifest.exists():
        manifest = candidate_manifest
    return {
        "import_ms": round((time.perf_counter() - started) * 1000, 3),
        "config": config,
        "schema": schema,
        "connections": analytics_connections,
        "register": register_freshness_functions,
        "register_lock": threading.Lock(),
        "registry": dataflow_registry,
        "fingerprint": fingerprint,
        "ResultCacheKey": ResultCacheKey,
        "get_or_compute": get_or_compute,
        "cache_provider": result_cache_provider,
        "clear_memory_cache": clear_memory_cache,
        "source_root": str(import_root),
        "monitoring_source_root": str(baseline_package_root or _package_root(import_root)),
        "baseline_root": str(baseline_package_root) if baseline_package_root is not None else None,
        "source_manifest": {
            "path": str(manifest) if manifest is not None else None,
            "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest is not None and manifest.exists() else None,
        },
    }


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _ddl(types: dict[str, str]) -> str:
    return ",\n  ".join(f"{_quote_identifier(name)} {dtype}" for name, dtype in types.items())


def _synthetic_path(arguments: argparse.Namespace) -> Path:
    if arguments.synthetic_path:
        return Path(arguments.synthetic_path).expanduser().resolve()
    return BUDGET_ROOT / f"synthetic-{arguments.synthetic_rows}.duckdb"


def _create_synthetic_database(arguments: argparse.Namespace, runtime: dict[str, Any]) -> dict[str, Any]:
    """Create deterministic synthetic data without reading the live database."""

    import duckdb

    rows = int(arguments.synthetic_rows)
    if rows < 1:
        raise SystemExit("--synthetic-rows must be positive")
    path = _synthetic_path(arguments)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        conn = duckdb.connect(str(path), read_only=True)
        try:
            marker = conn.execute(
                "SELECT value FROM registry_benchmark_meta WHERE key = 'canonical_rows'"
            ).fetchone()
            if marker and int(marker[0]) == rows:
                return {"path": str(path), "reused": True, "canonical_rows": rows}
        except duckdb.Error:
            pass
        finally:
            conn.close()
        raise RuntimeError(f"synthetic path exists with a different shape: {path}")

    started = time.perf_counter()
    conn = duckdb.connect(str(path), read_only=False)
    try:
        schema = runtime["schema"]
        dataflow_types = schema.cache_table_column_types(schema.DATAFLOW_COLUMN_TYPES)
        job_types = schema.cache_table_column_types(schema.JOB_COLUMN_TYPES)
        conn.execute(f"CREATE TABLE etl_dataflow_runs (\n  {_ddl(dataflow_types)}\n)")
        conn.execute(f"CREATE TABLE etl_job_runs (\n  {_ddl(job_types)}\n)")
        # Every canonical run is observed by two synthetic sources.  Source 2
        # has the later observed_at value, making the winner deterministic.
        conn.execute(
            f"""
            WITH base AS (
              SELECT CAST(source_id AS BIGINT) AS source_id, i,
                     TIMESTAMPTZ '2026-10-01 00:00:00+00'
                       + (i % 1200000) * INTERVAL '1 second'
                       + source_id * INTERVAL '1 microsecond' AS event_ts
              FROM range({rows}) AS r(i)
              CROSS JOIN (VALUES (1), (2)) AS s(source_id)
            )
            INSERT INTO etl_dataflow_runs BY NAME
            SELECT
              source_id AS _source_id,
              printf('synthetic://source/%d/run/%d.json', source_id, i) AS _file_uri,
              'dataflow_run_log' AS _file_kind,
              CAST(event_ts AS DATE) AS _file_date,
              4096::BIGINT AS _source_size,
              1::BIGINT AS _source_mtime_ns,
              event_ts AS _ingested_at,
              i + 1 AS _record_line,
              6::BIGINT AS log_schema_version,
              'dataflow_run_log' AS _type,
              'synthetic' AS datacoolie_version,
              printf('session-%d', CAST(FLOOR(i / 10) AS BIGINT)) AS log_session_id,
              printf('job-%d', i) AS job_id,
              1::HUGEINT AS job_num,
              0::HUGEINT AS job_index,
              printf('synthetic-flow-%d', CAST(FLOOR(i / 10) AS BIGINT)) AS dataflow_id,
              printf('synthetic-run-%d', i) AS dataflow_run_id,
              'synthetic-workspace' AS workspace_id,
              printf('Synthetic flow %d', CAST(FLOOR(i / 10) AS BIGINT)) AS dataflow_name,
              'deterministic benchmark flow' AS dataflow_description,
              'extract' AS stage,
              'succeeded' AS status,
              'synthetic run' AS message,
              TIMESTAMPTZ '2026-10-01 00:00:00+00' + (i % 1200000) * INTERVAL '1 second' AS start_time,
              TIMESTAMPTZ '2026-10-01 00:00:01+00' + (i % 1200000) * INTERVAL '1 second' AS end_time,
              CAST(1.0 + (i % 17) / 10.0 AS DOUBLE) AS duration_seconds,
              CASE WHEN i % 3 = 0 THEN 'incremental' ELSE 'full' END AS operation_type,
              'synthetic source' AS source_name,
              'local' AS source_connection_type,
              'table' AS source_format,
              printf('source_table_%d', i % 23) AS source_table,
              printf('source_table_%d', i % 23) AS source_full_table,
              'synthetic destination' AS destination_name,
              'local' AS destination_connection_type,
              'table' AS destination_format,
              printf('destination_table_%d', i % 17) AS destination_table,
              printf('destination_table_%d', i % 17) AS destination_full_table,
              'append' AS destination_load_type,
              CAST(struct_pack(
                rows_read := CAST(100 + (i % 500) AS HUGEINT),
                rows_written := CAST(80 + (i % 400) AS HUGEINT),
                rows_inserted := CAST(80 + (i % 400) AS HUGEINT),
                rows_updated := CAST(0 AS HUGEINT),
                rows_deleted := CAST(0 AS HUGEINT),
                files_added := CAST(1 AS HUGEINT),
                files_removed := CAST(0 AS HUGEINT),
                bytes_added := CAST(10000 + i AS HUGEINT),
                bytes_removed := CAST(0 AS HUGEINT),
                bytes_saved := CAST(0 AS HUGEINT)
              ) AS {schema.DATAFLOW_COLUMN_TYPES['metrics']} ) AS metrics,
              event_ts AS __event_time,
              CAST(event_ts AS DATE) AS __run_date
            FROM base
            """
        )
        conn.execute(
            f"""
            WITH base AS (
              SELECT CAST(source_id AS BIGINT) AS source_id, i,
                     TIMESTAMPTZ '2026-10-01 00:00:00+00'
                       + (i % 1200000) * INTERVAL '1 second'
                       + source_id * INTERVAL '1 microsecond' AS event_ts
              FROM range({rows}) AS r(i)
              CROSS JOIN (VALUES (1), (2)) AS s(source_id)
            )
            INSERT INTO etl_job_runs BY NAME
            SELECT
              source_id AS _source_id,
              printf('synthetic://source/%d/job/%d.json', source_id, i) AS _file_uri,
              'job_run_log' AS _file_kind,
              CAST(event_ts AS DATE) AS _file_date,
              2048::BIGINT AS _source_size,
              1::BIGINT AS _source_mtime_ns,
              event_ts AS _ingested_at,
              i + 1 AS _record_line,
              6::BIGINT AS log_schema_version,
              'job_run_log' AS _type,
              'synthetic' AS datacoolie_version,
              printf('session-%d', CAST(FLOOR(i / 10) AS BIGINT)) AS log_session_id,
              printf('job-%d', i) AS job_id,
              'synthetic-workspace' AS workspace_id,
              'engine' AS engine_name,
              'provider' AS metadata_provider_name,
              'platform' AS platform_name,
              'succeeded' AS status,
              CAST(event_ts AS VARCHAR) AS start_time,
              CAST(event_ts + INTERVAL '1 second' AS VARCHAR) AS end_time,
              CAST(1.0 + (i % 17) / 10.0 AS DOUBLE) AS duration_seconds,
              1::HUGEINT AS total_dataflows,
              1::HUGEINT AS total_succeeded,
              0::HUGEINT AS total_failed,
              0::HUGEINT AS total_skipped,
              0::HUGEINT AS total_running,
              0::HUGEINT AS total_pending,
              CAST(100 + (i % 500) AS HUGEINT) AS total_rows_read,
              CAST(80 + (i % 400) AS HUGEINT) AS total_rows_written,
              CAST(80 + (i % 400) AS HUGEINT) AS total_rows_inserted,
              CAST(0 AS HUGEINT) AS total_rows_updated,
              CAST(0 AS HUGEINT) AS total_rows_deleted,
              CAST(1 AS HUGEINT) AS total_files_added,
              CAST(0 AS HUGEINT) AS total_files_removed,
              CAST(10000 + i AS HUGEINT) AS total_bytes_added,
              CAST(0 AS HUGEINT) AS total_bytes_removed,
              CAST(event_ts AS VARCHAR) AS observed_at,
              event_ts AS __event_time,
              CAST(event_ts AS DATE) AS __run_date
            FROM base
            """
        )
        conn.execute("CREATE TABLE registry_benchmark_meta (key VARCHAR, value VARCHAR)")
        conn.execute(
            "INSERT INTO registry_benchmark_meta VALUES (?, ?), (?, ?), (?, ?)",
            ["canonical_rows", str(rows), "source_count", "2", "duplicate_factor", "2"],
        )
        # This is isolated serving-fact publication, never the live database.
        from datacoolie_studio.domains.analytics.serving_facts import rebuild_monitoring_serving_facts

        serving_started = time.perf_counter()
        rebuild_monitoring_serving_facts(
            conn,
            dataflow_table="etl_dataflow_runs",
            job_table="etl_job_runs",
            dataflow_column_types=schema.DATAFLOW_COLUMN_TYPES,
            job_column_types=schema.JOB_COLUMN_TYPES,
        )
        serving_ms = (time.perf_counter() - serving_started) * 1000
        conn.execute(
            "INSERT INTO registry_benchmark_meta VALUES (?, ?), (?, ?)",
            ["raw_dataflow_rows", str(rows * 2), "serving_ms", f"{serving_ms:.3f}"],
        )
        conn.execute("CHECKPOINT")
        result = {
            "path": str(path),
            "reused": False,
            "canonical_rows": rows,
            "raw_dataflow_rows": rows * 2,
            "raw_job_rows": rows * 2,
            "source_count": 2,
            "duplicate_factor": 2,
            "build_ms": round((time.perf_counter() - started) * 1000, 3),
            "serving_fact_ms": round(serving_ms, 3),
            "bytes": path.stat().st_size if path.exists() else None,
        }
        return result
    finally:
        conn.close()


def _database_path(arguments: argparse.Namespace, runtime: dict[str, Any]) -> Path:
    if arguments.dataset == "synthetic":
        path = _synthetic_path(arguments)
        if not path.exists():
            raise RuntimeError(f"synthetic database is missing; run --mode prepare-synthetic first: {path}")
        return path
    return Path(runtime["config"].analytics_database_path()).resolve()


def _source_ids(connection: Any) -> list[int]:
    rows = connection.execute(
        "SELECT DISTINCT _source_id FROM etl_dataflow_runs WHERE _source_id IS NOT NULL ORDER BY _source_id"
    ).fetchall()
    return [int(row[0]) for row in rows]


def _target_id(connection: Any, requested: str | None) -> str:
    if requested:
        row = connection.execute(
            "SELECT 1 FROM etl_dataflow_runs WHERE CAST(dataflow_id AS VARCHAR) = ? LIMIT 1",
            [requested],
        ).fetchone()
        if row:
            return requested
    row = connection.execute(
        """
        SELECT CAST(dataflow_id AS VARCHAR)
        FROM etl_dataflow_runs
        WHERE dataflow_id IS NOT NULL
        ORDER BY __event_time DESC NULLS LAST, _source_id ASC, _file_uri ASC, _record_line ASC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("no dataflow entity is available for drawer workload")
    return str(row[0])


def _open_context(
    arguments: argparse.Namespace,
    runtime: dict[str, Any],
) -> tuple[Any, list[int], str, dict[str, Any]]:
    path = _database_path(arguments, runtime)
    started = time.perf_counter()
    connection = runtime["connections"].connect(path, read_only=True)
    try:
        # UDF registration touches DuckDB's connection catalog.  Serialize
        # that setup boundary, then begin the request snapshot; query work
        # remains concurrent on independent connections.
        with runtime["register_lock"]:
            runtime["register"](connection)
        connection.execute("BEGIN TRANSACTION")
        source_ids = _source_ids(connection)
        if arguments.dataset == "synthetic":
            generation = f"synthetic-v1:{arguments.synthetic_rows}:sources-1-2"
        else:
            from datacoolie_studio.domains.analytics import access

            generation = access.materialization_token_from_connection(connection, source_ids)
        settings = {
            "configured_threads_env": os.environ.get(THREAD_ENV, "auto"),
            "threads": int(connection.execute("SELECT current_setting('threads')").fetchone()[0]),
            "memory_limit": str(connection.execute("SELECT current_setting('memory_limit')").fetchone()[0]),
            "temp_directory": str(connection.execute("SELECT current_setting('temp_directory')").fetchone()[0]),
            "timezone": str(connection.execute("SELECT current_setting('TimeZone')").fetchone()[0]),
        }
        metadata = {
            "database": str(path),
            "database_bytes": path.stat().st_size if path.exists() else None,
            "source_ids": source_ids,
            "generation": generation,
            "connection_setup_ms": round((time.perf_counter() - started) * 1000, 3),
            "settings": settings,
        }
        return connection, source_ids, generation, metadata
    except Exception:
        _close_context(connection)
        raise


def _close_context(connection: Any) -> None:
    """Rollback the request-owned snapshot before releasing its connection."""

    try:
        connection.execute("ROLLBACK")
    except Exception:
        # A failed setup may have already released the transaction; preserve
        # the original failure while still closing the connection.
        pass
    finally:
        connection.close()


def _counts(connection: Any, source_ids: list[int]) -> dict[str, Any]:
    ids = ",".join("?" for _ in source_ids) or "NULL"
    result: dict[str, Any] = {}
    for name, table in (("dataflow", "etl_dataflow_runs"), ("job", "etl_job_runs")):
        result[f"raw_{name}_rows"] = int(connection.execute(f"SELECT COUNT(*) FROM {table} WHERE _source_id IN ({ids})", source_ids).fetchone()[0])
    result["raw_dataflow_entities"] = int(connection.execute(
        f"SELECT COUNT(DISTINCT dataflow_id) FROM etl_dataflow_runs WHERE _source_id IN ({ids})", source_ids
    ).fetchone()[0])
    result["raw_dataflow_runs"] = int(connection.execute(
        f"SELECT COUNT(DISTINCT dataflow_id || '|' || dataflow_run_id) FROM etl_dataflow_runs WHERE _source_id IN ({ids})", source_ids
    ).fetchone()[0])
    return result


def _filters() -> dict[str, str]:
    return {"range": "30d", "_asOf": AS_OF, "_relativeStartTime": RELATIVE_START}


def _run_workload(
    runtime: dict[str, Any],
    connection: Any,
    source_ids: list[int],
    generation: str,
    workload_name: str,
    target: str,
) -> dict[str, Any]:
    workload = WORKLOADS[workload_name]
    target_id = target if workload["target"] else None
    started = time.perf_counter()
    result = runtime["registry"].dataflow_registry_read_model(
        [],
        _filters(),
        limit=int(workload["limit"]),
        offset=0,
        sort_by=str(workload["sort_by"]),
        sort_dir="desc",
        section="overview",
        facet=str(workload["facet"]),
        target_dataflow_id=target_id,
        analytics_context=(connection, source_ids, generation),
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "workload": workload_name,
        "description": workload["description"],
        "elapsed_ms": round(elapsed_ms, 3),
        "digest": _digest(result),
        "generation": result.get("generation"),
        "total_records": int(result.get("total_records") or 0),
        "record_count": len(result.get("records") or []),
        "result": result,
    }


def _directory_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _cleanup_owned_temp(path: Path) -> None:
    """Remove only a worker directory created by this diagnostic."""

    try:
        resolved = path.resolve()
        expected_parent = (BUDGET_ROOT / "temp").resolve()
        marker = resolved.parent / f".{resolved.name}.registry-benchmark-owned"
        if resolved.parent != expected_parent or not resolved.name.startswith("cell-") or not marker.is_file():
            return
        if resolved.exists():
            shutil.rmtree(resolved)
        marker.unlink(missing_ok=True)
    except OSError:
        # A diagnostic receipt still contains the measured spill peak if a
        # platform holds a transient file open.
        return


class _ResourceSampler:
    def __init__(self, spill_dir: Path) -> None:
        self.spill_dir = spill_dir
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.peak_rss: int | None = None
        self.peak_spill = _directory_bytes(spill_dir)
        self.start_spill = self.peak_spill
        self.process = None
        self.start_cpu_ms: float | None = None
        try:
            import psutil

            self.process = psutil.Process(os.getpid())
        except Exception:
            self.process = None

    def _sample(self) -> None:
        while not self.stop_event.wait(0.02):
            if self.process is not None:
                try:
                    rss = int(self.process.memory_info().rss)
                    self.peak_rss = max(self.peak_rss or rss, rss)
                except Exception:
                    pass
            self.peak_spill = max(self.peak_spill, _directory_bytes(self.spill_dir))

    def __enter__(self) -> _ResourceSampler:
        if self.process is not None:
            try:
                self.peak_rss = int(self.process.memory_info().rss)
                times = self.process.cpu_times()
                self.start_cpu_ms = (float(times.user) + float(times.system)) * 1000
            except Exception:
                pass
        self.thread = threading.Thread(target=self._sample, name="registry-resource-sampler", daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=1)
        self.peak_spill = max(self.peak_spill, _directory_bytes(self.spill_dir))

    def result(self) -> dict[str, Any]:
        cpu_ms = None
        if self.process is not None:
            try:
                times = self.process.cpu_times()
                current_cpu_ms = (float(times.user) + float(times.system)) * 1000
                cpu_ms = round(current_cpu_ms - (self.start_cpu_ms or current_cpu_ms), 3)
            except Exception:
                pass
        return {
            "peak_rss_bytes": self.peak_rss,
            "peak_spill_bytes": self.peak_spill,
            "spill_delta_bytes": max(0, self.peak_spill - self.start_spill),
            "cpu_time_ms": cpu_ms,
        }


def _cache_key(runtime: dict[str, Any], workload: str, generation: str) -> Any:
    return runtime["ResultCacheKey"](
        environment_id=900001,
        namespace="monitoring.page.registry-benchmark",
        parameters_fingerprint=runtime["fingerprint"]({"workload": workload, "filters": _filters()}),
        input_fingerprint=runtime["fingerprint"]({"generation": generation}),
        producer_version="registry-benchmark-v1",
    )


def _run_t0_workload(
    arguments: argparse.Namespace,
    runtime: dict[str, Any],
    workload_name: str,
    temp_root: Path,
) -> dict[str, Any]:
    setup_samples: list[float] = []
    uncached_samples: list[float] = []
    uncached_results: list[dict[str, Any]] = []
    resource_samples: list[dict[str, Any]] = []
    target: str | None = None
    base_metadata: dict[str, Any] | None = None

    for _index in range(arguments.repeats):
        connection = None
        try:
            setup_started = time.perf_counter()
            connection, source_ids, generation, metadata = _open_context(arguments, runtime)
            if WORKLOADS[workload_name]["target"]:
                target = _target_id(connection, arguments.target_id) if target is None else target
            setup_samples.append((time.perf_counter() - setup_started) * 1000)
            base_metadata = {**metadata, "counts": _counts(connection, source_ids)}
            with _ResourceSampler(temp_root) as sampler:
                query = _run_workload(runtime, connection, source_ids, generation, workload_name, target)
            resource_samples.append(sampler.result())
            uncached_samples.append(float(query["elapsed_ms"]))
            uncached_results.append(query)
        finally:
            if connection is not None:
                _close_context(connection)

    if base_metadata is None:
        raise RuntimeError("T0 did not open a connection")
    # The first cache build uses a fresh connection and the real result-cache
    # contract.  The payload is never written to disk: workers force sqlite://.
    connection, source_ids, generation, _metadata = _open_context(arguments, runtime)
    try:
        if WORKLOADS[workload_name]["target"]:
            target = _target_id(connection, arguments.target_id) if target is None else target
        provider = runtime["cache_provider"]()
        key = _cache_key(runtime, workload_name, generation)
        runtime["clear_memory_cache"]()
        cache_started = time.perf_counter()
        cached_result, was_hit = runtime["get_or_compute"](
            provider.store,
            provider.coordinator,
            key,
            lambda: _run_workload(runtime, connection, source_ids, generation, workload_name, target)["result"],
        )
        cache_build_ms = (time.perf_counter() - cache_started) * 1000
        runtime["clear_memory_cache"]()
        l2_started = time.perf_counter()
        l2_result = provider.store.get(key)
        cache_l2_ms = (time.perf_counter() - l2_started) * 1000
        l1_started = time.perf_counter()
        l1_result, l1_hit = runtime["get_or_compute"](
            provider.store,
            provider.coordinator,
            key,
            lambda: (_ for _ in ()).throw(RuntimeError("cache producer ran on hit")),
        )
        cache_l1_ms = (time.perf_counter() - l1_started) * 1000
        cache_equality = bool(
            cached_result is not None
            and l2_result is not None
            and l1_result is not None
            and _equal(cached_result.payload, l2_result.payload)
            and _equal(cached_result.payload, l1_result.payload)
        )
        cache_info = {
            "build_ms": round(cache_build_ms, 3),
            "build_was_hit": bool(was_hit),
            "l2_sqlite_get_ms": round(cache_l2_ms, 3),
            "l1_memory_hit_ms": round(cache_l1_ms, 3),
            "l1_was_hit": bool(l1_hit),
            "payload_digest": _digest(cached_result.payload),
            "payload_equality": cache_equality,
            "persistent_store": False,
            "cache_url": os.environ.get(CACHE_ENV),
        }
    finally:
        _close_context(connection)

    return {
        "workload": workload_name,
        "description": WORKLOADS[workload_name]["description"],
        "target_dataflow_id": target,
        "setup_ms": {
            "samples": [round(value, 3) for value in setup_samples],
            "median": round(statistics.median(setup_samples), 3),
        },
        "uncached_ms": {
            "samples": [round(value, 3) for value in uncached_samples],
            "median": round(statistics.median(uncached_samples), 3),
        },
        "uncached_output_digest": uncached_results[0]["digest"],
        "uncached_outputs_equal": all(
            _equal(uncached_results[0]["result"], item["result"]) for item in uncached_results[1:]
        ),
        "uncached_generations": sorted({item["generation"] for item in uncached_results}),
        "uncached_generation_coherent": len({item["generation"] for item in uncached_results}) == 1,
        "resources": {
            "samples": resource_samples,
            "peak_rss_bytes": max((item["peak_rss_bytes"] or 0 for item in resource_samples), default=0) or None,
            "peak_spill_bytes": max((item["peak_spill_bytes"] for item in resource_samples), default=0),
            "cpu_time_ms": round(sum(item["cpu_time_ms"] or 0 for item in resource_samples), 3),
        },
        "cache": cache_info,
        "metadata": base_metadata,
    }


def _run_t0_cell(arguments: argparse.Namespace, runtime: dict[str, Any], temp_root: Path) -> dict[str, Any]:
    started = time.perf_counter()
    workloads = _parse_csv(arguments.workloads, name="workloads")
    unknown = sorted(set(workloads) - set(WORKLOADS))
    if unknown:
        raise SystemExit(f"unknown workload(s): {', '.join(unknown)}")
    report = {
        "kind": "T0",
        "dataset": arguments.dataset,
        "thread_profile": arguments.thread_profile,
        "runtime": {
            "import_ms": runtime["import_ms"],
            "python": sys.version.split()[0],
            "source_root": runtime["source_root"],
            "monitoring_source_root": runtime["monitoring_source_root"],
            "baseline_root": runtime["baseline_root"],
            "source_manifest": runtime["source_manifest"],
        },
        "workloads": [_run_t0_workload(arguments, runtime, workload, temp_root) for workload in workloads],
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    return report


def _task(
    runtime: dict[str, Any],
    arguments: argparse.Namespace,
    workload_name: str,
    target: str,
    release: threading.Event,
    release_time: list[float],
    index: int,
) -> dict[str, Any]:
    release.wait()
    started = time.perf_counter()
    queued_ms = (started - release_time[0]) * 1000
    connection = None
    try:
        connection, source_ids, generation, _metadata = _open_context(arguments, runtime)
        query = _run_workload(runtime, connection, source_ids, generation, workload_name, target)
        return {
            "index": index,
            "queue_ms": round(queued_ms, 3),
            "request_ms": round((time.perf_counter() - started) * 1000, 3),
            "digest": query["digest"],
            "generation": query["generation"],
            "total_records": query["total_records"],
            "record_count": query["record_count"],
            "_result": query["result"],
            "error": None,
        }
    except Exception as exc:
        return {
            "index": index,
            "queue_ms": round(queued_ms, 3),
            "request_ms": round((time.perf_counter() - started) * 1000, 3),
            "digest": None,
            "generation": None,
            "total_records": None,
            "record_count": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if connection is not None:
            _close_context(connection)


def _run_t1_cell(arguments: argparse.Namespace, runtime: dict[str, Any], temp_root: Path) -> dict[str, Any]:
    started = time.perf_counter()
    connection, source_ids, generation, metadata = _open_context(arguments, runtime)
    try:
        target = _target_id(connection, arguments.target_id)
        counts = _counts(connection, source_ids)
    finally:
        _close_context(connection)
    release = threading.Event()
    release_time = [0.0]
    batch_started = time.perf_counter()
    with _ResourceSampler(temp_root) as sampler:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=arguments.concurrency, thread_name_prefix="registry-request") as executor:
            futures = [
                executor.submit(
                    _task,
                    runtime,
                    arguments,
                    arguments.workload,
                    target,
                    release,
                    release_time,
                    index,
                )
                for index in range(arguments.requests)
            ]
            release_time[0] = time.perf_counter()
            release.set()
            results = [future.result() for future in futures]
    batch_elapsed_ms = (time.perf_counter() - batch_started) * 1000
    successful = [item for item in results if item["error"] is None]
    request_times = [float(item["request_ms"]) for item in successful]
    queue_times = [float(item["queue_ms"]) for item in successful]
    digests = [item["digest"] for item in successful]
    digest = digests[0] if digests else None
    generation_mismatches = sorted({str(item["generation"]) for item in successful if item["generation"] != generation})
    outputs_equal = bool(successful) and all(_equal(successful[0]["_result"], item["_result"]) for item in successful[1:])
    output_difference_paths: list[dict[str, str]] = []
    if successful:
        first_result = successful[0]["_result"]
        for item in successful[1:]:
            if not _equal(first_result, item["_result"]):
                output_difference_paths.extend(_difference_paths(first_result, item["_result"]))
                if len(output_difference_paths) >= 32:
                    break
    sampler_metrics = sampler.result()
    for item in results:
        item.pop("_result", None)
    return {
        "kind": "T1-cell",
        "dataset": arguments.dataset,
        "workload": arguments.workload,
        "monitoring_source_root": runtime["monitoring_source_root"],
        "source_root": runtime["source_root"],
        "baseline_root": runtime["baseline_root"],
        "source_manifest": runtime["source_manifest"],
        "thread_profile": arguments.thread_profile,
        "concurrency": arguments.concurrency,
        "requests": arguments.requests,
        "settings": metadata["settings"],
        "counts": counts,
        "request_p50_ms": round(statistics.median(request_times), 3) if request_times else None,
        "request_p95_ms": round(statistics.quantiles(request_times, n=20, method="inclusive")[18], 3) if len(request_times) >= 2 else (round(request_times[0], 3) if request_times else None),
        "queue_p50_ms": round(statistics.median(queue_times), 3) if queue_times else None,
        "queue_p95_ms": round(statistics.quantiles(queue_times, n=20, method="inclusive")[18], 3) if len(queue_times) >= 2 else (round(queue_times[0], 3) if queue_times else None),
        "throughput_rps": round(len(successful) / (batch_elapsed_ms / 1000), 3) if batch_elapsed_ms > 0 else None,
        "batch_elapsed_ms": round(batch_elapsed_ms, 3),
        "peak_rss_bytes": sampler_metrics["peak_rss_bytes"],
        "peak_spill_bytes": sampler_metrics["peak_spill_bytes"],
        "spill_delta_bytes": sampler_metrics["spill_delta_bytes"],
        "cpu_time_ms": sampler_metrics["cpu_time_ms"],
        "failed_requests": len(results) - len(successful),
        "output_digest": digest,
        "output_digest_variants": sorted(set(digests)),
        "outputs_equal": outputs_equal,
        "output_difference_paths": output_difference_paths[:32],
        "generations": sorted({item["generation"] for item in successful}),
        "expected_generation": generation,
        "generation_mismatches": generation_mismatches,
        "provenance_coherent": not generation_mismatches,
        "errors": [item["error"] for item in results if item["error"] is not None],
        "samples": results,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def _child_environment(arguments: argparse.Namespace, *, name: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = "0"
    env[TEMP_ENV] = str((BUDGET_ROOT / "temp" / name).resolve())
    env[CACHE_ENV] = "sqlite://"
    if arguments.thread_profile == "auto":
        env.pop(THREAD_ENV, None)
    else:
        env[THREAD_ENV] = arguments.thread_profile
    if arguments.memory_limit:
        env[MEMORY_ENV] = arguments.memory_limit
    return env


def _run_child(arguments: argparse.Namespace, *, mode: str, name: str) -> dict[str, Any]:
    command = [
        sys.executable,
        str(SCRIPT),
        "--mode",
        "cell",
        "--phase",
        mode,
        "--dataset",
        arguments.dataset,
        "--workloads",
        arguments.workloads,
        "--workload",
        arguments.workload,
        "--thread-profile",
        arguments.thread_profile,
        "--concurrency",
        str(arguments.concurrency),
        "--requests",
        str(arguments.requests),
        "--repeats",
        str(arguments.repeats),
        "--source-root",
        arguments.source_root,
        "--synthetic-rows",
        str(arguments.synthetic_rows),
        "--synthetic-path",
        str(_synthetic_path(arguments)),
    ]
    if arguments.target_id:
        command.extend(["--target-id", arguments.target_id])
    if arguments.baseline_root:
        command.extend(["--baseline-root", arguments.baseline_root])
    if arguments.memory_limit:
        command.extend(["--memory-limit", arguments.memory_limit])
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=str(STUDIO_ROOT),
        env=_child_environment(arguments, name=name),
        capture_output=True,
        text=True,
        check=False,
    )
    launch_ms = (time.perf_counter() - started) * 1000
    if completed.returncode != 0:
        raise RuntimeError(
            f"worker failed ({completed.returncode}) for {name}:\n{completed.stderr[-4000:]}\n{completed.stdout[-2000:]}"
        )
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"worker produced no JSON for {name}: {completed.stderr[-2000:]}")
    report = json.loads(lines[-1])
    report["process_launch_ms"] = round(launch_ms, 3)
    report["worker_stderr"] = completed.stderr[-1000:] if completed.stderr.strip() else None
    return report


def _validate_workloads(value: str) -> list[str]:
    workloads = _parse_csv(value, name="workloads")
    unknown = sorted(set(workloads) - set(WORKLOADS))
    if unknown:
        raise SystemExit(f"unknown workload(s): {', '.join(unknown)}")
    return workloads


def _run_t0(arguments: argparse.Namespace) -> dict[str, Any]:
    workloads = _validate_workloads(arguments.workloads)
    if arguments.dataset == "synthetic":
        runtime = _load_runtime(
            Path(arguments.source_root),
            baseline_root=Path(arguments.baseline_root) if arguments.baseline_root else None,
        )
        synthetic = _create_synthetic_database(arguments, runtime)
    else:
        synthetic = None
    cells = []
    for workload in workloads:
        child_args = argparse.Namespace(**vars(arguments))
        child_args.workloads = workload
        child_args.workload = workload
        cells.append(_run_child(child_args, mode="t0", name=f"t0-{arguments.dataset}-{workload}"))
    return {
        "kind": "T0",
        "dataset": arguments.dataset,
        "thread_profile": arguments.thread_profile,
        "workloads": workloads,
        "synthetic": synthetic,
        "cells": cells,
        "constraints": {
            "live_read_only": arguments.dataset == "live",
            "monitoring_source_root": arguments.baseline_root or str(_package_root(Path(arguments.source_root))),
            "source_root": arguments.source_root,
            "baseline_root": arguments.baseline_root,
            "cache_payload_persisted": False,
            "output_rows_persisted": False,
        },
    }


def _matrix_summary(cells: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for cell in cells:
        workload = str(cell["workload"])
        profile = str(cell["thread_profile"])
        grouped.setdefault(workload, {}).setdefault(profile, {})[str(cell["concurrency"])] = {
            "request_p50_ms": cell.get("request_p50_ms"),
            "request_p95_ms": cell.get("request_p95_ms"),
            "throughput_rps": cell.get("throughput_rps"),
            "peak_rss_bytes": cell.get("peak_rss_bytes"),
            "spill_delta_bytes": cell.get("spill_delta_bytes"),
            "failed_requests": cell.get("failed_requests"),
            "outputs_equal": cell.get("outputs_equal"),
            "provenance_coherent": cell.get("provenance_coherent"),
            "output_digest": cell.get("output_digest"),
            "output_digest_variants": cell.get("output_digest_variants"),
            "output_difference_paths": cell.get("output_difference_paths"),
        }
    recommendations: dict[str, Any] = {}
    for workload, profiles in grouped.items():
        auto = profiles.get("auto", {})
        candidate_scores: dict[str, list[float]] = {}
        auto_scores = [float(value["request_p50_ms"]) for value in auto.values() if value.get("request_p50_ms") is not None]
        auto_median = statistics.median(auto_scores) if auto_scores else None
        for profile, concurrency_values in profiles.items():
            scores = [float(value["request_p50_ms"]) for value in concurrency_values.values() if value.get("request_p50_ms") is not None]
            if scores:
                candidate_scores[profile] = scores
        invalid_profiles = {
            profile: sorted(
                concurrency
                for concurrency, value in concurrency_values.items()
                if value.get("failed_requests", 0)
                or value.get("outputs_equal") is not True
                or value.get("provenance_coherent") is not True
            )
            for profile, concurrency_values in profiles.items()
            if any(
                value.get("failed_requests", 0)
                or value.get("outputs_equal") is not True
                or value.get("provenance_coherent") is not True
                for value in concurrency_values.values()
            )
        }
        chosen = "auto"
        reason = "auto retained; no complete auto baseline"
        if auto_median is not None:
            viable = {
                profile: statistics.median(scores)
                for profile, scores in candidate_scores.items()
                if profile != "auto"
                and len(scores) == len(auto_scores)
                and profile not in invalid_profiles
            }
            if viable:
                best_profile, best_score = min(viable.items(), key=lambda pair: pair[1])
                if best_score <= auto_median * 0.9:
                    chosen = best_profile
                    reason = "reversible explicit override candidate is at least 10% faster across measured cells"
                else:
                    reason = "auto retained; no explicit profile cleared the 10% regression guard"
            if "auto" in invalid_profiles:
                chosen = "auto"
                reason = "auto retained pending output equality review; at least one auto cell failed the correctness gate"
        recommendations[workload] = {
            "recommended_profile": chosen,
            "reason": reason,
            "auto_p50_median_ms": auto_median,
            "candidate_p50_median_ms": candidate_scores,
            "correctness_gate_failures": invalid_profiles,
            "requires_explicit_deployment_decision": True,
        }
    return {"by_workload": grouped, "recommendations": recommendations}


def _run_t1(arguments: argparse.Namespace) -> dict[str, Any]:
    workloads = _validate_workloads(arguments.workloads)
    profiles = _parse_csv(arguments.thread_profiles, name="thread-profiles")
    concurrencies = [int(value) for value in _parse_csv(arguments.concurrencies, name="concurrencies")]
    if any(value not in {1, 2, 4} for value in concurrencies):
        raise SystemExit("--concurrencies values must be 1, 2 or 4")
    if arguments.requests < 20:
        raise SystemExit("--requests must be at least 20 for T1 qualification")
    runtime = (
        _load_runtime(
            Path(arguments.source_root),
            baseline_root=Path(arguments.baseline_root) if arguments.baseline_root else None,
        )
        if arguments.dataset == "synthetic"
        else None
    )
    synthetic = _create_synthetic_database(arguments, runtime) if runtime is not None else None
    cells: list[dict[str, Any]] = []
    for workload in workloads:
        for profile in profiles:
            for concurrency in concurrencies:
                child_args = argparse.Namespace(**vars(arguments))
                child_args.workload = workload
                child_args.workloads = workload
                child_args.thread_profile = profile
                child_args.concurrency = concurrency
                cells.append(_run_child(child_args, mode="t1", name=f"t1-{arguments.dataset}-{workload}-{profile}-{concurrency}"))
    return {
        "kind": "T1",
        "dataset": arguments.dataset,
        "monitoring_source_root": arguments.baseline_root or str(_package_root(Path(arguments.source_root))),
        "source_root": arguments.source_root,
        "baseline_root": arguments.baseline_root,
        "workloads": workloads,
        "thread_profiles": profiles,
        "concurrencies": concurrencies,
        "requests_per_cell": arguments.requests,
        "synthetic": synthetic,
        "cells": cells,
        "summary": _matrix_summary(cells),
        "decision": {
            "auto_default_changed": False,
            "per_request_thread_toggle": False,
            "live_service_restarted": False,
            "maintenance_and_recovery": "isolated subprocess lifecycle only; no live maintenance or service restart",
        },
    }


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("prepare-synthetic", "t0", "t1", "cell", "route"))
    parser.add_argument("--phase", choices=("t0", "t1"))
    parser.add_argument("--dataset", choices=("live", "synthetic"), default="live")
    parser.add_argument("--workloads", default=",".join(WORKLOADS))
    parser.add_argument("--workload", default="drawer-all", choices=tuple(WORKLOADS))
    parser.add_argument("--thread-profile", default="auto")
    parser.add_argument("--thread-profiles", default="1,2,4,auto")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--concurrencies", default="1,2,4")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--synthetic-rows", type=int, default=10000)
    parser.add_argument("--synthetic-path")
    parser.add_argument(
        "--source-root",
        default=str(CURRENT_SOURCE_ROOT),
        help="source checkout root or datacoolie_studio package root; defaults to the current checkout",
    )
    parser.add_argument(
        "--baseline-root",
        help="optional captured datacoolie_studio root overlaid for stable Monitoring imports",
    )
    parser.add_argument("--target-id")
    parser.add_argument("--environment-id", type=int, default=1)
    parser.add_argument("--as-of", default=AS_OF)
    parser.add_argument("--section", default="overview", choices=("overview", "runtime", "execution", "configuration", "read-watermark", "technical"))
    parser.add_argument("--memory-limit")
    parser.add_argument("--receipt", type=Path)
    arguments = parser.parse_args(argv)
    arguments.source_root = str(_import_root(Path(arguments.source_root)))
    if arguments.baseline_root:
        arguments.baseline_root = str(_package_root(Path(arguments.baseline_root)))
    if arguments.repeats < 1:
        raise SystemExit("--repeats must be positive")
    if arguments.concurrency not in {1, 2, 4}:
        raise SystemExit("--concurrency must be 1, 2 or 4")
    if arguments.mode == "cell" and arguments.phase is None:
        raise SystemExit("--phase is required with --mode cell")
    if arguments.mode != "cell" and arguments.phase is not None:
        raise SystemExit("--phase is only valid with --mode cell")
    return arguments


def _write_receipt(report: dict[str, Any], path: Path | None) -> None:
    target = path or BUDGET_ROOT / f"registry-{str(report.get('kind', 'receipt')).lower()}-{int(time.time())}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    report["receipt"] = str(target)
    target.write_text(json.dumps(report, indent=2, sort_keys=True, default=_json_default) + "\n", encoding="utf-8")


def _run_route_diagnostics(arguments: argparse.Namespace) -> dict[str, Any]:
    """Exercise public reader/cache/serialization with complete source isolation.

    ASGI transport deliberately skips application lifespan and TCP overhead.
    Only digests, timings and resource metadata leave this process.
    """
    import asyncio
    from datetime import timedelta, timezone

    if arguments.baseline_root:
        raise SystemExit("route mode requires --source-root for a complete baseline; overlays are unsupported")
    if arguments.dataset != "live":
        raise SystemExit("route mode requires an admitted live workspace; synthetic read-model files are not publications")
    if arguments.requests < 1:
        raise SystemExit("--requests must be positive")
    temp_root = _configure_worker_environment(arguments, cell_name="route")
    sys.path.insert(0, arguments.source_root)
    import httpx
    from datacoolie_studio.main import app
    from datacoolie_studio.api.v1.routes import monitoring
    from datacoolie_studio.domains.analytics import access, schema
    from datacoolie_studio.domains.monitoring import evidence_hydration, page_cache, page_service

    anchor = datetime.fromisoformat(arguments.as_of.replace("Z", "+00:00"))
    if anchor.tzinfo is None:
        raise SystemExit("--as-of requires an explicit UTC offset")
    samples: list[dict[str, Any]] = []

    async def measure() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://registry-diagnostic.local",
        ) as client:
            async def request(index: int, repeat: int, client_index: int) -> None:
                params = {
                    "range": "30d", "section": arguments.section,
                    "dataflowId": arguments.target_id or DEFAULT_TARGET,
                    "asOf": (anchor + timedelta(seconds=index)).astimezone(timezone.utc).isoformat(),
                }
                started = time.perf_counter()
                response = await client.get(
                    f"/api/v1/environments/{arguments.environment_id}/monitoring/dataflow-registry/detail",
                    params=params,
                )
                elapsed = (time.perf_counter() - started) * 1000
                data = response.json()
                samples.append({
                    "scope_index": index, "repeat": repeat, "client": client_index,
                    "wall_ms": round(elapsed, 3), "status": response.status_code,
                    "server_timing": response.headers.get("Server-Timing"),
                    "body_bytes": len(response.content),
                    "generation": data.get("summary", {}).get("analytics_generation"),
                    "records_digest": _digest(data.get("records")),
                })

            for index in range(arguments.requests):
                for repeat in (0, 1):
                    await asyncio.gather(*[
                        request(index, repeat, client_index)
                        for client_index in range(arguments.concurrency)
                    ])

    try:
        with _ResourceSampler(temp_root) as sampler:
            asyncio.run(measure())
        return {
            "kind": "registry-route", "source_root": arguments.source_root,
            "module_paths": {module.__name__: module.__file__ for module in (
                monitoring, access, schema, page_cache, page_service, evidence_hydration,
            )},
            "thread_profile": arguments.thread_profile, "memory_limit": arguments.memory_limit,
            "environment_id": arguments.environment_id, "section": arguments.section,
            "concurrency": arguments.concurrency, "as_of": arguments.as_of,
            "note": "Complete source; ASGI excludes lifespan/TCP; private in-memory result cache; no source writes or record export. First sample is fresh process, not OS disk-cold. Concurrent first-scope requests may coalesce; use Server-Timing for actual hit/miss.",
            "resources": sampler.result(), "samples": samples,
            "repeat_records_equal": all(
                len({sample["records_digest"] for sample in samples if sample["scope_index"] == index}) == 1
                for index in range(arguments.requests)
            ),
        }
    finally:
        _cleanup_owned_temp(temp_root)


def main(argv: list[str] | None = None) -> int:
    arguments = _arguments(argv)
    BUDGET_ROOT.mkdir(parents=True, exist_ok=True)
    if arguments.mode == "prepare-synthetic":
        runtime = _load_runtime(
            Path(arguments.source_root),
            baseline_root=Path(arguments.baseline_root) if arguments.baseline_root else None,
        )
        report = {
            "kind": "synthetic-prepare",
            "runtime": {
                "import_ms": runtime["import_ms"],
                "source_root": runtime["source_root"],
                "monitoring_source_root": runtime["monitoring_source_root"],
                "baseline_root": runtime["baseline_root"],
                "source_manifest": runtime["source_manifest"],
            },
            "synthetic": _create_synthetic_database(arguments, runtime),
        }
    elif arguments.mode == "t0":
        report = _run_t0(arguments)
    elif arguments.mode == "t1":
        report = _run_t1(arguments)
    elif arguments.mode == "route":
        report = _run_route_diagnostics(arguments)
    else:
        temp_root = _configure_worker_environment(arguments, cell_name=f"cell-{arguments.phase}-{arguments.workload}")
        runtime = _load_runtime(
            Path(arguments.source_root),
            baseline_root=Path(arguments.baseline_root) if arguments.baseline_root else None,
        )
        try:
            if arguments.dataset == "synthetic" and not _synthetic_path(arguments).exists():
                _create_synthetic_database(arguments, runtime)
            if arguments.phase == "t0":
                report = _run_t0_cell(arguments, runtime, temp_root)
            else:
                report = _run_t1_cell(arguments, runtime, temp_root)
        finally:
            if arguments.dataset == "live":
                _cleanup_owned_temp(temp_root)
    _write_receipt(report, arguments.receipt)
    print(json.dumps(report, sort_keys=True, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
