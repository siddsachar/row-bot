"""Phase 3 behaviour: the background runner, expiry, Turn off and Remove, picked files and links,
and access that only an explicit Full access (or one tool's "Use") widens. Fakes only."""
# ruff: noqa: F811 -- shared isolated fixtures
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
    ("delete_record", None, "mutation", True),
    ("send_message", None, "mutation", True),
    ("add_comment", None, "mutation", True),  # Reaches other people.
    ("upload_file", None, "mutation", True),  # Moves local data out.
    ("lookup", {"destructiveHint": True}, "mutation", True),
    ("frobnicate", None, "unknown", False),
    ("frobnicate", {"readOnlyHint": False, "destructiveHint": False}, "unknown", False),  # Hints never relax.
    ("browser_close", None, "interaction", False),
])
def test_classification_separates_routine_changes_from_high_impact_ones(name, annotations, effect, high_impact):
    tool = {"annotations": annotations} if annotations else None
    assert safety.classify_tool_effect(name, "", tool) == effect
    assert safety.is_destructive_tool(name, "", tool) is high_impact


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
    done = api.start_plan(ctx(), plan_id=str(uuid4()), item_id=item, intent="turn_off", digest=plan["digest"])
    assert (done["state"], done["message"]) == ("completed", "Turned off.") and saved()["enabled"] is False
    _, keep = api.read_item(owner_id="owner", item_id=item, intent="remove")
    _, clean = api.read_item(owner_id="owner", item_id=item, intent="remove", cleanup=True)
    assert keep["digest"] != clean["digest"] and (keep["consent"]["cleanup"], clean["consent"]["cleanup"]) == (False, True)
    with pytest.raises(plans.PlanError, match="plan_changed"):  # Agreeing to keep data never deletes it.
        api.start_plan(ctx(), plan_id=str(uuid4()), item_id=item, intent="remove", digest=keep["digest"], cleanup=True)
    removed = api.start_plan(ctx(), plan_id=str(uuid4()), item_id=item, intent="remove", digest=keep["digest"])
    assert removed["state"] == "completed" and "Synthetic" not in config.read_saved_configuration().document["servers"]


def zipped(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_picked_files_are_recognised_privately_and_never_run(tmp_path, monkeypatch, owner):
    monkeypatch.setattr(uploads, "_root", lambda: tmp_path)
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
    page = api.upload_file(owner_id="owner", data=b"bundle", filename="tool.mcpb")
    _detail, plan = api.read_item(owner_id="owner", item_id=page["items"][0]["id"], revision=page["revision"])
    assert not plan["supported"] and {s["type"]: s["state"] for s in plan["steps"]}["runtime"] == "unsupported"


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
    for bad in ("http://mcp.example.com/mcp", "https://user:secret@mcp.example.com/mcp", "file:///etc/passwd"):
        with pytest.raises(ClientPlatformError, match="integration_link_unsupported"):
            api.resolve_reference(owner_id="owner", reference=bad)


def test_connecting_a_catalog_entry_saves_it_at_consent_and_reports_the_installed_item(owner):
    page = api.resolve_reference(owner_id="owner", reference="https://mcp.example.com/mcp")
    item_id = page["items"][0]["id"]
    _, plan = api.read_item(owner_id="owner", item_id=item_id, revision=page["revision"])
    plan_id = str(uuid4())
    paused = api.start_plan(ctx(), plan_id=plan_id, item_id=item_id, revision=page["revision"], digest=plan["digest"])
    assert (paused["state"], paused["pause"]) == ("paused", "access"), paused
    assert paused["steps"][0]["state"] == "done" and paused["installed_id"].startswith("mcp:")
    saved_name = next(iter(n for n in config.read_saved_configuration().document["servers"] if n != "Synthetic"))
    assert config.read_saved_configuration().document["servers"][saved_name]["enabled"] is False
