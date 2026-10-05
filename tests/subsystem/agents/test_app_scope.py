"""Apps in chat: the agent profile is the ceiling, a chat's switches and a message's mentions only
narrow it, and approvals still apply to every tool that stays. Fakes only; nothing connects."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from row_bot.integrations import scope
from tests.subsystem.agents.test_agent_tool_filtering import (  # noqa: F401 -- shared harness
    _bound_tools,
    _lc_tool,
    _prepare_graph,
    _restore_agent_context_after_filtering_test,
)

pytestmark = pytest.mark.subsystem


def _item(item_id, server, name, *, readiness="ready", lifecycle="installed", parent=None):
    return {"id": item_id, "kind": "mcp", "server": server, "name": name, "app": {"id": name.lower(), "name": name},
            "icon": "letter:" + name[0], "lifecycle": lifecycle, "readiness": readiness, "parent_id": parent,
            "children": []}


@pytest.fixture
def apps(monkeypatch):
    items = [_item("mcp:notion", "Notion", "Notion"), _item("mcp:linear", "Linear", "Linear"),
             _item("mcp:sentry", "Sentry", "Sentry", readiness="needs_sign_in"),
             _item("plugin:kit:mcp:figma", "plugin_kit_figma", "Figma", parent="plugin:kit")]
    off: list[str] = []
    profile: dict = {}
    monkeypatch.setattr(scope, "_items", lambda: items)
    monkeypatch.setattr(scope, "_skill_names", lambda: {"/writer": "writer", "/secret-skill": "secret-skill"})
    monkeypatch.setattr("row_bot.threads.get_thread_apps_off", lambda conversation_id: list(off))
    monkeypatch.setattr(scope, "_profile_allow", lambda conversation_id: profile.get("allow"))
    return SimpleNamespace(items=items, off=off, profile=profile)


def test_nothing_is_narrowed_without_a_switch_or_a_mention(apps):
    assert scope.turn_scope("chat", "Summarise my week", None) is None
    listed = scope.chat_apps("chat")
    assert [(a["name"], a["on"], a["available"]) for a in listed] == [
        ("Figma", True, True), ("Linear", True, True), ("Notion", True, True)]  # Ready apps only: Sentry needs a sign-in.


def test_a_switch_leaves_an_app_out_of_this_chat_only(apps):
    apps.off.append("mcp:notion")
    assert scope.turn_scope("chat", "Find the roadmap", None)["exclude_servers"] == ["Notion"]
    assert next(a for a in scope.chat_apps("chat") if a["name"] == "Notion")["on"] is False


def test_a_mention_focuses_the_turn_on_that_app(apps):
    found = scope.turn_scope("chat", "@linear what's open for me?", None)
    assert found["focus"] == ["mcp:linear"]
    assert found["exclude_servers"] == ["Notion", "Sentry", "plugin_kit_figma"]
    assert scope.turn_scope("chat", "email me@linear.app", None) is None  # An address is not a mention.


@pytest.mark.parametrize("why", ["off", "not_ready", "outside_profile"])
def test_a_mention_never_turns_on_an_app_the_chat_or_profile_left_out(apps, why):
    if why == "off":
        apps.off.append("mcp:notion")
        text = "@Notion find the roadmap"
    elif why == "not_ready":
        text = "@Sentry what broke?"
    else:
        text = "@Notion find the roadmap"
    allow = ["web_search"] if why == "outside_profile" else None
    found = scope.turn_scope("chat", text, allow)
    assert not (found or {}).get("focus")
    if why == "off":
        assert found["exclude_servers"] == ["Notion"]  # Still left out; nothing else changes.
    if why == "outside_profile":
        apps.profile["allow"] = allow
        assert all(not a["available"] for a in scope.chat_apps("chat"))


def test_a_continued_turn_keeps_what_it_left_out_and_todays_switches_still_apply(apps):
    previous = scope.turn_scope("chat", "@Linear triage", None)
    apps.off.append("mcp:linear")
    resumed = scope.turn_scope("chat", "", None, previous)
    assert set(resumed["exclude_servers"]) == {"Notion", "Sentry", "plugin_kit_figma", "Linear"}


def test_a_mentioned_skill_loads_for_that_turn_only_and_only_if_the_profile_allows_it(monkeypatch):
    import row_bot.agent as agent
    assert scope._mentioned("/writer tighten this, not /secret-skillful", {"/writer": "writer", "/secret-skill": "s"}, "/") \
        == ["writer"]
    monkeypatch.setattr("row_bot.threads.get_thread_skills_override", lambda thread_id: None)
    monkeypatch.setattr("row_bot.skills_activation.get_thread_activation_state",
                        lambda thread_id: {"disabled": [], "pinned": [], "auto_loaded": []})
    monkeypatch.setattr(agent, "_active_profile_snapshot", lambda: {})
    authorized = (SimpleNamespace(canonical_id="writer"),)  # A profile that leaves "secret-skill" out.
    agent._current_app_scope_var.set({"skills": ["writer", "secret-skill"]})
    assert [r.canonical_id for r in agent._resolve_active_skill_records(authorized)] == ["writer"]
    agent._current_app_scope_var.set(None)
    assert agent._resolve_active_skill_records(authorized) == []


def test_tools_of_apps_left_out_are_never_bound_and_kept_ones_still_ask_first(monkeypatch):
    agent = _prepare_graph(monkeypatch)
    agent._approval_mode_var.set("approve")
    tools = {name: _lc_tool(name) for name in ("mcp_notion_search", "mcp_notion_delete_page", "mcp_linear_list_issues")}
    servers = {"mcp_notion_search": "Notion", "mcp_notion_delete_page": "Notion", "mcp_linear_list_issues": "Linear"}
    monkeypatch.setattr(agent.tool_registry, "get_tool", lambda name: SimpleNamespace(
        as_langchain_tools=lambda: [], destructive_tool_names=set()) if name == "mcp" else None)
    monkeypatch.setattr(agent.tool_registry, "get_external_tool_loading_mode", lambda: "eager")
    from row_bot.mcp_client import runtime as mcp_runtime
    from row_bot.plugins import registry as plugin_registry
    monkeypatch.setattr(mcp_runtime, "get_langchain_tools", lambda allow_names=None: list(tools.values()))
    monkeypatch.setattr(mcp_runtime, "get_destructive_tool_names", lambda allow_names=None: {"mcp_notion_delete_page"})
    monkeypatch.setattr(mcp_runtime, "server_for_tool", servers.get)
    monkeypatch.setattr(plugin_registry, "get_langchain_tools", lambda allow_names=None: [])
    monkeypatch.setattr(plugin_registry, "get_destructive_names", lambda allow_names=None: set())

    agent._current_app_scope_var.set({"exclude_servers": ["Linear"], "exclude_tools": [], "focus": [], "skills": []})
    bound = _bound_tools(agent.get_agent_graph(["mcp"]))
    assert "mcp_linear_list_issues" not in bound
    assert {"mcp_notion_search", "mcp_notion_delete_page"} <= set(bound)
    assert hasattr(bound["mcp_notion_delete_page"].func, "__wrapped__")  # Still behind the approval gate.

    agent._current_app_scope_var.set({"exclude_servers": ["Notion", "Linear"], "exclude_tools": [], "focus": [], "skills": []})
    assert agent.get_agent_graph(["mcp"]).tools == []  # Every app left out: none of their tools is bound.
    agent._current_app_scope_var.set(None)
    assert set(tools) <= set(_bound_tools(agent.get_agent_graph(["mcp"])))  # Unscoped turns are unchanged.


@pytest.fixture
def identities(apps, monkeypatch):
    servers = {"mcp_notion_delete_page": "Notion", "mcp_notion_search": "Notion"}
    monkeypatch.setattr("row_bot.mcp_client.runtime.server_for_tool", servers.get)
    return {"item_id": "mcp:notion", "name": "Notion", "icon": "letter:N"}


def test_an_approval_names_the_app_asking_with_its_logo(identities):
    from row_bot.application.approval_projection import project_approval_context
    asked = project_approval_context({"tool": "mcp_notion_delete_page", "args": {"page": "Roadmap"},
                                      "description": "Delete a page"})
    assert asked["app"] == identities
    assert "app" not in project_approval_context({"tool": "workspace_file_delete", "args": {}})  # Row-Bot's own.


def test_a_settled_tool_step_names_its_app_even_when_found_through_discovery(identities):
    from row_bot.application.conversation_traces import project_assistant_row_traces
    records = [
        {"row": {"id": "assistant:checkpoint:parent", "message_id": "parent", "role": "assistant", "blocks": []},
         "tool_calls": [{"id": "call-1", "name": "mcp_notion_search", "args": {"query": "roadmap"}},
                        {"id": "call-2", "name": "tool_invoke", "args": {"name": "mcp_notion_search", "arguments": {}}},
                        {"id": "call-3", "name": "calculate", "args": {}}]},
    ]
    items = [item for group in project_assistant_row_traces(records)[0]["traces"] for item in group["items"]]
    assert [item.get("app") for item in items] == [identities, identities, None]
