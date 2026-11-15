"""A2 WP2: a write, copy or move says where the file now is, so the reply can tell the person.

Under the home folder the place is shown as ``~/…`` (the form the turn context names the workspace in), and a
``~`` path the model gives lands where it says, not in a folder named ``~`` inside the workspace.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


@pytest.fixture
def files(tmp_path, monkeypatch):
    """The file tools on a workspace in a fake home folder, under Hands-off so elsewhere needs no card."""
    home = tmp_path / "home"
    workspace = home / "Documents" / "Row-Bot"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    from row_bot.tools import approval_gate
    from row_bot.tools.filesystem_tool import FileSystemTool

    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "full")
    filesystem = FileSystemTool()
    monkeypatch.setattr(filesystem, "_get_workspace_root", lambda: str(workspace))
    monkeypatch.setattr(filesystem, "_get_selected_operations", lambda: ["write_file", "copy_file", "move_file"])
    tools = {tool.name: tool for tool in filesystem.as_langchain_tools()}
    return SimpleNamespace(tools=tools, home=home, workspace=workspace, elsewhere=tmp_path / "elsewhere")


def _write(files, path: str, **extra) -> str:
    return str(files.tools["workspace_write_file"].invoke({"file_path": path, "text": "Q3 numbers\n", **extra}))


def test_a_write_in_the_workspace_names_its_place_from_the_home_folder(files):
    result = _write(files, "reports/q3.md")

    assert (files.workspace / "reports" / "q3.md").is_file()
    assert result == "Saved reports/q3.md in the workspace (~/Documents/Row-Bot/reports/q3.md)."


def test_an_append_says_it_added_to_the_file(files):
    _write(files, "log.txt")

    result = _write(files, "log.txt", append=True)

    assert (files.workspace / "log.txt").read_text(encoding="utf-8") == "Q3 numbers\nQ3 numbers\n"
    assert result == "Added to log.txt in the workspace (~/Documents/Row-Bot/log.txt)."


def test_a_home_folder_path_lands_where_it_says(files):
    result = _write(files, "~/Documents/Row-Bot/notes.txt")

    assert (files.workspace / "notes.txt").is_file()
    assert not (files.workspace / "~").exists()
    assert result == "Saved notes.txt in the workspace (~/Documents/Row-Bot/notes.txt)."


def test_a_write_elsewhere_under_home_is_named_from_the_home_folder(files):
    result = _write(files, "~/Desktop/q3.md")

    assert (files.home / "Desktop" / "q3.md").is_file()
    assert result == "Saved q3.md outside the workspace: ~/Desktop/q3.md."


def test_a_write_outside_the_home_folder_names_the_full_path(files):
    files.elsewhere.mkdir()
    target = files.elsewhere / "q3.md"

    result = _write(files, str(target))

    assert target.is_file()
    assert result == f"Saved q3.md outside the workspace: {target}."


def test_copy_and_move_name_where_the_file_now_is(files):
    _write(files, "draft.md")
    (files.workspace / "backup").mkdir()

    copied = files.tools["workspace_copy_file"].invoke(
        {"source_path": "draft.md", "destination_path": "backup/draft.md"})
    moved = files.tools["workspace_move_file"].invoke(
        {"source_path": "draft.md", "destination_path": "final.md"})

    assert copied == "Copied draft.md to backup/draft.md in the workspace (~/Documents/Row-Bot/backup/draft.md)."
    assert moved == "Moved draft.md to final.md in the workspace (~/Documents/Row-Bot/final.md)."
    assert (files.workspace / "final.md").is_file() and not (files.workspace / "draft.md").exists()


def test_a_failed_write_says_so_and_names_no_place(files):
    result = _write(files, "../../escape.md")

    assert result.startswith("Error")
    assert "Saved" not in result
