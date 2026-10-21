from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

import pytest

from datacoolie_studio.db.session import _dispose_engine, create_session, init_db
from datacoolie_studio.domains.analytics import maintenance as analytics_maintenance
from datacoolie_studio.domains.read_models.database import reset_result_cache_engine
from tests.support.workspace import ProjectEnvironment, StudioTestPaths


_ENVIRONMENT_KEYS = (
    "DATACOOLIE_STUDIO_DATA_DIR",
    "DATACOOLIE_STUDIO_DATABASE_URL",
    "DATACOOLIE_STUDIO_RESULT_CACHE_URL",
)


@pytest.fixture
def studio_paths(tmp_path: Path) -> StudioTestPaths:
    """Provide a unique disposable Studio workspace for one test."""

    data_dir = tmp_path / "studio-data"
    database_path = data_dir / "db" / "studio.db"
    result_cache_path = data_dir / "cache" / "result-cache.db"
    analytics_path = data_dir / "cache" / "analytics.duckdb"
    data_dir.mkdir()
    database_path.parent.mkdir()
    result_cache_path.parent.mkdir()
    previous = {key: os.environ.get(key) for key in _ENVIRONMENT_KEYS}
    _dispose_engine()
    reset_result_cache_engine()
    os.environ["DATACOOLIE_STUDIO_DATA_DIR"] = str(data_dir)
    os.environ["DATACOOLIE_STUDIO_DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"
    os.environ["DATACOOLIE_STUDIO_RESULT_CACHE_URL"] = (
        f"sqlite:///{result_cache_path.as_posix()}"
    )
    try:
        yield StudioTestPaths(
            data_dir=data_dir,
            database_path=database_path,
            result_cache_path=result_cache_path,
            analytics_path=analytics_path,
        )
    finally:
        # Cleanup may touch optional analytics integrations. Always release
        # process-local engines and restore the test environment even when
        # that cleanup reports an error; otherwise the next test can inherit
        # this workspace's cache URL and observe a different SQLite file.
        try:
            analytics_maintenance.clear_cache()
        finally:
            try:
                reset_result_cache_engine()
            finally:
                try:
                    _dispose_engine()
                finally:
                    for key, value in previous.items():
                        if value is None:
                            os.environ.pop(key, None)
                        else:
                            os.environ[key] = value


@pytest.fixture
def studio_client(studio_paths: StudioTestPaths):
    """Run the FastAPI application against the test workspace lifecycle."""

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture
def studio_project_factory(studio_paths: StudioTestPaths) -> Callable[..., ProjectEnvironment]:
    """Build isolated project/environment rows for API and domain scenarios."""

    from datacoolie_studio.db.models import Environment, Project

    def create_project_environment(
        *,
        project_name: str = "test-project",
        environment_name: str = "dev",
    ) -> ProjectEnvironment:
        init_db()
        session = create_session()
        try:
            project = Project(name=project_name)
            session.add(project)
            session.flush()
            environment = Environment(project_id=project.id, name=environment_name)
            session.add(environment)
            session.commit()
            return ProjectEnvironment(
                project_id=project.id,
                environment_id=environment.id,
            )
        finally:
            session.close()

    return create_project_environment
