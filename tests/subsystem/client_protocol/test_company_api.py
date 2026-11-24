"""Settings › Company through the client API (C1b): the state, Join and Sign in again on this computer only,
Cancel, Check and Leave; and with no company joined, nothing of it runs."""

from __future__ import annotations

import sys

import httpx
import pytest

import row_bot.company
from row_bot.application import client_company
from row_bot.company import managed
from tests.fixtures.company_server import (  # noqa: F401 - the fixtures
    ATLAS,
    COMPANY_SPACE,
    ORIGIN,
    ORION,
    SALES_SPACE,
    company_server,
    company_state,
    keychain,
)
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
        # Knowledge home's Company scope (B8) asks nothing either.
        graph = app.get(f"/api/v1/company/graph?spaces={SALES_SPACE}&q=atlas", headers=headers)
        entity = app.get(f"/api/v1/company/entities/{ATLAS}", headers=headers)

    assert view.status_code == 200, view.text
    assert view.json()["state"] == "not_joined" and view.json()["on_this_computer"] is True
    assert view.json()["failure"] is None and view.json()["company"] is None
    assert (view.json()["policy_state"], view.json()["rules"], view.json()["locked"]) == ("not_joined", [], [])
    assert (view.json()["pinned_server"], view.json()["pin_invalid"]) == (None, False)
    assert checked.json()["state"] == left.json()["state"] == "not_joined"
    assert graph.status_code == entity.status_code == 200, graph.text + entity.text
    assert (graph.json()["state"], graph.json()["nodes"], graph.json()["spaces"]) == ("not_joined", [], [])
    assert (entity.json()["state"], entity.json()["entity"]) == ("not_joined", None)
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


def test_a_company_server_set_by_it_refuses_another_address_and_leave(company_server, monkeypatch):  # noqa: F811
    monkeypatch.setattr(managed, "_read", lambda: ORIGIN)  # IT's setting (C5), the OS source faked.
    app, _, _ = client_app()
    with app:
        _, headers = bootstrap(app)
        view = app.get("/api/v1/company", headers=headers)
        elsewhere = app.post("/api/v1/company/join", headers=headers, json={"address": "elsewhere.example.test"})
        started = app.post("/api/v1/company/join", headers=headers, json={"address": ORIGIN})
        client_company.wait()
        left = app.post("/api/v1/company/leave", headers=headers)
        after = app.get("/api/v1/company", headers=headers)

    assert view.status_code == 200 and view.json()["pinned_server"] == ORIGIN
    assert elsewhere.status_code == 409 and elsewhere.json()["code"] == "company_pinned"
    assert started.status_code == 200, started.text
    assert left.status_code == 409 and left.json()["code"] == "company_pinned"
    assert (after.json()["state"], after.json()["origin"]) == ("joined", ORIGIN)


def test_the_company_graph_and_an_entity_through_the_api_with_no_token_in_them(company_server):  # noqa: F811
    company_server.join_here()
    company_server.brain_entities[ORION] = {
        "entity": {"id": ORION, "space_id": SALES_SPACE, "subject": "Orion Foods", "entity_type": "customer",
                   "description": "A customer since 2024.", "aliases": [], "status": "active", "merged_into": None,
                   "edited_by": None, "edited_at": None, "created_at": "2026-11-01T09:00:00+00:00",
                   "updated_at": "2026-11-20T09:00:00+00:00"},
        "space": {"id": SALES_SPACE, "name": "Sales", "kind": "group"},
        "relations": [],
        "mentions": [{"type": "document", "space": {"id": SALES_SPACE, "name": "Sales"},
                      "document": {"id": "d1", "title": "Accounts 2026", "file_name": "accounts.xlsx"}, "chunk": 0,
                      "excerpt": "Orion Foods renewed for two years."}],
        "mention_count": 1,
    }
    app, _, _ = client_app()
    with app:
        _, headers = bootstrap(app)
        every = app.get("/api/v1/company/graph", headers=headers)
        sales = app.get(f"/api/v1/company/graph?spaces={SALES_SPACE}&q=orion&limit=50", headers=headers)
        entity = app.get(f"/api/v1/company/entities/{ORION}", headers=headers)
        unknown = app.get("/api/v1/company/entities/not-an-entity", headers=headers)

    assert every.status_code == 200, every.text
    assert every.json()["state"] == "ready" and len(every.json()["nodes"]) == 3
    assert [space["id"] for space in every.json()["spaces"]] == [COMPANY_SPACE, SALES_SPACE]
    assert sales.status_code == 200, sales.text
    assert [node["id"] for node in sales.json()["nodes"]] == [ORION]
    assert sales.json()["space_ids"] == [SALES_SPACE]
    assert entity.status_code == 200, entity.text
    assert entity.json()["entity"]["subject"] == "Orion Foods"
    assert entity.json()["mentions"] == [{"kind": "document", "space": "Sales", "document": "Accounts 2026",
                                          "excerpt": "Orion Foods renewed for two years."}]
    assert (unknown.status_code, unknown.json()["state"]) == (200, "not_found")
    for answer in (every, sales, entity, unknown):
        assert not any(token in answer.text for token in company_server.issued)
