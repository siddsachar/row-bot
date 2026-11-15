"""Quality signals shipped beside the Registry snapshot: how release preparation reads them (fake GitHub
and Registry answers), how the app loads them, and how they rank, all without a network call at search."""
from datetime import date, datetime, timedelta
import importlib.util
import json
import lzma
from pathlib import Path
import socket
import time

import pytest

from row_bot.application import client_integrations as api
from row_bot.integrations import index
from row_bot.integrations.safe import TtlCache
from row_bot.mcp_client import marketplace, registry_signals
from tests.helpers.registry import search_catalog, use_registry

Repository = registry_signals.Repository
NOW = int(time.time())


def _builder():
    path = Path(__file__).resolve().parents[3] / "scripts" / "build_catalog_signals.py"
    spec = importlib.util.spec_from_file_location("build_catalog_signals", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(api, "_SEARCHES", TtlCache(1200, 64))
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a, **k: pytest.fail("a passive search fetched"))
    return tmp_path


def listing(name, title, *, repo, days=10, description="A daily digest of the subreddits you follow, summarised for "
                                                          "busy readers who skim the news each morning."):
    install = {"transport": "stdio", "command": "npx", "args": ["-y", name.rpartition("/")[2] + "@1.0.0"]}
    return marketplace.MarketplaceEntry(f"{name}@1.0.0", title, description, "official", install=install, url=repo,
        metadata={"canonical_name": name, "version": "1.0.0", "status": "active", "setup_digest": "a" * 64,
                  "updated_at": (date.today() - timedelta(days=days)).isoformat()})


def test_release_preparation_reads_each_repository_once_in_batches():
    builder = _builder()
    asked = []

    def post(text):
        asked.append(text)
        return {"data": {"rateLimit": {"remaining": 4000, "resetAt": "2026-11-05T00:00:00Z"},
                         "r0": {"stargazerCount": 881, "pushedAt": "2026-11-02T02:45:42Z", "isArchived": False,
                                "parent": None, "licenseInfo": {"spdxId": "MIT"}},
                         "r1": None,  # Gone or private: left out.
                         "r2": {"stargazerCount": 3, "pushedAt": "2025-01-01T00:00:00Z", "isArchived": True,
                                "parent": {"nameWithOwner": "Karanb192/Reddit-MCP-Buddy"}, "licenseInfo": {"spdxId": "NOASSERTION"}}}}
    entries = [marketplace.MarketplaceEntry("a@1", "a", "", "official", url=url) for url in (
        "https://github.com/karanb192/reddit-mcp-buddy", "https://github.com/gone/x.git", "https://github.com/fan/buddy/tree/main/src",
        "https://gitlab.com/elsewhere/tool", "https://github.com/karanb192/reddit-mcp-buddy/")]
    names = builder.github_repositories(entries)
    assert names == ["github.com/fan/buddy", "github.com/gone/x", "github.com/karanb192/reddit-mcp-buddy"]
    found = builder.read_repositories(["github.com/karanb192/reddit-mcp-buddy", "github.com/gone/x", "github.com/fan/buddy"],
                                      post, sleep=lambda s: None)
    assert len(asked) == 1 and 'owner: "karanb192", name: "reddit-mcp-buddy"' in asked[0]
    assert found == {
        "github.com/karanb192/reddit-mcp-buddy": Repository(881, _at("2026-11-02T02:45:42Z"), False, "", "MIT"),
        "github.com/fan/buddy": Repository(3, _at("2025-01-01T00:00:00Z"), True, "github.com/karanb192/reddit-mcp-buddy", ""),
    }


def test_release_preparation_counts_every_published_version_of_each_record():
    builder = _builder()
    pages = [
        {"servers": [_version("io.github.a/one", "2025-09-17T15:39:49Z"), _version("io.github.a/one", "2026-07-29T11:04:09Z"),
                     _version("io.github.b/unlisted", "2026-01-01T00:00:00Z")], "metadata": {"nextCursor": "c1"}},
        {"servers": [_version("io.github.a/one", "2025-09-20T10:45:00Z"), _version("io.github.a/one", "2026-10-01T00:00:00Z", "deleted"),
                     _version("io.github.c/two", "2026-11-01T00:00:00Z")], "metadata": {}},
    ]
    urls = []
    records = builder.read_records({"io.github.a/one", "io.github.c/two"}, lambda url: urls.append(url) or pages[len(urls) - 1],
                                   sleep=lambda s: None)
    assert records == {"io.github.a/one": (_at("2025-09-17T15:39:49Z"), 3), "io.github.c/two": (_at("2026-11-01T00:00:00Z"), 1)}
    assert "cursor=c1" in urls[1] and "version=" not in urls[0]  # Every version, not only the latest.


def _at(stamp):
    return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp())


def _version(name, published, status="active"):
    return {"server": {"name": name}, "_meta": {"io.modelcontextprotocol.registry/official": {"status": status, "publishedAt": published}}}


def test_the_app_reads_the_shipped_file_and_ranks_without_it_when_it_is_missing_or_damaged(tmp_path, monkeypatch):
    path = tmp_path / "registry_signals.json.xz"
    monkeypatch.setattr(registry_signals, "SHIPPED", path)
    assert registry_signals.repository("github.com/a/b") is None and registry_signals.digest() == ""
    path.write_bytes(registry_signals.encode(captured_at=1.0, snapshot_digest="d", records={"io.github.a/b": (5, 2)},
        repositories={"github.com/a/b": Repository(10, 20, False, "", "MIT"),
                      "github.com/c/fork": Repository(1, 2, False, "github.com/a/b", ""),
                      "github.com/d/fork": Repository(1, 2, False, "github.com/not/listed", "")}))
    assert registry_signals.repository("github.com/a/b") == Repository(10, 20, False, "", "MIT")
    assert registry_signals.repository("github.com/c/fork").fork and not registry_signals.repository("github.com/d/fork").fork
    assert registry_signals.record("io.github.a/b") == (5, 2) and registry_signals.record("other") == (0, 0)
    assert registry_signals.digest()
    damaged = tmp_path / "damaged.json.xz"
    damaged.write_bytes(lzma.compress(json.dumps({"schema_version": 99}).encode()))
    monkeypatch.setattr(registry_signals, "SHIPPED", damaged)
    assert registry_signals.repository("github.com/a/b") is None and registry_signals.digest() == ""


def test_stars_let_a_well_kept_community_server_lead_and_the_index_follows_the_shipped_signals(local, monkeypatch):
    rows = [listing("io.github.digest/subreddit-digest", "Subreddit digest", repo="https://github.com/digest/subreddit-digest"),
            listing("io.github.other/news-digest", "News digest", repo="https://github.com/other/news-digest")]
    use_registry(monkeypatch, local, rows)
    plain = search_catalog(kind="app", query="digest")
    assert plain["best_match_id"] is None
    first = index.current()["file"]
    use_registry(monkeypatch, local, rows, repositories={
        "github.com/digest/subreddit-digest": Repository(120, NOW - 86400, False, "", "MIT"),
        "github.com/other/news-digest": Repository(150, NOW - 86400, True, "", "MIT")})  # More stars, but archived.
    assert index.current()["file"] != first  # Other signals: the index is ranked again.
    page = search_catalog(kind="app", query="digest")
    leader = page["items"][0]
    assert leader["name"] == "Subreddit digest" and page["best_match_id"] == leader["id"]
    assert leader["signals"]["stars"] == 120 and leader["signals"]["last_activity_at"] == NOW - 86400


def test_a_fork_with_fewer_stars_folds_into_its_parent_and_a_popular_one_stays_with_a_penalty(local, monkeypatch):
    use_registry(monkeypatch, local, [
        listing("io.github.maker/tracker", "Tracker", repo="https://github.com/maker/tracker"),
        listing("io.github.fan/tracker-fork", "Tracker fork", repo="https://github.com/fan/tracker"),
        listing("io.github.star/tracker-plus", "Tracker plus", repo="https://github.com/star/tracker"),
    ], repositories={"github.com/maker/tracker": Repository(300, NOW, False, "", "MIT"),
                     "github.com/fan/tracker": Repository(2, NOW, False, "github.com/maker/tracker", "MIT"),
                     "github.com/star/tracker": Repository(900, NOW, False, "github.com/maker/tracker", "MIT")})
    page = search_catalog(kind="app", query="tracker")
    assert [(row["name"], row["forks"]) for row in page["items"]] == [("Tracker", 1), ("Tracker plus", 0)]
    assert len(page["items"][0]["attributions"]) == 2


def test_searching_with_signals_never_opens_a_socket(local, monkeypatch):
    use_registry(monkeypatch, local, [listing("io.github.digest/subreddit-digest", "Subreddit digest",
                                              repo="https://github.com/digest/subreddit-digest")],
                 repositories={"github.com/digest/subreddit-digest": Repository(120, NOW, False, "", "MIT")},
                 records={"io.github.digest/subreddit-digest": (NOW - 400 * 86400, 7)})

    def refused(*args, **kwargs):
        raise AssertionError("a search opened a socket")
    monkeypatch.setattr(socket, "socket", refused)
    monkeypatch.setattr(socket, "create_connection", refused)
    page = search_catalog(kind="app", query="digest")
    assert page["items"][0]["signals"]["stars"] == 120
