"""The Registry mirror's snapshot: reproducible, integrity-checked, synced politely (fakes only)."""
import copy
import json
import lzma
from pathlib import Path

import httpx
import pytest

from row_bot.mcp_client import marketplace, registry_snapshot as snapshot
from tests.helpers.registry import search_catalog, use_registry

pytestmark = pytest.mark.platform


@pytest.fixture
def metadata():
    return json.loads((Path(__file__).parents[2] / "fixtures/integrations/registry-v01.json").read_text())


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    return tmp_path


def envelope(name, *, updated="2026-10-01T00:00:00Z", status="active", icons=None):
    server = {"name": name, "version": "1.0.0", "description": "Fixture " + name,
              "remotes": [{"type": "streamable-http", "url": f"https://{name.split('/')[0]}.example.test/mcp"}]}
    if icons:
        server["icons"] = icons
    return {"server": server, "_meta": {"io.modelcontextprotocol.registry/official": {"status": status, "updatedAt": updated}}}


def test_snapshot_is_reproducible_and_integrity_checked(local, metadata):
    entries = marketplace.registry_entries(metadata)
    first = snapshot.build_snapshot(entries, captured_at=1, watermark="2026-10-01T00:00:00Z")
    assert first == snapshot.build_snapshot(list(reversed(entries)), captured_at=1, watermark="2026-10-01T00:00:00Z")
    path = local / "snapshot.jsonl.xz"
    path.write_bytes(first)
    read = snapshot.read_snapshot(path)
    assert [e.id for e in read["entries"]] == [e.id for e in entries] and read["count"] == 1
    assert read["entries"][0].metadata["setup_digest"] == entries[0].metadata["setup_digest"]
    header, _, body = lzma.decompress(first).partition(b"\n")
    path.write_bytes(lzma.compress(header + b"\n" + body.replace(b"Synthetic", b"Tampered")))
    with pytest.raises(ValueError, match="digest"):
        snapshot.read_snapshot(path)
    older = b'"schema_version":' + str(snapshot.SCHEMA - 1).encode()
    path.write_bytes(lzma.compress(header.replace(b'"schema_version":' + str(snapshot.SCHEMA).encode(), older) + b"\n" + body))
    with pytest.raises(ValueError, match="invalid"):
        snapshot.read_header(path)


def test_svg_icons_are_dropped_and_raster_icons_kept_at_normalization():
    svg = [{"src": "https://cdn.example.test/logo.svg", "mimeType": "image/svg+xml"}]
    png = [{"src": "https://cdn.example.test/logo.png", "mimeType": "image/png"}]
    assert "icon" not in marketplace.registry_entries({"servers": [envelope("org.a/x", icons=svg)]})[0].metadata
    assert marketplace.registry_entries({"servers": [envelope("org.b/x", icons=png)]})[0].metadata["icon"] == png[0]["src"]


def test_sync_pages_politely_with_updated_since_etag_backoff_and_deletions():
    pages = [
        {"servers": [envelope("org.a/x", updated="2026-10-02T00:00:00Z"), envelope("org.b/x", status="deleted")],
         "metadata": {"nextCursor": "c1"}},
        {"servers": [envelope("org.c/x", updated="2026-10-03T05:00:00Z")], "metadata": {}},
    ]
    calls, sleeps, throttled = [], [], [True]

    def get(url, headers, meta):
        calls.append((url, dict(headers)))
        if throttled[0] and len(calls) == 2:
            throttled[0] = False
            response = httpx.Response(429, headers={"retry-after": "3"}, request=httpx.Request("GET", url))
            raise httpx.HTTPStatusError("throttled", request=response.request, response=response)
        meta["headers"] = {"etag": '"v2"'}
        return json.dumps(pages[0] if "cursor=" not in url else pages[1]).encode()
    result = snapshot.sync(since="2026-10-01T00:00:00Z", etag='"v1"', get=get, sleep=sleeps.append, pause=0.25)
    assert all("updated_since=2026-10-01" in url and "version=latest" in url and "search=" not in url for url, _ in calls)
    assert calls[0][1]["If-None-Match"] == '"v1"' and "If-None-Match" not in calls[-1][1]
    assert sleeps == [0.25, 3.0] and len(calls) == 3
    assert sorted(e.metadata["canonical_name"] for e in result["entries"]) == ["org.a/x", "org.c/x"]
    assert result["deleted"] == ["org.b/x"] and result["watermark"] == "2026-10-03T05:00:00.000000000Z" and result["etag"] == '"v2"'


def test_sync_retries_a_page_that_timed_out_or_dropped_with_growing_waits():
    page = json.dumps({"servers": [envelope("org.a/x")], "metadata": {}}).encode()
    failures = [httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("dropped"), httpx.ConnectError("reset")]
    sleeps = []

    def get(url, headers, meta):
        if failures:
            raise failures.pop(0)
        return page
    result = snapshot.sync(get=get, sleep=sleeps.append)
    assert [e.metadata["canonical_name"] for e in result["entries"]] == ["org.a/x"] and sleeps == [1.0, 2.0, 4.0]
    with pytest.raises(httpx.ReadTimeout):  # Five slow tries in a row: the sync stops.
        snapshot.sync(get=lambda *a: (_ for _ in ()).throw(httpx.ReadTimeout("slow")), sleep=lambda s: None)


def test_sync_not_modified_cancelled_and_runaway_cursors():
    def not_modified(url, headers, meta):
        response = httpx.Response(304, request=httpx.Request("GET", url))
        raise httpx.HTTPStatusError("not modified", request=response.request, response=response)
    assert snapshot.sync(since="w", etag='"v1"', get=not_modified)["not_modified"]
    with pytest.raises(ValueError, match="cancelled"):
        snapshot.sync(get=lambda *a: pytest.fail("fetched after cancel"), cancelled=lambda: True)
    loop = json.dumps({"servers": [], "metadata": {"nextCursor": "same"}}).encode()
    with pytest.raises(ValueError, match="cursor"):
        snapshot.sync(get=lambda *a: loop, sleep=lambda s: None)
    forbidden = httpx.Response(403, request=httpx.Request("GET", snapshot.SOURCE))
    with pytest.raises(httpx.HTTPStatusError):
        snapshot.sync(get=lambda *a: (_ for _ in ()).throw(httpx.HTTPStatusError("no", request=forbidden.request, response=forbidden)),
                      sleep=lambda s: pytest.fail("retried a refusal"))


def test_unreviewable_server_stays_listed_without_a_recipe(metadata):
    item = copy.deepcopy(metadata["servers"][0])
    item["server"]["remotes"] *= 17
    entry = marketplace.registry_entries({"servers": [item]})[0]
    assert entry.install is None and "setup_digest" not in entry.metadata and entry.notes
    with pytest.raises(ValueError, match="unsupported"):
        marketplace.entry_to_server_config(entry)


def test_registry_recipe_revalidated_at_publication(local, metadata, monkeypatch):
    entry = marketplace.registry_entries(metadata)[0]
    cfg = marketplace.entry_to_server_config(entry)
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a: metadata["servers"][0])
    snapshot.revalidate_configuration(cfg)
    metadata["servers"][0]["_meta"]["io.modelcontextprotocol.registry/official"]["status"] = "deleted"
    with pytest.raises(ValueError, match="removed"):
        snapshot.revalidate_configuration(cfg)


def test_recipe_identity_keeps_deployments_distinct(local, metadata, monkeypatch):
    second = copy.deepcopy(metadata["servers"][0])
    second["server"]["name"] = "org.other/notes"
    second["server"]["description"] = "The same notes over server-sent events"
    second["server"]["remotes"][0]["type"] = "sse"
    use_registry(monkeypatch, local, marketplace.registry_entries({"servers": [metadata["servers"][0], second]}))
    page = search_catalog("fixture", sources=["official"], query="notes")
    assert len(page["items"]) == 2  # One endpoint over two transports is two deployments, never merged.
    assert page["items"][0]["id"] != page["items"][1]["id"]
    assert all(len(item["attributions"]) == 1 for item in page["items"])
