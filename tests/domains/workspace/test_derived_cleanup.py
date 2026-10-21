from __future__ import annotations

import logging
from pathlib import Path

import duckdb

from datacoolie_studio.db import session as db_session
from datacoolie_studio.db.models import Environment, EnvironmentSource, Project
from datacoolie_studio.domains.analytics import maintenance as analytics_maintenance
from datacoolie_studio.domains.workspace import service as workspace
from datacoolie_studio.domains.workspace.derived_cleanup import (
    reconcile_orphaned_analytics_cache,
)
from tests.support.monitoring_fixture import build_analytics_fixture


def test_delete_commits_when_cache_purge_fails_and_reconciliation_repairs_orphans(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    db_path = tmp_path / "studio.db"
    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(analytics_maintenance, "analytics_database_path", lambda: analytics_path)
    db_session.init_db()

    with db_session.create_session() as session:
        project = Project(name="cleanup-project")
        session.add(project)
        session.flush()
        environment = Environment(project_id=project.id, name="dev")
        session.add(environment)
        session.flush()
        source = EnvironmentSource(
            environment_id=environment.id,
            source_kind="logs",
            uri="logs",
        )
        session.add(source)
        session.commit()
        environment_id = environment.id
        source_id = source.id

        build_analytics_fixture(
            analytics_path,
            source_ids=[source_id, 999_999],
            dataflow_rows=4,
        )

        real_purge = analytics_maintenance.purge_source_ids

        def fail_purge(_source_ids: list[int]):
            raise RuntimeError("temporary analytics lock")

        monkeypatch.setattr(analytics_maintenance, "purge_source_ids", fail_purge)
        with caplog.at_level(logging.WARNING, logger="datacoolie_studio.domains.workspace.derived_cleanup"):
            assert workspace.delete_environment(session, environment_id) is True

        assert session.get(Environment, environment_id) is None
        assert "analytics-cache-purge" in caplog.text
        assert "RuntimeError" in caplog.text
        assert "temporary analytics lock" not in caplog.text

        with duckdb.connect(str(analytics_path), read_only=True) as connection:
            cached_source_ids = {
                int(row[0])
                for row in connection.execute(
                    "SELECT source_id FROM etl_cache_sources ORDER BY source_id"
                ).fetchall()
            }
        assert cached_source_ids == {source_id, 999_999}

        monkeypatch.setattr(analytics_maintenance, "purge_source_ids", real_purge)
        reconciliation = reconcile_orphaned_analytics_cache(session)

        assert reconciliation["status"] == "ok"
        assert reconciliation["orphan_source_ids"] == [source_id, 999_999]
        with duckdb.connect(str(analytics_path), read_only=True) as connection:
            assert connection.execute("SELECT count(*) FROM etl_cache_sources").fetchone()[0] == 0
            for table_name in (
                "etl_dataflow_runs",
                "etl_job_runs",
                "etl_monitoring_filter_values",
            ):
                assert connection.execute(f"SELECT count(*) FROM {table_name}").fetchone()[0] == 0
