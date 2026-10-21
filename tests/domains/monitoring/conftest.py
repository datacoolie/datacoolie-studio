import pytest


@pytest.fixture(autouse=True)
def monitoring_workspace(studio_paths):
    from datacoolie_studio.db.session import init_db
    init_db()
