"""What search shows: one card per app, vendors before the community, no namespace noise and a
quality floor that "Show all results" lifts. Local fixture catalogs only."""
import pytest

from row_bot.application import client_integrations as api
from row_bot.integrations import plans
from row_bot.integrations.safe import TtlCache
from row_bot.mcp_client import marketplace
from tests.helpers.registry import search_catalog, use_registry


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(api, "_SEARCHES", TtlCache(1200, 64))
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a, **k: pytest.fail("a passive search fetched"))
    return tmp_path


def listing(name, title="", *, url="", description="A fixture server for tests"):
    install = {"transport": "streamable_http", "url": url or f"https://{name.replace('/', '-')}.example.test/mcp"}
    return marketplace.MarketplaceEntry(name + "@1.0.0", title or name, description, "official", install=install,
        metadata={"canonical_name": name, "version": "1.0.0", "status": "active", "updated_at": "2026-09-30",
                  "setup_digest": "a" * 64})


def names(page):
    return [row["id"].removeprefix("mcp:official:").removesuffix("@1.0.0") for row in page["items"]]


def test_namespace_words_never_match_unless_the_query_is_the_namespace(local, monkeypatch):
    use_registry(monkeypatch, local, [
        listing("io.github.zoom/zoom-meetings", "Zoom meetings", url="https://mcp.zoom.us/mcp"),
        listing("io.github.someone/github-helper", "GitHub helper"),
        listing("com.acme/tools", "Acme tools"),
    ])
    assert names(search_catalog(sources=["official"], query="github")) == ["io.github.someone/github-helper"]
    assert names(search_catalog(sources=["official"], query="com")) == []
    assert names(search_catalog(sources=["official"], query="io.github.zoom")) == ["io.github.zoom/zoom-meetings"]
    assert names(search_catalog(sources=["official"], query="com.acme")) == ["com.acme/tools"]
    # The owner of a namespace is still a word people search for.
    assert names(search_catalog(sources=["official"], query="acme")) == ["com.acme/tools"]


def test_placeholders_and_copies_are_hidden_until_show_all(local, monkeypatch):
    use_registry(monkeypatch, local, [
        listing("org.real/mail", "Mail sender", description="Send mail from your own domain"),
        listing("org.copy/mail", "Mail sender", description="Send mail from your own domain"),
        listing("live.alpic.staging.x/mail", "Mail sender staging", description="Send mail staging copy"),
        listing("org.template/mail", "My MCP Server", description="Description of my MCP server"),
        listing("org.broken/mail", "Mail thing", description="Non functional server (yet)"),
    ])
    page = search_catalog(sources=["official"], query="mail")
    assert names(page) in (["org.real/mail"], ["org.copy/mail"]) and page["hidden"] == 4  # One copy of a listing.
    everything = search_catalog(sources=["official"], query="mail", include_incompatible=True)
    assert len(everything["items"]) == 5 and everything["hidden"] == 0
    shown = api.read_items(owner_id="owner", query="mail", scope="catalog", kind="app", everything=True)
    assert sum(row["source"] == "official" for row in shown["items"]) == 5


def test_one_card_per_app_with_vendors_first_and_its_other_ways_on_its_page(local, monkeypatch):
    use_registry(monkeypatch, local, [
        listing("io.github.zoom/zoom-meetings", "Zoom meetings", url="https://mcp.zoom.us/mcp"),
        listing("io.github.zoom/zoom-tasks", "Zoom tasks", url="https://mcp-us.zoom.us/tasks"),
        listing("io.github.fan/zoom", "Zoom", description="An exact name, but from the community"),
    ])
    page = search_catalog(sources=["official"], query="zoom")
    assert [row["app"]["id"] if row["app"] else row["id"] for row in page["items"]] == ["zoom", "mcp:official:io.github.fan/zoom@1.0.0"]
    assert page["items"][0]["verified"] and page["items"][0]["publisher"] == "zoom on GitHub"
    detail, _ = api.read_item(owner_id="owner", item_id=page["items"][0]["id"], revision=page["revision"])
    ways = detail["about"]["ways"]
    assert {way["id"] for way in ways} == {"mcp:official:io.github.zoom/zoom-meetings@1.0.0", "mcp:official:io.github.zoom/zoom-tasks@1.0.0"}
    assert ways[0]["recommended"] and all(way["verified"] for way in ways)


def test_github_is_one_card_whose_page_offers_a_token_its_own_oauth_app_or_this_computer(local, monkeypatch):
    github = marketplace.MarketplaceEntry("io.github.github/github-mcp-server@1.13.0", "GitHub", "GitHub's server", "official",
        install={"transport": "streamable_http", "url": "https://api.githubcopilot.com/mcp/", "headers": {"Authorization": "{authorization}"}},
        metadata={"canonical_name": "io.github.github/github-mcp-server", "version": "1.13.0", "status": "active",
                  "updated_at": "2026-09-30", "setup_digest": "b" * 64})
    use_registry(monkeypatch, local, [github, listing("io.github.fan/github-helper", "GitHub helper")])
    page = search_catalog(query="github")
    first = page["items"][0]
    assert first["id"] == "mcp:curated:github-hosted" and first["verified"] and first["method"] == "api_key"
    assert sum((row["app"] or {}).get("id") == "github" for row in page["items"] if row["kind"] != "skill") == 1
    # The Registry's copy of the same endpoint is the same way to connect, not another card.
    assert "mcp:official:io.github.github/github-mcp-server@1.13.0" in {a["item_id"] for a in first["attributions"]}
    detail, _ = api.read_item(owner_id="owner", item_id=first["id"], revision=page["revision"])
    ways = [(way["id"], way["method"], way["recommended"]) for way in detail["about"]["ways"]]
    assert ways == [("mcp:curated:github-hosted", "api_key", True), ("mcp:curated:github-oauth-app", "hosted_sign_in", False),
                    ("mcp:curated:github-github-mcp-server", "local", False)]


def test_a_job_reaches_the_apps_that_do_it_before_the_community(local, monkeypatch):
    use_registry(monkeypatch, local, [listing("io.github.fan/send-email", "Send Email", description="Send email from prompts")])
    page = search_catalog(kind="app", query="send email")
    first = [row["app"]["id"] if row["app"] else row["id"] for row in page["items"]][:3]
    assert first == ["google", "microsoft-365", "resend"] or first[:2] == ["google", "resend"]
    assert page["items"][-1]["id"] == "mcp:official:io.github.fan/send-email@1.0.0"
    google = page["items"][0]
    assert google["source"] == "accounts" and google["publisher"] == "Row-Bot" and google["method"] == ""
    _, plan = api.read_item(owner_id="owner", item_id=google["id"], revision=page["revision"])
    assert not plan["supported"] and "Settings › Accounts" in plan["unsupported_reason"]


def test_reading_one_plan_slowly_never_holds_up_another(monkeypatch):
    import threading
    entered, release = threading.Event(), threading.Event()
    reads = []

    def read(ctx, plan_id):
        if plan_id == "slow":
            entered.set()
            assert release.wait(5)
        reads.append(plan_id)
        return {"plan_id": plan_id}
    monkeypatch.setattr(plans, "_read", read)
    ctx = plans.Context("owner", "owner", lambda: None)
    slow = threading.Thread(target=plans.read_plan, args=(ctx, "slow"))
    slow.start()
    assert entered.wait(5)
    assert plans.read_plan(ctx, "fast") == {"plan_id": "fast"}  # Not blocked by the slow read.
    release.set()
    slow.join(5)
    assert reads == ["fast", "slow"]
