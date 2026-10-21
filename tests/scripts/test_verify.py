from __future__ import annotations

import subprocess

import pytest


@pytest.fixture
def verify_module(script_module_loader):
    return script_module_loader("scripts/verify.py", "studio_verify")


def test_command_plan_has_stable_group_order_and_scope(tmp_path, verify_module):
    fast = verify_module.command_plan("fast", root=tmp_path, python="python", npm="npm")
    backend = verify_module.command_plan("backend", root=tmp_path, python="python")
    full = verify_module.command_plan("full", root=tmp_path, python="python", npm="npm")
    release = verify_module.command_plan("release", root=tmp_path, python="python", npm="npm")

    assert [command.label for command in fast] == [
        "Backend lint",
        "Backend fast regression",
        "OpenAPI contract",
        "Generated API types",
    ]
    assert [command.label for command in backend] == [
        "Backend lint",
        "Backend deterministic suite",
        "OpenAPI contract",
    ]
    assert [command.label for command in full][-4:] == [
        "Generated API types",
        "Frontend suite",
        "Frontend production build",
        "Frontend dependency audit",
    ]
    assert [command.label for command in release][-3:] == [
        "Build distributions",
        "Check distributions",
        "Smoke-test the built wheel",
    ]
    audit = next(command for command in release if command.label == "Python lock and dependency audit")
    assert audit.argv == ("python", "scripts/audit_python_dependencies.py")
    assert all(command.cwd == tmp_path.resolve() for command in backend)


def test_command_plan_uses_python_module_invocation_and_explicit_test_filter(
    tmp_path,
    verify_module,
):
    backend = verify_module.command_plan("backend", root=tmp_path, python="python")

    assert backend[0].argv[:4] == ("python", "-m", "ruff", "check")
    assert "not live and not benchmark" in backend[1].argv
    assert "scripts" in backend[0].argv


def test_command_plan_preserves_virtualenv_interpreter(monkeypatch, tmp_path, verify_module):
    executable = str(tmp_path / ".venv" / "bin" / "python")
    monkeypatch.setattr(verify_module.sys, "executable", executable)

    def unexpected_resolve(*args, **kwargs):
        raise AssertionError("Resolving a POSIX venv symlink selects base Python")

    monkeypatch.setattr(verify_module.Path, "resolve", unexpected_resolve)
    assert verify_module._python_executable() == executable


def test_runner_stops_on_first_failed_command(monkeypatch, tmp_path, verify_module):
    command = verify_module.Command(
        "failing command",
        ("python", "-c", "raise SystemExit(7)"),
        tmp_path,
    )
    monkeypatch.setattr(
        verify_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 7),
    )

    with pytest.raises(RuntimeError, match="exit code 7"):
        verify_module._run(command)


def test_windows_prefers_npm_cmd(monkeypatch, verify_module):
    monkeypatch.setattr(verify_module.os, "name", "nt")
    monkeypatch.setattr(
        verify_module.shutil,
        "which",
        lambda executable: "C:/node/npm.cmd" if executable == "npm.cmd" else None,
    )

    assert verify_module._npm_executable() == "C:/node/npm.cmd"


def test_distribution_expansion_ignores_non_artifact_files(tmp_path, verify_module):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / ".gitignore").write_text("*\n", encoding="utf-8")
    wheel = dist / "datacoolie_studio-0.2.0-py3-none-any.whl"
    source = dist / "datacoolie_studio-0.2.0.tar.gz"
    wheel.write_bytes(b"wheel")
    source.write_bytes(b"source")

    command = verify_module.Command("distribution check", ("twine", "check", "dist/*"), tmp_path)

    assert verify_module._resolved_argv(command) == (
        "twine",
        "check",
        str(wheel),
        str(source),
    )


def test_distribution_expansion_selects_current_project_version(tmp_path, verify_module):
    dist = tmp_path / "dist"
    dist.mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "datacoolie-studio"\nversion = "0.2.0"\n',
        encoding="utf-8",
    )
    old_wheel = dist / "datacoolie_studio-0.1.10-py3-none-any.whl"
    current_wheel = dist / "datacoolie_studio-0.2.0-py3-none-any.whl"
    current_source = dist / "datacoolie_studio-0.2.0.tar.gz"
    old_wheel.write_bytes(b"old")
    current_wheel.write_bytes(b"current")
    current_source.write_bytes(b"current source")

    command = verify_module.Command("distribution check", ("twine", "check", "dist/*"), tmp_path)

    assert verify_module._resolved_argv(command) == (
        "twine",
        "check",
        str(current_wheel),
        str(current_source),
    )
