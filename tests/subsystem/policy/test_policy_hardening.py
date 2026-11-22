"""The loader can't be made to fail open: a policy the person can't look at, a folder made in advance, a desktop
``ROW_BOT_POLICY_FILE``, a share or another disk, an unsigned source above pinned keys, a bug while looking, a
duplicate key, a hard link or a file swapped mid-check all lock down or are ignored, never "no policy". Every
OS location is under tmp_path; the real machine's policy locations are never read."""

from __future__ import annotations

import getpass
import json
import os
import plistlib
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from row_bot.policy import Lockdown, NoPolicy, OrgPolicy
from row_bot.policy import sources, trust
from row_bot.policy.sources import System, load_policy
from row_bot.policy.trust import posix_problem, trust_problem
from tests.helpers.policy_fakes import (
    Signer, fake_system, pin, registry_from, windows_policy_file, write_bytes, write_policy)

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

ASK = {"schema_version": 1, "approvals": {"max_level": "approve"}}
SERVER = {"ROW_BOT_DEPLOYMENT_MODE": "server"}


def _linux_file(root: Path) -> Path:
    return root / "etc" / "row-bot" / "org-policy.json"


def _lockdown(policy: object, reason: str) -> Lockdown:
    assert isinstance(policy, Lockdown), policy
    assert policy.reason == reason, policy.detail
    return policy


def _deny_looking(monkeypatch: pytest.MonkeyPatch, marker: str) -> None:
    real = os.lstat

    def lstat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if marker in str(path):
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", lstat)


# --- H1: a policy the person can't look at --------------------------------------------------------------

def test_a_policy_folder_the_person_cant_list_locks_down(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_policy(_linux_file(tmp_path), {"schema_version": 1, "tools": {"disabled": ["*"]}})
    named = write_policy(tmp_path / "server" / "policy.json", ASK)
    _deny_looking(monkeypatch, str(_linux_file(tmp_path).parent))

    policy = _lockdown(load_policy(fake_system(tmp_path, environ={**SERVER, sources.POLICY_FILE_ENV: str(named)})),
                       "unreadable")

    assert policy.source.kind == "file"
    assert [item.source.kind for item in policy.ignored] == ["env_file"]


@pytest.mark.parametrize("hidden", [".sig", "org-policy-keys.json"])
def test_a_signature_or_keys_file_the_person_cant_look_at_locks_down(tmp_path: Path, hidden: str,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_policy(_linux_file(tmp_path), ASK)
    signer = Signer()
    pin(path, signer)
    signer.sign(path)
    _deny_looking(monkeypatch, hidden)

    _lockdown(load_policy(fake_system(tmp_path)), "unreadable")


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows access lists")
def test_a_policy_hidden_by_its_access_list_locks_down_on_windows(tmp_path: Path) -> None:
    policy = write_policy(windows_policy_file(tmp_path), {"schema_version": 1, "tools": {"disabled": ["*"]}})
    me = getpass.getuser()
    subprocess.run(["icacls", str(policy), "/deny", f"{me}:(RA,REA,RC)"], check=True, capture_output=True)
    subprocess.run(["icacls", str(policy.parent), "/deny", f"{me}:(RD)"], check=True, capture_output=True)
    try:
        result = load_policy(System("windows", {}, program_data=lambda: tmp_path / "ProgramData"))
    finally:
        subprocess.run(["icacls", str(policy.parent), "/remove:d", me], capture_output=True)
        subprocess.run(["icacls", str(policy), "/remove:d", me], capture_output=True)

    _lockdown(result, "unreadable")


# --- H3: ROW_BOT_POLICY_FILE, shares and other disks ------------------------------------------------------

def test_a_desktop_build_ignores_the_policy_file_variable(tmp_path: Path) -> None:
    mine = write_policy(tmp_path / "share" / "p.json", {
        "schema_version": 1,
        "organisation": {"server_url": "https://org.example.net"},
        "models": {"gateway_url": "https://gateway.example.net"},
        "updates": {"external": True, "pinned_version": "5.0.0"},
    })

    for platform in ("windows", "macos", "linux"):
        policy = load_policy(fake_system(tmp_path, platform, environ={sources.POLICY_FILE_ENV: str(mine)}))
        assert isinstance(policy, NoPolicy)
        assert [(item.source.kind, item.source.location) for item in policy.ignored] == [("env_file", str(mine))]
        assert "server and container" in policy.ignored[0].reason


def test_a_desktop_build_reports_the_variable_beside_an_os_policy(tmp_path: Path) -> None:
    write_policy(_linux_file(tmp_path), ASK)

    policy = load_policy(fake_system(tmp_path, environ={sources.POLICY_FILE_ENV: "/srv/policy.json"}))

    assert isinstance(policy, OrgPolicy) and [item.source.kind for item in policy.ignored] == ["env_file"]


@pytest.mark.parametrize("environ", [{"ROW_BOT_CONTAINERIZED": "1"}, {"ROW_BOT_DEPLOYMENT_MODE": "Server"}])
def test_server_and_container_mode_read_the_policy_file_variable(tmp_path: Path, environ: dict[str, str]) -> None:
    named = write_policy(tmp_path / "srv" / "policy.json", ASK)

    policy = load_policy(fake_system(tmp_path, environ={**environ, sources.POLICY_FILE_ENV: str(named)}))

    assert isinstance(policy, OrgPolicy) and policy.source.kind == "env_file"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths")
@pytest.mark.parametrize("path", [r"\\files.example.net\share\org-policy.json", r"\\?\C:\Policy\org-policy.json",
                                  r"\\.\C:\Policy\org-policy.json", "//files.example.net/share/org-policy.json"])
def test_windows_refuses_shares_and_device_paths(path: str) -> None:
    problem = trust_problem(Path(path))

    assert problem and "network share or device path" in problem


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drives")
def test_windows_refuses_a_drive_that_is_not_fixed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_policy(tmp_path / "Row-Bot" / "policy" / "org-policy.json", ASK)
    monkeypatch.setattr(trust, "drive_type", lambda root: 2)  # DRIVE_REMOVABLE

    problem = trust_problem(path, tmp_path)

    assert problem and "fixed drive" in problem


def _stat(mode: int, dev: int = 1) -> os.stat_result:
    return os.stat_result((mode, 0, dev, 1, 0, 0, 0, 0, 0, 0))


def test_posix_refuses_a_file_on_another_disk_outside_server_mode(tmp_path: Path) -> None:
    file = tmp_path / "media" / "usb" / "org-policy.json"

    def lstat(path: Path) -> os.stat_result:
        if str(path) == str(file):
            return _stat(stat.S_IFREG | 0o644, dev=2)
        return _stat(stat.S_IFDIR | 0o755)

    problem = posix_problem(file, lstat=lstat, realpath=str)

    assert problem and "another disk" in problem
    assert posix_problem(file, lstat=lstat, realpath=str, any_device=True) is None


# --- M2: an unsigned source above pinned keys ---------------------------------------------------------

def test_a_registry_policy_above_pinned_keys_locks_down(tmp_path: Path) -> None:
    path = write_policy(windows_policy_file(tmp_path), {"schema_version": 1, "tools": {"disabled": ["*"]}})
    signer = Signer()
    pin(path, signer)
    signer.sign(path)
    registry = registry_from({"schema_version": 1, "approvals": {"max_level": "full"}})

    policy = _lockdown(load_policy(fake_system(tmp_path, "windows", registry=registry)),
                       "unsigned_source_with_pinned_keys")

    assert policy.source.kind == "registry" and policy.limit_approval_level("full") == "block"


def test_a_profile_above_pinned_keys_locks_down(tmp_path: Path) -> None:
    write_bytes(tmp_path / "Managed Preferences" / "ai.row-bot.plist", plistlib.dumps(ASK))
    pin(tmp_path / "Application Support" / "Row-Bot" / "org-policy.json", Signer())

    _lockdown(load_policy(fake_system(tmp_path, "macos")), "unsigned_source_with_pinned_keys")


def test_a_registry_policy_without_pinned_keys_is_used(tmp_path: Path) -> None:
    assert isinstance(load_policy(fake_system(tmp_path, "windows", registry=registry_from(ASK))), OrgPolicy)


# --- M3: a policy folder made in advance --------------------------------------------------------------

@pytest.mark.parametrize(("platform", "folder"), [
    ("windows", Path("ProgramData") / "Row-Bot"),
    ("windows", Path("ProgramData") / "Row-Bot" / "policy"),
    ("macos", Path("Application Support") / "Row-Bot"),
    ("linux", Path("etc") / "row-bot"),
])
def test_a_policy_folder_without_its_file_is_checked(tmp_path: Path, platform: str, folder: Path) -> None:
    (tmp_path / folder).mkdir(parents=True)
    checked: list[tuple[Path, bool]] = []

    def squatted(path: Path, boundary: Path | None, *, folder: bool = False, **_: object) -> str | None:
        checked.append((path, folder))
        return f"{path}: owned by a person"

    policy = _lockdown(load_policy(fake_system(tmp_path, platform, trust=squatted)), "untrusted_location")

    assert checked == [(tmp_path / folder, True)] and policy.source.kind == "file"


def test_a_trusted_policy_folder_without_its_file_is_no_policy(tmp_path: Path) -> None:
    (tmp_path / "etc" / "row-bot").mkdir(parents=True)

    assert load_policy(fake_system(tmp_path)) == NoPolicy()


def test_a_policy_folder_this_person_made_locks_down(tmp_path: Path) -> None:
    (tmp_path / "ProgramData" / "Row-Bot" / "policy").mkdir(parents=True)
    (tmp_path / "etc" / "row-bot").mkdir(parents=True)

    platform = "windows" if sys.platform == "win32" else "linux"
    _lockdown(load_policy(fake_system(tmp_path, platform, trust=trust_problem)), "untrusted_location")


# --- M4: a bug while looking --------------------------------------------------------------------------

@pytest.mark.parametrize("error", [RuntimeError("winreg returned something odd"), ValueError("garbage")])
def test_a_failure_while_looking_locks_down(tmp_path: Path, error: Exception) -> None:
    def broken() -> object:
        raise error

    registry = _lockdown(load_policy(System("windows", {}, registry=broken, program_data=lambda: tmp_path)),
                         "unreadable")
    program_data = _lockdown(load_policy(System("windows", {}, program_data=broken)), "unreadable")

    assert registry.source.kind == "registry" and program_data.source.kind == "file"


def test_a_failure_in_the_trust_check_of_a_folder_locks_down(tmp_path: Path) -> None:
    (tmp_path / "etc" / "row-bot").mkdir(parents=True)

    def broken(path: Path, boundary: Path | None, **_: object) -> str | None:
        raise ValueError("unexpected")

    _lockdown(load_policy(fake_system(tmp_path, trust=broken)), "unreadable")


# --- L1, L2, L3: encodings, duplicate keys, the file itself --------------------------------------------

@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-be", "utf-8-sig"])
def test_policies_written_by_powershell_are_read(tmp_path: Path, encoding: str) -> None:
    text = json.dumps(ASK)
    data = ("\ufeff" + text).encode("utf-16-be") if encoding == "utf-16-be" else text.encode(encoding)
    write_bytes(_linux_file(tmp_path), data)

    policy = load_policy(fake_system(tmp_path))

    assert isinstance(policy, OrgPolicy) and policy.limit_approval_level("auto") == "approve"


def test_a_signed_utf16_policy_is_verified_over_its_bytes(tmp_path: Path) -> None:
    path = write_bytes(_linux_file(tmp_path), json.dumps(ASK).encode("utf-16"))
    signer = Signer()
    pin(path, signer)
    signer.sign(path)

    assert load_policy(fake_system(tmp_path)).signer == signer.key_id


@pytest.mark.parametrize("body", [
    b"<key>schema_version</key><integer>1</integer><key>tools</key><dict><key>disabled</key><array>"
    b"<string>*</string></array></dict><key>tools</key><dict></dict>",
    b"<key>schema_version</key><integer>1</integer><key>tools</key><dict><key>disabled</key><array>"
    b"<string>*</string></array><key>disabled</key><array></array></dict>",
], ids=["section twice", "setting twice"])
def test_a_profile_with_a_duplicate_key_locks_down(tmp_path: Path, body: bytes) -> None:
    write_bytes(tmp_path / "Managed Preferences" / "ai.row-bot.plist",
                b'<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict>' + body + b"</dict></plist>")

    policy = _lockdown(load_policy(fake_system(tmp_path, "macos")), "invalid")

    assert "appears twice" in policy.detail


def test_a_policy_file_with_another_hard_link_locks_down(tmp_path: Path) -> None:
    path = write_policy(_linux_file(tmp_path), ASK)
    os.link(path, tmp_path / "elsewhere.json")

    policy = _lockdown(load_policy(fake_system(tmp_path)), "untrusted_location")

    assert "hard link" in policy.detail


def test_a_policy_file_swapped_while_it_is_checked_locks_down(tmp_path: Path) -> None:
    write_policy(_linux_file(tmp_path), ASK)
    other = write_policy(tmp_path / "other.json", {"schema_version": 1, "approvals": {"max_level": "full"}})

    def swapping(checked: Path, boundary: Path | None, **_: object) -> str | None:
        os.replace(other, checked)
        return None

    policy = _lockdown(load_policy(fake_system(tmp_path, trust=swapping)), "untrusted_location")

    assert "changed while" in policy.detail
