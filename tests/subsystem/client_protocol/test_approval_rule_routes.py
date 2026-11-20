"""D1 Always allow over HTTP: a rule is made only from a real card the person sees, the client can't say
what it covers, editing a command is checked again, and "Would this have asked?" counts the log."""

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.

from __future__ import annotations

import time
from pathlib import Path
from uuid import uuid4

import pytest

from row_bot import activity_log, approval_rules
from row_bot.approval_rules import COMMAND_RISK
from row_bot.tools import approval_gate
from tests.subsystem.client_protocol.test_protocol_application import _client, service
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


@pytest.fixture
def project(tmp_path, monkeypatch, service) -> Path:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    folder = tmp_path / "row-bot"
    folder.mkdir()
    yield folder
    activity_log.flush()


def _card(text: str, folder: Path) -> dict:
    """The card the gate raises for a command, as the person sees it waiting."""

    payload = {"tool": "run_command", "label": "Run shell command", "description": f"Run shell command: {text}",
               "args": {"command": text}, "folder": folder.name, "folder_path": str(folder)}
    shown: list[dict] = []
    approval_gate.resolve_approval_line(payload, approval_mode="approve", risks=(COMMAND_RISK,),
                                        approval_callback=lambda card: shown.append(card) or False)
    return {**shown[0], "__interrupt_id": "interrupt-1"}


def _waiting(card: dict) -> str:
    from row_bot import tasks, threads

    chat = threads.create_thread("Run the tests", seed_default_skills=False)
    _token, approval_id = tasks.create_approval_request(
        "pass-1", "", "conversation", card["description"], resume_kind="conversation",
        source_thread_id=chat, parent_thread_id=chat, approval_payload_json={
            "interrupt": card, "interrupt_ids": ["interrupt-1"],
            "model_selection": {"provider_id": "fixture", "model_ref": "fixture/model"}})
    return approval_id


def _save(client, headers, approval_id: str, **extra):
    view = client.get(f"/api/v1/approvals/{approval_id}", headers=headers).json()
    return client.post("/api/v1/approval-rules", headers=headers, json={
        "approval_id": approval_id, "nonce": view["nonce"], "command_id": str(uuid4()), **extra})


def test_a_rule_is_made_from_the_card_the_person_sees(service, project):
    approval_id = _waiting(_card("npm test", project))
    with _client(service) as client:
        _, headers = bootstrap(client)
        view = client.get(f"/api/v1/approvals/{approval_id}", headers=headers).json()
        assert view["rule_eligible"] is True
        assert "folder_path" not in str(view) and str(project) not in str(view), "the card shows no path"

        saved = _save(client, headers, approval_id)
        assert saved.status_code == 200, saved.text
        rule = saved.json()
        assert (rule["kind"], rule["command"], rule["folder_name"], rule["enabled"], rule["uses"]) == (
            "command", "npm test", "row-bot", True, 0)
        assert rule["words"] == "Always allow `npm test` in row-bot"
        listed = client.get("/api/v1/approval-rules", headers=headers).json()
        assert [item["rule_id"] for item in listed["items"]] == [rule["rule_id"]] and listed["max_rules"] == 100


def test_the_client_cant_say_what_a_rule_covers(service, project):
    approval_id = _waiting(_card("npm test", project))
    other = _waiting(_card("npm run lint", project))
    with _client(service) as client:
        _, headers = bootstrap(client)
        for widened in ({"command": "rm -rf ."}, {"kind": "command"}, {"folder": "C:\\"}):
            assert _save(client, headers, approval_id, **widened).status_code == 422
        other_nonce = client.get(f"/api/v1/approvals/{other}", headers=headers).json()["nonce"]
        crossed = client.post("/api/v1/approval-rules", headers=headers, json={
            "approval_id": approval_id, "nonce": other_nonce, "command_id": str(uuid4())})
        assert crossed.status_code == 409 and crossed.json()["code"] == "approval_expired"
        assert approval_rules.list_rules() == []


def test_a_card_no_rule_may_cover_offers_none(service, project):
    approval_id = _waiting(_card("git push origin main", project))
    with _client(service) as client:
        _, headers = bootstrap(client)
        assert client.get(f"/api/v1/approvals/{approval_id}", headers=headers).json()["rule_eligible"] is False
        refused = _save(client, headers, approval_id)
        assert refused.status_code == 409 and refused.json()["code"] == "rule_not_eligible"
        dry = client.post("/api/v1/approval-rules/dry-run", headers=headers, json={"approval_id": approval_id})
        assert dry.status_code == 409


def test_only_a_signed_in_session_changes_rules(service, project):
    approval_id = _waiting(_card("npm test", project))
    rule = approval_rules.create(approval_rules.from_interrupt(_card("npm run lint", project)))
    origin = {"Origin": "http://localhost"}
    with _client(service) as client:
        assert client.post("/api/v1/approval-rules", headers=origin, json={
            "approval_id": approval_id, "nonce": "x" * 40, "command_id": str(uuid4())}).status_code in {401, 403}
        assert client.put(f"/api/v1/approval-rules/{rule.rule_id}", headers=origin,
                          json={"enabled": False}).status_code in {401, 403}
        assert client.delete(f"/api/v1/approval-rules/{rule.rule_id}", headers=origin).status_code in {401, 403}
        assert client.get("/api/v1/approval-rules", headers=origin).status_code in {401, 403}
    assert approval_rules.get_rule(rule.rule_id).enabled


def test_editing_switches_narrows_and_deletes(service, project):
    rule = approval_rules.create(approval_rules.from_interrupt(_card("npm test", project)))
    with _client(service) as client:
        _, headers = bootstrap(client)
        url = f"/api/v1/approval-rules/{rule.rule_id}"
        off = client.put(url, headers=headers, json={"enabled": False})
        assert off.status_code == 200 and off.json()["enabled"] is False
        refused = client.put(url, headers=headers, json={"command": "pip install requests"})
        assert refused.status_code == 422 and refused.json()["code"] == "rule_command_refused"
        narrowed = client.put(url, headers=headers, json={"command": "npm test -- --run"})
        assert narrowed.json()["words"] == "Always allow `npm test -- --run` in row-bot"
        assert client.put(url, headers=headers, json={}).status_code == 422
        assert client.put("/api/v1/approval-rules/0123456789abcdef", headers=headers,
                          json={"enabled": True}).status_code == 404
        assert client.delete(url, headers=headers).json()["items"] == []
        assert client.delete(url, headers=headers).status_code == 404


def test_would_this_have_asked(service, project):
    card = _card("npm test", project)
    approval_id = _waiting(card)
    scope = approval_rules.from_interrupt(card)
    for index, (outcome, decided) in enumerate((("approved_by_you", "allow"), ("approved_by_you", "allow"),
                                                ("denied", "deny"))):
        note = {"stage": "asking", "level": "approve", "description": f"Run shell command: npm test #{index}",
                **scope.note()}
        activity_log.record(activity_log.ToolCall(
            call_id=f"dry-{index}", tool="run_command", thread_id="chat-dry", at=time.time() - 3600 * (index + 1),
            decisions=(note, {**note, "stage": "decided", "outcome": decided, "by": "person"})))
    assert activity_log.flush()
    rule = approval_rules.create(scope)
    with _client(service) as client:
        _, headers = bootstrap(client)
        response = client.post("/api/v1/approval-rules/dry-run", headers=headers, json={"approval_id": approval_id})
        assert response.status_code == 200, response.text
        result = response.json()
        assert (result["days"], result["total"], result["rule"]["words"]) == (
            30, 3, "Always allow `npm test` in row-bot")
        assert (result["counts"]["approved_by_you"], result["counts"]["denied"]) == (2, 1)
        assert [example["outcome"] for example in result["examples"]] == ["denied", "approved_by_you",
                                                                          "approved_by_you"]
        edited = client.post("/api/v1/approval-rules/dry-run", headers=headers,
                             json={"rule_id": rule.rule_id, "command": "npm run lint"}).json()
        assert (edited["total"], edited["rule"]["command"]) == (0, "npm run lint")
        for bad in ({}, {"approval_id": approval_id, "rule_id": rule.rule_id},
                    {"approval_id": approval_id, "command": "npm test"}):
            assert client.post("/api/v1/approval-rules/dry-run", headers=headers, json=bad).status_code == 422
