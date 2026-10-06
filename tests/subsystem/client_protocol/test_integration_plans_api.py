"""The typed /integrations API over HTTP: sources, items, plans and presets, with fakes only."""
# ruff: noqa: F811 -- shared isolated fixtures
import base64
import json
from uuid import uuid4

import pytest

from row_bot.mcp_client import config, marketplace
from tests.subsystem.client_protocol.test_integrations_api import isolated  # noqa: F401
from tests.subsystem.client_protocol.test_mcp_configuration_api import client_for
from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.platform
BASE = "/api/v1/integrations"


@pytest.fixture
def keyed(isolated):
    document = json.loads(config.CONFIG_PATH.read_text())
    document["servers"]["Work"]["source"] = {"auth_mode": "api_key", "auth_bindings": [
        {"kind": "header", "name": "X-Api-Key", "key": "token", "prefix": ""}]}
    config.CONFIG_PATH.write_text(json.dumps(document))
    return isolated


def test_reads_need_a_session_and_never_contact_a_source(service, isolated, monkeypatch):
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a, **k: pytest.fail("a read contacted the Registry"))
    with client_for(service) as client:
        assert client.get(BASE + "/sources").status_code == 401
        _, headers = bootstrap(client)
        sources = {s["id"]: s for s in client.get(BASE + "/sources", headers=headers).json()["items"]}
        assert sources["official"]["enabled"] and not sources["glama"]["enabled"]
        presets = client.get(BASE + "/presets", headers=headers).json()["items"]
        assert [p["id"] for p in presets if p["default"]] == ["ask"]
        installed = client.get(BASE + "/items", headers=headers).json()["items"]
        work = next(row for row in installed if row["name"] == "Work")
        assert work["lifecycle"] == "off" and work["next_action"]["kind"] == "continue_setup"
        catalog = client.get(BASE + "/items?scope=catalog&query=notion", headers=headers).json()["items"]
        notion = next(row for row in catalog if row["app"] and row["app"]["id"] == "notion")
        detail = client.get(BASE + "/detail", params={"item_id": notion["id"]}, headers=headers).json()
        assert detail["plan"]["intent"] == "connect" and detail["plan"]["consent_token"] == ""
        assert client.get(BASE + "/detail", params={"item_id": "mcp:curated:missing"}, headers=headers).status_code == 404


def test_plan_needs_this_sessions_consent_to_the_exact_plan(service, keyed, monkeypatch):
    from row_bot.integrations import plans
    monkeypatch.setattr(plans, "_spawn", lambda work: work())  # The background run finishes before the response.
    with client_for(service) as client:
        _, headers = bootstrap(client)
        work = next(row for row in client.get(BASE + "/items", headers=headers).json()["items"] if row["name"] == "Work")
        review = client.post(BASE + "/plans/review", headers=headers, json={"item_id": work["id"]})
        assert review.status_code == 200, review.text
        plan = review.json()
        assert plan["consent_token"] and [s["type"] for s in plan["steps"]][:2] == ["consent", "inputs"]
        plan_id = str(uuid4())
        body = {"plan_id": plan_id, "item_id": work["id"], "digest": plan["digest"], "consent_token": "forged"}
        before = config.CONFIG_PATH.read_bytes()
        assert client.post(BASE + "/plans", headers={**headers, "Idempotency-Key": plan_id}, json=body).status_code == 409
        body["consent_token"] = plan["consent_token"]
        assert client.post(BASE + "/plans", headers={**headers, "Idempotency-Key": str(uuid4())}, json=body).status_code == 409
        changed = {**body, "digest": "0" * 64}
        assert client.post(BASE + "/plans", headers={**headers, "Idempotency-Key": plan_id}, json=changed).status_code == 409
        assert config.CONFIG_PATH.read_bytes() == before
        started = client.post(BASE + "/plans", headers={**headers, "Idempotency-Key": plan_id}, json=body)
        assert started.status_code == 200, started.text
        assert (started.json()["state"], started.json()["pause"]) == ("paused", "inputs")
        again = client.post(BASE + "/plans", headers={**headers, "Idempotency-Key": plan_id}, json=body)
        assert again.json()["plan_id"] == plan_id and again.json()["pause"] == "inputs"
        other = client.post(BASE + "/plans", headers={**headers, "Idempotency-Key": (second := str(uuid4()))},
                            json={**body, "plan_id": second})
        assert other.status_code == 409, "one consent starts one plan"
        assert client.get(BASE + "/plans/" + plan_id, headers=headers).json()["pause"] == "inputs"
        found = client.get(BASE + "/detail", params={"item_id": work["id"]}, headers=headers).json()["plan"]
        resumed = client.post(BASE + "/plans/review", headers=headers, json={"item_id": work["id"]}).json()
        assert found["plan_id"] == resumed["plan_id"] == plan_id and resumed["consent_token"] == ""
        cancelled = client.post(BASE + "/plans/" + plan_id + "/cancel", headers=headers)
        assert cancelled.json()["state"] == "cancelled"
        assert client.post(BASE + "/plans/" + plan_id + "/continue", headers=headers, json={}).status_code == 409


def test_catalog_updates_start_only_on_request_and_the_schedule_is_opt_in(service, isolated, monkeypatch):
    from row_bot.integrations import catalogs
    started = []
    monkeypatch.setattr(catalogs, "update", lambda source, **k: started.append(source) or {"state": "updating"})
    monkeypatch.setattr(catalogs, "_register", lambda: None)
    with client_for(service) as client:
        assert client.post(BASE + "/sources/official/update").status_code in {401, 403}
        _, headers = bootstrap(client)
        views = {s["id"]: s for s in client.get(BASE + "/sources", headers=headers).json()["items"]}
        assert views["official"]["catalog"]["state"] == "never" and views["recommended"]["catalog"] is None
        assert started == []
        response = client.post(BASE + "/sources/official/update", headers=headers)
        assert response.status_code == 202 and response.json()["id"] == "official" and started == ["official"]
        monkeypatch.setattr(catalogs, "update", lambda source, **k: (_ for _ in ()).throw(ValueError("not_updatable")))
        assert client.post(BASE + "/sources/recommended/update", headers=headers).status_code == 404
        assert client.get(BASE + "/catalog-schedule", headers=headers).json() == {"enabled": False, "interval_days": 7, "sources": None}
        assert client.put(BASE + "/catalog-schedule", headers=headers, json={"enabled": True, "interval_days": 2}).status_code == 422
        assert client.put(BASE + "/catalog-schedule", headers=headers,
                          json={"enabled": True, "interval_days": 7, "sources": ["glama"]}).status_code == 409
        saved = client.put(BASE + "/catalog-schedule", headers=headers, json={"enabled": True, "interval_days": 30})
        assert saved.json() == {"enabled": True, "interval_days": 30, "sources": None} == catalogs.schedule()


def test_a_hosted_broker_is_off_until_the_person_turns_it_on(service, isolated):
    with client_for(service) as client:
        assert client.put(BASE + "/sources/composio/opt-in", json={"on": True}).status_code in {401, 403}
        _, headers = bootstrap(client)
        views = {s["id"]: s for s in client.get(BASE + "/sources", headers=headers).json()["items"]}
        assert views["composio"]["enabled"] is False and views["composio"]["opt_in"]["on"] is False
        assert "up to a year" in views["composio"]["opt_in"]["disclosure"]
        assert all(view.get("opt_in") is None for key, view in views.items() if key != "composio")
        turned = client.put(BASE + "/sources/composio/opt-in", headers=headers, json={"on": True})
        assert turned.status_code == 200 and turned.json()["enabled"] is True and turned.json()["opt_in"]["on"] is True
        assert client.put(BASE + "/sources/official/opt-in", headers=headers, json={"on": True}).status_code == 404
        off = client.put(BASE + "/sources/composio/opt-in", headers=headers, json={"on": False}).json()
        assert off["enabled"] is False


def test_apps_and_icons_are_served_from_local_data_only(service, isolated, monkeypatch):
    from row_bot.integrations import icons
    monkeypatch.setattr(icons, "_download", lambda url: pytest.fail("an icon was fetched while rendering"))
    with client_for(service) as client:
        _, headers = bootstrap(client)
        apps = client.get(BASE + "/apps", headers=headers).json()["items"]
        notion = next(app for app in apps if app["id"] == "notion")
        assert notion["icon"] == "si:notion" and notion["icon_license"]["license"] == "CC0-1.0"
        # Every icon a screen shows comes back in one answer, as data the page can draw at once.
        found = client.get(BASE + "/icons", headers=headers,  # A read: rendering a screen never sends a change.
                           params={"ids": ",".join(["si:notion", "si:github", "letter:Q", "cached:" + "0" * 32, "si:notamark"])})
        assert found.status_code == 200, found.text
        icons_by_id = {item["id"]: item for item in found.json()["items"]}
        assert set(icons_by_id) == {"si:notion", "si:github", "letter:Q"}  # Unknown or uncached icons are left out.
        mark = base64.b64decode(icons_by_id["si:notion"]["data"].split(",", 1)[1])
        assert icons_by_id["si:notion"]["data"].startswith("data:image/svg+xml;base64,") and b"<script" not in mark
        assert icons_by_id["si:github"]["mono"] and not icons_by_id["letter:Q"]["mono"]  # A dark mark is flagged for dark mode.
        for refused in (["../../etc/passwd"], ["letter:QQ"], [], ["si:notion"] * 65):
            assert client.get(BASE + "/icons", headers=headers, params={"ids": ",".join(refused)}).status_code == 422
