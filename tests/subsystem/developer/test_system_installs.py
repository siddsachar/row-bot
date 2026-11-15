"""Only the person installs software on their computer.

Asked for a narrated video, the agent tried `winget install --id Gyan.FFmpeg` to get round a missing
runtime, and the Auto reviewer, on a local model, passed it six times in six as "a necessary step" of the
video (D3, 3 Nov). A command that installs software for everyone on the computer now always asks the
person (the reviewer never accepts it for them); a project's own packages are the reviewer's call as before.
These are approval tests: they stay in the pull request lane.
"""
from __future__ import annotations

import pytest

from row_bot.developer.runtime import installs_system_software

pytestmark = pytest.mark.platform


@pytest.mark.parametrize("command", [
    "winget install --id Gyan.FFmpeg --silent --accept-source-agreements --accept-package-agreements",
    "winget.exe upgrade Git.Git",
    "choco install ffmpeg -y",
    "scoop install ffmpeg",
    "brew install ffmpeg",
    "sudo apt-get install -y ffmpeg",
    "sudo pacman -S ffmpeg",
    "msiexec /i setup.msi /qn",
    "Install-Module Pester -Force",
    "cd tools && winget install Python.Python.3.13",
])
def test_commands_that_install_software_for_everyone(command):
    assert installs_system_software(command)


@pytest.mark.parametrize("command", [
    "pip install requests",
    "npm install",
    "uv add httpx",
    "winget list",
    "brew list --versions",
    "apt list --installed",
    "Get-Command ffmpeg",
    'echo "winget install ffmpeg"',
])
def test_a_projects_packages_and_lookups_are_not_that(command):
    assert not installs_system_software(command)


def test_under_auto_installing_software_asks_the_person_not_the_reviewer(tmp_path, monkeypatch, reload_for_data_dir):
    from row_bot.approval_review import ReviewVerdict

    approval_gate, shell_tool = reload_for_data_dir(tmp_path / "data", "row_bot.tools.approval_gate",
                                                    "row_bot.tools.shell_tool")
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "auto")
    reviewed: list = []
    monkeypatch.setattr("row_bot.approval_review.review", lambda payload, **_kw: reviewed.append(payload)
                        or ReviewVerdict("approve", "Installing FFmpeg is a necessary step of the video."))
    asked: list[dict] = []
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: asked.append(payload) or False)

    output = shell_tool.ShellTool().execute("winget install --id Gyan.FFmpeg --silent",
                                            approval_reason="The video needs ffmpeg.")

    assert reviewed == []
    assert [payload["args"]["command"] for payload in asked] == ["winget install --id Gyan.FFmpeg --silent"]
    assert asked[0]["review"]["reason"] == "Only you can decide what software is installed on this computer."
    assert "did not run" in output
