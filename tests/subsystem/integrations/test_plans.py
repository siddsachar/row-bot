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
    monkeypatch.setattr(client_mcp_auth, "execute_auth", lambda **k: {"state": "starting", "authorization_url": None})
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


def test_changing_access_later_applies_a_preset_without_retesting(item, owner):
    _, plan = review(item)
    plan_id = str(uuid4())
    paused = api.start_plan(context(), plan_id=plan_id, item_id=item, digest=plan["digest"])
    digest = next(s for s in paused["steps"] if s["type"] == "access")["access"]["tools_digest"]
    assert plans.resume(context(tools_digest=digest), plan_id)["state"] == "completed"
    facts.invalidate()
    calls = list(owner.calls)
    _, change = review(item, intent="access")
    assert [(s["type"], s["state"]) for s in change["steps"]] == [
        ("consent", "pending"), ("test", "done"), ("access", "pending"), ("enable", "pending")]
    second = str(uuid4())
    paused = api.start_plan(context(), plan_id=second, item_id=item, intent="access", digest=change["digest"],
                            preset="read_only")
    access = next(s for s in paused["steps"] if s["type"] == "access")["access"]
    assert {t["name"]: t["state"] for t in access["tools"]} == {"delete_record": "off", "get_record": "use", "unrecognized": "off"}
    assert plans.resume(context(tools_digest=access["tools_digest"]), second)["state"] == "completed"
    assert presets.current(saved_tools()) == "read_only"
    assert saved_tools()["enabled"] == {"get_record": True, "delete_record": False, "unrecognized": False}
    assert "list_tools" not in owner.calls[len(calls):], "changing access does not run the server's discovery again"
