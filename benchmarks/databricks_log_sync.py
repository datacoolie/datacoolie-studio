from __future__ import annotations

import argparse
import json
import statistics
import time

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.db.session import create_session
from datacoolie_studio.domains.logs.ingestion import refresh_log_source_cache


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark a configured Databricks log source sync."
    )
    parser.add_argument("--source-id", required=True, type=int)
    parser.add_argument("--runs", type=int, default=1)
    arguments = parser.parse_args()
    if arguments.runs < 1:
        parser.error("--runs must be positive")

    session = create_session()
    try:
        source = session.get(EnvironmentSource, arguments.source_id)
        if source is None or source.source_kind != "logs":
            raise SystemExit("Configured log source was not found")
        if source.storage_provider != "dbfs":
            raise SystemExit("Benchmark source must use Databricks storage")
        samples = [_run_once(session, source) for _index in range(arguments.runs)]
        print(
            json.dumps(
                {
                    "source_id": source.id,
                    "runs": arguments.runs,
                    "cold_ms": samples[0]["elapsed_ms"],
                    "warm_median_ms": round(
                        statistics.median(
                            [
                                sample["elapsed_ms"]
                                for sample in samples[1:] or samples
                            ]
                        ),
                        3,
                    ),
                    "samples": samples,
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        session.close()


def _run_once(session, source: EnvironmentSource) -> dict[str, object]:
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
        "message": latest_job.get("message"),
        "elapsed_ms": round(elapsed_ms, 3),
        "timings_ms": result.get("timings_ms"),
        "storage_io": result.get("storage_io"),
        "record_counts": result.get("record_counts"),
    }


if __name__ == "__main__":
    main()
