"""The local Registry mirror: rule-based vendor badges, ranking, read-only search and rebuilds (fakes only)."""
import statistics
import time

import pytest

from row_bot.application import client_integrations as api
from row_bot.integrations import apps, index, sources
from row_bot.integrations.safe import TtlCache
from row_bot.mcp_client import marketplace, registry_snapshot
from tests.helpers.registry import search_catalog, use_registry


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(api, "_SEARCHES", TtlCache(1200, 64))
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a, **k: pytest.fail("a passive search fetched"))
    return tmp_path


def listing(name, title="", *, url="", npm="", updated="2026-09-30", description="Fixture server", status="active"):
    install = {"transport": "streamable_http", "url": url} if url else (
        {"transport": "stdio", "command": "npx", "args": [npm + "@1.0.0"]} if npm else None)
    return marketplace.MarketplaceEntry(name + "@1.0.0", title or name, description, "official", install=install,
        metadata={"canonical_name": name, "version": "1.0.0", "status": status, "updated_at": updated,
                  **({"setup_digest": "a" * 64} if install else {})})


@pytest.mark.parametrize(("refs", "expected"), [
    (["registry:com.notion/mcp", "registry:com.notion"], True),            # reverse-DNS of a vendor domain
    (["registry:io.github.makenotion/notion-mcp-server", "registry:io.github.makenotion"], True),  # vendor GitHub org
    (["endpoint:mcp.notion.com"], True),                                    # vendor endpoint
    (["registry:io.github.someone/notion", "registry:io.github.someone"], False),
    (["registry:com.notion-tools/notion", "registry:com.notion-tools"], False),
    (["endpoint:notion.com.example.test"], False),
    (["npm:@notionhq/notion-mcp-server"], False),                           # a package name is not a rule
])
def test_vendor_badge_comes_only_from_rules(refs, expected):
    assert apps.verified(apps.catalog()[0]["notion"], refs) is expected


def test_a_name_never_attaches_or_verifies():
    impostor = listing("io.github.someone/notion", "Notion", url="https://notion.someone.example.test/mcp")
    found = index.derive(impostor)
    assert found["app"] == "" and not found["verified"] and found["featured"] is None


def test_search_ranks_featured_and_verified_first_then_setup_and_freshness(local, monkeypatch):
    rows = [
        listing("io.github.someone/notion-helper", "Notion helper", url="https://helper.example.test/mcp", updated="2026-10-01"),
        listing("com.notion/mcp", "Notion", url="https://mcp.notion.com/mcp", updated="2025-01-01"),
        listing("org.old/notion-notes", "Notes for notion", npm="old-notes", updated="2024-01-01", description="Older notes"),
        listing("org.fresh/notion-notes", "Notes for notion", npm="fresh-notes", updated="2026-09-01", description="Fresh notes"),
        listing("org.none/notion-docs", "Notion docs", updated="2026-09-01"),
        listing("org.other/weather", "Weather", url="https://weather.example.test/mcp"),
    ]
    use_registry(monkeypatch, local, rows)
    results, total, _, _hidden = index.search("notion", now=1791000000)
    names = [entry.metadata["canonical_name"] for entry, _ in results]
    assert total == 5 and "org.other/weather" not in names
    assert names[0] == "com.notion/mcp" and results[0][1]["verified"]
    # Installable with known authentication (local), then installable, then no recipe; fresher first.
    assert names[1:] == ["org.fresh/notion-notes", "org.old/notion-notes", "io.github.someone/notion-helper", "org.none/notion-docs"]
    assert [e.metadata["canonical_name"] for e, _ in index.search("")[0]] == ["com.notion/mcp"]  # No alphabetical dump.
    assert [e.metadata["canonical_name"] for e, _ in index.search("noti")[0]][0] == "com.notion/mcp"  # As you type.
    assert index.search("wiki")[0][0][0].metadata["canonical_name"] == "com.notion/mcp"  # A job or synonym.


def test_search_never_builds_writes_or_fetches(local):
    with pytest.raises(LookupError):
        index.search("notion")
    assert not (local / "catalogs").exists()
    page = search_catalog(sources=["official"], query="notion")
    assert page["sources"][0]["status"] == "pending" and not (local / "catalogs").exists()


def test_a_newer_release_snapshot_rebuilds_and_an_older_one_keeps_the_mirror(local, monkeypatch):
    use_registry(monkeypatch, local, [listing("org.a/one", "One")], watermark="2026-10-01T00:00:00Z")
    first = index.current()
    use_registry(monkeypatch, local, [listing("org.a/one", "One"), listing("org.b/two", "Two")], watermark="2026-10-02T00:00:00Z")
    assert index.current()["count"] == 2 and index.current()["file"] != first["file"]
    use_registry(monkeypatch, local, [listing("org.a/one", "One")], watermark="2026-09-01T00:00:00Z")
    assert index.current()["count"] == 2  # An older release snapshot never replaces a newer mirror.
    assert index.current()["digest"] != registry_snapshot.read_header()["digest"]


def test_the_mirror_digest_matches_the_snapshot_it_was_built_from(local, monkeypatch):
    use_registry(monkeypatch, local, [listing("org.b/two", "Two"), listing("org.a/one", "One")])
    assert index.current()["digest"] == registry_snapshot.read_header()["digest"]
    assert [e.id for e in index.rows()] == ["org.a/one@1.0.0", "org.b/two@1.0.0"]
    assert index.lookup("org.b/two@1.0.0").name == "Two" and index.lookup("org.b/two@9.9.9") is None


def test_registry_and_curated_listings_of_one_deployment_merge_with_the_vendor_badge(local, monkeypatch):
    use_registry(monkeypatch, local, [listing("com.notion/mcp", "Notion", url="https://mcp.notion.com/mcp")])
    page = search_catalog(sources=["recommended", "official"], query="notion", limit=10)
    notion = [row for row in page["items"] if row["app"] and row["app"]["id"] == "notion"]
    assert len(notion) == 1 and {a["source"] for a in notion[0]["attributions"]} == {"recommended", "official"}
    typed = page["items"][0]
    assert typed["id"] == notion[0]["id"]
    assert typed["verified"] and typed["app"]["verified"] and typed["icon"] == "si:notion"


def test_ranking_key_orders_preferred_then_exact_then_setup_then_freshness():
    def key(**fields):
        base = dict(exact=False, preferred=False, strong=True, featured_rank=None, setup=0, updated=0, popularity=0,
                    precedence=0, name="b", ident="b", now=1_000_000_000)
        return sources.order(**{**base, **fields})
    ranked = sorted([key(name="setup1", setup=1), key(name="stale", updated=1), key(name="fresh", updated=999_999_000),
                     key(name="featured", preferred=True, featured_rank=3), key(name="exact", exact=True),
                     key(name="popular", popularity=10), key(name="weak", strong=False)])
    # A community record never outranks a featured or vendor-verified one on its name alone.
    assert [k[-2] for k in ranked] == ["featured", "exact", "fresh", "popular", "stale", "setup1", "weak"]


@pytest.mark.slow
def test_search_stays_under_100ms_over_60000_records(local, monkeypatch):
    words = ["notes", "github", "postgres", "weather", "calendar", "email", "files", "search", "crm", "issues"]
    rows = [listing(f"io.github.user{i}/{words[i % 10]}-{i}", f"{words[i % 10].title()} tool {i}",
                    url=f"https://s{i}.example.test/mcp" if i % 3 else "", npm="" if i % 3 else f"pkg-{i}",
                    description=f"Server {i} for {words[(i * 7) % 10]} and {words[(i * 3) % 10]} work",
                    updated=f"2026-0{1 + i % 9}-1{i % 9}") for i in range(60_000)]
    index.build(rows, captured_at=1, watermark="2026-10-01T00:00:00Z")  # What start-up does with a 60,000-record snapshot.
    timings = []
    for query in ["", "n", "no", "notes", "github", "postgres sql", "send email", "server", "weather tool", "is"] * 3:
        started = time.perf_counter()
        index.search(query)
        timings.append(time.perf_counter() - started)
    assert statistics.quantiles(timings, n=20)[-1] < 0.1, max(timings)


def test_a_record_pointing_at_a_vendor_endpoint_never_borrows_its_app_or_badge(local, monkeypatch):
    impostor = listing("io.github.evil/notion", "Notion (official)", url="https://mcp.notion.com/mcp")
    vendor = listing("com.notion/mcp", "Notion", url="https://mcp.notion.com/mcp")
    found = index.derive(impostor)
    assert found["app"] == "" and not found["verified"]
    use_registry(monkeypatch, local, [impostor, vendor])
    (row,) = search_catalog(sources=["official"], query="notion")["items"]
    assert row["id"] == "mcp:official:com.notion/mcp@1.0.0"  # The vendor's record leads the merged deployment.
    assert {a["item_id"] for a in row["attributions"]} == {row["id"], "mcp:official:io.github.evil/notion@1.0.0"}


def test_a_torn_index_is_rebuilt_from_the_release_snapshot(local, monkeypatch):
    use_registry(monkeypatch, local, [listing("org.a/one", "One")])
    broken = index.current()["file"]
    (local / "catalogs" / broken).write_bytes(b"SQLite format 3\x00" + b"\x00" * 64)
    index._READY.clear()
    assert index.ensure()["file"] != broken and index.search("one")[0]


def test_symbols_alone_are_not_search_terms(local, monkeypatch):
    use_registry(monkeypatch, local, [listing("org.a/github-tools", "GitHub tools")])
    assert index.search("github __")[1] == 1 and index.search("__ --")[1] >= 0
