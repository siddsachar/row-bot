"""The one catalog fetcher, link cleaner, preview cache and file publisher, with fakes only."""
import os
from pathlib import Path

import httpx
import pytest

from row_bot.integrations import safe

ALLOWED = {"catalog.example"}


@pytest.fixture
def http(monkeypatch):
    """Serve reviewed hosts from a handler; open fetches resolve through a fake DNS."""
    client_type, sent = httpx.Client, []

    def install(handler, addresses=None):
        def dispatch(request):
            sent.append(request)
            return handler(request)

        def client(**kwargs):
            assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
            transport = kwargs.pop("transport", None)
            if isinstance(transport, httpx.HTTPTransport):
                # Keep the pinning transport; only its socket layer is faked.
                monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda self, request: dispatch(request))
                return client_type(transport=transport, **kwargs)
            return client_type(transport=httpx.MockTransport(dispatch), **kwargs)

        monkeypatch.setattr(safe.httpx, "Client", client)
        monkeypatch.setattr(safe.socket, "getaddrinfo", lambda host, *a, **k: [
            (2, 1, 6, "", (address, 443)) for address in (addresses or {}).get(host, [])])
        return sent

    return install


def test_reviewed_host_is_bounded_and_never_follows_redirects(http):
    sent = http(lambda request: httpx.Response(200, content=b"x" * 10))
    assert safe.fetch("https://catalog.example/a.json", hosts=ALLOWED, max_bytes=10) == b"x" * 10
    with pytest.raises(ValueError, match="too_large"):
        safe.fetch("https://catalog.example/a.json", hosts=ALLOWED, max_bytes=9, too_large="too_large")
    sent.clear()
    http(lambda request: httpx.Response(302, headers={"location": "https://catalog.example/b.json"}))
    with pytest.raises(ValueError, match="refused"):
        safe.fetch("https://catalog.example/a.json", hosts=ALLOWED, max_bytes=10, refused="refused")
    assert len(sent) == 1


@pytest.mark.parametrize("url", [
    "http://catalog.example/a.json", "https://user:pass@catalog.example/a.json", "https://catalog.example:8443/a.json",
    "https://catalog.example/a.json#fragment", "https://other.example/a.json", "file:///etc/passwd", "",
])
def test_unreviewed_urls_are_refused_before_any_request(http, url):
    sent = http(lambda request: pytest.fail("refused URL was requested"))
    with pytest.raises(ValueError, match="refused"):
        safe.fetch(url, hosts=ALLOWED, max_bytes=10, refused="refused")
    assert sent == []


def test_exact_reviewed_migration_is_followed_once_and_only_once(http):
    source, mirror = "https://catalog.example/a.json", "https://mirror.example/a.json"
    sent = http(lambda request: httpx.Response(301, headers={"location": mirror}) if str(request.url) == source
                else httpx.Response(200, content=b"ok"))
    assert safe.fetch(source, hosts=ALLOWED, max_bytes=10, exact_redirects={source: mirror}) == b"ok"
    assert [str(r.url) for r in sent] == [source, mirror]
    sent.clear()
    http(lambda request: httpx.Response(301, headers={"location": mirror}))
    with pytest.raises(ValueError):
        safe.fetch(source, hosts=ALLOWED, max_bytes=10, exact_redirects={source: mirror})
    assert len(sent) == 2


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.8", "169.254.169.254", "192.168.1.1", "::1", "fd00::1",
                                     "::ffff:127.0.0.1", "100.64.0.1"])
def test_open_fetch_refuses_names_that_reach_private_networks(http, address):
    sent = http(lambda request: pytest.fail("private address was contacted"), {"skills.example": ["1.1.1.1", address]})
    with pytest.raises(ValueError, match="refused"):
        safe.fetch("https://skills.example/x", hosts=None, max_bytes=10, refused="refused")
    assert sent == []


@pytest.mark.parametrize("host", ["localhost", "printer.local", "svc.internal", "app.localhost"])
def test_open_fetch_refuses_local_names_without_resolving(http, monkeypatch, host):
    http(lambda request: pytest.fail("local name was contacted"))
    monkeypatch.setattr(safe.socket, "getaddrinfo", lambda *a, **k: pytest.fail("local name was resolved"))
    with pytest.raises(ValueError):
        safe.fetch(f"https://{host}/x", hosts=None, max_bytes=10)


def test_open_fetch_pins_the_checked_address_and_keeps_the_name_for_tls(http):
    sent = http(lambda request: httpx.Response(200, content=b"ok"), {"skills.example": ["1.1.1.1"]})
    assert safe.fetch("https://skills.example/x", hosts=None, max_bytes=10) == b"ok"
    assert sent[0].url.host == "1.1.1.1"
    assert sent[0].headers["host"] == "skills.example"
    assert sent[0].extensions["sni_hostname"] == "skills.example"


def test_redirects_are_rechecked_and_credentials_never_cross_hosts(http):
    def respond(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/same"})
        if request.url.path == "/same":
            return httpx.Response(302, headers={"location": "https://cdn.example/file"})
        return httpx.Response(200, content=b"ok")

    sent = http(respond, {"api.example": ["1.1.1.1"], "cdn.example": ["8.8.8.8"]})
    headers = {"Authorization": "Bearer fixture-token", "Cookie": "a=b", "Accept": "application/json"}
    assert safe.fetch("https://api.example/start", hosts=None, max_bytes=10, headers=headers, redirects=2) == b"ok"
    assert [r.headers.get("authorization") for r in sent] == ["Bearer fixture-token", "Bearer fixture-token", None]
    assert sent[2].headers.get("cookie") is None and sent[2].headers["accept"] == "application/json"
    sent.clear()
    http(lambda request: httpx.Response(302, headers={"location": "https://evil.example/"}),
         {"api.example": ["1.1.1.1"], "evil.example": ["127.0.0.1"]})
    with pytest.raises(ValueError):
        safe.fetch("https://api.example/start", hosts=None, max_bytes=10, redirects=5)
    assert len(sent) == 1


def test_redirect_budget_is_enforced(http):
    sent = http(lambda request: httpx.Response(302, headers={"location": "/again"}), {"api.example": ["1.1.1.1"]})
    with pytest.raises(ValueError):
        safe.fetch("https://api.example/start", hosts=None, max_bytes=10, redirects=2)
    assert len(sent) == 3


@pytest.mark.parametrize(("value", "expected"), [
    ("https://example.com/a?b=c", "https://example.com/a?b=c"), (" https://example.com ", "https://example.com"),
    ("http://example.com", ""), ("https://user@example.com", ""), ("https://u:p@example.com", ""),
    ("javascript:alert(1)", ""), ("https://example.com/\nx", ""), ("https://" + "a" * 3000 + ".com", ""), (None, ""),
])
def test_public_url_keeps_only_credential_free_https_links(value, expected):
    assert safe.public_url(value) == expected


def test_ttl_cache_expires_evicts_and_can_refuse_when_full():
    now = [0.0]
    cache = safe.TtlCache(10, 2, clock=lambda: now[0])
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)
    assert (cache.get("a"), cache.get("b"), cache.get("c")) == (None, 2, 3)
    now[0] = 11
    assert cache.get("b") is None
    strict = safe.TtlCache(10, 1, full="preview_capacity", clock=lambda: now[0])
    strict.put("a", 1)
    with pytest.raises(ValueError, match="preview_capacity"):
        strict.room()
    now[0] = 30
    strict.room()
    strict.put("b", 2)
    assert strict.pop("b") == 2 and strict.get("b") is None


def test_write_atomic_publishes_whole_files_and_cleans_only_its_own_temporary(tmp_path, monkeypatch):
    target = tmp_path / "cache.json"
    target.write_text("old")
    unrelated = tmp_path / "cache.json.unrelated.tmp"
    unrelated.write_text("keep")
    with pytest.raises(ValueError, match="cancelled"):
        safe.write_atomic(target, "new", cancelled=lambda: True)
    replace = os.replace
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("synthetic")))
    with pytest.raises(OSError):
        safe.write_atomic(target, "new")
    monkeypatch.setattr(os, "replace", replace)
    assert target.read_text() == "old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cache.json", "cache.json.unrelated.tmp"]
    safe.write_atomic(tmp_path / "nested" / "value.json", b"{}")
    assert (tmp_path / "nested" / "value.json").read_bytes() == b"{}"


def test_write_atomic_never_publishes_through_a_link(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("private")
    link = tmp_path / "cache.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symbolic links are unavailable to this account")
    with pytest.raises(OSError):
        safe.write_atomic(link, "replaced")
    assert outside.read_text() == "private" and Path(link).is_symlink()


def test_an_unresolvable_name_is_unreachable_not_refused(http, monkeypatch):
    http(lambda request: pytest.fail("nothing is contacted"))

    def offline(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(safe.socket, "getaddrinfo", offline)
    with pytest.raises(ConnectionError):
        safe.fetch("https://skills.example/x", hosts=None, max_bytes=10)
