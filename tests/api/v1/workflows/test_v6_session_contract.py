from __future__ import annotations

import json

import pytest

from datacoolie_studio.db.models import EnvironmentSource, LogFileManifest
from datacoolie_studio.db.session import create_session
from tests.support.v6_records import native_record


def test_system_session_scope_preserves_null_empty_and_literal_sentinel(
    studio_client, studio_paths, studio_project_factory,
):
    project = studio_project_factory()
    sessions = [None, "", "__NULL__", "Session-A", "session-a"]
    path = studio_paths.data_dir / "system_20261010_010000_1_0_job.json"
    rows = [native_record({"_type": "system_log", "job_id": "job", "log_session_id": value, "msg": str(index)}) for index, value in enumerate(sessions)]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    with create_session() as session:
        source = EnvironmentSource(environment_id=project.environment_id, source_kind="logs", uri=str(path.parent), storage_provider="local")
        session.add(source)
        session.flush()
        source_id = source.id
        session.add(LogFileManifest(source_id=source_id, file_uri=str(path), file_kind="system_json", revision_json="{}", status="ok", job_id="different-first-envelope", log_session_id="different-first-envelope"))
        session.commit()
    endpoint = f"/api/v1/environments/{project.environment_id}/monitoring/system-logs"
    base = {"source_id": source_id, "job_id": "job"}
    assert studio_client.get(endpoint, params=base).json()["total"] == len(sessions)
    assert studio_client.get(endpoint, params={**base, "log_session_scope": "exact"}).status_code == 422
    for value in sessions:
        scope = {"log_session_scope": "null"} if value is None else {"log_session_scope": "exact", "log_session_id": value}
        response = studio_client.get(endpoint, params={**base, **scope})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 1
        assert payload["records"][0]["log_session_id"] == value
        assert payload["records"][0]["_log_session_id"] == value
        assert payload["records"][0]["_record_line"] == sessions.index(value) + 1
        assert json.loads(payload["records"][0]["raw_record_json"])["log_session_id"] == value


@pytest.mark.parametrize("endpoint", ["pages/overview", "pages/performance/evidence", "dataflows", "jobs"])
def test_monitoring_routes_reject_exact_scope_without_value(studio_client, studio_project_factory, endpoint):
    project = studio_project_factory()
    response = studio_client.get(f"/api/v1/environments/{project.environment_id}/monitoring/{endpoint}", params={"log_session_scope": "exact"})
    assert response.status_code == 422
