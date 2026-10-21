from __future__ import annotations

import os
import subprocess
import sys

import pytest
import yaml

from tests.support.paths import REPOSITORY_ROOT


def workflow(name: str) -> dict:
    return yaml.load(
        (REPOSITORY_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def test_tag_publication_requires_the_same_run_verification_artifact():
    release = workflow("release.yml")
    ci = workflow("ci.yml")
    publisher = workflow("publish-pypi.yml")

    assert release["on"]["workflow_call"]["inputs"]["tag"]["required"] == "true"
    assert "workflow_call" in ci["on"]
    assert "tags" not in ci["on"]["push"]
    assert "workflow_call" not in publisher["on"]
    assert publisher["on"]["push"] == {"tags": ["v*"]}
    assert publisher["on"]["workflow_dispatch"]["inputs"]["tag"]["required"] == "true"
    assert publisher["jobs"]["verify"]["uses"] == "./.github/workflows/ci.yml"
    assert publisher["jobs"]["verify"]["if"] == "github.event_name != 'workflow_dispatch' || github.ref == 'refs/heads/main'"
    assert publisher["jobs"]["publish"]["needs"] == "verify"
    assert publisher["jobs"]["github-release"]["needs"] == "publish"
    assert publisher["jobs"]["github-release"]["uses"] == "./.github/workflows/release.yml"

    upload = next(
        step for step in ci["jobs"]["release"]["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    download = next(
        step for step in publisher["jobs"]["publish"]["steps"]
        if step.get("uses", "").startswith("actions/download-artifact@")
    )
    assert upload["with"]["name"] == download["with"]["name"] == "python-distributions"
    assert download["with"]["path"] == "dist"
    assert "run-id" not in download["with"]
    assert "repository" not in download["with"]
    assert publisher["jobs"]["verify"]["with"]["ref"] == "${{ inputs.tag || github.ref }}"
    checkout = publisher["jobs"]["publish"]["steps"][0]
    assert checkout["with"]["ref"] == publisher["jobs"]["verify"]["with"]["ref"]
    assert ci["jobs"]["release"]["steps"][0]["with"]["ref"] == "${{ inputs.ref || github.ref }}"
    assert release["jobs"]["github-release"]["steps"][0]["with"]["tag_name"] == "${{ inputs.tag }}"
    assert publisher["jobs"]["publish"]["environment"]["name"] == "pypi"
    assert publisher["jobs"]["publish"]["permissions"]["id-token"] == "write"


def test_ci_installs_locked_dependencies_and_keeps_release_checks():
    ci = workflow("ci.yml")
    for job_name in ci["jobs"]:
        commands = "\n".join(step.get("run", "") for step in ci["jobs"][job_name]["steps"])
        assert "uv sync --locked --all-extras" in commands
        assert "uv run --no-sync python scripts/verify.py" in commands
        assert "pip install --upgrade" not in commands
    commands = "\n".join(step.get("run", "") for step in ci["jobs"]["release"]["steps"])
    assert "npm ci" in commands
    assert "npm run test:lifecycle" in commands


def test_ci_runs_backend_once_on_python_311_and_keeps_full_release_scope(script_module_loader):
    ci = workflow("ci.yml")
    assert set(ci["jobs"]) == {"release"}
    job = ci["jobs"]["release"]
    assert "strategy" not in job
    steps = job["steps"]
    python = next(step for step in steps if step.get("uses", "").startswith("actions/setup-python@"))
    assert python["with"]["python-version"] == "3.11"
    verification = next(step for step in steps if "scripts/verify.py" in step.get("run", ""))
    assert verification["run"] == "python -m uv run --no-sync python scripts/verify.py release"
    verify = script_module_loader("scripts/verify.py", "studio_ci_verify")
    release_plan = verify.command_plan("release", python="python", npm="npm")
    backend_plan = verify.command_plan("backend", python="python")
    assert release_plan[:len(backend_plan)] == backend_plan
    assert [command.label for command in release_plan].count("Backend deterministic suite") == 1
    assert {command.label for command in release_plan} == {
        "Backend lint", "Backend deterministic suite", "OpenAPI contract", "Generated API types",
        "Frontend suite", "Frontend production build", "Frontend dependency audit",
        "Python lock and dependency audit", "Build distributions", "Check distributions",
        "Smoke-test the built wheel",
    }
    lifecycle = next(step for step in steps if "npm run test:lifecycle" in step.get("run", ""))
    upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert steps.index(verification) < steps.index(lifecycle) < steps.index(upload)
    assert not any(step.get("continue-on-error") == "true" for step in steps)


def test_publisher_checks_version_and_does_not_rebuild_verified_artifacts():
    publisher = workflow("publish-pypi.yml")
    steps = publisher["jobs"]["publish"]["steps"]
    version_index = next(i for i, step in enumerate(steps) if "tomllib" in step.get("run", ""))
    publish_index = next(
        i for i, step in enumerate(steps)
        if step.get("uses", "").startswith("pypa/gh-action-pypi-publish@")
    )
    assert version_index < publish_index
    commands = "\n".join(step.get("run", "") for step in steps)
    assert 'expected_tag = f"v{version}"' in commands
    assert 'refs/tags/{actual_tag}^{{commit}}' in commands
    assert '"--is-ancestor", head, "origin/main"' in commands
    assert 'f"datacoolie_studio-{version}-py3-none-any.whl"' in commands
    assert 'f"datacoolie_studio-{version}.tar.gz"' in commands
    assert "python -m build" not in commands
    assert "scripts/verify.py release" not in commands


@pytest.mark.parametrize("failure", [None, "unstable", "version", "checkout", "ancestry"])
def test_release_guard_uses_requested_tag_during_main_dispatch(tmp_path, failure):
    steps = workflow("publish-pypi.yml")["jobs"]["publish"]["steps"]
    guard = next(step for step in steps if step.get("name") == "Verify release tag matches package version")
    assert guard["env"] == {"STUDIO_RELEASE_TAG": "${{ inputs.tag || github.ref_name }}"}
    script = guard["run"].removeprefix("python - <<'PY'\n").removesuffix("PY\n")

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "--initial-branch=main")
    git("config", "user.name", "Release guard test")
    git("config", "user.email", "release-test@example.invalid")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.1"\n', encoding="utf-8")
    git("add", "pyproject.toml")
    git("commit", "-m", "Release fixture")
    commit = git("rev-parse", "HEAD")
    tag = "v0.2.1" if failure not in {"unstable", "version"} else {"unstable": "main", "version": "v0.2.0"}[failure]
    git("tag", "-a", tag, "-m", "Release fixture")
    git("update-ref", "refs/remotes/origin/main", commit)
    if failure in {"checkout", "ancestry"}:
        git("commit", "--allow-empty", "-m", "Unpublished change")
        if failure == "ancestry":
            git("tag", "-d", tag)
            git("tag", "-a", tag, "-m", "Not on origin/main")
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True,
        env={**os.environ, "GITHUB_REF_NAME": "main", "STUDIO_RELEASE_TAG": tag},
    )
    assert (result.returncode == 0) == (failure is None), result.stdout + result.stderr
