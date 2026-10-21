from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from datacoolie_studio.db.models import EnvironmentSource, LogFileManifest
from datacoolie_studio.db.session import create_session
from datacoolie_studio.domains.logs.admission import source_admission
from datacoolie_studio.domains.logs.control import delete_manifest_rows, upsert_manifest_rows


def test_admission_is_revision_bound_and_survives_new_session(studio_project_factory):
    environment = studio_project_factory()
    now = datetime.now(timezone.utc)
    with create_session() as session:
        source = EnvironmentSource(environment_id=environment.environment_id, source_kind="logs", uri="fixture://logs")
        session.add(source)
        session.flush()
        source_id = source.id
        state = {"file_uri": "fixture://old.json", "file_kind": "dataflow_json", "revision_json": '{"etag":"1"}', "status": "current", "admission_summary": {"admitted": 0, "excluded": 3, "versions": {"4": 3}, "locations": [{"line": 1, "version": "4"}]}}
        upsert_manifest_rows(session, source_id, [state], {}, seen_at=now)
        session.commit()
    with create_session() as session:
        result = source_admission(session, source_id)
        assert result["unsupported_only"] and result["excluded"] == 3
        # An unchanged scan carries no replacement admission result.
        unchanged = {key: value for key, value in state.items() if key != "admission_summary"}
        upsert_manifest_rows(session, source_id, [unchanged], {}, seen_at=now)
        session.commit()
        assert source_admission(session, source_id)["excluded"] == 3
        revised = {**state, "revision_json": '{"etag":"2"}', "admission_summary": {"admitted": 2, "excluded": 0}}
        upsert_manifest_rows(session, source_id, [revised], {}, seen_at=now)
        session.commit()
        assert source_admission(session, source_id)["excluded"] == 0
        assert json.loads(session.scalar(select(LogFileManifest.admission_summary_json))) == revised["admission_summary"]
        delete_manifest_rows(session, source_id, [state["file_uri"]])
        session.commit()
        assert source_admission(session, source_id)["admitted"] == 0
