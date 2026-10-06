"""Apps in chat: the agent profile is the ceiling, a chat's switches and a message's mentions only
narrow it, and approvals still apply to every tool that stays. Fakes only; nothing connects."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from row_bot.integrations import scope
from tests.contracts.client_platform.test_headless_lifecycle import platform  # noqa: F401 -- shared harness
from tests.subsystem.agents.test_agent_tool_filtering import (  # noqa: F401 -- shared harness
    _bound_tools,
    _lc_tool,
    _prepare_graph,
    _restore_agent_context_after_filtering_test,
)

pytestmark = pytest.mark.subsystem
_ITEMS, _MCP_ITEMS = scope._items, scope._mcp_items  # The real readers, before any fixture replaces them.


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
    monkeypatch.setattr(scope, "_items", lambda strict=False: items)
    monkeypatch.setattr(scope, "_mcp_items", lambda strict=False: [item for item in items if item["kind"] == "mcp"])
    monkeypatch.setattr(scope, "_skill_names", lambda: {"/writer": ("writer",), "/secret-skill": ("secret-skill",)})
    monkeypatch.setattr("row_bot.threads.get_thread_apps_off", lambda conversation_id: list(off))
    monkeypatch.setattr(scope, "_profile_allow", lambda conversation_id: profile.get("allow"))
    builtins: list[dict] = []
    monkeypatch.setattr("row_bot.integrations.builtin.rows", lambda validate=None, wait=True: builtins)
    return SimpleNamespace(items=items, off=off, profile=profile, builtins=builtins)


def test_nothing_is_narrowed_without_a_switch_or_a_mention(apps):
    assert scope.turn_scope("chat", "Summarise my week", None) is None
    listed = scope.chat_apps("chat")
    assert [(a["name"], a["on"], a["available"]) for a in listed] == [
        ("Figma", True, True), ("Linear", True, True), ("Notion", True, True)]  # Ready apps only: Sentry needs a sign-in.


def test_the_menu_lists_every_ready_built_in_way_and_says_why_some_have_no_switch(apps):
    """The composer matches Your apps: the GitHub account and a channel are ready but bring no chat
    tools, so they are listed without a switch, saying why; mentions still only reach chat tools."""
    def way(ref, name, tools=(), readiness="ready"):
        return {"id": "builtin:" + ref, "kind": "builtin", "owner_ref": ref, "name": name, "tools": list(tools),
                "app": {"id": ref.split(":")[1], "name": name.split()[0]}, "icon": "letter:" + name[0],
                "lifecycle": "installed", "readiness": readiness}
    apps.builtins += [way("account:github", "GitHub account"), way("channel:telegram", "Telegram channel"),
                      way("account:x", "X account", readiness="needs_sign_in")]
    listed = {a["item_id"]: a for a in scope.chat_apps("chat")}
    github, telegram = listed["builtin:account:github"], listed["builtin:channel:telegram"]
    assert (github["switchable"], github["reason"]) == (False, "Used by skills and Developer, not by chat tools.")
    assert telegram["switchable"] is False and "talk to Row-Bot from Telegram" in telegram["reason"]
    assert "builtin:account:x" not in listed  # Not ready: Your apps says what it needs.
    assert all(a["switchable"] for a in listed.values() if a["item_id"].startswith("mcp:"))
    assert scope.turn_scope("chat", "@GitHub what's open?", None) is None  # Nothing to focus on.


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


def test_apps_that_share_a_name_are_mentioned_as_the_composer_names_them(apps):
    hosted = {**_item("mcp:tavily", "tavily", "Tavily"), "name": "Tavily MCP"}
    built_in = {"id": "builtin:tool:web_search", "kind": "builtin", "tools": ["web_search"], "name": "Web search",
                "app": {"id": "tavily", "name": "Tavily"}, "icon": "si:tavily", "lifecycle": "installed", "readiness": "ready",
                "parent_id": None, "children": []}
    apps.items.extend([hosted, built_in])
    shown = {app["item_id"]: app["name"] for app in scope.chat_apps("chat")}
    assert shown["mcp:tavily"] == "Tavily (Tavily MCP)" and shown["builtin:tool:web_search"] == "Tavily (Web search)"
    picked = scope.turn_scope("chat", f"@{shown['mcp:tavily']} find the docs", None)
    assert picked["focus"] == ["mcp:tavily"] and "web_search" in picked["exclude_tools"]
    assert "tavily" not in picked["exclude_servers"]
    either = scope.turn_scope("chat", "@Tavily find the docs", None)  # The shared name means both.
    assert set(either["focus"]) == {"mcp:tavily", "builtin:tool:web_search"}


def test_a_mentioned_skill_loads_for_that_turn_only_and_only_if_the_profile_allows_it(monkeypatch):
    import row_bot.agent as agent
    assert scope._mentioned("/writer tighten this, not /secret-skillful", {"/writer": ("writer",), "/secret-skill": ("s",)},
                            "/") == ["writer"]
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
    apps.items.insert(0, {"id": "builtin:account:google", "kind": "builtin", "tools": ["gmail"], "name": "Google account",
                       "app": {"id": "google", "name": "Google"}, "icon": "si:google", "lifecycle": "installed",
                       "readiness": "ready", "parent_id": None, "children": []})  # Built-in ways have no server.
    servers = {"mcp_notion_delete_page": "Notion", "mcp_notion_search": "Notion"}
    monkeypatch.setattr("row_bot.mcp_client.runtime.server_for_tool", servers.get)
    titles = {"mcp_notion_delete_page": "Delete page", "mcp_notion_search": "Search"}
    monkeypatch.setattr("row_bot.mcp_client.runtime.tool_title", lambda name: titles.get(name, ""))
    return {"item_id": "mcp:notion", "name": "Notion", "icon": "letter:N"}


def test_an_approval_names_the_app_asking_with_its_logo(identities):
    from row_bot.application.approval_projection import project_approval_context
    asked = project_approval_context({"tool": "mcp_notion_delete_page", "args": {"page": "Roadmap"},
                                      "description": "Delete a page"})
    assert asked["app"] == {**identities, "tool": "Delete page", "view": False}  # "Allow Notion to delete page?"
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
    assert [item.get("app") for item in items] == [{**identities, "tool": "Search", "view": False}] * 2 + [None]


def test_a_built_in_app_follows_the_same_rules_through_its_own_tools(apps):
    import row_bot.agent as agent
    apps.items.append({"id": "builtin:account:google", "kind": "builtin", "tools": ["gmail", "calendar"],
                       "name": "Google account", "app": {"id": "google", "name": "Google"}, "icon": "si:google",
                       "lifecycle": "installed", "readiness": "ready", "parent_id": None, "children": []})
    apps.off.append("builtin:account:google")
    found = scope.turn_scope("chat", "What's on today?", None)
    assert found["exclude_tools"] == ["calendar", "gmail"] and found["exclude_servers"] == []
    apps.off.clear()
    focused = scope.turn_scope("chat", "@Google what's on today?", None)
    assert focused["focus"] == ["builtin:account:google"] and focused["exclude_tools"] == []
    assert "Notion" in focused["exclude_servers"]
    assert scope.turn_scope("chat", "@Google what's on today?", ["web_search"]) is None  # Outside the profile: ignored.
    core = [{"tool": SimpleNamespace(name="send_gmail_message"), "source": "core", "parent": "gmail"},
            {"tool": SimpleNamespace(name="calculate"), "source": "core", "parent": "calculator"}]
    kept, _ = agent._apply_app_scope(core, [], found)
    assert [entry["parent"] for entry in kept] == ["calculator"]  # Row-Bot's own tools stay.


def _turn(platform, label, text, kind="conversation.submit"):
    from tests.contracts.client_platform.test_headless_lifecycle import command
    from tests.helpers.client_platform_fakes import fixture_id
    payload = {"submission_id": fixture_id(label), "text": text, "attachment_refs": [],
               "model_selection": {"provider_id": "fixture", "model_ref": "fixture/model"}}
    if kind == "conversation.steer":
        payload = {"steering_id": fixture_id(label), "text": text}
    return platform.execute(owner_id="fixture-owner", idempotency_key=fixture_id(label + ":key"),
                            target="conversation-a", command=command(kind, label, payload))


@pytest.fixture
def platform_apps(platform, monkeypatch):  # noqa: F811 -- the shared platform harness
    items = [_item("mcp:notion", "Notion", "Notion"), _item("mcp:linear", "Linear", "Linear")]
    monkeypatch.setattr(scope, "_items", lambda strict=False: items)
    seen: list = []
    return platform, seen


def _recording(fake, seen):
    def stream(text, enabled, config, **kwargs):
        seen.append((text, config["configurable"].get("app_scope")))
        yield from fake.stream(text, enabled, config, **kwargs)

    def resume(enabled, config, approved, **kwargs):
        seen.append(("resume", config["configurable"].get("app_scope")))
        yield from fake.resume(enabled, config, approved, **kwargs)
    return stream, resume


def test_a_turn_resumed_after_an_approval_keeps_what_its_mention_left_out(platform_apps):
    from langchain_core.messages import AIMessage
    from tests.contracts.client_platform.test_headless_lifecycle import command
    from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream, fixture_id
    platform, seen = platform_apps
    native = fixture_id("resumed-answer")
    fake = ScriptedAgentStream((("interrupt", {"__interrupt_id": "app-interrupt", "tool": "mcp_linear_close_issue",
                                               "description": "Close an issue"}),),
                               (CheckpointCommit((AIMessage(content="Closed", id=native),), native), ("done", "Closed")))
    platform.stream_factory, platform.resume_factory = _recording(fake, seen)
    first = platform.registry.get(_turn(platform, "focused", "@Linear close the stale issue")["execution_id"])
    assert first.producer_done.wait(10) and first.status == "waiting_approval"
    receipt = platform.execute(owner_id="owner", idempotency_key="approve-focused", target=first.approval_id,
                               command=command("approval.resolve", "approve-focused", {"decision": "approve"}))
    assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)
    (_, started), (kind, resumed) = seen
    assert started["focus"] == ["mcp:linear"] and started["exclude_servers"] == ["Notion"]
    assert kind == "resume" and resumed["exclude_servers"] == ["Notion"]  # Approving never brings Notion back.


def test_a_queued_message_narrows_by_its_own_mentions_not_the_turn_it_waited_behind(platform_apps):
    from langchain_core.messages import AIMessage
    from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream, StreamBarrier, fixture_id
    platform, seen = platform_apps
    barrier, later = StreamBarrier(), StreamBarrier()
    done = [fixture_id(f"queued-answer-{n}") for n in range(2)]
    fake = ScriptedAgentStream((barrier, CheckpointCommit((AIMessage(content="One", id=done[0]),), done[0]), ("done", "One")),
                               (later, CheckpointCommit((AIMessage(content="Two", id=done[1]),), done[1]), ("done", "Two")))
    platform.stream_factory, platform.resume_factory = _recording(fake, seen)
    first = _turn(platform, "running", "@Linear what's open?")
    try:
        assert barrier.entered.wait(10)
        _turn(platform, "waiting", "@Notion and the roadmap?", kind="conversation.steer")
        barrier.release.set()
        assert later.entered.wait(10)
        assert seen[0][1]["focus"] == ["mcp:linear"]
        assert seen[1][0] == "@Notion and the roadmap?" and seen[1][1]["focus"] == ["mcp:notion"]
        assert seen[1][1]["exclude_servers"] == ["Linear"]
    finally:
        barrier.release.set()
        later.release.set()
        assert platform.registry.get(first["execution_id"]).producer_done.wait(10)
        platform.registry.stop("conversation-a")


def test_an_agent_a_turn_delegates_to_never_gains_the_apps_the_turn_left_out(platform, monkeypatch):  # noqa: F811
    import row_bot.agent as agent
    from row_bot import agent_runner, threads
    parent = threads.create_thread("Parent")
    seen = []

    def child(prompt, enabled_tool_names, config, *, stop_event):
        seen.append(config["configurable"].get("app_scope"))
        return "Child done"
    monkeypatch.setattr(agent_runner, "_invoke_agent", child)
    token = agent._current_app_scope_var.set({"exclude_servers": ["Notion"], "exclude_tools": ["gmail"],
                                              "focus": ["mcp:linear"], "skills": ["writer"]})
    try:
        agent_runner.spawn_agent_run("Summarise the issues.", parent_thread_id=parent, enabled_tool_names=["mcp"],
                                     wait=True)
    finally:
        agent._current_app_scope_var.reset(token)
    assert seen == [{"exclude_servers": ["Notion"], "exclude_tools": ["gmail"], "focus": [], "skills": []}]


def test_a_retry_or_resume_outside_the_turn_keeps_the_messages_focus(platform, monkeypatch):  # noqa: F811
    """The run remembers what its message left out; a retry or explicit resume started later (outside
    any turn) narrows the same way, plus whatever the chat has switched off since."""
    import row_bot.agent as agent
    from row_bot import agent_orchestrator, agent_runner, threads
    from row_bot.agent_runs import get_agent_run
    parent = threads.create_thread("Parent")
    monkeypatch.setattr(scope, "_items", lambda strict=False: [_item("mcp:notion", "Notion", "Notion"),
                                                               _item("mcp:linear", "Linear", "Linear")])
    seen = []
    monkeypatch.setattr(agent_runner, "_invoke_agent", lambda prompt, tools, config, *, stop_event: (
        seen.append(config["configurable"].get("app_scope")) or "Child done"))
    token = agent._current_app_scope_var.set({"exclude_servers": ["Notion"], "exclude_tools": [],
                                              "focus": ["mcp:linear"], "skills": []})  # "@Linear …"
    try:
        run = agent_runner.spawn_agent_run("Summarise the issues.", parent_thread_id=parent,
                                           enabled_tool_names=["mcp"], wait=True)
    finally:
        agent._current_app_scope_var.reset(token)
    stored = get_agent_run(run["run_id"] if "run_id" in run else run["id"])["app_scope_json"]
    assert stored["exclude_servers"] == ["Notion"]

    asked, spawn = {}, agent_runner.spawn_agent_run
    monkeypatch.setattr(agent_runner, "spawn_agent_run", lambda *args, **kwargs: asked.update(kwargs) or {})
    agent_orchestrator._default_retry_executor({"id": "orchestration-1"}, {"run_id": run.get("run_id", run.get("id")),
                                                                          "required": True, "attempt": 1}, True)
    assert asked["app_scope"]["exclude_servers"] == ["Notion"]  # The retry carries the original focus.
    threads.set_thread_app(parent, "mcp:linear", False)  # Switched off in the chat since.
    spawn("Summarise the issues.", parent_thread_id=parent, enabled_tool_names=["mcp"],
          app_scope=asked["app_scope"], wait=True)  # Outside any turn.
    assert seen[-1]["exclude_servers"] == ["Linear", "Notion"]


def test_a_built_in_tool_is_named_from_what_a_turn_bound_and_a_read_builds_no_tool(monkeypatch):
    from row_bot.integrations import builtin
    from row_bot.tools import registry

    def building(name):
        return SimpleNamespace(as_langchain_tools=lambda: pytest.fail(f"a read built {name}'s tools"))
    monkeypatch.setattr(registry, "get_tool", building)
    monkeypatch.setattr(builtin, "_PARENTS", {})
    monkeypatch.setattr("row_bot.mcp_client.runtime.server_for_tool", lambda name: None)
    assert scope.app_for_tool("send_gmail_message") is None  # Not bound yet: no name, and nothing built.
    builtin.remember_tools({"send_gmail_message": "gmail", "calculate": "calculator"})
    assert scope.app_for_tool("send_gmail_message")["item_id"] == "builtin:account:google"
    assert scope.app_for_tool("calculate") is None  # Row-Bot's own tools belong to no app.


def test_an_app_switched_off_here_never_comes_back_when_apps_cannot_be_read(apps, monkeypatch):
    from row_bot.integrations import builtin, facts
    monkeypatch.setattr(scope, "_items", _ITEMS)
    monkeypatch.setattr(scope, "_mcp_items", _MCP_ITEMS)
    monkeypatch.setattr(builtin, "rows", lambda validate=None, chat_tools=False: [])
    readable = [item for item in apps.items if item["id"] != "mcp:notion"]
    errors: list = []
    monkeypatch.setattr(facts, "inventory", lambda validate=None: (readable, list(errors)))
    apps.off.append("mcp:notion")
    assert scope.turn_scope("chat", "Find the roadmap", None) is None  # Removed since: nothing left to leave out.
    errors.append({"source": "plugins", "code": "source_unavailable"})  # Now its source could not be read.
    with pytest.raises(RuntimeError, match="apps_unreadable"):
        scope.turn_scope("chat", "Find the roadmap", None)  # The turn refuses rather than let Notion back in.


def test_an_agent_started_outside_a_turn_still_leaves_out_what_the_chat_switched_off(platform, monkeypatch):  # noqa: F811
    from row_bot import agent_runner, threads
    parent = threads.create_thread("Parent")
    monkeypatch.setattr(scope, "_items", lambda strict=False: [_item("mcp:notion", "Notion", "Notion"),
                                                               _item("mcp:linear", "Linear", "Linear")])
    threads.set_thread_app(parent, "mcp:notion", False)
    seen = []
    monkeypatch.setattr(agent_runner, "_invoke_agent", lambda prompt, tools, config, *, stop_event: (
        seen.append(config["configurable"].get("app_scope")) or "Child done"))
    agent_runner.spawn_agent_run("Summarise the issues.", parent_thread_id=parent, enabled_tool_names=["mcp"], wait=True)
    assert seen == [{"exclude_servers": ["Notion"], "exclude_tools": [], "focus": [], "skills": []}]  # As /agent starts one.


def test_reading_one_built_in_way_reads_only_its_own_owner(monkeypatch):
    from row_bot import github_account
    from row_bot.integrations import builtin
    monkeypatch.setattr(github_account, "shared_github_status", lambda: pytest.fail("Google's row ran the GitHub CLI"))
    monkeypatch.setattr("row_bot.application.channel_controls.read_channels",
                        lambda **_: pytest.fail("Google's row read the channels"))
    row = builtin.read("builtin:account:google")
    assert row is not None and row["id"] == "builtin:account:google"
    assert builtin.read("builtin:tool:web_search")["id"] == "builtin:tool:web_search"
    assert builtin.read("builtin:account:nobody") is None and builtin.read("mcp:notion") is None


def test_the_skills_catalog_failing_never_refuses_a_turn(apps, monkeypatch):
    from row_bot.integrations import builtin, facts
    monkeypatch.setattr(scope, "_items", _ITEMS)
    monkeypatch.setattr(scope, "_mcp_items", _MCP_ITEMS)
    monkeypatch.setattr(builtin, "rows", lambda validate=None, chat_tools=False: [])
    monkeypatch.setattr(facts, "inventory", lambda validate=None: (
        [item for item in apps.items if item["id"] != "mcp:notion"], [{"source": "skills", "status": "error"}]))
    apps.off.append("mcp:notion")  # Removed since; only the skills catalog failed to read.
    assert scope.turn_scope("chat", "Find the roadmap", None) is None
