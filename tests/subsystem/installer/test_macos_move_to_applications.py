"""The macOS host's "Move to Applications": when it offers the move, and how it moves (RM1).

Builds installer/macos/RowBotTrayHost.m with clang and runs the two commands it
shares with the move: ``--move-plan`` (the decision) and ``--install-copy`` (copy
to a hidden name in the folder, verify, then swap in with one rename) against
fake app bundles in a temporary folder. Nothing is trashed: the host trashes the
app it replaced only after the swap, and the command prints that app instead.
"""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = [
    pytest.mark.subsystem,
    pytest.mark.installer,
    pytest.mark.platform,
    pytest.mark.skipif(sys.platform != "darwin", reason="the macOS tray host"),
]

SOURCE = Path("installer/macos/RowBotTrayHost.m")
TRANSLOCATED = "/private/var/folders/zz/abc/T/AppTranslocation/0F1E2D3C-4B5A/d/Row-Bot.app"


@pytest.fixture(scope="module")
def host(tmp_path_factory) -> Path:
    if shutil.which("xcrun") is None:
        pytest.skip("needs the Xcode command line tools")
    program = tmp_path_factory.mktemp("host") / "row-bot"
    result = subprocess.run(
        ["xcrun", "clang", "-fobjc-arc", "-fblocks", "-framework", "Cocoa", "-framework", "Security",
         "-mmacosx-version-min=14.0", str(SOURCE.resolve()), "-o", str(program)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return program


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> Path:
    folder = tmp_path / "data"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(folder))
    monkeypatch.setenv("ROW_BOT_INSTALL_COUNT", "0")
    return folder


def _plan(host: Path, bundle: str, original: str, home: Path) -> str:
    result = subprocess.run([str(host), "--move-plan", bundle, original, str(home)],
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _bundle(path: Path, host: Path, *, version: str = "5.3.4", marker: str = "new",
            identifier: str = "ai.row-bot.assistant") -> Path:
    contents = path / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    shutil.copy2(host, contents / "MacOS" / "row-bot")
    with (contents / "Info.plist").open("wb") as info:
        plistlib.dump({"CFBundleIdentifier": identifier, "CFBundleShortVersionString": version,
                       "CFBundleVersion": version, "CFBundleExecutable": "row-bot",
                       "CFBundlePackageType": "APPL", "CFBundleName": "Row-Bot"}, info)
    (contents / "Resources" / "marker.txt").write_text(marker, encoding="utf-8")
    return path


def _marker(bundle: Path) -> str:
    return (bundle / "Contents" / "Resources" / "marker.txt").read_text(encoding="utf-8")


def _install(host: Path, source: Path, folder: Path, version: str = "5.3.4") -> subprocess.CompletedProcess:
    return subprocess.run([str(host), "--install-copy", str(source), str(folder), version],
                          capture_output=True, text=True, check=False)


def _log(data_dir: Path) -> str:
    path = data_dir / "launcher-host.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


@pytest.mark.parametrize("original", ["/Applications/Row-Bot.app", "~/Applications/Row-Bot.app",
                                      "/Applications/Utilities/Row-Bot.app"])
def test_an_app_already_in_applications_is_never_moved_even_translocated(host, tmp_path, original) -> None:
    original = original.replace("~", str(tmp_path))
    assert _plan(host, TRANSLOCATED, original, tmp_path) == "already_installed"
    assert _plan(host, original, original, tmp_path) == "already_installed"


def test_an_app_from_downloads_or_a_disk_image_is_offered_the_move(host, tmp_path) -> None:
    downloads = str(tmp_path / "Downloads" / "Row-Bot.app")
    assert _plan(host, downloads, downloads, tmp_path) == "offer"
    assert _plan(host, TRANSLOCATED, downloads, tmp_path) == "offer"
    # Translocated from a disk image, or where macOS would not say where from.
    assert _plan(host, TRANSLOCATED, "/Volumes/Row-Bot/Row-Bot.app", tmp_path) == "offer"
    assert _plan(host, TRANSLOCATED, TRANSLOCATED, tmp_path) == "offer"


def test_an_app_somewhere_else_runs_where_it_is(host, tmp_path) -> None:
    desktop = str(tmp_path / "Desktop" / "Row-Bot.app")
    assert _plan(host, desktop, desktop, tmp_path) == "stay"
    # An "Applications" folder somewhere else is not one.
    elsewhere = str(tmp_path / "Projects" / "Applications" / "Row-Bot.app")
    assert _plan(host, elsewhere, elsewhere, tmp_path) == "stay"


def test_the_move_copies_verifies_and_then_swaps_the_new_app_in(host, tmp_path, data_dir) -> None:
    applications = tmp_path / "Applications"
    installed = _bundle(applications / "Row-Bot.app", host, version="5.3.3", marker="old")
    source = _bundle(tmp_path / "Downloads" / "Row-Bot.app", host)

    result = _install(host, source, applications)

    assert result.returncode == 0, result.stderr
    lines = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert Path(lines["installed"]) == installed
    assert _marker(installed) == "new"
    displaced = Path(lines["displaced"])
    assert displaced.parent == applications and displaced.name.startswith(".")
    assert _marker(displaced) == "old"
    assert sorted(p.name for p in applications.iterdir()) == sorted(["Row-Bot.app", displaced.name])
    assert _marker(source) == "new"
    log = _log(data_dir)
    assert log.index("copied ") < log.index("copy_verified ") < log.index("swapped ")
    assert "move_failed" not in log


def test_the_move_into_an_empty_folder_leaves_nothing_to_trash(host, tmp_path, data_dir) -> None:
    applications = tmp_path / "Applications"
    applications.mkdir()
    source = _bundle(tmp_path / "Downloads" / "Row-Bot.app", host)

    result = _install(host, source, applications)

    assert result.returncode == 0, result.stderr
    assert "displaced=\n" in result.stdout
    assert [p.name for p in applications.iterdir()] == ["Row-Bot.app"]
    assert _marker(applications / "Row-Bot.app") == "new"
    assert "replaced=no" in _log(data_dir)


def test_a_signed_app_is_moved_only_with_a_valid_signature(host, tmp_path, data_dir) -> None:
    if shutil.which("codesign") is None:
        pytest.skip("needs codesign")
    applications = tmp_path / "Applications"
    applications.mkdir()
    source = _bundle(tmp_path / "Downloads" / "Row-Bot.app", host)
    subprocess.run(["codesign", "--force", "--sign", "-", str(source)], capture_output=True, check=True)

    result = _install(host, source, applications)

    assert result.returncode == 0, result.stderr
    assert "copy_verified signature=valid" in _log(data_dir)
    subprocess.run(["codesign", "--verify", "--strict", str(applications / "Row-Bot.app")], check=True)


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads any file")
def test_a_failed_copy_leaves_the_installed_app_untouched(host, tmp_path, data_dir) -> None:
    applications = tmp_path / "Applications"
    installed = _bundle(applications / "Row-Bot.app", host, version="5.3.3", marker="old")
    source = _bundle(tmp_path / "Downloads" / "Row-Bot.app", host)
    unreadable = source / "Contents" / "Resources" / "unreadable.bin"
    unreadable.write_bytes(b"x")
    unreadable.chmod(0)
    try:
        result = _install(host, source, applications)
    finally:
        unreadable.chmod(0o644)

    assert result.returncode == 1
    assert "copy:" in result.stderr
    assert [p.name for p in applications.iterdir()] == ["Row-Bot.app"]
    assert _marker(installed) == "old"
    log = _log(data_dir)
    assert "move_failed step=copy" in log
    assert "swapped " not in log


@pytest.mark.parametrize(("identifier", "version"), [("ai.row-bot.assistant", "5.3.5"), ("com.example.other", "5.3.4")])
def test_a_copy_that_is_not_the_running_app_is_never_swapped_in(host, tmp_path, data_dir, identifier, version) -> None:
    applications = tmp_path / "Applications"
    installed = _bundle(applications / "Row-Bot.app", host, version="5.3.3", marker="old")
    source = _bundle(tmp_path / "Downloads" / "Row-Bot.app", host, version=version, identifier=identifier)

    result = _install(host, source, applications, version="5.3.4")

    assert result.returncode == 1
    assert "verify:" in result.stderr
    assert [p.name for p in applications.iterdir()] == ["Row-Bot.app"]
    assert _marker(installed) == "old"
    assert "move_failed step=verify" in _log(data_dir)
