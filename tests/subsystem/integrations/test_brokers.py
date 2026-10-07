"""Hosted connector brokers (Composio): off until the person turns one on after its disclosure; then
found by Row-Bot's own list of app names and connected through the normal hosted plan, with its
remote code tools off and its acting tools asking every time. Nothing here contacts Composio."""
# ruff: noqa: F811 -- shared isolated fixtures
import pytest

from row_bot.application import client_integrations as api
from row_bot.application.capability_catalog_controls import capture_tested_catalog
from row_bot.application.client_platform import ClientPlatformError
from row_bot.integrations import brokers, plans, presets, sources
from tests.helpers.registry import search_catalog
from tests.subsystem.mcp.test_capability_catalog_controls import owner  # noqa: F401

pytestmark = [pytest.mark.platform, pytest.mark.mcp_transport]


@pytest.fixture
def local(owner, tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    return owner


def composio_view():
    return next(item for item in api.list_sources()["items"] if item["id"] == "composio")


def test_a_broker_is_off_until_the_person_turns_it_on_and_says_what_that_means(local):
    view = composio_view()
    assert view["enabled"] is False and view["opt_in"]["on"] is False
    disclosure = view["opt_in"]["disclosure"]
    assert "separate company" in disclosure and "own account" in disclosure and "up to a year" in disclosure
    assert [link["url"] for link in view["opt_in"]["links"]] == [
        "https://composio.dev/terms", "https://composio.dev/privacy", "https://composio.dev/legal/dpa"]
    # Off: no search finds it, even asked for by name, and no app offers it.
    assert search_catalog(query="notion")["items"] and not [
        row for row in search_catalog(query="notion")["items"] if row["source"] == "composio"]
    assert search_catalog(query="composio", sources=["composio"])["items"] == []
    detail, _ = api.read_item(owner_id="owner", item_id="mcp:curated:makenotion-notion-mcp-server")
    assert not [way for way in detail["about"]["ways"] if "Composio" in way["name"]]
    with pytest.raises(ClientPlatformError, match="not_found"):
        api.set_source_opt_in("official", True)


def test_once_on_apps_offer_it_by_name_and_it_connects_through_the_hosted_plan(local):
    assert api.set_source_opt_in("composio", True)["opt_in"]["on"] is True
    found = [row for row in search_catalog(query="notion")["items"] if row["source"] == "composio"]
    assert {row["name"] for row in found} == {"Composio", "Composio with a consumer key"}
    assert all(row["description"].startswith("Connect Notion and other apps through one Composio account.") for row in found)
    detail, _ = api.read_item(owner_id="owner", item_id="mcp:curated:makenotion-notion-mcp-server")
    via = [way for way in detail["about"]["ways"] if way["name"] == "Notion via Composio"]
    assert len(via) == 1 and via[0]["id"] == "mcp:composio:composio" and via[0]["method"] == "hosted_sign_in"
    assert not via[0]["recommended"]  # The vendor's own way stays first.

    row, reference = sources.catalog_entry("mcp:composio:composio")
    plan = plans.compute(row, reference, intent="connect")
    assert plan["supported"] and plan["consent"]["destinations"] == ["https://connect.composio.dev"]
    assert "sign_in" in [step["type"] for step in plan["steps"]]
    row, reference = sources.catalog_entry("mcp:composio:composio-key")
    plan = plans.compute(row, reference, intent="connect")
    keys = [item for step in plan["steps"] for item in step.get("inputs") or []]
    assert [(item["key"], item["secret"]) for item in keys] == [("consumer_key", True)]  # Kept in the keychain.

    api.set_source_opt_in("composio", False)
    assert composio_view()["enabled"] is False
    assert search_catalog(query="composio", sources=["composio"])["items"] == []


def test_its_remote_code_tools_start_off_and_its_acting_tools_always_ask():
    claimed = {"effect": "read_only", "destructive": False, "requires_approval": False, "input_schema": {}}
    tested = {"ok": True, "tools": [
        {"name": name, "prefixed_name": "mcp_composio_" + name.lower(), "description": "", **claimed}
        for name in ("COMPOSIO_SEARCH_TOOLS", "COMPOSIO_REMOTE_WORKBENCH", "COMPOSIO_REMOTE_BASH_TOOL",
                     "COMPOSIO_MULTI_EXECUTE_TOOL", "COMPOSIO_MANAGE_CONNECTIONS")]}
    rows = {row["name"]: row for row in capture_tested_catalog(tested)["tools"]}
    for preset in presets.PRESETS:
        states = {name: presets.tool_state(preset, row) for name, row in rows.items()}
        assert states["COMPOSIO_SEARCH_TOOLS"] == "use"  # Finding tools only reads.
        assert states["COMPOSIO_REMOTE_WORKBENCH"] == states["COMPOSIO_REMOTE_BASH_TOOL"] == "off"
        assert states["COMPOSIO_MULTI_EXECUTE_TOOL"] in {"ask", "off"} and states["COMPOSIO_MANAGE_CONNECTIONS"] in {"ask", "off"}
    tools: dict = {}
    for name in ("COMPOSIO_REMOTE_WORKBENCH", "COMPOSIO_MULTI_EXECUTE_TOOL"):
        with pytest.raises(ValueError, match="approval_required"):
            presets.set_state(tools, name, "use", rows[name])
    presets.set_state(tools, "COMPOSIO_REMOTE_WORKBENCH", "ask", rows["COMPOSIO_REMOTE_WORKBENCH"])  # The person may, and is asked.
    assert tools["enabled"]["COMPOSIO_REMOTE_WORKBENCH"] and "COMPOSIO_REMOTE_WORKBENCH" in tools["require_approval"]
    assert brokers.tool_rules("get_record") == {}  # Other tools keep their own classification.
