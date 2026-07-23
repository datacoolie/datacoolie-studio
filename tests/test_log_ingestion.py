from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient


def _write_job_file(path: Path, job_ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "job_id": job_id,
            "status": "succeeded",
            "start_time": f"2026-07-22T0{index}:00:00+00:00",
            "end_time": f"2026-07-22T0{index}:01:00+00:00",
            "duration_seconds": 60,
        }
        for index, job_id in enumerate(job_ids)
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _write_dataflow_file(path: Path, run_ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        (
            run_id,
            f"job-{index}",
            f"flow-{index}",
            "succeeded",
            f"2026-07-22T0{index}:00:00+00:00",
            f"2026-07-22T0{index}:01:00+00:00",
            60.0,
        )
        for index, run_id in enumerate(run_ids)
    ]
    connection = duckdb.connect()
    try:
        connection.execute(
            """
            CREATE TABLE fixture (
                dataflow_run_id VARCHAR,
                job_id VARCHAR,
                dataflow_name VARCHAR,
                status VARCHAR,
                start_time TIMESTAMPTZ,
                end_time TIMESTAMPTZ,
                duration_seconds DOUBLE
            )
            """
        )
        connection.executemany("INSERT INTO fixture VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        escaped_path = str(path).replace("'", "''")
        connection.execute(f"COPY fixture TO '{escaped_path}' (FORMAT PARQUET)")
    finally:
        connection.close()


def _set_revision(path: Path, mtime_ns: int) -> None:
    os.utime(path, ns=(mtime_ns, mtime_ns))


def _create_workspace(client: TestClient, log_root: Path) -> tuple[int, int]:
    project = client.post("/api/v1/projects", json={"name": "ingestion-contract"}).json()
    environment = client.post(
        f"/api/v1/projects/{project['id']}/environments",
        json={"name": "dev"},
    ).json()
    source_response = client.post(
        f"/api/v1/environments/{environment['id']}/log-sources",
        json={"uri": str(log_root), "label": "partitioned logs"},
    )
    assert source_response.status_code == 200, source_response.text
    return int(environment["id"]), int(source_response.json()["id"])


def _refresh(
    client: TestClient,
    environment_id: int,
    source_id: int,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/environments/{environment_id}/log-sources/{source_id}/refresh",
        json=payload or {"mode": "incremental"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["latest_job"]["status"] == "succeeded", body
    assert body["latest_job"]["result"]["status"] == "ok", body["latest_job"]["result"]
    return body


def _raw_ids(analytics_path: Path, table: str, id_column: str) -> list[str]:
    connection = duckdb.connect(str(analytics_path), read_only=True)
    try:
        return [
            row[0]
            for row in connection.execute(
                f'SELECT "{id_column}" FROM "{table}" ORDER BY "{id_column}"'
            ).fetchall()
        ]
    finally:
        connection.close()


def _query_rows(analytics_path: Path, sql: str) -> list[tuple[object, ...]]:
    connection = duckdb.connect(str(analytics_path), read_only=True)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def _test_client(tmp_path: Path, monkeypatch):
    database_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(database_path))

    from datacoolie_studio.domains.logs import ingestion as log_ingestion
    from datacoolie_studio.main import app

    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    return TestClient(app), analytics_path


def test_incremental_replaces_same_job_and_dataflow_file_paths(tmp_path: Path, monkeypatch) -> None:
    log_root = tmp_path / "logs"
    job_file = log_root / "etl_logs" / "analyst" / "job_run_log" / "2026-07-22" / "jobs.jsonl"
    dataflow_file = (
        log_root
        / "etl_logs"
        / "analyst"
        / "dataflow_run_log"
        / "2026-07-22"
        / "dataflows.parquet"
    )
    initial_revision = 1_784_678_400_000_000_000
    _write_job_file(job_file, ["job-old"])
    _write_dataflow_file(dataflow_file, ["run-old"])
    _set_revision(job_file, initial_revision)
    _set_revision(dataflow_file, initial_revision)

    client, analytics_path = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, log_root)
        _refresh(client, environment_id, source_id)
        assert _raw_ids(analytics_path, "etl_job_runs", "job_id") == ["job-old"]
        assert _raw_ids(analytics_path, "etl_dataflow_runs", "dataflow_run_id") == ["run-old"]
        assert _query_rows(
            analytics_path,
            "SELECT log_kind, count(*) FROM log_ingest_checkpoint GROUP BY log_kind ORDER BY log_kind",
        ) == [("dataflow_parquet", 1), ("job_jsonl", 1)]

        _write_job_file(job_file, ["job-new-a", "job-new-b"])
        _write_dataflow_file(dataflow_file, ["run-new-a", "run-new-b"])
        _set_revision(job_file, initial_revision + 1_000_000_000)
        _set_revision(dataflow_file, initial_revision + 1_000_000_000)

        result = _refresh(client, environment_id, source_id)
        assert result["latest_job"]["result"]["record_counts"]["replaced_files"] == 2
        assert _raw_ids(analytics_path, "etl_job_runs", "job_id") == ["job-new-a", "job-new-b"]
        assert _raw_ids(analytics_path, "etl_dataflow_runs", "dataflow_run_id") == [
            "run-new-a",
            "run-new-b",
        ]
        assert _query_rows(
            analytics_path,
            "SELECT log_kind, row_count FROM log_ingest_file_manifest ORDER BY log_kind",
        ) == [("dataflow_parquet", 2), ("job_jsonl", 2)]


def test_incremental_does_not_delete_rows_when_source_files_disappear(tmp_path: Path, monkeypatch) -> None:
    log_root = tmp_path / "logs"
    job_file = log_root / "etl_logs" / "analyst" / "job_run_log" / "20260722" / "jobs.jsonl"
    dataflow_file = (
        log_root / "etl_logs" / "analyst" / "dataflow_run_log" / "20260722" / "dataflows.parquet"
    )
    _write_job_file(job_file, ["job-retained"])
    _write_dataflow_file(dataflow_file, ["run-retained"])

    client, analytics_path = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, log_root)
        _refresh(client, environment_id, source_id)
        job_file.unlink()
        dataflow_file.unlink()

        result = _refresh(client, environment_id, source_id)
        counts = result["latest_job"]["result"]["record_counts"]
        assert counts["removed_files"] == 0
        assert _raw_ids(analytics_path, "etl_job_runs", "job_id") == ["job-retained"]
        assert _raw_ids(analytics_path, "etl_dataflow_runs", "dataflow_run_id") == ["run-retained"]


def test_invalid_changed_file_fails_without_advancing_published_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_root = tmp_path / "logs"
    job_file = log_root / "etl_logs" / "analyst" / "job_run_log" / "2026-07-22" / "jobs.jsonl"
    initial_revision = 1_784_678_400_000_000_000
    _write_job_file(job_file, ["job-retained"])
    _set_revision(job_file, initial_revision)

    client, analytics_path = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, log_root)
        _refresh(client, environment_id, source_id)
        before = _query_rows(
            analytics_path,
            """
            SELECT
              (SELECT generation FROM etl_analytics_meta WHERE singleton_id = 1),
              (SELECT generation FROM etl_cache_sources WHERE source_id = 1),
              (SELECT count(*) FROM log_ingest_file_manifest WHERE source_id = 1),
              (SELECT max(boundary_last_modified) FROM log_ingest_checkpoint WHERE source_id = 1)
            """,
        )

        job_file.write_text('{"job_id": "broken"\n', encoding="utf-8")
        _set_revision(job_file, initial_revision + 1_000_000_000)
        response = client.post(
            f"/api/v1/environments/{environment_id}/log-sources/{source_id}/refresh",
            json={"mode": "incremental"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "error"
        assert body["latest_job"]["status"] == "failed"
        assert body["latest_job"]["result"]["error"]["code"] == "invalid_jsonl"
        assert _raw_ids(analytics_path, "etl_job_runs", "job_id") == ["job-retained"]
        assert _query_rows(
            analytics_path,
            """
            SELECT
              (SELECT generation FROM etl_analytics_meta WHERE singleton_id = 1),
              (SELECT generation FROM etl_cache_sources WHERE source_id = 1),
              (SELECT count(*) FROM log_ingest_file_manifest WHERE source_id = 1),
              (SELECT max(boundary_last_modified) FROM log_ingest_checkpoint WHERE source_id = 1)
            """,
        ) == before


def test_incremental_refresh_does_not_use_legacy_recursive_source_scans(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_root = tmp_path / "logs"
    job_file = log_root / "etl_logs" / "analyst" / "job_run_log" / "2026-07-22" / "jobs.jsonl"
    _write_job_file(job_file, ["job-bounded-discovery"])

    client, _ = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, log_root)

        from datacoolie_studio.domains.logs import ingestion as log_ingestion

        def fail_legacy_scan(*_args, **_kwargs):
            raise AssertionError("incremental refresh must not invoke a legacy recursive source scan")

        monkeypatch.setattr(log_ingestion.sync, "stat_source", fail_legacy_scan)
        monkeypatch.setattr(log_ingestion.source_validation, "validate_log_source", fail_legacy_scan)
        _refresh(client, environment_id, source_id)


def test_historical_change_requires_explicit_lookback(tmp_path: Path, monkeypatch) -> None:
    log_root = tmp_path / "logs"
    old_job_file = log_root / "etl_logs" / "analyst" / "job_run_log" / "2026_07_21" / "jobs.jsonl"
    current_job_file = log_root / "etl_logs" / "analyst" / "job_run_log" / "2026_07_22" / "jobs.jsonl"
    old_dataflow_file = (
        log_root
        / "etl_logs"
        / "analyst"
        / "dataflow_run_log"
        / "2026_07_21"
        / "dataflows.parquet"
    )
    current_dataflow_file = (
        log_root
        / "etl_logs"
        / "analyst"
        / "dataflow_run_log"
        / "2026_07_22"
        / "dataflows.parquet"
    )
    initial_revision = 1_784_592_000_000_000_000
    current_revision = initial_revision + 86_400_000_000_000
    _write_job_file(old_job_file, ["job-old-version"])
    _write_job_file(current_job_file, ["job-current"])
    _write_dataflow_file(old_dataflow_file, ["run-old-version"])
    _write_dataflow_file(current_dataflow_file, ["run-current"])
    for path in (old_job_file, old_dataflow_file):
        _set_revision(path, initial_revision)
    for path in (current_job_file, current_dataflow_file):
        _set_revision(path, current_revision)

    client, analytics_path = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, log_root)
        _refresh(client, environment_id, source_id)

        _write_job_file(old_job_file, ["job-lookback-version"])
        _write_dataflow_file(old_dataflow_file, ["run-lookback-version"])
        _set_revision(old_job_file, current_revision + 1_000_000_000)
        _set_revision(old_dataflow_file, current_revision + 1_000_000_000)

        _refresh(client, environment_id, source_id)
        assert _raw_ids(analytics_path, "etl_job_runs", "job_id") == [
            "job-current",
            "job-old-version",
        ]
        assert _raw_ids(analytics_path, "etl_dataflow_runs", "dataflow_run_id") == [
            "run-current",
            "run-old-version",
        ]

        _refresh(
            client,
            environment_id,
            source_id,
            {
                "mode": "incremental_with_lookback",
                "lookback": {
                    "from_partition": "2026-07-21",
                    "to_partition": "2026-07-21",
                },
            },
        )
        assert _raw_ids(analytics_path, "etl_job_runs", "job_id") == [
            "job-current",
            "job-lookback-version",
        ]
        assert _raw_ids(analytics_path, "etl_dataflow_runs", "dataflow_run_id") == [
            "run-current",
            "run-lookback-version",
        ]

        # Lookback repairs history but must not move the incremental partition backward.
        _write_job_file(old_job_file, ["job-after-lookback"])
        _write_dataflow_file(old_dataflow_file, ["run-after-lookback"])
        _set_revision(old_job_file, current_revision + 2_000_000_000)
        _set_revision(old_dataflow_file, current_revision + 2_000_000_000)
        _refresh(client, environment_id, source_id)
        assert _raw_ids(analytics_path, "etl_job_runs", "job_id") == [
            "job-current",
            "job-lookback-version",
        ]
        assert _raw_ids(analytics_path, "etl_dataflow_runs", "dataflow_run_id") == [
            "run-current",
            "run-lookback-version",
        ]


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "incremental_with_lookback"},
        {
            "mode": "incremental_with_lookback",
            "lookback": {"from_partition": "2026-07-22", "to_partition": "2026-07-21"},
        },
        {
            "mode": "incremental_with_lookback",
            "lookback": {"from_partition": "20260721", "to_partition": "2026-07-22"},
        },
        {
            "mode": "incremental",
            "lookback": {"from_partition": "2026-07-21", "to_partition": "2026-07-22"},
        },
    ],
    ids=["missing-lookback", "reversed-range", "invalid-date", "unexpected-lookback"],
)
def test_log_refresh_rejects_invalid_sync_specs(
    tmp_path: Path,
    monkeypatch,
    payload: dict[str, object],
) -> None:
    log_root = tmp_path / "logs"
    job_file = log_root / "etl_logs" / "analyst" / "job_run_log" / "2026-07-22" / "jobs.jsonl"
    _write_job_file(job_file, ["job-validation"])

    client, _ = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, log_root)
        response = client.post(
            f"/api/v1/environments/{environment_id}/log-sources/{source_id}/refresh",
            json=payload,
        )

    assert response.status_code == 422, response.text
