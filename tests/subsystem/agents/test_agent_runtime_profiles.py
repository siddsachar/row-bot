from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import HumanMessage, SystemMessage

from row_bot.agent_budget import new_execution_budget


def _fresh_runtime_modules(tmp_path, reload_for_data_dir):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    _tasks, threads, agent_profiles, agent = reload_for_data_dir(
        data_dir, "row_bot.tasks", "row_bot.threads", "row_bot.agent_profiles", "row_bot.agent"
    )
    return threads, agent_profiles, agent


def _prompt_text(result: dict) -> str:
    return "\n".join(str(message.content) for message in result["llm_input_messages"])


def test_graph_interrupt_result_extracts_paused_state_for_non_streaming_invoke(
    tmp_path,
    reload_for_data_dir,
):
    _threads, _profiles, agent = _fresh_runtime_modules(
        tmp_path,
        reload_for_data_dir,
    )
    state = SimpleNamespace(
        next=("tools",),
        tasks=[
            SimpleNamespace(
                interrupts=[
                    SimpleNamespace(
                        id="interrupt-parent-1",
                        value={
                            "tool": "run_command",
                            "approval_reason": "Authorize the disposable smoke file.",
                        },
                    )
                ]
            )
        ],
    )

    assert agent._graph_interrupt_result(state) == {
        "type": "interrupt",
        "interrupts": [
            {
                "tool": "run_command",
                "approval_reason": "Authorize the disposable smoke file.",
                "__interrupt_id": "interrupt-parent-1",
            }
        ],
    }


def test_thread_agent_profile_is_injected_into_agent_mode_prompt(tmp_path, reload_for_data_dir):
    threads, _profiles, agent = _fresh_runtime_modules(tmp_path, reload_for_data_dir)
    thread_id = threads.create_thread("Profile runtime")
    threads._set_thread_agent_profile(thread_id, "quality_reviewer")

    agent._set_active_runtime_context(thread_id=thread_id, enabled_tool_names=[])
    trimmed = agent._pre_model_trim({
        "execution_budget": new_execution_budget(f"profile-{thread_id}"),
        "messages": [
            SystemMessage(content="Base system"),
            HumanMessage(content="Review this change."),
        ]
    })
    prompt = _prompt_text(trimmed)

    assert "AGENT PROFILE: Review" in prompt
    assert "Findings first" in prompt
    assert "capability=read_only" in prompt


def test_ordinary_profile_skill_selection_is_a_hard_canonical_boundary(tmp_path, monkeypatch, reload_for_data_dir):
    _threads, _profiles, agent = _fresh_runtime_modules(
        tmp_path,
        reload_for_data_dir,
    )
    import row_bot.skill_discovery as discovery
    import row_bot.skills_activation as activation

    records = [
        discovery.SkillRecord(
            canonical_id="manual_other",
            alias=None,
            display_name="Manual Other",
            icon="*",
            description="Other",
            tags=(),
            activation={},
            instructions="MANUAL_OTHER_BODY",
            source="manual",
            root=Path(tmp_path),
        ),
        discovery.SkillRecord(
            canonical_id="plugin:demo:selected",
            alias="selected",
            display_name="Plugin Selected",
            icon="*",
            description="Selected",
            tags=(),
            activation={},
            instructions="PLUGIN_SELECTED_BODY",
            source="plugin:demo",
            root=Path(tmp_path),
            plugin_name="Demo Plugin",
        ),
    ]
    monkeypatch.setattr(discovery, "collect_enabled_skill_records", lambda: records)
    activation.load_auto_skill(
        "profile-thread",
        "manual_other",
        available_ids={record.canonical_id for record in records},
    )
    agent._set_active_runtime_context(
        thread_id="profile-thread",
        runtime_surface="agent",
        agent_profile_snapshot={
            "id": "profile:plugin",
            "enabled": True,
            "skill_policy_json": {"skills_override": ["plugin:demo:selected"]},
        },
    )

    authorized, active_ids, discoverable, _fingerprint = agent._build_runtime_skill_snapshot()
    prompt = discovery.render_active_skills_prompt(agent._resolve_active_skill_records(authorized))

    assert [record.canonical_id for record in authorized] == ["plugin:demo:selected"]
    assert active_ids == ("plugin:demo:selected",)
    assert discoverable is False
    assert "PLUGIN_SELECTED_BODY" in prompt
    assert "plugin: Demo Plugin" in prompt
    assert "MANUAL_OTHER_BODY" not in prompt


def test_a_workflow_run_can_find_and_load_the_persons_skills_as_a_chat_can(tmp_path, monkeypatch,
                                                                           reload_for_data_dir):
    threads, _profiles, agent = _fresh_runtime_modules(tmp_path, reload_for_data_dir)
    import row_bot.skill_discovery as discovery

    def skill(skill_id: str) -> discovery.SkillRecord:
        return discovery.SkillRecord(canonical_id=skill_id, alias=None, display_name=skill_id, icon="*",
                                     description=skill_id, tags=(), activation={},
                                     instructions=f"{skill_id.upper()}_BODY", source="manual", root=Path(tmp_path))

    monkeypatch.setattr(discovery, "collect_enabled_skill_records", lambda: [skill("my_x_voice"), skill("pinned_one")])
    monkeypatch.setattr(agent, "is_background_workflow", lambda: True)
    thread_id = threads.create_thread("⚡ X content")
    threads.set_thread_skills_override(thread_id, ["pinned_one"])
    agent._set_active_runtime_context(thread_id=thread_id, runtime_surface="agent", agent_profile_snapshot={
        "id": "builtin:row_bot_default", "enabled": True, "skill_policy_json": {"skills_override": []}})

    authorized, active_ids, discoverable, _fingerprint = agent._build_runtime_skill_snapshot()

    # The person's skill made in another chat is findable and loadable in the workflow's run.
    assert {record.canonical_id for record in authorized} == {"my_x_voice", "pinned_one"}
    assert active_ids == ("pinned_one",), "the workflow's own pinned skill stays on"
    assert discoverable is True


def test_thread_agent_profile_is_injected_into_chat_only_prompt(tmp_path, reload_for_data_dir):
    threads, _profiles, agent = _fresh_runtime_modules(tmp_path, reload_for_data_dir)
    thread_id = threads.create_thread("Chat only profile")
    threads._set_thread_agent_profile(thread_id, "planner")

    agent._set_active_runtime_context(thread_id=thread_id, enabled_tool_names=[])
    messages = agent._build_chat_only_messages(thread_id, "Plan this.", context_window=4096)
    text = "\n".join(str(message.content) for message in messages if isinstance(message, SystemMessage))

    assert "AGENT PROFILE: Plan" in text
    assert "Turn fuzzy goals into a concise plan" in text


def test_disabled_thread_profile_warns_instead_of_silent_fallback(tmp_path, reload_for_data_dir):
    threads, profiles, agent = _fresh_runtime_modules(tmp_path, reload_for_data_dir)
    thread_id = threads.create_thread("Disabled profile")
    saved = profiles.save_agent_profile(
        slug="brief_reviewer",
        display_name="Brief Reviewer",
        description="Review briefly.",
        instructions="Only review the risk.",
        tool_policy_json={"capability": "read_only"},
        context_policy_json={"default_context_mode": "focused"},
        workspace_policy_json={"workspace_mode_default": "read_only"},
    )
    threads._set_thread_agent_profile(thread_id, saved["id"])
    profiles.save_agent_profile({**saved, "enabled": False})

    agent._set_active_runtime_context(thread_id=thread_id, enabled_tool_names=[])
    trimmed = agent._pre_model_trim({
        "execution_budget": new_execution_budget(f"disabled-profile-{thread_id}"),
        "messages": [
            SystemMessage(content="Base system"),
            HumanMessage(content="Review this change."),
        ]
    })
    prompt = _prompt_text(trimmed)

    assert "THREAD AGENT PROFILE WARNING" in prompt
    assert "brief_reviewer" in prompt
    assert "disabled" in prompt


def test_a_chats_profile_is_frozen_into_its_turn_without_narrowing_its_tools(tmp_path, reload_for_data_dir):
    threads, profiles, _agent = _fresh_runtime_modules(tmp_path, reload_for_data_dir)
    thread_id = threads.create_thread("Profile tool filter")
    saved = profiles.save_agent_profile(
        slug="tool_filter_smoke",
        display_name="Tool Filter Smoke",
        description="Constrain tools for smoke checks.",
        instructions="Use only the selected tools.",
        tool_policy_json={
            "capability": "read_only",
            "allow_tools": ["filesystem", "row_bot_status"],
        },
        context_policy_json={"default_context_mode": "focused"},
        workspace_policy_json={"workspace_mode_default": "read_only"},
    )
    threads._set_thread_agent_profile(thread_id, saved["id"])
    from row_bot.application.profile_controls import freeze_profile

    config = {"agent_profile_id": threads._get_thread_agent_profile(thread_id)["id"]}
    freeze_profile(config)

    assert config["agent_profile_id"] == saved["id"]
    assert config["agent_profile_snapshot"]["slug"] == "tool_filter_smoke"
    assert "tool_allowlist" not in config, "a profile lists no tools (F31)"
    missing = {"agent_profile_id": threads._get_thread_agent_profile("missing-thread")["id"]}
    freeze_profile(missing)
    assert missing["agent_profile_snapshot"] == {}
    assert "tool_allowlist" not in missing
