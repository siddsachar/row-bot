"""Phase 3 behaviour: the background runner, expiry, Turn off and Remove, picked files and links,
and access that only an explicit Full access (or one tool's "Use") widens. Fakes only."""
# ruff: noqa: F811 -- shared isolated fixtures
import copy
import io
import json
from uuid import uuid4
import zipfile

import pytest

from row_bot import secret_store
from row_bot.application import client_integrations as api
from row_bot.application.client_platform import ClientPlatformError
from row_bot.integrations import facts, plans, presets, uploads
from row_bot.mcp_client import config, runtime as mcp_runtime, safety
from tests.subsystem.mcp.test_capability_catalog_controls import owner  # noqa: F401
from tests.subsystem.plugins.conftest import MemoryKeyring

pytestmark = [pytest.mark.platform, pytest.mark.mcp_transport]

LOCKS = {"get_record": False, "update_record": False, "delete_record": True, "unrecognized": True}


@pytest.fixture
def item(owner):
    owner.tools.append({"name": "update_record", "description": "Change a synthetic record", "inputSchema": {}})
    secret_store._set_backend_for_tests(MemoryKeyring())
    facts.invalidate()
    yield next(row for row in facts.inventory()[0] if row["name"] == "Synthetic")["id"]
    secret_store._set_backend_for_tests(None)


def ctx(**fields):
    return plans.Context(owner_id="owner", mcp_owner_id="owner", validate=lambda: None, local_owner=True, **fields)


def connect(item_id, preset, overrides=None):
    _, plan = api.read_item(owner_id="owner", item_id=item_id)
    plan_id = str(uuid4())
    paused = api.start_plan(ctx(), plan_id=plan_id, item_id=item_id, digest=plan["digest"], preset=preset)
    access = next(s for s in paused["steps"] if s["type"] == "access")["access"]
    done = plans.resume(ctx(tools_digest=access["tools_digest"]), plan_id, overrides=overrides)
    assert done["state"] == "completed", done
    return access


def saved():
    return config.read_saved_configuration().document["servers"]["Synthetic"]


def gate(tools):
    """What the invocation gate does with each tool under the saved policy."""
    found = mcp_runtime._normalize_tools("Synthetic", saved(), tools)
    return {n: "off" if not i.enabled else "ask" if i.requires_approval else "use" for n, i in found.items()}


@pytest.mark.parametrize(("name", "annotations", "effect", "high_impact"), [
    ("get_record", None, "read_only", False),
    ("update_record", None, "mutation", False),
    ("create_issue", {"readOnlyHint": True}, "mutation", False),  # A change by name stays a change.
    ("tag_issue", None, "mutation", False),
    ("get_label", {"readOnlyHint": True}, "read_only", False),  # A noun after a read verb is not a change.
    ("list_by_tag", None, "read_only", False),
    ("create_release", None, "mutation", True),  # A routine verb on something that publishes or grants access.
    ("add_collaborator", None, "mutation", True),
    ("set_password", {"readOnlyHint": True}, "mutation", True),
    ("create_pull_request", None, "mutation", True),
    ("list_members", None, "read_only", False),
    ("reset_workspace", None, "mutation", True),
    ("add_user_to_org", None, "mutation", True),
    ("update_repository_visibility", None, "mutation", True),
    ("delete_record", None, "mutation", True),
    ("send_message", None, "mutation", True),
    ("add_comment", None, "mutation", True),  # Reaches other people.
    ("upload_file", None, "mutation", True),  # Moves local data out.
    ("push_files", None, "mutation", True),
    ("merge_pull_request", None, "mutation", True),
    ("create_repository", None, "mutation", True),
    ("create_branch", None, "mutation", False),
    ("get_file_contents", None, "read_only", False),
    ("lookup", {"destructiveHint": True}, "mutation", True),
    ("frobnicate", None, "unknown", False),
    ("frobnicate", {"readOnlyHint": False, "destructiveHint": False}, "unknown", False),  # Hints never relax.
    ("browser_close", None, "interaction", False),
])
def test_classification_separates_routine_changes_from_high_impact_ones(name, annotations, effect, high_impact):
    tool = {"annotations": annotations} if annotations else None
    assert safety.classify_tool_effect(name, "", tool) == effect
    assert safety.is_destructive_tool(name, "", tool) is high_impact


@pytest.mark.parametrize(("name", "description", "annotations", "effect", "high_impact"), [
    # Found live: GitHub's "Get commit" asked every time. A read of a commit, a run or an order is a read.
    ("get_commit", "Get details for a commit from a GitHub repository", None, "read_only", False),
    ("list_commits", "Get list of commits of a branch in a GitHub repository", None, "read_only", False),
    ("get_workflow_run", "Get details of a specific workflow run", None, "read_only", False),
    ("get_order", "Get an order by its id", None, "read_only", False),
    # Still asks: another verb joined on, a server saying it is destructive, a change in its description,
    # a high-impact word that is not what a read returns, or the word as the name's verb.
    ("get_and_push", "", None, "mutation", True),
    ("get_commit", "", {"destructiveHint": True}, "mutation", True),
    ("get_commit", "Get a commit and push it to main", None, "unknown", False),
    ("list_workflow_runs", "List workflow runs and cancel stale ones", None, "unknown", False),
    ("read_delete_log", "", None, "mutation", True),
    ("commit_changes", "", None, "mutation", True),
    ("run_query", "", None, "mutation", True),
])
def test_a_read_of_a_commit_run_or_order_is_a_read_and_changes_still_ask(name, description, annotations, effect,
                                                                         high_impact):
    tool = {"annotations": annotations} if annotations else None
    assert safety.classify_tool_effect(name, description, tool) == effect
    assert safety.is_destructive_tool(name, description, tool) is high_impact


def test_a_file_write_is_high_impact_in_a_repository_and_routine_on_this_computer():
    assert safety.is_destructive_tool("create_or_update_file", "Create or update a single file in a GitHub repository")
    assert not safety.is_destructive_tool("edit_file", "Make line-based edits to a text file in an allowed folder")
    assert safety.classify_tool_effect("edit_file", "Make line-based edits to a text file in an allowed folder") == "mutation"


def test_a_tool_only_described_as_a_change_always_asks_unless_hinted_read_only():
    assert safety.classify_tool_effect("get_or_make_page", "Update the page, creating it when missing") == "unknown"
    assert safety.classify_tool_effect("collaborator", "Add a collaborator to a repository") == "unknown"
    assert safety.classify_tool_effect("get_page", "Update the page", {"annotations": {"readOnlyHint": True}}) == "read_only"


def test_routine_changes_ask_until_allowed_while_high_impact_and_unknown_always_ask():
    tools = [{"name": name} for name in ("get_record", "update_record", "delete_record", "frobnicate")]
    allowed = {"enabled": True, "tools": {"enabled": dict.fromkeys(["update_record", "delete_record", "frobnicate"], True),
                                          "run_without_asking": ["update_record", "delete_record", "frobnicate"]}}
    found = mcp_runtime._normalize_tools("fixture", allowed, tools)
    assert {n: i.requires_approval for n, i in found.items()} == {
        "get_record": False, "update_record": False, "delete_record": True, "frobnicate": True}
    # A policy saved before this change has no allowance: routine changes keep asking and stay off by default.
    legacy = mcp_runtime._normalize_tools("fixture", {"enabled": True, "tools": {"enabled": {"update_record": True}}}, tools)
    assert legacy["update_record"].requires_approval is True
    fresh = mcp_runtime._normalize_tools("fixture", {"enabled": True, "tools": {}}, tools)
    assert (fresh["update_record"].enabled, fresh["delete_record"].enabled, fresh["get_record"].enabled) == (False, False, True)


@pytest.mark.parametrize(("preset", "expected"), [
    ("full", {"get_record": "use", "update_record": "use", "delete_record": "ask", "unrecognized": "ask"}),
    ("ask", {"get_record": "use", "update_record": "ask", "delete_record": "ask", "unrecognized": "ask"}),
    ("read_only", {"get_record": "use", "update_record": "off", "delete_record": "off", "unrecognized": "off"}),
])
def test_each_preset_is_what_the_invocation_gate_enforces(item, owner, preset, expected):
    access = connect(item, preset)
    assert {t["name"]: t["always_asks"] for t in access["tools"]} == LOCKS
    assert all(t["description"] for t in access["tools"]), "readable descriptions reach the access sheet"
    assert gate(owner.tools) == expected
    assert {name: presets.actual(saved()["tools"], name) for name in expected} == expected
    assert presets.current(saved()["tools"]) == preset


def test_tools_to_allow_are_named_in_words_whatever_their_case(item, owner):
    owner.tools.append({"name": "createJiraIssue", "description": "", "inputSchema": {}})
    titles = {tool["name"]: tool["title"] for tool in connect(item, "ask")["tools"]}
    assert (titles["createJiraIssue"], titles["update_record"]) == ("Create jira issue", "Update record")


def test_customised_tools_apply_and_a_locked_tool_never_runs_without_asking(item, owner):
    connect(item, "ask", overrides={"update_record": "use", "get_record": "off"})
    assert gate(owner.tools) == {"get_record": "off", "update_record": "use", "delete_record": "ask", "unrecognized": "ask"}
    assert presets.current(saved()["tools"]) == "custom"
    with pytest.raises(ValueError, match="approval_required"):
        presets.set_state(saved()["tools"], "delete_record", "use", saved()["tools"]["catalog"]["delete_record"])
    facts.invalidate()
    detail, plan = api.read_item(owner_id="owner", item_id=item, intent="access")
    before = json.dumps(saved()["tools"], sort_keys=True)
    refused = api.start_plan(ctx(tools_digest=detail["about"]["access"]["tools_digest"]), plan_id=str(uuid4()), item_id=item,
                             intent="access", digest=plan["digest"], preset="full", overrides={"delete_record": "use"})
    assert refused["state"] == "failed" and json.dumps(saved()["tools"], sort_keys=True) == before


def test_full_access_later_widens_only_routine_changes(item, owner):
    connect(item, "ask")
    facts.invalidate()
    detail, plan = api.read_item(owner_id="owner", item_id=item, intent="access")
    assert detail["about"]["access"]["preset"] == "ask"
    done = api.start_plan(ctx(tools_digest=detail["about"]["access"]["tools_digest"]), plan_id=str(uuid4()), item_id=item,
                          intent="access", digest=plan["digest"], preset="full")
    assert done["state"] == "completed", done
    assert gate(owner.tools) == {"get_record": "use", "update_record": "use", "delete_record": "ask", "unrecognized": "ask"}


def test_a_background_plan_reports_progress_and_reads_never_step_it(item, owner, monkeypatch):
    queued = []
    monkeypatch.setattr(plans, "_spawn", queued.append)
    _, plan = api.read_item(owner_id="owner", item_id=item)
    plan_id = str(uuid4())
    started = api.start_plan(ctx(), plan_id=plan_id, item_id=item, digest=plan["digest"], background=True)
    assert started["state"] == "running" and len(queued) == 1 and owner.calls == []
    assert plans.read_plan(ctx(), plan_id)["state"] == "running" and owner.calls == []
    with pytest.raises(plans.PlanError, match="operation_pending"):
        plans.resume(ctx(), plan_id)
    queued.pop()()
    paused = plans.read_plan(ctx(), plan_id)
    assert (paused["state"], paused["pause"]) == ("paused", "access")
    assert [s["state"] for s in paused["steps"]] == ["done", "done", "waiting", "pending"]
    assert "catalog" not in saved().get("tools", {}), "nothing is accepted before access is confirmed"


def test_a_second_start_while_one_runs_shows_the_first_instead_of_failing(item, owner, monkeypatch):
    """B309: two clicks, or two pages, each agreed to connect; the second shows the first one's progress."""
    queued = []
    monkeypatch.setattr(plans, "_spawn", queued.append)
    _, plan = api.read_item(owner_id="owner", item_id=item)
    first = str(uuid4())
    assert api.start_plan(ctx(), plan_id=first, item_id=item, digest=plan["digest"], background=True)["state"] == "running"
    second = api.start_plan(ctx(), plan_id=str(uuid4()), item_id=item, digest=plan["digest"], background=True)
    assert (second["plan_id"], second["state"]) == (first, "running")
    assert len(queued) == 1 and owner.calls == []  # Nothing else was started.
    other = plans.compute(facts.read(item), {"kind": "mcp", "reference": item}, intent="remove", cleanup=True)
    with pytest.raises(plans.PlanError, match="operation_pending"):  # Another choice is never taken for the first.
        plans.start(ctx(), facts.read(item), {"kind": "mcp", "reference": item}, digest=other["digest"],
                    intent="remove", cleanup=True, background=True)


@pytest.fixture
def chats(owner, tmp_path, monkeypatch):
    """The "Use apps in chats" switch, off, on a tool registry of its own (it finds its file by data folder)."""
    from row_bot import tool_configuration
    from row_bot.tools import registry
    from row_bot.tools.mcp_tool import McpTool
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(registry, "_active_config_path", tool_configuration.configuration_path())
    monkeypatch.setattr(registry, "_tools", {"mcp": McpTool()})
    monkeypatch.setattr(registry, "_enabled", {"mcp": False})
    monkeypatch.setattr(registry, "_tool_configs", {})
    monkeypatch.setattr(registry, "_global_config", {})
    monkeypatch.setattr(registry, "_invalidate_agent_cache", lambda: None)

    def switch(on: bool) -> None:  # As the switch saves it: each save names its own change.
        change = str(uuid4())
        publication = {"owner_id": "settings", "key": change, "command_id": change}
        tool_configuration.configuration_path().write_text(json.dumps({"tools": {"mcp": on}, "_client_publication": publication}),
                                                           encoding="utf-8")
        registry._enabled["mcp"] = on
    switch(False)
    return registry, switch


def test_connecting_an_app_lets_chats_use_it(item, owner, chats):
    """B308: connecting is asking to use the app, so the consent says chats will, and its tools reach a chat."""
    registry, _ = chats
    _, plan = api.read_item(owner_id="owner", item_id=item)
    assert plan["consent"]["turns_on_chats"] is True
    connect(item, "ask")
    from row_bot.application.native_mcp_controls import read_native_mcp_state
    assert read_native_mcp_state().saved_enabled is True and registry.is_enabled("mcp")
    assert "mcp_synthetic_get_record" in {tool.name for tool in registry.get_langchain_tools()}


@pytest.mark.parametrize("on_at_consent", [True, False])
def test_a_chats_switch_turned_off_after_agreeing_stays_off_and_is_named(item, owner, chats, on_at_consent):
    registry, switch = chats
    switch(on_at_consent)
    _, plan = api.read_item(owner_id="owner", item_id=item)
    assert plan["consent"]["turns_on_chats"] is not on_at_consent
    plan_id = str(uuid4())
    paused = api.start_plan(ctx(), plan_id=plan_id, item_id=item, digest=plan["digest"], preset="ask")
    switch(True)
    switch(False)  # The person turns it off meanwhile (on, then off again): connecting never overrides that.
    access = next(s for s in paused["steps"] if s["type"] == "access")["access"]
    done = plans.resume(ctx(tools_digest=access["tools_digest"]), plan_id)
    assert done["state"] == "completed" and "turn on Use apps in Apps" in done["message"]
    assert registry.is_enabled("mcp") is False


@pytest.mark.parametrize(("wrote", "says"), [
    (["Traceback (most recent call last):", "AttributeError: 'Server' object has no attribute 'list_resources'"],
     "Pin its mcp dependency"),
    (["Starting with key synthetic-secret and password shortpw99", "Could not reach the database"],
     "What it wrote last is below."),
])
def test_a_server_that_fails_to_start_shows_what_it_wrote(item, owner, monkeypatch, wrote, says):
    """F22: the person sees why, in the program's own last lines, with what it was started with masked: its
    variables (the fixture's PRIVATE) and a key given on its command line."""
    class Wrote:
        def lines(self, wait=0.0):
            return wrote

    async def fails(server):
        server._stderr = Wrote()
        server.cfg = {**server.cfg, "args": ["--password", "shortpw99"]}
        raise RuntimeError("Connection closed")
    monkeypatch.setattr(mcp_runtime.McpServerRuntime, "_connect", fails)
    _, plan = api.read_item(owner_id="owner", item_id=item)
    plan_id = str(uuid4())
    assert api.start_plan(ctx(), plan_id=plan_id, item_id=item, digest=plan["digest"])["state"] == "failed"
    failed = plans.read_plan(ctx(), plan_id)  # Row-Bot on this computer.
    step = next(s for s in failed["steps"] if s["state"] == "failed")
    assert failed["state"] == "failed" and says in failed["message"]
    assert step["log"] == [line.replace("synthetic-secret", "\u2026").replace("shortpw99", "\u2026") for line in wrote]
    assert "synthetic-secret" not in json.dumps(failed) and "shortpw99" not in json.dumps(failed)
    remote = plans.read_plan(plans.Context(owner_id="owner", mcp_owner_id="owner", validate=lambda: None), plan_id)
    assert all("log" not in s for s in remote["steps"])  # Never to another device,
    assert wrote[-1] not in json.dumps(plans._load("owner", plan_id)[0])  # and never kept in the plan's record.


def test_a_refused_key_fails_the_check_and_lets_the_app_go(item, owner, monkeypatch):
    """Found live (GitHub): a key the service refused left the check, and then Remove, finishing for good."""
    import asyncio
    import contextlib

    import httpx

    async def refused(server):
        # As the MCP SDK does it: the refused request cancels this task, and its HTTP error comes as it closes.
        request = httpx.Request("POST", "https://synthetic.example.test/mcp")
        error = httpx.HTTPStatusError("Client error '401 Unauthorized'", request=request,
                                      response=httpx.Response(401, request=request))

        async def raise_at_close():
            raise ExceptionGroup("unhandled errors in a TaskGroup", [error])
        server.exit_stack = contextlib.AsyncExitStack()
        server.exit_stack.push_async_callback(raise_at_close)
        raise asyncio.CancelledError
    monkeypatch.setattr(mcp_runtime.McpServerRuntime, "_connect", refused)
    _, plan = api.read_item(owner_id="owner", item_id=item)
    failed = api.start_plan(ctx(), plan_id=str(uuid4()), item_id=item, digest=plan["digest"])
    assert failed["state"] == "failed" and "didn't accept the key" in failed["message"], failed
    _, remove = api.read_item(owner_id="owner", item_id=item, intent="remove")
    removed = api.start_plan(ctx(), plan_id=str(uuid4()), item_id=item, intent="remove", digest=remove["digest"])
    assert removed["state"] == "completed" and "Synthetic" not in config.read_saved_configuration().document["servers"]


def test_a_remove_left_unfinished_after_the_app_is_gone_can_still_be_found_and_finished(item, owner, monkeypatch):
    """Found live (GitHub): Remove deleted the app but its cleanup stayed unconfirmed, so its page said "Couldn't
    open this", Apps didn't list it, and its saved key stayed until something read the plan."""
    stuck = {"on": True}
    stop = mcp_runtime.stop_server_owned
    monkeypatch.setattr(mcp_runtime, "get_server_lifecycle",
                        lambda name: {"runtime_id": "stuck"} if stuck["on"] else {"runtime_id": None})
    monkeypatch.setattr(mcp_runtime, "stop_server_owned",
                        lambda name, runtime_id, **kw: {"state": "cleanup_incomplete"} if stuck["on"] else stop(name, runtime_id, **kw))
    _, remove = api.read_item(owner_id="owner", item_id=item, intent="remove", cleanup=True)
    api.start_plan(ctx(), plan_id=str(uuid4()), item_id=item, intent="remove", digest=remove["digest"], cleanup=True)
    assert "Synthetic" not in config.read_saved_configuration().document["servers"]  # The app is gone,
    from row_bot.api.v1 import schemas as dto
    mine = dto.IntegrationEntryPage.model_validate_json(json.dumps(api.read_items(owner_id="owner")))  # As sent.
    listed = next(i for i in mine.model_dump(mode="json")["items"] if i["id"] == item)
    assert (listed["readiness"], listed["next_action"]["kind"]) == ("attention", "retry")  # but still listed,
    page, _ = api.read_item(owner_id="owner", item_id=item)  # and its page shows the change, not "Couldn't open this".
    sent = dto.IntegrationDetail.model_validate_json(json.dumps(page)).model_dump(mode="json")
    assert (sent["plan"]["intent"], sent["plan"]["state"]) == ("remove", "uncertain")
    stuck["on"] = False  # A restart later: what held it has gone.
    assert api.settle_item(ctx(), item_id=item)["plan"]["pause"] == "resume"  # Retry proves the cleanup,
    done = plans.resume(ctx(), page["plan"]["plan_id"])  # and Continue finishes the Remove.
    assert (done["state"], done["message"]) == ("completed", "Removed.")
    assert all(i["id"] != item for i in api.read_items(owner_id="owner")["items"])
    with pytest.raises(ClientPlatformError, match="not_found"):
        api.read_item(owner_id="owner", item_id=item)


def test_a_tool_list_row_bot_cannot_keep_says_so_instead_of_claiming_a_change(item, owner):
    owner.tools[:] = [{"name": f"get_{n}", "description": "d" * 16000, "inputSchema": {}} for n in range(10)]
    _, plan = api.read_item(owner_id="owner", item_id=item)
    failed = api.start_plan(ctx(), plan_id=str(uuid4()), item_id=item, digest=plan["digest"], preset="ask")
    assert failed["state"] == "failed" and failed["message"] == plans._MESSAGES["tools_unreadable"]


def test_a_paused_plan_expires_keeps_what_was_done_and_frees_the_item(item, owner, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(plans, "_now", lambda: clock[0])
    _, plan = api.read_item(owner_id="owner", item_id=item)
    plan_id = str(uuid4())
    assert api.start_plan(ctx(), plan_id=plan_id, item_id=item, digest=plan["digest"])["pause"] == "access"
    clock[0] += plans.EXPIRES - 1
    assert plans.read_plan(ctx(), plan_id)["state"] == "paused"
    clock[0] += 2
    calls = list(owner.calls)
    expired = plans.read_plan(ctx(), plan_id)
    assert expired["state"] == "expired" and expired["next_action"]["kind"] == "none" and owner.calls == calls
    with pytest.raises(plans.PlanError, match="plan_not_resumable"):
        plans.resume(ctx(), plan_id)
    detail, again = api.read_item(owner_id="owner", item_id=item)
    assert detail["plan"]["plan_id"] is None
    assert api.start_plan(ctx(), plan_id=str(uuid4()), item_id=item, digest=again["digest"])["pause"] == "access"


def test_turn_off_and_remove_are_consented_plans_and_cleanup_is_part_of_the_consent(item, owner):
    connect(item, "ask")
    facts.invalidate()
    _, plan = api.read_item(owner_id="owner", item_id=item, intent="turn_off")
    assert [s["type"] for s in plan["steps"]] == ["consent", "enable"]
    from row_bot.application.capability_runtime_controls import read_mcp_runtime_state
    server_id = facts.read(item)["owner_ref"]
    assert read_mcp_runtime_state(server_id).state == "connected"
    done = api.start_plan(ctx(), plan_id=str(uuid4()), item_id=item, intent="turn_off", digest=plan["digest"])
    assert (done["state"], done["message"]) == ("completed", "Turned off.") and saved()["enabled"] is False
    assert read_mcp_runtime_state(server_id).state in {"stopped", "missing"}  # Turning off stops the live session.
    _, keep = api.read_item(owner_id="owner", item_id=item, intent="remove")
    _, clean = api.read_item(owner_id="owner", item_id=item, intent="remove", cleanup=True)
    assert keep["digest"] != clean["digest"] and (keep["consent"]["cleanup"], clean["consent"]["cleanup"]) == (False, True)
    with pytest.raises(plans.PlanError, match="plan_changed"):  # Agreeing to keep data never deletes it.
        api.start_plan(ctx(), plan_id=str(uuid4()), item_id=item, intent="remove", digest=keep["digest"], cleanup=True)
    removed = api.start_plan(ctx(), plan_id=str(uuid4()), item_id=item, intent="remove", digest=keep["digest"])
    assert removed["state"] == "completed" and "Synthetic" not in config.read_saved_configuration().document["servers"]


def test_a_recheck_keeps_earlier_allowances_and_applies_explicit_choices_to_earlier_tools(item, owner):
    from row_bot.application import capability_catalog_controls as catalog, capability_runtime_controls as lifecycle
    from row_bot.application.capability_configuration_controls import read_mcp_configuration
    from tests.subsystem.mcp.test_capability_catalog_controls import test_request

    def check_and_accept(preset, overrides=None):
        tested = test_request()
        result = lifecycle.execute_mcp_runtime_command(owner_id="owner", key=tested["command_id"], command=tested,
                                                       validate=lambda: None, validate_review=lambda _: None)
        assert result["mcp_runtime"]["state"] == "tested", result
        payload = {"configuration_revision": read_mcp_configuration().revision, "server_id": tested["payload"]["server_id"],
                   "test_command_id": tested["command_id"], "preset": preset, **({"overrides": overrides} if overrides else {})}
        command = {"command_id": str(uuid4()), "type": "mcp.catalog.accept", "expected_revision": "0", "payload": payload}
        catalog.execute_mcp_catalog_command(owner_id="owner", key=command["command_id"], command=command,
                                            validate=lambda: None, validate_review=lambda review: None)
    check_and_accept("full")
    assert gate(owner.tools)["update_record"] == "use"
    check_and_accept("ask", {"get_record": "off"})  # Checking the connection again, as a fix does.
    assert gate(owner.tools)["update_record"] == "use"  # Accepting again never takes back what the user allowed.
    assert gate(owner.tools)["get_record"] == "off"  # An explicit choice reaches a tool accepted before.


def test_a_read_never_overwrites_progress_saved_while_it_looked(item, owner, monkeypatch):
    _, plan = api.read_item(owner_id="owner", item_id=item)
    plan_id = str(uuid4())
    paused = api.start_plan(ctx(), plan_id=plan_id, item_id=item, digest=plan["digest"])
    assert paused["pause"] == "access"
    record, _ = plans._load("owner", plan_id)
    record.update(state="running", pause=None)  # As if its runner stopped mid-step.
    plans._save(record)

    def runner_finishes_meanwhile(_ctx, seen, _step):
        moved = copy.deepcopy(seen)
        moved.update(state="paused", pause="access", message="moved on")
        plans._save(moved)
        return "resume"
    monkeypatch.setitem(plans._OBSERVERS, "access", runner_finishes_meanwhile)
    assert plans.read_plan(ctx(), plan_id)["message"] == "moved on"
    assert plans._load("owner", plan_id)[0]["message"] == "moved on"


def test_saving_access_never_runs_setup_steps_nobody_agreed_to(item, owner, monkeypatch):
    connect(item, "ask")
    facts.invalidate()
    _, plan = api.read_item(owner_id="owner", item_id=item, intent="access")
    assert [s["type"] for s in plan["steps"]] == ["consent", "test", "access", "enable"] and plan["supported"]
    real = facts.mcp_setup
    monkeypatch.setattr(facts, "mcp_setup", lambda *args: {**real(*args), "auth_mode": "oauth", "credential_configured": False})
    _, signed_out = api.read_item(owner_id="owner", item_id=item, intent="access")
    assert [s["type"] for s in signed_out["steps"]] == ["consent", "test", "access", "enable"]
    assert not signed_out["supported"] and "Finish setting up" in signed_out["unsupported_reason"]


def _found(**fields):
    """What inspecting a package found."""
    return {"plugin_id": "kit", "name": "Kit", "version": "1.1.0", "publisher": "Example", "pin": "new", "preview_id": "preview",
            "tree_digest": "sha256:" + "1" * 64, "skills": [], "servers": [], "tools": [], "permissions": [], **fields}


def _reviewed(row, plan):
    """Start a package plan; it shows what the package declares and waits; continue on exactly that."""
    paused = plans.start(ctx(), row, {}, digest=plan["digest"], intent=plan["intent"])
    review = next(s for s in paused["steps"] if s.get("review"))["review"]
    assert paused["pause"] == "digest_changed"
    return plans.resume(ctx(review_digest=review["digest"]), paused["plan_id"])


def test_a_package_is_added_only_after_the_person_has_seen_what_it_runs_and_may_do(monkeypatch):
    from row_bot.application import client_plugin_lifecycle as lifecycle
    from row_bot.plugins import hermes_catalog
    row = facts.finish(facts.entry("plugin", "kit", "Kit", lifecycle="available"))
    monkeypatch.setattr(hermes_catalog, "inspect_package", lambda **_: _found(
        tools=["Sample Tool"], permissions=["shell_processes", "external_send"],
        servers=[{"key": "notes", "transport": "stdio", "command": "node", "args": ["server.js"], "url": ""}]))
    monkeypatch.setattr(lifecycle, "review_plugin_lifecycle", lambda action, plugin_id, **_: {"changes": [], "revision": "r"})
    sent = []
    monkeypatch.setattr(lifecycle, "execute_plugin_lifecycle", lambda command, **_: sent.append(command["action"]) or {"status": "completed"})
    plan = plans.compute(row, {"kind": "plugin", "reference": "https://github.com/example/kit"}, intent="add")
    paused = plans.start(ctx(), row, {"kind": "plugin", "reference": "https://github.com/example/kit"}, digest=plan["digest"],
                         intent="add")
    review = next(s for s in paused["steps"] if s.get("review"))["review"]
    assert paused["pause"] == "digest_changed" and sent == []  # Nothing is added before the person has seen it.
    assert review["summary"] == "Kit 1.1.0" and review["lines"] == [
        "Runs its own code on this computer for: Sample Tool.", "Runs node server.js on this computer.",
        "Asks to run programs on this computer, send messages or posts for you.",
        "Says it comes from Example; Row-Bot hasn't checked that."]
    assert plans.resume(ctx(review_digest="sha256:" + "2" * 64), paused["plan_id"])["pause"] == "digest_changed"
    assert sent == []  # Agreeing to something else adds nothing.
    assert plans.resume(ctx(review_digest=review["digest"]), paused["plan_id"])["state"] == "completed" and sent == ["install"]


def _updating_kit(monkeypatch, origin, entries):
    """An installed package from ``origin`` with the catalog holding ``entries``; records what is read and sent."""
    from row_bot.application import client_plugin_lifecycle as lifecycle
    from row_bot.plugins import hermes_catalog, state
    from row_bot.plugins.lifecycle_review import NO_CHANGES
    source = "https://github.com/example/kit"
    row = facts.finish(facts.entry("plugin", "kit", "Kit", installed=True, lifecycle="installed", pin="a" * 40,
                                   source_url=source, canonical_identity="plugin:" + source + "@" + "a" * 40))
    monkeypatch.setattr(state, "package_origin", lambda plugin_id: origin)
    monkeypatch.setattr(state, "get_plugin_package_state", lambda plugin_id: {"digest": "sha256:" + "9" * 64})
    monkeypatch.setattr(hermes_catalog, "read_catalog", lambda **_: {"entries": [{**e, "source_identity": source} for e in entries]})
    inspected, sent = [], []
    monkeypatch.setattr(hermes_catalog, "inspect_package", lambda **kwargs: inspected.append(kwargs["reference"]) or _found())
    monkeypatch.setattr(lifecycle, "review_plugin_lifecycle", lambda action, plugin_id, **_: {"changes": [NO_CHANGES], "revision": "r"})
    monkeypatch.setattr(lifecycle, "execute_plugin_lifecycle", lambda command, **_: sent.append(command["action"]) or {"status": "completed"})
    return row, inspected, sent


def test_an_update_that_changes_nothing_the_package_can_do_is_applied(monkeypatch):
    row, inspected, sent = _updating_kit(monkeypatch, "local", [])
    done = _reviewed(row, plans.compute(row, {}, intent="update"))
    assert (done["state"], done["message"], sent, inspected) == ("completed", "Updated.", ["update"], ["https://github.com/example/kit"])


def test_a_package_added_from_hermes_updates_to_the_catalog_pin_not_the_repository_head(monkeypatch):
    row, inspected, _ = _updating_kit(monkeypatch, "hermes", [{"id": "hermes:kit", "pin": "b" * 40}])
    assert _reviewed(row, plans.compute(row, {}, intent="update"))["state"] == "completed"
    assert inspected == ["hermes:kit"]  # The catalog's pinned entry, never the repository's moving head.


def test_a_package_that_left_its_catalog_is_never_updated_from_its_repository(monkeypatch):
    row, inspected, _ = _updating_kit(monkeypatch, "hermes", [])
    plan = plans.compute(row, {}, intent="update")
    failed = plans.start(ctx(), row, {}, digest=plan["digest"], intent="update")
    assert failed["state"] == "failed" and "left its catalog" in failed["message"] and inspected == []


def test_a_marketplace_package_updates_against_the_marketplace(monkeypatch):
    row, inspected, _ = _updating_kit(monkeypatch, "marketplace", [])
    assert _reviewed(row, plans.compute(row, {}, intent="update"))["state"] == "completed"
    assert inspected == ["marketplace:kit"]  # Checked against the checksum the marketplace publishes.


def test_removing_what_a_package_left_behind_is_agreed_as_deleting_its_data():
    row = facts.finish(facts.entry("plugin", "kept", "Kept", installed=True, lifecycle="data_retained"))
    plan = plans.compute(row, {}, intent="remove")
    assert plan["consent"]["cleanup"] is True
    assert plans.compute(row, {}, intent="remove", cleanup=True)["digest"] == plan["digest"]


def test_retry_checks_only_this_owners_unfinished_changes_again_and_packages_stay_local(item, owner, monkeypatch):
    from row_bot.mcp_client import targets
    from row_bot.runtime import admissions
    target = targets.admission_target(targets.normalize(None))
    pending = [{"target": target, "owner_id": who, "command_id": who + "-change", "type": kind}
               for who, kind in (("owner", "mcp.configuration.save"), ("another-device", "mcp.configuration.save"),
                                 ("another-device", "skill.hub.install"))]
    monkeypatch.setattr(admissions, "read_unfinished_commands", lambda **_: {"items": pending, "overflow": False})
    checked = []
    monkeypatch.setattr(facts, "reconcile_command", lambda owner_id, command_id, kind, validate, explicit=False: (
        checked.append((command_id, explicit)) or {"command_id": command_id, "settled": True, "message": ""}))
    assert api.settle_item(ctx(), item_id=item)["entry"]["id"] == item
    assert [c for c in checked if c[1]] == [("owner-change", True)]
    package = facts.finish(facts.entry("plugin", "kit", "Kit", installed=True, lifecycle="installed"))
    monkeypatch.setattr(facts, "read", lambda item_id, validate=None: package)
    remote = plans.Context(owner_id="owner", mcp_owner_id="owner", validate=lambda: None, local_owner=False)
    with pytest.raises(ClientPlatformError, match="owner_local_only"):
        api.settle_item(remote, item_id="plugin:kit")


def test_a_package_cannot_choose_what_runs_without_asking(monkeypatch):
    from types import SimpleNamespace
    from row_bot.plugins import mcp as plugin_mcp, registry, state
    entry = {"id": "notes", "url": "https://example.test/mcp", "tools": {
        "enabled": {"update_note": True}, "require_approval": ["read_note"], "run_without_asking": ["update_note"],
        "catalog": {"update_note": {"effect": "read_only", "destructive": False}}, "accepted_names": ["update_note"]}}
    manifest = SimpleNamespace(id="kit", name="Kit", path="", provides=SimpleNamespace(mcp_servers=[entry]))
    monkeypatch.setattr(registry, "get_loaded_manifests", lambda: [manifest])
    monkeypatch.setattr(state, "is_plugin_enabled", lambda plugin_id: True)
    monkeypatch.setattr(state, "get_mcp_child_overrides", lambda plugin_id, server_id: {})  # The user chose nothing yet.
    (name, cfg), = plugin_mcp.plugin_mcp_servers().items()
    assert not {"run_without_asking", "catalog", "accepted_names"} & set(cfg["tools"])
    assert cfg["tools"]["require_approval"] == ["read_note"]
    found = mcp_runtime._normalize_tools(name, cfg, [{"name": "update_note", "description": "", "inputSchema": {}}])
    assert found["update_note"].requires_approval is True  # A routine change still asks until the user allows it.


def zipped(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_picked_files_are_recognised_privately_and_never_run(tmp_path, monkeypatch, owner):
    monkeypatch.setattr(uploads, "_root", lambda: tmp_path / "uploads")
    skill = uploads.stage(zipped({"pdf-helper/SKILL.md": b"---\nname: pdf-helper\ndescription: Fill PDFs\n---\nSteps",
                                  "pdf-helper/run.sh": b"echo never"}), "pdf.skill")
    assert (skill["kind"], skill["name"]) == ("skill", "pdf-helper")
    files, folder = uploads.skill_files(skill["upload"])
    assert folder == "pdf-helper" and sorted(name for name, _ in files) == ["SKILL.md", "run.sh"]
    assert uploads.stage(zipped({"tools/plugin.json": b"{}"}), "tools.zip")["kind"] == "plugin"
    for bad in ({"../evil/SKILL.md": b"x"}, {"c:evil/SKILL.md": b"x"}):
        with pytest.raises(ValueError, match="unsafe_upload"):
            uploads.stage(zipped(bad), "bad.zip")
    with pytest.raises(ValueError, match="unsupported_upload"):
        uploads.stage(zipped({"readme.txt": b"x"}), "notes.zip")
    with pytest.raises(ValueError, match="invalid_upload"):
        uploads.stage(b"MZ", "setup.exe")
    with pytest.raises(ValueError, match="invalid_upload"):
        uploads.stage(b"not an archive", "broken.zip")
    assert len(list((tmp_path / "uploads").iterdir())) == 2  # A refused file is never kept.
    for index in range(uploads.MAX_KEPT + 3):
        uploads.stage(zipped({"tools/plugin.json": b"{}"}), f"tools{index}.zip")
    assert len(list((tmp_path / "uploads").iterdir())) == uploads.MAX_KEPT
    from row_bot.application.client_platform import ClientPlatformError
    with pytest.raises(ClientPlatformError, match="invalid_upload"):  # A bundle is checked when it is picked.
        api.upload_file(owner_id="owner", data=b"bundle", filename="tool.mcpb")


@pytest.mark.parametrize(("link", "kind"), [
    ("https://mcp.example.com/mcp", "mcp"),
    ("https://github.com/example/tools", "plugin"),
    ("https://github.com/example/library/tree/main/skills/pdf", "skill"),
    ("https://clawhub.ai/example/pdf", "skill"),
])
def test_a_pasted_link_is_recognised_locally_and_only_https_is_accepted(owner, link, kind):
    page = api.resolve_reference(owner_id="owner", reference=link)
    assert page["items"][0]["kind"] == kind and owner.calls == []
    _detail, plan = api.read_item(owner_id="owner", item_id=page["items"][0]["id"], revision=page["revision"])
    assert plan["steps"][0]["type"] == "consent" and plan["supported"]
    for bad in ("http://mcp.example.com/mcp", "https://user:secret@mcp.example.com/mcp", "file:///etc/passwd",
                "https://mcp.example.com/mcp?api_key=secret"):
        with pytest.raises(ClientPlatformError, match="integration_link_unsupported"):
            api.resolve_reference(owner_id="owner", reference=bad)


def test_a_skill_or_package_link_keeps_only_its_address(owner):
    page = api.resolve_reference(owner_id="owner", reference="https://github.com/example/tools?tab=readme#install")
    detail, _plan = api.read_item(owner_id="owner", item_id=page["items"][0]["id"], revision=page["revision"])
    assert detail["about"]["source_url"] == "https://github.com/example/tools"


def test_connecting_a_catalog_entry_saves_it_at_consent_and_reports_the_installed_item(owner, monkeypatch):
    from row_bot.mcp_client import auth as auth_module
    monkeypatch.setattr(auth_module, "discover_sign_in", lambda url: {"required": False, "metadata": True, "dcr": True})
    page = api.resolve_reference(owner_id="owner", reference="https://mcp.example.com/mcp")
    item_id = page["items"][0]["id"]
    _, plan = api.read_item(owner_id="owner", item_id=item_id, revision=page["revision"])
    plan_id = str(uuid4())
    paused = api.start_plan(ctx(), plan_id=plan_id, item_id=item_id, revision=page["revision"], digest=plan["digest"])
    assert (paused["state"], paused["pause"]) == ("paused", "access"), paused
    assert paused["steps"][0]["state"] == "done" and paused["installed_id"].startswith("mcp:")
    saved_name = next(iter(n for n in config.read_saved_configuration().document["servers"] if n != "Synthetic"))
    assert config.read_saved_configuration().document["servers"][saved_name]["enabled"] is False
