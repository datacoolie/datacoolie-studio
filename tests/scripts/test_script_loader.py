from __future__ import annotations

import sys


def test_script_module_loader_uses_unique_names_and_restores_modules(script_module_loader):
    first = script_module_loader("scripts/verify.py", "verify")
    second = script_module_loader("scripts/verify.py", "verify")

    assert first.__name__ != second.__name__
    assert sys.modules[first.__name__] is first
    assert sys.modules[second.__name__] is second

    first_name = first.__name__
    second_name = second.__name__
    script_module_loader.close()

    assert first_name not in sys.modules
    assert second_name not in sys.modules
