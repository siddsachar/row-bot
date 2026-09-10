"""F-P12 records exact-model Thinking through authenticated HTTP DTO owners."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.contracts.client_platform.test_headless_lifecycle import platform as platform
from tests.contracts.client_platform.test_protocol_boundaries import client as client, protocol_clock as protocol_clock
from tests.contracts.client_platform.test_recordings import post_command
from tests.helpers.client_platform_fakes import RecordedProtocolTrace

ROOT = Path(__file__).resolve().parents[3]
MODEL = "model:fixture:fixture/model"
pytestmark = pytest.mark.contract


def test_record_f_p12_exact_model_thinking_http_roundtrip(platform, client, monkeypatch):
    from row_bot import threads
    from row_bot.api.v1.schemas import ConversationWorkspace
    from row_bot.providers import reasoning

    current = {MODEL: reasoning.ReasoningCapabilities(
        supported_efforts=("low", "high"), request_style="openai", revision="synthetic-effort-1")}
    monkeypatch.setattr(reasoning, "resolve_reasoning_capabilities_for_ref", current.get)
    trace = RecordedProtocolTrace("F-P12")

    def controls(label, revision, selection=None, *, model=MODEL, capability_revision=None):
        payload = {"model_selection": {"provider_id": "fixture", "model_ref": model},
                   "runtime_mode": "agent", "profile_id": "", "approval_mode": "approve"}
        if selection is not None:
            payload["reasoning"] = {"model_ref": model, "capability_revision": capability_revision,
                                    "selection": selection}
        return post_command(client, trace, "conversation.controls", label, payload, revision=revision)

    def workspace():
        response = client.get("/api/v1/conversations/conversation-a/workspace")
        assert response.status_code == 200, response.text
        value = response.json()
        assert ConversationWorkspace.model_validate_json(response.text).model_dump(
            mode="json", exclude_unset=True) == value
        trace.record("ConversationWorkspace", value)
        return value["reasoning"]

    # Legacy controls omit reasoning, then the new query projects provider default.
    assert controls("p12-select-model", "0").status_code == 200
    view = workspace()
    assert view["available"] and view["selection"] == {"kind": "provider_default"}
    assert [choice["label"] for choice in view["choices"]] == ["Provider default", "Low", "High"]
    effort_revision = view["capability_revision"]
    first = controls("p12-low", "1", {"kind": "effort", "effort": "low"}, capability_revision=effort_revision)
    assert first.status_code == 200, first.text
    replay = controls("p12-low", "1", {"kind": "effort", "effort": "low"}, capability_revision=effort_revision)
    assert replay.status_code == 200 and replay.json() == first.json()
    assert workspace()["selection"] == {"kind": "effort", "effort": "low"}
    conflict = controls("p12-stale-client", "1", {"kind": "effort", "effort": "high"}, capability_revision=effort_revision)
    assert conflict.status_code == 409 and conflict.json()["code"] == "revision_conflict"

    current[MODEL] = replace(current[MODEL], supported_efforts=(), supports_budget=True,
                            budget_min=128, budget_max=4096, request_style="google_budget", revision="synthetic-budget-2")
    view = workspace()
    assert view["stale"] and view["selection"] == {"kind": "provider_default"}
    assert threads.get_thread_reasoning_selection("conversation-a", MODEL) == {"kind": "effort", "effort": "low"}
    stale = controls("p12-stale-capability", "2", {"kind": "effort", "effort": "low"}, capability_revision=effort_revision)
    assert stale.status_code == 409 and stale.json()["code"] == "reasoning_capabilities_changed"
    invalid = controls("p12-invalid-budget", "2", {"kind": "budget", "budget": 127}, capability_revision=view["capability_revision"])
    assert invalid.status_code == 422 and invalid.json()["code"] == "invalid_reasoning_selection"
    assert controls("p12-budget", "2", {"kind": "budget", "budget": 128}, capability_revision=view["capability_revision"]).status_code == 200
    assert workspace()["selection"] == {"kind": "budget", "budget": 128}
    assert controls("p12-default", "3", {"kind": "provider_default"}, capability_revision=view["capability_revision"]).status_code == 200
    assert workspace()["selection"] == {"kind": "provider_default"}
    assert threads.get_thread_reasoning_selection("conversation-a", MODEL) is None
    assert controls("p12-unsupported", "4", model="model:fixture:unsupported").status_code == 200
    unavailable = workspace()
    assert not unavailable["available"] and unavailable["choices"] == []
    assert platform.get_conversation("conversation-a")["revision"] == "5"

    trace.barriers = ["Typed controls commit before query", "Replay reuses the accepted command identity",
                      "Exact-model capability changes before a stale client writes"]
    trace.assertions = ["Authenticated HTTP controls and workspace queries roundtrip generated DTOs",
                        "Legacy omitted reasoning remains accepted", "Effort and budget persist per qualified model",
                        "Stale capabilities and stale revisions reject without effects",
                        "Unsupported models have no active Thinking choices", "Provider default clears only the exact-model preference"]
    document = trace.document(ROOT)
    document["source"]["base_commit"] = "5c0f2217074d5ca940d8443d8c05a83329bc9dc1"
    for relative in ("src/row_bot/providers/reasoning.py", "tests/contracts/client_platform/test_reasoning_recording.py"):
        document["source"]["files"][relative] = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    document["source"]["sha256"] = hashlib.sha256(json.dumps(document["source"]["files"], sort_keys=True).encode()).hexdigest()
    trace.validate(ROOT, document)
    if os.environ.get("ROW_BOT_RECORD_PROTOCOL_FIXTURES") == "1":
        trace.write(ROOT, document)
    saved = json.loads((ROOT / "contracts/client-platform/v1/fixtures/F-P12.json").read_text(encoding="utf-8"))
    trace.validate(ROOT, saved)
    assert {key: value for key, value in saved.items() if key != "source"} == {
        key: value for key, value in document.items() if key != "source"}
