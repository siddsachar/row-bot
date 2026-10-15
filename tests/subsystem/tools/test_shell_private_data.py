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
    """The setting is a hard limit: with it off, not even Hands-off reads the folder, nor asks."""
    shell_tool, _registry, _home = shell
    monkeypatch.setattr("langgraph.types.interrupt", lambda _payload: pytest.fail("must not ask"))
    monkeypatch.setattr("row_bot.approval_review.review", lambda *_a, **_k: pytest.fail("must not review"))
    for mode in ("block", "approve", "auto", "full"):
        monkeypatch.setattr(shell_tool.approval_gate, "current_approval_mode", lambda mode=mode: mode)
        output = shell_tool.ShellTool().execute("cat ~/.row-bot/settings.json")
        assert output.startswith("BLOCKED: this command reads Row-Bot's private data folder")


def test_with_the_setting_on_ask_asks_with_a_warning(shell, monkeypatch):
    shell_tool, registry, _home = shell
    registry.set_tool_config("shell", "allow_data_folder", True)
    asked: list[dict] = []
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: asked.append(payload) or False)
    monkeypatch.setattr(shell_tool.approval_gate, "current_approval_mode", lambda: "approve")

    output = shell_tool.ShellTool().execute("cat ~/.row-bot/settings.json")

    assert "denied by you" in output
    assert asked[0]["description"].startswith("This reads Row-Bot's private data folder (conversations, settings, keys)")
    monkeypatch.setattr(shell_tool.approval_gate, "current_approval_mode", lambda: "block")
    assert shell_tool.ShellTool().execute("cat ~/.row-bot/settings.json").startswith("BLOCKED")
    assert len(asked) == 1


@pytest.mark.parametrize(("decision", "asks"), [("ask", True), ("approve", False)])
def test_with_the_setting_on_auto_shows_it_to_the_reviewer_as_private_data(shell, monkeypatch, decision, asks):
    """Under Auto the reviewer decides (A0 decision 2): it may approve a read the person clearly asked for."""
    from row_bot.approval_review import ReviewVerdict

    shell_tool, registry, _home = shell
    registry.set_tool_config("shell", "allow_data_folder", True)
    asked, reviewed = [], []
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: asked.append(payload) or False)
    monkeypatch.setattr("row_bot.approval_review.review", lambda payload, *, risks=(), **_kw: reviewed.append(
        tuple(risks)) or ReviewVerdict(decision, "You asked to read the settings file."))
    monkeypatch.setattr(shell_tool.approval_gate, "current_approval_mode", lambda: "auto")
    monkeypatch.setattr(shell_tool._session_manager, "get_session", lambda *_a: type("S", (), {
        "run_command": lambda self, command: {"output": "{}", "exit_code": 0, "duration": 0.01, "cwd": "."}})())

    output = shell_tool.ShellTool().execute("cat ~/.row-bot/settings.json")

    assert any("private data" in risk for risk in reviewed[0])
    assert bool(asked) is asks
    if asks:
        assert "denied by you" in output and "The reviewer asked you" in output
    else:
        assert output.startswith("Approval: approved by the reviewer: You asked to read the settings file")


def test_with_the_setting_on_full_autonomy_reads_it_without_asking(shell, monkeypatch):
    shell_tool, registry, _home = shell
    registry.set_tool_config("shell", "allow_data_folder", True)
    monkeypatch.setattr("langgraph.types.interrupt", lambda _payload: pytest.fail("must not ask"))
    monkeypatch.setattr(shell_tool.approval_gate, "current_approval_mode", lambda: "full")
    monkeypatch.setattr(shell_tool._session_manager, "get_session", lambda *_a: type("S", (), {
        "run_command": lambda self, command: {"output": "{}", "exit_code": 0, "duration": 0.01, "cwd": "."}})())

    assert shell_tool.ShellTool().execute("cat ~/.row-bot/settings.json").startswith(
        "Approval: allowed by Hands-off")


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


def test_file_tools_refuse_a_sibling_folder_named_like_the_workspace(shell, reload_for_data_dir):
    """B320: the workspace check compares folders, not string prefixes."""
    _shell_tool, registry, home = shell
    workspace, sibling = home / "Row-Bot", home / "Row-Bot2"
    workspace.mkdir()
    sibling.mkdir()
    (sibling / "secret.txt").write_text("private", encoding="utf-8")
    (workspace / "notes.txt").write_text("hello", encoding="utf-8")
    (filesystem_tool,) = reload_for_data_dir(home / ".row-bot", "row_bot.tools.filesystem_tool")
    registry.set_tool_config("filesystem", "workspace_root", str(workspace))
    tools = {tool.name: tool for tool in filesystem_tool.FileSystemTool().as_langchain_tools()}

    for path in (str(sibling / "secret.txt"), "../Row-Bot2/secret.txt"):
        assert "private" not in tools["workspace_read_file"].invoke({"file_path": path})
    assert "outside the workspace" in tools["workspace_list_directory"].invoke({"dir_path": str(sibling)})
    assert "hello" in tools["workspace_read_file"].invoke({"file_path": str(workspace / "notes.txt")})


def test_a_self_hosted_installs_secrets_folder_is_private_too(shell, monkeypatch, tmp_path, reload_for_data_dir):
    shell_tool, registry, home = shell
    secrets = tmp_path / "run-secrets"
    secrets.mkdir()
    (secrets / "OPENAI_API_KEY").write_text("sk-synthetic", encoding="utf-8")
    monkeypatch.setenv("ROW_BOT_SECRETS_DIR", str(secrets))

    assert shell_tool.classify_command(f"cat {secrets / 'OPENAI_API_KEY'}") == "private_data"
    assert shell_tool.classify_command(f"ls {str(secrets).replace(chr(92), '/')}") == "private_data"
    assert shell_tool.classify_command("echo $ROW_BOT_SECRETS_DIR") == "private_data"

    (filesystem_tool,) = reload_for_data_dir(home / ".row-bot", "row_bot.tools.filesystem_tool")
    registry.set_tool_config("filesystem", "workspace_root", str(tmp_path))
    tools = {tool.name: tool for tool in filesystem_tool.FileSystemTool().as_langchain_tools()}
    output = tools["workspace_read_file"].invoke({"file_path": "run-secrets/OPENAI_API_KEY"})
    assert output.startswith("Error: this path is inside Row-Bot's private data folder")
    assert "sk-synthetic" not in output
