"""Insights actions are explicit, revision-fenced, and duplicate-safe."""

from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest

from row_bot.application.client_platform import ClientPlatformError
from row_bot.application import client_insights as owner
from row_bot import evolution, insights

from tests.subsystem.client_protocol.test_empty_workspace_setup import (
    service, workspace_api,
)

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.


@pytest.fixture
def isolated_insights(monkeypatch):
    current = {
        "id": "ins-test",
        "title": "A useful observation",
        "body": "Synthetic finding",
        "suggestion": "Inspect a proposal",
        "category": "skill_proposal",
        "severity": "medium",
        "status": "new",
    }
    proposal = {
        "id": "proposal-test",
        "title": "Synthetic skill change",
        "proposal_type": "create_skill",
        "status": "ready",
        "risk": "low",
        "rationale": "Synthetic rationale",
        "verification_plan": "Verify locally",
        "preview": {"text": "Synthetic preview"},
    }
    calls = []
    monkeypatch.setattr(insights, "get_active_insights", lambda: [deepcopy(current)] if current["status"] != "dismissed" else [])
    monkeypatch.setattr(insights, "get_insight_by_id", lambda _id: deepcopy(current))
    monkeypatch.setattr(insights, "update_insight_status", lambda _id, status: current.update(status=status) or True)
    monkeypatch.setattr(evolution, "list_display_proposals_for_insight", lambda *_args, **_kwargs: [deepcopy(proposal)])
    monkeypatch.setattr(evolution, "get_proposal", lambda _id: deepcopy(proposal))
    monkeypatch.setattr(evolution, "list_curator_reports", lambda **_kwargs: [])
    monkeypatch.setattr(evolution, "apply_proposal", lambda *args, **kwargs: calls.append((args, kwargs)) or {"ok": True})
    records: dict[str, dict] = {}

    def metadata(_owner, command_id):
        row = records.get(command_id)
        return {"target": row["target"], "type": row["wire"]["type"]} if row else None

    def claim(_owner, key, wire, target, **_kwargs):
        row = records.get(key)
        if row:
            if row["wire"] != wire:
                raise ValueError("idempotency_mismatch")
            return row["result"]
        records[key] = {"wire": wire, "target": target, "result": None}
        return None

    def complete(_owner, key, result):
        records[key]["result"] = result
        return result

    monkeypatch.setattr(owner.admissions, "read_command_metadata", metadata)
    monkeypatch.setattr(owner.admissions, "claim_command", claim)
    monkeypatch.setattr(owner.admissions, "complete_command", complete)
    return current, proposal, calls


def test_inspect_is_read_only_and_apply_is_one_explicit_command(isolated_insights):
    _current, _proposal, calls = isolated_insights
    snapshot = owner.read_insights(validate=lambda: None)
    assert snapshot["items"][0]["proposals"][0]["preview"]
    assert calls == []
    command = {
        "command_id": str(uuid4()),
        "revision": snapshot["revision"],
        "action": "apply",
        "insight_id": "ins-test",
        "proposal_id": "proposal-test",
        "reason": "",
    }
    result = owner.execute_insight(command, owner_id="owner", validate=lambda: None)
    assert result["status"] == "completed"
    assert calls == [(('proposal-test',), {"require_approval": False, "approved_by_user": True})]
    assert owner.execute_insight(command, owner_id="owner", validate=lambda: None) == result
    assert len(calls) == 1


def test_stale_insight_command_cannot_change_status(isolated_insights):
    current, _proposal, _calls = isolated_insights
    snapshot = owner.read_insights(validate=lambda: None)
    with pytest.raises(ClientPlatformError, match="insight_revision_conflict"):
        owner.execute_insight(
            {
                "command_id": str(uuid4()),
                "revision": "0" * 64,
                "action": "dismiss",
                "insight_id": "ins-test",
                "proposal_id": "",
                "reason": "",
            },
            owner_id="owner",
            validate=lambda: None,
        )
    assert current["status"] == "new"
    assert snapshot["revision"] == owner.read_insights(validate=lambda: None)["revision"]


def test_failed_proposal_application_has_a_failed_receipt(isolated_insights, monkeypatch):
    _current, _proposal, _calls = isolated_insights
    monkeypatch.setattr(evolution, "apply_proposal", lambda *_args, **_kwargs: {"ok": False})
    snapshot = owner.read_insights(validate=lambda: None)
    result = owner.execute_insight(
        {
            "command_id": str(uuid4()), "revision": snapshot["revision"],
            "action": "apply", "insight_id": "ins-test", "proposal_id": "proposal-test",
            "reason": "",
        },
        owner_id="owner", validate=lambda: None,
    )
    assert result["status"] == "failed"
    assert result["summary"] == "Proposal failed; inspect its status."


def test_feedback_and_investigation_views_are_bounded(isolated_insights, monkeypatch):
    _current, proposal, _calls = isolated_insights
    proposal.update(
        proposal_type="send_feedback", preview={
            "feedback_draft": {"body": "Synthetic redacted report"},
        },
    )
    monkeypatch.setattr(evolution, "list_display_proposals_for_insight", lambda *_args, **_kwargs: [proposal])
    monkeypatch.setattr(evolution, "list_curator_reports", lambda **_kwargs: [{
        "created_at": "2026-09-23T00:00:00Z",
        "summary": {"manual_skill_count": 2, "finding_count": 1, "proposal_count": 1},
        "findings": [{"kind": "synthetic"}],
    }])
    feedback = owner.read_insights(validate=lambda: None)
    view = feedback["items"][0]["proposals"][0]
    assert view["feedback_body"] == "Synthetic redacted report"
    assert view["support_url"].startswith("https://")
    assert feedback["curator_report"]["finding_count"] == 1
    proposal.update(proposal_type="investigate", status="applied")
    monkeypatch.setattr(evolution, "list_action_runs", lambda **_kwargs: [{
        "result_refs": ["synthetic-thread-id"],
    }])
    investigation = owner.read_insights(validate=lambda: None)
    assert investigation["items"][0]["proposals"][0]["open_thread_id"] == "synthetic-thread-id"


def test_insight_api_checks_session_and_idempotency_key(workspace_api, monkeypatch):
    _, _, _, client, headers, _, _ = workspace_api
    snapshot = {"schema_version": 1, "items": [], "curator_report": None, "revision": "a" * 64}
    monkeypatch.setattr(owner, "read_insights", lambda *, validate: snapshot)
    calls = []

    def execute(command, *, owner_id, validate):
        validate()
        calls.append(command)
        return {
            "command_id": command["command_id"], "status": "completed",
            "summary": "Skill review completed.", "snapshot": snapshot,
        }

    monkeypatch.setattr(owner, "execute_insight", execute)
    response = client.get("/api/v1/insights", headers=headers)
    assert response.status_code == 200, response.text
    command_id = str(uuid4())
    command = {
        "command_id": command_id,
        "client_session_id": headers["X-Client-Session"],
        "revision": "a" * 64,
        "action": "review_skills",
    }
    denied = client.post("/api/v1/insights/commands", headers=headers, json=command)
    assert denied.status_code == 409
    assert calls == []
    accepted = client.post(
        "/api/v1/insights/commands",
        headers={**headers, "idempotency-key": command_id},
        json=command,
    )
    assert accepted.status_code == 200, accepted.text
    assert len(calls) == 1
