"""Exact-model Thinking uses retained storage and reaches real request preparation."""
from __future__ import annotations

from contextvars import Context
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.contracts.client_platform.test_headless_lifecycle import platform, command  # noqa: F401
from tests.helpers.client_platform_fakes import ScriptedAgentStream, StreamBarrier, fixture_id
from tests.subsystem.client_platform.test_client_queue import completed, settle

pytestmark = pytest.mark.subsystem
MODEL = "model:fixture:fixture/model"


def enqueue(service, label, text="Queued synthetic input"):
    return service.execute(owner_id="fixture-owner", idempotency_key=fixture_id(label + ":key"),
        target="conversation-a", command=command("conversation.steer", label,
        {"steering_id": fixture_id(label), "text": text},
        revision=service.get_conversation("conversation-a")["revision"]))


@pytest.fixture
def capabilities(monkeypatch):
    from row_bot.providers import reasoning
    caps = reasoning.ReasoningCapabilities(supported_efforts=("low", "high"), request_style="openai", revision="one")
    current = {MODEL: caps}
    monkeypatch.setattr(reasoning, "resolve_reasoning_capabilities_for_ref", current.get)
    return current


def controls(service, label, selection=None, *, model=MODEL, cap_revision=None, revision=None):
    from row_bot.application.reasoning_controls import reasoning_view
    view = reasoning_view("conversation-a", model)
    payload = {"model_selection": {"provider_id": "fixture", "model_ref": model},
               "runtime_mode": "agent", "profile_id": "", "approval_mode": "approve"}
    if selection is not None:
        payload["reasoning"] = {"model_ref": model, "capability_revision": cap_revision or view["capability_revision"],
                                "selection": selection}
    return service.execute(owner_id="fixture-owner", idempotency_key=fixture_id(label + ":key"), target="conversation-a",
        command=command("conversation.controls", label, payload,
                        revision=revision or service.get_conversation("conversation-a")["revision"]))


def test_exact_model_query_choices_unsupported_budget_and_stale_read_only(platform, capabilities):
    from row_bot import threads
    from row_bot.application.reasoning_controls import reasoning_view
    from row_bot.providers.reasoning import ReasoningCapabilities
    assert [choice["label"] for choice in reasoning_view("conversation-a", MODEL)["choices"]] == ["Provider default", "Low", "High"]
    unavailable = reasoning_view("conversation-a", "model:fixture:other")
    assert unavailable["available"] is False and unavailable["choices"] == []
    capabilities[MODEL] = ReasoningCapabilities(supports_budget=True, budget_min=128, budget_max=4096, request_style="google_budget")
    view = reasoning_view("conversation-a", MODEL)
    assert view["supports_budget"] and (view["budget_min"], view["budget_max"]) == (128, 4096)
    threads.set_thread_reasoning_selection("conversation-a", MODEL, {"kind": "effort", "effort": "high"})
    view = reasoning_view("conversation-a", MODEL)
    assert view["stale"] and view["selection"] == {"kind": "provider_default"}
    assert threads.get_thread_reasoning_selection("conversation-a", MODEL)["effort"] == "high"


def test_control_save_reload_switch_default_and_legacy_payload_preserve_other_models(platform, capabilities):
    from row_bot import threads
    from row_bot.application.reasoning_controls import reasoning_view
    controls(platform, "save-high", {"kind": "effort", "effort": "high"})
    assert reasoning_view("conversation-a", MODEL)["selection"] == {"kind": "effort", "effort": "high"}
    controls(platform, "other-model", model="model:fixture:other")
    controls(platform, "switch-back")
    assert threads.get_thread_reasoning_selection("conversation-a", MODEL)["effort"] == "high"
    assert threads.get_thread_reasoning_selection("conversation-b", MODEL) is None
    controls(platform, "provider-default", {"kind": "provider_default"})
    assert threads.get_thread_reasoning_selection("conversation-a", MODEL) is None


def test_controls_revision_cas_two_clients_and_response_loss_reuse_one_receipt(platform, capabilities):
    from row_bot.application.client_platform import ClientPlatformError
    first = controls(platform, "two-client-first", {"kind": "effort", "effort": "low"}, revision="0")
    assert controls(platform, "two-client-first", {"kind": "effort", "effort": "low"}, revision="0") == first
    with pytest.raises(ClientPlatformError, match="revision_conflict"):
        controls(platform, "two-client-second", {"kind": "effort", "effort": "high"}, revision="0")
    assert platform.get_conversation("conversation-a")["revision"] == "1"


def test_stale_capabilities_and_invalid_choices_fail_without_changing_other_controls(platform, capabilities):
    from row_bot import threads
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.application.reasoning_controls import reasoning_view, validated_control
    old = reasoning_view("conversation-a", MODEL)
    capabilities[MODEL] = replace(capabilities[MODEL], supported_efforts=("low",), revision="two")
    with pytest.raises(ClientPlatformError, match="reasoning_capabilities_changed"):
        controls(platform, "stale-caps", {"kind": "effort", "effort": "low"}, cap_revision=old["capability_revision"])
    with pytest.raises(ClientPlatformError, match="invalid_reasoning_selection"):
        controls(platform, "invalid-high", {"kind": "effort", "effort": "high"})
    with pytest.raises(ClientPlatformError, match="reasoning_model_mismatch"):
        validated_control(MODEL, {"model_ref": "model:fixture:other"})
    assert platform.get_conversation("conversation-a")["revision"] == "0"
    assert threads.get_thread_reasoning_selection("conversation-a", MODEL) is None


def test_budget_boundary_and_on_off_reuse_existing_domain_validation(platform, capabilities):
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.providers.reasoning import ReasoningCapabilities
    capabilities[MODEL] = ReasoningCapabilities(supports_budget=True, budget_min=128, budget_max=4096,
                                              can_disable=True, thinking_mode="toggle", request_style="google_budget")
    for index, selection in enumerate(({"kind": "budget", "budget": 128}, {"kind": "budget", "budget": 4096},
                                       {"kind": "off"}, {"kind": "on"})):
        assert controls(platform, f"valid-choice-{index}", selection)["status"] == "completed"
    for index, budget in enumerate((1, 4097)):
        with pytest.raises(ClientPlatformError, match="invalid_reasoning_selection"):
            controls(platform, f"invalid-budget-{index}", {"kind": "budget", "budget": budget})


def test_typed_payload_preserves_old_receipt_hash_shape_and_rejects_mixed_choices():
    from row_bot.api.v1.schemas import Command, ReasoningSelectionValue
    from pydantic import ValidationError
    import json
    old = command("conversation.controls", "legacy-controls", {"model_selection": None, "runtime_mode": "agent",
                  "profile_id": "", "approval_mode": "approve"})
    assert Command.model_validate_json(json.dumps(old)).payload == old["payload"]
    for value in ({"kind": "effort"}, {"kind": "provider_default", "effort": "high"},
                  {"kind": "budget", "budget": True}, {"kind": "on", "budget": 1024}):
        with pytest.raises(ValidationError):
            ReasoningSelectionValue.model_validate(value)


def test_snapshot_reaches_real_agent_chat_model_request_mapping_and_clears_between_invocations(platform, capabilities, monkeypatch):
    from row_bot import agent, threads
    from row_bot.application.reasoning_controls import freeze_reasoning
    from row_bot.providers import runtime, resolution, reasoning
    controls(platform, "accepted-low", {"kind": "effort", "effort": "low"})
    config = {"model_override": MODEL}
    freeze_reasoning(config, "conversation-a")
    threads.set_thread_reasoning_selection("conversation-a", MODEL, {"kind": "effort", "effort": "high"})
    actual = []
    monkeypatch.setattr(resolution, "resolve_provider_config", lambda *a, **kw: SimpleNamespace(
        runtime_model="fixture/model", provider_id="openai", selection_ref=MODEL))
    monkeypatch.setattr(runtime, "create_chat_model", lambda *a, reasoning_plan, **kw:
        actual.append(runtime._reasoning_constructor_kwargs(reasoning_plan, provider="openai")) or object())
    def invoke():
        agent._set_active_runtime_context(thread_id="conversation-a", model_override=MODEL, reasoning_snapshot=config["reasoning_snapshot"])
        agent._chat_only_llm(MODEL)
        assert actual[-1] == {"reasoning_effort": "low"}
        assert reasoning.request_plan_for("conversation-b", MODEL).is_default
        agent._set_active_runtime_context(thread_id="conversation-a", model_override=MODEL)
        agent._chat_only_llm(MODEL)
        assert actual[-1] == {"reasoning_effort": "high"}
    Context().run(invoke)


def test_queue_dispatch_keeps_admitted_selection_after_later_saved_change(platform, capabilities):
    from row_bot import threads
    from row_bot.providers import reasoning, runtime
    controls(platform, "queue-low", {"kind": "effort", "effort": "low"})
    first, second = StreamBarrier(), StreamBarrier()
    fake = ScriptedAgentStream(completed("reasoning-first", first), completed("reasoning-second", second))
    captured = []
    original = fake.stream
    def stream(text, enabled, config, **kwargs):
        snapshot = deepcopy(config["configurable"]["reasoning_snapshot"])
        def inspect():
            from row_bot import agent
            agent._set_active_runtime_context(thread_id="conversation-a", reasoning_snapshot=snapshot)
            plan = reasoning.request_plan_for("conversation-a", MODEL)
            captured.append(runtime._reasoning_constructor_kwargs(plan, provider="openai"))
        Context().run(inspect)
        yield from original(text, enabled, config, **kwargs)
    platform.stream_factory = stream
    receipt = platform._start("conversation-a", {"submission_id": fixture_id("reasoning-first"), "text": "Synthetic input",
        "model_selection": {"provider_id": "fixture", "model_ref": MODEL}}, resume=False)
    try:
        assert first.entered.wait(10)
        enqueue(platform, "reasoning-queued", "Queued synthetic input")
        threads.set_thread_reasoning_selection("conversation-a", MODEL, {"kind": "effort", "effort": "high"})
        first.release.set()
        assert second.entered.wait(10)
        assert captured == [{"reasoning_effort": "low"}, {"reasoning_effort": "low"}]
    finally:
        platform.registry.stop("conversation-a")
        first.release.set()
        second.release.set()
        assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)
    settle(platform)


def test_queued_capability_revocation_pauses_before_provider_dispatch(platform, capabilities):
    from row_bot.application import client_queue
    controls(platform, "revoke-low", {"kind": "effort", "effort": "low"})
    barrier = StreamBarrier()
    fake = ScriptedAgentStream(completed("revoke-first", barrier))
    platform.stream_factory = fake.stream
    receipt = platform._start("conversation-a", {"submission_id": fixture_id("revoke-first"), "text": "Synthetic input",
        "model_selection": {"provider_id": "fixture", "model_ref": MODEL}}, resume=False)
    try:
        assert barrier.entered.wait(10)
        queued = enqueue(platform, "revoke-queued")
        capabilities.clear()
        barrier.release.set()
        assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)
        assert settle(platform).items[0].state == "paused"
        assert client_queue._row("conversation-a", queued["submission_id"])["queue_state"] == "paused"
        assert len(fake.calls) == 1
    finally:
        barrier.release.set()


@pytest.mark.parametrize("resume", [False, True])
def test_actual_agent_and_resume_forward_frozen_reasoning_to_model_constructor(platform, capabilities, monkeypatch, resume):
    from row_bot import agent, threads
    from row_bot.application.reasoning_controls import freeze_reasoning
    from row_bot.providers import runtime
    controls(platform, "agent-low", {"kind": "effort", "effort": "low"})
    config = {"configurable": {"thread_id": "conversation-a", "model_override": MODEL, "runtime_mode": "agent"}}
    freeze_reasoning(config["configurable"], "conversation-a")
    threads.set_thread_reasoning_selection("conversation-a", MODEL, {"kind": "effort", "effort": "high"})
    monkeypatch.setattr(agent, "get_current_model", lambda: MODEL)
    monkeypatch.setattr(agent, "_selected_model_label_from_config", lambda _: (MODEL, MODEL))
    monkeypatch.setattr(agent, "_ensure_agent_mode_ready", lambda _: SimpleNamespace(provider_id="fixture", runtime_model="fixture/model"))
    monkeypatch.setattr(agent, "get_context_size", lambda _: 8192)
    monkeypatch.setattr(agent, "_log_runtime_decision", lambda **_: None)
    class ReachedModel(Exception):
        pass
    actual = []
    def constructor(model, plan, **kwargs):
        actual.append(runtime._reasoning_constructor_kwargs(plan, provider="openai"))
        raise ReachedModel()
    monkeypatch.setattr(agent, "_get_llm_for_reasoning_plan", constructor)
    def invoke():
        with pytest.raises(ReachedModel):
            list(agent.resume_stream_agent([], config, approved=True) if resume
                 else agent.stream_agent("Synthetic request", [], config))
    Context().run(invoke)
    assert actual == [{"reasoning_effort": "low"}]


@pytest.mark.parametrize("approval", [False, True])
def test_service_resume_restores_admitted_reasoning_after_preference_change(platform, capabilities, approval):
    from row_bot import threads
    controls(platform, "resume-cut-low", {"kind": "effort", "effort": "low"})
    event = ("interrupt", [{"__interrupt_id": fixture_id("resume-cut-interrupt"), "tool": "synthetic"}]) if approval else ("error", "synthetic interruption")
    fake = ScriptedAgentStream((event,), completed("resume-cut-final"))
    captured = []
    def resume_factory(enabled, config, approved, **kwargs):
        captured.append(deepcopy(config["configurable"]["reasoning_snapshot"]))
        yield from fake.resume(enabled, config, approved, **kwargs)
    platform.stream_factory, platform.resume_factory = fake.stream, resume_factory
    initial = platform._start("conversation-a", {"submission_id": fixture_id("resume-cut-input"), "text": "Synthetic input",
        "model_selection": {"provider_id": "fixture", "model_ref": MODEL}}, resume=False)
    handle = platform.registry.get(initial["execution_id"])
    assert handle.producer_done.wait(10)
    threads.set_thread_reasoning_selection("conversation-a", MODEL, {"kind": "effort", "effort": "high"})
    resumed = (platform._resolve_approval(handle.approval_id, {"decision": "approve"}) if approval else
               platform._start("conversation-a", {"model_selection": {"provider_id": "fixture", "model_ref": MODEL}}, resume=True))
    assert platform.registry.get(resumed["execution_id"]).producer_done.wait(10)
    assert captured[0]["selection"] == {"kind": "effort", "effort": "low"}


def test_stale_resume_capability_keeps_approval_pending_and_never_dispatches(platform, capabilities):
    from row_bot.application.client_platform import ClientPlatformError
    controls(platform, "approval-cut-low", {"kind": "effort", "effort": "low"})
    fake = ScriptedAgentStream((("interrupt", [{"__interrupt_id": fixture_id("approval-cut-interrupt"), "tool": "synthetic"}]),))
    platform.stream_factory, platform.resume_factory = fake.stream, fake.resume
    initial = platform._start("conversation-a", {"submission_id": fixture_id("approval-cut-input"), "text": "Synthetic input",
        "model_selection": {"provider_id": "fixture", "model_ref": MODEL}}, resume=False)
    handle = platform.registry.get(initial["execution_id"])
    assert handle.producer_done.wait(10)
    capabilities.clear()
    with pytest.raises(ClientPlatformError, match="reasoning_capabilities_changed"):
        platform._resolve_approval(handle.approval_id, {"decision": "approve"})
    assert platform.get_approval(handle.approval_id)["status"] == "pending"
    assert len(fake.calls) == 1


def test_explicit_manual_resume_model_change_uses_that_models_own_choice(platform, capabilities):
    from row_bot import threads
    other = "model:fixture:other"
    capabilities[other] = capabilities[MODEL]
    controls(platform, "manual-model-low", {"kind": "effort", "effort": "low"})
    fake = ScriptedAgentStream((("error", "synthetic interruption"),), completed("manual-model-final"))
    captured = []
    def resume_factory(enabled, config, approved, **kwargs):
        captured.append(deepcopy(config["configurable"]["reasoning_snapshot"]))
        yield from fake.resume(enabled, config, approved, **kwargs)
    platform.stream_factory, platform.resume_factory = fake.stream, resume_factory
    initial = platform._start("conversation-a", {"submission_id": fixture_id("manual-model-input"), "text": "Synthetic input",
        "model_selection": {"provider_id": "fixture", "model_ref": MODEL}}, resume=False)
    assert platform.registry.get(initial["execution_id"]).producer_done.wait(10)
    threads.set_thread_reasoning_selection("conversation-a", other, {"kind": "effort", "effort": "high"})
    resumed = platform._start("conversation-a", {"model_selection": {"provider_id": "fixture", "model_ref": other}}, resume=True)
    assert platform.registry.get(resumed["execution_id"]).producer_done.wait(10)
    assert captured[0]["model_ref"] == other and captured[0]["selection"] == {"kind": "effort", "effort": "high"}


def test_retained_resume_admission_restores_existing_snapshot(platform, capabilities):
    from row_bot import threads
    controls(platform, "retained-low", {"kind": "effort", "effort": "low"})
    platform.stream_factory = ScriptedAgentStream((("error", "synthetic interruption"),)).stream
    initial = platform._start("conversation-a", {"submission_id": fixture_id("retained-input"), "text": "Synthetic input",
        "model_selection": {"provider_id": "fixture", "model_ref": MODEL}}, resume=False)
    assert platform.registry.get(initial["execution_id"]).producer_done.wait(10)
    threads.set_thread_reasoning_selection("conversation-a", MODEL, {"kind": "effort", "effort": "high"})
    config = {"configurable": {"thread_id": "conversation-a", "model_override": MODEL}}
    handle = platform.admit_execution("conversation-a", config)
    try:
        assert config["configurable"]["reasoning_snapshot"]["selection"] == {"kind": "effort", "effort": "low"}
    finally:
        platform.finish_execution(handle, "interrupted")
