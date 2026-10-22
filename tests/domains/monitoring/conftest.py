import pytest


@pytest.fixture
def monitoring_workspace(studio_paths):
    """Initialize schema only for Monitoring tests that access Studio services."""
    from datacoolie_studio.db.session import init_db
    init_db()
