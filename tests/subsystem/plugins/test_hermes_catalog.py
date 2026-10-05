"""Hermes discovery uses bounded public downloads and survives source failures."""
import json

import httpx
import pytest

from row_bot.integrations import safe
from row_bot.plugins import hermes_catalog

pytestmark = pytest.mark.platform

CATALOG = "https://hermes-agent.nousresearch.com/docs/api/plugin-catalog.json"
MIRROR = "https://nousresearch.github.io/hermes-agent/docs/api/plugin-catalog.json"
ENTRY = {"name": "notes", "repo": "https://github.com/example/notes", "sha": "a" * 40}
DOCUMENT = {"entries": [ENTRY], "removed": []}


@pytest.fixture
def catalog_http(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    client_type = httpx.Client
    requests = []

    def install(handler):
        def dispatch(request):
            requests.append(request)
            assert "authorization" not in request.headers
            assert "cookie" not in request.headers
            return handler(request)

        def client(**kwargs):
            assert kwargs["follow_redirects"] is False
            assert kwargs["trust_env"] is False
            kwargs.pop("transport", None)  # The address-pinning transport; its checks are tested with the fetcher.
            return client_type(transport=httpx.MockTransport(dispatch), **kwargs)

        monkeypatch.setattr(hermes_catalog.httpx, "Client", client)
        monkeypatch.setattr(safe.socket, "getaddrinfo", lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
        return requests

    return install


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_catalog_follows_only_reviewed_migration_and_then_reads_cache(catalog_http, status):
    def respond(request):
        if str(request.url) == CATALOG:
            return httpx.Response(status, headers={"location": MIRROR, "set-cookie": "tracking=refused; Path=/"})
        assert str(request.url) == MIRROR
        return httpx.Response(200, json=DOCUMENT)

    requests = catalog_http(respond)
    result = hermes_catalog.read_catalog(refresh=True)
    assert result["status"] == "live"
    assert result["entries"][0]["pin"] == "a" * 40
    assert result["entries"][0]["compatibility"] == "not_inspected"
    assert hermes_catalog.read_catalog()["status"] == "cached"
    assert [str(request.url) for request in requests] == [CATALOG, MIRROR]


@pytest.mark.parametrize("destination", [
    "http://nousresearch.github.io/hermes-agent/docs/api/plugin-catalog.json",
    "https://nousresearch.github.io/other/catalog.json",
    MIRROR + "?token=unreviewed",
    MIRROR + "#fragment",
    MIRROR.replace("https://", "https://user:password@"),
    MIRROR.replace(".io/", ".io:444/"),
    "https://nousresearch.github.io.evil.test/hermes-agent/docs/api/plugin-catalog.json",
    "https://127.0.0.1/catalog.json",
    "https://169.254.169.254/latest/meta-data/",
    "https://raw.githubusercontent.com/example/notes/main/plugin.json",
    CATALOG,
    "",
])
def test_unreviewed_redirect_never_requests_destination(catalog_http, destination):
    requests = catalog_http(lambda request: httpx.Response(302, headers={"location": destination}))
    result = hermes_catalog.read_catalog(refresh=True)
    assert result["status"] == "error"
    assert len(requests) == 1


def test_second_catalog_redirect_is_refused(catalog_http):
    requests = catalog_http(lambda request: httpx.Response(301, headers={"location": MIRROR}))
    assert hermes_catalog.read_catalog(refresh=True)["status"] == "error"
    assert len(requests) == 2


def test_package_archive_redirect_is_not_enabled_by_catalog_exception(catalog_http):
    requests = catalog_http(lambda request: httpx.Response(302, headers={"location": MIRROR}))
    with pytest.raises((ValueError, httpx.HTTPStatusError)):
        hermes_catalog.inspect_package(owner_id="fixture", reference="https://github.com/example/notes#" + "a" * 40)
    assert len(requests) == 1
    assert requests[0].url.host == "codeload.github.com"


@pytest.mark.parametrize("failure", ["oversized", "invalid_json", "invalid_shape", "rate_limit", "offline"])
def test_refresh_failure_preserves_previous_cache(catalog_http, tmp_path, failure):
    catalog_http(lambda request: httpx.Response(200, json=DOCUMENT))
    assert hermes_catalog.read_catalog(refresh=True)["status"] == "live"
    cache = tmp_path / "hermes_catalog_cache.json"
    before = cache.read_bytes()

    def respond(request):
        if str(request.url) == CATALOG:
            return httpx.Response(301, headers={"location": MIRROR})
        if failure == "oversized":
            return httpx.Response(200, content=b"x" * (4 * 1024 * 1024 + 1))
        if failure == "invalid_json":
            return httpx.Response(200, content=b"not json")
        if failure == "invalid_shape":
            return httpx.Response(200, json={"entries": [], "removed": {}})
        if failure == "rate_limit":
            return httpx.Response(429, headers={"Retry-After": "30"})
        raise httpx.ConnectError("offline", request=request)

    catalog_http(respond)
    result = hermes_catalog.read_catalog(refresh=True)
    assert result["status"] == "stale"
    assert result["entries"][0]["name"] == "notes"
    if failure == "rate_limit":
        assert "30" in result["message"]
    assert cache.read_bytes() == before


@pytest.mark.parametrize("saved", [[], [ENTRY], None, {"entries": None, "removed": []}, {"entries": [], "removed": None}])
def test_malformed_cache_can_be_repaired_by_explicit_refresh(catalog_http, tmp_path, saved):
    (tmp_path / "hermes_catalog_cache.json").write_text(json.dumps(saved), encoding="utf-8")
    requests = catalog_http(lambda request: httpx.Response(200, json=DOCUMENT))
    assert hermes_catalog.read_catalog()["status"] == "empty"
    assert not requests
    assert hermes_catalog.read_catalog(refresh=True)["status"] == "live"


def test_an_update_classifies_each_pin_from_its_manifest_only_and_never_twice(catalog_http, tmp_path):
    from row_bot.plugins.portable import SCHEMA
    entries = [{"name": "portable", "repo": "https://github.com/example/portable", "sha": "b" * 40},
               {"name": "native", "repo": "https://github.com/example/native", "sha": "c" * 40},
               {"name": "throttled", "repo": "https://github.com/example/throttled", "sha": "d" * 40}]
    (tmp_path / "hermes_catalog_cache.json").write_text(json.dumps({"entries": entries, "removed": []}), encoding="utf-8")
    asked = []

    def read(url):
        asked.append(url)
        if "/portable/" in url:
            return json.dumps({"$schema": SCHEMA, "name": "portable"}).encode()
        status = 404 if "/native/" in url else 429
        raise httpx.HTTPStatusError("no", request=httpx.Request("GET", url), response=httpx.Response(status))
    assert hermes_catalog.classify(read=read, pause=0) == {"portable": 1, "native": 1}
    assert asked == [f"https://raw.githubusercontent.com/example/{name}/{pin * 40}/plugin.json"
                     for name, pin in (("portable", "b"), ("native", "c"), ("throttled", "d"))]
    rows = {e["name"]: e for e in hermes_catalog.read_catalog()["entries"]}
    assert rows["native"]["compatibility"] == "unsupported" and "no Agent Plugins manifest" in rows["native"]["reason"]
    assert rows["portable"]["portable"] and rows["portable"]["compatibility"] == "not_inspected"
    assert rows["throttled"]["compatibility"] == "not_inspected" and not rows["throttled"]["portable"]
    asked.clear()
    hermes_catalog.classify(read=read, pause=0)
    assert asked == [asked[0]] and "/throttled/" in asked[0]  # Known pins are never read again.
