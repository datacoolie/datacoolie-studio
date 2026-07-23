from datetime import timezone
from pathlib import Path

from benchmarks.monitoring_fixture import build_analytics_fixture
from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.logs import cache as logs_cache
from datacoolie_studio.domains.monitoring import page_service
from datacoolie_studio.domains.monitoring import service as monitoring_service


def test_diagnostics_page_uses_bounded_sql_aggregates(tmp_path: Path, monkeypatch):
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setattr(logs_cache, "analytics_database_path", lambda: analytics_path)
    build_analytics_fixture(analytics_path, source_ids=[7, 8], dataflow_rows=250)
    paths = [
        EnvironmentSource(
            id=source_id,
            environment_id=1,
            source_kind="logs",
            uri=f"benchmark://fixture/{source_id}",
            enabled=True,
        )
        for source_id in (7, 8)
    ]

    assert not hasattr(monitoring_service, "_monitoring_rows")
    original_connect = logs_cache.analytics_connections.connect
    connection_calls = 0

    def counted_connect(*args, **kwargs):
        nonlocal connection_calls
        connection_calls += 1
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(logs_cache.analytics_connections, "connect", counted_connect)
    with logs_cache.analytics_reader(paths) as analytics_context:
        payload = page_service._build_monitoring_page(
            paths,
            page="diagnostics",
            filters={"range": "all"},
            session=object(),
            timezone_info=timezone.utc,
            timezone_label="UTC",
            timezone_source="configured",
            analytics_context=analytics_context,
        )

    assert connection_calls == 1
    assert payload["summary"]["dataflow_records"] == 250
    assert payload["summary"]["job_records"] == 50
    assert payload["coverage"]["enabled_log_paths"] == 2
    assert payload["diagnostics"]["job_linkage_summary"]
    assert len(payload["diagnostics"]["field_completeness"]) == 12
    required_groups = [
        row for row in payload["diagnostics"]["field_completeness"]
        if row["actionable"]
    ]
    expected_required_rate = round(
        100 * sum(row["present_values"] for row in required_groups)
        / sum(row["records"] * row["required_fields"] for row in required_groups),
        2,
    )
    assert payload["diagnostics"]["kpis"]["field_readiness_rate"] == expected_required_rate
    assert {row["source"] for row in payload["diagnostics"]["source_coverage"]} == {
        "source:7",
        "source:8",
    }
    assert all("dataflow_records" in row for row in payload["diagnostics"]["record_evidence_by_date"])
