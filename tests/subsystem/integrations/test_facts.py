"""Owner facts and status v2 over isolated owner stores and fakes."""
# ruff: noqa: F811 -- shared isolated fixtures
import json
from types import SimpleNamespace

import pytest

from row_bot.application import client_integrations as api
from row_bot.integrations import facts
from row_bot.mcp_client import config
from tests.subsystem.client_protocol.test_integrations_api import isolated  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401

pytestmark = pytest.mark.platform


def _server(**fields):
    document = json.loads(config.CONFIG_PATH.read_text())
    document["enabled"] = True
    document["servers"]["Work"].update(fields)
    config.CONFIG_PATH.write_text(json.dumps(document))


def _work():
    return next(row for row in facts.inventory()[0] if row["name"] == "Work")


@pytest.mark.parametrize(("lifecycle", "codes", "readiness", "action"), [
    ("installed", [], "ready", "try"),
    ("off", [], "ready", "turn_on"),
    ("available", [], None, "connect"),
    ("data_retained", [], None, "delete_data"),
    ("installed", ["tools_not_accepted", "connection_failed"], "attention", "fix"),
    ("off", ["missing_runtime", "tools_not_accepted"], "needs_runtime", "install_runtime"),
    ("installed", ["expired"], "needs_sign_in", "sign_in"),
    ("installed", ["key_required"], "needs_key", "add_key"),
    ("installed", ["diagnostic"], "ready", "try"),
    ("installed", ["configuration_recovery", "sign_in_required"], "attention", "retry"),
])
def test_status_has_one_next_action_from_the_first_blocking_reason(lifecycle, codes, readiness, action):
    value = facts.status("mcp", lifecycle, [facts.blocker(code) for code in codes])
    assert (value["lifecycle"], value["readiness"], value["next_action"]["kind"]) == (lifecycle, readiness, action)
    assert [b["code"] for b in value["blockers"]] == sorted(codes, key=lambda c: (c not in facts.BLOCKING, list(facts.BLOCKING).index(c) if c in facts.BLOCKING else 0))


def test_refused_saved_sign_in_is_expired_without_reading_the_keychain(isolated, monkeypatch):
    from row_bot import secret_store
    from row_bot.mcp_client import runtime
    monkeypatch.setattr(secret_store, "get_secret", lambda *a, **k: pytest.fail("a read touched the keychain"))
    _server(tools={"catalog": {}}, enabled=True, source={"auth_mode": "api_key"},
            auth={"mode": "api_key", "credential_ref": "a" * 32, "binding": "b" * 64, "label": "Work"})
    monkeypatch.setattr(runtime, "get_passive_server_statuses", lambda names: {"Work": {"status": "failed", "sign_in_failed": True}})
    row = _work()
    assert row["setup"]["credential_configured"] and row["blockers"][0]["code"] == "expired"
    assert row["readiness"] == "needs_sign_in"
    assert row["next_action"] == {"kind": "sign_in", "label": "Sign in again"}
    monkeypatch.setattr(runtime, "get_passive_server_statuses", lambda names: {"Work": {"status": "failed"}})
    assert _work()["blockers"][0]["code"] == "connection_failed"


def test_missing_runtime_is_emitted_from_live_requirements(isolated, monkeypatch):
    _server(transport="stdio", command="uvx", args=["mcp-server-fetch"], tools={"catalog": {}}, enabled=True,
            environment_mode="minimal")  # As a catalog recipe saves it; the person's own uvx line runs as written.
    monkeypatch.setattr(facts, "_requirements", lambda cfg: [{"id": "uv", "label": "uv", "available": False,
        "managed": True, "installable": True, "source": "missing"}])
    row = _work()
    assert row["blockers"][0]["code"] == "missing_runtime" and row["readiness"] == "needs_runtime"
    assert row["next_action"]["kind"] == "install_runtime"
    monkeypatch.setattr(facts, "_requirements", lambda cfg: [])
    assert _work()["blockers"][0]["code"] == "package_preparation"  # Its Python package is prepared before it runs.


def test_reads_reuse_the_index_until_an_owner_publishes(isolated, monkeypatch):
    from row_bot.application import plugin_commands
    calls = []
    original = plugin_commands.read_integration_packages
    monkeypatch.setattr(plugin_commands, "read_integration_packages", lambda **k: calls.append(1) or original(**k))
    page = api.read_items(owner_id="owner")
    for row in page["items"]:
        if row["kind"] != "builtin":  # Built-in ways are read from their owners, not this index.
            assert api.entry(facts.read(row["id"]))["id"] == row["id"]
    assert len(calls) == 1
    _server(enabled=True)
    api.read_items(owner_id="owner")
    assert len(calls) == 2


def test_more_than_a_thousand_skills_are_listed(isolated, monkeypatch):
    from row_bot import skills
    items = {f"skill-{i:04}": {"revision": "r", "skill": SimpleNamespace(name=f"skill-{i:04}", display_name=f"Skill {i:04}",
        description="", source="user", version="")} for i in range(1500)}
    monkeypatch.setattr(skills, "read_client_skills", lambda: {"items": items, "enabled": {}, "pinned": [], "revision": "r"})
    monkeypatch.setattr(skills, "is_tool_guide", lambda skill: False)
    page = api.read_items(owner_id="owner", kind="skill", limit=50)
    assert page["total"] == 1500 and not page["sources"]


def test_unfinished_change_is_reconciled_on_read_without_repeating_it(service, isolated, monkeypatch):
    from row_bot.runtime import admissions
    from tests.subsystem.client_protocol.test_mcp_configuration_api import client_for, review, send
    from tests.subsystem.client_protocol.test_protocol_security import bootstrap
    persist = admissions.command_progress

    def lose_completion(owner_id, key, result):
        if result.get("status") == "completed":
            raise OSError("lost response")
        return persist(owner_id, key, result)
    with client_for(service) as client:
        _, headers = bootstrap(client)
        command = review(client, headers)
        monkeypatch.setattr(admissions, "command_progress", lose_completion)
        assert send(client, headers, command).status_code >= 400
        monkeypatch.setattr(admissions, "command_progress", persist)
        monkeypatch.setattr(config, "publish_saved_configuration", lambda *a, **k: pytest.fail("repeated publication"))
        assert admissions.read_unfinished_target_commands("settings:mcp")["items"]
        rows = client.get("/api/v1/integrations/items?kind=mcp", headers=headers).json()["items"]
        assert rows and all(b["code"] != "configuration_recovery" for row in rows for b in row["blockers"])
        assert not admissions.read_unfinished_target_commands("settings:mcp")["items"]
        assert client.get("/api/v1/commands/" + command["command_id"], headers=headers).json()["status"] == "completed"


def test_other_subsystems_unfinished_commands_never_mark_integrations(isolated):
    from uuid import uuid4
    from row_bot.runtime import admissions
    for target in ("conversation:fixture", "conversation:fixture", "settings:mcp"):
        key = str(uuid4())
        admissions.claim_command("owner", key, {"command_id": key, "type": "fixture.change"}, target)
    found = admissions.read_unfinished_commands(prefixes=("settings:mcp",), limit=1)
    assert [c["target"] for c in found["items"]] == ["settings:mcp"] and not found["overflow"]
