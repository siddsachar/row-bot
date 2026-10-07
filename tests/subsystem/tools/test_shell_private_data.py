"""B310: the agent can't read Row-Bot's private data folder through the shell or the file tools."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.platform


@pytest.fixture
def shell(tmp_path, monkeypatch, reload_for_data_dir):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    _approval_gate, shell_tool, registry = reload_for_data_dir(
        home / ".row-bot", "row_bot.tools.approval_gate", "row_bot.tools.shell_tool", "row_bot.tools.registry")
    return shell_tool, registry, home


@pytest.mark.parametrize("command", [
    # demo 4: qwen looking for an MCP server's configuration
    r"""Get-ChildItem "$env:USERPROFILE\.row-bot" -Recurse | Where-Object { $_.Name -match 'mcp' }""",
    "cat ~/.row-bot/settings.json",
    r"type %USERPROFILE%\.row-bot\tools_config.json",
    "ls $HOME/.row-bot",
    r"Get-Content (Join-Path $HOME '.row-bot\threads.db')",
    "dir .row-bot",
    "echo $ROW_BOT_DATA_DIR",
    "Get-ChildItem $env:ROW_BOT_DATA_DIR",
])
def test_commands_naming_the_data_folder_are_private(shell, command):
    shell_tool, _registry, _home = shell
    assert shell_tool.classify_command(command) == "private_data"


def test_the_data_folder_by_its_full_path_in_either_slash_is_private(shell):
    shell_tool, _registry, home = shell
    data = home / ".row-bot"
    assert shell_tool.classify_command(f"cat {data / 'memory.db'}") == "private_data"
    assert shell_tool.classify_command(f"cat {str(data).replace(chr(92), '/')}/memory.db") == "private_data"


@pytest.mark.parametrize(("command", "expected"), [
    ("ls ~/projects", "safe"),
    ("cat notes/.row-bot-ideas.md", "safe"),
    ("python build.py", "needs_approval"),
])
def test_unrelated_paths_are_unaffected(shell, command, expected):
    shell_tool, _registry, _home = shell
    assert shell_tool.classify_command(command) == expected


def test_reading_the_data_folder_is_refused_in_every_mode_without_asking(shell, monkeypatch):
    shell_tool, _registry, _home = shell
    monkeypatch.setattr("langgraph.types.interrupt", lambda _payload: pytest.fail("must not ask"))
    for mode in ("block", "approve", "allow_all"):
        monkeypatch.setattr(shell_tool.approval_gate, "current_approval_mode", lambda mode=mode: mode)
        output = shell_tool.ShellTool().execute("cat ~/.row-bot/settings.json")
        assert output.startswith("BLOCKED: this command reads Row-Bot's private data folder")


def test_with_the_setting_on_it_asks_with_a_warning_even_in_auto(shell, monkeypatch):
    shell_tool, registry, _home = shell
    registry.set_tool_config("shell", "allow_data_folder", True)
    asked: list[dict] = []
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: asked.append(payload) or False)
    monkeypatch.setattr(shell_tool.approval_gate, "current_approval_mode", lambda: "allow_all")

    output = shell_tool.ShellTool().execute("cat ~/.row-bot/settings.json")

    assert "denied by you" in output
    assert asked[0]["description"].startswith("This reads Row-Bot's private data folder (conversations, settings, keys)")
    monkeypatch.setattr(shell_tool.approval_gate, "current_approval_mode", lambda: "block")
    assert shell_tool.ShellTool().execute("cat ~/.row-bot/settings.json").startswith("BLOCKED")
    assert len(asked) == 1


def test_file_tools_refuse_paths_inside_the_data_folder(shell, reload_for_data_dir):
    _shell_tool, registry, home = shell
    (home / ".row-bot" / "settings.json").write_text("{}", encoding="utf-8")
    (home / "notes.txt").write_text("hello", encoding="utf-8")
    (filesystem_tool,) = reload_for_data_dir(home / ".row-bot", "row_bot.tools.filesystem_tool")
    registry.set_tool_config("filesystem", "workspace_root", str(home))
    tools = {tool.name: tool for tool in filesystem_tool.FileSystemTool().as_langchain_tools()}

    assert tools["workspace_read_file"].invoke({"file_path": ".row-bot/settings.json"}).startswith(
        "Error: this path is inside Row-Bot's private data folder")
    assert tools["workspace_list_directory"].invoke({"dir_path": ".row-bot"}).startswith(
        "Error: this path is inside Row-Bot's private data folder")
    assert "hello" in tools["workspace_read_file"].invoke({"file_path": "notes.txt"})
