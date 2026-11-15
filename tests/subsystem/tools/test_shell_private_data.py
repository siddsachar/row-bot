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


@pytest.mark.parametrize("command", [
    "~/.row-bot/ffmpeg/imageio-ffmpeg/ffmpeg.exe -i clip.webm clip.mp4",
    "ls ~/.row-bot/recordings",
    r"type %USERPROFILE%\.row-bot\logs\row_bot.log",
    "cp ~/.row-bot/exports/report.pdf ./report.pdf",
])
def test_the_data_folders_shared_parts_follow_the_approval_level(shell, command):
    """5.3.1 A1: the managed ffmpeg, recordings, exports and logs are tools and results, not private data."""
    shell_tool, _registry, _home = shell
    assert shell_tool.classify_command(command) in {"safe", "needs_approval"}


@pytest.mark.parametrize("command", [
    "cat ~/.row-bot/ffmpeg/../threads.db",
    "ls ~/.row-bot/recordings ~/.row-bot",
    "cat ~/.row-bot/ffmpegs/settings.json",
])
def test_a_shared_part_never_opens_the_rest_of_the_data_folder(shell, command):
    shell_tool, _registry, _home = shell
    assert shell_tool.classify_command(command) == "private_data"


def test_a_projects_own_row_bot_folder_is_not_the_data_folder(shell):
    shell_tool, _registry, _home = shell
    assert shell_tool.classify_command("cat my-app/.row-bot/config.json") == "safe"


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
        "cwd": ".", "run_command": lambda self, command, **_kwargs: {"output": "{}", "exit_code": 0, "duration": 0.01, "cwd": "."}})())

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
        "cwd": ".", "run_command": lambda self, command, **_kwargs: {"output": "{}", "exit_code": 0, "duration": 0.01, "cwd": "."}})())

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


def _file_tools(shell, reload_for_data_dir):
    _shell_tool, registry, home = shell
    workspace, sibling = home / "Row-Bot", home / "Row-Bot2"
    workspace.mkdir()
    sibling.mkdir()
    (sibling / "secret.txt").write_text("private", encoding="utf-8")
    (workspace / "notes.txt").write_text("hello", encoding="utf-8")
    filesystem_tool, approval_gate = reload_for_data_dir(
        home / ".row-bot", "row_bot.tools.filesystem_tool", "row_bot.tools.approval_gate")
    registry.set_tool_config("filesystem", "workspace_root", str(workspace))
    tools = {tool.name: tool for tool in filesystem_tool.FileSystemTool().as_langchain_tools()}
    return tools, approval_gate, workspace, sibling


def test_file_tools_treat_a_sibling_folder_named_like_the_workspace_as_outside(shell, reload_for_data_dir,
                                                                                monkeypatch):
    """B320: the workspace check compares folders, not string prefixes; a sibling is outside it."""
    tools, approval_gate, workspace, sibling = _file_tools(shell, reload_for_data_dir)
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "block")

    for path in (str(sibling / "secret.txt"), "../Row-Bot2/secret.txt"):
        assert "private" not in tools["workspace_read_file"].invoke({"file_path": path})
    assert "outside the workspace" in tools["workspace_list_directory"].invoke({"dir_path": str(sibling)})
    assert "hello" in tools["workspace_read_file"].invoke({"file_path": str(workspace / "notes.txt")})


def test_a_file_outside_the_workspace_is_read_only_after_the_persons_approval(shell, reload_for_data_dir,
                                                                             monkeypatch):
    """5.3.1 B1: a file elsewhere on this computer asks by the approval level, naming its path."""
    tools, approval_gate, _workspace, sibling = _file_tools(shell, reload_for_data_dir)
    asked: list[dict] = []
    answers = iter([False, True])
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "approve")
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: asked.append(payload) or next(answers))
    target = str(sibling / "secret.txt")

    denied = tools["workspace_read_file"].invoke({"file_path": target})
    approved = tools["workspace_read_file"].invoke({"file_path": target})

    assert "private" not in denied and "private" in approved
    assert asked[0]["label"] == "Read outside the workspace?"
    assert asked[0]["description"] == f"Outside the workspace: {target}"


def test_a_file_outside_the_workspace_is_written_after_approval_through_the_same_tool(shell, reload_for_data_dir,
                                                                                     monkeypatch):
    tools, approval_gate, _workspace, sibling = _file_tools(shell, reload_for_data_dir)
    asked: list[dict] = []
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "approve")
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: asked.append(payload) or True)

    tools["workspace_write_file"].invoke({"file_path": str(sibling / "plan.md"), "text": "Ship 5.3.1"})

    assert (sibling / "plan.md").read_text(encoding="utf-8") == "Ship 5.3.1"
    assert asked[0]["label"] == "Change files outside the workspace?"
    assert asked[0]["description"] == f"Outside the workspace: {sibling / 'plan.md'}"


def test_the_data_folder_stays_out_of_reach_even_under_hands_off(shell, reload_for_data_dir, monkeypatch):
    tools, approval_gate, _workspace, _sibling = _file_tools(shell, reload_for_data_dir)
    _shell_tool, _registry, home = shell
    (home / ".row-bot").mkdir(exist_ok=True)
    (home / ".row-bot" / "settings.json").write_text("{\"key\": \"secret\"}", encoding="utf-8")
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "full")

    answer = tools["workspace_read_file"].invoke({"file_path": str(home / ".row-bot" / "settings.json")})

    assert "secret" not in answer and "private data folder" in answer


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


@pytest.fixture
def container(tmp_path, monkeypatch, reload_for_data_dir):
    """The container image: the home folder is the data folder (HOME=/data, ROW_BOT_DATA_DIR=/data), so the
    default workspace, Documents/Row-Bot, lies inside it."""
    data = tmp_path / "data"
    workspace = data / "Documents" / "Row-Bot"
    workspace.mkdir(parents=True)
    (data / "threads.db").write_text("", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: data))
    _approval_gate, shell_tool, registry, filesystem_tool = reload_for_data_dir(
        data, "row_bot.tools.approval_gate", "row_bot.tools.shell_tool", "row_bot.tools.registry",
        "row_bot.tools.filesystem_tool")
    registry.set_tool_config("filesystem", "workspace_root", str(workspace))
    tools = {tool.name: tool for tool in filesystem_tool.FileSystemTool().as_langchain_tools()}
    return shell_tool, tools, data, workspace


def test_in_the_container_the_default_workspace_is_the_agents(container):
    """A hosted Row-Bot's file tools write its own workspace, and the shell may name it."""
    from row_bot.data_paths import is_private_data_path

    shell_tool, tools, _data, workspace = container

    written = tools["workspace_write_file"].invoke({"file_path": "primes.py", "text": "print(2)\n"})

    assert "private data folder" not in written
    assert (workspace / "primes.py").read_text(encoding="utf-8") == "print(2)\n"
    assert not is_private_data_path(workspace / "primes.py")
    assert shell_tool.classify_command(f"python {workspace / 'primes.py'}") != "private_data"
    assert shell_tool.classify_command("cat ~/Documents/Row-Bot/primes.py") != "private_data"


def test_in_the_container_the_rest_of_the_data_folder_stays_private(container):
    from row_bot.data_paths import is_private_data_path

    shell_tool, tools, data, workspace = container

    assert is_private_data_path(data / "threads.db")
    assert is_private_data_path(data / "Documents" / "other.txt"), "the workspace, not the rest of Documents"
    assert is_private_data_path(data / "Documents" / "Row-Bot2" / "x.txt"), "a folder named like it is not it"
    assert tools["workspace_read_file"].invoke({"file_path": str(data / "threads.db")}).startswith(
        "Error: this path is inside Row-Bot's private data folder")
    assert tools["workspace_read_file"].invoke({"file_path": "../../threads.db"}).startswith("Error: ")
    assert shell_tool.classify_command(f"cat {data / 'threads.db'}") == "private_data"
    assert shell_tool.classify_command("cat ~/Documents/Row-Bot/../../threads.db") == "private_data"
    assert shell_tool.classify_command(f"cat {workspace}2/notes.md") == "private_data"


def test_a_sandbox_in_the_container_never_gets_the_real_workspace(container, monkeypatch):
    """5.3.1 B4: a sandbox's own workspace is its to use; the real Row-Bot's stays private to it."""
    from row_bot.data_paths import is_private_data_path

    _shell_tool, _tools, data, workspace = container
    box = data / "sandboxes" / "box-1" / "workspace"
    monkeypatch.setenv("ROW_BOT_SANDBOX_OF", str(data))
    monkeypatch.setenv("ROW_BOT_WORKSPACE", str(box))

    assert is_private_data_path(workspace / "primes.py")
    assert not is_private_data_path(box / "notes.md")
