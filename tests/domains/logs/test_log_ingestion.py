from __future__ import annotations

from tests.support.v6_records import native_record

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
from fastapi.testclient import TestClient
from tests.support.eventual import wait_for


def test_cache_rebuild_keeps_fresh_planned_revision_over_stale_manifest() -> None:
    from datacoolie_studio.domains.logs.file_state import merge_rebuild_candidates
    from datacoolie_studio.domains.storage.adapters import StorageRevision

    uri = "s3://bucket/logs/jobs.json"
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
        file_kind="job_json",
        file_uri=uri,
        revision_json=json.dumps(
            {
                "size": 17,
                "mtime_ns": 1784678400000000000,
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

    candidates = merge_rebuild_candidates(
        Adapter(),
        [(planned, "job_json")],
        [manifest],
        {},
    )

    assert len(candidates) == 1
    assert candidates[0][0].revision == fresh_revision


def test_cache_rebuild_reuses_manifest_revisions_without_provider_stat() -> None:
    from datacoolie_studio.domains.logs.file_state import merge_rebuild_candidates

    revision_json = json.dumps(
        {
            "size": 17,
            "last_modified": "2026-07-22T00:00:00+00:00",
            "provider_revision": "revision-7",
        }
    )
    manifest = SimpleNamespace(
        file_kind="job_json",
        file_uri="dbfs:/Volumes/catalog/schema/volume/jobs.json",
        revision_json=revision_json,
        partition_value=None,
        run_date=None,
        partition_format=None,
    )

    class Adapter:
        def stat(self, _uri: str):
            raise AssertionError("valid manifest revisions must avoid provider stat")

    candidates = merge_rebuild_candidates(
        Adapter(),
        [],
        [manifest],
        {},
    )

    assert len(candidates) == 1
    assert candidates[0][0].revision.provider_revision == "revision-7"


def test_revision_json_uses_remote_modified_time_not_millisecond_provider_token() -> None:
    from datacoolie_studio.domains.logs.file_state import revision_json
    from datacoolie_studio.domains.storage.adapters import StorageRevision

    modified = datetime(2026, 9, 1, 12, 30, tzinfo=timezone.utc)
    revision = StorageRevision(
        canonical_uri="dbfs:/Volumes/catalog/schema/volume/jobs.json",
        size=17,
        last_modified=modified,
        provider_revision=f"{int(modified.timestamp() * 1000)}:17",
    )

    payload = json.loads(revision_json(revision))

    assert payload["mtime_ns"] == int(modified.timestamp() * 1_000_000_000)


def _write_job_file(path: Path, job_ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        native_record({
            "log_schema_version": 3,
            "_type": "job_run_log",
            "job_id": job_id,
            "status": "succeeded",
            "start_time": f"2026-07-22T0{index}:00:00+00:00",
            "end_time": f"2026-07-22T0{index}:01:00+00:00",
            "duration_seconds": 60,
        })
        for index, job_id in enumerate(job_ids)
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _write_dataflow_file(path: Path, run_ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        native_record({
            "log_schema_version": 3,
            "_type": "dataflow_run_log",
            "dataflow_run_id": run_id,
            "job_id": f"job-{index}",
            "dataflow_name": f"flow-{index}",
            "status": "succeeded",
            "start_time": f"2026-07-22T0{index}:00:00+00:00",
            "end_time": f"2026-07-22T0{index}:01:00+00:00",
            "duration_seconds": 60.0,
        })
        for index, run_id in enumerate(run_ids)
    ]
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_dataflow_reader_does_not_project_hive_path_columns(tmp_path: Path) -> None:
    from datacoolie_studio.domains.analytics import store
    from datacoolie_studio.domains.analytics.contracts import LogFileDescriptor

    path = (
        tmp_path
        / "__run_date=2026-08-09"
        / "__run_hour=10"
        / "dataflow.json"
    )
    _write_dataflow_file(path, ["run-1"])

    analytics_path = tmp_path / "analytics.duckdb"
    result = store.publish_rows(
        1,
        [
            LogFileDescriptor(
                canonical_uri=str(path),
                file_kind="dataflow_json",
                revision_json=json.dumps(
                    {"size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
                ),
                read_uri=str(path),
                partition_date="2026-08-09",
            )
        ],
        [],
        [],
        [str(path)],
        database_path=analytics_path,
    )

    assert result["published"] is True
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        columns = {
            row[0] for row in connection.execute("DESCRIBE etl_dataflow_runs").fetchall()
        }
    assert "__run_date" in columns
    assert "__run_hour" not in columns


def test_batched_publish_detaches_and_cleans_each_raw_batch(tmp_path: Path) -> None:
    from datacoolie_studio.domains.analytics import contracts, store

    descriptors = []
    raw_paths = []
    for index in range(2):
        raw_path = tmp_path / f"raw-{index}.json"
        _write_dataflow_file(raw_path, [f"run-{index}"])
        canonical_uri = f"s3://bucket/logs/raw-{index}.json"
        descriptor = contracts.LogFileDescriptor(
            canonical_uri=canonical_uri,
            file_kind="dataflow_json",
            revision_json=json.dumps(
                {"size": raw_path.stat().st_size, "mtime_ns": 1}
            ),
            read_uri=str(raw_path),
            partition_date="2026-07-22",
        )
        descriptors.append(descriptor)
        raw_paths.append(raw_path)

    def batches():
        for descriptor in descriptors:
            try:
                yield contracts.LogPublishBatch(
                    dataflow_files=(descriptor,),
                    source_files=(
                        {
                            "file_uri": descriptor.canonical_uri,
                            "revision_json": descriptor.revision_json,
                        },
                    ),
                )
            finally:
                Path(descriptor.read_uri).unlink(missing_ok=True)

    detached = []
    result = store.publish_rows_batched(
        9,
        batches(),
        [],
        [descriptor.canonical_uri for descriptor in descriptors],
        database_path=tmp_path / "analytics.duckdb",
        on_batch_detached=lambda batch: detached.append(batch.dataflow_files[0].canonical_uri),
    )

    assert result["published"] is True
    assert result["parsed_dataflow_records"] == 2
    assert detached == [descriptor.canonical_uri for descriptor in descriptors]
    assert all(not path.exists() for path in raw_paths)
    with duckdb.connect(str(tmp_path / "analytics.duckdb"), read_only=True) as connection:
        assert connection.execute(
            "SELECT count(*) FROM etl_dataflow_runs WHERE _source_id = 9"
        ).fetchone()[0] == 2


def test_batched_publish_failure_preserves_live_rows_and_cleans_raw_batch(
    tmp_path: Path,
) -> None:
    from datacoolie_studio.domains.analytics import contracts, store

    baseline = tmp_path / "baseline.json"
    _write_dataflow_file(baseline, ["run-baseline"])
    analytics_path = tmp_path / "analytics.duckdb"
    baseline_descriptor = contracts.LogFileDescriptor(
        canonical_uri="s3://bucket/logs/baseline.json",
        file_kind="dataflow_json",
        revision_json=json.dumps({"size": baseline.stat().st_size, "mtime_ns": 1}),
        read_uri=str(baseline),
        partition_date="2026-07-22",
    )
    assert store.publish_rows(
        10,
        [baseline_descriptor],
        [],
        [],
        [baseline_descriptor.canonical_uri],
        database_path=analytics_path,
    )["published"]

    replacement = tmp_path / "replacement.json"
    _write_dataflow_file(replacement, ["run-replacement"])
    malformed = tmp_path / "malformed.json"
    malformed.write_text("not-json\n", encoding="utf-8")
    replacement_descriptor = contracts.LogFileDescriptor(
        canonical_uri="s3://bucket/logs/replacement.json",
        file_kind="dataflow_json",
        revision_json=json.dumps(
            {"size": replacement.stat().st_size, "mtime_ns": 2}
        ),
        read_uri=str(replacement),
        partition_date="2026-07-22",
    )
    malformed_descriptor = contracts.LogFileDescriptor(
        canonical_uri="s3://bucket/logs/malformed.json",
        file_kind="dataflow_json",
        revision_json=json.dumps({"size": malformed.stat().st_size, "mtime_ns": 2}),
        read_uri=str(malformed),
        partition_date="2026-07-22",
    )

    def batches():
        for descriptor in (replacement_descriptor, malformed_descriptor):
            try:
                yield contracts.LogPublishBatch(
                    dataflow_files=(descriptor,),
                    source_files=(
                        {
                            "file_uri": descriptor.canonical_uri,
                            "revision_json": descriptor.revision_json,
                        },
                    ),
                )
            finally:
                Path(descriptor.read_uri).unlink(missing_ok=True)

    result = store.publish_rows_batched(
        10,
        batches(),
        [],
        [replacement_descriptor.canonical_uri, malformed_descriptor.canonical_uri],
        database_path=analytics_path,
    )

    assert result["published"] is False
    assert not replacement.exists()
    assert not malformed.exists()
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        assert connection.execute(
            "SELECT dataflow_run_id FROM etl_dataflow_runs WHERE _source_id = 10"
        ).fetchall() == [("run-baseline",)]


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
    status = wait_for(
        lambda: _source_status(client, environment_id, source_id),
        lambda observed: (observed.get("sync_execution") or {}).get("state")
        in {"succeeded", "failed"},
        description="initial log source synchronization",
    )
    assert status.get("sync_execution", {}).get("state") != "running", status
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
    assert body["sync_execution"]["state"] == "succeeded", body
    assert body["sync_execution"]["result"]["status"] == "ok", body["sync_execution"]["result"]
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


def test_refresh_reports_missing_local_log_root_and_preserves_cache(
    tmp_path: Path, monkeypatch
    ) -> None:
    log_root = tmp_path / "logs"
    current_partition = datetime.now(timezone.utc).date().isoformat()
    job_file = log_root / "execution_logs" / "job_run_log" / current_partition / "jobs.json"
    _write_job_file(job_file, ["job-retained"])

    client, analytics_path = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, log_root)
        assert _raw_ids(analytics_path, "etl_job_runs", "job_id") == ["job-retained"]

        log_root.rename(tmp_path / "logs-moved")
        response = client.post(
            f"/api/v1/environments/{environment_id}/log-sources/{source_id}/refresh",
            json={"mode": "incremental"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "error"
        assert body["observation"]["error"]["code"] == "not_found"
        assert body["sync_execution"]["state"] == "failed"
        assert _raw_ids(analytics_path, "etl_job_runs", "job_id") == ["job-retained"]


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
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(database_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{database_path.as_posix()}")

    from datacoolie_studio.domains.logs import ingestion as log_ingestion
    from datacoolie_studio.main import app

    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    return TestClient(app), analytics_path


def test_add_log_source_with_naive_watermark_completes_initialization(tmp_path, monkeypatch):
    log_root = tmp_path / 'logs'
    partition = datetime.now(timezone.utc).date().isoformat()
    path = log_root / 'execution_logs' / 'dataflow_run_log' / partition / 'naive.json'
    path.parent.mkdir(parents=True)
    row = native_record({'dataflow_run_id': 'naive-watermark'})
    row['attempts'][0]['source']['selection']['lower'] = {
        'event_date': {'__datetime__': '2024-01-02T10:05:00'},
    }
    path.write_text(json.dumps(row), encoding='utf-8')
    client, analytics_path = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, log_root)
        status = _source_status(client, environment_id, source_id)
        assert status['sync_execution']['state'] == 'succeeded', status
        assert _raw_ids(analytics_path, 'etl_dataflow_runs', 'dataflow_run_id') == ['naive-watermark']
        assert _query_rows(analytics_path, 'SELECT count(*) FROM monitoring_dataflow_facts') == [(1,)]


def test_dataflow_json_event_time_uses_root_end_time(tmp_path: Path) -> None:
    json_path = tmp_path / "explicit-event.json"
    json_path.write_text(
        json.dumps(
            native_record({
                "log_schema_version": 3,
                "_type": "dataflow_run_log",
                "dataflow_run_id": "run-explicit",
                "job_id": "job-explicit",
                "start_time": "2026-07-22T01:00:00Z",
                "end_time": "2026-07-22T02:00:00Z",
            })
        )
        + "\n",
        encoding="utf-8",
    )

    from datacoolie_studio.domains.analytics import schema as analytics_schema
    from datacoolie_studio.domains.analytics import schema_tables as analytics_schema_tables
    from datacoolie_studio.domains.analytics import store as analytics_store

    target = duckdb.connect()
    try:
        target.execute("SET TimeZone = 'UTC'")
        analytics_schema_tables.ensure_tables(target)
        analytics_store.insert_dataflow_file(
            target,
            1,
            str(json_path),
            "dataflow_json",
            "{}",
        )
        assert target.execute(
            f"""
            SELECT
              typeof(__event_time),
              epoch(__event_time)
            FROM {analytics_schema.DATAFLOW_TABLE}
            """
        ).fetchone() == ("TIMESTAMP WITH TIME ZONE", 1784685600.0)
    finally:
        target.close()


def test_incremental_replaces_same_job_and_dataflow_file_paths(tmp_path: Path, monkeypatch) -> None:
    log_root = tmp_path / "logs"
    current_partition = datetime.now(timezone.utc).date().isoformat()
    job_file = log_root / "execution_logs" / "job_run_log" / current_partition / "jobs.json"
    dataflow_file = (
        log_root
        / "execution_logs"
        / "dataflow_run_log"
        / current_partition
        / "dataflows.json"
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
            WHERE stream_kind IN ('dataflow_json', 'job_json')
            GROUP BY stream_kind
            ORDER BY stream_kind
            """,
        ) == [("dataflow_json", 1), ("job_json", 1)]

        _write_job_file(job_file, ["job-new-a", "job-new-b"])
        _write_dataflow_file(dataflow_file, ["run-new-a", "run-new-b"])
        _set_revision(job_file, initial_revision + 1_000_000_000)
        _set_revision(dataflow_file, initial_revision + 1_000_000_000)

        result = _refresh(client, environment_id, source_id)
        assert result["sync_execution"]["result"]["record_counts"]["replaced_files"] == 2
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
            WHERE file_kind IN ('dataflow_json', 'job_json')
            ORDER BY file_kind
            """,
        ) == [("dataflow_json", 2), ("job_json", 2)]


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
            / "execution_logs"
            / stream
            / f"__run_date={value:%Y-%m-%d}"
            / f"__run_hour={value:%H}"
            / filename
        )

    old_job = hourly_path("job_run_log", previous_hour, "job-old.json")
    old_dataflow = hourly_path("dataflow_run_log", previous_hour, "dataflow-old.json")
    _write_job_file(old_job, ["job-hour-old"])
    _write_dataflow_file(old_dataflow, ["run-hour-old"])

    client, analytics_path = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, log_root)
        _refresh(client, environment_id, source_id)

        new_job = hourly_path("job_run_log", current_hour, "job-new.json")
        new_dataflow = hourly_path("dataflow_run_log", current_hour, "dataflow-new.json")
        _write_job_file(new_job, ["job-hour-new"])
        _write_dataflow_file(new_dataflow, ["run-hour-new"])

        result = _refresh(client, environment_id, source_id)
        assert result["sync_execution"]["result"]["record_counts"]["parsed_files"] == 2
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
                  last_scanned_partition_key
            FROM log_stream_states
            WHERE stream_kind IN ('dataflow_json', 'job_json')
            ORDER BY stream_kind
            """,
        ) == [
                (
                    "dataflow_json",
                    "hour",
                    current_hour.isoformat(timespec="seconds"),
                ),
                (
                    "job_json",
                    "hour",
                    current_hour.isoformat(timespec="seconds"),
                ),
        ]

        before_noop_generation = _query_rows(
            analytics_path,
            """
            SELECT
              (SELECT generation FROM etl_analytics_meta WHERE singleton_id = 1),
              (SELECT generation FROM etl_cache_sources WHERE source_id = 1)
            """,
        )
        noop = _refresh(client, environment_id, source_id)
        assert noop["sync_execution"]["result"]["record_counts"]["parsed_files"] == 0
        assert _query_rows(
            analytics_path,
            """
            SELECT
              (SELECT generation FROM etl_analytics_meta WHERE singleton_id = 1),
              (SELECT generation FROM etl_cache_sources WHERE source_id = 1)
            """,
        ) == before_noop_generation


def test_incremental_removes_rows_and_manifest_when_scanned_files_disappear(tmp_path: Path, monkeypatch) -> None:
    log_root = tmp_path / "logs"
    current_partition = datetime.now(timezone.utc).date().strftime("%Y%m%d")
    job_file = log_root / "execution_logs" / "job_run_log" / current_partition / "jobs.json"
    dataflow_file = (
        log_root / "execution_logs" / "dataflow_run_log" / current_partition / "dataflows.json"
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
        counts = result["sync_execution"]["result"]["record_counts"]
        assert counts["removed_files"] == 2
        assert _raw_ids(analytics_path, "etl_job_runs", "job_id") == []
        assert _raw_ids(analytics_path, "etl_dataflow_runs", "dataflow_run_id") == []
        assert _workspace_rows(tmp_path / "studio.db", "SELECT count(*) FROM log_file_manifest") == [(0,)]
        assert _query_rows(analytics_path, "SELECT count(*) FROM monitoring_dataflow_facts") == [(0,)]


def test_invalid_changed_file_fails_without_advancing_published_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_root = tmp_path / "logs"
    current_partition = datetime.now(timezone.utc).date().isoformat()
    job_file = log_root / "execution_logs" / "job_run_log" / current_partition / "jobs.json"
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
                  (SELECT max(last_scanned_partition_key)
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
        assert body["sync_execution"]["state"] == "failed"
        assert body["sync_execution"]["result"]["error"]["code"] == "invalid_json"
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
                  (SELECT max(last_scanned_partition_key)
                   FROM log_stream_states WHERE source_id = 1)
            """,
        ) == before_control


def test_incremental_refresh_does_not_use_legacy_recursive_source_scans(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_root = tmp_path / "logs"
    job_file = log_root / "execution_logs" / "job_run_log" / "2026-07-22" / "jobs.json"
    _write_job_file(job_file, ["job-bounded-discovery"])

    client, analytics_path = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, log_root)

        from datacoolie_studio.domains.logs import ingestion as log_ingestion

        def fail_legacy_scan(*_args, **_kwargs):
            raise AssertionError("incremental refresh must not invoke a legacy recursive source scan")

        monkeypatch.setattr(log_ingestion.sync, "stat_source", fail_legacy_scan)
        monkeypatch.setattr(log_ingestion.source_service, "validate_log_source", fail_legacy_scan)
        monkeypatch.setattr(log_ingestion.source_service, "record_source_validation", fail_legacy_scan)
        _refresh(client, environment_id, source_id)


def test_log_refresh_records_real_completion_time_and_phase_timings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_root = tmp_path / "logs"
    job_file = (
        log_root
        / "execution_logs"
        / "job_run_log"
        / "2026-07-22"
        / "jobs.json"
    )
    _write_job_file(job_file, ["job-timing"])

    client, _ = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, log_root)
        result = _refresh(client, environment_id, source_id)

    job = result["sync_execution"]
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
        / "execution_logs"
        / "job_run_log"
        / "2026-07-22"
        / "jobs.json"
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
    assert body["sync_execution"]["state"] == "failed"
    assert body["sync_execution"]["result"]["timings_ms"]["total"] >= 0
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
        / "system_20260722_010203_1_0_fixture-v6-job.json"
    )
    system_file.parent.mkdir(parents=True)
    system_file.write_text(
        json.dumps(native_record({"_type": "system_log", "msg": "manifest metadata and schema are read during sync"})) + "\n",
        encoding="utf-8",
    )

    client, analytics_path = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, log_root)
        from datacoolie_studio.domains.storage.adapters import LocalStorageAdapter

        def fail_content_read(*_args, **_kwargs):
            raise AssertionError("System sync must not read file contents")

        monkeypatch.setattr(LocalStorageAdapter, "open_read", fail_content_read)
        original_path_open = Path.open

        def fail_target_open(path, *args, **kwargs):
            if Path(path) == system_file:
                raise AssertionError("System sync must not open system contents")
            return original_path_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fail_target_open)
        first = _source_status(client, environment_id, source_id)
        assert first["sync_execution"]["result"]["record_counts"][
            "system_json_files"
        ] == 1
        assert _workspace_rows(
            tmp_path / "studio.db",
            """
            SELECT file_kind, row_count, job_id, filename_job_id,
                   filename_job_id_alt, log_session_id, log_timestamp,
                   event_min_timestamp, event_max_timestamp, partition_value
            FROM log_file_manifest
            WHERE source_id = 1 AND file_kind = 'system_json'
            """,
        ) == [
            (
                "system_json", 0, None, "fixture-v6-job", None, None,
                "2026-07-22 01:02:03.000000", None, None, "2026-07-22",
            ),
        ]
        assert _workspace_rows(
            tmp_path / "studio.db",
            """
            SELECT layout_status, partition_format, partition_granularity
            FROM log_stream_states
            WHERE source_id = 1 AND stream_kind = 'system_json'
            """,
        ) == [
            ("learned", "__run_date=%Y-%m-%d", "day"),
        ]
        first_generation = _query_rows(
            analytics_path,
            "SELECT generation FROM etl_analytics_meta WHERE singleton_id = 1",
        )

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
        assert second["sync_execution"]["result"]["record_counts"][
            "system_json_files"
        ] == 0
        assert _query_rows(
            analytics_path,
            "SELECT generation FROM etl_analytics_meta WHERE singleton_id = 1",
        ) == first_generation


def test_system_sync_never_reads_contents_across_replay_modes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_root = tmp_path / "logs"
    system_file = (
        log_root
        / "system_logs"
        / "__run_date=2026-07-22"
        / "system_20260722_010203_1_0_mode-check-job.json"
    )
    system_file.parent.mkdir(parents=True)
    system_file.write_text(
        json.dumps(native_record({"_type": "system_log", "msg": "opaque"})) + "\n",
        encoding="utf-8",
    )

    client, _analytics_path = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, log_root)
        from datacoolie_studio.domains.storage.adapters import LocalStorageAdapter

        def fail_content_read(*_args, **_kwargs):
            raise AssertionError("System sync must not read file contents")

        monkeypatch.setattr(LocalStorageAdapter, "open_read", fail_content_read)
        original_path_open = Path.open

        def fail_target_open(path, *args, **kwargs):
            if Path(path) == system_file:
                raise AssertionError("System sync must not open system contents")
            return original_path_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fail_target_open)
        _refresh(client, environment_id, source_id, {"mode": "incremental"})
        _refresh(
            client,
            environment_id,
            source_id,
            {
                "mode": "incremental_with_lookback",
                "lookback": {
                    "from_partition": "2026-07-22",
                    "to_partition": "2026-07-22",
                },
            },
        )

        from sqlalchemy import select

        from datacoolie_studio.db.models import EnvironmentSource, LogFileManifest
        from datacoolie_studio.db.session import create_session
        from datacoolie_studio.domains.logs.control_state import apply_deferred_log_control
        from datacoolie_studio.domains.logs.ingestion import refresh_log_source_cache

        with create_session() as session:
            manifest = session.scalar(
                select(LogFileManifest).where(
                    LogFileManifest.source_id == source_id,
                    LogFileManifest.file_kind == "system_json",
                )
            )
            assert manifest is not None
            manifest_uri = manifest.file_uri
            manifest.row_count = 12
            manifest.job_id = "content-derived-job"
            manifest.log_session_id = "content-session"
            manifest.event_min_timestamp = datetime(2026, 7, 22, 1, 2, tzinfo=timezone.utc)
            manifest.event_max_timestamp = datetime(2026, 7, 22, 1, 3, tzinfo=timezone.utc)
            manifest.admission_summary_json = '{"accepted": 12}'
            session.commit()

        with create_session() as session:
            source = session.get(EnvironmentSource, source_id)
            assert source is not None
            replay = refresh_log_source_cache(
                session,
                source,
                job_type="analytics_upgrade",
                force_analytics_replay=True,
                defer_analytics_generation=True,
                defer_control_commit=True,
            )
        assert replay["sync_execution"]["state"] == "succeeded"
        assert replay["sync_execution"]["result"]["status"] == "ok"
        deferred_control = replay["sync_execution"]["result"]["deferred_control"]

        with create_session() as session:
            source = session.get(EnvironmentSource, source_id)
            assert source is not None
            apply_deferred_log_control(
                session,
                source,
                deferred_control,
                checked_at=datetime.now(timezone.utc),
            )
            session.commit()

        assert _workspace_rows(
            tmp_path / "studio.db",
            """
            SELECT row_count, job_id, log_session_id, event_min_timestamp,
                   event_max_timestamp, admission_summary_json
            FROM log_file_manifest
            WHERE source_id = 1 AND file_kind = 'system_json'
            """,
        ) == [
            (
                12,
                "content-derived-job",
                "content-session",
                "2026-07-22 01:02:00.000000",
                "2026-07-22 01:03:00.000000",
                '{"accepted": 12}',
            ),
        ]

    assert _workspace_rows(
        tmp_path / "studio.db",
        """
        SELECT filename_job_id, row_count, job_id, log_session_id,
               event_min_timestamp, event_max_timestamp, admission_summary_json
        FROM log_file_manifest
        WHERE source_id = 1 AND file_kind = 'system_json'
        """,
    ) == [
        (
            "mode-check-job",
            12,
            "content-derived-job",
            "content-session",
            "2026-07-22 01:02:00.000000",
            "2026-07-22 01:03:00.000000",
            '{"accepted": 12}',
        ),
    ]

    from datacoolie_studio.db.session import create_session
    from datacoolie_studio.domains.logs import control as log_control

    with create_session() as session:
        log_control.upsert_manifest_rows(
            session,
            source_id,
            [
                {
                    "file_uri": manifest_uri,
                    "file_kind": "system_json",
                    "revision_json": '{"mtime_ns": 2, "size": 1}',
                    "filename_job_id": "mode-check-job",
                    "filename_job_id_alt": None,
                    "log_timestamp": datetime(2026, 7, 22, 1, 2, 3, tzinfo=timezone.utc),
                    "run_date": datetime(2026, 7, 22, tzinfo=timezone.utc).date(),
                },
            ],
            {},
            seen_at=datetime.now(timezone.utc),
        )
        session.commit()

    assert _workspace_rows(
        tmp_path / "studio.db",
        """
        SELECT filename_job_id, row_count, job_id, log_session_id,
               event_min_timestamp, event_max_timestamp, admission_summary_json
        FROM log_file_manifest
        WHERE source_id = 1 AND file_kind = 'system_json'
        """,
    ) == [("mode-check-job", 0, None, None, None, None, None)]


def test_historical_change_requires_explicit_lookback(tmp_path: Path, monkeypatch) -> None:
    log_root = tmp_path / "logs"
    old_job_file = log_root / "execution_logs" / "job_run_log" / "2026_07_21" / "jobs.json"
    current_job_file = log_root / "execution_logs" / "job_run_log" / "2026_07_22" / "jobs.json"
    old_dataflow_file = (
        log_root
        / "execution_logs"
        / "dataflow_run_log"
        / "2026_07_21"
        / "dataflows.json"
    )
    current_dataflow_file = (
        log_root
        / "execution_logs"
        / "dataflow_run_log"
        / "2026_07_22"
        / "dataflows.json"
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
    job_file = log_root / "execution_logs" / "job_run_log" / "2026-07-22" / "jobs.json"
    _write_job_file(job_file, ["job-validation"])

    client, _ = _test_client(tmp_path, monkeypatch)
    with client:
        environment_id, source_id = _create_workspace(client, log_root)
        response = client.post(
            f"/api/v1/environments/{environment_id}/log-sources/{source_id}/refresh",
            json=payload,
        )

    assert response.status_code == 422, response.text
