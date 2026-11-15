"""A2 WP2: the shell starts in the workspace folder the file tools use, never the home folder.

With no workspace chosen in Settings (a fresh profile, or the file tools switched off), the shell used to
start in the home folder while the prompt said it starts in the workspace. A conversation bound to a code
folder keeps starting in that folder.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.platform


@pytest.fixture
def computer(tmp_path, monkeypatch, reload_for_data_dir):
    """A fresh profile: nothing saved for the file tools, and commands recorded instead of started."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    monkeypatch.delenv("ROW_BOT_WORKSPACE", raising=False)
    _threads, storage, shell_tool = reload_for_data_dir(
        tmp_path / "data", "row_bot.threads", "row_bot.developer.storage", "row_bot.tools.shell_tool")
    started: list[str] = []

    def run_command(conversation_id, argv, *, command, folder, timeout, background):
        started.append(folder)
        return SimpleNamespace(finished=True, output="hi", exit_code=0, earlier_output_dropped=False,
                               cancelled=False, limit_message="")

    monkeypatch.setattr("row_bot.background_processes.run_command", run_command)
    return SimpleNamespace(shell=shell_tool.ShellTool(), storage=storage, home=home, started=started)


def test_with_no_workspace_chosen_the_shell_starts_in_the_default_workspace(computer):
    result = computer.shell.execute("echo hi", runtime_config={"configurable": {"thread_id": "chat-1"}})

    workspace = computer.home / "Documents" / "Row-Bot"
    assert workspace.is_dir()
    assert [Path(folder) for folder in computer.started] == [workspace]
    assert f"cwd: {workspace}]" in result


def test_a_conversation_bound_to_a_code_folder_keeps_starting_there(computer, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    code_folder = computer.storage.add_or_update_local_workspace(str(repo))

    computer.shell.execute("echo hi", runtime_config={
        "configurable": {"thread_id": "code-1", "developer_workspace_id": code_folder.id}})

    assert [Path(folder) for folder in computer.started] == [repo.resolve()]
