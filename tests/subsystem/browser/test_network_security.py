from __future__ import annotations

from types import SimpleNamespace

import pytest

from row_bot.browser.network_security import BrowserNetworkSecurity


class _Resolver:
    def __init__(self, values: dict[tuple[str, int], tuple[str, ...]]) -> None:
        self.values = values
        self.calls: list[tuple[str, int]] = []

    def __call__(self, host: str, port: int) -> tuple[str, ...]:
        self.calls.append((host, port))
        value = self.values.get((host, port))
        if value is None:
            raise OSError("synthetic DNS failure")
        return value


def test_all_dns_answers_must_be_public_and_ipv4_mapped_ipv6_is_normalized() -> None:
    resolver = _Resolver(
        {
            ("public.test", 443): ("8.8.8.8", "1.1.1.1"),
            ("mixed.test", 443): ("8.8.8.8", "10.0.0.7"),
            ("mapped.test", 80): ("::ffff:127.0.0.1",),
        }
    )
    policy = BrowserNetworkSecurity(resolver=resolver)

    public = policy.authorize(
        "https://public.test/frame",
        browser_session_id="browser-a",
    )
    mixed = policy.authorize(
        "https://mixed.test/image.png",
        browser_session_id="browser-a",
    )
    mapped = policy.authorize(
        "http://mapped.test/socket",
        browser_session_id="browser-a",
    )

    assert public.allowed is True
    assert public.destinations == frozenset({"8.8.8.8", "1.1.1.1"})
    assert mixed.code == "restricted_destination"
    assert mapped.code == "restricted_destination"
    assert mapped.destinations == frozenset({"127.0.0.1"})
    with pytest.raises(ValueError, match="literal IP"):
        policy.grant_local_development(
            browser_session_id="browser-a",
            origin="https://mixed.test",
            intended_use="Review the mixed-address development host",
            ttl_seconds=60,
        )


def test_dns_address_changes_fail_closed_before_redirect_dispatch() -> None:
    answers = {("rebind.test", 443): ("8.8.8.8",)}
    policy = BrowserNetworkSecurity(
        resolver=lambda host, port: answers[(host, port)],
    )

    first = policy.authorize(
        "https://rebind.test/start",
        browser_session_id="browser-a",
    )
    answers[("rebind.test", 443)] = ("127.0.0.1",)
    redirected = policy.authorize(
        "https://rebind.test/redirected",
        browser_session_id="browser-a",
    )

    assert first.allowed is True
    assert redirected.allowed is False
    assert redirected.code == "dns_changed"


def test_metadata_management_and_non_http_targets_are_never_grantable() -> None:
    resolver = _Resolver(
        {
            ("metadata.google.internal", 80): ("169.254.169.254",),
            ("dev.test", 8080): ("169.254.169.254",),
            ("public.test", 443): ("8.8.8.8",),
            ("azure-metadata.test", 80): ("168.63.129.16",),
        }
    )
    policy = BrowserNetworkSecurity(resolver=resolver)

    assert (
        policy.authorize(
            "http://metadata.google.internal/computeMetadata/v1/",
            browser_session_id="browser-a",
        ).code
        == "metadata_blocked"
    )
    assert (
        policy.authorize(
            "http://169.254.169.254/latest/meta-data/",
            browser_session_id="browser-a",
        ).code
        == "metadata_blocked"
    )
    assert (
        policy.authorize(
            "https://public.test/api/access/session",
            browser_session_id="browser-a",
        ).code
        == "management_blocked"
    )
    assert (
        policy.authorize(
            "https://public.test/%2561pi%252faccess/session",
            browser_session_id="browser-a",
        ).code
        == "management_blocked"
    )
    assert (
        policy.authorize(
            "http://azure-metadata.test/metadata/instance",
            browser_session_id="browser-a",
        ).code
        == "metadata_blocked"
    )
    assert (
        policy.authorize(
            "file:///etc/passwd",
            browser_session_id="browser-a",
        ).code
        == "invalid_url"
    )
    with pytest.raises(ValueError, match="metadata"):
        policy.grant_local_development(
            browser_session_id="browser-a",
            origin="http://169.254.169.254:8080",
            intended_use="Local test server",
            ttl_seconds=60,
        )


def test_local_development_grant_is_exact_expiring_and_revocable() -> None:
    clock = [100.0]
    policy = BrowserNetworkSecurity(
        clock=lambda: clock[0],
    )

    blocked = policy.authorize(
        "http://192.168.1.20:8080/app",
        browser_session_id="browser-a",
    )
    grant = policy.grant_local_development(
        browser_session_id="browser-a",
        origin="http://192.168.1.20:8080",
        intended_use="Preview the local development server",
        ttl_seconds=30,
    )

    assert blocked.code == "restricted_destination"
    assert grant.intended_use == "Preview the local development server"
    assert (
        policy.authorize(
            "http://192.168.1.20:8080/app.js",
            browser_session_id="browser-a",
        ).allowed
        is True
    )
    write = policy.authorize(
        "http://192.168.1.20:8080/upload",
        browser_session_id="browser-a",
        method="POST",
    )
    assert write.allowed is False
    assert write.code == "consequential_private_request"
    assert (
        policy.authorize(
            "http://192.168.1.20:8081/app.js",
            browser_session_id="browser-a",
        ).allowed
        is False
    )
    assert (
        policy.authorize(
            "http://192.168.1.20:8080/app.js",
            browser_session_id="browser-b",
        ).allowed
        is False
    )

    assert policy.revoke_grant(grant.id) is True
    assert (
        policy.authorize(
            "http://192.168.1.20:8080/app.js",
            browser_session_id="browser-a",
        ).allowed
        is False
    )

    replacement = policy.grant_local_development(
        browser_session_id="browser-a",
        origin="http://192.168.1.20:8080",
        intended_use="Preview the local development server",
        ttl_seconds=30,
    )
    clock[0] = replacement.expires_at
    assert (
        policy.authorize(
            "http://192.168.1.20:8080/app.js",
            browser_session_id="browser-a",
        ).allowed
        is False
    )
    with pytest.raises(ValueError, match="lifetime"):
        policy.grant_local_development(
            browser_session_id="browser-a",
            origin="http://192.168.1.20:8080",
            intended_use="Too broad",
            ttl_seconds=3601,
        )


def test_browser_session_end_revokes_all_local_development_grants() -> None:
    policy = BrowserNetworkSecurity(
        resolver=lambda _host, _port: ("127.0.0.1",),
    )
    policy.grant_local_development(
        browser_session_id="browser-a",
        origin="http://127.0.0.1:3000",
        intended_use="Preview the local application",
        ttl_seconds=60,
    )

    assert policy.authorize(
        "http://127.0.0.1:3000/",
        browser_session_id="browser-a",
    ).allowed
    assert policy.revoke_session("browser-a") == 1
    assert not policy.authorize(
        "http://127.0.0.1:3000/",
        browser_session_id="browser-a",
    ).allowed


class _Route:
    def __init__(self, url: str) -> None:
        self.request = SimpleNamespace(url=url)
        self.outcome = ""

    def continue_(self) -> None:
        self.outcome = "continued"

    def abort(self, reason: str) -> None:
        self.outcome = reason


class _WebSocketRoute:
    def __init__(self, url: str) -> None:
        self.url = url
        self.outcome = ""

    def connect_to_server(self) -> None:
        self.outcome = "connected"

    def close(self, *, code: int, reason: str) -> None:
        self.outcome = f"closed:{code}:{reason}"


class _Download:
    def __init__(self, url: str) -> None:
        self.url = url
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _Page:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    def on(self, event: str, callback) -> None:
        self.handlers[event] = callback


class _Context:
    def __init__(self) -> None:
        self.pages = [_Page()]
        self.handlers: dict[str, object] = {}
        self.route_handler = None
        self.websocket_handler = None

    def route(self, pattern: str, callback) -> None:
        assert pattern == "**/*"
        self.route_handler = callback

    def route_web_socket(self, pattern: str, callback) -> None:
        assert pattern == "**/*"
        self.websocket_handler = callback

    def on(self, event: str, callback) -> None:
        self.handlers[event] = callback


def test_engine_hooks_cover_redirect_subresource_websocket_and_download() -> None:
    resolver = _Resolver(
        {
            ("public.test", 443): ("8.8.8.8",),
            ("private.test", 443): ("10.0.0.7",),
        }
    )
    policy = BrowserNetworkSecurity(resolver=resolver)
    context = _Context()
    policy.install_context(context, browser_session_id="browser-a")

    public_document = _Route("https://public.test/start")
    redirect_to_private = _Route("https://private.test/redirect")
    private_frame = _Route("https://private.test/frame")
    private_subresource = _Route("https://private.test/app.js")
    for route in (
        public_document,
        redirect_to_private,
        private_frame,
        private_subresource,
    ):
        context.route_handler(route)

    public_ws = _WebSocketRoute("wss://public.test/socket")
    private_ws = _WebSocketRoute("wss://private.test/socket")
    context.websocket_handler(public_ws)
    context.websocket_handler(private_ws)
    private_download = _Download("https://private.test/archive.zip")
    context.pages[0].handlers["download"](private_download)

    assert public_document.outcome == "continued"
    assert redirect_to_private.outcome == "blockedbyclient"
    assert private_frame.outcome == "blockedbyclient"
    assert private_subresource.outcome == "blockedbyclient"
    assert public_ws.outcome == "connected"
    assert private_ws.outcome.startswith("closed:1008")
    assert private_download.cancelled is True


def test_private_subresource_needs_its_own_exact_origin_grant() -> None:
    resolver = _Resolver({("public.test", 443): ("8.8.8.8",)})
    policy = BrowserNetworkSecurity(resolver=resolver)
    context = _Context()
    policy.install_context(context, browser_session_id="browser-a")

    before = _Route("https://10.0.0.8:8443/app.js")
    context.route_handler(before)
    policy.grant_local_development(
        browser_session_id="browser-a",
        origin="https://10.0.0.8:8443",
        intended_use="Load assets for the reviewed local preview",
        ttl_seconds=60,
    )
    after = _Route("https://10.0.0.8:8443/app.js")
    context.route_handler(after)

    assert before.outcome == "blockedbyclient"
    assert after.outcome == "continued"
