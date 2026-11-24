"""The rest of the company's policy in the app (C3b): tools switched off are neither offered nor callable; the
approval ceiling caps every level where it takes effect (a run's level, the gate, the person-started decisions, a
chat's stored level, the standing allowances) and keeps what the person stored; Block refuses every gated action;
apps the company doesn't allow can't be added and don't run, and are kept; chat export and the data backup are
refused; lockdown applies ``lockdown_policy()``; and with no company joined nothing changes.

The policy in force comes from C3a's API (``policy.state()``), set here by the test; no server is involved."""

from __future__ import annotations

import asyncio
import json
from contextvars import ContextVar
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.tools import StructuredTool

from row_bot.approval_policy import decision_for_action
from row_bot.company import enforce, policy
from row_bot.policy import lockdown_policy, parse_policy, resolve
from row_bot.tools import approval_gate
from row_bot.tools import registry as tool_registry

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

DELETE = {"tool": "workspace_file_delete", "label": "Delete file", "description": "Delete file: old.txt"}
RUN = {"tool": "run_command", "label": "Run shell command", "description": "Run shell command: npm test"}


@pytest.fixture
def company(monkeypatch, tmp_path):
    """``company(document)`` puts a company policy in force (as C3a's ``state()`` reports it), ``company(None)``
    leaves no company joined and ``company(lockdown=True)`` locks down."""
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    held: dict = {"status": policy.Status("not_joined")}
    monkeypatch.setattr(policy, "state", lambda: held["status"])

    def use(document: dict | None = None, *, lockdown: bool = False) -> None:
        if lockdown:
            held["status"] = policy.Status("locked_down", "lease_expired", 0.0, lockdown_policy())
        elif document is None:
            held["status"] = policy.Status("not_joined")
        else:
            held["status"] = policy.Status("current", None, 0.0, resolve(parse_policy(document)))

    return use


@pytest.fixture
def agent_context():
    """Every agent context variable put back as it was (graphs and levels set them)."""
    import row_bot.agent as agent

    variables = {value for value in vars(agent).values() if isinstance(value, ContextVar)}
    tokens = [(variable, variable.set(variable.get(None))) for variable in variables]
    try:
        yield agent
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def _lc_tool(name: str) -> StructuredTool:
    def _run(query: str = "") -> str:
        return f"{name} ran {query}"

    return StructuredTool.from_function(func=_run, name=name, description=f"{name} test tool")


def _registered(monkeypatch, names: tuple[str, ...]) -> None:
    tools = [SimpleNamespace(name=name) for name in names]
    monkeypatch.setattr(tool_registry, "get_all_tools", lambda: tools)
    monkeypatch.setattr(tool_registry, "is_switched_on", lambda name: True)


def _asker(answer):
    asked: list[dict] = []

    def ask(payload):
        asked.append(payload)
        return answer

    return ask, asked


# -- tools --------------------------------------------------------------------------------------------

def test_a_tool_group_the_company_switched_off_is_not_among_the_enabled_tools(company, monkeypatch):
    _registered(monkeypatch, ("browser", "shell", "weather"))

    company({"tools": {"disabled_groups": ["shell"]}})
    assert [tool.name for tool in tool_registry.get_enabled_tools()] == ["browser", "weather"]

    company(None)
    assert [tool.name for tool in tool_registry.get_enabled_tools()] == ["browser", "shell", "weather"]


def test_a_tool_the_company_switched_off_isnt_enabled_and_its_switch_reads_as_the_person_set_it(
        company, monkeypatch):
    monkeypatch.setattr(tool_registry, "_ensure_config_scope", lambda: None)
    monkeypatch.setattr(tool_registry, "_enabled", {"browser": True, "computer_use": True})

    company({"tools": {"disabled_groups": ["browser"]}})
    assert tool_registry.is_enabled("browser") is False  # The browser pane, readiness, Buddy's checks.
    assert tool_registry.is_switched_on("browser") is True  # Settings' switch.
    assert tool_registry.is_enabled("computer_use") is True


def _prepare_graph(agent, monkeypatch, groups: dict[str, list[StructuredTool]]):
    agent.clear_agent_cache()
    agent._approval_mode_var.set("approve")
    monkeypatch.setattr(agent, "_build_runtime_skill_snapshot", lambda: ((), (), False, "test-skills"))
    monkeypatch.setattr(agent, "get_current_model", lambda: "model:test")
    monkeypatch.setattr(agent, "get_llm", lambda: object())
    monkeypatch.setattr(agent, "get_context_size", lambda model_name=None: 65_536)
    monkeypatch.setattr(agent, "get_agent_system_prompt", lambda: "system")
    monkeypatch.setattr(agent, "_ensure_agent_mode_ready", lambda model_name: SimpleNamespace(
        provider_id="test", runtime_model="test", capability_source="test", confidence="high"))
    monkeypatch.setattr(agent, "create_react_agent", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(agent.tool_registry, "get_tool", lambda name: SimpleNamespace(
        as_langchain_tools=lambda: groups[name], destructive_tool_names=set()) if name in groups else None)
    from row_bot.channels import registry as channel_registry
    from row_bot.plugins import registry as plugin_registry

    monkeypatch.setattr(plugin_registry, "get_langchain_tools", lambda allow_names=None: [])
    monkeypatch.setattr(plugin_registry, "get_destructive_names", lambda allow_names=None: set())
    monkeypatch.setattr(channel_registry, "running_channels", lambda: [])


def _bound(graph) -> dict:
    from langgraph.prebuilt import ToolNode

    assert isinstance(graph.tools, ToolNode)
    return graph.tools.tools_by_name


def test_tools_the_company_switched_off_are_never_offered_and_a_call_that_slips_through_is_refused(
        company, agent_context, monkeypatch):
    agent = agent_context
    _prepare_graph(agent, monkeypatch, {"filesystem": [_lc_tool("read_file"), _lc_tool("write_file")],
                                        "shell": [_lc_tool("run_command")]})

    before = _bound(agent.get_agent_graph(["filesystem", "shell"]))
    assert {"read_file", "write_file", "run_command"} <= set(before)

    # A registry tool with everything it brings, and one model-facing tool, are left out of the tool list.
    company({"tools": {"disabled_groups": ["shell"], "disabled_tools": ["write_file"]}})
    agent.clear_agent_cache()  # As a change of policy does (C3a's refresh).
    after = _bound(agent.get_agent_graph(["filesystem", "shell"]))
    assert "read_file" in after
    assert not {"write_file", "run_command"} & set(after)

    # A tool list made before the change still refuses the call, in plain words, and nothing runs.
    for name in ("run_command", "write_file"):
        result = before[name].invoke({"query": "ls"})
        assert "switched off by your company" in str(result)
        assert "ran ls" not in str(result)
    assert "read_file ran ls" in str(before["read_file"].invoke({"query": "ls"}))


# -- the approval ceiling -----------------------------------------------------------------------------

def test_the_level_a_chat_workflow_or_agent_run_was_given_runs_at_most_at_the_ceiling(company, agent_context):
    agent = agent_context
    agent._approval_mode_var.set("full")  # What the run was given: the person's own level.

    company({"approvals": {"max_level": "auto"}})
    assert agent.get_approval_mode() == "auto"
    company({"approvals": {"max_level": "block"}})
    assert agent.get_approval_mode() == "block"
    # Raised, or left: the person's level is back.
    company({"approvals": {"max_level": "full"}})
    assert agent.get_approval_mode() == "full"
    company(None)
    assert agent.get_approval_mode() == "full"


def test_hands_off_asks_the_person_below_a_ceiling_of_ask_at_the_gate(company):
    ask, asked = _asker(True)

    company({"approvals": {"max_level": "approve"}})
    outcome, line = approval_gate.resolve_approval_line(DELETE, approval_mode="full", approval_callback=ask)
    assert (outcome, line) == ("allow", approval_gate.APPROVAL_GIVEN)
    assert len(asked) == 1
    assert approval_gate.request_approval(DELETE, approval_mode="full", approval_callback=ask) is True
    assert len(asked) == 2

    company(None)
    outcome, line = approval_gate.resolve_approval_line(DELETE, approval_mode="full", approval_callback=ask)
    assert (outcome, line) == ("allow", approval_gate.APPROVAL_FULL_AUTONOMY)
    assert len(asked) == 2


def test_a_ceiling_of_block_refuses_every_gated_action_with_plain_words(company, agent_context):
    ask, asked = _asker(True)
    company({"approvals": {"max_level": "block"}})

    assert approval_gate.resolve_approval_line(DELETE, approval_mode="full", approval_callback=ask) == ("block", "")
    # Even an action that already knows it needs an approval: nobody approves it, not even the person.
    assert approval_gate.request_approval(RUN, approval_mode="auto", approval_callback=ask) is False
    assert asked == []
    refusal, line = approval_gate.approval_check(DELETE)
    assert refusal == enforce.BLOCKED.format(label="Delete file") and line == ""
    assert "your company's rules" in refusal


def test_what_the_person_starts_in_the_app_is_decided_at_most_at_the_ceiling(company):
    company({"approvals": {"max_level": "approve"}})
    assert decision_for_action("full", by_person=True) == "ask"
    company({"approvals": {"max_level": "block"}})
    assert decision_for_action("auto", by_person=True) == "block"
    assert decision_for_action("auto", read_only=True) == "allow"  # Reading never needed an approval.
    company(None)
    assert decision_for_action("full", by_person=True) == "allow"


def test_a_standing_rule_or_the_shells_always_allow_approves_only_while_the_company_allows_auto(
        company, monkeypatch):
    from row_bot import approval_rules

    scope = SimpleNamespace(note=lambda: {}, card=lambda: {"kind": "command"})
    rule = SimpleNamespace(rule_id="rule-1", words="`npm test` in row-bot")
    monkeypatch.setattr(approval_rules, "candidate", lambda payload, risks=(), *, person_only=False: scope)
    monkeypatch.setattr(approval_rules, "match", lambda found: rule if found is scope else None)
    monkeypatch.setattr(approval_rules, "note_used", lambda rule_id: None)
    ask, asked = _asker(False)

    company({"approvals": {"max_level": "auto"}})
    assert approval_gate.request_approval(RUN, approval_mode="approve", approval_callback=ask) is True
    assert asked == []  # The person's rule allowed it.

    company({"approvals": {"max_level": "approve"}})
    assert approval_gate.request_approval(RUN, approval_mode="approve", approval_callback=ask) is False
    assert len(asked) == 1  # Asked each time; no "Always allow…" offered on the card either.
    assert "always_allow" not in asked[0]


def test_a_chats_stored_level_above_the_ceiling_is_kept_and_comes_back_when_the_ceiling_rises(
        company, tmp_path, reload_for_data_dir):
    data_dir = tmp_path / "threads"
    data_dir.mkdir()
    _tasks, threads = reload_for_data_dir(data_dir, "row_bot.tasks", "row_bot.threads")
    thread_id = threads.create_thread("Mine", approval_mode="full")

    company({"approvals": {"max_level": "auto"}})
    assert threads._get_thread_approval_mode(thread_id) == "auto"
    assert threads._get_thread_approval_mode_raw(thread_id) == "full"  # Never rewritten.

    company({"approvals": {"max_level": "full"}})
    assert threads._get_thread_approval_mode(thread_id) == "full"
    company(None)
    assert threads._get_thread_approval_mode(thread_id) == "full"


# -- apps ---------------------------------------------------------------------------------------------

def test_a_plugin_the_company_doesnt_allow_cant_be_added(company, tmp_path):
    from row_bot.plugins import installer

    company({"apps": {"plugins": "allow-list", "allowed_plugins": ["acme-ok"]}})
    result = installer.install_plugin("acme-tools", source_dir=tmp_path / "acme-tools")

    assert (result.success, result.code, result.message) == (False, "company_policy", enforce.APP_HELD["plugins"])
    # The Custom Tools prefix isn't a package name while a company's rules apply.
    company({"apps": {"plugins": "any"}})
    reserved = installer.install_plugin("custom-tool-notes", source_dir=tmp_path / "custom-tool-notes")
    assert (reserved.success, reserved.code) == (False, "plugin_id_reserved")


def _custom_tools(monkeypatch, promoted: set[str], installed: set[str] = frozenset()) -> None:
    from row_bot.developer import tool_capsules
    from row_bot.plugins import devtools, installer

    monkeypatch.setattr(tool_capsules, "list_promoted_capsules",
                        lambda: [SimpleNamespace(promoted_plugin_id=plugin_id) for plugin_id in sorted(promoted)])
    monkeypatch.setattr(installer, "is_installed", lambda plugin_id: plugin_id in installed)
    monkeypatch.setattr(devtools, "iter_linked_plugin_dirs", lambda: {})


def test_an_installed_plugin_the_company_doesnt_allow_doesnt_run_and_keeps_its_switch(company, monkeypatch):
    from row_bot.plugins import registry as plugin_registry
    from row_bot.plugins import state as plugin_state

    _custom_tools(monkeypatch, {"custom-tool-notes"})
    saved = {"acme-tools": {"enabled": True}, "custom-tool-notes": {"enabled": True}}
    monkeypatch.setattr(plugin_state, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(plugin_state, "_state", saved)
    lookup = SimpleNamespace(as_langchain_tools=lambda: [_lc_tool("acme_lookup")], plugin_api=None)
    monkeypatch.setattr(plugin_registry, "_plugin_tools", {"acme_lookup": lookup})
    monkeypatch.setattr(plugin_registry, "_tool_to_plugin", {"acme_lookup": "acme-tools"})
    monkeypatch.setattr(plugin_registry, "_loaded_manifests", {})

    company({"apps": {"plugins": "none"}})
    assert plugin_state.is_plugin_enabled("acme-tools") is False
    assert plugin_registry.get_langchain_tools(refresh_mcp=False) == []
    # A Custom Tool is the person's own: it follows the Custom Tool Builder, not the plugin rule.
    assert plugin_state.is_plugin_enabled("custom-tool-notes") is True

    company({"apps": {"plugins": "allow-list", "allowed_plugins": ["acme-tools"]}})
    assert [tool.name for tool in plugin_registry.get_langchain_tools(refresh_mcp=False)] == ["acme_lookup"]

    company({"tools": {"disabled_groups": ["custom_tool_builder"]}})
    assert plugin_state.is_plugin_enabled("custom-tool-notes") is False
    assert saved == {"acme-tools": {"enabled": True}, "custom-tool-notes": {"enabled": True}}  # Kept as set.


def test_only_a_custom_tool_made_in_row_bot_counts_as_one(company, monkeypatch):
    # A package that took the prefix, or an id no Custom Tool was promoted under, follows the plugin rule.
    _custom_tools(monkeypatch, {"custom-tool-notes", "custom-tool-shadow"}, installed={"custom-tool-shadow"})
    company({"apps": {"plugins": "none"}})

    assert enforce.is_custom_tool("custom-tool-notes") is True
    assert enforce.allows_plugin("custom-tool-notes") is True
    assert enforce.is_custom_tool("custom-tool-shadow") is False
    assert enforce.allows_plugin("custom-tool-shadow") is False
    assert enforce.allows_plugin("custom-tool-made-up") is False


@pytest.fixture
def mcp_config(tmp_path, monkeypatch):
    from row_bot import tasks
    from row_bot.mcp_client import config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "mcp_servers.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "_config_cache", None)
    monkeypatch.setattr(tasks, "_DB_PATH", str(tmp_path / "tasks.db"))
    return config


def _server(name: str) -> dict:
    return {"operation": "add", "fields": {"name": name, "transport": "stdio", "command": "synthetic-command"}}


def _paste(name: str, server: dict) -> dict:
    return {"operation": "import", "import_json": json.dumps({"mcpServers": {name: server}})}


def _curated():
    from row_bot.mcp_client.marketplace import CURATED_STARTER_CATALOG, entry_to_server_config

    entry = CURATED_STARTER_CATALOG[0]
    return entry, entry_to_server_config(entry)


def test_an_mcp_server_counts_as_allowed_only_by_the_catalog_listing_row_bot_recorded(company, mcp_config):
    from row_bot.application import capability_configuration_controls as controls

    entry, listed = _curated()
    company({"apps": {"mcp_servers": "allow-list", "allowed_mcp_servers": [f"curated:{entry.id}", "github"]}})
    revision = controls.read_mcp_configuration().revision

    def review(intent):
        return controls.review_mcp_configuration_command(revision, intent, validate=lambda: None)

    # The listing itself, added from the catalog: allowed.
    assert review(_paste("Anything", listed))["operation"] == "import"
    # A name the person chose is never a match, typed or pasted.
    for intent in (_server("github"), _paste("github", {"command": "synthetic-command"})):
        with pytest.raises(controls.CapabilityConfigurationError, match="company_policy"):
            review(intent)
    # Pasted claims of where it came from count for nothing: another launch under the listing's name and id…
    forged = {**listed, "transport": "stdio", "command": "synthetic-command", "url": ""}
    with pytest.raises(controls.CapabilityConfigurationError, match="company_policy"):
        review(_paste("Forged", forged))
    # …the catalog names and launch Row-Bot records…
    claimed = {"command": "synthetic-command", "source": {
        "marketplace": "manual", enforce.MCP_CATALOG_NAMES: ["github"],
        enforce.MCP_LAUNCH: enforce.mcp_launch({"command": "synthetic-command"})}}
    with pytest.raises(controls.CapabilityConfigurationError, match="company_policy"):
        review(_paste("Claimed", claimed))
    # …and a plugin's own marks, even where the company allows a plugin's servers.
    company({"apps": {"mcp_servers": "none", "plugins": "any"}})
    as_plugin = {"command": "synthetic-command", "source": {"kind": "plugin", "plugin_id": "custom-tool-x"},
                 "plugin_prepared": {"python": "x"}}
    with pytest.raises(controls.CapabilityConfigurationError, match="company_policy"):
        review(_paste("Plugin", as_plugin))
    # Saved as pasted, nothing of that is kept.
    saved = enforce.mcp_provenance({**as_plugin, "name": "Plugin"})
    assert "plugin_prepared" not in saved and "kind" not in saved["source"] and "plugin_id" not in saved["source"]


def test_an_mcp_server_stops_matching_once_its_launch_is_edited(company):
    from row_bot.integrations import facts

    entry, listed = _curated()
    company({"apps": {"mcp_servers": "allow-list", "allowed_mcp_servers": [f"curated:{entry.id}"]}})
    saved = enforce.mcp_provenance({**listed, "name": "Renamed by the person"})

    assert enforce.mcp_refusal("Renamed by the person", saved) is None
    row = facts.entry("mcp", "x", "x", _mcp={"name": "Renamed by the person", "cfg": saved})
    assert facts.company_held(row) == ""
    edited = {**saved, "transport": "stdio", "command": "synthetic-command", "url": ""}
    assert enforce.mcp_refusal("Renamed by the person", edited) == enforce.APP_HELD["mcp_servers"]
    # An official Registry listing is known by its record's canonical name, without a version.
    assert enforce.mcp_catalog_names("official", "abc", "io.example/server@1.2.0") == (
        "official:abc", "io.example/server")


def test_a_plugins_mcp_server_is_known_only_from_the_plugins_overlay(company, monkeypatch):
    from row_bot.plugins import mcp as plugin_mcp

    cfg = {"transport": "stdio", "command": "plugin-command", "source": {"kind": "plugin", "plugin_id": "acme"}}
    monkeypatch.setattr(plugin_mcp, "plugin_mcp_servers", lambda: {"plugin_acme_notes": dict(cfg)})

    company({"apps": {"mcp_servers": "none", "plugins": "allow-list", "allowed_plugins": ["acme"]}})
    assert enforce.mcp_refusal("plugin_acme_notes", cfg) is None
    # The same marks on a server of another name, or another launch, are the MCP rule's.
    assert enforce.mcp_refusal("pasted", cfg) == enforce.APP_HELD["mcp_servers"]
    other = {**cfg, "command": "something-else"}
    assert enforce.mcp_refusal("plugin_acme_notes", other) == enforce.APP_HELD["mcp_servers"]
    company({"apps": {"mcp_servers": "any", "plugins": "none"}})
    assert enforce.mcp_refusal("plugin_acme_notes", cfg) == enforce.APP_HELD["plugins"]


def test_an_installed_mcp_server_the_company_doesnt_allow_isnt_started_or_checked(company, mcp_config, monkeypatch):
    from row_bot.mcp_client import runtime

    monkeypatch.setattr(runtime, "_statuses", {})
    monkeypatch.setattr(runtime, "_servers", {})
    connected: list[str] = []

    async def connect(self):
        connected.append(self.name)

    monkeypatch.setattr(runtime.McpServerRuntime, "_connect", connect)
    cfg = {"enabled": True, "transport": "stdio", "command": "synthetic-command", "source": {}}
    company({"apps": {"mcp_servers": "none"}})

    asyncio.run(runtime.McpServerRuntime("held", dict(cfg)).start())
    assert runtime._statuses["held"].last_error == enforce.APP_HELD["mcp_servers"]
    checked = asyncio.run(runtime.probe_server_async("held", dict(cfg)))
    assert checked["ok"] is False and "your company" in checked["error"]
    assert connected == []
    assert runtime._servers == {}


def test_an_mcp_server_held_while_it_runs_is_stopped_and_refuses_every_use(company, mcp_config, monkeypatch):
    from row_bot.mcp_client import runtime

    held = runtime.McpServerRuntime("held", {"transport": "stdio", "command": "synthetic-command", "source": {}})
    held.session = SimpleNamespace()
    monkeypatch.setattr(runtime, "_servers", {"held": held})
    monkeypatch.setattr(runtime, "_statuses", {})
    stopped: list[str] = []
    monkeypatch.setattr(runtime, "stop_server", lambda name: stopped.append(name))

    company(None)
    assert runtime.held_servers() == []
    company({"apps": {"mcp_servers": "none"}})
    assert runtime.stop_held_servers() == ["held"] and stopped == ["held"]
    for use in (held.list_resources(), held.read_resource("file:///notes"), held.list_prompts(),
                held.get_prompt("summary"), held.read_view("ui://view", deadline=1e12),
                held.call_tool_for_view("search", {}, deadline=1e12), held.call_tool("search", {})):
        with pytest.raises(PermissionError, match="your company"):
            asyncio.run(use)


def test_a_skills_hub_skill_the_company_doesnt_allow_cant_be_added(company, monkeypatch):
    import row_bot.skills as skills
    from row_bot.skills_hub import installer
    from row_bot.skills_hub.models import SkillBundle

    monkeypatch.setattr(skills, "load_skills", lambda: (_ for _ in ()).throw(AssertionError("nothing is read")))
    bundle = SkillBundle(source="skills_sh", install_ref="skills_sh:acme/skills/review", root_name="review",
                         primary_skill_path="SKILL.md", files=[], frontmatter={}, instructions="",
                         content_hash="0" * 64)

    company({"apps": {"skills_hub": "allow-list", "allowed_skills": ["skills_sh:acme/skills/other"]}})
    result = installer.install_bundle(bundle)

    assert (result.success, result.message) == (False, enforce.APP_HELD["skills_hub"])


def test_an_installed_skills_hub_skill_the_company_doesnt_allow_isnt_used_and_the_persons_own_are(
        company, monkeypatch):
    import row_bot.skills as skills
    from row_bot.plugins import registry as plugin_registry
    from row_bot.skill_discovery import collect_enabled_skill_records
    from row_bot.skills_hub import provenance

    def skill(name: str, tools: tuple[str, ...] = (), on_demand: bool = False) -> SimpleNamespace:
        return SimpleNamespace(name=name, display_name=name.title(), icon="✨", description="", tags=[],
                               activation={}, instructions="Do it.", path="", tools=list(tools),
                               on_demand=on_demand, inline=False)

    found = [skill("review"), skill("mine"), skill("hub-guide", ("shell",)), skill("hub-flow", ("shell",), True)]
    monkeypatch.setattr(skills, "get_all_skills", lambda: found)
    monkeypatch.setattr(skills, "_enabled", {"review": True, "mine": True})
    monkeypatch.setattr(plugin_registry, "get_enabled_plugin_skill_records", lambda: [])
    records = {name: SimpleNamespace(install_ref=f"skills_sh:acme/skills/{name}", metadata={})
               for name in ("review", "hub-guide", "hub-flow")}
    monkeypatch.setattr(provenance, "get_record", lambda name: records.get(name))

    def used() -> tuple[list[str], list[str], list[str]]:
        return ([record.canonical_id for record in collect_enabled_skill_records(load_manual=False)],
                [guide.name for guide in skills.active_tool_guides(["shell"])],
                [flow.name for flow in skills.get_on_demand_tool_skills(["shell"])])

    company({"apps": {"skills_hub": "none"}})
    assert used() == (["mine"], [], [])  # Tool guides and on-demand skills from the hub included.

    company({"apps": {"skills_hub": "allow-list", "allowed_skills": ["skills_sh:acme/skills/review"]}})
    assert used() == (["review", "mine"], [], [])
    company(None)
    assert used() == (["review", "mine"], ["hub-guide"], ["hub-flow"])


class _ChannelRegistry:
    def __init__(self, channel) -> None:
        self.channel = channel

    def get(self, name: str):
        return self.channel if name == self.channel.name else None

    def all_channels(self):
        return [self.channel]

    def get_source(self, _name: str):
        return SimpleNamespace(kind="core", plugin_id="", label="")

    def clear_agent_cache_if_loaded(self) -> None:
        pass


class _ChannelConfig:
    def get(self, _channel: str, _key: str, default=None):
        return default

    def set(self, *_args) -> None:
        raise AssertionError("nothing is saved")


class _ChannelAccounts:
    """The channel's sign-ins and secrets: none."""

    def get_approved_users(self, _channel: str) -> list[str]:
        return []

    def get_user_names(self, _channel: str) -> dict[str, str]:
        return {}

    def channel_secret_status(self, _channel: str, _env_key: str) -> dict:
        return {"configured": False, "source": "", "fingerprint": ""}


def test_a_chat_channel_the_company_doesnt_allow_cant_be_set_up_or_started(company, tmp_path, monkeypatch):
    from row_bot import tasks
    from row_bot.application import channel_controls as controls
    from row_bot.runtime import admissions
    from tests.fixtures.channels import FakeChannel

    monkeypatch.setattr(tasks, "_DB_PATH", tmp_path / "tasks.db")
    monkeypatch.setattr(tasks, "_SCHEMA_READY_PATH", None)
    admissions.instance_identity()
    channel = FakeChannel(name="whatsapp", display_name="Synthetic WhatsApp")
    accounts = _ChannelAccounts()
    found = {"registry_owner": _ChannelRegistry(channel), "config_owner": _ChannelConfig(),
             "auth_owner": accounts, "secret_owner": accounts}
    revision = controls.read_channel_status("whatsapp", validate=lambda: None, **found)["revision"]

    def review():
        return controls.review_channel_command("whatsapp", revision, "start", field_key=None, value=None,
                                               identity_id=None, validate=lambda: None, **found)

    company({"apps": {"channels": "allow-list", "allowed_channels": ["slack"]}})
    with pytest.raises(controls.ChannelControlError, match="company_policy"):
        review()
    company(None)
    assert review()["operation"] == "start"



def test_a_channel_the_company_stops_allowing_is_stopped_and_sends_nothing(company, monkeypatch):
    from row_bot.channels import registry as channel_registry
    from row_bot.channels.base import ChannelHeld
    from tests.fixtures.channels import FakeChannel

    channel = FakeChannel(name="whatsapp", display_name="Synthetic WhatsApp")
    monkeypatch.setitem(channel_registry._channels, "whatsapp", channel)

    async def scenario() -> list[str]:
        assert await channel.start() is True  # Started while no company held it.
        channel.send_message("someone", "before")
        company({"apps": {"channels": "allow-list", "allowed_channels": ["slack"]}})
        # Every send is refused, whichever part of Row-Bot asks (one guard on the channel itself).
        with pytest.raises(ChannelHeld, match="your company"):
            channel.send_message("someone", "after")
        assert channel_registry.deliver("whatsapp", "someone", "after")[0] == "delivery_failed"
        stopped = channel_registry.stop_held_channels()  # As a change of policy does.
        for _ in range(5):
            await asyncio.sleep(0)
        return stopped

    assert asyncio.run(scenario()) == ["whatsapp"]
    assert not channel.is_running()
    assert [message.text for message in channel.messages] == ["before"]


def test_an_app_the_company_doesnt_allow_is_marked_in_the_catalog_and_its_card(company):
    from row_bot.integrations import facts, plans, sources

    entry, listed = _curated()
    company({"apps": {"plugins": "allow-list", "allowed_plugins": ["acme-ok"],
                      "mcp_servers": "allow-list", "allowed_mcp_servers": [f"curated:{entry.id}", "github"]}})
    found = sources.Found()
    found.add(facts.entry("plugin", "acme-tools", "Acme tools", installed=False, lifecycle="available"),
              {"kind": "plugin", "reference": "acme-tools"})
    found.add(facts.entry("plugin", "acme-ok", "Acme OK", installed=False, lifecycle="available"),
              {"kind": "plugin", "reference": "acme-ok"})
    held, allowed = found.rows

    assert [blocker["code"] for blocker in held["blockers"]] == ["company_policy"]
    assert held["next_action"]["kind"] == "none"
    assert allowed["blockers"] == [] and allowed["next_action"]["kind"] == "install"
    plan = plans.compute(held, {"kind": "plugin", "reference": "acme-tools"}, intent="add")
    assert (plan["supported"], plan["unsupported_reason"]) == (False, enforce.APP_HELD["plugins"])

    # Installed: kept, with the one quiet reason.
    installed = facts.entry("plugin", "acme-tools", "Acme tools", installed=True, lifecycle="installed")
    assert facts.company_held(installed) == enforce.APP_HELD["plugins"]
    server = facts.entry("mcp", "evil", "evil", _mcp={"name": "evil", "cfg": {"source": {}}})
    assert facts.company_held(server) == enforce.APP_HELD["mcp_servers"]
    # A catalog listing by its catalog names, never by its display name.
    listing = facts.entry("mcp", f"curated:{entry.id}", "Anything", installed=False, lifecycle="available")
    assert facts.company_listed(listing, {"kind": "mcp", "entry": entry}) == ""
    named = facts.entry("mcp", "hermes_mcp:github", "github", installed=False, lifecycle="available")
    assert facts.company_listed(named, {"kind": "hermes_mcp", "name": "github"}) == enforce.APP_HELD["mcp_servers"]


# -- export -------------------------------------------------------------------------------------------

def test_chat_export_and_the_data_backup_are_refused_on_the_server_while_export_is_off(company):
    from row_bot.application import client_data_backup, conversation_actions
    from row_bot.application.client_platform import ClientPlatformError

    company({"data": {"chat_export": False}})

    with pytest.raises(conversation_actions.ConversationActionError) as reviewed:
        conversation_actions.review_conversation_action(None, "conversation-1", "conversation.export", "1", {},
                                                        validate=lambda: None)
    assert reviewed.value.code == "company_policy"
    command_id = str(uuid4())
    command = {"command_id": command_id, "type": "conversation.export", "expected_revision": "1",
               "payload": {"checkpoint_revision": "checkpoint-1", "action_digest": "a" * 64,
                           "export_title": "Chat"}}
    with pytest.raises(conversation_actions.ConversationActionError) as executed:
        conversation_actions.execute_conversation_action(None, "conversation-1", command, owner_id="owner",
                                                         key=command_id, validate=lambda: None,
                                                         validate_review=lambda review: None)
    assert executed.value.code == "company_policy"
    for action in (client_data_backup.backup, client_data_backup.open_last_backup):
        with pytest.raises(ClientPlatformError) as refused:
            action()
        assert refused.value.code == "company_policy"


def test_a_backup_never_carries_the_company_and_a_restore_never_replaces_it():
    from row_bot import profile_restore

    assert profile_restore.kept_in_place("company.json")
    assert profile_restore.kept_in_place("company_policy.json")
    assert profile_restore.kept_in_place("company_audit.jsonl")  # What waits to be told to it (C4).


# -- lockdown, the disclosure, and no company -----------------------------------------------------------

def test_lockdown_enforces_lockdown_policy(company, monkeypatch, agent_context):
    _registered(monkeypatch, ("memory", "shell", "browser", "mcp"))
    company(lockdown=True)

    assert [tool.name for tool in tool_registry.get_enabled_tools()] == ["memory"]
    agent_context._approval_mode_var.set("auto")
    assert agent_context.get_approval_mode() == "block"
    assert approval_gate.resolve_approval_line(DELETE, approval_mode="auto") == ("block", "")
    assert not enforce.allows_plugin("acme-tools")
    assert not enforce.allows_app("channels", "slack")
    assert enforce.mcp_refusal("github", {"source": {}}) == enforce.APP_HELD["mcp_servers"]
    assert not enforce.allows_chat_export()


def test_settings_lists_c3bs_settings_in_plain_words(company):
    restrictive = resolve(parse_policy({
        "tools": {"disabled_groups": ["shell"], "disabled_tools": ["run_command"]},
        "approvals": {"max_level": "approve"},
        "apps": {"plugins": "none", "mcp_servers": "any", "skills_hub": "any", "channels": "any"},
        "data": {"chat_export": False, "share_to_company": False}}))

    words = {lock["setting"]: lock["words"] for lock in policy.locked(restrictive)}
    assert words["tools.disabled_groups"] == "Your company switched off some of Row-Bot's tools"
    assert words["approvals.max_level"] == "Your company limits how much Row-Bot may do without asking"
    assert words["apps.plugins"] == "Your company limits which plugins you can add"
    assert words["data.chat_export"] == "Your company turned off chat export"
    assert words["data.share_to_company"] == "Your company turned off Share to company"
    assert words["tools.disabled_tools"] == "Your company switched off some single tools"
    assert not {"apps.mcp_servers", "apps.skills_hub", "apps.channels"} & set(words)
    rules = {rule["setting"]: rule["value"] for rule in policy.rules(restrictive)}
    assert (rules["approvals.max_level"], rules["apps.plugins"], rules["tools.disabled_groups"]) == (
        "Ask", "None", "shell")

    assert enforce.limits(restrictive) == {
        "tools_off": ["run_command", "shell"], "max_level": "approve", "chat_export": False,
        "share_to_company": False, "plugins": {"mode": "none", "allowed": []}, "mcp_servers": {"mode": "any", "allowed": []},
        "skills_hub": {"mode": "any", "allowed": []}, "channels": {"mode": "any", "allowed": [
            "discord", "slack", "sms", "telegram"]}}
    json.dumps(enforce.limits(restrictive))  # As the view sends it.


def test_with_no_company_joined_nothing_changes(company, monkeypatch, agent_context):
    from row_bot.plugins import state as plugin_state

    company(None)
    _registered(monkeypatch, ("shell", "browser"))
    assert [tool.name for tool in tool_registry.get_enabled_tools()] == ["shell", "browser"]
    agent_context._approval_mode_var.set("full")
    assert agent_context.get_approval_mode() == "full"
    assert decision_for_action("full", by_person=True) == "allow"
    monkeypatch.setattr(plugin_state, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(plugin_state, "_state", {"acme-tools": {"enabled": True}})
    assert plugin_state.is_plugin_enabled("acme-tools") is True
    assert enforce.mcp_refusal("anything", {"source": {}}) is None
    assert tool_registry.is_switched_on("shell") is True
    assert enforce.allows_app("channels", "whatsapp")
    assert enforce.allows_chat_export()
    assert enforce.allows_standing_approvals()
    assert enforce.tool_refusal("run_command", parent="shell") is None
    assert enforce.ceiling() is None and enforce.limits(policy.current()) is None
