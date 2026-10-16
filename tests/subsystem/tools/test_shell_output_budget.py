"""run_command keeps a long output's head and tail, and the whole of it readable by handle (WP D1)."""
from __future__ import annotations

import re

import pytest

from row_bot import tool_outputs
from row_bot.tools import shell_tool


pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


def test_long_command_output_is_capped_with_the_rest_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(tool_outputs, "current_thread_id", lambda: "thread-shell")
    output = "".join(f"build step {index:06d} done\n" for index in range(10_000)).rstrip("\n")  # ~250K
    monkeypatch.setattr(
        shell_tool.ShellSession,
        "run_command",
        lambda self, command, timeout=120, **_kwargs: {
            "command": command, "output": output, "exit_code": 0, "cwd": str(tmp_path), "duration": 0.1,
        },
    )
    monkeypatch.setattr(shell_tool, "append_shell_history", lambda *args, **kwargs: None)

    result = shell_tool.ShellTool().execute("ls")

    assert len(result) < tool_outputs.COMMAND_OUTPUT_CHARS + 1_000
    body = result.split("$ ls\n", 1)[1]
    assert body.startswith("[run_command output is ")
    assert "build step 000000 done" in result and "build step 009999 done" in result
    assert result.rstrip().endswith(f"cwd: {tmp_path}]")
    match = re.search(r'read_tool_output\(handle="(out_[0-9a-f]+)", offset=(\d+)\)', result)
    assert match
    shown = body.split("\n", 1)[1][: int(match.group(2))]
    pages, offset = [shown], int(match.group(2))
    while True:
        header, _, body = tool_outputs.read_tool_output(match.group(1), offset=offset).partition("\n")
        pages.append(body)
        if "End of output." in header:
            break
        offset = int(re.search(r"Continue with offset=(\d+)", header).group(1))
    reassembled = "".join(pages) == output
    assert reassembled


def test_short_command_output_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        shell_tool.ShellSession,
        "run_command",
        lambda self, command, timeout=120, **_kwargs: {
            "command": command, "output": "a.txt\nb.txt", "exit_code": 0, "cwd": str(tmp_path), "duration": 0.1,
        },
    )
    monkeypatch.setattr(shell_tool, "append_shell_history", lambda *args, **kwargs: None)

    result = shell_tool.ShellTool().execute("ls")

    assert "$ ls\na.txt\nb.txt\n" in result
    assert not (tmp_path / "data" / "tool_outputs").exists()
