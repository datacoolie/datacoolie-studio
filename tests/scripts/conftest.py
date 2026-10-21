from __future__ import annotations

import pytest

from tests.scripts.script_loader import ScriptModuleLoader


@pytest.fixture
def script_module_loader():
    loader = ScriptModuleLoader()
    yield loader
    loader.close()
