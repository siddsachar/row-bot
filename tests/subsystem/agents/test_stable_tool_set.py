"""A conversation binds the same tools on every turn; a turn's scope is enforced when a tool is called.

A media word, an @mention, a /skill mention or a skill_load used to change the bound tool list for one
turn (F38), and with it the start of every provider request, so each such turn re-billed the whole
prompt. Now the graph and its bound tools stay the same, the turn context says what a turn leaves out,
and a call outside the turn is refused before any approval or effect (WP A5). Fakes only.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

from row_bot.skill_discovery import SkillRecord
from tests.subsystem.agents.test_agent_tool_filtering import (  # noqa: F401 -- shared harness
    ASK,
    READER,
    _bound_tools,
    _lc_tool,
    _prepare_graph,
    _restore_agent_context_after_filtering_test,
)

pytestmark = pytest.mark.subsystem

GROUPS = ["filesystem", "image_gen", "video_gen", "designer", "mcp"]
GROUP_TOOLS = {"filesystem": ["workspace_read_file"], "image_gen": ["generate_image"],
               "video_gen": ["generate_video"], "designer": ["designer_update_page"]}
MCP_SERVERS = {"mcp_notion_search": "Notion", "mcp_linear_list_issues": "Linear"}
DESTRUCTIVE = {"generate_image", "generate_video"}


def _skill(skill_id: str) -> SkillRecord:
    return SkillRecord(canonical_id=skill_id, alias=None, display_name=skill_id.title(), icon="*",
                       description=f"The {skill_id} workflow.", tags=(), activation={},
                       instructions=f"{skill_id.upper()} STEPS", source="manual", root=None)


@pytest.fixture
def conversation(monkeypatch):
    """A 200K model, the tool groups above, two MCP apps and two skills, on Row-Bot's real tool assembly."""
    import row_bot.agent as agent_module

    real_snapshot = agent_module._build_runtime_skill_snapshot
    agent = _prepare_graph(monkeypatch)
    monkeypatch.setattr(agent, "_build_runtime_skill_snapshot", real_snapshot)
    monkeypatch.setattr(agent, "get_context_size", lambda model_name=None: 200_000)
    monkeypatch.setattr(agent, "_has_code_folder", lambda: False)
    agent._approval_mode_var.set("approve")
    groups = {name: SimpleNamespace(as_langchain_tools=lambda tools=tools: [_lc_tool(tool) for tool in tools],
                                    destructive_tool_names=set(tools) & DESTRUCTIVE)
              for name, tools in GROUP_TOOLS.items()}
    groups["mcp"] = SimpleNamespace(as_langchain_tools=lambda: [], destructive_tool_names=set())
    monkeypatch.setattr(agent.tool_registry, "get_tool", groups.get)
    state = SimpleNamespace(loading="eager", activation={"disabled": [], "pinned": [], "auto_loaded": []},
                            interrupts=[])
    monkeypatch.setattr(agent.tool_registry, "get_external_tool_loading_mode", lambda: state.loading)
    from row_bot.channels import registry as channel_registry
    from row_bot.mcp_client import runtime as mcp_runtime
    from row_bot.plugins import registry as plugin_registry

    monkeypatch.setattr(mcp_runtime, "get_langchain_tools",
                        lambda allow_names=None, refresh=True: [_lc_tool(name) for name in MCP_SERVERS])
    monkeypatch.setattr(mcp_runtime, "get_destructive_tool_names", lambda allow_names=None: set())
    monkeypatch.setattr(mcp_runtime, "servers_for_tool",
                        lambda name: {MCP_SERVERS[name]} if name in MCP_SERVERS else set())
    monkeypatch.setattr(plugin_registry, "get_langchain_tools", lambda allow_names=None, refresh_mcp=True: [])
    monkeypatch.setattr(plugin_registry, "get_destructive_names", lambda allow_names=None: set())
    monkeypatch.setattr(channel_registry, "running_channels", lambda: [])
    monkeypatch.setattr("row_bot.skill_discovery.collect_enabled_skill_records",
                        lambda **kwargs: [_skill("writer"), _skill("reviewer")])
    monkeypatch.setattr("row_bot.skill_discovery.tool_skill_records", lambda parents, guides=False: [])
    monkeypatch.setattr("row_bot.skills_activation.get_thread_activation_state", lambda thread_id: state.activation)
    monkeypatch.setattr("row_bot.skills_activation.is_smart_off", lambda thread_id: False)
    monkeypatch.setattr("row_bot.threads.get_thread_skills_override", lambda thread_id: None)
    monkeypatch.setattr(agent, "interrupt", lambda payload: state.interrupts.append(payload["tool"]) or True)
    agent._current_thread_id_var.set("stable-tools")
    state.agent = agent
    return state


def _graph(conversation, scope=None):
    conversation.agent._current_app_scope_var.set(scope)
    return conversation.agent.get_agent_graph(list(GROUPS))


def _tools_block(graph) -> str:
    """The bound tools as a provider gets them: every schema, in order."""
    return json.dumps([convert_to_openai_tool(tool) for tool in _bound_tools(graph).values()], sort_keys=True)


def test_the_graph_and_its_tools_stay_byte_stable_across_media_app_and_skill_turns(conversation):
    from row_bot.application.conversation_creation import media_left_out
    from row_bot.integrations.scope import with_tools_left_out

    first = _graph(conversation)
    block = _tools_block(first)
    media = with_tools_left_out(None, media_left_out("Make an image of a lighthouse.", GROUPS, has_design=False))
    assert media["exclude_tools"] == ["designer", "video_gen"]
    turns = {
        "media request": media,
        "@app mention": {"exclude_servers": ["Linear"], "exclude_tools": [], "focus": ["mcp:notion"], "skills": []},
        "/skill mention": {"exclude_servers": [], "exclude_tools": [], "focus": [], "skills": ["writer"]},
    }
    for turn, scope in turns.items():
        graph = _graph(conversation, scope)
        assert graph is first, turn  # the same cache key: no rebuild, no new tool list
        assert _tools_block(graph) == block, turn

    conversation.activation["auto_loaded"] = ["reviewer"]  # skill_load in an earlier turn
    after_load = _graph(conversation)
    assert after_load is first and _tools_block(after_load) == block
    catalog = _bound_tools(first)["skill_search"].description
    assert "- reviewer [manual]" in catalog and "- writer [manual]" in catalog  # active or not, always listed
    assert len(conversation.agent._agent_cache) == 1


def test_only_a_real_configuration_change_rebuilds_the_tool_list(conversation):
    first = _graph(conversation)

    switched_off = _graph(conversation, {"exclude_servers": ["Linear"], "exclude_tools": [], "focus": [],
                                         "skills": [], "off_servers": ["Linear"], "off_tools": []})
    assert switched_off is not first
    assert "mcp_linear_list_issues" not in _bound_tools(switched_off)  # switched off in the chat: not bound

    conversation.agent._approval_mode_var.set("block")
    blocked = _graph(conversation)
    assert blocked is not first and "generate_video" not in _bound_tools(blocked)


def test_a_call_outside_the_turn_is_refused_before_any_approval_and_in_scope_calls_still_ask(conversation):
    """Security: what a turn leaves out stays bound, so the refusal is the only thing between a model
    that calls it anyway and its effect. It comes before the approval card; approvals still apply."""
    scope = {"exclude_servers": ["Linear"], "exclude_tools": ["designer", "video_gen"], "focus": ["mcp:notion"],
             "skills": []}
    bound = _bound_tools(_graph(conversation, scope))

    for name in ("generate_video", "designer_update_page", "mcp_linear_list_issues"):
        refused = bound[name].invoke({"query": f"{name} now"})
        assert refused.startswith(f"BLOCKED: {name} is not part of this turn."), refused
        assert "turn context lists what is left out" in refused  # says why and what to do
    assert conversation.interrupts == []  # no approval card for a call the turn leaves out

    assert "generate_image:a lighthouse" in bound["generate_image"].invoke({"query": "a lighthouse"})
    assert conversation.interrupts == ["generate_image"]  # in scope: its approval still applies
    assert bound["mcp_notion_search"].invoke({"query": "roadmap"}) == "mcp_notion_search:roadmap"

    conversation.agent._current_app_scope_var.set(None)  # the next, unscoped turn
    assert "generate_video:a clip" in bound["generate_video"].invoke({"query": "a clip"})


def test_tool_search_and_tool_invoke_keep_to_the_turn(conversation):
    conversation.loading = "auto"  # app tools reached through the bridge
    scope = {"exclude_servers": ["Linear"], "exclude_tools": [], "focus": ["mcp:notion"], "skills": []}
    bound = _bound_tools(_graph(conversation, scope))
    assert "mcp_linear_list_issues" in bound["tool_search"].description  # the catalog is the whole snapshot

    found = json.loads(bound["tool_search"].invoke({"query": "mcp linear issues list notion search", "limit": 5}))
    assert [result["name"] for result in found["results"]] == ["mcp_notion_search"]
    refused = bound["tool_invoke"].invoke({"name": "mcp_linear_list_issues", "arguments": {"query": "mine"}})
    assert refused.startswith("BLOCKED: mcp_linear_list_issues is not part of this turn.")
    assert bound["tool_invoke"].invoke({"name": "mcp_notion_search", "arguments": {"query": "q3"}}) == (
        "mcp_notion_search:q3")


def test_the_turn_context_says_what_the_turn_leaves_out(conversation):
    agent = conversation.agent
    _graph(conversation)
    agent._current_app_scope_var.set({"exclude_servers": ["Linear"], "exclude_tools": ["video_gen"], "focus": [],
                                      "skills": [], "off_servers": [], "off_tools": []})
    fields = {field.key: field for field in agent._runtime_turn_fields(sorted(GROUP_TOOLS))}

    assert fields["turn_scope"].text.startswith("NOT IN THIS TURN: video_gen, Linear.")
    assert "a call to one is refused" in fields["turn_scope"].text
    assert "video_gen" not in fields["tools"].text and "image_gen" in fields["tools"].text
    assert "NOT IN THIS TURN: nothing" in fields["turn_scope"].cleared  # said once the next turn has no scope


def test_the_mcp_guide_the_index_names_loads_with_skill_load(conversation, monkeypatch):
    """29 Oct: a workflow run read mcp_guide in the tool-guide index and skill_load refused it (unknown_skill).
    MCP tools come as external tools, so their group named the guide in the index but never reached the skills
    skill_load may read."""
    guide = SkillRecord(canonical_id="mcp_guide", alias=None, display_name="MCP Guide", icon="*",
                        description="Guidance for using external MCP tools safely.", tags=(), activation={},
                        instructions="MCP EXTERNAL TOOLS", source="guide", root=None)
    monkeypatch.setattr("row_bot.skill_discovery.tool_skill_records",
                        lambda parents, guides=False: [guide] if guides and "mcp" in parents else [])

    tools = _bound_tools(_graph(conversation))

    assert "mcp" in conversation.agent._current_effective_tool_parent_names_var.get()  # The index names it.
    loaded = json.loads(tools["skill_load"].invoke(
        {"name": "mcp_guide"}, config={"configurable": {"thread_id": "stable-tools"}}))
    assert loaded["ok"] is True and "MCP EXTERNAL TOOLS" in loaded["instructions"]


def test_developer_tools_and_workflows_come_with_a_code_folder(tmp_path, monkeypatch, reload_for_data_dir):
    """F34: without a code folder every Developer tool fails, so none is bound and its guide is not
    sent; the setup tools stay, with one line saying a code folder brings them. With one, the tools
    and guide are bound and the Developer workflows load through skill_load, never injected."""
    _activation, skills = reload_for_data_dir(tmp_path / "data", "row_bot.skills_activation", "row_bot.skills")
    skills.load_skills()
    import row_bot.agent as agent_module

    real_snapshot = agent_module._build_runtime_skill_snapshot
    agent = _prepare_graph(monkeypatch)
    monkeypatch.setattr(agent, "_build_runtime_skill_snapshot", real_snapshot)
    monkeypatch.setattr(agent, "get_context_size", lambda model_name=None: 200_000)
    monkeypatch.setattr("row_bot.skill_discovery.collect_enabled_skill_records", lambda **kwargs: [])
    # Only these built-in groups: no plugin or channel tools another test may have left registered.
    monkeypatch.setattr("row_bot.plugins.registry.get_langchain_tools", lambda allow_names=None, refresh_mcp=True: [])
    monkeypatch.setattr("row_bot.plugins.registry.get_destructive_names", lambda allow_names=None: set())
    monkeypatch.setattr("row_bot.channels.registry.running_channels", lambda: [])
    tools = {"developer": ["developer_read_file"], "conversation_setup": ["create_code_folder", "use_code_folder"]}
    monkeypatch.setattr(agent.tool_registry, "get_tool", lambda name: SimpleNamespace(
        as_langchain_tools=lambda: [_lc_tool(tool) for tool in tools[name]], destructive_tool_names=set())
        if name in tools else None)
    agent._current_thread_id_var.set("developer-folder")

    monkeypatch.setattr(agent, "_has_code_folder", lambda: False)
    without = _bound_tools(agent.get_agent_graph(list(tools)))
    assert set(without) == {"create_code_folder", "use_code_folder", READER, ASK}
    assert "CODE TOOLS: the Developer tools" in agent._agent_runtime_guidance(sorted(tools))
    assert "Developer Tool Guide" not in skills.get_skills_prompt(
        [], active_tool_names=agent._current_effective_tool_parent_names_var.get())

    monkeypatch.setattr(agent, "_has_code_folder", lambda: True)
    with_folder = _bound_tools(agent.get_agent_graph(list(tools)))
    assert "developer_read_file" in with_folder
    guides = skills.get_skills_prompt([], active_tool_names=agent._current_effective_tool_parent_names_var.get())
    assert "Developer Tool Guide" in guides and "# Developer Coding" not in guides
    assert "- developer_coding [manual]" in with_folder["skill_search"].description
    loaded = json.loads(with_folder["skill_load"].invoke(
        {"name": "developer_coding"}, config={"configurable": {"thread_id": "developer-folder"}}))
    assert loaded["ok"] is True and "# Developer Coding" in loaded["instructions"]
