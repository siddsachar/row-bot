"""The typed /integrations API over HTTP: sources, items, plans and presets, with fakes only."""
# ruff: noqa: F811 -- shared isolated fixtures
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


def test_plan_needs_this_sessions_consent_to_the_exact_plan(service, keyed):
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
