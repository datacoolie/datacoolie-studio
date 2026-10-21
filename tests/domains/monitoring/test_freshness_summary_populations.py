from __future__ import annotations

from datetime import date, datetime, timezone

import duckdb
import pytest

from datacoolie_studio.domains.monitoring.read_models.freshness import _SUMMARY_SQL
from datacoolie_studio.domains.monitoring.dimensions import UNKNOWN_KEY


_EVENT_TIME = datetime(2026, 7, 20, tzinfo=timezone.utc)


def _summary(
    *,
    dataflows: list[tuple[date, datetime, str, str]] = (),
    jobs: list[tuple[datetime]] = (),
    etl_rows: list[tuple[str, bool, str, str | None, str, str]] = (),
    registry: list[tuple[datetime | None, str, str, bool, int, float | None, float | None]] = (),
) -> dict[str, object]:
    """Run the production summary against compact population fixtures."""

    with duckdb.connect() as conn:
        conn.execute(
            """
            CREATE TABLE filtered_dataflows (
                run_date DATE,
                event_time TIMESTAMPTZ,
                engine_name VARCHAR,
                metadata_provider_name VARCHAR
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE filtered_jobs (
                event_time TIMESTAMPTZ
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE etl_rows (
                normalized_status VARCHAR,
                v6_attempt_present BOOLEAN,
                v6_source_status VARCHAR,
                dataflow_id VARCHAR,
                movement_state VARCHAR,
                persistence_state VARCHAR
            )
            """
        )
        conn.execute("CREATE VIEW issue_etl_rows AS SELECT * FROM etl_rows")
        conn.execute(
            """
            CREATE TABLE registry (
                latest_freshness_at TIMESTAMPTZ,
                latest_status VARCHAR,
                latest_movement_state VARCHAR,
                watermark_enabled BOOLEAN,
                skipped_streak INTEGER,
                age_days DOUBLE,
                    age_seconds DOUBLE,
                    latest_watermark_issue_state VARCHAR
            )
            """
        )
        if dataflows:
            conn.executemany("INSERT INTO filtered_dataflows VALUES (?, ?, ?, ?)", dataflows)
        if jobs:
            conn.executemany("INSERT INTO filtered_jobs VALUES (?)", jobs)
        if etl_rows:
            conn.executemany("INSERT INTO etl_rows VALUES (?, ?, ?, ?, ?, ?)", etl_rows)
        if registry:
                conn.executemany(
                    """INSERT INTO registry (
                         latest_freshness_at, latest_status, latest_movement_state,
                         watermark_enabled, skipped_streak, age_days, age_seconds
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    registry,
                )

        result = conn.execute(_SUMMARY_SQL)
        columns = [column[0] for column in result.description]
        return dict(zip(columns, result.fetchone(), strict=True))


def test_summary_aggregates_each_population_once_for_normal_data() -> None:
    summary = _summary(
        dataflows=[
            (date(2026, 7, 20), _EVENT_TIME, "duckdb", "file"),
            (date(2026, 7, 21), datetime(2026, 7, 21, tzinfo=timezone.utc), UNKNOWN_KEY, "file"),
        ],
        jobs=[(datetime(2026, 7, 21, 1, tzinfo=timezone.utc),)],
        etl_rows=[
            ("succeeded", True, "succeeded", "orders", "advanced", "saved"),
            ("failed", True, "failed", "orders", "changed", "save_error"),
            ("skipped", True, "succeeded", "orders", "unchanged", "not_requested"),
        ],
        registry=[
            (_EVENT_TIME, "failed", "changed", True, 3, 8.0, 691200.0),
            (None, "succeeded", "unknown", False, 0, None, None),
        ],
    )

    assert summary["dataflow_records"] == 2
    assert summary["job_records"] == 1
    assert summary["active_engines"] == 1
    assert summary["active_metadata_providers"] == 1
    assert summary["successful_runs"] == 1
    assert summary["freshness_runs"] == 2
    assert summary["failed_runs"] == 1
    assert summary["skipped_runs"] == 1
    assert summary["observed_dataflows"] == 2
    assert summary["watermark_advanced_runs"] == 1
    assert summary["watermark_changed_runs"] == 1
    assert summary["watermark_advanced_rate"] == 33.33
    assert summary["watermark_rate_runs"] == 3
    assert summary["watermark_empty_runs"] == 0
    assert summary["watermark_coverage_rate"] == 50.0
    assert summary["p50_age_days"] == 8.0


def test_summary_returns_one_zero_row_for_empty_populations() -> None:
    summary = _summary()

    assert summary["dataflow_records"] == 0
    assert summary["job_records"] == 0
    assert summary["latest_log_at"] is None
    assert summary["successful_runs"] == 0
    assert summary["freshness_runs"] == 0
    assert summary["missing_dataflow_id_runs"] == 0
    assert summary["observed_dataflows"] == 0
    assert summary["watermark_coverage_rate"] is None
    assert summary["watermark_advanced_rate"] is None
    assert summary["stale_dataflow_rate"] is None
    assert summary["p50_age_days"] is None
    assert summary["p95_age_seconds"] is None


def test_summary_keeps_log_counts_when_non_etl_data_leaves_registry_empty() -> None:
    summary = _summary(
        dataflows=[(date(2026, 7, 20), _EVENT_TIME, "duckdb", "file")],
        jobs=[(datetime(2026, 7, 20, 1, tzinfo=timezone.utc),)],
    )

    assert summary["dataflow_records"] == 1
    assert summary["job_records"] == 1
    assert summary["date_min"] == date(2026, 7, 20)
    assert summary["date_max"] == date(2026, 7, 20)
    assert summary["latest_dataflow_log_at"] == _EVENT_TIME
    assert summary["latest_job_log_at"] == datetime(2026, 7, 20, 1, tzinfo=timezone.utc)
    assert summary["successful_runs"] == 0
    assert summary["observed_dataflows"] == 0
    assert summary["watermark_enabled_dataflows"] == 0
    assert summary["watermark_coverage_rate"] is None
    assert summary["min_age_days"] is None


def test_summary_keeps_etl_counts_for_missing_and_blank_dataflow_ids() -> None:
    summary = _summary(
        etl_rows=[
            ("succeeded", True, "succeeded", None, "advanced", "saved"),
            ("succeeded", True, "succeeded", "  ", "unchanged", "not_requested"),
        ],
    )

    assert summary["successful_runs"] == 2
    assert summary["freshness_runs"] == 2
    assert summary["missing_dataflow_id_runs"] == 2
    assert summary["watermark_advanced_runs"] == 1
    assert summary["watermark_unchanged_runs"] == 1
    assert summary["watermark_advanced_rate"] == 50.0
    assert summary["observed_dataflows"] == 0


@pytest.mark.parametrize(
    ("movement_states", "total_runs", "advanced_rate"),
    [
        (["advanced", "changed"], 2, 50.0),
        (["advanced", *(["empty"] * 9)], 10, 10.0),
        (["advanced", *(["unknown"] * 99)], 100, 1.0),
        (["empty"], 1, 0.0),
        (["initialized", "unknown", "not_configured"], 3, 0.0),
    ],
)
def test_summary_advanced_rate_uses_all_filtered_etl_runs(
    movement_states, total_runs, advanced_rate
) -> None:
    etl_rows = [
        (
            "succeeded",
            True,
            "succeeded",
            "flow",
            movement,
            "no_candidate" if movement == "empty" else "saved",
        )
        for movement in movement_states
    ]
    summary = _summary(etl_rows=etl_rows)

    assert summary["watermark_rate_runs"] == total_runs
    assert summary["watermark_advanced_rate"] == advanced_rate
    assert summary["watermark_empty_runs"] == movement_states.count("empty")
