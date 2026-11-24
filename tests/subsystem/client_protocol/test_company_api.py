"""Settings › Company through the client API (C1b): the state, Join and Sign in again on this computer only,
Cancel, Check and Leave; and with no company joined, nothing of it runs."""

from __future__ import annotations

import sys

import httpx
import pytest

import row_bot.company
from row_bot.application import client_company
from tests.fixtures.company_server import ORIGIN, company_server, company_state, keychain  # noqa: F401 - the fixtures
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = [pytest.mark.subsystem, pytest.mark.platform, pytest.mark.usefixtures("company_state")]


def test_with_no_company_the_app_makes_no_company_call_and_loads_nothing_that_opens_a_socket(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    # Loading the server client or the sign-in listener now fails, and so would any real request.
    for name in ("client", "sign_in"):
        monkeypatch.setitem(sys.modules, f"row_bot.company.{name}", None)
        monkeypatch.delattr(row_bot.company, name, raising=False)

    def no_request(*_: object, **__: object) -> httpx.Response:
        raise AssertionError("no company call without a company")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_request)
    app, _, _ = client_app()
    with app:
        _, headers = bootstrap(app)
        view = app.get("/api/v1/company", headers=headers)
        checked = app.post("/api/v1/company/check", headers=headers)
        left = app.post("/api/v1/company/leave", headers=headers)

    assert view.status_code == 200, view.text
    assert view.json()["state"] == "not_joined" and view.json()["on_this_computer"] is True
    assert view.json()["failure"] is None and view.json()["company"] is None
    assert (view.json()["policy_state"], view.json()["rules"], view.json()["locked"]) == ("not_joined", [], [])
    assert checked.json()["state"] == left.json()["state"] == "not_joined"
    assert not (tmp_path / "company.json").exists()


def test_join_and_sign_in_again_are_for_this_computer_only(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    local, _, _ = client_app()
    with local:
        _, headers = bootstrap(local)
        bad = local.post("/api/v1/company/join", headers=headers, json={"address": ""})
        assert bad.status_code == 422
        refused = local.post("/api/v1/company/join", headers=headers, json={"address": "http://rowbot.example.test"})
        assert refused.status_code == 200 and refused.json()["failure"] == "not_https"

    remote, _, _ = client_app(remote=True)
    with remote:
        _, headers = bootstrap(remote)
        phone = remote.get("/api/v1/company", headers=headers)
        assert phone.status_code == 200 and phone.json()["on_this_computer"] is False
        for path, body in (("/api/v1/company/join", {"address": ORIGIN}), ("/api/v1/company/sign-in-again", None)):
            answer = remote.post(path, headers=headers, json=body)
            assert answer.status_code == 409 and answer.json()["code"] == "desktop_only"


def test_a_company_joined_through_the_api_is_shown_without_any_token(company_server):  # noqa: F811 - the fixture
    app, _, _ = client_app()
    with app:
        _, headers = bootstrap(app)
        started = app.post("/api/v1/company/join", headers=headers,
                           json={"address": "rowbot.example.test", "device_name": "Fixture laptop"})
        assert started.status_code == 200, started.text
        client_company.wait()
        view = app.get("/api/v1/company", headers=headers)
        checked = app.post("/api/v1/company/check", headers=headers)
        left = app.post("/api/v1/company/leave", headers=headers)

    assert view.json()["state"] == "joined" and view.json()["name"] == "Fixture Person"
    assert view.json()["computer_name"] == "Fixture laptop"
    # The company's policy (C3a), in plain words.
    assert view.json()["policy_state"] == "current"
    assert {rule["setting"] for rule in view.json()["rules"]} >= {"models.allowed", "offline.max_days"}
    assert "Your company turned off personal API keys" in [lock["words"] for lock in view.json()["locked"]]
    assert checked.json()["state"] == "joined"
    assert (left.json()["state"], left.json()["notice"]) == ("not_joined", "left")
    for answer in (started, view, checked, left):
        assert not any(token in answer.text for token in company_server.issued)
