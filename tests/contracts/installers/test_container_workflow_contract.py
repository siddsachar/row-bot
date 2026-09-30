from __future__ import annotations

from pathlib import Path

import pytest
import yaml


pytestmark = [pytest.mark.contract, pytest.mark.installer]

WORKFLOW = Path(".github/workflows/container.yml")


def _source() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _workflow() -> dict[str, object]:
    return yaml.load(_source(), Loader=yaml.BaseLoader)


def test_container_workflow_has_only_review_and_release_triggers() -> None:
    workflow = _workflow()
    triggers = workflow["on"]

    assert set(triggers) == {"pull_request", "workflow_dispatch", "release"}
    assert triggers["release"] == {"types": ["published"]}
    assert "push" not in triggers


def test_verification_build_loads_and_smokes_without_registry_write() -> None:
    workflow = _workflow()
    job = workflow["jobs"]["verify-image"]
    steps = job["steps"]
    build = next(step for step in steps if step.get("name") == "Build native verification image")
    smoke = next(step for step in steps if step.get("name") == "Smoke native verification image")

    assert workflow["permissions"] == {"contents": "read"}
    assert "permissions" not in job
    assert job["if"] == "${{ github.event_name != 'release' }}"
    assert build["uses"] == "docker/build-push-action@v6"
    assert build["with"]["load"] == "true"
    assert build["with"]["push"] == "false"
    assert build["with"]["provenance"] == "false"
    assert build["with"]["sbom"] == "false"
    assert "scripts/smoke_docker_server.py" in smoke["run"]
    assert not any("docker/login-action" in step.get("uses", "") for step in steps)
    assert not any("docker push" in step.get("run", "") for step in steps)


def test_release_job_logs_in_only_after_the_smoke_with_the_workflow_token() -> None:
    workflow = _workflow()
    job = workflow["jobs"]["release-image"]
    steps = job["steps"]
    source = _source()
    names = [step.get("name", step.get("uses", "")) for step in steps]

    assert job["if"] == "${{ github.event_name == 'release' }}"
    assert job["permissions"] == {"contents": "read", "packages": "write"}
    assert names.index("Build native release image") < names.index(
        "Smoke native release image"
    )
    assert names.index("Smoke native release image") < names.index(
        "Log in to GHCR after smoke"
    )
    assert names.index("Log in to GHCR after smoke") < names.index(
        "Push architecture-specific temporary tag"
    )
    assert "password: ${{ secrets.GITHUB_TOKEN }}" in source
    assert "PAT" not in source


def test_manifest_job_publishes_only_on_a_release() -> None:
    job = _workflow()["jobs"]["release-manifest"]

    assert job["needs"] == "release-image"
    assert job["if"] == "${{ github.event_name == 'release' }}"
    assert job["permissions"] == {"contents": "read", "packages": "write"}
