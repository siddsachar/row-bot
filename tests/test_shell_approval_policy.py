from __future__ import annotations

import importlib
import sys


def _fresh_shell_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    for name in (
        "row_bot.tools.approval_gate",
        "row_bot.tools.shell_tool",
    ):
        sys.modules.pop(name, None)

    import row_bot.tools.approval_gate as approval_gate
    import row_bot.tools.shell_tool as shell_tool

    approval_gate = importlib.reload(approval_gate)
    shell_tool = importlib.reload(shell_tool)
    return approval_gate, shell_tool


def test_shell_block_policy_ignores_model_authored_reason(tmp_path, monkeypatch):
    approval_gate, shell_tool = _fresh_shell_modules(tmp_path, monkeypatch)
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


def test_shell_ask_policy_includes_reason_in_interrupt_payload(tmp_path, monkeypatch):
    approval_gate, shell_tool = _fresh_shell_modules(tmp_path, monkeypatch)
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

    assert output == "Command cancelled by user."
    assert captured["tool"] == "run_command"
    assert captured["approval_reason"] == "Verify command execution policy."
    assert captured["args"]["command"] == "python -c \"print('hi')\""


def test_shell_auto_policy_runs_without_interrupt(tmp_path, monkeypatch):
    approval_gate, shell_tool = _fresh_shell_modules(tmp_path, monkeypatch)
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "allow_all")

    def fail_interrupt(_payload):
        raise AssertionError("allow_all policy should not ask for approval")

    monkeypatch.setattr("langgraph.types.interrupt", fail_interrupt)
    seen: list[str] = []

    class FakeSession:
        def run_command(self, command: str) -> dict:
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
