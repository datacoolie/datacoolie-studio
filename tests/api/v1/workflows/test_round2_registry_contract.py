from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.db.session import create_session
from datacoolie_studio.domains.analytics import store
from datacoolie_studio.domains.analytics.contracts import DataflowFile
from tests.support.v6_records import native_record


def test_registry_to_exact_related_history_preserves_entity_and_session_scope(
    studio_client, studio_paths, studio_project_factory,
):
    project = studio_project_factory()
    first = native_record({"dataflow_id": "flow", "dataflow_run_id": "one", "log_session_id": "A"})
    first["diagnostic_issues"] = [{"field": "/message", "reason": "truncated"}]
    second = native_record({"dataflow_id": "flow", "dataflow_run_id": "two", "log_session_id": "B"})
    collision = native_record({"dataflow_id": "another", "dataflow_name": "flow", "dataflow_run_id": "other", "log_session_id": "B", "destination_schema": "other"})
    path = studio_paths.data_dir / "dataflow.json"
    path.write_text("\n".join(json.dumps(row) for row in [first, second, collision]), encoding="utf-8")
    with create_session() as session:
        source = EnvironmentSource(environment_id=project.environment_id, source_kind="logs", uri=str(path.parent), storage_provider="local", enabled=True)
        session.add(source)
        session.commit()
        source_id = source.id
    result = store.publish_rows(source_id, [DataflowFile(str(path), "dataflow_json", "{}", str(path))], [], [], [], database_path=studio_paths.analytics_path)
    assert result["published"], result
    base = f"/api/v1/environments/{project.environment_id}/monitoring"
    common = {"range": "all", "investigateSourceId": source_id}
    flow = studio_client.get(f"{base}/dataflows", params={**common, "investigateKind": "dataflow_id", "investigateValue": "flow"})
    assert flow.status_code == 200, flow.text
    assert flow.json()["summary"]["analytics_generation"]
    assert {row["dataflow_run_id"] for row in flow.json()["records"]} == {"one", "two"}
    exact_session = studio_client.get(f"{base}/dataflows", params={**common, "investigateKind": "dataflow_id", "investigateValue": "flow", "log_session_scope": "exact", "log_session_id": "A"})
    assert exact_session.status_code == 200, exact_session.text
    assert [row["dataflow_run_id"] for row in exact_session.json()["records"]] == ["one"]

    # The registry emits the canonical Assets physical identity.  It must
    # round-trip to exactly the same declared destination history, even when a
    # separate dataflow uses the same display name.
    registry = studio_client.get(f"{base}/pages/maintenance/evidence", params={**common, "limit": 100})
    assert registry.status_code == 200, registry.text
    targets = registry.json()["records"]
    assert len(targets) == 2
    key = next(row["target"] for row in targets if row["destination_schema"] == first["destination_schema"])
    target = studio_client.get(f"{base}/dataflows", params={**common, "investigateKind": "physical_destination", "investigateValue": key})
    assert target.status_code == 200, target.text
    assert {row["dataflow_run_id"] for row in target.json()["records"]} == {"one", "two"}

    diagnostic = studio_client.get(f"{base}/pages/diagnostics", params=common)
    assert diagnostic.status_code == 200, diagnostic.text
    payload = diagnostic.json()
    assert payload["schema_version"] == "monitoring-page.v12"
    assert payload["diagnostics"]["kpis"]["diagnostic_known_markers"] == "1"
    assert payload["diagnostics"]["diagnostic_capture"]["summary"]["affected_records"] == "1"
    assert payload["diagnostics"]["diagnostic_capture"]["by_reason"] == [{"reason": "truncated", "count": "1"}]


def test_shared_registry_snapshot_scopes_recent_runs_and_exact_generation(
    studio_client, studio_paths, studio_project_factory,
):
    project = studio_project_factory()
    base_time = datetime(2026, 10, 1, tzinfo=timezone.utc)
    records = []
    for index in range(7):
        stamp = (base_time + timedelta(hours=index)).isoformat()
        records.append(native_record({
            "dataflow_id": "flow-360", "dataflow_run_id": f"run-{index}",
            "log_session_id": None, "start_time": stamp, "end_time": stamp,
            "status": "failed" if index == 6 else "succeeded",
            "operation_type": "maintenance" if index == 6 else "etl",
            "dataflow_name": "new-name" if index == 6 else "old-name",
            "dataflow_description": None if index == 6 else "previous description",
            "source_name": "new-source" if index == 6 else "old-source",
            "configure": None if index == 6 else "old-configuration",
        }))
        records[-1]["read_request"]["reference_time"] = stamp
    path = studio_paths.data_dir / "dataflow.json"
    path.write_text("\n".join(json.dumps(row) for row in records), encoding="utf-8")
    with create_session() as session:
        source = EnvironmentSource(environment_id=project.environment_id, source_kind="logs", uri=str(path.parent), storage_provider="local", enabled=True)
        session.add(source)
        session.commit()
        source_id = source.id
    published = store.publish_rows(source_id, [DataflowFile(str(path), "dataflow_json", "{}", str(path))], [], [], [], database_path=studio_paths.analytics_path)
    assert published["published"], published
    base = f"/api/v1/environments/{project.environment_id}/monitoring"
    params = {"range": "all", "dataflowId": "flow-360", "asOf": "2026-10-14T00:00:00Z"}
    overview = studio_client.get(f"{base}/dataflow-registry/detail", params=params)
    assert overview.status_code == 200, overview.text
    payload = overview.json()
    assert payload["section"] == "overview"
    for facet in ("freshness", "performance", "volume"):
        compact = studio_client.get(f"{base}/dataflow-registry", params={
            "range": "all", "facet": facet, "asOf": params["asOf"],
        })
        assert compact.status_code == 200, compact.text
        assert compact.json()["summary"]["total_records"] == 1
        assert compact.json()["records"][0]["run_count"] == "7"
    assert studio_client.get(f"{base}/dataflow-registry", params={"facet": "invalid"}).status_code == 422
    assert studio_client.get(f"{base}/dataflow-registry", params={"facet": "freshness", "sortBy": "volume_rows_read"}).status_code == 422
    row = payload["records"][0]
    assert {item["dataflow_run_id"] for item in row["recent_runs"]} == {f"run-{i}" for i in range(2, 7)}
    assert all("attempts" not in item and "raw_record_json" not in item for item in row["recent_runs"])
    filtered = {**params, "status": "succeeded"}
    scoped = studio_client.get(f"{base}/dataflow-registry/detail", params=filtered)
    assert scoped.status_code == 200, scoped.text
    scoped_row = scoped.json()["records"][0]
    assert scoped_row["latest_run_id"] == "run-5"
    profile = scoped_row["profile_snapshot"]
    assert profile["dataflow_run_id"] == "run-5"
    assert profile["source_name"] == "old-source"
    assert profile["dataflow_description"] == "previous description"
    assert profile["outside_filter"] is False
    config = studio_client.get(f"{base}/dataflow-registry/detail", params={**filtered, "section": "configuration"})
    assert config.status_code == 200, config.text
    assert config.json()["records"][0]["configure"] == "old-configuration"
    read = studio_client.get(f"{base}/dataflow-registry/detail", params={**filtered, "section": "read-watermark"})
    assert read.status_code == 200, read.text
    assert read.json()["records"][0]["dataflow_run_id"] == "run-5"
    assert read.json()["records"][0]["read_request"]["reference_time"] == records[5]["read_request"]["reference_time"]
    for section in ("execution", "runtime", "technical"):
        response = studio_client.get(f"{base}/dataflow-registry/detail", params={**filtered, "section": section, "limit": 2})
        assert response.status_code == 200, response.text
        assert response.json()["section"] == section
        assert response.json()["summary"]["analytics_generation"] == payload["summary"]["analytics_generation"]
    exact = studio_client.get(f"{base}/dataflows", params={"range": "all", "investigateKind": "dataflow_run_id", "investigateValue": "run-5", "investigateSourceId": source_id, "log_session_scope": "null", "limit": 1})
    assert exact.status_code == 200, exact.text
    assert exact.json()["summary"]["analytics_generation"] == payload["summary"]["analytics_generation"]
    assert exact.json()["records"][0]["dataflow_run_id"] == "run-5"
    invalid = studio_client.get(f"{base}/dataflow-registry/detail", params={**params, "section": "runtime", "sortBy": "duration_seconds"})
    assert invalid.status_code == 422
    runtime_page = studio_client.get(f"{base}/dataflow-registry/detail", params={**params, "section": "runtime", "offset": 100})
    assert runtime_page.status_code == 200, runtime_page.text
    assert runtime_page.json()["records"] == []
    assert runtime_page.json()["summary"]["total_records"] == 1
