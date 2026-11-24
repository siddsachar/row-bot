"""A joined Row-Bot's credential (C1b): refreshed lazily, one at a time and saved before use; 429 waits and
keeps it; a company that no longer accepts the device shows as disconnected; Sign in again keeps the device;
Leave forgets it here even when the server can't be told."""

from __future__ import annotations

import threading
import time
from dataclasses import replace

import httpx
import pytest

from row_bot.application import client_company
from row_bot.company import client, store
from tests.fixtures.company_server import ORIGIN

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


def _joined(server) -> store.Record:
    client_company.join(ORIGIN, "Fixture laptop")
    client_company.wait()
    record = store.read_record()
    assert record is not None and client_company.status()["state"] == "joined"
    return record


def _expire_access() -> store.Credential:
    """The access token's hour is up (the refresh token still works)."""
    credential = replace(store.read_credential(), expires_at=0.0)
    store.save_credential(credential)
    return credential


class WatchedLock:
    """The refresh lock, telling the test when a second caller arrives at it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._arrivals = 0
        self.second = threading.Event()

    def __enter__(self) -> WatchedLock:
        self._arrivals += 1
        if self._arrivals == 2:
            self.second.set()
        self._lock.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self._lock.release()


def test_an_ended_access_token_is_refreshed_and_the_new_pair_saved_before_it_is_used(company_server):
    _joined(company_server)
    old = _expire_access()
    saved_first = []
    company_server.on_bearer = lambda token: saved_first.append(store.read_credential().access_token == token)

    view = client_company.check()

    assert view["state"] == "joined" and company_server.refreshes == 1
    new = store.read_credential()
    assert new.refresh_token != old.refresh_token and old.refresh_token not in company_server.refresh
    # The keychain held the new pair when it was first presented (/api/me, then the company's models).
    assert saved_first and all(saved_first)


def test_two_calls_that_need_a_refresh_share_one(company_server, monkeypatch):
    _joined(company_server)
    _expire_access()
    entered, release = threading.Event(), threading.Event()
    company_server.refresh_gate = (entered, release)
    lock = WatchedLock()
    monkeypatch.setattr(client, "_REFRESH", lock)
    names: list[tuple[str, str]] = []

    first = threading.Thread(target=lambda: names.append(client.me(ORIGIN)))
    first.start()
    assert entered.wait(5)  # The first call is refreshing; the second still holds the old pair.
    second = threading.Thread(target=lambda: names.append(client.me(ORIGIN)))
    second.start()
    assert lock.second.wait(5)
    release.set()
    first.join(5)
    second.join(5)

    assert names == [("Fixture Person", "person@example.test")] * 2
    assert company_server.refreshes == 1


def test_a_server_that_says_wait_keeps_the_credential_and_is_not_asked_again_until_then(company_server, monkeypatch):
    _joined(company_server)
    before = _expire_access()
    company_server.limited = True

    view = client_company.check()

    assert (view["state"], view["failure"]) == ("unreachable", "busy")
    assert store.read_credential() == before and store.read_record().disconnected is False
    asked = len(company_server.requests)
    assert client_company.check()["state"] == "unreachable"
    assert len(company_server.requests) == asked  # Within Retry-After, nothing is sent.

    company_server.limited = False
    monkeypatch.setattr(client, "now", lambda: time.time() + 61)
    assert client_company.check()["state"] == "joined"


def test_a_server_that_cant_be_reached_keeps_the_row_bot_joined(company_server, monkeypatch):
    record = _joined(company_server)

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(client, "transport", httpx.MockTransport(offline))
    view = client_company.check()

    assert (view["state"], view["failure"], view["company"]) == ("unreachable", None, "rowbot.example.test")
    assert store.read_record() == record and store.read_credential() is not None

    monkeypatch.setattr(client, "transport", company_server.transport)
    assert client_company.check()["state"] == "joined"


def test_a_locked_keychain_keeps_the_row_bot_joined_and_says_so(company_server, keychain):
    record = _joined(company_server)
    keychain.fail = True

    view = client_company.check()

    assert (view["state"], view["failure"]) == ("unreachable", "storage_unavailable")
    keychain.fail = False
    assert store.read_record() == record and store.read_credential() is not None
    assert client_company.check()["state"] == "joined"


def test_a_removed_device_shows_as_disconnected_and_keeps_no_credential(company_server):
    record = _joined(company_server)
    company_server.revoke(record.device_id)

    view = client_company.check()

    assert view["state"] == "disconnected" and view["company"] == "rowbot.example.test"
    assert store.read_credential() is None
    kept = store.read_record()
    assert kept.disconnected is True and kept.device_id == record.device_id
    # A disconnected Row-Bot asks the server nothing more on its own.
    asked = len(company_server.requests)
    assert client_company.check()["state"] == "disconnected"
    assert len(company_server.requests) == asked


def test_signing_in_again_keeps_the_same_device(company_server):
    record = _joined(company_server)
    company_server.end_refresh(record.device_id)  # A month since the last sign-in.
    _expire_access()
    assert client_company.check()["state"] == "disconnected"

    client_company.sign_in_again()
    client_company.wait()

    view = client_company.status()
    assert view["state"] == "joined"
    assert company_server.authorize[-1]["device_id"] == record.device_id
    again = store.read_record()
    assert (again.device_id, again.joined_at, again.disconnected) == (record.device_id, record.joined_at, False)


def test_a_sign_in_again_the_server_refuses_falls_back_to_joining_afresh(company_server):
    record = _joined(company_server)
    company_server.revoke(record.device_id)
    assert client_company.check()["state"] == "disconnected"

    # The server refuses the old device in the browser, which never comes back; the person cancels.
    waiting = client_company.sign_in_again()
    assert (waiting["state"], waiting["rejoining"]) == ("waiting", True)
    assert client_company.cancel()["failure"] == "cancelled"
    company_server.bad_requests.clear()

    client_company.sign_in_again()
    client_company.wait()

    assert "device_id" not in company_server.authorize[-1]
    assert client_company.status()["state"] == "joined"
    assert store.read_record().device_id not in {record.device_id, ""}


def test_leave_tells_the_server_and_forgets_everything_here(company_server, tmp_path):
    record = _joined(company_server)

    view = client_company.leave()

    assert (view["state"], view["notice"], view["company"]) == ("not_joined", "left", "rowbot.example.test")
    assert company_server.devices[record.device_id]["revoked"] is True
    assert ("POST", "/api/device/leave") in [(request.method, request.url.path) for request in company_server.requests]
    assert store.read_credential() is None and not (tmp_path / "data" / "company.json").exists()


def test_leave_with_the_server_out_of_reach_still_forgets_here_and_says_so(company_server, monkeypatch, tmp_path):
    record = _joined(company_server)

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(client, "transport", httpx.MockTransport(offline))
    view = client_company.leave()

    assert (view["state"], view["notice"]) == ("not_joined", "left_offline")
    assert company_server.devices[record.device_id]["revoked"] is False  # Its admin removes it there.
    assert store.read_credential() is None and not (tmp_path / "data" / "company.json").exists()
