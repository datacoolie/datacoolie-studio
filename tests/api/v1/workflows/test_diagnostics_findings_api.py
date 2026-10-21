from __future__ import annotations

from pathlib import Path

from datacoolie_studio.domains.analytics import access as analytics_access
from datacoolie_studio.domains.analytics import maintenance as analytics_maintenance
from tests.api.v1.api_support import SAMPLE_LOGS


def test_diagnostics_findings_page_and_evidence_roundtrip(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.logs import ingestion as log_ingestion
    from datacoolie_studio.main import app

    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_maintenance, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_access, "analytics_database_path", lambda: analytics_path)

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/log-sources",
            json={"uri": str(SAMPLE_LOGS), "label": "sample logs"},
        ).json()
        refreshed = client.post(
            f"/api/v1/environments/{env['id']}/log-sources/{source['id']}/refresh",
            json={"mode": "incremental"},
        )
        assert refreshed.status_code == 200, refreshed.text
        base = f"/api/v1/environments/{env['id']}/monitoring/pages/diagnostics"

        page = client.get(base, params={"range": "all"})
        assert page.status_code == 200, page.text
        diagnostics = page.json()["diagnostics"]
        summary = diagnostics["findings_summary"]
        assert summary["as_of"] and summary["analytics_generation"]
        assert int(summary["groups_total"]) == (
            int(summary["issue_groups"]) + int(summary["warning_groups"]) + int(summary["limitation_groups"])
        )
        assert len(diagnostics["finding_groups"]) == min(int(summary["groups_total"]), summary["group_limit"])
        # The legacy combined queue/summary keep their existing wire fields.
        assert "investigation_queue" in diagnostics and "registry_summary" in diagnostics

        params = {"range": "all", "asOf": summary["as_of"], "expectedGeneration": summary["analytics_generation"]}
        registry = client.get(f"{base}/evidence", params={**params, "limit": 2})
        assert registry.status_code == 200, registry.text
        payload = registry.json()
        assert payload["mode"] == "registry"
        assert payload["summary"]["total_records"] == int(summary["groups_total"])
        assert payload["summary"]["analytics_generation"] == summary["analytics_generation"]
        assert len(payload["groups"]) == min(2, int(summary["groups_total"]))
        keys = []
        offset = 0
        while True:
            chunk = client.get(f"{base}/evidence", params={**params, "limit": 2, "offset": offset}).json()["groups"]
            if not chunk:
                break
            keys.extend(item["group_key"] for item in chunk)
            offset += 2
        assert len(keys) == len(set(keys)) == int(summary["groups_total"])

        if keys:
            # Every group must serve its objects (rules differ in which columns are set).
            for key in keys:
                detail = client.get(f"{base}/evidence", params={**params, "view": "detail", "findingGroup": key})
                assert detail.status_code == 200, (key, detail.text)
                body = detail.json()
                assert body["mode"] == "detail" and body["group"]["group_key"] == key
                assert body["summary"]["total_records"] == int(body["group"]["affected_objects"])
                assert all(isinstance(item["metrics"], list) for item in body["objects"] if "metrics" in item)

        assert client.get(f"{base}/evidence", params={**params, "view": "detail"}).status_code == 422
        assert client.get(f"{base}/evidence", params={**params, "findingGroup": "log_loss|records|"}).status_code == 422
        assert client.get(f"{base}/evidence", params={**params, "sortBy": "job_id"}).status_code == 422
        assert client.get(
            f"{base}/evidence", params={**params, "view": "detail", "findingGroup": "bogus|x|"},
        ).status_code == 422
        assert client.get(f"{base}/evidence", params={**params, "asOf": "nonsense"}).status_code == 422
        limitations = client.get(f"{base}/evidence", params={**params, "findingKind": "limitations", "limit": 500}).json()
        findings = client.get(f"{base}/evidence", params={**params, "findingKind": "findings", "limit": 500}).json()
        assert all(item["kind"] == "limitation" for item in limitations["groups"])
        assert all(item["kind"] in {"issue", "warning"} for item in findings["groups"])
        assert limitations["summary"]["total_records"] + findings["summary"]["total_records"] == int(summary["groups_total"])
        assert client.get(f"{base}/evidence", params={**params, "findingKind": "bogus"}).status_code == 422
        assert client.get(
            f"{base}/evidence", params={**params, "view": "detail", "findingGroup": "log_loss|records|", "findingKind": "all"},
        ).status_code == 422
        stale = client.get(f"{base}/evidence", params={**params, "expectedGeneration": "stale"})
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "analytics_generation_changed"
