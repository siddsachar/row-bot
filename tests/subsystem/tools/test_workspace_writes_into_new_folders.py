"""S2 (18 Nov): parallel writes into a new folder were refused at random on Windows.

A workflow saving several files into a folder that didn't exist yet got "Error: Access denied … Permission
granted exclusively to the current working directory" for some of them; a retry of the same path worked. The
file tool checks that the resolved path stays in the workspace, and Windows answers a path whose folder another
write is creating at that moment with a ``\\?\`` form, which isn't "inside". Reproduced on Windows: 3 of 1,200
parallel writes; with the folder made first, 0 of 3,600. Here ``realpath`` answers a missing folder that way.
"""
from __future__ import annotations

import os

import pytest

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


@pytest.fixture
def write(tmp_path, monkeypatch, reload_for_data_dir):
    data, workspace = tmp_path / "data", tmp_path / "workspace"
    data.mkdir()
    workspace.mkdir()
    filesystem_tool, registry = reload_for_data_dir(data, "row_bot.tools.filesystem_tool", "row_bot.tools.registry")
    registry.set_tool_config("filesystem", "workspace_root", str(workspace))
    real = os.path.realpath

    def as_windows_mid_creation(path, *args, **kwargs):
        text = os.fspath(path)
        if "creator-trials" in text and not os.path.isdir(os.path.dirname(text)):
            return "//?/" + real(text, *args, **kwargs).replace("\\", "/")
        return real(text, *args, **kwargs)

    monkeypatch.setattr(os.path, "realpath", as_windows_mid_creation)
    tools = {tool.name: tool for tool in filesystem_tool.FileSystemTool().as_langchain_tools()}
    return tools["workspace_write_file"], workspace


def test_a_write_into_a_folder_that_does_not_exist_yet_lands(write):
    tool, workspace = write
    result = tool.invoke({"file_path": "creator-trials/maintenance-brief/results/run1.md", "text": "# Run 1\n"})
    assert "Access denied" not in result
    assert (workspace / "creator-trials" / "maintenance-brief" / "results" / "run1.md").read_text() == "# Run 1\n"


def test_a_write_that_climbs_out_of_the_workspace_makes_no_folder_there(write, tmp_path):
    tool, _workspace = write
    result = tool.invoke({"file_path": "../creator-trials-outside/notes.md", "text": "x"})
    assert not (tmp_path / "creator-trials-outside").exists(), result
