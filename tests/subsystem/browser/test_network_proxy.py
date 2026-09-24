from __future__ import annotations

from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading

from row_bot.browser.network_proxy import PinnedNetworkProxy
from row_bot.browser.network_security import BrowserNetworkSecurity


class _OriginHandler(BaseHTTPRequestHandler):
    hits: list[tuple[str, str]] = []

    def do_GET(self) -> None:
        self.hits.append(("GET", self.path))
        body = b"pinned origin"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        self.hits.append(("POST", self.path))
        self.send_response(204)
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _request(
    proxy: PinnedNetworkProxy,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    config = proxy.playwright_config
    host, port_text = config["server"].removeprefix("http://").split(":")
    connection = HTTPConnection(host, int(port_text), timeout=5)
    try:
        request_headers = {
            "Proxy-Authorization": proxy.authorization_header,
            **(headers or {}),
        }
        connection.request(method, url, headers=request_headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_proxy_denies_private_origin_until_exact_grant_and_never_forwards_writes() -> None:
    _OriginHandler.hits = []
    origin = ThreadingHTTPServer(("127.0.0.1", 0), _OriginHandler)
    thread = threading.Thread(target=origin.serve_forever, daemon=True)
    thread.start()
    port = origin.server_address[1]
    policy = BrowserNetworkSecurity()
    try:
        with PinnedNetworkProxy(policy, browser_session_id="browser-a") as proxy:
            url = f"http://127.0.0.1:{port}/fixture"
            assert _request(proxy, "GET", url)[0] == 403
            assert _OriginHandler.hits == []

            policy.grant_local_development(
                browser_session_id="browser-a",
                origin=f"http://127.0.0.1:{port}",
                intended_use="Exercise the contained proxy origin",
                ttl_seconds=60,
            )
            proxy.approve_request(url, "GET")
            assert _request(proxy, "GET", url) == (200, b"pinned origin")
            proxy.approve_request(url, "POST")
            assert _request(proxy, "POST", url)[0] == 403
            download_url = f"http://127.0.0.1:{port}/download"
            assert _request(
                proxy,
                "GET",
                download_url,
                headers={
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Site": "same-origin",
                },
            )[0] == 200
            assert _OriginHandler.hits == [
                ("GET", "/fixture"),
                ("GET", "/download"),
            ]
    finally:
        origin.shutdown()
        origin.server_close()
        thread.join(timeout=5)


def test_proxy_connects_to_resolved_ip_without_a_second_hostname_lookup() -> None:
    connected: list[tuple[str, int]] = []

    def connector(address: str, port: int, _timeout: float) -> socket.socket:
        connected.append((address, port))
        client, server = socket.socketpair()

        def respond() -> None:
            with server:
                server.recv(8192)
                server.sendall(
                    b"HTTP/1.1 200 OK\r\nConnection: close\r\n"
                    b"Content-Length: 2\r\n\r\nok"
                )

        threading.Thread(target=respond, daemon=True).start()
        return client

    policy = BrowserNetworkSecurity(
        resolver=lambda host, port: ("8.8.8.8",)
        if (host, port) == ("public.test", 80)
        else (),
    )
    with PinnedNetworkProxy(
        policy,
        browser_session_id="browser-a",
        connector=connector,
    ) as proxy:
        proxy.approve_request("http://public.test/exact", "GET")
        assert _request(proxy, "GET", "http://public.test/exact") == (200, b"ok")

    assert connected == [("8.8.8.8", 80)]
