"""F5: a relative path into Row-Bot's private data folder is private data too, from the folder the shell is in.

The shell classifies a command by the paths it names. From the workspace, "../.." reaches the home folder on
a computer (with ".row-bot" in it) and the data folder itself in the container image, where the home folder
is the data folder (HOME=/data) and the default workspace lies inside it. So each relative path counts from
the shell session's own folder, and from wherever a "cd" earlier on the line moved it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.platform


@pytest.fixture
def desktop(tmp_path, monkeypatch, reload_for_data_dir):
    """A computer: the data folder is ~/.row-bot and the workspace ~/Documents/Row-Bot."""
    home = tmp_path / "home"
    workspace = home / "Documents" / "Row-Bot"
    workspace.mkdir(parents=True)
    (home / ".row-bot").mkdir()
    (home / ".row-bot" / "threads.db").write_text("", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    _approval_gate, shell_tool = reload_for_data_dir(
        home / ".row-bot", "row_bot.tools.approval_gate", "row_bot.tools.shell_tool")
    return shell_tool, home, workspace


@pytest.fixture
def container(tmp_path, monkeypatch, reload_for_data_dir):
    """The image: the data folder is /data, which is the home folder too, and the workspace lies inside it."""
    data = tmp_path / "data"
    workspace = data / "Documents" / "Row-Bot"
    workspace.mkdir(parents=True)
    (data / "threads.db").write_text("", encoding="utf-8")
    (data / "secret-store-key").mkdir()
    (data / "secret-store-key" / "ROW_BOT_SECRET_STORE_KEY").write_text("00" * 32, encoding="utf-8")
    (data / "exports").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: data))
    _approval_gate, shell_tool = reload_for_data_dir(
        data, "row_bot.tools.approval_gate", "row_bot.tools.shell_tool")
    return shell_tool, data, workspace


@pytest.mark.parametrize("command", [
    "cat ../../.row-bot/threads.db",
    "ls ../../.row-bot",
    r"type ..\..\.row-bot\threads.db",
    "cd .. && cat ../.row-bot/threads.db",
    "cd ../.. && cat .row-bot/threads.db",
    r"cd ../.. ; type .row-bot\threads.db",
    r"Set-Location ..\..; Get-Content .row-bot\settings.json",
    "pushd ../.. && cat .row-bot/settings.json",
    "cd ../..\ncat .row-bot/threads.db",
    "sqlite3 file=../../.row-bot/threads.db",
    # Only where the "cd" led makes these the data folder.
    "cd ../.. && cat ./.row-bot/threads.db",
    r"cd ..\.. ; type .\.row-bot\threads.db",
    "cd ../..\nsqlite3 ./.row-bot/threads.db .dump",
])
def test_on_a_computer_a_relative_path_from_the_workspace_into_the_data_folder_is_private(desktop, command):
    shell_tool, _home, workspace = desktop
    assert shell_tool.classify_command(command, cwd=workspace) == "private_data"


@pytest.mark.parametrize(("command", "expected"), [
    ("ls ../..", "safe"),
    ("cat notes/../readme.md", "safe"),
    ("cat my-app/.row-bot/config.json", "safe"),
    ("cd ../../projects && ls ..", "needs_approval"),
    ("python ../tools/build.py", "needs_approval"),
])
def test_on_a_computer_ordinary_relative_paths_are_unaffected(desktop, command, expected):
    shell_tool, _home, workspace = desktop
    assert shell_tool.classify_command(command, cwd=workspace) == expected


@pytest.mark.parametrize("command", [
    "cat ../../secret-store-key/ROW_BOT_SECRET_STORE_KEY",
    "cat ../../threads.db",
    "ls ../..",
    "ls ..",
    "cd .. && cat ../threads.db",
    "cd ../.. && cat threads.db",
    "cd ~ && cat secret-store-key/ROW_BOT_SECRET_STORE_KEY",
    r"Get-Content ..\..\secret-store-key\ROW_BOT_SECRET_STORE_KEY",
    "cp ../../exports/../threads.db .",
])
def test_in_the_image_a_relative_path_from_the_workspace_into_the_data_folder_is_private(container, command):
    shell_tool, _data, workspace = container
    assert shell_tool.classify_command(command, cwd=workspace) == "private_data"


@pytest.mark.parametrize("command", ["cat threads.db", "cat secret-store-key/ROW_BOT_SECRET_STORE_KEY", "ls ."])
def test_in_the_image_a_shell_already_in_the_data_folder_reaches_it_by_any_name_there(container, command):
    shell_tool, data, _workspace = container
    assert shell_tool.classify_command(command, cwd=data) == "private_data"


@pytest.mark.parametrize(("command", "folder"), [
    ("cat src/main.py", "workspace"),
    ("ls ./notes", "workspace"),
    ("ls ../../exports", "workspace"),
    ("cp ../../exports/report.pdf .", "workspace"),
    ("echo hello", "data"),
])
def test_in_the_image_the_workspace_and_the_shared_parts_stay_the_agents(container, command, folder):
    shell_tool, data, workspace = container
    where = workspace if folder == "workspace" else data
    assert shell_tool.classify_command(command, cwd=where) != "private_data"


def test_run_command_counts_from_the_folder_the_shell_session_is_in(container, monkeypatch):
    """Refused before anything runs, at every approval level, while the setting is off."""
    shell_tool, data, workspace = container
    monkeypatch.setattr("langgraph.types.interrupt", lambda _payload: pytest.fail("must not ask"))
    shell_tool._session_manager.kill_all()
    session = shell_tool._session_manager.get_session("default", str(workspace))
    monkeypatch.setattr(type(session), "run_command", lambda *_a, **_k: pytest.fail("must not run"))

    for mode in ("block", "approve", "auto", "full"):
        monkeypatch.setattr(shell_tool.approval_gate, "current_approval_mode", lambda mode=mode: mode)
        output = shell_tool.ShellTool().execute("cat ../../secret-store-key/ROW_BOT_SECRET_STORE_KEY")
        assert output == shell_tool.PRIVATE_DATA_REFUSAL

    # The session moved since ("cd ../.."): a bare name there is the data folder's own file.
    session.cwd = str(data)
    assert shell_tool.ShellTool().execute("cat threads.db") == shell_tool.PRIVATE_DATA_REFUSAL
    shell_tool._session_manager.kill_all()


def test_a_wait_check_counts_from_the_workspace_it_runs_in(desktop):
    from row_bot.tools.waits_tool import _check_approval

    _shell_tool, _home, _workspace = desktop
    assert _check_approval("cat ../../.row-bot/threads.db", 5, "the backup is done") == (
        "BLOCKED: a check can't read Row-Bot's private data folder.")


def test_the_file_tools_refuse_a_relative_path_into_the_data_folder_on_a_computer(desktop, reload_for_data_dir):
    """The file tools resolve a relative path against the workspace before checking it, as the shell now does."""
    _shell_tool, home, workspace = desktop
    filesystem_tool, registry = reload_for_data_dir(
        home / ".row-bot", "row_bot.tools.filesystem_tool", "row_bot.tools.registry")
    registry.set_tool_config("filesystem", "workspace_root", str(workspace))
    tools = {tool.name: tool for tool in filesystem_tool.FileSystemTool().as_langchain_tools()}

    for path in ("../../.row-bot/threads.db", r"..\..\.row-bot\threads.db"):
        assert tools["workspace_read_file"].invoke({"file_path": path}).startswith("Error: ")
