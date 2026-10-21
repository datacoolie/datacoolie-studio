"""Benchmark a configured Databricks log source against an explicit Studio target."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Sequence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", required=True, type=int)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument(
        "--data-dir",
        required=True,
        type=Path,
        help="existing disposable or explicitly approved Studio data directory",
    )
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help="acknowledge that the benchmark refreshes the selected log source",
    )
    return parser


def _validate_arguments(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> Path:
    if arguments.runs < 1:
        parser.error("--runs must be positive")
    if not arguments.allow_write:
        parser.error("--allow-write is required because this benchmark refreshes Studio state")
    data_dir = arguments.data_dir.expanduser().resolve()
    if not data_dir.is_dir():
        parser.error(f"--data-dir must be an existing directory: {data_dir}")
    return data_dir


def _run_once(session, source) -> dict[str, object]:
    from datacoolie_studio.domains.logs.ingestion import refresh_log_source_cache

    started = time.perf_counter()
    status = refresh_log_source_cache(
        session,
        source,
        job_type="performance_check",
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    latest_job = status.get("latest_job") or {}
    result = latest_job.get("result") or {}
    return {
        "job_id": latest_job.get("id"),
        "status": status.get("status"),
        "message": status.get("message"),
        "elapsed_ms": round(elapsed_ms, 3),
        "timings_ms": result.get("timings_ms"),
        "storage_io": result.get("storage_io"),
        "discovery_io": result.get("discovery_io"),
        "record_counts": result.get("record_counts"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    data_dir = _validate_arguments(parser, arguments)

    environment_keys = (
        "DATACOOLIE_STUDIO_DATA_DIR",
        "DATACOOLIE_STUDIO_DATABASE_URL",
    )
    previous_environment = {key: os.environ.get(key) for key in environment_keys}
    session = None
    try:
        os.environ["DATACOOLIE_STUDIO_DATA_DIR"] = str(data_dir)
        os.environ["DATACOOLIE_STUDIO_DATABASE_URL"] = (
            f"sqlite:///{(data_dir / 'db' / 'studio.db').as_posix()}"
        )

        from datacoolie_studio.db.models import EnvironmentSource
        from datacoolie_studio.db.session import create_session, init_db

        init_db()
        session = create_session()
        source = session.get(EnvironmentSource, arguments.source_id)
        if source is None or source.source_kind != "logs":
            raise ValueError("Configured log source was not found")
        if source.storage_provider != "dbfs":
            raise ValueError("Benchmark source must use Databricks storage")
        samples = [_run_once(session, source) for _index in range(arguments.runs)]
        print(
            json.dumps(
                {
                    "source_id": source.id,
                    "runs": arguments.runs,
                    "target_name": data_dir.name,
                    "cold_ms": samples[0]["elapsed_ms"],
                    "warm_median_ms": round(
                        statistics.median(
                            [sample["elapsed_ms"] for sample in samples[1:] or samples]
                        ),
                        3,
                    ),
                    "samples": samples,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - benchmark boundary maps operational errors
        # Provider exceptions may echo remote URIs or credential-bearing details.
        print(f"error: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        if session is not None:
            session.close()
        try:
            from datacoolie_studio.db.session import _dispose_engine
        except ImportError:
            pass
        else:
            _dispose_engine()
        for key, value in previous_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    raise SystemExit(main())
