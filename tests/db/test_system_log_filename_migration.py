from __future__ import annotations

from datetime import datetime, timezone
import importlib

from alembic import command
import pytest
from sqlalchemy import text

from datacoolie_studio.db.migration_runtime import (
    migration_config,
    upgrade_database,
)
from datacoolie_studio.db.models import Environment, EnvironmentSource, LogStreamState, Project
from datacoolie_studio.db.session import create_session, get_engine, init_db
from datacoolie_studio.domains.logs.system_log_filename import (
    parse_system_log_filename,
)


FROZEN_FILENAME_MIGRATION = importlib.import_module(
    "datacoolie_studio.db.migrations.versions."
    "b6d4f8a1c209_system_log_filename_candidates"
)


@pytest.mark.parametrize(
    "file_uri",
    (
        "system_20261010_010203_1_0_job-name.json",
        "file:///tmp/system_20261010_010203_2_1_c%C3%B4ng_vi%E1%BB%87c.json",
        "s3://bucket/system_20261010_010203_1_0_job%2E_part_00000001.json",
        "abfss://workspace@onelake.dfs.fabric.microsoft.com/item/Files/"
        "system_20261010_010203_1_0_%2Eleading.json",
        "opaque.json",
        "http://[malformed",
    ),
)
def test_frozen_filename_derivation_matches_runtime_parser(file_uri: str) -> None:
    runtime = parse_system_log_filename(file_uri)
    expected = (
        (runtime.job_id, runtime.job_id_alt)
        if runtime.job_id is not None
        else None
    )
    assert FROZEN_FILENAME_MIGRATION._parse_frozen(file_uri) == expected


def test_filename_migration_backfills_bounded_uri_only_and_preserves_state(
    studio_paths,
) -> None:
    init_db()
    engine = get_engine()
    with create_session() as session:
        environment = Environment(name="filename-migration-env", project=Project(name="filename-migration-project"))
        session.add(environment)
        session.flush()
        source = EnvironmentSource(
            environment_id=environment.id,
            source_kind="logs",
            uri="s3://bucket/logs",
            storage_provider="s3",
            storage_auth_mode="ambient",
        )
        session.add(source)
        session.flush()
        stream = LogStreamState(
            source_id=source.id,
            stream_kind="system_json",
            root_uri=source.uri,
            layout_status="learned",
            partition_format="%Y-%m-%d",
            partition_granularity="day",
            last_scanned_partition_key="2026-10-10",
        )
        session.add(stream)
        session.commit()
        source_id = source.id

    with engine.begin() as connection:
        command.downgrade(migration_config(connection), "a3b7d9e2f104")
        now = datetime(2026, 10, 10, tzinfo=timezone.utc).isoformat()
        rows = []
        for index in range(300):
            token = f"job-{index}"
            uri = f"s3://bucket/logs/system_20261010_010203_1_0_{token}.json"
            rows.append(
                {
                    "source_id": source_id,
                    "file_uri": uri,
                    "file_kind": "system_json",
                    "revision_json": '{"size": 17, "provider_revision": "etag"}',
                    "row_count": 17,
                    "job_id": f"content-{index}",
                    "log_timestamp": now,
                    "run_date": "2026-10-10",
                    "status": "ok",
                    "first_seen_at": now,
                    "last_seen_at": now,
                }
            )
        rows.append(
            {
                "source_id": source_id,
                "file_uri": "s3://bucket/logs/opaque.json",
                "file_kind": "system_json",
                "revision_json": '{"size": 3}',
                "row_count": 9,
                "job_id": "opaque-content",
                "log_timestamp": now,
                "run_date": "2026-10-10",
                "status": "ok",
                "first_seen_at": now,
                "last_seen_at": now,
            }
        )
        rows.append(
            {
                "source_id": source_id,
                "file_uri": "s3://bucket/logs/dataflow.json",
                "file_kind": "dataflow_json",
                "revision_json": '{"size": 5}',
                "row_count": 4,
                "job_id": None,
                "log_timestamp": None,
                "run_date": None,
                "status": "ok",
                "first_seen_at": now,
                "last_seen_at": now,
            }
        )
        connection.execute(
            text(
                "INSERT INTO log_file_manifest "
                "(source_id,file_uri,file_kind,revision_json,row_count,job_id,"
                "log_timestamp,run_date,status,first_seen_at,last_seen_at) "
                "VALUES (:source_id,:file_uri,:file_kind,:revision_json,:row_count,:job_id,"
                ":log_timestamp,:run_date,:status,:first_seen_at,:last_seen_at)"
            ),
            rows,
        )
        before_manifest = connection.execute(
            text(
                "SELECT file_uri, revision_json, row_count, job_id, "
                "log_timestamp, run_date, status, first_seen_at, last_seen_at "
                "FROM log_file_manifest WHERE source_id = :source_id ORDER BY id"
            ),
            {"source_id": source_id},
        ).all()

    upgrade_database(engine)
    with engine.connect() as connection:
        selected = connection.execute(
            text(
                "SELECT file_uri, filename_job_id, filename_job_id_alt, row_count, "
                "job_id, revision_json, log_timestamp, run_date, status, "
                "first_seen_at, last_seen_at FROM log_file_manifest "
                "WHERE source_id = :source_id ORDER BY id"
            ),
            {"source_id": source_id},
        ).all()
        indexes = {
            row[0]
            for row in connection.execute(
                text("SELECT name, sql FROM sqlite_master WHERE type = 'index'")
            ).all()
        }
        checkpoint = connection.execute(
            text(
                "SELECT last_scanned_partition_key FROM log_stream_states "
                "WHERE source_id = :source_id AND stream_kind = 'system_json'"
            ),
            {"source_id": source_id},
        ).scalar_one()

    assert len(selected) == 302
    assert [
        (row[0], row[5], row[3], row[4], row[6], row[7], row[8], row[9], row[10])
        for row in selected
    ] == before_manifest
    assert selected[0][1:3] == ("job-0", None)
    assert selected[299][1:3] == ("job-299", None)
    assert selected[300][1:3] == (None, None)
    assert selected[301][1:3] == (None, None)
    assert selected[300][3:6] == (9, "opaque-content", '{"size": 3}')
    assert {
        "ix_log_file_manifest_source_kind_filename_job",
        "ix_log_file_manifest_source_kind_filename_job_alt",
    } <= indexes
    assert checkpoint == "2026-10-10"

    upgrade_database(engine)
