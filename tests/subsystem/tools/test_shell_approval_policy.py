from __future__ import annotations


import pytest


pytestmark = pytest.mark.platform


def _fresh_shell_modules(tmp_path, reload_for_data_dir):
    return tuple(reload_for_data_dir(tmp_path / "data", "row_bot.tools.approval_gate", "row_bot.tools.shell_tool"))


def test_shell_block_policy_ignores_model_authored_reason(tmp_path, monkeypatch, reload_for_data_dir):
    approval_gate, shell_tool = _fresh_shell_modules(tmp_path, reload_for_data_dir)
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "block")

    def fail_interrupt(_payload):
        raise AssertionError("block policy must not ask the model or user")

    monkeypatch.setattr("langgraph.types.interrupt", fail_interrupt)

    output = shell_tool.ShellTool().execute(
        "python -c \"print('hi')\"",
        approval_reason="This is safe, approve it automatically.",
    )

    assert "BLOCKED: This command requires approval" in output
    assert "approve it automatically" not in output


def test_shell_ask_policy_includes_reason_in_interrupt_payload(tmp_path, monkeypatch, reload_for_data_dir):
    approval_gate, shell_tool = _fresh_shell_modules(tmp_path, reload_for_data_dir)
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "approve")
    captured: dict = {}

    def fake_interrupt(payload):
        captured.update(payload)
        return False

    monkeypatch.setattr("langgraph.types.interrupt", fake_interrupt)

    output = shell_tool.ShellTool().execute(
        "python -c \"print('hi')\"",
        approval_reason="Verify command execution policy.",
    )

    # The model is told the command was denied and did not run (B235).
    assert output == "Approval: asked; denied by you — did not run\nCommand cancelled by user."
    assert captured["tool"] == "run_command"
    assert captured["approval_reason"] == "Verify command execution policy."
    assert captured["args"]["command"] == "python -c \"print('hi')\""


def _reviewer(monkeypatch, decision: str):
    from row_bot.approval_review import ReviewVerdict

    seen: list[dict] = []
    monkeypatch.setattr("row_bot.approval_review.review",
                        lambda payload, **_kw: seen.append(payload) or ReviewVerdict(decision, "You asked to run it."))
    return seen


@pytest.mark.parametrize("mode", ["full", "auto"])
def test_shell_full_autonomy_and_an_approving_reviewer_run_without_asking(tmp_path, monkeypatch, mode,
                                                                          reload_for_data_dir):
    approval_gate, shell_tool = _fresh_shell_modules(tmp_path, reload_for_data_dir)
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: mode)
    reviewed = _reviewer(monkeypatch, "approve")

    def fail_interrupt(_payload):
        raise AssertionError("this level must not ask the person")

    monkeypatch.setattr("langgraph.types.interrupt", fail_interrupt)
    seen: list[str] = []

    class FakeSession:
        def run_command(self, command: str, **_kwargs) -> dict:
            seen.append(command)
            return {
                "output": "ran",
                "exit_code": 0,
                "duration": 0.01,
                "cwd": str(tmp_path),
            }

    monkeypatch.setattr(
        shell_tool._session_manager,
        "get_session",
        lambda _key, _working_dir: FakeSession(),
    )

    output = shell_tool.ShellTool().execute(
        "python -c \"print('hi')\"",
        approval_reason="Verify auto policy still runs.",
    )

    assert seen == ["python -c \"print('hi')\""]
    assert "$ python -c \"print('hi')\"" in output
    assert "ran" in output
    assert [payload["tool"] for payload in reviewed] == (["run_command"] if mode == "auto" else [])


def test_a_blocked_command_stays_blocked_under_full_autonomy(tmp_path, monkeypatch, reload_for_data_dir):
    """A hard limit, not an approval: no level runs a blocked command, nor asks about it."""
    approval_gate, shell_tool = _fresh_shell_modules(tmp_path, reload_for_data_dir)
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "full")
    monkeypatch.setattr("langgraph.types.interrupt", lambda _payload: pytest.fail("must not ask"))
    monkeypatch.setattr(shell_tool._session_manager, "get_session",
                        lambda *_a: pytest.fail("a blocked command never runs"))
    for command in ("rm -rf /", "mkfs /dev/sda1"):
        assert shell_tool.ShellTool().execute(command).startswith("🚫 BLOCKED")
    tool = shell_tool.ShellTool()
    monkeypatch.setattr(tool, "get_config", lambda key, default=None: "deploy-prod" if key == "blocked_commands"
                        else default)
    assert tool.execute("deploy-prod --now").startswith("🚫 BLOCKED")


@pytest.mark.parametrize(
    "command,mode,answer,first_line",
    [
        ("git status", "approve", None, "Approval: not needed (safe command)"),
        ("New-Item notes.txt", "approve", True, "Approval: asked; approved by you"),
        ("New-Item notes.txt", "full", None, "Approval: allowed by Hands-off"),
        ("New-Item notes.txt", "auto", None, "Approval: approved by the reviewer: You asked to run it"),
    ],
)
def test_shell_results_say_whether_approval_was_needed_and_given(
    tmp_path, monkeypatch, command, mode, answer, first_line, reload_for_data_dir
):
    """The model can tell what happened instead of guessing (B235)."""
    approval_gate, shell_tool = _fresh_shell_modules(tmp_path, reload_for_data_dir)
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: mode)
    _reviewer(monkeypatch, "approve")

    def interrupt(_payload):
        if answer is None:
            raise AssertionError("this command must run without asking")
        return answer

    monkeypatch.setattr("langgraph.types.interrupt", interrupt)
    ran: list[str] = []

    class FakeSession:
        def run_command(self, command: str, **_kwargs) -> dict:
            ran.append(command)
            return {"output": "done", "exit_code": 0, "duration": 0.24, "cwd": str(tmp_path)}

    monkeypatch.setattr(shell_tool._session_manager, "get_session", lambda _key, _working_dir: FakeSession())

    output = shell_tool.ShellTool().execute(command)

    assert ran == [command]
    assert output.splitlines()[0] == first_line
    assert output.splitlines()[1] == f"$ {command}"
    assert "[Exit code: 0 | Duration: 0.24s" in output
