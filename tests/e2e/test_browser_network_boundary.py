from __future__ import annotations

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
import pytest

from row_bot.browser.network_proxy import PinnedNetworkProxy
from row_bot.browser.network_security import BrowserNetworkSecurity
from row_bot.browser.runtime import (
    check_managed_browser_runtime,
    check_packaged_browser_runtime,
)
from row_bot.browser.service import _installed_channel


pytestmark = pytest.mark.e2e


class _EngineOrigin(BaseHTTPRequestHandler):
    hits: list[tuple[str, str]] = []
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        self.hits.append(("GET", self.path))
        if self.headers.get("Upgrade", "").casefold() == "websocket":
            key = self.headers["Sec-WebSocket-Key"]
            accept = base64.b64encode(
                hashlib.sha1(
                    (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
                ).digest()
            ).decode()
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            self.connection.settimeout(5)
            try:
                self.connection.recv(4096)
                self.connection.sendall(b"\x88\x00")
            except OSError:
                pass
            return
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/frame")
            self.end_headers()
            return
        if self.path == "/download":
            body = b"contained download"
            self.send_response(200)
            self.send_header("Content-Disposition", "attachment; filename=fixture.txt")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/app.js":
            body = b"window.engineSubresourceLoaded = true;"
            content_type = "text/javascript"
        elif self.path == "/frame":
            body = b"<!doctype html><title>frame</title>"
            content_type = "text/html"
        else:
            body = (
                b"<!doctype html><script src='/app.js'></script>"
                b"<iframe src='/frame'></iframe>"
                b"<a id='download' href='/download' download>download</a>"
            )
            content_type = "text/html"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        self.hits.append(("POST", self.path))
        self.send_response(204)
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


def test_actual_chromium_enforces_pinned_network_boundary() -> None:
    channel = _installed_channel()
    executable_path: str | None = None
    if channel is None:
        readiness = check_packaged_browser_runtime()
        if not readiness.ready:
            readiness = check_managed_browser_runtime()
        if not readiness.ready or not readiness.executable_path:
            pytest.skip("reviewed managed Chromium runtime is unavailable")
        executable_path = readiness.executable_path

    _EngineOrigin.hits = []
    origin = ThreadingHTTPServer(("127.0.0.1", 0), _EngineOrigin)
    origin_thread = threading.Thread(target=origin.serve_forever, daemon=True)
    origin_thread.start()
    port = origin.server_address[1]
    resolver_calls = 0

    def resolver(host: str, requested_port: int) -> tuple[str, ...]:
        nonlocal resolver_calls
        assert (host, requested_port) == ("rebind.test", port)
        resolver_calls += 1
        return ("8.8.8.8",) if resolver_calls == 1 else ("127.0.0.1",)

    policy = BrowserNetworkSecurity(resolver=resolver)
    session_id = "actual-engine"
    proxy = PinnedNetworkProxy(policy, browser_session_id=session_id)
    approved_requests: list[tuple[str, str]] = []

    def approve_request(url: str, method: str) -> None:
        approved_requests.append((method, url))
        proxy.approve_request(url, method)

    proxy.start()
    browser = None
    context = None
    try:
        with sync_playwright() as playwright:
            launch_identity = (
                {"channel": channel}
                if channel is not None
                else {"executable_path": executable_path}
            )
            browser = playwright.chromium.launch(
                **launch_identity,
                headless=True,
                args=[
                    "--proxy-bypass-list=<-loopback>",
                    "--host-resolver-rules=MAP rebind.test 127.0.0.1",
                ],
            )
            context = browser.new_context(
                proxy=proxy.playwright_config,
                service_workers="block",
                accept_downloads=True,
            )
            policy.install_context(
                context,
                browser_session_id=session_id,
                on_allowed=approve_request,
            )
            page = context.new_page()
            local_url = f"http://127.0.0.1:{port}"

            with pytest.raises(PlaywrightError):
                page.goto(local_url, wait_until="domcontentloaded")
            assert _EngineOrigin.hits == []
            page.close()
            page = context.new_page()

            grant = policy.grant_local_development(
                browser_session_id=session_id,
                origin=local_url,
                intended_use="Contained actual-engine security evidence",
                ttl_seconds=60,
            )
            response = page.goto(local_url, wait_until="networkidle")
            assert response is not None and response.status == 200
            assert page.evaluate("window.engineSubresourceLoaded") is True
            assert {path for method, path in _EngineOrigin.hits if method == "GET"}.issuperset(
                {"/", "/app.js", "/frame"}
            )

            assert page.evaluate(
                """async () => {
                  try { await fetch('/write', {method: 'POST'}); return 'sent'; }
                  catch { return 'blocked'; }
                }"""
            ) == "blocked"
            assert ("POST", "/write") not in _EngineOrigin.hits

            redirected = page.goto(f"{local_url}/redirect", wait_until="domcontentloaded")
            assert redirected is not None and redirected.url.endswith("/frame")

            page.goto(local_url, wait_until="domcontentloaded")
            with page.expect_download() as download_event:
                page.locator("#download").click()
            download = download_event.value
            assert download.suggested_filename.endswith(".txt")
            download_approvals = [
                entry for entry in approved_requests if entry[1].endswith("/download")
            ]
            assert download_approvals == []
            assert ("GET", "/download") in _EngineOrigin.hits
            download.cancel()

            policy.grant_local_development(
                browser_session_id=session_id,
                origin=f"ws://127.0.0.1:{port}",
                intended_use="Contained actual-engine WebSocket evidence",
                ttl_seconds=60,
            )
            websocket_result = page.evaluate(
                f"""() => new Promise(resolve => {{
                  const socket = new WebSocket('ws://127.0.0.1:{port}/socket');
                  const timer = setTimeout(() => resolve('timed-out'), 3000);
                  socket.onopen = () => {{
                    socket.close();
                  }};
                  socket.onclose = () => {{ clearTimeout(timer); resolve('opened'); }};
                  socket.onerror = () => {{ clearTimeout(timer); resolve('failed'); }};
                }})"""
            )
            assert websocket_result == "opened", (
                _EngineOrigin.hits[-5:],
                [entry for entry in approved_requests if entry[1].startswith("ws")],
            )
            assert ("GET", "/socket") in _EngineOrigin.hits

            before_rebind = list(_EngineOrigin.hits)
            rebound = page.goto(
                f"http://rebind.test:{port}/rebound",
                wait_until="domcontentloaded",
            )
            assert rebound is not None and rebound.status == 403
            assert resolver_calls >= 2
            assert _EngineOrigin.hits == before_rebind

            assert policy.revoke_grant(grant.id) is True
            with pytest.raises(PlaywrightError):
                page.goto(local_url, wait_until="domcontentloaded")
            page.close()
            context.unroute_all(behavior="wait")
            context.close()
            browser.close()
            context = None
            browser = None
    finally:
        if context is not None:
            try:
                context.close()
            except PlaywrightError:
                pass
        if browser is not None:
            try:
                browser.close()
            except PlaywrightError:
                pass
        proxy.close()
        origin.shutdown()
        origin.server_close()
        origin_thread.join(timeout=5)
