"""Runtimes in a plan: nothing runs before consent, the exact lock is reviewed in place before anything
is installed, and the installed package is bound to that lock. Faked tools and registry only."""
# ruff: noqa: F811 -- shared isolated fixtures
import json
from uuid import uuid4

import pytest

from row_bot.application import client_integrations as api
from row_bot.integrations import facts, plans
from row_bot.mcp_client import config, packages
from tests.subsystem.mcp.test_capability_catalog_controls import owner  # noqa: F401
from tests.subsystem.mcp.test_integration_packages import NPM, Tools, self_contained

pytestmark = [pytest.mark.platform, pytest.mark.mcp_transport]


def context(**fields):
    return plans.Context(owner_id="owner", mcp_owner_id="owner", validate=lambda: None, local_owner=True, **fields)


@pytest.fixture
def npm_server(owner, tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    (tmp_path / "mcp_packages").mkdir(exist_ok=True)
    document = json.loads(config.CONFIG_PATH.read_text())
    document["servers"]["Synthetic"] = {**NPM, "enabled": False}
    config.CONFIG_PATH.write_text(json.dumps(document))
    from row_bot.mcp_client import requirements
    monkeypatch.setattr(requirements, "check_requirement",
                        lambda requirement, env=None: requirements.RuntimeCheck(requirement=requirement, available=True))
    tools = Tools(monkeypatch, tmp_path)
    fetched = self_contained(monkeypatch)
    facts.invalidate()
    return tools, fetched


def item():
    return next(row for row in facts.inventory()[0] if row["name"] == "Synthetic")["id"]


def test_the_exact_lock_is_reviewed_before_anything_is_installed(npm_server):
    tools, fetched = npm_server
    _, plan = api.read_item(owner_id="owner", item_id=item())
    step = next(s for s in plan["steps"] if s["type"] == "runtime")
    assert step["runtime"]["id"] == "npm_package" and not fetched and not tools.calls  # Reading a plan contacts nothing.
    plan_id = str(uuid4())
    paused = api.start_plan(context(), plan_id=plan_id, item_id=item(), digest=plan["digest"])
    step = next(s for s in paused["steps"] if s["type"] == "runtime")
    assert paused["pause"] == "digest_changed" and step["state"] == "waiting"
    assert step["review"]["items"][0]["name"] == "fixture-mcp" and step["review"]["items"][0]["integrity"].startswith("sha512-")
    assert len(fetched) == 1 and not list((packages._folder("x").parent).glob("[0-9a-f]" * 32))  # Metadata only; nothing laid out.
    again = plans.resume(context(), plan_id)  # Continuing without what was shown changes nothing.
    assert again["pause"] == "digest_changed" and len(fetched) == 1
    done = plans.resume(context(review_digest=step["review"]["digest"]), plan_id)
    assert done["pause"] == "access", done
    launch = config.read_saved_configuration().document["servers"]["Synthetic"]["managed_launch"]
    assert launch["lock_digest"] == step["review"]["digest"] and launch["integrity"] == step["review"]["items"][0]["integrity"]
    assert not tools.calls  # A self-contained package never runs npm at all.


def test_a_container_app_needs_docker_on_this_computer(owner, monkeypatch):
    document = json.loads(config.CONFIG_PATH.read_text())
    document["servers"]["Synthetic"] = {"transport": "stdio", "command": "docker", "enabled": False,
                                        "args": ["run", "-i", "--rm", "ghcr.io/example/notes:1.0.0"]}
    config.CONFIG_PATH.write_text(json.dumps(document))
    monkeypatch.setattr("row_bot.mcp_client.requirements.shutil.which", lambda *a, **k: None)
    facts.invalidate()
    _, plan = api.read_item(owner_id="owner", item_id=item())
    assert not plan["supported"] and "Install Docker Desktop" in plan["unsupported_reason"]


def test_a_desktop_app_is_looked_for_only_on_this_computer(monkeypatch):
    import socket
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    reached = []
    original = socket.create_connection
    monkeypatch.setattr(socket, "create_connection", lambda address, **k: reached.append(address) or original(address, **k))
    try:
        assert plans.local_app_open({"port": port})
    finally:
        listener.close()
    assert not plans.local_app_open({"port": port})
    assert {host for host, _ in reached} == {"127.0.0.1"}


def test_a_package_for_a_desktop_app_waits_until_the_app_is_open(monkeypatch):
    from row_bot.integrations import apps
    row = facts.finish(facts.entry("plugin", "hermes:blender", "Blender", installed=False, lifecycle="available",
                                   app=apps.catalog()[0]["blender"].ref()))
    plan = plans.compute(row, {"kind": "plugin", "reference": "hermes:blender"})
    step = next(s for s in plan["steps"] if s["type"] == "local_app_check")
    assert step["state"] == "pending" and step["local_app"]["label"] == "Blender 5.1 with its MCP add-on"
    record = {**plan, "app_id": "blender"}
    monkeypatch.setattr(plans, "local_app_open", lambda check: check == {"port": 9876} and False)
    assert plans._local_app(context(), record, step) == "resume" and "check again" in step["message"]
    monkeypatch.setattr(plans, "local_app_open", lambda check: True)
    assert plans._local_app(context(), record, step) == "done"
