from __future__ import annotations

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

    assert release["on"] == {"push": {"tags": ["v*"]}}
    assert "workflow_call" in ci["on"]
    assert "tags" not in ci["on"]["push"]
    assert publisher["on"] == {"workflow_call": ""}
    assert release["jobs"]["verify"]["uses"] == "./.github/workflows/ci.yml"
    assert release["jobs"]["publish"]["needs"] == "verify"
    assert release["jobs"]["publish"]["uses"] == "./.github/workflows/publish-pypi.yml"
    assert release["jobs"]["github-release"]["needs"] == "publish"

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
    assert publisher["jobs"]["publish"]["environment"]["name"] == "pypi"
    assert publisher["jobs"]["publish"]["permissions"]["id-token"] == "write"


def test_ci_installs_locked_dependencies_and_keeps_release_checks():
    ci = workflow("ci.yml")
    for job_name in ("backend", "release"):
        commands = "\n".join(step.get("run", "") for step in ci["jobs"][job_name]["steps"])
        assert "uv sync --locked --all-extras" in commands
        assert "uv run --no-sync python scripts/verify.py" in commands
        assert "pip install --upgrade" not in commands
    commands = "\n".join(step.get("run", "") for step in ci["jobs"]["release"]["steps"])
    assert "npm ci" in commands
    assert "npm run test:lifecycle" in commands


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
    assert "python -m build" not in commands
    assert "scripts/verify.py release" not in commands
