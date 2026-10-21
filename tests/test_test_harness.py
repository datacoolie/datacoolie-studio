from __future__ import annotations

from pathlib import Path

from tests.support.workspace import ProjectEnvironment, StudioTestPaths


def test_studio_paths_are_unique_and_factory_uses_the_isolated_workspace(
    studio_paths: StudioTestPaths,
    studio_project_factory,
):
    created = studio_project_factory(project_name="fixture-project")

    assert isinstance(created, ProjectEnvironment)
    assert studio_paths.data_dir.is_dir()
    assert studio_paths.database_path.parent == studio_paths.data_dir / "db"
    assert Path(studio_paths.database_path).is_file()
    assert studio_paths.data_dir != Path.home() / ".datacoolie" / "datacoolie-studio"


def test_studio_client_owns_application_lifecycle(studio_client):
    response = studio_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
