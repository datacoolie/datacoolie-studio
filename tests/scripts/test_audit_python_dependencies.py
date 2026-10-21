from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def test_audit_exports_locked_hashed_dependencies_and_cleans_up(monkeypatch, script_module_loader):
    module = script_module_loader("scripts/audit_python_dependencies.py", "studio_audit")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["check"] is True
        if "export" in argv:
            Path(argv[argv.index("--output-file") + 1]).write_text("", encoding="utf-8")
        else:
            assert Path(argv[argv.index("--requirement") + 1]).exists()

    monkeypatch.setattr(module.subprocess, "run", run)
    module.main()

    assert "--locked" in calls[0]
    assert "--all-extras" in calls[0]
    assert "--no-emit-project" in calls[0]
    assert calls[0][calls[0].index("--python") + 1] == module.sys.executable
    assert "--require-hashes" in calls[1]
    assert "--strict" in calls[1]
    assert not Path(calls[1][calls[1].index("--requirement") + 1]).exists()


def test_lock_export_failure_prevents_audit(monkeypatch, script_module_loader):
    module = script_module_loader("scripts/audit_python_dependencies.py", "studio_audit")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        module.main()
    assert len(calls) == 1
