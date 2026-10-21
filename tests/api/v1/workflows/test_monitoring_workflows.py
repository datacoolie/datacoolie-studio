from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from datacoolie_studio.domains.analytics import access as analytics_access
from datacoolie_studio.domains.analytics import maintenance as analytics_maintenance
from tests.api.v1.api_support import (
    SAMPLE_LOGS,
    _source_status,
)


def test_monitoring_filter_counts_follow_range_and_reuse_cached_counts(tmp_path, monkeypatch):
    import duckdb
    from fastapi.testclient import TestClient
    from datacoolie_studio.domains.monitoring import filter_options
    from datacoolie_studio.domains.read_models.provider import result_cache_provider
    from datacoolie_studio.domains.analytics.cache_state import mark_cache_source
    from datetime import datetime, timezone
    from tests.domains.monitoring.test_filter_range_counts import populate

    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path/'studio.db').as_posix()}")
    from datacoolie_studio.main import app
    calls = []
    original = filter_options.monitoring_filter_options_read_model
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(filter_options, 'monitoring_filter_options_read_model', counted)
    with TestClient(app) as client:
        project = client.post('/api/v1/projects', json={'name': 'range-counts'}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={'name': 'dev'}).json()
        logs = tmp_path/'logs'
        logs.mkdir()
        source = client.post(f"/api/v1/environments/{env['id']}/log-sources", json={'uri': str(logs), 'label': 'logs'}).json()
        with duckdb.connect(str(analytics_access.analytics_database_path())) as conn:
            populate(conn)
            assert source['id'] == 1
        url = f"/api/v1/environments/{env['id']}/monitoring/filter-options"
        all_time = client.get(url)
        assert all_time.status_code == 200, all_time.text
        registry = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/dataflow-registry",
            params={"range": "all", "limit": 100},
        )
        assert registry.status_code == 200, registry.text
        assert registry.json()["summary"]["total_records"] >= 1
        first_dataflow_id = registry.json()["records"][0].get("dataflow_id")
        assert first_dataflow_id
        execution = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/dataflow-registry/detail",
            params={"dataflowId": first_dataflow_id, "section": "execution", "range": "all"},
        )
        assert execution.status_code == 200, execution.text
        assert execution.json()["summary"]["total_records"] >= 1
        def values(response):
            return {item['value']: item['count'] for item in response.json()['options']['stage']}
        assert values(all_time) == {'edge': 3, 'old': 1}
        query = '?range=custom&startTime=2026-10-09T00%3A00%3A00Z&endTime=2026-10-09T00%3A00%3A00Z'
        ranged = client.get(url+query)
        assert values(ranged) == {'edge': 3, 'old': 0}
        assert client.get(url+query+'&status=failed&operationType=maintenance').json() == ranged.json()
        assert len(calls) == 2
        empty = client.get(url+'?range=custom&startTime=2027-01-01T00%3A00%3A00Z')
        assert values(empty) == {'edge': 0, 'old': 0}
        assert client.get(url+'?range=invalid').status_code == 422
        assert values(client.get(url+query)) == {'edge': 3, 'old': 0}
        assert len(calls) == 3
        result_cache_provider().store.invalidate(env['id'], {'monitoring.page.any'})
        assert values(client.get(url+query)) == {'edge': 3, 'old': 0}
        assert len(calls) == 4
        with duckdb.connect(str(analytics_access.analytics_database_path())) as conn:
            mark_cache_source(conn, 1, refreshed_at=datetime.now(timezone.utc))
        assert values(client.get(url+query)) == {'edge': 3, 'old': 0}
        assert len(calls) == 5


def test_monitoring_handles_accessible_empty_log_sources(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    empty_logs = tmp_path / "empty-logs"
    empty_logs.mkdir()
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.logs import ingestion as log_ingestion
    from datacoolie_studio.main import app

    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_access, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_maintenance, "analytics_database_path", lambda: analytics_path)
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "empty-monitoring"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        source = client.post(
            f"/api/v1/environments/{environment['id']}/log-sources",
            json={"uri": str(empty_logs), "label": "empty logs"},
        ).json()
        source = client.get(
            f"/api/v1/environments/{environment['id']}/log-sources"
        ).json()[0]

        assert source["latest_validation"]["status"] == "warning"
        assert source["latest_validation"]["message"] == (
            "Log source is readable but contains no matching log files"
        )

        overview = client.get(f"/api/v1/environments/{environment['id']}/overview")
        filter_options = client.get(
            f"/api/v1/environments/{environment['id']}/monitoring/filter-options"
        )
        assert overview.status_code == 200, overview.text
        assert overview.json()["monitoring"]["job_records"] == "0"
        assert filter_options.status_code == 200, filter_options.text
        assert filter_options.json()["options"] == {"connection": []}
        for page in (
            "environment-overview",
            "overview",
            "jobs",
            "dataflows",
            "failures",
            "diagnostics",
            "performance",
            "volume",
            "maintenance",
            "freshness",
        ):
            monitoring_page = client.get(
                f"/api/v1/environments/{environment['id']}/monitoring/pages/{page}"
            )
            assert monitoring_page.status_code == 200, monitoring_page.text
            assert monitoring_page.json()["summary"]["job_records"] == "0"

        for endpoint in ("dataflows", "jobs"):
            records = client.get(
                f"/api/v1/environments/{environment['id']}/monitoring/{endpoint}"
            )
            assert records.status_code == 200, records.text
            assert records.json()["summary"]["total_records"] == 0
        for page in ("performance", "freshness", "volume", "maintenance"):
            evidence = client.get(
                f"/api/v1/environments/{environment['id']}/monitoring/pages/{page}/evidence"
            )
            assert evidence.status_code == 200, evidence.text
            assert evidence.json()["summary"]["total_records"] == 0
        registry = client.get(
            f"/api/v1/environments/{environment['id']}/monitoring/dataflow-registry"
        )
        assert registry.status_code == 200, registry.text
        assert registry.json()["summary"]["total_records"] == 0
        registry_detail = client.get(
            f"/api/v1/environments/{environment['id']}/monitoring/dataflow-registry/detail",
            params={"dataflowId": "missing"},
        )
        assert registry_detail.status_code == 200, registry_detail.text
        assert registry_detail.json()["records"] == []


def test_empty_log_source_recovers_when_files_arrive(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    empty_logs = tmp_path / "empty-logs"
    empty_logs.mkdir()
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.logs import ingestion as log_ingestion
    from datacoolie_studio.main import app

    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_access, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_maintenance, "analytics_database_path", lambda: analytics_path)
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "empty-recovery"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        source = client.post(
            f"/api/v1/environments/{environment['id']}/log-sources",
            json={"uri": str(empty_logs), "label": "empty logs"},
        ).json()
        assert source["latest_validation"] is None

        initial = client.get(
            f"/api/v1/environments/{environment['id']}/log-sources"
        ).json()[0]
        assert initial["latest_validation"]["status"] == "warning"
        assert initial["latest_validation"]["record_counts"] == {
            "dataflow_json_files": 0,
            "job_json_files": 0,
            "system_json_files": 0,
        }

        sample = next(
            (SAMPLE_LOGS / "execution_logs" / "job_run_log").rglob("*.json")
        )
        arriving_file = empty_logs / sample.relative_to(SAMPLE_LOGS)
        arriving_file.parent.mkdir(parents=True, exist_ok=True)
        arriving_file.write_bytes(sample.read_bytes())

        refreshed = client.post(
            f"/api/v1/environments/{environment['id']}/log-sources/{source['id']}/refresh",
            json={"mode": "incremental"},
        )
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["status"] == "ok"

        recovered = client.get(
            f"/api/v1/environments/{environment['id']}/log-sources"
        ).json()[0]
        assert recovered["latest_validation"]["status"] == "ok"
        jobs = client.get(
            f"/api/v1/environments/{environment['id']}/monitoring/jobs?range=365d"
        )
        assert jobs.status_code == 200, jobs.text
        assert jobs.json()["summary"]["total_records"] > 0

def test_environment_overview_is_available_when_monitoring_cache_is_missing(
    tmp_path: Path,
    monkeypatch,
):
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.logs import ingestion as log_ingestion
    from datacoolie_studio.main import app

    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_access, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_maintenance, "analytics_database_path", lambda: analytics_path)
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "overview-fail-soft"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        source = client.post(
            f"/api/v1/environments/{environment['id']}/log-sources",
            json={"uri": str(SAMPLE_LOGS), "label": "logs"},
        ).json()
        source = client.get(
            f"/api/v1/environments/{environment['id']}/log-sources"
        ).json()[0]
        assert source["latest_validation"]["status"] == "ok"
        # Add performs the required initial sync. Remove that cache explicitly
        # so this test still exercises the fail-soft read contract.
        analytics_path.unlink()

        overview = client.get(f"/api/v1/environments/{environment['id']}/overview")
        filter_options = client.get(
            f"/api/v1/environments/{environment['id']}/monitoring/filter-options"
        )
        dataflows = client.get(
            f"/api/v1/environments/{environment['id']}/monitoring/dataflows"
        )
        jobs = client.get(f"/api/v1/environments/{environment['id']}/monitoring/jobs")
        latest_status = client.get(
            f"/api/v1/environments/{environment['id']}/monitoring/latest-status"
        )
        evidence = client.get(
            f"/api/v1/environments/{environment['id']}/monitoring/pages/performance/evidence"
        )

        assert overview.status_code == 200, overview.text
        assert overview.json()["monitoring"] == {
            "job_records": "0",
            "total_failures": "0",
            "dataflow_success_rate": 0.0,
            "failed_job_windows": {"last7": "0", "last30": "0", "last365": "0"},
            "active_engines": "0",
            "latest_log_at": None,
            "date_range": {"min": None, "max": None},
            "errors": [],
        }
        assert filter_options.status_code == 200, filter_options.text
        assert filter_options.json()["options"] == {}
        assert dataflows.status_code == 200, dataflows.text
        assert dataflows.json()["summary"]["total_records"] == 0
        assert jobs.status_code == 200, jobs.text
        assert jobs.json()["summary"]["total_records"] == 0
        assert latest_status.status_code == 200, latest_status.text
        assert latest_status.json()["latest_by_id"] == {}
        assert evidence.status_code == 200, evidence.text
        assert evidence.json()["records"] == []

        for page in (
            "environment-overview",
            "overview",
            "jobs",
            "dataflows",
            "failures",
            "diagnostics",
            "performance",
            "volume",
            "maintenance",
            "freshness",
        ):
            monitoring_page = client.get(
                f"/api/v1/environments/{environment['id']}/monitoring/pages/{page}"
            )
            assert monitoring_page.status_code == 200, monitoring_page.text
            assert monitoring_page.json()["summary"]["job_records"] == "0"

def test_metadata_api_uses_current_materialization_and_auto_refreshes_stale_source(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "studio.db"
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        """
        {
          "connections": [{"name": "lake"}],
          "dataflows": [{"name": "flow_v1", "destination": {"table": "target_v1"}}],
          "schema_hints": []
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.metadata import service as metadata_service
    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(metadata_path), "label": "metadata"},
        ).json()

        first = client.get(f"/api/v1/environments/{env['id']}/metadata").json()
        assert first["summary"]["dataflows"] == 1
        assert first["dataflows"][0]["name"] == "flow_v1"
        with sqlite3.connect(db_path) as connection:
            assert (
                connection.execute(
                    "select count(*) from metadata_materializations"
                ).fetchone()[0]
                == 1
            )
            initial_sync_job_count = connection.execute(
                "select count(*) from sync_jobs"
            ).fetchone()[0]

        original_read_metadata_document_bytes = metadata_service.read_metadata_document_bytes

        def fail_if_parsed(*_args, **_kwargs):
            raise AssertionError("metadata file should not be parsed on cache hit")

        monkeypatch.setattr(
            metadata_service,
            "read_metadata_document_bytes",
            fail_if_parsed,
        )
        second = client.get(f"/api/v1/environments/{env['id']}/metadata").json()
        assert second["dataflows"][0]["name"] == "flow_v1"
        with sqlite3.connect(db_path) as connection:
            assert (
                connection.execute(
                    "select count(*) from metadata_materializations"
                ).fetchone()[0]
                == 1
            )
            assert connection.execute(
                "select count(*) from sync_jobs"
            ).fetchone()[0] == initial_sync_job_count

        monkeypatch.setattr(
            metadata_service,
            "read_metadata_document_bytes",
            original_read_metadata_document_bytes,
        )

        metadata_path.write_text(
            """
            {
              "connections": [{"name": "lake"}],
              "dataflows": [{"name": "flow_v2", "destination": {"table": "target_v2"}}],
              "schema_hints": []
            }
            """,
            encoding="utf-8",
        )
        stale = client.get(f"/api/v1/environments/{env['id']}/metadata").json()
        assert stale["dataflows"][0]["name"] == "flow_v2"
        with sqlite3.connect(db_path) as connection:
            assert (
                connection.execute(
                    "select count(*) from metadata_materializations"
                ).fetchone()[0]
                == 1
            )
            assert connection.execute(
                "select count(*) from sync_jobs"
            ).fetchone()[0] == initial_sync_job_count + 1

        metadata_path.write_text("{ invalid json", encoding="utf-8")
        invalid = client.get(f"/api/v1/environments/{env['id']}/metadata").json()
        assert invalid["dataflows"][0]["name"] == "flow_v2"
        assert invalid["summary"]["errors"] == 1
        listed = client.get(
            f"/api/v1/environments/{env['id']}/metadata-sources"
        ).json()
        assert listed[0]["latest_validation"] is None

        metadata_path.unlink()
        last_good = client.get(f"/api/v1/environments/{env['id']}/metadata").json()
        assert last_good["dataflows"][0]["name"] == "flow_v2"
        assert last_good["summary"]["errors"] == 1
        assert last_good["errors"][0]["cache_status"] == "stale"
        status = _source_status(client, env["id"], source["id"])
        assert status["status"] == "error"


def test_automatic_metadata_refresh_has_no_per_source_schedule(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {"connections": [{"name": "lake"}], "dataflows": [], "schema_hints": []}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.sync.scheduler import run_due_schedules_once
    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(metadata_path), "label": "metadata"},
        ).json()

        rejected = client.patch(
            f"/api/v1/environments/{env['id']}/metadata-sources/{source['id']}",
            json={"sync_schedule_enabled": True, "sync_interval_minutes": 15},
        )
        assert rejected.status_code == 422

        assert run_due_schedules_once() == 0
        assert client.post(
            f"/api/v1/environments/{env['id']}/sources/observe-local"
        ).json()["failed"] == 0
        status = _source_status(client, env["id"], source["id"])
        assert status["status"] == "ok"
        assert status["sync_execution"]["trigger"] == "automatic"
        with sqlite3.connect(db_path) as connection:
            assert (
                connection.execute(
                    "select count(*) from metadata_materializations"
                ).fetchone()[0]
                == 1
            )
            row = connection.execute("select last_scheduled_sync_at from environment_sources where id = ?", (source["id"],)).fetchone()
            assert row[0] is None

        assert client.post(
            f"/api/v1/environments/{env['id']}/sources/observe-local"
        ).json()["changed"] == 0


def test_automatic_code_refresh_rebuilds_only_after_source_change(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    code_path = tmp_path / "functions"
    code_path.mkdir()
    module_path = code_path / "transform.py"
    module_path.write_text("def transform(value):\n    return value\n", encoding="utf-8")
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/code-artifacts",
            json={"uri": str(code_path), "label": "functions"},
        ).json()

        assert client.post(
            f"/api/v1/environments/{env['id']}/sources/observe-local"
        ).json()["failed"] == 0
        status = _source_status(client, env["id"], source["id"])
        assert status["status"] == "ok"
        assert status["sync_execution"]["trigger"] == "automatic"
        assert client.post(
            f"/api/v1/environments/{env['id']}/sources/observe-local"
        ).json()["changed"] == 0

        module_path.write_text("def transform(value):\n    return value * 2\n", encoding="utf-8")
        assert client.post(
            f"/api/v1/environments/{env['id']}/sources/observe-local"
        ).json()["changed"] == 1

        with sqlite3.connect(db_path) as connection:
            assert connection.execute("select count(*) from code_artifact_materializations").fetchone()[0] == 1


def test_monitoring_page_api_roundtrip(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.analytics import access as analytics_access
    from datacoolie_studio.domains.logs import ingestion as log_ingestion
    from datacoolie_studio.main import app

    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_maintenance, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_access, "analytics_database_path", lambda: analytics_path)

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
        ).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/log-sources",
            json={"uri": str(SAMPLE_LOGS), "label": "sample logs"},
        ).json()
        paths = client.get(f"/api/v1/environments/{env['id']}/log-sources").json()
        validation = client.post(
            f"/api/v1/environments/{env['id']}/log-sources/{paths[0]['id']}/validate"
        ).json()
        assert validation["status"] == "ok"
        assert validation["record_counts"]["job_json_files"] > 0
        paths = client.get(f"/api/v1/environments/{env['id']}/log-sources").json()
        assert paths[0]["latest_validation"]["record_counts"]["job_json_files"] > 0
        refreshed = client.post(
            f"/api/v1/environments/{env['id']}/log-sources/{source['id']}/refresh",
            json={"mode": "incremental"},
        )
        assert refreshed.status_code == 200, refreshed.text

        original_connect = analytics_access.connect
        connection_calls = 0

        def counted_connect(*args, **kwargs):
            nonlocal connection_calls
            connection_calls += 1
            return original_connect(*args, **kwargs)

        monkeypatch.setattr(analytics_access, "connect", counted_connect)
        for endpoint in ("filter-options", "dataflows?limit=1", "jobs?limit=1"):
            before = connection_calls
            response = client.get(
                f"/api/v1/environments/{env['id']}/monitoring/{endpoint}"
            )
            assert response.status_code == 200, response.text
            assert connection_calls - before <= 1

        before = connection_calls
        # The fixture is historical; use an explicit full-history window so
        # the assertions do not change as the current date moves forward.
        report_response = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/overview?range=all"
        )
        # A result-cache hit opens no analytics connection; a miss uses one shared reader.
        assert connection_calls - before <= 1
        report = report_response.json()
        assert report["summary"]["dataflow_records"] != "0"
        assert report["summary"]["job_records"] != "0"
        assert report["summary"]["requested_grain"] == "auto"
        assert report["summary"]["effective_grain"] in {"hour", "day", "week", "month"}
        assert report["summary"]["latest_log_at"].endswith(("+00:00", "Z"))
        assert report["summary"]["latest_job_log_at"].endswith(("+00:00", "Z"))
        assert report["summary"]["latest_dataflow_log_at"].endswith(("+00:00", "Z"))
        assert report["operations"]["kpis"]["total_jobs"] != "0"
        assert report["operations"]["dataflows_by_date_status"]
        assert {"bucket", "bucket_start", "bucket_end", "grain"} <= set(
            report["operations"]["dataflows_by_date_status"][0]
        )
        job_operation_types = {
            item["operation_type"]
            for item in report["operations"]["job_runs_by_dataflow_operation_type"]
        }
        assert {"etl", "dry_run"} <= job_operation_types
        assert job_operation_types != {"unknown"}
        assert report["volume"]["rows_by_date"]
        assert report["volume"]["bytes_by_date"]
        weekly_report = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/overview?range=90d&grain=auto"
        ).json()
        assert weekly_report["summary"]["requested_grain"] == "auto"
        assert weekly_report["summary"]["effective_grain"] in {
            "hour",
            "day",
            "week",
            "month",
        }
        performance_response = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/performance?range=all"
        )
        performance_report = performance_response.json()
        maintenance_report = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/maintenance?range=all"
        ).json()
        volume_report = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/volume?range=all"
        ).json()
        failures_report = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/failures?range=all"
        ).json()
        assert performance_report["performance"]["kpis"]["p95_duration_seconds"] >= 0
        assert performance_report["schema_version"] == "monitoring-page.v12"
        assert performance_report["performance"]["kpis"]["slowest_run_start_time"]
        assert performance_report["performance"]["kpis"]["slowest_run_end_time"]
        assert performance_report["performance"]["slowest_dataflow_profiles"]
        assert "investigation_queue" not in performance_report["performance"]
        assert "slowest_dataflows_by_p95" not in performance_report["performance"]
        etag = performance_response.headers["etag"]
        unchanged = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/performance?range=all",
            headers={"If-None-Match": etag},
        )
        assert unchanged.status_code == 304
        assert unchanged.content == b""
        evidence = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/performance/evidence",
            params={"range": "all", "limit": 2, "offset": 0, "sortBy": "duration_seconds", "sortDir": "desc"},
        ).json()
        assert len(evidence["records"]) <= 2
        assert evidence["summary"]["total_records"] >= len(evidence["records"])
        freshness_registry = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/freshness/evidence",
            params={"range": "all", "limit": 2, "offset": 0, "view": "registry", "sortBy": "latest_freshness_at", "sortDir": "desc"},
        )
        assert freshness_registry.status_code == 200, freshness_registry.text
        freshness_registry_payload = freshness_registry.json()
        assert len(freshness_registry_payload["records"]) <= 2
        assert freshness_registry_payload["summary"]["total_records"] >= len(freshness_registry_payload["records"])
        assert freshness_registry_payload["records"]
        assert all("raw_record_json" not in record for record in freshness_registry_payload["records"])
        # Destinations now owns this page and reports the all-operation
        # population; the old maintenance-only total is no longer published.
        assert int(maintenance_report["maintenance"]["kpis"]["operation_count"]) >= 0
        for bucket in maintenance_report["maintenance"]["status_by_date"]:
            assert {"succeeded", "failed", "skipped", "running", "pending", "unknown", "total"} <= set(bucket)
            assert int(bucket["total"]) == sum(
                int(bucket[status]) for status in ("succeeded", "failed", "skipped", "running", "pending", "unknown")
            )
        for bucket in maintenance_report["maintenance"]["reclaim_by_date"]:
            # Destinations owns this all-operation series.  Physical effects
            # are exposed with the destination metric aliases and their
            # observed coverage; legacy maintenance-only bytes_saved and
            # bytes_reclaimed fields are not part of this contract.
            assert {"bytes_removed_observed", "files_removed_observed", "runs"} <= set(bucket)
        assert sum(
            int(item["count"]) for item in failures_report["failures"]["error_categories"]
        ) == int(failures_report["failures"]["kpis"]["failed_dataflows"])
        for failed_record in failures_report["failures"]["failed_records"]:
            assert isinstance(failed_record["failure_tags"], list)
            assert len(failed_record["failure_tags"]) <= 5
            failure_rule_id = failed_record.get("failure_rule_id")
            assert failure_rule_id is None or isinstance(failure_rule_id, str)
        maintenance_evidence = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/maintenance/evidence",
            params={"range": "all", "limit": 100, "offset": 0},
        ).json()
        for table in maintenance_evidence["records"]:
            assert isinstance(table["upstream_dataflows"], list)
            # The Destinations-owned evidence contract bounds the contributor
            # list and reports its population as contributing_dataflow_count.
            assert int(table["contributing_dataflow_count"]) >= len(table["upstream_dataflows"])
            if int(table["contributing_dataflow_count"]):
                assert table["upstream_dataflows"]

        first_job = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/jobs?range=all&limit=1"
        ).json()["records"][0]
        canonical_job = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/jobs",
            params={
                "range": "all",
                "investigateKind": "job_id",
                "investigateValue": first_job["job_id"],
                "limit": 1,
                "offset": 0,
            },
        ).json()
        assert canonical_job["records"][0]["job_id"] == first_job["job_id"]
        child_page = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/dataflows",
            params={
                "limit": 2,
                "offset": 0,
                "range": "all",
                "sortBy": "duration_seconds",
                "sortDir": "desc",
                "investigateKind": "job_id",
                "investigateValue": first_job["job_id"],
            },
        ).json()
        assert child_page["summary"]["limit"] == 2
        assert child_page["summary"]["offset"] == 0
        assert len(child_page["records"]) <= 2
        assert child_page["summary"]["total_records"] >= len(child_page["records"])
        assert child_page["records"]
        first_child = child_page["records"][0]
        assert first_child["stage"]
        assert first_child["operation_type"]
        filtered_job = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/jobs",
            params={
                "range": "all",
                "stage": first_child["stage"],
                "operationType": first_child["operation_type"],
                "investigateKind": "job_id",
                "investigateValue": first_job["job_id"],
                "limit": 1,
                "offset": 0,
            },
        ).json()
        assert filtered_job["summary"]["total_records"] == 1
        assert filtered_job["records"][0]["job_id"] == first_job["job_id"]
        mismatched_job = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/jobs",
            params={
                "range": "all",
                "stage": first_child["stage"],
                "operationType": "__no_matching_operation__",
                "investigateKind": "job_id",
                "investigateValue": first_job["job_id"],
                "limit": 1,
                "offset": 0,
            },
        ).json()
        assert mismatched_job["summary"]["total_records"] == 0
        if child_page["summary"]["total_records"] > 2:
            next_child_page = client.get(
                f"/api/v1/environments/{env['id']}/monitoring/dataflows",
                params={
                    "limit": 2,
                    "offset": 2,
                    "range": "all",
                    "sortBy": "duration_seconds",
                    "sortDir": "desc",
                    "investigateKind": "job_id",
                    "investigateValue": first_job["job_id"],
                },
            ).json()
            first_ids = {row["dataflow_run_id"] for row in child_page["records"]}
            next_ids = {row["dataflow_run_id"] for row in next_child_page["records"]}
            assert first_ids.isdisjoint(next_ids)

        from datacoolie_studio.domains.monitoring import query_service

        assert not hasattr(query_service, "_monitoring_rows")
        default_overview_report = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/overview?range=30d"
        ).json()
        environment_overview = client.get(
            f"/api/v1/environments/{env['id']}/monitoring/pages/environment-overview"
        ).json()
        assert environment_overview["summary"]["job_records"] == default_overview_report["summary"]["job_records"]
        assert environment_overview["operations"]["kpis"]["total_failures"] == default_overview_report["operations"]["kpis"]["total_failures"]
        assert environment_overview["operations"]["dataflow_kpis"]["success_rate"] == default_overview_report["operations"]["dataflow_kpis"]["success_rate"]
        assert environment_overview["operations"]["jobs_by_date_status"] == default_overview_report["operations"]["jobs_by_date_status"]
        assert environment_overview["performance"]["slowest_dataflows"] == []
        assert environment_overview["maintenance"]["per_table"] == []

        overview_summary = client.get(f"/api/v1/environments/{env['id']}/overview")
        assert overview_summary.status_code == 200, overview_summary.text
        overview_payload = overview_summary.json()
        assert overview_payload["monitoring"]["job_records"] == environment_overview["summary"]["job_records"]
        assert overview_payload["monitoring"]["total_failures"] == environment_overview["operations"]["kpis"]["total_failures"]
        assert overview_payload["monitoring"]["dataflow_success_rate"] == environment_overview["operations"]["dataflow_kpis"]["success_rate"]
        assert overview_payload["monitoring"].get("latest_log_at") == environment_overview["summary"].get("latest_log_at")
        assert overview_payload["monitoring"]["date_range"] == environment_overview["summary"]["date_range"]
        assert overview_payload["monitoring"]["errors"] == environment_overview["errors"]

        assert report["health"]["status"]
        assert isinstance(report["attention"], list)
        # The fixture's failed run is intentionally older than the default 30-day
        # monitoring window.  Assert the failure taxonomy against the explicit
        # 90-day report so the test remains deterministic as the calendar moves.
        assert weekly_report["failures"]["error_categories"]
        assert {
            item["category"] for item in weekly_report["failures"]["error_categories"]
        } != {"Unspecified"}
        assert volume_report["volume"]["rows_by_date"]
        assert volume_report["volume"]["bytes_by_date"]
        assert volume_report["volume"]["kpis"]["total_rows_written"] is None
        assert "total_est_rows_written" not in volume_report["volume"]["kpis"]
        assert any(item.get("rows_written") is None for item in volume_report["volume"]["rows_by_date"])
        assert evidence["records"]
        assert "performance" not in report
        assert client.get(
            f"/api/v1/environments/{env['id']}/monitoring/overview"
        ).status_code == 404


def test_environment_overview_failure_windows_and_cache_upgrade(
    studio_client, studio_paths, monkeypatch
):
    import duckdb
    from datetime import datetime, timezone

    from datacoolie_studio.domains.analytics import cache_state, publication, schema, schema_tables
    from datacoolie_studio.domains.monitoring import log_repository, query_service
    from datacoolie_studio.domains.overview import service as overview_service
    from tests.support.v6_records import insert_native_rows, native_record

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 10, 12, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(query_service, "datetime", FrozenDateTime)
    project = studio_client.post("/api/v1/projects", json={"name": "failure-windows"}).json()
    environment = studio_client.post(
        f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}
    ).json()
    logs = studio_paths.data_dir / "logs"
    logs.mkdir()
    source = studio_client.post(
        f"/api/v1/environments/{environment['id']}/log-sources",
        json={"uri": str(logs), "label": "logs"},
    ).json()
    job_rows = [
        (job_id, "job_run_log", "{}", native_record({
            "_type": "job_run_log", "job_id": job_id, "status": status, "end_time": event_time,
        }))
        for job_id, status, event_time in [
            ("recent", "failed", "2026-07-09T12:00:00Z"),
            ("historical", "failed", "2026-04-01T12:00:00Z"),
            ("historical-success", "succeeded", "2026-04-01T12:00:00Z"),
            ("too-old", "failed", "2025-01-01T12:00:00Z"),
        ]
    ]
    with duckdb.connect(str(studio_paths.analytics_path)) as connection:
        schema_tables.ensure_tables(connection)
        for table, types in [(schema.DATAFLOW_TABLE, schema.DATAFLOW_COLUMN_TYPES),
                             (schema.JOB_TABLE, schema.JOB_COLUMN_TYPES)]:
            schema_tables.ensure_typed_table(connection, table, types)
        insert_native_rows(connection, schema.JOB_TABLE, source["id"], job_rows, schema.JOB_COLUMN_TYPES)
        cache_state.mark_cache_source(connection, source["id"], refreshed_at=datetime.now(timezone.utc))
        publication.publish_generation(
            connection, dataflow_column_types=schema.DATAFLOW_COLUMN_TYPES,
            job_column_types=schema.JOB_COLUMN_TYPES, published_at=datetime.now(timezone.utc),
        )

    url = f"/api/v1/environments/{environment['id']}/overview"
    # Seed a legacy producer's truncated result without changing source state.
    with monkeypatch.context() as legacy:
        legacy.setattr(overview_service, "OVERVIEW_CALCULATOR_VERSION", "environment-overview-v4-v6-metrics")
        legacy.setattr(overview_service, "_load_monitoring_summary", lambda *_: {
            **overview_service._empty_monitoring_summary(errors=[]),
            "failed_job_windows": {"last7": 1, "last30": 1, "last365": 1},
        })
        old_response = studio_client.get(url)
        assert old_response.status_code == 200, old_response.text
        assert old_response.json()["monitoring"]["failed_job_windows"]["last365"] == "1"

    first = studio_client.get(url)
    assert first.status_code == 200, first.text
    assert first.json()["cache"]["state"] == "miss"
    assert first.json()["monitoring"]["job_records"] == "1"
    assert first.json()["monitoring"]["total_failures"] == "1"
    assert first.json()["monitoring"]["failed_job_windows"] == {
        "last7": "1", "last30": "1", "last365": "2",
    }

    def reject_summary_query(*_args, **_kwargs):
        raise AssertionError("Warm Overview must reuse its corrected summary")

    monkeypatch.setattr(log_repository, "cached_monitoring_summary", reject_summary_query)
    second = studio_client.get(url)
    assert second.status_code == 200, second.text
    assert second.json()["cache"]["state"] == "hit"
    assert second.json()["monitoring"] == first.json()["monitoring"]


def test_environment_overview_summary_cache_and_lineage_projection(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps({
            "connections": [{
                "name": "lake",
                "connection_type": "lakehouse",
                "format": "delta",
                "catalog": "main",
                "database": "warehouse",
            }],
            "dataflows": [{
                "name": "load_orders",
                "stage": "bronze",
                "load_type": "append",
                "source": {"connection_name": "lake", "schema_name": "raw", "table": "orders"},
                "destination": {"connection_name": "lake", "schema_name": "bronze", "table": "orders"},
            }],
            "schema_hints": [],
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(db_path.parent))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.domains.lineage import service as lineage_service
    from datacoolie_studio.domains.overview import service as overview_service
    from datacoolie_studio.main import app

    calls = 0
    original_summary_builder = overview_service.build_lineage_overview_summary

    def count_summary_builds(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_summary_builder(*args, **kwargs)

    def reject_full_graph_builder(*_args, **_kwargs):
        raise AssertionError("Environment Overview must not call the full Lineage graph builder")

    monkeypatch.setattr(overview_service, "build_lineage_overview_summary", count_summary_builds)
    monkeypatch.setattr(lineage_service, "load_or_build_lineage_graph", reject_full_graph_builder)

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "overview-demo"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        source = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={"uri": str(metadata_path), "label": "metadata"},
        ).json()

        first = client.get(f"/api/v1/environments/{environment['id']}/overview")
        assert first.status_code == 200, first.text
        payload = first.json()
        assert payload["schema_version"] == "environment-overview.v2"
        assert payload["cache"]["state"] == "miss"
        assert payload["metadata"]["dataflows"] == 1
        assert payload["metadata"]["stages"] == [{"name": "bronze", "count": 1}]
        assert payload["lineage"]["dataflows"] == 1
        assert set(payload["lineage"]) >= {
            "automatic_references",
            "manual_references",
            "unresolved_references",
            "automatic_dependencies",
            "manual_dependencies",
            "unresolved_dependencies",
        }
        assert calls == 1

        second = client.get(f"/api/v1/environments/{environment['id']}/overview")
        assert second.status_code == 200, second.text
        assert second.json()["cache"]["state"] == "hit"
        assert calls == 1

        changed = client.patch(
            f"/api/v1/environments/{environment['id']}/metadata-sources/{source['id']}",
            json={"label": "renamed metadata"},
        )
        assert changed.status_code == 200, changed.text
        third = client.get(f"/api/v1/environments/{environment['id']}/overview")
        assert third.status_code == 200, third.text
        assert third.json()["cache"]["state"] == "miss"
        assert calls == 2

        mapping = client.post(
            f"/api/v1/projects/{project['id']}/reference-mappings",
            json={
                "reference_type": "table_reference",
                "reference_value": "orders",
                "target_identifier_kind": "logical_table",
                "target_value": "catalog:main:warehouse|main.warehouse.raw.orders",
            },
        )
        assert mapping.status_code == 200, mapping.text
        after_mapping = client.get(f"/api/v1/environments/{environment['id']}/overview")
        assert after_mapping.status_code == 200, after_mapping.text
        assert after_mapping.json()["cache"]["state"] == "miss"
        assert calls == 3

    with sqlite3.connect(tmp_path / "cache" / "read-models.sqlite3") as connection:
        assert connection.execute("select count(*) from result_cache_entries where namespace = 'environment-overview'").fetchone()[0] == 1
