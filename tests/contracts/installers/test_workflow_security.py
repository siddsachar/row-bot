"""The GitHub workflows keep secrets out of reach and main's CI result honest.

No workflow runs pull-request code with repository secrets (pull_request_target),
workflows started by events rather than by a person declare their token
permissions, third-party actions are pinned to a release, only the jobs that
sign or publish read secrets, and `CI / ci-ok` passes only when every required
job did (B196).
"""
from __future__ import annotations

from pathlib import Path
import re

import pytest
import yaml


pytestmark = [pytest.mark.contract, pytest.mark.installer]

WORKFLOWS = sorted(Path(".github/workflows").glob("*.yml"))
ACTIONS = sorted(Path(".github/actions").glob("*/action.yml"))
EVENT_TRIGGERS = {"pull_request", "push", "schedule", "release", "workflow_run"}
# Jobs allowed to read secrets: signing, notarization, publishing, and the
# workflow token for labels and the update manifest.
SECRET_READERS = {
    ("container.yml", "release-image"),
    ("container.yml", "release-manifest"),
    ("labels.yml", "sync"),
    ("notarize-check.yml", "check"),
    ("notarize-submit.yml", "submit"),
    ("release.yml", "build-macos"),
    ("update-manifest.yml", "manifest"),
}


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _triggers(workflow: dict) -> set[str]:
    on = workflow["on"]
    return {on} if isinstance(on, str) else set(on)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_no_workflow_runs_pull_request_code_with_secrets(path: Path) -> None:
    assert "pull_request_target" not in _triggers(_load(path))


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_event_triggered_workflows_declare_their_token_permissions(path: Path) -> None:
    workflow = _load(path)
    if _triggers(workflow) & EVENT_TRIGGERS:
        assert "permissions" in workflow
        assert "write-all" not in str(workflow["permissions"])


def test_third_party_actions_are_pinned_to_a_release() -> None:
    for path in [*WORKFLOWS, *ACTIONS]:
        for reference in re.findall(r"uses:\s*([^\s#]+)", path.read_text(encoding="utf-8")):
            if reference.startswith("./"):
                continue
            action, _, ref = reference.partition("@")
            assert re.fullmatch(r"v\d+(\.\d+)*|[0-9a-f]{40}", ref), f"{path.name}: {reference}"


def test_only_signing_and_publishing_jobs_read_secrets() -> None:
    readers = {
        (path.name, name)
        for path in WORKFLOWS
        for name, job in _load(path).get("jobs", {}).items()
        if "secrets." in str(job)
    }
    assert readers <= SECRET_READERS


def test_ci_ok_passes_only_when_every_required_job_did() -> None:
    workflow = _load(Path(".github/workflows/ci.yml"))
    jobs = workflow["jobs"]
    gate = jobs["ci-ok"]

    assert gate["if"] == "always()"
    # Browser smoke stays optional until it has a week of green runs.
    assert set(gate["needs"]) == set(jobs) - {"ci-ok", "browser-smoke"}
    assert all("needs" not in job for name, job in jobs.items() if name != "ci-ok")
    assert "success|skipped" in gate["steps"][0]["run"]
    # A push to main is never cancelled, so every main commit gets a result.
    assert workflow["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
