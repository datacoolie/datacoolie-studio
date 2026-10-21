"""Run reproducible Studio verification profiles from any checkout."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


GROUPS = ("fast", "backend", "full", "release")
_WHEEL_SMOKE = """
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import venv
from pathlib import Path

with open("pyproject.toml", "rb") as file:
    version = tomllib.load(file)["project"]["version"].replace("-", "_")
wheels = sorted(Path("dist").glob(f"*-{version}-*.whl"))
if len(wheels) != 1:
    raise RuntimeError(f"Expected one wheel for version {version!r}, found {wheels!r}")
wheel = wheels[0]
environment = Path(tempfile.mkdtemp(prefix="datacoolie-studio-wheel-"))
try:
    venv.EnvBuilder(with_pip=True).create(environment)
    executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run([str(executable), "-m", "pip", "install", str(wheel)], check=True)
    subprocess.run([str(executable), "-m", "datacoolie_studio", "--help"], check=True)
    subprocess.run([str(executable), "-c", '''
import importlib.util
from importlib.resources import files
import re
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import duckdb
from datacoolie_studio.main import app
from datacoolie_studio.domains.analytics.log_contract import log_validator
from datacoolie_studio.domains.analytics.schema_compiler import SCHEMA_DOCUMENT
from jsonschema_rs import Draft202012Validator
validator = log_validator()
assert callable(app)
connection = duckdb.connect()
assert connection.execute("SELECT current_timestamp").fetchall()[0][0].tzinfo is not None
connection.create_function('studio_wheel_smoke', lambda value: value, ['BIGINT'], 'BIGINT')
assert connection.execute('SELECT studio_wheel_smoke(7)').fetchall() == [(7,)]
connection.close()
assert SCHEMA_DOCUMENT['$defs']['dataflowRecord']['properties']['log_schema_version']['const'] == 6
dates = Draft202012Validator({'type':'string','format':'date-time'}, validate_formats=True)
assert dates.is_valid('2026-10-04T12:00:00Z')
assert not dates.is_valid('invalid-datetime')
assert importlib.util.find_spec('datacoolie') is None
static = files('datacoolie_studio').joinpath('static')
index = static.joinpath('index.html').read_text(encoding='utf-8')
references = re.findall(r'(?:src|href)="(/assets/[^"]+)"', index)
assert references, 'Packaged frontend entrypoint has no asset references'
for reference in references:
    assert static.joinpath(reference.lstrip('/')).is_file(), reference
print('Offline packaged v6 schema and datetime validation passed')
print('Installed application import, DuckDB timestamps and Python UDF passed')
print(f'Packaged frontend entrypoint resolves all {len(references)} assets')
with tempfile.TemporaryDirectory(prefix='studio-wheel-startup-') as data_dir:
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen([sys.executable, '-m', 'datacoolie_studio',
        '--host', '127.0.0.1', '--port', str(port), '--data-dir', data_dir,
        '--no-open', '--no-banner'])
    try:
        for attempt in range(60):
            if process.poll() is not None:
                raise RuntimeError('Installed application exited before readiness')
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as response:
                    assert json.load(response) == {'status': 'ok'}
                break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.5)
        else:
            raise RuntimeError('Installed application health timeout')
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=5) as response:
            assert b'/assets/' in response.read()
        print('Installed application startup, health and frontend serving passed')
    finally:
        process.terminate()
        process.wait(timeout=20)
'''], check=True, cwd=environment)
finally:
    shutil.rmtree(environment, ignore_errors=True)
""".strip()


@dataclass(frozen=True)
class Command:
    label: str
    argv: tuple[str, ...]
    cwd: Path


def studio_root() -> Path:
    root = Path(__file__).resolve().parents[1]
    if not (root / "pyproject.toml").is_file():
        raise RuntimeError(f"Unable to resolve Studio root from {__file__}")
    return root


def _python_executable() -> str:
    # POSIX venv interpreters may be symlinks; resolving selects base Python.
    return sys.executable


def _npm_executable() -> str:
    candidates = ("npm.cmd", "npm") if os.name == "nt" else ("npm", "npm.cmd")
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise RuntimeError("npm executable was not found on PATH")


def _python_command(python: str, *arguments: str, label: str, cwd: Path) -> Command:
    return Command(label, (python, *arguments), cwd)


def command_plan(
    group: str,
    *,
    root: Path | None = None,
    python: str | None = None,
    npm: str | None = None,
) -> list[Command]:
    """Return the ordered command plan without executing any child process."""

    if group not in GROUPS:
        raise ValueError(f"Unknown verification group: {group}")
    root = (root or studio_root()).resolve()
    frontend = root / "frontend"
    python = python or _python_executable()

    backend_lint = _python_command(
        python,
        "-m",
        "ruff",
        "check",
        "src",
        "tests",
        "scripts",
        label="Backend lint",
        cwd=root,
    )
    backend_tests = _python_command(
        python,
        "-m",
        "pytest",
        "-m",
        "not live and not benchmark",
        label="Backend deterministic suite",
        cwd=root,
    )
    architecture_tests = _python_command(
        python,
        "-m",
        "pytest",
        "tests/architecture/test_architecture_boundaries.py",
        "tests/test_main.py",
        "tests/test_openapi_export.py",
        label="Backend fast regression",
        cwd=root,
    )
    openapi = _python_command(
        python,
        "-m",
        "datacoolie_studio.openapi_export",
        "--check",
        label="OpenAPI contract",
        cwd=root,
    )
    if group == "fast":
        npm = npm or _npm_executable()
        generated_api = Command("Generated API types", (npm, "run", "api:check"), frontend)
        return [backend_lint, architecture_tests, openapi, generated_api]
    if group == "backend":
        return [backend_lint, backend_tests, openapi]

    npm = npm or _npm_executable()
    generated_api = Command("Generated API types", (npm, "run", "api:check"), frontend)
    frontend_tests = Command("Frontend suite", (npm, "test"), frontend)
    frontend_build = Command("Frontend production build", (npm, "run", "build"), frontend)
    frontend_audit = Command(
        "Frontend dependency audit",
        (npm, "audit", "--audit-level=high"),
        frontend,
    )
    commands = [backend_lint, backend_tests, openapi, generated_api, frontend_tests, frontend_build]
    if group == "full":
        return [*commands, frontend_audit]
    return [
        *commands,
        frontend_audit,
        _python_command(
            python,
            "scripts/audit_python_dependencies.py",
            label="Python lock and dependency audit",
            cwd=root,
        ),
        _python_command(python, "-m", "build", label="Build distributions", cwd=root),
        _python_command(
            python,
            "-m",
            "twine",
            "check",
            "dist/*",
            label="Check distributions",
            cwd=root,
        ),
        _python_command(
            python,
            "-c",
            _WHEEL_SMOKE,
            label="Smoke-test the built wheel",
            cwd=root,
        ),
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", choices=GROUPS, nargs="?", default="fast")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the selected command plan without executing child processes",
    )
    return parser


def _resolved_argv(command: Command) -> tuple[str, ...]:
    """Expand the one distribution glob that shells handle differently by platform."""

    resolved: list[str] = []
    for argument in command.argv:
        if argument != "dist/*":
            resolved.append(argument)
            continue
        artifacts = sorted(
            path
            for path in (command.cwd / "dist").glob("*")
            if path.is_file() and path.name.endswith((".whl", ".tar.gz"))
        )
        pyproject = command.cwd / "pyproject.toml"
        if pyproject.is_file():
            with pyproject.open("rb") as file:
                version = str(tomllib.load(file)["project"]["version"]).replace("-", "_")
            artifacts = [
                path
                for path in artifacts
                if f"-{version}-" in path.name or path.name.endswith(f"-{version}.tar.gz")
            ]
        if not artifacts:
            raise RuntimeError(f"No distribution artifacts found under {command.cwd / 'dist'}")
        resolved.extend(str(path) for path in artifacts)
    return tuple(resolved)


def _run(command: Command) -> None:
    argv = _resolved_argv(command)
    print(f"\n==> {command.label}")
    print(f"    {shlex.join(argv)}")
    completed = subprocess.run(argv, cwd=command.cwd, check=False)
    if completed.returncode:
        raise RuntimeError(f"{command.label} failed with exit code {completed.returncode}")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    commands = command_plan(arguments.group)
    if arguments.dry_run:
        for command in commands:
            print(f"{command.label}: {shlex.join(command.argv)} [cwd={command.cwd}]")
        return 0
    for command in commands:
        _run(command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
