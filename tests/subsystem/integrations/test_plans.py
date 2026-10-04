"""Install plans and the server-side runner, over the existing owners with fakes only."""
# ruff: noqa: F811 -- shared isolated fixtures
import json
from uuid import uuid4

import pytest

from row_bot import secret_store
from row_bot.api.v1 import schemas as dto
from row_bot.application import client_integrations as api
from row_bot.integrations import facts, plans, presets
from row_bot.mcp_client import config, marketplace
from row_bot.runtime import admissions
from tests.helpers.registry import search_catalog
from tests.subsystem.mcp.test_capability_catalog_controls import owner  # noqa: F401
from tests.subsystem.plugins.conftest import MemoryKeyring

pytestmark = [pytest.mark.platform, pytest.mark.mcp_transport]


@pytest.fixture
def item(owner, monkeypatch):
    secret_store._set_backend_for_tests(MemoryKeyring())
    facts.invalidate()
    row = next(row for row in facts.inventory()[0] if row["name"] == "Synthetic")
    yield row["id"]
    secret_store._set_backend_for_tests(None)


def context(**fields):
    return plans.Context(owner_id="owner", mcp_owner_id="owner", validate=lambda: None, **fields)


def review(item_id, **fields):
    detail, plan = api.read_item(owner_id="owner", item_id=item_id, **fields)
    return detail, plan


def saved_tools():
    return config.read_saved_configuration().document["servers"]["Synthetic"].get("tools", {})


def test_every_curated_recipe_has_a_valid_plan_and_only_supported_ones_can_start():
    supported = set()
    for entry in marketplace.CURATED_STARTER_CATALOG:
        detail, plan = review("mcp:curated:" + entry.id)
        dto.IntegrationDetail.model_validate_json(json.dumps(detail))
        assert plan["steps"][0]["type"] == "consent" and plan["steps"][-1]["type"] == "enable"
        assert plan["supported"] == (not plan["unsupported_reason"])
        if plan["supported"]:
            supported.add(entry.id)
    assert {"makenotion-notion-mcp-server", "linear-mcp", "upstash-context7", "microsoftdocs-mcp"} <= supported
    github = review("mcp:curated:github-github-mcp-server")[1]
    assert not github["supported"]
    assert {s["type"]: s["state"] for s in github["steps"]}["inputs"] == "unsupported"
    notion = review("mcp:curated:makenotion-notion-mcp-server")[1]
    assert [s["type"] for s in notion["steps"]] == ["consent", "sign_in", "test", "access", "enable"]
    assert notion["consent"]["destinations"] == ["https://mcp.notion.com/mcp"] and notion["consent"]["access_preset"] == "ask"
    assert review("mcp:curated:makenotion-notion-mcp-server")[1]["digest"] == notion["digest"]


def test_later_phase_steps_are_in_the_contract_as_unsupported():
    from row_bot.integrations import apps
    row = facts.finish(facts.entry("mcp", "x", "Blender", installed=False, lifecycle="available",
                                   app=apps.catalog()[0]["blender"].ref()))
    reference = {"cfg": {"transport": "stdio", "command": "uv", "args": ["tool", "run", "blender-mcp"]}}
    plan = plans.compute(row, reference)
    states = {s["type"]: s["state"] for s in plan["steps"]}
    assert states["runtime"] == states["local_app_check"] == "unsupported" and not plan["supported"]
    with pytest.raises(plans.PlanError, match="plan_unsupported"):
        plans.start(context(), row, reference, digest=plan["digest"])


def test_nothing_runs_without_the_consented_digest(item, owner):
    before = config.CONFIG_PATH.read_bytes()
    _, plan = review(item)
    with pytest.raises(plans.PlanError, match="plan_changed"):
        api.start_plan(context(), plan_id=str(uuid4()), item_id=item, digest="0" * 64)
    assert config.CONFIG_PATH.read_bytes() == before and owner.calls == []


@pytest.mark.parametrize(("preset", "expected"), [
    ("ask", {"get_record": (True, False), "delete_record": (True, True), "unrecognized": (True, True)}),
    ("read_only", {"get_record": (True, False), "delete_record": (False, None), "unrecognized": (False, None)}),
])
def test_runner_tests_then_pauses_for_access_then_enables_with_the_preset(item, owner, preset, expected):
    _, plan = review(item)
    assert plan["intent"] == "fix" and [s["type"] for s in plan["steps"]] == ["consent", "test", "access", "enable"]
    plan_id = str(uuid4())
    paused = api.start_plan(context(), plan_id=plan_id, item_id=item, digest=plan["digest"], preset=preset)
    assert (paused["state"], paused["pause"]) == ("paused", "access")
    assert "catalog" not in saved_tools(), "tools are not accepted before access is confirmed"
    access = next(s for s in paused["steps"] if s["type"] == "access")["access"]
    assert {t["name"] for t in access["tools"]} == set(expected)
    with_stale_tools = plans.resume(context(tools_digest="0" * 64), plan_id)
    assert with_stale_tools["pause"] == "access" and "changed" in with_stale_tools["steps"][2]["message"]
    done = plans.resume(context(tools_digest=access["tools_digest"]), plan_id)
    assert done["state"] == "completed", done
    tools = saved_tools()
    for name, (enabled, ask) in expected.items():
        assert tools["enabled"][name] is enabled
        if ask is not None:
            assert (name in tools["require_approval"]) is ask
    assert presets.current(tools) == preset
    assert config.read_saved_configuration().document["enabled"] is True
    dto.InstallPlan.model_validate_json(json.dumps(done))


def test_cancel_keeps_finished_steps_and_reads_never_send_commands(item, owner):
    _, plan = review(item)
    plan_id = str(uuid4())
    api.start_plan(context(), plan_id=plan_id, item_id=item, digest=plan["digest"])
    calls = list(owner.calls)
    assert plans.read_plan(context(), plan_id)["pause"] == "access"
    assert owner.calls == calls
    cancelled = plans.cancel(context(), plan_id)
    assert cancelled["state"] == "cancelled" and cancelled["next_action"]["kind"] == "none"
    assert config.read_saved_configuration().document["servers"]["Synthetic"]["enabled"] is False
    with pytest.raises(plans.PlanError, match="plan_not_resumable"):
        plans.resume(context(), plan_id)
    assert api.start_plan(context(), plan_id=plan_id, item_id=item, digest=plan["digest"])["state"] == "cancelled"


def test_unconfirmed_owner_change_is_reconciled_on_read_and_never_repeated(item, owner, monkeypatch):
    _, plan = review(item)
    plan_id = str(uuid4())
    access = next(s for s in api.start_plan(context(), plan_id=plan_id, item_id=item, digest=plan["digest"])["steps"]
                  if s["type"] == "access")["access"]
    persist = admissions.command_progress

    def lose_completion(owner_id, key, result):
        if result.get("mcp_configuration", {}).get("status") == "saved":
            raise OSError("lost response")
        return persist(owner_id, key, result)
    monkeypatch.setattr(admissions, "command_progress", lose_completion)
    uncertain = plans.resume(context(tools_digest=access["tools_digest"]), plan_id)
    assert uncertain["state"] == "uncertain" and uncertain["next_action"]["kind"] == "retry"
    monkeypatch.setattr(admissions, "command_progress", persist)
    published = []
    original = config.publish_saved_configuration
    monkeypatch.setattr(config, "publish_saved_configuration", lambda *a, **k: published.append(1) or original(*a, **k))
    assert plans.read_plan(context(), plan_id)["pause"] == "resume"
    done = plans.resume(context(tools_digest=access["tools_digest"]), plan_id)
    assert done["state"] == "completed", done
    assert "get_record" in saved_tools()["catalog"]
    assert len(published) == 2, "the accepted catalog was settled, not saved again; only the switches were published"


def test_key_input_pauses_and_the_secret_stays_in_the_keychain(item, owner):
    document = json.loads(config.CONFIG_PATH.read_text())
    document["servers"]["Synthetic"].update(source={"auth_mode": "api_key", "auth_bindings": [
        {"kind": "env", "name": "SYNTHETIC_TOKEN", "key": "token"}]})
    config.CONFIG_PATH.write_text(json.dumps(document))
    facts.invalidate()
    _, plan = review(item)
    assert [s["type"] for s in plan["steps"]][:2] == ["consent", "inputs"]
    plan_id = str(uuid4())
    paused = api.start_plan(context(), plan_id=plan_id, item_id=item, digest=plan["digest"])
    assert paused["pause"] == "inputs" and paused["next_action"]["kind"] == "add_key"
    resumed = plans.resume(context(inputs={"token": "fixture-private-key"}), plan_id)
    assert resumed["pause"] == "access"
    stored = json.dumps(admissions.receipt("owner", plan_id)) + config.CONFIG_PATH.read_text() + json.dumps(resumed)
    assert "fixture-private-key" not in stored
    assert config.read_saved_configuration().document["servers"]["Synthetic"]["auth"]["credential_ref"]


def test_browser_sign_in_pauses_and_resumes_after_the_callback(item, owner, monkeypatch):
    from row_bot.application import client_mcp_auth
    document = json.loads(config.CONFIG_PATH.read_text())
    document["servers"]["Synthetic"] = {"transport": "streamable_http", "url": "https://example.test/mcp", "enabled": False,
                                        "source": {"auth_mode": "oauth"}}
    config.CONFIG_PATH.write_text(json.dumps(document))
    facts.invalidate()
    state = {"value": "waiting"}

    def start_sign_in(**k):  # Admitted like the owner's own sign-in command.
        admissions.claim_command(k["owner_id"], k["command_id"], {"command_id": k["command_id"], "type": "mcp.auth.start"}, "fixture")
        return {"state": "starting", "authorization_url": None}
    monkeypatch.setattr(client_mcp_auth, "execute_auth", start_sign_in)
    monkeypatch.setattr(client_mcp_auth, "auth_status", lambda **k: {"state": state["value"], "message": "",
        "authorization_url": "https://auth.example.test/authorize" if state["value"] == "waiting" else None})
    _, plan = review(item)
    plan_id = str(uuid4())
    paused = api.start_plan(context(redirect_uri="http://127.0.0.1:1/api/v1/settings/mcp/auth/callback"), plan_id=plan_id,
                            item_id=item, digest=plan["digest"])
    assert (paused["pause"], paused["next_action"]["kind"]) == ("sign_in", "none")
    read = plans.read_plan(context(), plan_id)
    assert read["steps"][1]["sign_in"]["authorization_url"] == "https://auth.example.test/authorize"
    state["value"] = "signed_in"
    assert plans.read_plan(context(), plan_id)["pause"] == "resume"


def test_package_adds_need_this_computer_and_presets_never_unlock_risky_tools():
    row = facts.finish(facts.entry("plugin", "hermes:x", "X", installed=False, lifecycle="available"))
    plan = plans.compute(row, {"kind": "plugin", "reference": "hermes:x"})
    with pytest.raises(plans.PlanError, match="owner_local_only"):
        plans.start(context(local_owner=False), row, {"kind": "plugin", "reference": "hermes:x"}, digest=plan["digest"])
    for effect in ("mutation", "interaction", "unknown"):
        assert presets.tool_state("full", {"effect": effect, "destructive": True}) == "ask"
    assert presets.tool_state("full", {"effect": "unknown"}) == "ask"
    assert presets.tool_state("full", {"effect": "mutation"}) == "use"
    assert presets.tool_state("read_only", {"effect": "read_only", "requires_approval": True}) == "off"


def test_switching_a_tool_off_keeps_its_approval_for_when_it_is_switched_back_on():
    tools = {"catalog": {"click": {"effect": "interaction"}}, "enabled": {"click": True}, "require_approval": ["click"]}
    presets.apply(tools, "read_only")
    assert tools["enabled"]["click"] is False and tools["require_approval"] == ["click"]
    presets.apply(tools, "full")
    assert tools["enabled"]["click"] is True and tools["require_approval"] == []


def test_changing_access_later_applies_a_preset_without_retesting_or_turning_anything_on(item, owner):
    _, plan = review(item)
    plan_id = str(uuid4())
    paused = api.start_plan(context(), plan_id=plan_id, item_id=item, digest=plan["digest"])
    digest = next(s for s in paused["steps"] if s["type"] == "access")["access"]["tools_digest"]
    assert plans.resume(context(tools_digest=digest), plan_id)["state"] == "completed"
    document = json.loads(config.CONFIG_PATH.read_text())
    document["servers"]["Synthetic"]["enabled"] = False
    config.CONFIG_PATH.write_text(json.dumps(document))
    facts.invalidate()
    calls = list(owner.calls)
    _, change = review(item, intent="access")
    assert [(s["type"], s["state"]) for s in change["steps"]] == [
        ("consent", "pending"), ("test", "done"), ("access", "pending"), ("enable", "pending")]
    assert change["consent"]["turns_on_mcp"] is False
    second = str(uuid4())
    paused = api.start_plan(context(), plan_id=second, item_id=item, intent="access", digest=change["digest"],
                            preset="read_only")
    access = next(s for s in paused["steps"] if s["type"] == "access")["access"]
    assert {t["name"]: t["state"] for t in access["tools"]} == {"delete_record": "off", "get_record": "use", "unrecognized": "off"}
    assert plans.resume(context(tools_digest=access["tools_digest"]), second)["state"] == "completed"
    assert presets.current(saved_tools()) == "read_only"
    assert saved_tools()["enabled"] == {"get_record": True, "delete_record": False, "unrecognized": False}
    assert "list_tools" not in owner.calls[len(calls):], "changing access does not run the server's discovery again"
    assert config.read_saved_configuration().document["servers"]["Synthetic"]["enabled"] is False


def test_turning_on_mcp_is_part_of_the_consent(item, owner):
    document = json.loads(config.CONFIG_PATH.read_text())
    document["enabled"] = False
    config.CONFIG_PATH.write_text(json.dumps(document))
    facts.invalidate()
    _, off = review(item)
    assert off["consent"]["turns_on_mcp"] is True
    document["enabled"] = True
    document["servers"]["Synthetic"].update(source={"auth_mode": "api_key", "auth_bindings": [
        {"kind": "env", "name": "SYNTHETIC_TOKEN", "key": "token"}]})
    config.CONFIG_PATH.write_text(json.dumps(document))
    facts.invalidate()
    _, on = review(item)
    assert on["consent"]["turns_on_mcp"] is False and on["digest"] != off["digest"]
    plan_id = str(uuid4())
    assert api.start_plan(context(), plan_id=plan_id, item_id=item, digest=on["digest"])["pause"] == "inputs"
    document = json.loads(config.CONFIG_PATH.read_text())
    document["enabled"] = False  # Switched off while the plan waits: turning it back on was never agreed.
    config.CONFIG_PATH.write_text(json.dumps(document))
    paused = plans.resume(context(inputs={"token": "fixture-private-key"}), plan_id)
    digest = next(s for s in paused["steps"] if s["type"] == "access")["access"]["tools_digest"]
    failed = plans.resume(context(tools_digest=digest), plan_id)
    assert failed["state"] == "failed" and failed["message"] == plans._MESSAGES["plan_changed"]
    assert config.read_saved_configuration().document["enabled"] is False and "catalog" not in saved_tools()


def test_a_paused_plan_never_resumes_on_a_changed_launch_recipe(item, owner):
    document = json.loads(config.CONFIG_PATH.read_text())
    document["servers"]["Synthetic"].update(source={"auth_mode": "api_key", "auth_bindings": [
        {"kind": "env", "name": "SYNTHETIC_TOKEN", "key": "token"}]})
    config.CONFIG_PATH.write_text(json.dumps(document))
    facts.invalidate()
    _, plan = review(item)
    plan_id = str(uuid4())
    assert api.start_plan(context(), plan_id=plan_id, item_id=item, digest=plan["digest"])["pause"] == "inputs"
    document["servers"]["Synthetic"]["args"] = [*document["servers"]["Synthetic"].get("args", []), "--other"]
    config.CONFIG_PATH.write_text(json.dumps(document))
    failed = plans.resume(context(inputs={"token": "fixture-private-key"}), plan_id)
    assert failed["state"] == "failed" and failed["message"] == plans._MESSAGES["plan_changed"]
    assert owner.calls == [] and "auth" not in config.read_saved_configuration().document["servers"]["Synthetic"]


def test_the_access_review_binds_the_tools_and_the_click_carries_the_preset(item, owner):
    _, plan = review(item)
    plan_id = str(uuid4())
    paused = api.start_plan(context(), plan_id=plan_id, item_id=item, digest=plan["digest"], preset="ask")
    asked = next(s for s in paused["steps"] if s["type"] == "access")["access"]
    stale = plans.resume(context(tools_digest="0" * 64), plan_id, preset="read_only")
    access = next(s for s in stale["steps"] if s["type"] == "access")["access"]
    assert stale["pause"] == "access" and access["preset"] == "read_only"
    assert access["tools_digest"] == asked["tools_digest"], "the review binds the tools, not the preset"
    assert "catalog" not in saved_tools(), "tools that were not the ones reviewed are never saved"
    done = plans.resume(context(tools_digest=asked["tools_digest"]), plan_id, preset="read_only")
    assert done["state"] == "completed", done
    assert presets.current(saved_tools()) == "read_only", "the preset sent with the click is the one saved"
    assert saved_tools()["enabled"] == {"get_record": True, "delete_record": False, "unrecognized": False}


def test_a_cancel_that_lands_while_a_continue_starts_wins(item, owner, monkeypatch):
    _, plan = review(item)
    plan_id = str(uuid4())
    paused = api.start_plan(context(), plan_id=plan_id, item_id=item, digest=plan["digest"])
    digest = next(s for s in paused["steps"] if s["type"] == "access")["access"]["tools_digest"]
    load, raced = plans._load, []

    def racing(owner_id, key):
        value = load(owner_id, key)
        if not raced:
            raced.append(None)
            raced[0] = plans.cancel(context(), key)
        return value
    monkeypatch.setattr(plans, "_load", racing)
    calls = list(owner.calls)
    with pytest.raises(plans.PlanError, match="plan_not_resumable"):
        plans.resume(context(tools_digest=digest), plan_id)
    assert raced[0]["state"] == "cancelled" and owner.calls == calls and "catalog" not in saved_tools()


def test_another_owners_unfinished_plan_never_blocks_this_one(item, owner):
    _, plan = review(item)
    api.start_plan(context(), plan_id=str(uuid4()), item_id=item, digest=plan["digest"])
    device = plans.Context(owner_id="device", mcp_owner_id="owner", validate=lambda: None)
    detail, fresh = api.read_item(owner_id="device", item_id=item, context=device)
    assert detail["plan"]["plan_id"] is None, "another owner's plan is neither shown nor holding the item"
    assert api.start_plan(device, plan_id=str(uuid4()), item_id=item, digest=fresh["digest"])["pause"] == "access"


class FakeRuntimes:
    """The managed runtime installer's surface: reviewed, background, receipt-observed."""

    def __init__(self):
        self.started, self.status = [], {}

    def review(self, *, owner_id, runtime_id, operation, resource_revision, source_command_id, validate, read_policy):
        from types import SimpleNamespace
        return SimpleNamespace(runtime_id=runtime_id, operation=operation, resource_revision=resource_revision,
                               source_command_id=source_command_id, action_digest=operation + "-digest")

    def execute(self, command, *, owner_id, key, validate, read_policy, validate_review):
        validate_review(self.review(owner_id=owner_id, runtime_id="node", operation=command["type"].rsplit(".", 1)[1],
            resource_revision=command["payload"]["resource_revision"], source_command_id=command["payload"]["source_command_id"],
            validate=validate, read_policy=read_policy))
        self.started.append(command["type"])
        self.status[key] = "accepted"
        return {"command_id": key, "status": "accepted"}

    def receipt(self, *, owner_id, runtime_id, command_id, validate):
        return {"command_id": command_id, "status": self.status[command_id], "installation": {"quiesced": False}}


def test_node_and_package_preparation_run_in_the_background_and_resume(item, owner, monkeypatch):
    from row_bot.application import mcp_runtime_installation as installation
    from row_bot.mcp_client import requirements
    document = json.loads(config.CONFIG_PATH.read_text())
    document["servers"]["Synthetic"].update(command="npx", args=["-y", "fixture-mcp@1.0.0"])
    config.CONFIG_PATH.write_text(json.dumps(document))
    facts.invalidate()
    monkeypatch.setattr(facts, "_requirements", lambda cfg: [{"id": "node", "label": "Node.js", "available": False,
        "managed": True, "installable": True, "source": "missing"}])
    monkeypatch.setattr(requirements, "runtime_install_revision", lambda runtime: "r" * 64)
    prepared = []
    monkeypatch.setattr(installation, "inspect_mcp_package", lambda **k: {"preview_id": "p", "digest": "d", "action_digest": "a"})

    def prepare(**k):
        k["validate_review"]({"action_digest": "a"})
        prepared.append(k["command_id"])
        return {"status": "completed"}
    monkeypatch.setattr(installation, "prepare_mcp_package", prepare)
    _, plan = review(item)
    assert [s["runtime"]["id"] for s in plan["steps"] if s["type"] == "runtime"] == ["node", "npm_package"]
    assert plan["consent"]["downloads"] == ["Node.js", "npm package"]
    runtimes, plan_id = FakeRuntimes(), str(uuid4())
    remote = context(runtimes=runtimes, read_policy=lambda operation: {})
    with pytest.raises(plans.PlanError, match="owner_local_only"):
        api.start_plan(remote, plan_id=str(uuid4()), item_id=item, digest=plan["digest"])
    assert runtimes.started == [] and prepared == [], "packages are prepared only from this computer"
    ctx = context(runtimes=runtimes, read_policy=lambda operation: {}, local_owner=True)
    running = api.start_plan(ctx, plan_id=plan_id, item_id=item, digest=plan["digest"])
    assert running["state"] == "running" and runtimes.started == ["mcp.runtime.resolve"]
    assert plans.read_plan(ctx, plan_id)["state"] == "running", "a read never starts the next stage"
    runtimes.status = {key: "completed" for key in runtimes.status}
    assert plans.read_plan(ctx, plan_id)["pause"] == "resume"
    assert plans.resume(ctx, plan_id)["state"] == "running" and runtimes.started[-1] == "mcp.runtime.install"
    runtimes.status = {key: "completed" for key in runtimes.status}
    monkeypatch.setattr(facts, "_requirements", lambda cfg: [])
    paused = plans.resume(ctx, plan_id)
    assert paused["pause"] == "access" and len(prepared) == 1
    assert runtimes.started == ["mcp.runtime.resolve", "mcp.runtime.install"]


def test_adding_a_skill_checks_it_then_adds_it_turned_on(tmp_path, monkeypatch, reload_for_data_dir):
    from row_bot.application import client_skill_hub as hub
    from row_bot.skills_hub.installer import InstallResult
    from tests.subsystem.client_protocol.test_skill_hub_api import _fake_catalog
    reload_for_data_dir(tmp_path, "row_bot.tasks")
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    _fake_catalog(monkeypatch)
    installed = []
    monkeypatch.setattr(hub.installer, "install_bundle", lambda bundle, *, enabled: installed.append(enabled)
                        or InstallResult(True, "Skill installed.", skill_name="sample"))
    page = search_catalog(sources=["clawhub"], query="sample", refresh=True)
    item_id = page["items"][0]["id"]
    _, plan = review(item_id, revision=page["revision"])
    assert plan["intent"] == "add" and [s["type"] for s in plan["steps"]] == ["consent", "test", "enable"]
    with pytest.raises(ValueError, match="not_found"):
        review(item_id)  # Without this owner's search, a public listing is not resolvable.
    done = api.start_plan(context(), plan_id=str(uuid4()), item_id=item_id, revision=page["revision"], digest=plan["digest"])
    assert done["state"] == "completed", done
    assert installed == [True], "Add installs the checked bundle once, turned on"


def test_a_sign_in_that_never_started_fails_cleanly_and_frees_the_item(item, owner, monkeypatch):
    from row_bot.application import client_mcp_auth
    from row_bot.mcp_client.auth import McpAuthError
    document = json.loads(config.CONFIG_PATH.read_text())
    document["servers"]["Synthetic"] = {"transport": "streamable_http", "url": "https://example.test/mcp", "enabled": False,
                                        "source": {"auth_mode": "oauth"}}
    config.CONFIG_PATH.write_text(json.dumps(document))
    facts.invalidate()

    def refuse(**_):
        raise McpAuthError("mcp_auth_busy")
    monkeypatch.setattr(client_mcp_auth, "execute_auth", refuse)
    _, plan = review(item)
    failed = api.start_plan(context(redirect_uri="http://127.0.0.1:1/cb"), plan_id=str(uuid4()), item_id=item, digest=plan["digest"])
    assert failed["state"] == "failed" and failed["next_action"]["kind"] == "retry"
    assert review(item)[0]["plan"]["plan_id"] is None, "a failed plan no longer holds the item"


def test_an_unfinished_plan_is_returned_with_the_item_and_reconciles_through_facts(item, owner):
    _, plan = review(item)
    plan_id = str(uuid4())
    api.start_plan(context(), plan_id=plan_id, item_id=item, digest=plan["digest"])
    detail, consent = review(item)
    assert detail["plan"]["plan_id"] == plan_id and detail["plan"]["pause"] == "access" and consent is None
    assert facts.reconcile_command("owner", plan_id, "mcp", lambda: None) == {"command_id": plan_id, "settled": True, "message": ""}


def test_servers_needing_manual_tool_choice_keep_every_tool_off(item, owner):
    document = json.loads(config.CONFIG_PATH.read_text())
    document["servers"]["Synthetic"]["source"] = {"risk_level": "high"}
    config.CONFIG_PATH.write_text(json.dumps(document))
    facts.invalidate()
    _, plan = review(item)
    plan_id = str(uuid4())
    paused = api.start_plan(context(), plan_id=plan_id, item_id=item, digest=plan["digest"], preset="full")
    access = next(s for s in paused["steps"] if s["type"] == "access")
    assert {t["state"] for t in access["access"]["tools"]} == {"off"} and "choose" in access["message"]
    assert plans.resume(context(tools_digest=access["access"]["tools_digest"]), plan_id)["state"] == "completed"
    assert not any(saved_tools()["enabled"].values())
    facts.invalidate()
    _, change = review(item, intent="access")
    assert not change["supported"] and "one by one" in change["unsupported_reason"]
    from row_bot.application import capability_policy_controls as policy
    from row_bot.application.capability_configuration_controls import read_mcp_configuration
    revision = read_mcp_configuration(validate=lambda: None).revision
    server_id = next(row for row in facts.inventory()[0] if row["id"] == item)["owner_ref"]
    with pytest.raises(ValueError, match="mcp_policy_unavailable"):
        policy.review_mcp_policy_command(revision, {"operation": "preset", "server_id": server_id, "preset": "full"},
                                         validate=lambda: None)
    assert not any(saved_tools()["enabled"].values())


def test_blank_values_in_an_installed_configuration_are_not_missing_settings():
    row = facts.finish(facts.entry("mcp", "x", "Local", lifecycle="off"))
    plan = plans.compute(row, {"cfg": {"transport": "stdio", "command": "node", "args": ["s.js"], "env": {"DEBUG": ""},
                                       "tools": {"catalog": {}}}}, intent="turn_on")
    assert plan["supported"] and "inputs" not in [s["type"] for s in plan["steps"]]
