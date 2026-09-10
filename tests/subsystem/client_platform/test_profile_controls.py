"""Accepted profile instructions, skills and tool gates reach the real runtime."""
from __future__ import annotations

from contextvars import Context
from contextlib import closing
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tests.contracts.client_platform.test_headless_lifecycle import platform, submit  # noqa: F401
from tests.helpers.client_platform_fakes import ScriptedAgentStream, StreamBarrier
from tests.subsystem.client_platform.test_client_queue import completed, enqueue, settle

pytestmark = pytest.mark.subsystem


@pytest.fixture
def profile(platform):
    from row_bot import agent_profiles
    agent_profiles.ensure_agent_profiles_schema(force=True)
    return agent_profiles.save_agent_profile(slug="accepted-profile", display_name="Accepted profile",
        instructions="ORIGINAL accepted instructions", tool_policy_json={"allow_tools": ["read_fixture"]},
        skill_policy_json={"skills_override": ["accepted-skill"]})


def runtime_boundary(monkeypatch):
    """Enter real stream_agent and inspect exactly its graph-construction cut."""
    from row_bot import agent
    from row_bot.plugins import registry as plugins
    observed = []
    monkeypatch.setattr(agent, "_selected_model_label_from_config", lambda _: ("fixture/model", "fixture/model"))
    monkeypatch.setattr(agent, "get_context_size", lambda _: 8192)
    monkeypatch.setattr(agent, "_log_runtime_decision", lambda **_: None)
    monkeypatch.setattr(plugins, "get_langchain_tools", lambda **_: [])
    monkeypatch.setattr(plugins, "get_destructive_names", lambda **_: set())
    monkeypatch.setattr(plugins, "get_enabled_plugin_tool_records", lambda: [])
    monkeypatch.setattr(agent.tool_registry, "get_tool", lambda name: SimpleNamespace(
        as_langchain_tools=lambda: [SimpleNamespace(name=name)], destructive_tool_names=set()))

    class BoundaryReached(Exception):
        pass

    def graph(enabled_tool_names, *, model_override, tool_allowlist):
        core, _, _ = agent._collect_agent_tool_candidates(enabled_tool_names, set(tool_allowlist) if tool_allowlist is not None else None)
        observed.append({"tools": [entry["tool"].name for entry in core],
                         "allowlist": tool_allowlist, "model": model_override,
                         "instructions": agent._agent_profile_system_context("conversation-a"),
                         "skills": agent._active_profile_snapshot().get("skill_policy_json", {})})
        raise BoundaryReached()
    monkeypatch.setattr(agent, "get_agent_graph", graph)

    def inspect(config):
        def invoke():
            with pytest.raises(BoundaryReached):
                list(agent.stream_agent("Inspect public work", ["read_fixture", "write_fixture"], config))
        Context().run(invoke)
        return observed[-1]
    return inspect


def test_selected_profile_reaches_real_runtime_and_queued_dispatch_keeps_snapshot(platform, profile, monkeypatch):
    from row_bot import threads, agent_profiles
    inspect = runtime_boundary(monkeypatch)
    threads._set_thread_agent_profile("conversation-a", profile["id"])
    first, second = StreamBarrier(), StreamBarrier()
    fake = ScriptedAgentStream(completed("initial-profile", first), completed("queued-profile", second))
    captured = []
    original = fake.stream
    def stream(text, enabled, config, **kwargs):
        captured.append((deepcopy(config["configurable"]), inspect(config)))
        yield from original(text, enabled, config, **kwargs)
    fake.stream = stream
    receipt = submit(platform, fake, "profile-admission")
    try:
        assert first.entered.wait(10)
        enqueue(platform, "profile-queued", "Accepted pending input")
        agent_profiles.save_agent_profile({**profile, "instructions": "CHANGED later instructions",
            "tool_policy_json": {"allow_tools": ["write_fixture"]},
            "skill_policy_json": {"skills_override": ["changed-skill"]}, "enabled": False})
        first.release.set()
        assert second.entered.wait(10)
        assert len(captured) == 2
        for config, boundary in captured:
            assert config["agent_profile_frozen"] is True
            assert boundary["tools"] == ["read_fixture"]
            assert boundary["allowlist"] == ("read_fixture",)
            assert "ORIGINAL accepted instructions" in boundary["instructions"]
            assert "CHANGED" not in boundary["instructions"]
            assert boundary["skills"]["skills_override"] == ["accepted-skill"]
            assert boundary["model"] == "model:fixture:fixture/model"
        assert captured[0][0]["agent_profile_snapshot"] == captured[1][0]["agent_profile_snapshot"]
    finally:
        platform.registry.stop("conversation-a")
        first.release.set()
        second.release.set()
        assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)
    settle(platform)


def test_frozen_no_profile_does_not_adopt_later_thread_selection(platform, profile, monkeypatch):
    from row_bot import threads
    from row_bot.application.profile_controls import freeze_profile
    inspect = runtime_boundary(monkeypatch)
    config = {"thread_id": "conversation-a", "model_override": "fixture/model", "runtime_mode": "agent", "agent_profile_id": ""}
    freeze_profile(config)
    threads._set_thread_agent_profile("conversation-a", profile["id"])
    freeze_profile(config)
    boundary = inspect({"configurable": config})
    assert boundary["instructions"] == "" and boundary["skills"] == {}
    assert config["agent_profile_snapshot"] == {}
    # Absence of the marker retains legacy runtime fallback for other adapters.
    legacy = {key: value for key, value in config.items() if key != "agent_profile_frozen"}
    assert "ORIGINAL accepted instructions" in inspect({"configurable": legacy})["instructions"]


def test_profile_restrictions_intersect_explicit_gates_and_preserve_empty_semantics(platform, profile):
    from row_bot.application.profile_controls import freeze_profile
    from row_bot.application.client_queue import freeze_context
    from row_bot.agent import _runtime_tool_allowlist
    for original, expected in ((None, ("read_fixture",)), ([], ()), (["write_fixture"], ())):
        config = {"agent_profile_id": profile["id"], "tool_allowlist": original}
        freeze_profile(config)
        assert _runtime_tool_allowlist(config) == expected
        frozen = freeze_context({"configurable": config}, (), [])
        assert frozen["configurable"]["tool_allowlist"] == list(expected)
    for original in (None, []):
        config = {"agent_profile_id": "", "tool_allowlist": original}
        freeze_profile(config)
        assert freeze_context({"configurable": config}, (), [])["configurable"]["tool_allowlist"] == original


@pytest.mark.parametrize("missing", [False, True])
def test_missing_or_disabled_selected_profile_fails_before_admission(platform, profile, missing):
    from row_bot import threads, agent_profiles
    from row_bot.application.client_platform import ClientPlatformError
    threads._set_thread_agent_profile("conversation-a", profile["id"])
    if missing:
        from row_bot.tasks import _get_conn
        with closing(_get_conn()) as conn, conn:
            conn.execute("DELETE FROM agent_profiles WHERE id=?", (profile["id"],))
    else:
        agent_profiles.save_agent_profile({**profile, "enabled": False})
    with pytest.raises(ClientPlatformError, match="profile_unavailable"):
        submit(platform, ScriptedAgentStream(), "disabled-profile")
    assert not platform.registry.active("conversation-a")
    assert threads.get_latest_checkpoint_messages("conversation-a") == []


def test_shared_admission_captures_implicit_legacy_pointer_but_explicit_empty_stays_empty(platform, profile):
    from row_bot import threads
    threads._set_thread_agent_profile("conversation-a", profile["id"])
    legacy = {"configurable": {"model_override": "fixture/model"}}
    handle = platform.admit_execution("conversation-a", legacy)
    assert legacy["configurable"]["agent_profile_snapshot"]["id"] == profile["id"]
    platform.finish_execution(handle, status="stopped")
    explicit = {"configurable": {"model_override": "fixture/model", "agent_profile_id": ""}}
    handle = platform.admit_execution("conversation-a", explicit)
    assert explicit["configurable"]["agent_profile_snapshot"] == {}
    platform.finish_execution(handle, status="stopped")
