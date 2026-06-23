from __future__ import annotations

import pytest

from tests.fixtures.developer import fake_workspace


pytestmark = pytest.mark.subsystem


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("python -m pip install rich", "run_install"),
        ("npm install", "run_install"),
        ("curl https://example.com", "run_network"),
        ("python -c \"import requests; requests.get('https://example.com')\"", "run_network"),
        ("python -m http.server 8000", "start_server"),
        ("rm -rf build", "delete"),
        ("git commit -m test", "git_commit"),
        ("git push origin HEAD", "git_push"),
        ("gh pr create --draft", "git_pr"),
        ("python -m pytest", "run_safe_command"),
    ],
)
def test_developer_command_classifier_is_conservative(command: str, expected: str) -> None:
    from row_bot.developer.runtime import classify_command_action

    assert classify_command_action(command) == expected


@pytest.mark.parametrize("action", ["run_install", "delete", "git_commit", "git_push", "git_pr"])
def test_high_risk_developer_actions_require_approval_in_approve_mode(action: str) -> None:
    from row_bot.developer.sandbox import action_needs_explicit_user_intent, decide_action

    decision = decide_action("approve", action)  # type: ignore[arg-type]

    assert decision.requires_approval is True
    assert action_needs_explicit_user_intent(action) is True  # type: ignore[arg-type]


def test_docker_network_policy_blocks_network_and_install_when_off(tmp_path, monkeypatch) -> None:
    from row_bot.developer import runtime

    workspace = fake_workspace(tmp_path, execution_mode="docker", sandbox_network="off")
    monkeypatch.setattr("row_bot.developer.storage.get_workspace", lambda workspace_id: workspace)

    result = runtime.run_workspace_command(
        workspace.path,
        "python -m pip install rich",
        "allow_all",
        workspace_id=workspace.id,
        thread_id="thread-1",
    )

    assert result.ran is False
    assert result.decision is not None
    assert result.decision.decision == "block"
    assert "Docker Sandbox network is Off" in result.stderr


def test_confirmed_shell_command_can_cross_approval_gate_for_safe_workspace(tmp_path, monkeypatch) -> None:
    from row_bot.developer import runtime

    workspace = fake_workspace(tmp_path, execution_mode="local")
    monkeypatch.setattr("row_bot.developer.storage.get_workspace", lambda workspace_id: workspace)
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *_args, **_kwargs: type("Completed", (), {"returncode": 0, "stdout": "ok", "stderr": ""})(),
    )
    monkeypatch.setattr(runtime, "_snapshot_changed_files", lambda _root: {})

    result = runtime.run_workspace_shell_command(
        workspace.path,
        "git commit -m test",
        "approve",
        workspace_id=workspace.id,
        thread_id="thread-1",
        confirmed=True,
    )

    assert result.ran is True
    assert result.decision is not None
    assert result.decision.allowed is True
