"""Golden queries: what a search shows first, against a fixture Registry snapshot and the shipped recipes,
apps and built-in ways. A change to the ranking weights in ``integrations/sources.py`` that moves any of these
is a reviewed change, never a silent one."""
from datetime import date, timedelta
import socket
import time

import pytest

from row_bot.application import client_integrations as api
from row_bot.integrations import sources
from row_bot.integrations.safe import TtlCache
from row_bot.mcp_client import marketplace
from row_bot.mcp_client.registry_signals import Repository
from tests.helpers.registry import search_catalog, use_registry


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(api, "_SEARCHES", TtlCache(1200, 64))
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a, **k: pytest.fail("a passive search fetched"))
    return tmp_path


def listing(name, title="", *, url="", npm="", version="1.0.0", days=10, icon="", repo="",
            description="A fixture server with enough words to describe what it does for people who search the catalog."):
    install = ({"transport": "streamable_http", "url": url} if url else
               {"transport": "stdio", "command": "npx", "args": ["-y", f"{npm}@{version}"]} if npm else None)
    updated = (date.today() - timedelta(days=days)).isoformat()
    return marketplace.MarketplaceEntry(f"{name}@{version}", title or name, description, "official", install=install, url=repo,
        metadata={"canonical_name": name, "version": version, "status": "active", "updated_at": updated,
                  **({"icon": icon} if icon else {}), **({"setup_digest": "a" * 64} if install else {})})


# Shaped like the real Registry: vendors' own records, popular community servers, copies and look-alikes.
REGISTRY = [
    listing("io.github.karanb192/reddit-mcp-buddy", npm="reddit-mcp-buddy", version="1.1.14",
            description="Reddit browser for AI assistants. Browse without API keys; add credentials for search and analysis."),
    listing("io.github.jordanburke/reddit-mcp-server", npm="reddit-mcp-server", version="1.6.3",
            repo="https://github.com/jordanburke/reddit-mcp-server",
            description="Reddit MCP server with full read/write - posts, comments, search, and content creation."),
    listing("com.example/reddit", "Reddit", url="https://reddit.example.test/mcp", days=400,
            description="Reddit data for agents. Pay per call."),
    listing("io.github.someone/reddit-ads", "Reddit Ads", npm="reddit-ads-mcp", days=200, repo="https://github.com/someone/reddit-ads",
            description="Manage Reddit advertising: campaigns, ad groups and reports from your ads account."),
    listing("io.github.digest/subreddit-digest", "Subreddit digest", npm="subreddit-digest",
            description="A daily digest of the subreddits you follow, summarised for busy readers who skim."),
    listing("io.github.someone/notion-helper", "Notion helper", url="https://helper.example.test/mcp",
            description="Search your Notion workspace from any assistant."),
    listing("com.notion/mcp", "Notion", url="https://mcp.notion.com/mcp"),
    listing("io.github.github/github-mcp-server", "GitHub", url="https://api.githubcopilot.com/mcp/"),
    listing("io.github.fan/github-helper", "GitHub helper", npm="github-helper",
            description="Helpers for GitHub issues and pull requests, run on your own computer."),
    listing("io.github.someone/slack-bot", "Slack bot", npm="slack-bot-mcp",
            description="Post to Slack channels from an assistant using your own Slack bot token."),
    listing("io.github.crystaldba/postgres-mcp", "Postgres MCP Pro", npm="postgres-mcp", icon="https://crystaldba.example.test/i.png",
            repo="https://github.com/crystaldba/postgres-mcp",
            description="Postgres tuning and query analysis: index advice, health checks and safe SQL for your database."),
    listing("io.github.someone/postgres", "Postgres", npm="pg-mcp", days=500, description="Postgres."),
    listing("io.github.someone/calendar-sync", "Calendar sync", npm="calendar-sync",
            description="Sync calendars between providers on this computer, with conflict checks and a dry run first."),
    listing("io.github.someone/filesystem", "Filesystem", npm="fs-mcp", days=300,
            description="Read and write files in folders you choose."),
]


# Shipped signals for some of them: a popular community server, a stale one and a well-kept database tool.
SIGNALS = {
    "github.com/jordanburke/reddit-mcp-server": Repository(310, int(time.time()) - 5 * 86400, False, "", "MIT"),
    "github.com/someone/reddit-ads": Repository(4, int(time.time()) - 300 * 86400, False, "", ""),
    "github.com/crystaldba/postgres-mcp": Repository(2100, int(time.time()) - 20 * 86400, False, "", "MIT"),
}


def summary(page):
    """The best match and the first five as (app or name, label, match)."""
    first = [((row["app"] or {}).get("id") or row["name"], row["label"], row["match"]) for row in page["items"][:5]]
    best = next((row for row in page["items"] if row["id"] == page["best_match_id"]), None)
    return ((best["app"] or {}).get("id") or best["name"]) if best else None, first


@pytest.mark.parametrize(("query", "best", "first"), [
    # Reddit publishes no server: the reviewed community one leads; the Registry's copy of it is the same card,
    # "subreddit" alone never matches, and a well-kept community server comes before a bare one named "Reddit".
    ("reddit", "reddit", [("reddit", "reviewed", "exact"), ("reddit-mcp-server", "community", "name"),
                          ("Reddit Ads", "community", "name"), ("Reddit", "community", "exact")]),
    ("notion", "notion", [("notion", "official", "exact"), ("Notion helper", "community", "name")]),
    ("github", "github", [("github", "official", "exact"), ("GitHub helper", "community", "name")]),
    ("slack", "slack", [("slack", "official", "exact"), ("Slack bot", "community", "name")]),
    # No vendor makes Postgres itself: hosts that run it first, then a reviewed connector, then the community.
    ("postgres", "supabase", [("supabase", "official", "name"), ("render", "official", "name"), ("dbhub", "reviewed", "name"),
                              ("Postgres MCP Pro", "community", "name"), ("Postgres", "community", "exact")]),
    ("calendar", "google", [("google", "built_in", "name"), ("outlook", "built_in", "name"),
                            ("Calendar sync", "community", "name")]),
    ("filesystem", "filesystem", [("filesystem", "reviewed", "exact"), ("Filesystem", "community", "exact")]),
])
def test_golden_queries(local, monkeypatch, query, best, first):
    use_registry(monkeypatch, local, REGISTRY, repositories=SIGNALS)
    page = search_catalog(kind="app", query=query)
    assert summary(page) == (best, first)
    assert sum(page["label_counts"].values()) == page["total"]


def test_a_query_word_starts_a_word_in_every_source():
    assert sources.matches("reddit", "Reddit tools") and sources.matches("redd", "reddit-mcp")
    assert not sources.matches("reddit", "Subreddit digest") and not sources.matches("hub", "GitHub")
    assert sources.matches("send email", "Send an email") and sources.matches("", "anything")


def test_only_a_name_match_backed_by_rowbot_the_vendor_or_quality_is_the_best_match(local, monkeypatch):
    use_registry(monkeypatch, local, REGISTRY)
    community = search_catalog(kind="app", query="digest")
    assert community["items"][0]["name"] == "Subreddit digest" and community["best_match_id"] is None
    described = search_catalog(kind="app", query="advertising")  # Only in Reddit Ads' description.
    assert described["items"][0]["match"] == "description" and described["best_match_id"] is None


def test_filters_run_on_the_server_so_counts_stay_true(local, monkeypatch):
    use_registry(monkeypatch, local, REGISTRY)
    backed = search_catalog(kind="app", query="reddit", labels=["official", "reviewed", "built_in"])
    assert [row["app"]["id"] for row in backed["items"]] == ["reddit"]
    assert backed["label_counts"] == {"official": 0, "reviewed": 1, "built_in": 0, "community": 0}
    hosted = search_catalog(kind="app", query="reddit", runs="hosted")
    assert [row["name"] for row in hosted["items"]] == ["Reddit"] and hosted["items"][0]["method"] == "hosted"
    here = search_catalog(kind="app", query="reddit", runs="local", no_sign_in=True)
    assert "Reddit" not in [row["name"] for row in here["items"]] and here["items"][0]["app"]["id"] == "reddit"
    # The Apps page reads the same filters.
    page = api.read_items(owner_id="owner", query="reddit", scope="catalog", kind="app", labels=["community"])
    assert page["items"] and {row["label"] for row in page["items"]} == {"community"}
    with pytest.raises(api.ClientPlatformError):
        search_catalog(query="reddit", runs="moon")


@pytest.mark.parametrize("query", ["reddit", "postgres", "calendar"])
def test_the_index_orders_registry_records_as_the_ranking_does(local, monkeypatch, query):
    """The index keeps only the top rows, so its SQL order mirrors the ranking key."""
    from row_bot.integrations import index
    use_registry(monkeypatch, local, REGISTRY)
    ranked = [row["id"].removeprefix("mcp:official:") for row in search_catalog(sources=["official"], query=query)["items"]]
    assert ranked == [entry.id for entry in index.search(query)[0]]


def test_searching_reads_only_this_computer(local, monkeypatch):
    use_registry(monkeypatch, local, REGISTRY)

    def refused(*args, **kwargs):
        raise AssertionError("a search opened a socket")
    monkeypatch.setattr(socket, "socket", refused)
    monkeypatch.setattr(socket, "create_connection", refused)
    page = search_catalog(kind="app", query="reddit")
    assert page["best_match_id"] and page["items"][0]["app"]["id"] == "reddit"


def names(page):
    return [row["name"] for row in page["items"]]


def test_one_package_listed_by_several_publishers_is_one_result(local, monkeypatch):
    use_registry(monkeypatch, local, [
        listing("io.github.maker/weather-kit", "Weather kit", npm="weather-kit", version="2.0.0"),
        listing("ai.smithery/maker-weather-kit", "Weather Kit (Smithery)", npm="weather-kit", version="1.9.0", days=40),
        listing("com.mirror/weather", "Weather", npm="weather-kit", version="1.0.0", days=90),
        listing("io.github.other/weather-kit-pro", "Weather kit pro", npm="weather-kit-pro"),
    ])
    page = search_catalog(sources=["official"], query="weather")
    merged = next(row for row in page["items"] if len(row["attributions"]) > 1)
    assert len(merged["attributions"]) == 3 and page["total"] == 2
    assert "Weather kit pro" in names(page)  # Another package is another result.


def test_a_repository_merges_only_with_a_second_sign(local, monkeypatch):
    repo = "https://github.com/Maker/Walker.git"
    use_registry(monkeypatch, local, [
        listing("io.github.maker/walker-flow", "Walker Flow", npm="walker-flow", repo=repo),
        listing("ai.smithery/walker-flow-mcp", "walker-flow MCP server", npm="walker-flow-cli", repo="https://github.com/maker/walker"),
        listing("io.github.maker/walker-browser", "Walker Browser", npm="walker-browser", repo=repo),  # Same repo, another server.
        listing("io.github.maker/walker-flow-cloud", "Walker Flow", url="https://flow.example.test/mcp", repo=repo),  # Hosted.
    ])
    page = search_catalog(sources=["official"], query="walker")
    assert page["total"] == 3, names(page) and sorted(len(row["attributions"]) for row in page["items"]) == [1, 1, 2]
    assert "Walker Browser" in names(page)


def test_near_identical_listings_by_one_owner_are_one_with_the_freshest_leading(local, monkeypatch):
    text = "Track parcels from every major carrier and get delivery updates in one place for your orders"
    use_registry(monkeypatch, local, [
        listing("io.github.ship/parcels", "Parcel tracker", npm="parcels-a", days=200, description=text),
        listing("io.github.ship/parcels-v2", "Parcel tracker", npm="parcels-b", days=5, description=text + " today"),
        listing("io.github.copycat/parcels", "Parcel tracker", npm="parcels-c", days=1, description=text + " now"),  # Another owner.
    ])
    page = search_catalog(sources=["official"], query="parcel")
    assert page["total"] == 2
    ship = next(row for row in page["items"] if row["publisher"] == "ship on GitHub")
    assert ship["id"].endswith("parcels-v2@1.0.0") and len(ship["attributions"]) == 2


def test_a_fork_folds_into_its_parent_unless_it_has_more_stars():
    from row_bot.integrations import facts

    def row(name, repo, *, parent="", stars=0):
        return facts.finish(facts.entry("mcp", "official:" + name, name, installed=False, lifecycle="available",
            source="official", source_url=f"https://github.com/{repo}", fork_parent=parent, signals={"stars": stars},
            publisher=repo.split("/")[0], description=f"{name} for tests", canonical_identity="mcp:registry:" + name))
    ranked = sources.rank([row("Tracker", "maker/tracker", stars=300),
                           row("Tracker fork", "fan/tracker", parent="github.com/maker/tracker", stars=2),
                           row("Tracker copy", "other/tracker", parent="github.com/maker/tracker", stars=0),
                           row("Tracker plus", "star/tracker", parent="github.com/maker/tracker", stars=900)], "tracker")
    assert [(r["name"], r["forks"]) for r in ranked] == [("Tracker plus", 0), ("Tracker", 2)]


def test_a_shared_endpoint_serving_different_tools_is_not_one_deployment():
    one = sources.mcp_identity({"transport": "streamable_http", "url": "https://mcp.apify.com/?tools=a/leaks"})
    other = sources.mcp_identity({"transport": "streamable_http", "url": "https://mcp.apify.com/?tools=b/signals"})
    template = sources.mcp_identity({"transport": "streamable_http", "url": "https://mcp.example.test/mcp?project={project}"})
    assert one != other and template == sources.mcp_identity({"transport": "streamable_http", "url": "https://mcp.example.test/mcp"})
