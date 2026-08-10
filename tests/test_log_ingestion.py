from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
from fastapi.testclient import TestClient


def test_cache_rebuild_keeps_fresh_planned_revision_over_stale_manifest() -> None:
    from datacoolie_studio.domains.logs.ingestion import (
        _merge_rebuild_candidates,
    )
    from datacoolie_studio.domains.storage.adapters import StorageRevision

    uri = "s3://bucket/logs/jobs.jsonl"
    fresh_revision = StorageRevision(
        canonical_uri=uri,
        size=21,
        last_modified=datetime(2026, 8, 2, tzinfo=timezone.utc),
        provider_revision="etag-fresh",
    )
    planned = SimpleNamespace(
        canonical_uri=uri,
        revision=fresh_revision,
        partition=SimpleNamespace(partition_value=datetime(2026, 8, 2).date()),
    )
    manifest = SimpleNamespace(
        file_kind="job_jsonl",
        file_uri=uri,
        revision_json=json.dumps(
            {
                "size": 17,
                "last_modified": "2026-07-22T00:00:00+00:00",
                "provider_revision": "etag-stale",
            }
        ),
        partition_value=None,
        run_date=None,
        partition_format=None,
    )

    class Adapter:
        def stat(self, _uri: str):
            raise AssertionError("a currently planned object must not be restatted")

    candidates = _merge_rebuild_candidates(
        Adapter(),
        [(planned, "job_jsonl")],
        [manifest],
        {},
    )

    assert len(candidates) == 1
    assert candidates[0][0].revision == fresh_revision


def test_cache_rebuild_reuses_manifest_revisions_without_provider_stat() -> None:
    from datacoolie_studio.domains.logs.ingestion import (
        _merge_rebuild_candidates,
    )

    revision_json = json.dumps(
        {
            "size": 17,
            "last_modified": "2026-07-22T00:00:00+00:00",
            "provider_revision": "revision-7",
        }
    )
    manifest = SimpleNamespace(
        file_kind="job_jsonl",
        file_uri="dbfs:/Volumes/catalog/schema/volume/jobs.jsonl",
        revision_json=revision_json,
        partition_value=None,
        run_date=None,
        partition_format=None,
    )

    class Adapter:
        def stat(self, _uri: str):
            raise AssertionError("valid manifest revisions must avoid provider stat")

    candidates = _merge_rebuild_candidates(
        Adapter(),
        [],
        [manifest],
        {},
    )

    assert len(candidates) == 1
    assert candidates[0][0].revision.provider_revision == "revision-7"


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


def test_dataflow_reader_does_not_project_hive_path_columns(tmp_path: Path) -> None:
    from datacoolie_studio.domains.logs.ingestion import _read_dataflow_file

    path = (
        tmp_path
        / "__run_date=2026-08-09"
        / "__run_hour=10"
        / "dataflow.parquet"
    )
    _write_dataflow_file(path, ["run-1"])

    rows, errors = _read_dataflow_file(str(path))

    assert errors == []
    assert len(rows) == 1
    assert "__run_date" not in rows[0]
    assert "__run_hour" not in rows[0]


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
    environment_id = int(environment["id"])
    source_id = int(source_response.json()["id"])
    status: dict[str, object] = {}
    for _ in range(100):
        status = _source_status(client, environment_id, source_id)
        latest_job = status.get("latest_job") or {}
        if (
            status.get("active_operation") is None
            and latest_job.get("job_type") == "initial_refresh"
            and latest_job.get("status") == "succeeded"
        ):
            break
        time.sleep(0.02)
    assert status.get("active_operation") is None, status
    return environment_id, source_id


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


def _source_status(
    client: TestClient,
    environment_id: int,
    source_id: int,
) -> dict[str, object]:
    response = client.get(f"/api/v1/environments/{environment_id}/sources/workspace")
    assert response.status_code == 200, response.text
    return next(
        item
        for item in response.json()["statuses"]
        if int(item["source_id"]) == source_id
    )


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


def _workspace_rows(database_path: Path, sql: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(database_path) as connection:
        return connection.execute(sql).fetchall()


def _test_client(tmp_path: Path, monkeypatch):
    database_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(database_path))

    from datacoolie_studio.domains.logs import ingestion as log_ingestion
    from datacoolie_studio.main import app

    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    return TestClient(app), analytics_path


def test_dataflow_parquet_explicit_event_time_precedes_end_and_start(tmp_path: Path) -> None:
    parquet_path = tmp_path / "explicit-event.parquet"
    source = duckdb.connect()
    try:
        source.execute(
            """
            CREATE TABLE fixture (
              dataflow_run_id VARCHAR,
              __event_time VARCHAR,
              start_time TIMESTAMPTZ,
              end_time TIMESTAMPTZ
            )
            """
        )
        source.execute(
            """
            INSERT INTO fixture VALUES (
              'run-explicit',
              '2026-07-22T03:00:00Z',
              '2026-07-22T01:00:00Z',
              '2026-07-22T02:00:00Z'
            )
            """
        )
        escaped_path = str(parquet_path).replace("'", "''")
        source.execute(f"COPY fixture TO '{escaped_path}' (FORMAT PARQUET)")
    finally:
        source.close()

    from datacoolie_studio.domains.analytics import schema as analytics_schema
    from datacoolie_studio.domains.analytics import store as analytics_store

    target = duckdb.connect()
    try:
        analytics_store.ensure_tables(target)
        analytics_store.insert_dataflow_file(
            target,
            1,
            str(parquet_path),
            "dataflow_parquet",
            "{}",
        )
        assert target.execute(
            f"""
            SELECT
              typeof(__event_time),
              epoch(__event_time)
            FROM {analytics_schema.DATAFLOW_TABLE}
            """
        ).fetchone() == ("TIMESTAMP WITH TIME ZONE", 1784689200.0)
    finally:
        target.close()


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
        # A redundant no-op refresh must not make the active checkpoint
        # partition invisible to the next incremental run.
        _refresh(client, environment_id, source_id)
        assert _raw_ids(analytics_path, "etl_job_runs", "job_id") == ["job-old"]
        assert _raw_ids(analytics_path, "etl_dataflow_runs", "dataflow_run_id") == ["run-old"]
        assert _query_rows(
            analytics_path,
            """
            SELECT
              d.dataflow_run_id,
              d.__event_time = d.end_time AS raw_event_time_matches,
              f.event_time = d.__event_time AS serving_event_time_matches
            FROM etl_dataflow_runs d
            JOIN monitoring_dataflow_facts f
              ON f._source_id = d._source_id
             AND f.dataflow_run_id = d.dataflow_run_id
            """,
        ) == [("run-old", True, True)]
        assert _workspace_rows(
            tmp_path / "studio.db",
            """
            SELECT stream_kind, count(*)
            FROM log_stream_states
            WHERE stream_kind IN ('dataflow_parquet', 'job_jsonl')
            GROUP BY stream_kind
            ORDER BY stream_kind
            """,
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
        assert _workspace_rows(
            tmp_path / "studio.db",
            """
            SELECT file_kind, row_count
            FROM log_file_manifest
            WHERE file_kind IN ('dataflow_parquet', 'job_jsonl')
            ORDER BY file_kind
            """,
        ) == [("dataflow_parquet", 2), ("job_jsonl", 2)]


def test_hourly_incremental_ingestion_advances_exact_checkpoint_and_is_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_root = tmp_path / "logs"
    current_hour = datetime.now(timezone.utc).replace(
        minute=0,
        second=0,
        microsecond=0,
        tzinfo=None,
    )
    previous_hour = current_hour - timedelta(hours=1)

    def hourly_path(stream: str, value: datetime, filename: str) -> Path:
        return (
            log_root
            / "etl_logs"
            / "analyst"
            / stream
            / f"__run_date={value:%Y-%m-%d}"
            / f"__run_hour={value:%H}"
            / filename
        )

    old_job = hourly_path("job_run_log", previous_hour, "job-old.jsonl")
    old_dataflow = hourly_path("dataflow_run_log", previous_hour, "dataflow-old.parquet")
    _write_job_file(old_job, ["job-hour-old"])
    _write_dataflow_file(old_dataflow, ["run-hour-old"])

    client, analytics_path = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, log_root)
        _refresh(client, environment_id, source_id)

        new_job = hourly_path("job_run_log", current_hour, "job-new.jsonl")
        new_dataflow = hourly_path("dataflow_run_log", current_hour, "dataflow-new.parquet")
        _write_job_file(new_job, ["job-hour-new"])
        _write_dataflow_file(new_dataflow, ["run-hour-new"])

        result = _refresh(client, environment_id, source_id)
        assert result["latest_job"]["result"]["record_counts"]["parsed_files"] == 2
        assert _raw_ids(analytics_path, "etl_job_runs", "job_id") == [
            "job-hour-new",
            "job-hour-old",
        ]
        assert _raw_ids(analytics_path, "etl_dataflow_runs", "dataflow_run_id") == [
            "run-hour-new",
            "run-hour-old",
        ]
        with duckdb.connect(str(analytics_path), read_only=True) as connection:
            cached_run_dates = {
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT __run_date FROM etl_dataflow_runs"
                ).fetchall()
            }
        assert cached_run_dates == {
            previous_hour.date().isoformat(),
            current_hour.date().isoformat(),
        }
        assert _workspace_rows(
            tmp_path / "studio.db",
            """
            SELECT
              stream_kind,
              partition_granularity,
              checkpoint_partition_key,
              last_scanned_partition_key
            FROM log_stream_states
            WHERE stream_kind IN ('dataflow_parquet', 'job_jsonl')
            ORDER BY stream_kind
            """,
        ) == [
            (
                "dataflow_parquet",
                "hour",
                current_hour.isoformat(timespec="seconds"),
                current_hour.isoformat(timespec="seconds"),
            ),
            (
                "job_jsonl",
                "hour",
                current_hour.isoformat(timespec="seconds"),
                current_hour.isoformat(timespec="seconds"),
            ),
        ]

        noop = _refresh(client, environment_id, source_id)
        assert noop["latest_job"]["result"]["record_counts"]["parsed_files"] == 0


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
        before_analytics = _query_rows(
            analytics_path,
            """
            SELECT
              (SELECT generation FROM etl_analytics_meta WHERE singleton_id = 1),
              (SELECT generation FROM etl_cache_sources WHERE source_id = 1)
            """,
        )
        before_control = _workspace_rows(
            tmp_path / "studio.db",
            """
            SELECT
              (SELECT count(*) FROM log_file_manifest WHERE source_id = 1),
              (SELECT max(boundary_last_modified)
               FROM log_stream_states WHERE source_id = 1)
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
              (SELECT generation FROM etl_cache_sources WHERE source_id = 1)
            """,
        ) == before_analytics
        assert _workspace_rows(
            tmp_path / "studio.db",
            """
            SELECT
              (SELECT count(*) FROM log_file_manifest WHERE source_id = 1),
              (SELECT max(boundary_last_modified)
               FROM log_stream_states WHERE source_id = 1)
            """,
        ) == before_control


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
        monkeypatch.setattr(log_ingestion.source_validation, "record_source_validation", fail_legacy_scan)
        _refresh(client, environment_id, source_id)


def test_log_refresh_records_real_completion_time_and_phase_timings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_root = tmp_path / "logs"
    job_file = (
        log_root
        / "etl_logs"
        / "analyst"
        / "job_run_log"
        / "2026-07-22"
        / "jobs.jsonl"
    )
    _write_job_file(job_file, ["job-timing"])

    client, _ = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, log_root)
        result = _refresh(client, environment_id, source_id)

    job = result["latest_job"]
    assert datetime.fromisoformat(job["completed_at"]) >= datetime.fromisoformat(
        job["started_at"]
    )
    timings = job["result"]["timings_ms"]
    assert {
        "adapter_init",
        "planning",
        "materialization",
        "parsing",
        "publish",
        "control_commit",
        "total",
    } <= timings.keys()
    assert all(value >= 0 for value in timings.values())
    assert timings["total"] >= timings["materialization"]


def test_materialization_failure_does_not_publish_or_advance_control_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_root = tmp_path / "logs"
    job_file = (
        log_root
        / "etl_logs"
        / "analyst"
        / "job_run_log"
        / "2026-07-22"
        / "jobs.jsonl"
    )
    _write_job_file(job_file, ["job-must-not-publish"])

    client, _ = _test_client(tmp_path, monkeypatch)
    from datacoolie_studio.domains.logs import ingestion as log_ingestion

    def fail_materialization(*_args, **_kwargs):
        raise OSError("simulated materialization failure")

    def fail_publish(*_args, **_kwargs):
        raise AssertionError("publish must not run after materialization failure")

    monkeypatch.setattr(log_ingestion, "map_storage_io", fail_materialization)
    monkeypatch.setattr(log_ingestion.analytics_store, "publish_rows", fail_publish)
    with client:
        environment_id, source_id = _create_workspace(client, log_root)
        response = client.post(
            f"/api/v1/environments/{environment_id}/log-sources/{source_id}/refresh",
            json={"mode": "incremental"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["latest_job"]["status"] == "failed"
    assert body["latest_job"]["result"]["timings_ms"]["total"] >= 0
    assert _workspace_rows(
        tmp_path / "studio.db",
        """
        SELECT
          (SELECT count(*) FROM log_file_manifest WHERE source_id = 1),
          (SELECT count(*) FROM log_stream_states WHERE source_id = 1)
        """,
    ) == [(0, 0)]


def test_system_sync_is_manifest_only_and_uses_learned_exact_partitions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_root = tmp_path / "logs"
    system_file = (
        log_root
        / "system_logs"
        / "__run_date=2026-07-22"
        / "system_log_20260722_010203_job-system.jsonl"
    )
    system_file.parent.mkdir(parents=True)
    system_file.write_text('{"message": "must not be read during sync"}\n', encoding="utf-8")

    from datacoolie_studio.domains.storage.adapters import LocalStorageAdapter

    def fail_content_read(*_args, **_kwargs):
        raise AssertionError("System sync must not read file contents")

    monkeypatch.setattr(LocalStorageAdapter, "open_read", fail_content_read)
    client, _ = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, log_root)
        first = _source_status(client, environment_id, source_id)
        assert first["latest_job"]["result"]["record_counts"][
            "system_jsonl_files"
        ] == 1
        assert _workspace_rows(
            tmp_path / "studio.db",
            """
            SELECT file_kind, row_count, job_id, partition_value
            FROM log_file_manifest
            WHERE source_id = 1 AND file_kind = 'system_jsonl'
            """,
        ) == [
            ("system_jsonl", 0, "job-system", "2026-07-22"),
        ]
        assert _workspace_rows(
            tmp_path / "studio.db",
            """
            SELECT layout_status, partition_format, partition_granularity
            FROM log_stream_states
            WHERE source_id = 1 AND stream_kind = 'system_jsonl'
            """,
        ) == [
            ("learned", "__run_date=%Y-%m-%d", "day"),
        ]

        original_inventory = LocalStorageAdapter.inventory

        def fail_system_tree_discovery(adapter, request):
            if (
                request.object_types == frozenset({"directory"})
                and "system_logs" in str(request.uri)
            ):
                raise AssertionError(
                    "Learned System sync must not discover partition children"
                )
            return original_inventory(adapter, request)

        monkeypatch.setattr(
            LocalStorageAdapter,
            "inventory",
            fail_system_tree_discovery,
        )
        second = _refresh(client, environment_id, source_id)
        assert second["latest_job"]["result"]["record_counts"][
            "system_jsonl_files"
        ] == 0


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
