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


@pytest.mark.parametrize(("count", "words"), [(1, "1 proposal"), (2, "2 proposals")])
def test_prepared_proposals_are_counted_in_words(isolated_insights, monkeypatch, count, words):
    monkeypatch.setattr(evolution, "review_skill_library_dry_run",
                        lambda **_kwargs: {"summary": {"proposal_count": count}})
    monkeypatch.setattr(evolution, "ensure_proposals_for_insight",
                        lambda _insight: [{"id": f"p-{index}"} for index in range(count)])
    results = []
    for action in ("review_skills", "generate"):
        snapshot = owner.read_insights(validate=lambda: None)
        results.append(owner.execute_insight(
            {
                "command_id": str(uuid4()), "revision": snapshot["revision"],
                "action": action, "insight_id": "ins-test", "proposal_id": "",
                "reason": "",
            },
            owner_id="owner", validate=lambda: None,
        )["summary"])
    assert results == [f"Skill review prepared {words}.", f"Prepared {words}."]


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


def test_a_dismissed_insight_can_be_restored_once(isolated_insights):
    """Undo after Dismiss (decision 19): restore brings a dismissed insight back."""
    current, _proposal, _calls = isolated_insights

    def act(action):
        snapshot = owner.read_insights(validate=lambda: None)
        return owner.execute_insight(
            {"command_id": str(uuid4()), "revision": snapshot["revision"], "action": action,
             "insight_id": "ins-test", "proposal_id": "", "reason": ""},
            owner_id="owner", validate=lambda: None,
        )

    assert act("dismiss")["status"] == "completed"
    assert current["status"] == "dismissed"
    assert owner.read_insights(validate=lambda: None)["items"] == []
    assert act("restore")["status"] == "completed"
    assert current["status"] == "new"
    assert [item["id"] for item in owner.read_insights(validate=lambda: None)["items"]] == ["ins-test"]
    # Only a dismissed insight is restored; a shown one is left alone.
    with pytest.raises(ClientPlatformError, match="insight_unavailable"):
        act("restore")


def _apply(proposal_id="proposal-test"):
    snapshot = owner.read_insights(validate=lambda: None)
    return owner.execute_insight(
        {"command_id": str(uuid4()), "revision": snapshot["revision"], "action": "apply",
         "insight_id": "ins-test", "proposal_id": proposal_id, "reason": ""},
        owner_id="owner", validate=lambda: None,
    )


@pytest.mark.parametrize("kind", ["consolidate_skills", "settings_change", "memory_correction"])
def test_a_review_only_proposal_offers_no_apply_and_is_never_reported_applied(isolated_insights, kind):
    """B124: Row-Bot can't carry out these kinds, so applying one is refused
    in words instead of saying "Proposal applied." for a change that never happened."""
    _current, proposal, calls = isolated_insights
    assert owner.read_insights(validate=lambda: None)["items"][0]["proposals"][0]["executable"] is True
    proposal.update(proposal_type=kind)
    view = owner.read_insights(validate=lambda: None)["items"][0]["proposals"][0]
    assert view["executable"] is False
    with pytest.raises(ClientPlatformError, match="insight_proposal_draft_only"):
        _apply()
    assert calls == []


@pytest.mark.parametrize(("kind", "message", "summary"), [
    ("create_skill", "Skill created: Weekly digest", "Skill created: Weekly digest."),
    ("patch_skill", "Skill patched: Weekly digest", "Skill patched: Weekly digest."),
    ("investigate", "Investigation thread created: fixture-thread", "Investigation draft ready to open."),
    ("send_feedback", "Feedback report prepared: C:\\Users\\Fixture\\report.md\nSubmit here: https://example.test",
     "Feedback report saved on this computer. Nothing was sent; copy it to send it yourself."),
])
def test_applying_says_what_happened_for_its_kind(isolated_insights, monkeypatch, kind, message, summary):
    """B124: the receipt names what the proposal did (a feedback report is
    saved, never sent), not a generic "Proposal applied."; no private path."""
    _current, proposal, _calls = isolated_insights
    proposal.update(proposal_type=kind)
    monkeypatch.setattr(evolution, "apply_proposal", lambda *_args, **_kwargs: {"ok": True, "message": message})
    result = _apply()
    assert result["status"] == "completed"
    assert result["summary"] == summary
    assert "Fixture" not in result["summary"]


def test_an_insight_says_when_it_may_no_longer_apply(isolated_insights, monkeypatch):
    """B124: an insight found while another model was in use, or a new one
    found more than two weeks ago, says it may be out of date."""
    from datetime import datetime, timedelta, timezone
    import importlib

    current, _proposal, _calls = isolated_insights
    models = importlib.import_module("row_bot.models")
    monkeypatch.setattr(models, "get_current_model", lambda: "gpt-5.6-sol")
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()

    def item():
        return owner.read_insights(validate=lambda: None)["items"][0]

    current.update(created=recent, found_with_model="gpt-5.6-sol")
    assert item()["found_at"] == recent
    assert item()["out_of_date"] == ""
    current.update(found_with_model="qwen3.8:27b")
    assert item()["out_of_date"] == "Found while another model was in use, so it may no longer apply."
    current.update(created=old, found_with_model="gpt-5.6-sol")
    assert item()["out_of_date"] == "Found more than two weeks ago, so it may no longer apply."
    # Pinned is the person's choice to keep it: age alone doesn't flag it.
    current.update(status="pinned")
    assert item()["out_of_date"] == ""
