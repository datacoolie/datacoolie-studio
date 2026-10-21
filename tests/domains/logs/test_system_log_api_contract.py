from __future__ import annotations

from tests.support.v6_records import native_record

import json
from datetime import datetime, timezone
from pathlib import Path

from datacoolie_studio.db.models import EnvironmentSource, LogFileManifest
from datacoolie_studio.db.session import create_session


def _write_system_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )


def _system_row(
    *,
    job_id: str,
    session_id: str,
    dataflow_id: str,
    dataflow_run_id: str,
    event_name: str,
    timestamp: str,
    message: str,
) -> dict[str, object]:
    return native_record({
        "log_schema_version": 4,
        "_type": "system_log",
        "job_id": job_id,
        "log_session_id": session_id,
        "dataflow_id": dataflow_id,
        "dataflow_run_id": dataflow_run_id,
        "event_name": event_name,
        "ts": timestamp,
        "level": "INFO",
        "logger": "api.contract",
        "msg": message,
    })


def test_v6_system_log_api_serializes_manifest_time_and_preserves_context(
    studio_client, studio_paths, studio_project_factory,
):
    project = studio_project_factory()
    fixture = Path(__file__).parents[2] / "fixtures/datacoolie/logging/v6/system_snapshot.json"
    log_file = studio_paths.data_dir / "system_fixture-v6-job.json"
    log_file.write_bytes(fixture.read_bytes())
    timestamp = datetime(2026, 9, 11, 0, 0, 30, tzinfo=timezone.utc)
    with create_session() as session:
        source = EnvironmentSource(
            environment_id=project.environment_id, source_kind="logs",
            uri=str(log_file.parent), storage_provider="local",
        )
        session.add(source)
        session.flush()
        source_id = source.id
        session.add(LogFileManifest(
            source_id=source_id, file_uri=str(log_file), file_kind="system_json",
            revision_json="{}", row_count=1, status="ok",
            job_id="fixture-v6-job",
            log_session_id="fixture-v6-session", log_timestamp=timestamp,
            event_min_timestamp=timestamp, event_max_timestamp=timestamp,
            run_date=timestamp.date(),
        ))
        session.commit()
    response = studio_client.get(
        f"/api/v1/environments/{project.environment_id}/monitoring/system-logs",
        params={"job_id": "fixture-v6-job",
                "source_id": source_id, "log_session_id": "fixture-v6-session",
                "dataflow_id": "fixture-v6-success",
                "dataflow_run_id": "fixture-v6-success-run"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    row = payload["records"][0]
    assert row["msg"] == "fixture ready"
    assert row["datacoolie_version"] == "0.2.0"
    assert payload["files"][0]["run_date"] == "2026-09-11"
    for key in ("log_timestamp", "event_min_timestamp", "event_max_timestamp"):
        assert payload["files"][0][key] == timestamp.isoformat()


def test_system_log_api_keeps_identity_collisions_isolated_and_paginated(
    studio_client,
    studio_paths,
    studio_project_factory,
):
    project = studio_project_factory()
    source_a_root = studio_paths.data_dir / "source-a"
    source_b_root = studio_paths.data_dir / "source-b"
    source_a_part_one = source_a_root / "system-job%2Fshared-part-one.json"
    source_a_part_two = source_a_root / "system-job%2Fshared-part-two.json"
    source_a_other_job = source_a_root / "system-other-job.json"
    source_a_other_session = source_a_root / "system-job%2Fshared-other-session.json"
    source_b_part = source_b_root / "system-job%2Fshared-part.json"

    exact_identity = {
        "dataflow_id": "DF-A",
        "dataflow_run_id": "Run-A",
        "event_name": "Event.A",
    }
    source_a_rows = [
        _system_row(
            job_id="job/shared",
            session_id="session-A",
            timestamp="2026-09-12T00:01:00+00:00",
            message="exact-a-one",
            **exact_identity,
        ),
        _system_row(
            job_id="job/shared",
            session_id="session-A",
            dataflow_id="df-a",
            dataflow_run_id="run-a",
            event_name="event.a",
            timestamp="2026-09-12T00:01:01+00:00",
            message="lowercase-collision",
        ),
        _system_row(
            job_id="job/shared",
            session_id="session-A",
            dataflow_id="DF-A ",
            dataflow_run_id="Run-A ",
            event_name="Event.A ",
            timestamp="2026-09-12T00:01:02+00:00",
            message="space-collision",
        ),
        _system_row(
            job_id="job/shared",
            session_id="session-A",
            dataflow_id="DF/A",
            dataflow_run_id="Run/A",
            event_name="Event/A",
            timestamp="2026-09-12T00:01:03+00:00",
            message="punctuation-collision",
        ),
    ]
    _write_system_rows(source_a_part_one, source_a_rows)
    _write_system_rows(
        source_a_part_two,
        [
            _system_row(
                job_id="job/shared",
                session_id="session-A",
                timestamp="2026-09-12T00:02:00+00:00",
                message="exact-a-two",
                **exact_identity,
            )
        ],
    )
    _write_system_rows(
        source_a_other_job,
        [
            _system_row(
                job_id="job/other",
                session_id="session-A",
                timestamp="2026-09-12T00:03:00+00:00",
                message="other-job",
                **exact_identity,
            )
        ],
    )
    _write_system_rows(
        source_a_other_session,
        [
            _system_row(
                job_id="job/shared",
                session_id="session-B",
                timestamp="2026-09-12T00:04:00+00:00",
                message="other-session",
                **exact_identity,
            )
        ],
    )
    _write_system_rows(
        source_b_part,
        [
            _system_row(
                job_id="job/shared",
                session_id="session-A",
                timestamp="2026-09-12T00:05:00+00:00",
                message="other-source",
                **exact_identity,
            )
        ],
    )

    with create_session() as session:
        source_a = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri=str(source_a_root),
            storage_provider="local",
        )
        source_b = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri=str(source_b_root),
            storage_provider="local",
        )
        session.add_all([source_a, source_b])
        session.flush()
        source_a_id = source_a.id
        source_b_id = source_b.id
        manifests = [
            (source_a, source_a_part_one, source_a_rows, "job/shared", "session-A"),
            (source_a, source_a_part_two, ["exact-a-two"], "job/shared", "session-A"),
            (source_a, source_a_other_job, ["other-job"], "job/other", "session-A"),
            (source_a, source_a_other_session, ["other-session"], "job/shared", "session-B"),
            (source_b, source_b_part, ["other-source"], "job/shared", "session-A"),
        ]
        for source, path, rows, job_id, session_id in manifests:
            file_timestamp = datetime(2026, 9, 12, tzinfo=timezone.utc)
            session.add(
                LogFileManifest(
                    source_id=source.id,
                    file_uri=str(path),
                    file_kind="system_json",
                    revision_json="{}",
                    row_count=len(rows),
                    status="ok",
                    job_id=job_id,
                    log_session_id=session_id,
                    log_timestamp=file_timestamp,
                    event_min_timestamp=file_timestamp,
                    event_max_timestamp=file_timestamp,
                    run_date=file_timestamp.date(),
                )
            )
        session.commit()

    endpoint = (
        f"/api/v1/environments/{project.environment_id}/monitoring/system-logs"
    )
    base_params = {
        "job_id": "job/shared",
        "source_id": source_a_id,
        "log_session_id": "session-A",
        "log_session_scope": "exact",
        **exact_identity,
    }

    first_page = studio_client.get(
        endpoint,
        params={**base_params, "limit": 1, "offset": 0},
    )
    second_page = studio_client.get(
        endpoint,
        params={**base_params, "limit": 1, "offset": 1},
    )
    assert first_page.status_code == 200, first_page.text
    assert second_page.status_code == 200, second_page.text
    first_payload = first_page.json()
    second_payload = second_page.json()
    assert first_payload["total"] == second_payload["total"] == 2
    assert [first_payload["records"][0]["msg"], second_payload["records"][0]["msg"]] == [
        "exact-a-one",
        "exact-a-two",
    ]
    assert first_payload["records"][0]["_source_id"] == source_a_id
    assert first_payload["records"][0]["_file_uri"] == str(source_a_part_one)
    assert second_payload["records"][0]["_source_id"] == source_a_id
    assert second_payload["records"][0]["_file_uri"] == str(source_a_part_two)
    assert {
        item["file_uri"] for item in first_payload["files"]
    } == {
        str(source_a_part_one),
        str(source_a_part_two),
        str(source_a_other_session),
    }
    assert {item["source_id"] for item in first_payload["files"]} == {source_a_id}
    assert {item["log_session_id"] for item in first_payload["files"]} == {"session-A", "session-B"}

    for identity, expected_message in (
        (
            {
                "dataflow_id": "df-a",
                "dataflow_run_id": "run-a",
                "event_name": "event.a",
            },
            "lowercase-collision",
        ),
        (
            {
                "dataflow_id": "DF-A ",
                "dataflow_run_id": "Run-A ",
                "event_name": "Event.A ",
            },
            "space-collision",
        ),
        (
            {
                "dataflow_id": "DF/A",
                "dataflow_run_id": "Run/A",
                "event_name": "Event/A",
            },
            "punctuation-collision",
        ),
    ):
        response = studio_client.get(
            endpoint,
            params={
                **base_params,
                **identity,
                "limit": 10,
                "offset": 0,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 1
        assert [row["msg"] for row in payload["records"]] == [expected_message]

    case_mismatch = studio_client.get(
        endpoint,
        params={**base_params, "dataflow_id": "DF-a", "limit": 10, "offset": 0},
    )
    assert case_mismatch.status_code == 200, case_mismatch.text
    assert case_mismatch.json()["total"] == 0

    other_session = studio_client.get(
        endpoint,
        params={
            **base_params,
            "log_session_id": "session-B",
            "log_session_scope": "exact",
            "limit": 10,
            "offset": 0,
        },
    )
    other_source = studio_client.get(
        endpoint,
        params={
            **base_params,
            "source_id": source_b_id,
            "limit": 10,
            "offset": 0,
        },
    )
    assert other_session.status_code == 200, other_session.text
    assert other_source.status_code == 200, other_source.text
    assert [row["msg"] for row in other_session.json()["records"]] == ["other-session"]
    assert [row["msg"] for row in other_source.json()["records"]] == ["other-source"]
    assert other_session.json()["records"][0]["_source_id"] == source_a_id
    assert other_source.json()["records"][0]["_source_id"] == source_b_id
