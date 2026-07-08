from __future__ import annotations

from row_bot.process_cancellation import ProcessRunResult
from row_bot.tools import shell_tool


def test_shell_session_reports_cancelled_command(monkeypatch, tmp_path) -> None:
    def fake_run(args, **kwargs):
        return ProcessRunResult(args, 130, stdout="partial out", stderr="partial err", cancelled=True)

    monkeypatch.setattr(shell_tool, "run_cancellable_subprocess", fake_run)
    session = shell_tool.ShellSession(str(tmp_path))

    result = session.run_command("echo hi")

    assert result["exit_code"] == 130
    assert "partial out" in result["output"]
    assert "partial err" in result["output"]
    assert "Command stopped by user." in result["output"]
