"""Developer benchmark for the v6 analytics-upgrade replay path.

This is intentionally a source-checkout diagnostic rather than a published Studio command.
It creates a disposable SQLite workspace and local v6 log roots, runs one complete analytics
upgrade, and prints a compact JSON receipt. The receipt is useful for boundary-freeze evidence;
release thresholds still require the representative-volume benchmark from the coordinated release
plan.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


def _native_record(fields):
    # Benchmark fixtures are checkout-only and never enter the runtime package.
    import copy
    kind = fields.get("_type")
    filename = {"job_run_log": "job_snapshot.json", "dataflow_run_log": "dataflow_snapshot.json", "system_log": "system_snapshot.json"}[kind]
    fixture = Path(__file__).parents[2] / "tests/fixtures/datacoolie/logging/v6" / filename
    record = copy.deepcopy(json.loads(fixture.read_text().splitlines()[0]))
    record.update(fields)
    record["log_schema_version"] = 6
    return record


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=int, default=2, help="number of disposable Log sources")
    parser.add_argument(
        "--files-per-stream",
        type=int,
        default=4,
        help="execution JSON files per source and stream",
    )
    parser.add_argument(
        "--rows-per-file",
        type=int,
        default=5,
        help="newline-delimited records per execution JSON file",
    )
    return parser.parse_args(argv)


def _validate_arguments(arguments: argparse.Namespace) -> None:
    for name in ("sources", "files_per_stream", "rows_per_file"):
        if getattr(arguments, name) < 1:
            raise SystemExit(f"--{name.replace('_', '-')} must be at least 1")


def _write_execution_file(
    path: Path,
    *,
    stream: str,
    source_index: int,
    file_index: int,
    rows_per_file: int,
) -> None:
    records: list[dict[str, Any]] = []
    for row_index in range(rows_per_file):
        ordinal = file_index * rows_per_file + row_index
        if stream == "job_run_log":
            record = _native_record({
                "_type": "job_run_log",
                "log_schema_version": 3,
                "job_id": f"job-{source_index}-{ordinal}",
                "status": "succeeded",
                "start_time": "2026-08-03T00:00:00Z",
                "end_time": "2026-08-03T00:01:00Z",
            })
        else:
            record = _native_record({
                "_type": "dataflow_run_log",
                "log_schema_version": 3,
                "dataflow_run_id": f"run-{source_index}-{ordinal}",
                "dataflow_id": f"flow-{source_index}-{ordinal}",
                "job_id": f"job-{source_index}-{ordinal}",
                "job_num": 2,
                "job_index": 1,
                "status": "succeeded",
                "start_time": "2026-08-03T00:00:00Z",
                "end_time": "2026-08-03T00:01:00Z",
                "transform_select_columns": '["id"]',
                "transform_configure": "{}",
            })
        records.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n",
        encoding="utf-8",
    )


def _write_system_file(path: Path, *, source_index: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            _native_record({
                "_type": "system_log",
                "log_schema_version": 3,
                "job_id": f"job-{source_index}-0",
                "event_name": "run_started",
                "ts": "2026-08-03T00:00:00Z",
                "level": "INFO",
                "logger": "datacoolie",
                "msg": "run started",
            }),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _create_workspace(root: Path, arguments: argparse.Namespace) -> None:
    from datacoolie_studio.db.models import Environment, EnvironmentSource, Project
    from datacoolie_studio.db.session import create_session, init_db

    init_db()
    session = create_session()
    try:
        project = Project(name="analytics-upgrade-benchmark")
        session.add(project)
        session.flush()
        environment = Environment(project_id=project.id, name="dev")
        session.add(environment)
        session.flush()

        for source_index in range(arguments.sources):
            source_root = root / f"logs-{source_index + 1}"
            for stream in ("job_run_log", "dataflow_run_log"):
                for file_index in range(arguments.files_per_stream):
                    _write_execution_file(
                        source_root
                        / "execution_logs"
                        / stream
                        / "20260803"
                        / f"{stream}-{file_index}.json",
                        stream=stream,
                        source_index=source_index,
                        file_index=file_index,
                        rows_per_file=arguments.rows_per_file,
                    )
            _write_system_file(
                source_root / "system_logs" / "20260803" / "system-events.json",
                source_index=source_index,
            )
            session.add(
                EnvironmentSource(
                    environment_id=environment.id,
                    source_kind="logs",
                    uri=str(source_root),
                    storage_provider="local",
                    storage_auth_mode="none",
                    enabled=True,
                )
            )
        session.commit()
    finally:
        session.close()


def _job_receipt() -> tuple[dict[str, int], dict[str, float]]:
    from sqlalchemy import select

    from datacoolie_studio.db.models import SyncJob
    from datacoolie_studio.db.session import create_session

    counts: dict[str, int] = {}
    timings: dict[str, float] = {}
    session = create_session()
    try:
        jobs = session.scalars(
            select(SyncJob).where(SyncJob.job_type == "analytics_upgrade")
        ).all()
        for job in jobs:
            if not job.result_json:
                continue
            result = json.loads(job.result_json)
            for key, value in (result.get("record_counts") or {}).items():
                if isinstance(value, (int, float)):
                    counts[key] = counts.get(key, 0) + int(value or 0)
            for key, value in (result.get("timings_ms") or {}).items():
                timings[key] = timings.get(key, 0.0) + float(value or 0.0)
    finally:
        session.close()
    return counts, timings


def _file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _run(arguments: argparse.Namespace) -> dict[str, Any]:
    environment_keys = (
        "DATACOOLIE_STUDIO_DATA_DIR",
        "DATACOOLIE_STUDIO_DATABASE_URL",
    )
    previous_environment = {key: os.environ.get(key) for key in environment_keys}
    root: Path | None = None
    publication = None
    original_finalize = None
    dispose_engine = None
    tracemalloc_started = False
    try:
        root = Path(tempfile.mkdtemp(prefix="datacoolie-studio-analytics-baseline-"))
        os.environ["DATACOOLIE_STUDIO_DATA_DIR"] = str(root)
        os.environ["DATACOOLIE_STUDIO_DATABASE_URL"] = (
            f"sqlite:///{(root / 'studio.db').as_posix()}"
        )

        from datacoolie_studio.core.config import analytics_database_path
        from datacoolie_studio.db.session import _dispose_engine
        from datacoolie_studio.domains.analytics import publication as publication_module
        from datacoolie_studio.domains.analytics_upgrade import service as upgrade

        dispose_engine = _dispose_engine
        publication = publication_module
        _create_workspace(root, arguments)

        finalize_calls = 0
        original_finalize = publication.finalize_candidate

        def counted_finalize(*args: Any, **kwargs: Any) -> None:
            nonlocal finalize_calls
            finalize_calls += 1
            original_finalize(*args, **kwargs)

        publication.finalize_candidate = counted_finalize
        if not tracemalloc.is_tracing():
            tracemalloc.start()
            tracemalloc_started = True
        started = time.perf_counter()
        result = upgrade.run_analytics_upgrade_once(
            now=datetime(2026, 8, 3, tzinfo=timezone.utc)
        )
        elapsed_ms = (time.perf_counter() - started) * 1_000
        _current_memory, peak_memory = tracemalloc.get_traced_memory()

        counts, timings = _job_receipt()
        live_path = analytics_database_path()
        candidate_path = live_path.with_name("analytics.candidate.duckdb")
        receipt = {
            "result_state": result.get("state"),
            "elapsed_ms": round(elapsed_ms, 3),
            "tracemalloc_peak_bytes": peak_memory,
            "analytics_live_bytes": _file_size(live_path),
            "analytics_candidate_bytes": _file_size(candidate_path),
            "source_count": arguments.sources,
            "execution_file_count": arguments.sources * arguments.files_per_stream * 2,
            "system_file_count": arguments.sources,
            "rows_per_stream_file": arguments.rows_per_file,
            "finalize_calls": finalize_calls,
            "record_counts": counts,
            "timings_ms": {key: round(value, 3) for key, value in timings.items()},
        }
        if receipt["result_state"] != "succeeded":
            raise RuntimeError(json.dumps(receipt, sort_keys=True))
        return receipt
    finally:
        if publication is not None and original_finalize is not None:
            publication.finalize_candidate = original_finalize
        if tracemalloc_started:
            tracemalloc.stop()
        if dispose_engine is not None:
            dispose_engine()
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)
        for key, value in previous_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    _validate_arguments(arguments)
    print(json.dumps(_run(arguments), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
