"""A Windows upgrade replaces the app's source tree instead of layering on it (B190).

Inno Setup copies files but never removes ones a newer release no longer ships,
so modules deleted from Row-Bot (the NiceGUI ``ui`` package) stayed on disk
after an in-place upgrade. The installer now deletes the shipped source tree
first; it never touches anything outside the install folder (user data lives
in the profile folder, not under ``{app}``).
"""
from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.subsystem

ISS = pathlib.Path("installer/row_bot_setup.iss")


def _section(name: str) -> list[str]:
    text = ISS.read_text(encoding="utf-8")
    match = re.search(rf"^\[{re.escape(name)}\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    assert match, name
    return [line.strip() for line in match.group(1).splitlines()
            if line.strip() and not line.strip().startswith(";")]


def _deleted_paths(section: str) -> list[str]:
    return [re.search(r'Name:\s*"([^"]+)"', line).group(1) for line in _section(section)]


def test_an_upgrade_clears_the_shipped_source_before_copying_the_new_one() -> None:
    deleted = _deleted_paths("InstallDelete")
    assert "{app}\\app\\src" in deleted
    assert "{app}\\app\\static" in deleted


def test_install_and_uninstall_only_delete_inside_the_install_folder() -> None:
    for path in _deleted_paths("InstallDelete") + _deleted_paths("UninstallDelete"):
        assert path.startswith("{app}\\"), path
