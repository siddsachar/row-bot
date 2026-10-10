"""The one catalog fetcher, link cleaner, preview cache and file publisher, with fakes only."""
import os
from pathlib import Path

import httpx
import pytest

from row_bot.integrations import safe

ALLOWED = {"catalog.example"}


class Sent(list):
    """Requests that reached a fake server, the proxy each client was given (None: direct),
    and the names resolved for direct connections."""
    proxies: list
    resolved: list


def reached(request: httpx.Request) -> str:
    """The reviewed URL a request was for, also when it was pinned to an address."""
    return "https://" + request.headers["host"] + request.url.path


@pytest.fixture(autouse=True)
def no_system_proxy(monkeypatch):
    """Tests see only the proxy environment they set, never this machine's settings, and no address
    another test found unreachable."""
    monkeypatch.setattr(safe, "_UNREACHABLE", {})
    import urllib.request
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(urllib.request, "getproxies", urllib.request.getproxies_environment)
    monkeypatch.setattr(urllib.request, "proxy_bypass",
                        lambda host: urllib.request.proxy_bypass_environment(host, urllib.request.getproxies_environment()))


@pytest.fixture
def http(monkeypatch):
    """Serve hosts from a handler; every direct connection resolves through a fake DNS."""
    client_type, sent = httpx.Client, Sent()
    sent.proxies = []

    def install(handler, addresses=None):
        resolved = {"catalog.example": ["93.184.216.34"], "mirror.example": ["93.184.216.35"], **(addresses or {})}

        def dispatch(request):
            sent.append(request)
            return handler(request)

        def client(**kwargs):
            assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
            sent.proxies.append(kwargs.pop("proxy", None))
            transport = kwargs.pop("transport", None)
            if isinstance(transport, httpx.HTTPTransport):
                # Keep the pinning transport; only its socket layer is faked.
                monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda self, request: dispatch(request))
                return client_type(transport=transport, **kwargs)
            return client_type(transport=httpx.MockTransport(dispatch), **kwargs)

        monkeypatch.setattr(safe.httpx, "Client", client)
        def resolve(host, *args, **kwargs):
            sent.resolved.append(host)
            return [(2, 1, 6, "", (address, 443)) for address in resolved.get(host, [])]
        sent.resolved = []
        monkeypatch.setattr(safe.socket, "getaddrinfo", resolve)
        return sent

    return install


def test_stopping_the_work_ends_a_fetch_already_reading(http, monkeypatch):
    clock = iter(range(0, 1000))
    monkeypatch.setattr(safe.time, "monotonic", lambda: next(clock) / 2)  # Each read is half a second apart.
    http(lambda request: httpx.Response(200, content=b"x" * 10))
    stopped = []

    def check():
        if stopped:
            raise RuntimeError("plan_cancelled")
    assert safe.fetch("https://catalog.example/a.json", hosts=ALLOWED, max_bytes=10, check=check) == b"x" * 10
    stopped.append(True)
    with safe.checked(check), pytest.raises(RuntimeError, match="plan_cancelled"):
        safe.fetch("https://catalog.example/a.json", hosts=ALLOWED, max_bytes=10)


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
    sent = http(lambda request: httpx.Response(301, headers={"location": mirror}) if reached(request) == source
                else httpx.Response(200, content=b"ok"))
    assert safe.fetch(source, hosts=ALLOWED, max_bytes=10, exact_redirects={source: mirror}) == b"ok"
    assert [reached(r) for r in sent] == [source, mirror]
    sent.clear()
    http(lambda request: httpx.Response(301, headers={"location": mirror}))
    with pytest.raises(ValueError):
        safe.fetch(source, hosts=ALLOWED, max_bytes=10, exact_redirects={source: mirror})
    assert len(sent) == 2


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.8", "169.254.169.254", "192.168.1.1", "::1", "fd00::1",
                                     "::ffff:127.0.0.1", "100.64.0.1", "64:ff9b::7f00:1", "64:ff9b::a00:1", "::7f00:1"])
def test_open_fetch_refuses_names_that_reach_private_networks(http, address):
    sent = http(lambda request: pytest.fail("private address was contacted"), {"skills.example": ["1.1.1.1", address]})
    with pytest.raises(ValueError, match="refused"):
        safe.fetch("https://skills.example/x", hosts=None, max_bytes=10, refused="refused")
    assert sent == []


def test_an_address_that_cannot_be_connected_to_gives_way_to_the_next_checked_one(http):
    """Found live: one of raw.githubusercontent.com's addresses never answered here, and every featured
    skill failed although the others did. Each address is still checked public; TLS and Host keep the name."""
    def handler(request):
        if request.url.host == "93.184.216.34":
            raise httpx.ConnectTimeout("no answer")
        return httpx.Response(200, content=b"ok")
    sent = http(handler, {"catalog.example": ["93.184.216.34", "93.184.216.36"]})
    assert safe.fetch("https://catalog.example/a.json", hosts=ALLOWED, max_bytes=10) == b"ok"
    assert [request.url.host for request in sent] == ["93.184.216.34", "93.184.216.36"]
    # A dead address costs a short wait while another could answer; the last one gets the whole timeout.
    assert [request.extensions["timeout"]["connect"] for request in sent] == [5.0, 20]
    assert {reached(request) for request in sent} == {"https://catalog.example/a.json"}
    assert sent.resolved == ["catalog.example"]  # Resolved once: the same checked answer.
    # A skill is several files: the next fetch tries the address that answered first, not the dead one.
    sent.clear()
    assert safe.fetch("https://catalog.example/b.json", hosts=ALLOWED, max_bytes=10) == b"ok"
    assert [request.url.host for request in sent] == ["93.184.216.36"]


def test_a_request_that_reached_an_address_is_never_sent_again(http):
    """Once connected, the request may have been seen: a later failure is not retried elsewhere."""
    def handler(request):
        raise httpx.ReadTimeout("slow")
    sent = http(handler, {"catalog.example": ["93.184.216.34", "93.184.216.36"]})
    with pytest.raises(httpx.ReadTimeout):
        safe.fetch("https://catalog.example/a.json", hosts=ALLOWED, max_bytes=10)
    assert [request.url.host for request in sent] == ["93.184.216.34"]


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
    assert strict.get("b") == 2


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


def test_write_atomic_keeps_a_write_that_windows_briefly_holds(tmp_path, monkeypatch):
    """Antivirus can hold a just-written file for a moment (WinError 5): the write lands, not lost."""
    target, real, refused = tmp_path / "state.json", safe.os.replace, []

    def replace_once_refused(source, destination):
        if not refused:
            refused.append(destination)
            error = PermissionError(13, "Access is denied")
            error.winerror = 5
            raise error
        real(source, destination)

    monkeypatch.setattr(safe.os, "replace", replace_once_refused)
    safe.write_atomic(target, "saved")
    assert refused and target.read_text() == "saved"
    assert [path.name for path in tmp_path.iterdir()] == ["state.json"]


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


def test_compressed_bodies_are_refused_so_the_size_cap_holds(http):
    sent = http(lambda request: httpx.Response(200, headers={"content-encoding": "gzip"}, content=b"\x1f\x8b"))
    with pytest.raises(ValueError, match="refused"):
        safe.fetch("https://catalog.example/a.json", hosts=ALLOWED, max_bytes=10, refused="refused")
    assert sent[0].headers["accept-encoding"] == "identity"


def test_a_slow_source_stops_at_one_overall_deadline(http, monkeypatch):
    from itertools import count
    http(lambda request: httpx.Response(200, content=(b"x" for _ in range(5))))
    ticks = count(0, 100)
    monkeypatch.setattr(safe.time, "monotonic", lambda: float(next(ticks)))
    with pytest.raises(TimeoutError):
        safe.fetch("https://catalog.example/a.json", hosts=ALLOWED, max_bytes=10, timeout=20)


PROXY = "http://user:proxy-secret@proxy.corp.example:3128"


def test_reviewed_hosts_go_through_the_proxy_and_open_fetches_never_do(http, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", PROXY)
    sent = http(lambda request: httpx.Response(200, content=b"ok"), {"open.example": ["93.184.216.36"]})
    assert safe.fetch("https://catalog.example/a.json", hosts=ALLOWED, max_bytes=10) == b"ok"
    assert sent.proxies == [PROXY] and sent.resolved == []  # The proxy resolves reviewed names.
    assert safe.fetch("https://open.example/a.json", hosts=None, max_bytes=10) == b"ok"
    assert sent.proxies == [PROXY, None] and sent.resolved == ["open.example"]  # Direct, to a checked address.
    assert all("proxy-authorization" not in r.headers and "proxy-secret" not in str(r.headers) for r in sent)


def test_proxy_is_chosen_again_on_every_redirect_hop_and_honours_no_proxy(http, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", PROXY)
    monkeypatch.setenv("NO_PROXY", "mirror.example")
    source, mirror = "https://catalog.example/a.json", "https://mirror.example/a.json"
    sent = http(lambda request: httpx.Response(301, headers={"location": mirror}) if reached(request) == source
                else httpx.Response(200, content=b"ok"))
    assert safe.fetch(source, hosts=ALLOWED, max_bytes=10, exact_redirects={source: mirror}) == b"ok"
    assert sent.proxies == [PROXY, None] and sent.resolved == ["mirror.example"]


@pytest.mark.parametrize("url", ["http://catalog.example/a.json", "https://other.example/a.json",
                                 "https://catalog.example:8443/a.json"])
def test_a_proxy_never_relaxes_https_the_allow_list_or_the_port(http, monkeypatch, url):
    monkeypatch.setenv("HTTPS_PROXY", PROXY)
    sent = http(lambda request: pytest.fail("refused URL was requested"))
    with pytest.raises(ValueError, match="refused"):
        safe.fetch(url, hosts=ALLOWED, max_bytes=10, refused="refused")
    assert sent == [] and sent.proxies == []


def test_a_proxy_still_bounds_size_and_strips_credentials_across_hosts(http, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", PROXY)
    source, mirror = "https://catalog.example/a.json", "https://mirror.example/a.json"
    sent = http(lambda request: httpx.Response(301, headers={"location": mirror}) if reached(request) == source
                else httpx.Response(200, content=b"x" * 11))
    with pytest.raises(ValueError, match="too_large"):
        safe.fetch(source, hosts=ALLOWED | {"mirror.example"}, max_bytes=10, redirects=1, too_large="too_large",
                   headers={"Authorization": "Bearer catalog-token"})
    assert sent.proxies == [PROXY, PROXY]
    assert sent[0].headers["authorization"] == "Bearer catalog-token" and "authorization" not in sent[1].headers


def test_a_proxy_written_without_a_scheme_is_an_http_proxy(http, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "proxy.corp.example:3128")
    sent = http(lambda request: httpx.Response(200, content=b"ok"))
    safe.fetch("https://catalog.example/a.json", hosts=ALLOWED, max_bytes=10)
    assert sent.proxies == ["http://proxy.corp.example:3128"]


@pytest.mark.parametrize("proxy", ["socks5://proxy.corp.example:1080", "ftp://proxy.corp.example", "http://:3128",
                                   "http://proxy.corp.example:notaport"])
def test_unusable_proxy_settings_fall_back_to_a_checked_direct_connection(http, monkeypatch, proxy):
    monkeypatch.setenv("HTTPS_PROXY", proxy)
    sent = http(lambda request: httpx.Response(200, content=b"ok"))
    assert safe.fetch("https://catalog.example/a.json", hosts=ALLOWED, max_bytes=10) == b"ok"
    assert sent.proxies == [None] and sent.resolved == ["catalog.example"]


def test_system_proxy_settings_are_used_for_reviewed_hosts(http, monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"https": "http://system.proxy.example:8080"})
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: host == "mirror.example")
    sent = http(lambda request: httpx.Response(200, content=b"ok"))
    safe.fetch("https://catalog.example/a.json", hosts=ALLOWED, max_bytes=10)
    safe.fetch("https://mirror.example/a.json", hosts={"mirror.example"}, max_bytes=10)
    assert sent.proxies == ["http://system.proxy.example:8080", None]


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.8", "169.254.169.254", "::1"])
def test_a_direct_reviewed_host_must_resolve_to_a_public_address(http, address):
    sent = http(lambda request: pytest.fail("private address was contacted"), {"catalog.example": [address]})
    with pytest.raises(ValueError, match="refused"):
        safe.fetch("https://catalog.example/a.json", hosts=ALLOWED, max_bytes=10, refused="refused")
    assert sent == []
