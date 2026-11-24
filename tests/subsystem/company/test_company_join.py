"""Joining a company (C1b) against a fake company server and browser: the sign-in in the browser, back to
Row-Bot's loopback address, and what is kept where. Nothing leaves this computer."""

from __future__ import annotations

import itertools
import json

import pytest

from row_bot import secret_store
from row_bot.application import client_company
from row_bot.company import client, sign_in, store
from row_bot.version import __version__
from tests.fixtures.company_server import ORIGIN
from tests.subsystem.plugins.conftest import MemoryKeyring

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


def _join(address: str = "rowbot.example.test") -> dict:
    client_company.join(address, "Fixture laptop")
    client_company.wait()
    return client_company.status()


def _tokens(server) -> list[str]:
    assert server.issued
    return server.issued


def test_joining_signs_in_through_the_browser_and_keeps_the_credential_in_the_keychain_only(company_server, tmp_path):
    view = _join()

    assert view["state"] == "joined" and view["failure"] is None
    assert (view["company"], view["origin"]) == ("rowbot.example.test", ORIGIN)
    assert (view["name"], view["email"]) == ("Fixture Person", "person@example.test")
    assert view["computer_name"] == "Fixture laptop" and view["joined_at"]
    # The browser opened the server's sign-in with PKCE, a state and this computer; nothing else was asked first.
    assert company_server.bad_requests == []
    asked = company_server.authorize[0]
    assert (asked["device_name"], asked["platform"], asked["app_version"]) == ("Fixture laptop", sign_in.platform_name(),
                                                                            __version__)
    assert "device_id" not in asked
    # Then the company's models (C2), so every model list has them at once.
    assert [request.url.path for request in company_server.requests] == ["/healthz", "/api/device/token", "/api/me",
                                                                         "/api/models"]
    # The loopback page says to close the tab and loads nothing.
    (answer,) = company_server.loopback_answers
    assert answer.status == 200 and "You can close this tab." in answer.body
    assert answer.policy.startswith("default-src 'none'") and "src=" not in answer.body and "href" not in answer.body
    # A bearer on each call, never the cookie the server set.
    assert all("cookie" not in request.headers for request in company_server.requests)
    assert company_server.requests[-1].headers["authorization"].startswith("Bearer rboa_")
    # The credential is in the keychain; the record in the data folder holds nothing secret.
    credential = store.read_credential()
    assert credential.access_token in company_server.access and credential.refresh_token in company_server.refresh
    text = (tmp_path / "data" / "company.json").read_text(encoding="utf-8")
    record = json.loads(text)
    assert record["device_id"] in company_server.devices and record["disconnected"] is False
    assert not any(token in text for token in _tokens(company_server))
    assert not any(token in json.dumps(view) for token in _tokens(company_server))


def test_an_answer_with_another_state_is_refused_and_the_sign_in_waits_for_its_own(company_server):
    company_server.browser = "wrong_state"

    view = _join()

    assert [answer.status for answer in company_server.loopback_answers] == [400, 200]
    assert "isn't Row-Bot's current sign-in" in company_server.loopback_answers[0].body
    assert view["state"] == "joined"


def test_cancel_stops_waiting_and_keeps_nothing(company_server, tmp_path):
    company_server.browser = "ignore"

    waiting = client_company.join(ORIGIN, "Fixture laptop")
    assert (waiting["state"], waiting["company"], waiting["rejoining"]) == ("waiting", "rowbot.example.test", False)
    view = client_company.cancel()

    assert (view["state"], view["failure"]) == ("not_joined", "cancelled")
    assert store.read_credential() is None and not (tmp_path / "data" / "company.json").exists()
    assert "/api/device/token" not in [request.url.path for request in company_server.requests]


def test_a_sign_in_that_never_comes_back_times_out(company_server, monkeypatch):
    company_server.browser = "ignore"
    ticks = itertools.count(0, 1000)
    monkeypatch.setattr(sign_in, "clock", lambda: next(ticks))

    view = _join()

    assert (view["state"], view["failure"]) == ("not_joined", "timed_out")
    assert store.read_credential() is None


def test_cancel_on_the_company_page_joins_nothing(company_server):
    company_server.browser = "cancel"

    view = _join()

    assert (view["state"], view["failure"]) == ("not_joined", "denied")
    assert "/api/device/token" not in [request.url.path for request in company_server.requests]
    assert "cancelled" in company_server.loopback_answers[0].body


@pytest.mark.parametrize(("address", "origin"), [
    ("rowbot.example.test", "https://rowbot.example.test"),
    ("  HTTPS://RowBot.Example.test:443/ ", "https://rowbot.example.test"),
    ("https://rowbot.example.test:8443", "https://rowbot.example.test:8443"),
    ("http://127.0.0.1:8080", "http://127.0.0.1:8080"),
    ("http://localhost:8080/", "http://localhost:8080"),
])
def test_an_address_becomes_one_plain_origin(address, origin):
    assert client.normalise_origin(address) == origin


@pytest.mark.parametrize(("address", "reason"), [
    ("http://rowbot.example.test", "not_https"),
    ("", "invalid_address"),
    ("https://person@rowbot.example.test", "invalid_address"),
    ("https://rowbot.example.test/console", "invalid_address"),
    ("https://rowbot.example.test/?next=x", "invalid_address"),
    ("ftp://rowbot.example.test", "invalid_address"),
    ("https://rowbot.example.test\\@other.test", "invalid_address"),
    ("https://rowbot example.test", "invalid_address"),
])
def test_an_address_that_isnt_a_plain_https_origin_is_refused(address, reason):
    with pytest.raises(client.InvalidAddress) as refused:
        client.normalise_origin(address)
    assert refused.value.reason == reason


def test_join_says_early_when_the_address_isnt_one_to_join(company_server):
    assert _join("http://rowbot.example.test")["failure"] == "not_https"
    assert _join("https://rowbot.example.test/console")["failure"] == "invalid_address"
    assert company_server.requests == []

    company_server.healthy = False
    view = _join()

    assert (view["state"], view["failure"]) == ("not_joined", "not_company_server")
    assert [request.url.path for request in company_server.requests] == ["/healthz"]
    assert company_server.authorize == []


def test_with_the_keychain_unavailable_join_is_refused_before_anything_is_asked(company_server, tmp_path):
    secret_store._set_backend_for_tests(MemoryKeyring(fail=True))

    view = _join()

    assert (view["state"], view["failure"]) == ("not_joined", "storage_unavailable")
    assert company_server.requests == [] and company_server.authorize == []
    assert not (tmp_path / "data" / "company.json").exists()


def test_no_token_or_code_is_ever_logged(company_server, caplog):
    caplog.set_level("DEBUG")

    _join()
    client_company.check()
    client_company.leave()

    assert caplog.text
    assert not any(token in caplog.text for token in _tokens(company_server))
    assert "rboa_" not in caplog.text and "rbor_" not in caplog.text and "rboc_" not in caplog.text
