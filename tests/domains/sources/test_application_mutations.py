"""Application callers share the HTTP mutation invariants."""

import pytest

from datacoolie_studio.db.models import Environment, EnvironmentSource, Project
from datacoolie_studio.db.session import create_session, init_db
from datacoolie_studio.domains.sources import application
from datacoolie_studio.domains.sources.persistence import create_source
from datacoolie_studio.domains.sync.service import source_refresh_guard


@pytest.fixture
def log_source(studio_paths, tmp_path):
    init_db()
    session = create_session()
    environment = Environment(name="dev", project=Project(name="application-mutations"))
    session.add(environment)
    session.flush()
    source = create_source(session, environment.id, source_kind="logs", uri=str(tmp_path / "logs"), label="keep", enabled=True)
    session.commit()
    yield session, environment.id, source.id
    session.close()


def test_direct_log_update_distinguishes_omission_and_null(log_source):
    session, environment_id, source_id = log_source
    assert application.update_log_source(session, environment_id, source_id, enabled=False).label == "keep"
    assert application.update_log_source(session, environment_id, source_id, label=None).label is None


@pytest.mark.parametrize("commit_fails", [False, True])
def test_log_delete_holds_guard_through_commit_and_releases_on_error(log_source, monkeypatch, commit_fails):
    session, environment_id, source_id = log_source
    with source_refresh_guard(source_id) as acquired:
        assert acquired
        with pytest.raises(application.SourceRefreshBusyError):
            application.delete_log_source(session, environment_id, source_id)
        assert session.get(EnvironmentSource, source_id) is not None
    original_commit = session.commit
    def guarded_commit():
        with source_refresh_guard(source_id) as acquired:
            assert not acquired
        if commit_fails:
            raise OSError("commit failure")
        original_commit()
    monkeypatch.setattr(session, "commit", guarded_commit)
    if commit_fails:
        with pytest.raises(OSError, match="commit failure"):
            application.delete_log_source(session, environment_id, source_id)
        session.rollback()
        assert session.get(EnvironmentSource, source_id) is not None
    else:
        assert application.delete_log_source(session, environment_id, source_id)
    with source_refresh_guard(source_id) as acquired:
        assert acquired
