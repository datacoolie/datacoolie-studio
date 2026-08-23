from __future__ import annotations

from typing import Any

import duckdb
import pytest

from datacoolie_studio.domains.monitoring.read_models.duration_distribution import (
    duration_distribution,
)


def _connection(rows: list[tuple[Any, ...]]) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE duration_runs (
          stage VARCHAR,
          operation_type VARCHAR,
          duration_seconds DOUBLE,
          normalized_status VARCHAR,
          dataflow_name VARCHAR,
          dataflow_id VARCHAR,
          dataflow_run_id VARCHAR
        )
        """
    )
    if rows:
        connection.executemany(
            "INSERT INTO duration_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    return connection


def _distribution(
    rows: list[tuple[Any, ...]],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    connection = _connection(rows)
    try:
        return duration_distribution(
            connection,
            "WITH filtered_dataflows AS (SELECT * FROM duration_runs)",
            [],
            group_column="stage",
            output_key="stage",
            limit=limit,
        )
    finally:
        connection.close()


def _row(
    duration: float | None,
    *,
    stage: str = "load",
    status: str = "succeeded",
    run_id: str = "run",
    operation_type: str = "etl",
) -> tuple[Any, ...]:
    return (stage, operation_type, duration, status, run_id, run_id, run_id)


@pytest.mark.parametrize(
    ("durations", "expected"),
    [
        ([7], (7, 7, 7, 7)),
        ([1, 2, 3, 4], (2, 3, 3, 4)),
        ([1, 2, 3, 4, 5], (2, 3, 4, 5)),
        ([2, 2, 2, 2], (2, 2, 2, 2)),
    ],
)
def test_duration_distribution_preserves_nearest_rank_percentiles(
    durations: list[float],
    expected: tuple[float, float, float, float],
) -> None:
    result = _distribution([
        _row(value, run_id=f"run-{index}")
        for index, value in enumerate(durations)
    ])

    assert len(result) == 1
    metric = result[0]
    assert (
        metric["q1_duration_seconds"],
        metric["p50_duration_seconds"],
        metric["q3_duration_seconds"],
        metric["p95_duration_seconds"],
    ) == expected


def test_duration_distribution_uses_only_completed_non_null_runs() -> None:
    result = _distribution([
        _row(10, status="succeeded", run_id="succeeded"),
        _row(20, status="failed", run_id="failed"),
        _row(30, status="skipped", run_id="skipped"),
        _row(1, status="pending", run_id="pending"),
        _row(2, status="running", run_id="running"),
        _row(None, status="succeeded", run_id="null"),
    ])

    metric = result[0]
    assert metric["count"] == 3
    assert metric["succeeded"] == 1
    assert metric["failed"] == 1
    assert metric["skipped"] == 1
    assert metric["success_rate"] == 50


def test_duration_distribution_bounds_details_to_selected_groups() -> None:
    rows = [
        *[_row(value, stage="slow", run_id=f"slow-{index}") for index, value in enumerate([1, 2, 3, 4, 100])],
        *[_row(value, stage="fast", run_id=f"fast-{index}", operation_type="copy") for index, value in enumerate([1, 1, 1])],
    ]

    result = _distribution(rows, limit=1)

    assert len(result) == 1
    assert result[0]["stage"] == "slow"
    assert result[0]["outlier_count"] == 1
    assert [item[2] for item in result[0]["outliers"]] == ["slow-4"]
    assert result[0]["operation_mix"] == "etl: 5"


def test_job_duration_outliers_include_runtime_context() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE job_duration_runs (
          operation_types VARCHAR,
          duration_seconds DOUBLE,
          normalized_status VARCHAR,
          job_id VARCHAR,
          engine_name VARCHAR,
          metadata_provider_name VARCHAR,
          platform_name VARCHAR
        )
        """
    )
    connection.executemany(
        "INSERT INTO job_duration_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ('["etl", "maintenance"]', duration, "succeeded", f"job-{index}", "duckdb", "file", "local")
            for index, duration in enumerate([1, 2, 3, 4, 100])
        ],
    )
    try:
        result = duration_distribution(
            connection,
            "WITH filtered_jobs AS (SELECT * FROM job_duration_runs)",
            [],
            group_column="operation_types",
            output_key="operation_type",
            limit=20,
            fact_kind="job",
        )
    finally:
        connection.close()

    assert result[0]["outliers"] == [[
        100.0,
        "job-4",
        "job-4",
        "succeeded",
        "etl, maintenance",
        "duckdb",
        "file",
        "local",
    ]]


def test_duration_distribution_returns_empty_for_no_eligible_runs() -> None:
    assert _distribution([
        _row(None, run_id="null"),
        _row(1, status="pending", run_id="pending"),
    ]) == []
