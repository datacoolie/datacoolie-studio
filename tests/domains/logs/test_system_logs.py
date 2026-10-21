from __future__ import annotations

from tests.support.v6_records import native_record

import json
from datetime import datetime, timezone
from pathlib import Path

from datacoolie_studio.db.models import EnvironmentSource, LogFileManifest
from datacoolie_studio.db.session import create_session, init_db
from datacoolie_studio.domains.logs.file_state import revision_json
from datacoolie_studio.domains.logs.system_logs import system_log_records
from datacoolie_studio.domains.storage.adapters import LocalStorageAdapter


def _write_system_file(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )


def _row(session_id: str, timestamp: str, message: str) -> dict[str, object]:
    return native_record({
        "log_schema_version": 3,
        "_type": "system_log",
        "log_session_id": session_id,
        "job_id": "reused-job",
        "ts": timestamp,
        "level": "INFO",
        "logger": "test",
        "msg": message,
    })


def test_system_log_query_unions_batch_parts_before_global_pagination(
    studio_paths,
    studio_project_factory,
    monkeypatch,
) -> None:
    project = studio_project_factory()
    root = studio_paths.data_dir / "logs"
    first = root / "system_reused-job_a.json"
    second = root / "system_reused-job_b_part_00000001.json"
    unrelated = root / "system_reused-job_other.json"
    _write_system_file(
        first,
        [
            _row("session-a", "2026-09-17T00:01:00+00:00", "one"),
            _row("session-a", "2026-09-17T00:03:00+00:00", "three"),
        ],
    )
    _write_system_file(
        second,
        [
            _row("session-a", "2026-09-17T00:02:00+00:00", "two"),
            # UNION ALL semantics retain a repeated record from a separate
            # immutable part instead of silently deduplicating it.
            _row("session-a", "2026-09-17T00:02:00+00:00", "two"),
        ],
    )
    _write_system_file(
        unrelated,
        [_row("session-b", "2026-09-17T00:00:00+00:00", "other run")],
    )

    init_db()
    session = create_session()
    try:
        source = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri=str(root),
            storage_provider="local",
        )
        session.add(source)
        session.flush()
        adapter = LocalStorageAdapter()
        for path, session_id in (
            (first, "session-a"),
            (second, "session-a"),
            (unrelated, "session-b"),
        ):
            revision = adapter.stat(str(path))
            session.add(
                LogFileManifest(
                    source_id=source.id,
                    file_uri=revision.canonical_uri,
                    file_kind="system_json",
                    revision_json=revision_json(revision),
                    row_count=len(path.read_text(encoding="utf-8").splitlines()),
                    job_id="reused-job",
                    log_session_id=session_id,
                    log_timestamp=datetime(2026, 9, 17, tzinfo=timezone.utc),
                    event_min_timestamp=datetime(2026, 9, 17, 0, 1, tzinfo=timezone.utc),
                    event_max_timestamp=datetime(2026, 9, 17, 0, 3, tzinfo=timezone.utc),
                    run_date=datetime(2026, 9, 17).date(),
                    status="ok",
                )
            )
        session.commit()

        first_page = system_log_records(
            session,
            [source],
            job_id="reused-job",
            source_id=source.id,
            log_session_id="session-a",
            log_session_scope="exact",
            limit=2,
            offset=0,
        )
        second_page = system_log_records(
            session,
            [source],
            job_id="reused-job",
            source_id=source.id,
            log_session_id="session-a",
            log_session_scope="exact",
            limit=2,
            offset=2,
        )
        from datacoolie_studio.domains.logs import system_logs

        original_reader = system_logs.read_system_log_file

        def fail_one_file(read_uri: str, **kwargs):
            if Path(read_uri).name == second.name:
                raise OSError("simulated part read failure")
            return original_reader(read_uri, **kwargs)

        monkeypatch.setattr(system_logs, "read_system_log_file", fail_one_file)
        partial_page = system_log_records(
            session,
            [source],
            job_id="reused-job",
            source_id=source.id,
            log_session_id="session-a",
            log_session_scope="exact",
            limit=10,
            offset=0,
        )
    finally:
        session.close()

    assert first_page["total"] == 4
    assert [row["msg"] for row in first_page["records"] + second_page["records"]] == [
        "one",
        "two",
        "two",
        "three",
    ]
    assert {row["_log_session_id"] for row in first_page["records"] + second_page["records"]} == {
        "session-a"
    }
    assert len(first_page["files"]) == 3
    assert {item["log_session_id"] for item in first_page["files"]} == {"session-a", "session-b"}
    assert not first_page["errors"]
    assert partial_page["total"] == 2
    assert [row["msg"] for row in partial_page["records"]] == ["one", "three"]
    assert len(partial_page["errors"]) == 1
