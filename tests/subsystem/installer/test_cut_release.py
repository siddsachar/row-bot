"""cut_release.py moves every version file to the new release in one step.

The release gate refuses a build whose requested version differs from
version.py or the Inno Setup script, and the installers, the macOS bundle, the
bug report form and the docs screenshots all carry the version too.
"""
from __future__ import annotations

import plistlib
import shutil
from pathlib import Path

import pytest
import yaml

import scripts.cut_release as cut_release
import scripts.release_gate as gate


pytestmark = [pytest.mark.subsystem, pytest.mark.installer]

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    for relative in cut_release.VERSION_FILES:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / relative, target)
    return tmp_path


def test_every_version_file_follows_the_new_release(checkout: Path) -> None:
    cut_release.main(["9.8.7", "--root", str(checkout)])

    assert gate._source_versions(checkout) == ("9.8.7", "9.8.7")
    iss = (checkout / "installer/row_bot_setup.iss").read_text(encoding="utf-8")
    assert "; Row-Bot v9.8.7 - Inno Setup Script" in iss

    release = (checkout / ".github/workflows/release.yml").read_text(encoding="utf-8")
    workflow = yaml.load(release, Loader=yaml.BaseLoader)
    assert workflow["on"]["workflow_dispatch"]["inputs"]["version"]["default"] == "9.8.7"
    assert workflow["env"]["ROW_BOT_VERSION"] == "${{ inputs.version || '9.8.7' }}"
    gate_step = next(step for step in workflow["jobs"]["release-gate"]["steps"] if step.get("id") == "gate")
    assert gate_step["env"]["RELEASE_VERSION"] == "${{ inputs.version || '9.8.7' }}"

    plist = plistlib.loads((checkout / "installer/Row-Bot.app/Contents/Info.plist").read_bytes())
    assert (plist["CFBundleVersion"], plist["CFBundleShortVersionString"]) == ("9.8.7", "9.8.7")

    command = (checkout / "Start Row-Bot.command").read_text(encoding="utf-8")
    assert '    ROW_BOT_VERSION="9.8.7"' in command.splitlines()

    form = yaml.load((checkout / ".github/ISSUE_TEMPLATE/bug_report.yml").read_text(encoding="utf-8"),
                     Loader=yaml.BaseLoader)
    placeholders = [item.get("attributes", {}).get("placeholder") for item in form["body"]]
    assert "v9.8.7" in placeholders

    screenshot = (checkout / "docs-site/src/components/Screenshot.tsx").read_text(encoding="utf-8")
    assert "const SCREENSHOT_REVISION = '9.8.7';" in screenshot


def test_line_endings_survive_the_bump(checkout: Path) -> None:
    version_py = checkout / "src/row_bot/version.py"
    version_py.write_bytes(version_py.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    command = checkout / "Start Row-Bot.command"
    command.write_bytes(command.read_bytes().replace(b"\r\n", b"\n"))

    cut_release.main(["9.8.7-rc.1", "--root", str(checkout)])

    assert b'__version__ = "9.8.7-rc.1"\r\n' in version_py.read_bytes()
    assert b"\r\n" not in command.read_bytes()
    assert gate._source_versions(checkout) == ("9.8.7-rc.1", "9.8.7-rc.1")


def test_a_malformed_version_changes_nothing(checkout: Path) -> None:
    before = {relative: (checkout / relative).read_bytes() for relative in cut_release.VERSION_FILES}

    with pytest.raises(SystemExit):
        cut_release.main(["5.0", "--root", str(checkout)])

    assert {relative: (checkout / relative).read_bytes() for relative in cut_release.VERSION_FILES} == before
