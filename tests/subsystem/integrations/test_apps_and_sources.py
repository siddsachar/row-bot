"""App identities and catalog sources, over shipped data and fakes only."""
import pytest

from row_bot.application import client_integrations as api
from row_bot.integrations import apps, sources
from row_bot.integrations.safe import TtlCache
from row_bot.mcp_client import marketplace
from tests.helpers.registry import search_catalog, use_registry


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(api, "_SEARCHES", TtlCache(1200, 64))
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a, **k: pytest.fail("a passive search fetched"))
    return tmp_path


def test_every_curated_recipe_and_seed_package_has_an_app_identity():
    for entry in marketplace.CURATED_STARTER_CATALOG:
        assert apps.match(["curated:" + entry.id.lower()]) is not None, entry.id
    assert apps.match(["hermes:blender"]).name == "Blender"
    assert apps.match(apps.repository_refs("https://github.com/NousResearch/hermes-plugin-blender")).id == "blender"
    assert apps.match(apps.repository_refs("row-bot:local-text-tools")).id == "local-text-tools"
    # Row-Bot's own accounts, channels and key tools belong to their apps too.
    assert {apps.match([ref]).id for ref in ("account:google", "account:x", "channel:telegram", "channel:whatsapp",
                                             "channel:discord", "channel:sms", "tool:web_search")} == {
        "google", "x", "telegram", "whatsapp", "discord", "sms", "tavily"}
    assert apps.match(["account:github"]).id == apps.match(["curated:github-github-mcp-server"]).id == "github"


@pytest.mark.parametrize(("install", "refs"), [
    ({"url": "https://MCP.Notion.com/mcp"}, ["endpoint:mcp.notion.com"]),
    ({"command": "npx", "args": ["-y", "@playwright/mcp@1.2.3"]}, ["npm:@playwright/mcp"]),
    ({"command": "npx.cmd", "args": ["tavily-mcp@0.1.4"]}, ["npm:tavily-mcp"]),
    ({"command": "uvx", "args": ["mcp-server-fetch"]}, ["pypi:mcp-server-fetch"]),
    ({"command": "docker", "args": ["run", "-i", "--rm", "-e", "TOKEN", "ghcr.io/github/github-mcp-server:latest"]},
     ["oci:ghcr.io/github/github-mcp-server"]),
    ({"command": "docker", "args": ["run", "-v", "ghcr.io/github/github-mcp-server:/x", "--rm", "example/other"]},
     ["oci:example/other"]),
    ({"command": "node", "args": ["server.js"]}, []),
    (None, []),
])
def test_recipes_attach_by_source_neutral_reference(install, refs):
    assert apps.recipe_refs(install) == refs


def test_registry_namespaces_attach_and_unknown_records_stay_community():
    assert apps.match(apps.registry_refs("com.notion/mcp")).id == "notion"
    assert apps.match(apps.registry_refs("ai.example/notion-clone")) is None
    assert apps.match(apps.recipe_refs({"url": "https://mcp.notion.com/mcp"})).id == "notion"


def test_source_list_is_served_with_server_side_eligibility():
    views = {source.id: source.view() for source in sources.SOURCES.values()}
    assert views["official"]["enabled"] and views["official"]["network"] == "explicit"
    assert views["recommended"]["network"] == "none"
    assert not views["glama"]["enabled"] and views["glama"]["access"] == "unavailable"
    assert views["examples"]["eligibility"] == "explicit_only"
    assert all(view["label"] for view in views.values())


def test_all_curated_recipes_are_visible_and_findable_by_job(local):
    page = search_catalog(sources=["recommended"], limit=96)
    # One card per app; an app's other recipes (GitHub's token, OAuth app and local server) are on its page.
    cards = {getattr(apps.match(["curated:" + entry.id.lower()]), "id", entry.id) for entry in marketplace.CURATED_STARTER_CATALOG}
    assert page["total"] == len(cards) == 48 and len(marketplace.CURATED_STARTER_CATALOG) == 50
    for job, app in (("payments", "Stripe MCP"), ("issues tickets", "Linear MCP"), ("web search", "Tavily MCP"),
                     ("local files", "Filesystem")):
        found = search_catalog(sources=["recommended"], query=job)
        assert app in [row["name"] for row in found["items"]], job


def test_one_deployment_listed_twice_is_merged_with_both_attributions(local, monkeypatch):
    listing = marketplace.MarketplaceEntry("com.notion/mcp@1.0.0", "Notion", "Registry listing", "official",
        install={"transport": "streamable_http", "url": "https://mcp.notion.com/mcp"},
        metadata={"canonical_name": "com.notion/mcp", "version": "1.0.0", "setup_digest": "a" * 64})
    use_registry(monkeypatch, local, [listing])
    page = search_catalog(sources=["recommended", "official"], query="notion")
    notion = [row for row in page["items"] if row["app"] and row["app"]["id"] == "notion"]
    assert len(notion) == 1 and notion[0]["source"] == "curated"
    assert {a["source"] for a in notion[0]["attributions"]} == {"recommended", "official"}
    assert {a["item_id"] for a in notion[0]["attributions"]} == {"mcp:curated:makenotion-notion-mcp-server",
                                                                 "mcp:official:com.notion/mcp@1.0.0"}


def test_unsupported_entries_appear_only_when_searched(local, monkeypatch):
    listing = marketplace.MarketplaceEntry("org.example/tool@1.0.0", "Example Tool", "Needs headers", "official",
        metadata={"canonical_name": "org.example/tool", "version": "1.0.0"}, notes=["Header declarations unsupported."])
    use_registry(monkeypatch, local, [listing])
    assert search_catalog(sources=["official"])["total"] == 0
    found = search_catalog(sources=["official"], query="example")["items"]
    assert len(found) == 1 and found[0]["compatibility"] == "unsupported"
    assert found[0]["blockers"][0]["code"] == "unsupported" and found[0]["blockers"][0]["message"]
    assert found[0]["next_action"]["kind"] == "none"


def test_unknown_sources_are_refused(local):
    with pytest.raises(ValueError, match="invalid_integration_query"):
        search_catalog(sources=["not-a-source"])
