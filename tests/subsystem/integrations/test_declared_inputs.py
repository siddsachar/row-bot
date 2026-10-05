"""Declared inputs, one path for every source: what a connection needs from the person, kept as
templates in its configuration and filled in only when Row-Bot connects. Fakes only."""
# ruff: noqa: F811 -- shared isolated fixtures
import json
from uuid import uuid4

import pytest

from row_bot import secret_store
from row_bot.application import client_integrations as api
from row_bot.integrations import facts, inputs, plans
from row_bot.mcp_client import auth, config
from row_bot.runtime import admissions
from tests.subsystem.mcp.test_capability_catalog_controls import owner  # noqa: F401
from tests.subsystem.plugins.conftest import MemoryKeyring

pytestmark = [pytest.mark.platform, pytest.mark.mcp_transport]
SECRET = "synthetic-key-0123456789"


def declared(key, target, name, **fields):
    return inputs.declaration(key, target=target, name=name, **fields)


def test_templates_fill_at_use_and_optional_inputs_drop_what_they_carry():
    cfg = {"transport": "streamable_http", "url": "https://{tenant}.notes.example.test/v1/{space}/mcp",
           "headers": {"Authorization": "Bearer {api_key}", "X-Trace": "{trace}"},
           "args": ["--region", "{region}", "--verbose"], "env": {"LOG": "{level}"},
           "inputs": [declared("tenant", "url_variable", "tenant", required=True),
                      declared("space", "url_variable", "space", required=True),
                      declared("api_key", "header", "Authorization", secret=True, required=True),
                      declared("trace", "header", "X-Trace"), declared("region", "argument", "region", flag="--region"),
                      declared("level", "env", "LOG", default="info")],
           "input_values": {"tenant": "acme", "space": "a b"}}
    filled = inputs.resolve(cfg, {"api_key": SECRET})
    assert filled["url"] == "https://acme.notes.example.test/v1/a%20b/mcp"
    assert filled["headers"] == {"Authorization": "Bearer " + SECRET}  # An optional header left empty is not sent.
    assert filled["args"] == ["--verbose"] and filled["env"] == {"LOG": "info"}
    assert cfg["headers"]["Authorization"] == "Bearer {api_key}"  # The saved configuration keeps its template.
    with pytest.raises(inputs.InputError, match="mcp_inputs_required"):
        inputs.resolve(cfg, {})
    for value in ("acme.evil.test/", "x@evil.test", "evil.test:8443"):
        with pytest.raises(inputs.InputError):
            inputs.resolve({**cfg, "input_values": {"tenant": value, "space": "s"}}, {"api_key": SECRET})


@pytest.mark.parametrize(("template", "typed", "sent"), [
    ("{key}", "abc123", "Bearer abc123"), ("Bearer {key}", "Bearer abc123", "Bearer abc123"),
    ("{key}", "apikey abc123", "apikey abc123"), ("{key}", "Basic YWJj", "Basic YWJj")])
def test_a_typed_authorization_value_is_sent_with_one_scheme(template, typed, sent):
    cfg = {"headers": {"Authorization": template}, "inputs": [declared("key", "header", "Authorization", secret=True, required=True)]}
    assert inputs.resolve(cfg, {"key": typed})["headers"]["Authorization"] == sent


@pytest.mark.parametrize("url", ["https://{host}/mcp", "https://{tenant}.com/mcp", "https://x.{rest}/mcp", "http://{t}.example.test/mcp"])
def test_a_url_variable_can_never_choose_the_destination(url):
    with pytest.raises(inputs.InputError):
        inputs.check_url(url, [declared("host", "url_variable", "host"), declared("tenant", "url_variable", "tenant"),
                               declared("rest", "url_variable", "rest"), declared("t", "url_variable", "t")])


@pytest.mark.parametrize("name", ["PATH", "NODE_OPTIONS", "LD_PRELOAD", "PYTHONPATH", "DYLD_INSERT_LIBRARIES"])
def test_variables_that_change_what_runs_are_never_declared(name):
    with pytest.raises(inputs.InputError):
        inputs.check([declared("x", "env", name)])


@pytest.fixture
def declared_server(owner):
    document = json.loads(config.CONFIG_PATH.read_text())
    document["servers"]["Synthetic"].update(
        env={"NOTES_TOKEN": "{notes_token}", "NOTES_SPACE": "{space}"},
        inputs=[declared("notes_token", "env", "NOTES_TOKEN", secret=True, required=True, label="Notes token"),
                declared("space", "env", "NOTES_SPACE", required=True, choices=["work", "home"])],
        source={"auth_mode": "api_key"})
    document["servers"]["Synthetic"]["env"].pop("PRIVATE", None)
    config.CONFIG_PATH.write_text(json.dumps(document))
    keyring = MemoryKeyring()
    secret_store._set_backend_for_tests(keyring)
    facts.invalidate()
    yield keyring
    secret_store._set_backend_for_tests(None)


def context(**fields):
    return plans.Context(owner_id="owner", mcp_owner_id="owner", validate=lambda: None, local_owner=True, **fields)


def test_a_key_goes_only_to_the_keychain_and_a_setting_to_the_configuration(declared_server):
    item = next(row for row in facts.inventory()[0] if row["name"] == "Synthetic")
    assert {b["code"] for b in item["blockers"]} >= {"key_required", "inputs_required"}
    detail, plan = api.read_item(owner_id="owner", item_id=item["id"])
    step = next(s for s in plan["steps"] if s["type"] == "inputs")
    assert [(i["key"], i["secret"], i["choices"]) for i in step["inputs"]] == [("notes_token", True, []), ("space", False, ["work", "home"])]
    plan_id = str(uuid4())
    paused = api.start_plan(context(), plan_id=plan_id, item_id=item["id"], digest=plan["digest"])
    assert paused["pause"] == "inputs"
    refused = plans.resume(context(inputs={"notes_token": SECRET, "space": "garden"}), plan_id)
    assert refused["pause"] == "inputs" and "isn't a value" in next(s for s in refused["steps"] if s["type"] == "inputs")["message"]
    done = plans.resume(context(inputs={"notes_token": SECRET, "space": "work"}), plan_id)
    assert done["pause"] == "access", done
    saved = config.read_saved_configuration().document["servers"]["Synthetic"]
    assert saved["input_values"] == {"space": "work"} and saved["env"]["NOTES_TOKEN"] == "{notes_token}"
    # The key is in the keychain only: never in the configuration, the plan record or any answer.
    assert auth.read_credentials(saved["auth"]["credential_ref"])["values"] == {"notes_token": SECRET}
    assert SECRET not in config.CONFIG_PATH.read_text()
    assert SECRET not in json.dumps([admissions.receipt("owner", plan_id), done, detail])
    launch, _ = auth.transport_options("Synthetic", saved)
    assert launch["env"] == {"NOTES_TOKEN": SECRET, "NOTES_SPACE": "work"}  # Filled in for one connection only.
    facts.invalidate()
    after = next(row for row in facts.inventory()[0] if row["name"] == "Synthetic")
    assert not {b["code"] for b in after["blockers"]} & {"key_required", "inputs_required"}


def test_a_portable_plugin_asks_for_its_placeholders_instead_of_sending_them_literally():
    from row_bot.plugins.portable import declared_inputs
    entry = declared_inputs({"type": "stdio", "command": "node", "args": ["${PLUGIN_ROOT}/server.js", "--team", "${TEAM}"],
                             "env": {"API_KEY": "${API_KEY}", "MODE": "fast"}})
    assert entry["env"] == {"API_KEY": "{api_key}", "MODE": "fast"}
    assert entry["args"] == ["${PLUGIN_ROOT}/server.js", "--team", "{team}"]
    assert {i["key"]: (i["target"], i["secret"]) for i in entry["inputs"]} == {"api_key": ("env", True), "team": ("argument", False)}


def test_a_key_inside_a_pasted_link_is_never_saved_with_the_address():
    key = "k7Qx9vR2mP4tL8wZ3nB6"
    page = api.resolve_reference(owner_id="owner", reference=f"https://mcp.notes.example.test/s/{key}/mcp")
    (row,) = page["items"]
    assert key not in json.dumps(page)
    detail, plan = api.read_item(owner_id="owner", item_id=row["id"], revision=page["revision"])
    assert key not in json.dumps([detail, plan])
    asks = next(s for s in plan["steps"] if s["type"] == "inputs")["inputs"]
    assert [(i["label"], i["secret"], i["target"]) for i in asks] == [("Key from your link", True, "url_variable")]
