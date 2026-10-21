from __future__ import annotations

import os

from datacoolie_studio.db.session import get_engine
from tests.support.workspace import StudioTestPaths


def test_engine_reconfigures_and_disposes_when_workspace_url_changes(
    studio_paths: StudioTestPaths,
    tmp_path,
):
    first = get_engine()
    second_path = tmp_path / "second" / "studio.db"
    second_path.parent.mkdir()
    os.environ["DATACOOLIE_STUDIO_DATA_DIR"] = str(second_path.parent)
    os.environ["DATACOOLIE_STUDIO_DATABASE_URL"] = f"sqlite:///{second_path.as_posix()}"

    second = get_engine()

    assert first is not second
    assert str(first.url) == f"sqlite:///{studio_paths.database_path.as_posix()}"
    assert str(second.url) == f"sqlite:///{second_path.as_posix()}"
