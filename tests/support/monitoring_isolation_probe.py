"""Subprocess-only guards for Monitoring's explicit workspace lifecycle."""
from __future__ import annotations

import os
from pathlib import Path
import sys

import duckdb
import pytest

from datacoolie_studio.db import session
from datacoolie_studio.domains.read_models import database


KEYS = (
    "DATACOOLIE_STUDIO_DATA_DIR",
    "DATACOOLIE_STUDIO_DATABASE_URL",
    "DATACOOLIE_STUDIO_RESULT_CACHE_URL",
)
_workspace_allowed = False
_workspaces: set[str] = set()


def _audit(event, args):
    if event != "sqlite3.connect":
        return
    assert _workspace_allowed, f"Fixture-free Monitoring test opened SQLite: {args[0]}"
    if str(args[0]) not in ("", ":memory:"):
        targets = [os.environ[key].removeprefix("sqlite:///") for key in KEYS[1:]]
        assert Path(args[0]).resolve() in [Path(target).resolve() for target in targets], args[0]


def pytest_configure():
    sys.addaudithook(_audit)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item, nextitem):
    global _workspace_allowed
    before = {key: os.environ.get(key) for key in KEYS}
    _workspace_allowed = "studio_paths" in item.fixturenames
    patch = pytest.MonkeyPatch()
    if _workspace_allowed and "monitoring_workspace" not in item.fixturenames:
        def reject_schema(*args, **kwargs):
            raise AssertionError("Paths-only Monitoring test initialized workspace schema")

        patch.setattr(session, "init_db", reject_schema)
    if not _workspace_allowed:
        def reject_workspace(*args, **kwargs):
            raise AssertionError("Fixture-free Monitoring test accessed a Studio workspace")

        patch.setattr(session, "create_session", reject_workspace)
        patch.setattr(session, "init_db", reject_workspace)
        patch.setattr(database, "get_result_cache_engine", reject_workspace)
        connect = duckdb.connect

        def memory_connect(*args, **kwargs):
            target = args[0] if args else kwargs.get("database", ":memory:")
            assert target in ("", ":memory:"), f"Pure Monitoring test opened DuckDB: {target}"
            return connect(*args, **kwargs)

        patch.setattr(duckdb, "connect", memory_connect)
        if "test_log_v6_status_message.py" in item.nodeid:
            patch.setattr(duckdb, "connect", reject_workspace)
    try:
        result = yield
    finally:
        patch.undo()
        _workspace_allowed = False
        assert {key: os.environ.get(key) for key in KEYS} == before, item.nodeid
        assert session._engine is None, item.nodeid
        assert database._engine is None, item.nodeid
        assert not Path(before[KEYS[0]]).exists(), item.nodeid
    return result


def pytest_runtest_call(item):
    if "studio_paths" not in item.fixturenames:
        return
    workspace = os.environ[KEYS[0]]
    assert workspace not in _workspaces, "Monitoring tests shared a writable workspace"
    _workspaces.add(workspace)
