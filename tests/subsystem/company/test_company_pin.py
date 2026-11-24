"""The company server an IT department sets for this computer (C5): one managed setting per OS, read through
fakes (never this machine's registry, managed preferences or /etc); with it, Join goes only there, Leave and
Sign in again elsewhere are refused, and a Row-Bot IT moved may leave its old company. Without it nothing
changes."""

from __future__ import annotations

import json
import logging
import plistlib
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from row_bot.application import client_company
from row_bot.company import managed, store
from tests.fixtures.company_server import ORIGIN

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

OTHER = "https://elsewhere.example.test"
LOOPBACK = "http://127.0.0.1:8770"


class FakeKey:
    def __init__(self, values: dict[str, tuple[object, int]]) -> None:
        self.values = values

    def __enter__(self) -> FakeKey:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class FakeRegistry:
    """Just enough of ``winreg``: keys by (hive, path), each value with its type; it records what was opened."""

    HKEY_LOCAL_MACHINE = "HKLM"
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 0x20019
    KEY_WOW64_64KEY = 0x0100
    REG_SZ = 1
    REG_EXPAND_SZ = 2
    REG_DWORD = 4

    def __init__(self, keys: dict[tuple[str, str], dict[str, tuple[object, int]]]) -> None:
        self.keys = keys
        self.opened: list[tuple[str, str]] = []

    def OpenKey(self, hive: str, path: str, reserved: int = 0, access: int = 0) -> FakeKey:  # noqa: N802 - winreg
        self.opened.append((hive, path))
        if (hive, path) not in self.keys:
            raise FileNotFoundError(path)
        return FakeKey(self.keys[(hive, path)])

    def QueryValueEx(self, key: FakeKey, name: str) -> tuple[object, int]:  # noqa: N802 - winreg
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name]


@pytest.fixture
def fresh(monkeypatch):
    """Nothing logged about the setting yet in this process."""
    monkeypatch.setattr(managed, "_logged", set())


def _windows(monkeypatch, keys) -> FakeRegistry:
    registry = FakeRegistry(keys)
    monkeypatch.setattr(managed.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", registry)
    return registry


def _macos(monkeypatch, tmp_path, preferences: dict | None, *, fmt=plistlib.FMT_XML):
    managed_file = tmp_path / "Managed Preferences" / "ai.row-bot.assistant.plist"
    if preferences is not None:
        managed_file.parent.mkdir(parents=True)
        managed_file.write_bytes(plistlib.dumps(preferences, fmt=fmt))
    monkeypatch.setattr(managed.sys, "platform", "darwin")
    monkeypatch.setattr(managed, "MACOS_PLIST", managed_file)


def _linux(monkeypatch, tmp_path, content: bytes | None, *, uid: int = 0, mode: int = 0o100644):
    path = tmp_path / "etc" / "row-bot" / "company.json"
    if content is not None:
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
    monkeypatch.setattr(managed.sys, "platform", "linux")
    monkeypatch.setattr(managed, "LINUX_FILE", path)
    # The owner and mode the file would have (this machine's own can't be made root's).
    monkeypatch.setattr(managed, "_fstat", lambda fd: SimpleNamespace(st_uid=uid, st_mode=mode))


def _pinned(monkeypatch, server: str) -> None:
    """IT set ``server`` for this computer (the OS source faked; the value is still checked as IT's is)."""
    monkeypatch.setattr(managed, "_read", lambda: server)


def _ignored(caplog) -> list[str]:
    return [entry.getMessage() for entry in caplog.records
            if entry.name == "row_bot.company.managed" and "was ignored" in entry.getMessage()]


# -- the setting, per OS ------------------------------------------------------------------------------------


def test_windows_reads_the_machine_policy_key_and_normalises_the_address(monkeypatch, fresh):
    registry = _windows(monkeypatch, {
        ("HKLM", r"SOFTWARE\Policies\Row-Bot"): {"CompanyServer": (" RowBot.Example.Test ", FakeRegistry.REG_SZ)},
    })

    assert managed.pin() == managed.Pin(server=ORIGIN)
    assert managed.pinned_server() == ORIGIN
    assert set(registry.opened) == {("HKLM", r"SOFTWARE\Policies\Row-Bot")}


def test_windows_never_takes_the_setting_from_the_person_s_own_hive(monkeypatch, fresh):
    registry = _windows(monkeypatch, {
        ("HKCU", r"SOFTWARE\Policies\Row-Bot"): {"CompanyServer": (OTHER, FakeRegistry.REG_SZ)},
    })

    assert managed.pin() == managed.Pin()
    assert all(hive == "HKLM" for hive, _ in registry.opened)


def test_windows_a_value_that_isn_t_text_is_ignored_and_reported(monkeypatch, fresh, caplog):
    caplog.set_level(logging.WARNING, logger="row_bot.company.managed")
    _windows(monkeypatch, {("HKLM", r"SOFTWARE\Policies\Row-Bot"): {"CompanyServer": (1, FakeRegistry.REG_DWORD)}})

    assert managed.pin() == managed.Pin(invalid=True)
    assert len(_ignored(caplog)) == 1


@pytest.mark.parametrize("fmt", [plistlib.FMT_XML, plistlib.FMT_BINARY])
def test_macos_reads_the_app_s_managed_preferences(monkeypatch, tmp_path, fresh, fmt):
    _macos(monkeypatch, tmp_path, {"CompanyServer": "https://rowbot.example.test:8443/"}, fmt=fmt)

    assert managed.pinned_server() == "https://rowbot.example.test:8443"


def test_macos_never_takes_the_setting_from_the_person_s_own_defaults(monkeypatch, tmp_path, fresh):
    home = tmp_path / "home"
    own = home / "Library" / "Preferences" / "ai.row-bot.assistant.plist"
    own.parent.mkdir(parents=True)
    own.write_bytes(plistlib.dumps({"CompanyServer": OTHER}))
    monkeypatch.setenv("HOME", str(home))
    _macos(monkeypatch, tmp_path, None)  # No configuration profile installed.

    assert managed.pin() == managed.Pin()


def test_macos_preferences_without_the_key_set_nothing(monkeypatch, tmp_path, fresh):
    _macos(monkeypatch, tmp_path, {"SomethingElse": True})

    assert managed.pin() == managed.Pin()


def test_linux_reads_a_root_owned_file(monkeypatch, tmp_path, fresh):
    _linux(monkeypatch, tmp_path, json.dumps({"server": "rowbot.example.test"}).encode())

    assert managed.pinned_server() == ORIGIN


@pytest.mark.parametrize(("uid", "mode"), [(1000, 0o100644), (0, 0o100666), (0, 0o040755)],
                         ids=["not_roots", "world_writable", "not_a_file"])
def test_linux_ignores_a_file_others_could_have_written_logging_it_once(monkeypatch, tmp_path, fresh, caplog,
                                                                         uid, mode):
    caplog.set_level(logging.WARNING, logger="row_bot.company.managed")
    _linux(monkeypatch, tmp_path, json.dumps({"server": ORIGIN}).encode(), uid=uid, mode=mode)

    assert managed.pin() == managed.Pin()
    assert managed.pin() == managed.Pin()
    # Not reported as IT's mistake (Pin.invalid stays False): anyone could have put it there.
    assert len(_ignored(caplog)) == 1


def test_linux_without_the_file_sets_nothing_and_says_nothing(monkeypatch, tmp_path, fresh, caplog):
    caplog.set_level(logging.WARNING, logger="row_bot.company.managed")
    _linux(monkeypatch, tmp_path, None)

    assert managed.pin() == managed.Pin()
    assert _ignored(caplog) == []


@pytest.mark.parametrize("content", [
    json.dumps({"server": "http://rowbot.example.test"}).encode(),  # Plain http off this computer.
    json.dumps({"server": "https://rowbot.example.test/path?x=1"}).encode(),
    json.dumps({"server": 443}).encode(),
    json.dumps({"address": ORIGIN}).encode(),
    b"rowbot.example.test",  # Not JSON.
], ids=["http", "path", "number", "no_server", "not_json"])
def test_a_value_that_isn_t_a_valid_address_is_ignored_and_logged_once(monkeypatch, tmp_path, fresh,
                                                                             caplog, content):
    caplog.set_level(logging.WARNING, logger="row_bot.company.managed")
    _linux(monkeypatch, tmp_path, content)

    assert managed.pin() == managed.Pin(invalid=True)
    assert managed.pin() == managed.Pin(invalid=True)
    assert len(_ignored(caplog)) == 1


def test_plain_http_is_taken_only_on_this_computer_s_loopback(monkeypatch, tmp_path, fresh):
    _linux(monkeypatch, tmp_path, json.dumps({"server": LOOPBACK}).encode())

    assert managed.pinned_server() == LOOPBACK


# -- what the setting changes ------------------------------------------------------------------------------


def test_with_no_setting_nothing_changes(company_server):
    view = client_company.status()
    assert (view["state"], view["pinned_server"], view["pin_invalid"]) == ("not_joined", None, False)

    client_company.join(ORIGIN, "Fixture laptop")
    client_company.wait()
    assert client_company.status()["state"] == "joined"
    assert client_company.leave()["notice"] == "left"


def test_an_address_it_set_that_isn_t_valid_is_shown_and_ignored(company_server, monkeypatch):
    _pinned(monkeypatch, "http://rowbot.example.test")

    view = client_company.status()
    assert (view["pinned_server"], view["pin_invalid"]) == (None, True)
    # Ignored: another address isn't refused for it, only checked as usual.
    assert client_company.join("http://elsewhere.example.test", "Fixture laptop")["failure"] == "not_https"


def test_not_joined_the_view_names_the_server_it_set(company_server, monkeypatch):
    _pinned(monkeypatch, "rowbot.example.test")

    view = client_company.status()

    assert (view["state"], view["pinned_server"], view["pin_invalid"]) == ("not_joined", ORIGIN, False)


def test_join_goes_only_to_the_server_it_set(company_server, monkeypatch):
    _pinned(monkeypatch, ORIGIN)

    for elsewhere in (OTHER, "not an address", "http://rowbot.example.test"):
        with pytest.raises(managed.CompanyPinned):
            client_company.join(elsewhere, "Fixture laptop")
    assert company_server.requests == [] and store.read_record() is None

    client_company.join("RowBot.Example.Test", "Fixture laptop")
    client_company.wait()
    view = client_company.status()
    assert (view["state"], view["origin"], view["pinned_server"]) == ("joined", ORIGIN, ORIGIN)


def test_joined_to_the_server_it_set_leave_is_refused_and_sign_in_again_allowed(company_server, monkeypatch):
    record = company_server.join_here()
    _pinned(monkeypatch, ORIGIN)

    with pytest.raises(managed.CompanyPinned):
        client_company.leave()
    assert store.read_record() == record
    assert ("POST", "/api/device/leave") not in [(r.method, r.url.path) for r in company_server.requests]
    assert company_server.devices[record.device_id]["revoked"] is False

    company_server.end_refresh(record.device_id)  # A month since the last sign-in: disconnected.
    store.write_record(replace(record, disconnected=True))
    with pytest.raises(managed.CompanyPinned):
        client_company.leave()
    client_company.sign_in_again()
    client_company.wait()
    assert client_company.status()["state"] == "joined"
    assert company_server.authorize[-1]["device_id"] == record.device_id


def test_moved_by_it_the_old_company_may_be_left_and_nothing_else(company_server, monkeypatch):
    company_server.join_here()
    _pinned(monkeypatch, OTHER)

    view = client_company.status()
    assert (view["origin"], view["pinned_server"]) == (ORIGIN, OTHER)
    with pytest.raises(managed.CompanyPinned):
        client_company.sign_in_again()
    with pytest.raises(managed.CompanyPinned):
        client_company.join(ORIGIN)

    left = client_company.leave()

    assert (left["state"], left["notice"], left["pinned_server"]) == ("not_joined", "left", OTHER)
    assert store.read_record() is None


def test_moved_by_it_signing_in_to_the_new_server_leaves_the_old_one_first(company_server, monkeypatch):
    record = company_server.join_here()
    old = replace(record, origin="https://old.example.test", company="old.example.test")
    store.write_record(old)
    _pinned(monkeypatch, ORIGIN)

    client_company.join(ORIGIN)
    client_company.wait()

    sent = [(r.method, r.url.host, r.url.path) for r in company_server.requests]
    assert ("POST", "old.example.test", "/api/device/leave") in sent
    assert sent.index(("POST", "old.example.test", "/api/device/leave")) < sent.index(
        ("GET", "rowbot.example.test", "/healthz"))
    view = client_company.status()
    assert (view["state"], view["origin"], view["computer_name"]) == ("joined", ORIGIN, "Fixture laptop")
    assert store.read_record().device_id != record.device_id
