from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from datacoolie_studio.domains.analytics import access, contracts, schema, store
from tests.support.v6_fixture import deepcopy_v6_rows
from tests.support.v6_records import native_record


def _job_row(job_id: str) -> tuple[str, str, str, dict]:
    record = native_record(
        {
            "_type": "job_run_log",
            "job_id": job_id,
            "status": "succeeded",
            "end_time": "2026-08-01T00:00:00Z",
            "observed_at": "2026-08-01T00:00:00Z",
        }
    )
    return f"{job_id}.jsonl", "job_jsonl", "{}", record


def _local_dataflow_descriptor(path: Path) -> contracts.LogFileDescriptor:
    stat = path.stat()
    return contracts.LogFileDescriptor(
        canonical_uri=str(path),
        file_kind="dataflow_json",
        revision_json=json.dumps(
            {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
            sort_keys=True,
        ),
        read_uri=str(path),
    )


def _write_dataflow(path: Path) -> None:
    path.write_text(
        json.dumps(deepcopy_v6_rows("dataflow_snapshot.json")[0]) + "\n",
        encoding="utf-8",
    )


def test_incompatible_multisource_cache_routes_to_upgrade_owner(tmp_path: Path) -> None:
    analytics_path = tmp_path / "analytics.duckdb"
    with duckdb.connect(str(analytics_path)) as connection:
        connection.execute(
            "CREATE TABLE etl_dataflow_run_cache "
            "(source_id INTEGER, file_uri VARCHAR, row_json VARCHAR)"
        )
        connection.execute(
            "CREATE TABLE etl_job_run_cache "
            "(source_id INTEGER, file_uri VARCHAR, row_json VARCHAR)"
        )
        connection.execute(
            "INSERT INTO etl_job_run_cache VALUES "
            "(7, 'source-7.jsonl', '{\"job_id\":\"seven\"}'), "
            "(8, 'source-8.jsonl', '{\"job_id\":\"eight\"}')"
        )

    result = store.publish_rows(
        7,
        [],
        [_job_row("replacement")],
        [],
        [],
        database_path=analytics_path,
    )

    assert result["published"] is False
    assert result["errors"][0]["code"] == "analytics_upgrade_required"
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        assert connection.execute(
            "SELECT source_id, file_uri FROM etl_job_run_cache ORDER BY source_id"
        ).fetchall() == [
            (7, "source-7.jsonl"),
            (8, "source-8.jsonl"),
        ]
        assert "etl_job_runs" not in {
            row[0] for row in connection.execute("SHOW TABLES").fetchall()
        }


@pytest.mark.parametrize(
    ("schema_version", "build_state"),
    [
        (schema.ANALYTICS_SCHEMA_VERSION - 1, "ready"),
        (schema.ANALYTICS_SCHEMA_VERSION, "building"),
    ],
)
def test_public_cache_readiness_requires_current_ready_metadata(
    tmp_path: Path,
    schema_version: int,
    build_state: str,
) -> None:
    analytics_path = tmp_path / "analytics.duckdb"
    assert store.publish_rows(
        7,
        [],
        [_job_row("before")],
        [],
        [],
        database_path=analytics_path,
    )["published"]
    with duckdb.connect(str(analytics_path)) as connection:
        before = connection.execute(
            "SELECT generation, build_state FROM etl_analytics_meta "
            "WHERE singleton_id = 1"
        ).fetchone()
        connection.execute(
            "UPDATE etl_analytics_meta SET schema_version = ?, build_state = ? "
            "WHERE singleton_id = 1",
            [schema_version, build_state],
        )

    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        assert schema.typed_cache_schema_is_ready(connection) is True
        assert schema.analytics_publication_is_ready(connection) is False
    assert access.cache_is_ready(analytics_path) is False

    result = store.publish_rows(
        7,
        [],
        [_job_row("after")],
        [],
        [],
        database_path=analytics_path,
    )
    assert result["published"] is False
    assert result["errors"][0]["code"] == "analytics_upgrade_required"
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        assert connection.execute(
            "SELECT generation, build_state FROM etl_analytics_meta "
            "WHERE singleton_id = 1"
            ).fetchone() == (before[0], build_state)
        assert connection.execute(
            "SELECT job_id FROM etl_job_runs ORDER BY job_id"
        ).fetchall() == [("before",)]


def test_absent_database_remains_publishable(tmp_path: Path) -> None:
    result = store.publish_rows(
        7,
        [],
        [_job_row("new")],
        [],
        [],
        database_path=tmp_path / "new.duckdb",
    )
    assert result["published"] is True


@pytest.mark.parametrize("batched", [False, True])
def test_local_source_change_before_commit_rolls_back_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    batched: bool,
) -> None:
    analytics_path = tmp_path / "analytics.duckdb"
    assert store.publish_rows(
        7,
        [],
        [_job_row("before")],
        [],
        [],
        database_path=analytics_path,
    )["published"]
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        before_generation = connection.execute(
            "SELECT generation FROM etl_analytics_meta WHERE singleton_id = 1"
        ).fetchone()[0]

    source_path = tmp_path / ("batched.jsonl" if batched else "direct.jsonl")
    _write_dataflow(source_path)
    descriptor = _local_dataflow_descriptor(source_path)
    original_assert = store._assert_ingest_files_stable
    revision_calls = 0

    def assert_then_mutate(files, read_uris):
        nonlocal revision_calls
        revision_calls += 1
        original_assert(files, read_uris)
        if revision_calls == 2:
            source_path.write_text(
                source_path.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )

    monkeypatch.setattr(store, "_assert_ingest_files_stable", assert_then_mutate)
    if batched:
        result = store.publish_rows_batched(
            7,
            [contracts.LogPublishBatch(dataflow_files=(descriptor,))],
            [],
            [],
            database_path=analytics_path,
        )
    else:
        result = store.publish_rows(
            7,
            [descriptor],
            [_job_row("after")],
            [],
            [],
            database_path=analytics_path,
        )

    assert revision_calls >= 3
    assert result["published"] is False
    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        assert connection.execute(
            "SELECT generation FROM etl_analytics_meta WHERE singleton_id = 1"
        ).fetchone()[0] == before_generation
        assert connection.execute(
            "SELECT count(*) FROM etl_job_runs WHERE _source_id = 7"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM etl_dataflow_runs WHERE _source_id = 7"
        ).fetchone()[0] == 0


@pytest.mark.parametrize("failure", ["detached_local_mutation", "second_base_write", "derived_write"])
def test_multiple_batch_failure_preserves_old_generation_and_unaffected_source(tmp_path, monkeypatch, failure):
    from datacoolie_studio.domains.analytics import publication
    db=tmp_path/"multi.duckdb"
    for source in (7,8):
        assert store.publish_rows(source, [], [_job_row(f"before-{source}")], [], [], database_path=db)["published"]
    with duckdb.connect(str(db),read_only=True) as conn:
        before=conn.execute("SELECT generation FROM etl_analytics_meta").fetchone()
    descriptors=[]
    for i in range(2):
        p=tmp_path/f"batch-{i}.ndjson"
        row={**deepcopy_v6_rows("dataflow_snapshot.json")[0],"dataflow_run_id":f"batch-run-{i}"}
        p.write_text(json.dumps(row)+"\n",encoding="utf-8")
        descriptors.append(_local_dataflow_descriptor(p))
    detached=[]
    def detach(batch):
        detached.append(batch)
        if failure=="detached_local_mutation" and len(detached)==2:
            with Path(descriptors[0].read_uri).open("a") as f:
                f.write("\n")
    if failure=="second_base_write":
        original=store.insert_native_batch
        writes=[]
        def insert(*args,**kwargs):
            writes.append(1)
            if len(writes)==2:
                raise RuntimeError("second base write")
            return original(*args,**kwargs)
        monkeypatch.setattr(store,"insert_native_batch",insert)
    elif failure=="derived_write":
        original=publication.publish_generation
        def publish(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError("after derived writes")
        monkeypatch.setattr(publication,"publish_generation",publish)
    result=store.publish_rows_batched(7,[contracts.LogPublishBatch(dataflow_files=(x,)) for x in descriptors],[],[x.canonical_uri for x in descriptors],database_path=db,on_batch_detached=detach)
    assert not result["published"]
    assert result["file_row_counts"]=={}
    with duckdb.connect(str(db),read_only=True) as conn:
        assert conn.execute("SELECT generation FROM etl_analytics_meta").fetchone()==before
        assert conn.execute("SELECT _source_id,job_id FROM etl_job_runs ORDER BY _source_id").fetchall()==[(7,"before-7"),(8,"before-8")]
        assert conn.execute("SELECT count(*) FROM etl_dataflow_runs").fetchone()==(0,)
