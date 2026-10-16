"""Public web names served from a loopback server, for fetchers that only open public addresses.

A fake DNS maps each name to a public address. The fetcher checks that address and pins its connection to
it, and the fake carries that connection to the loopback server instead, so nothing leaves this computer.
"""
from __future__ import annotations

import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

PUBLIC_ADDRESS = "93.184.215.14"


class PublicWeb:
    def __init__(self) -> None:
        # "host/path" -> (status, headers, body)
        self.routes: dict[str, tuple[int, dict[str, str], bytes]] = {}
        # name -> the addresses a lookup returns, or a function giving each lookup's answer
        self.dns: dict[str, list[str] | Callable[[], list[str]]] = {}
        self.resolved: list[str] = []
        # (address connected to, Host header sent)
        self.connected: list[tuple[str, str]] = []
        self.served: list[str] = []
        self.loopback_port = 0

    def page(self, url: str, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
        self.routes[url.split("://", 1)[1]] = (200, {"Content-Type": content_type}, body)

    def file(self, url: str, body: bytes) -> None:
        self.page(url, body, "application/octet-stream")

    def redirect(self, url: str, location: str, status: int = 302) -> None:
        self.routes[url.split("://", 1)[1]] = (status, {"Location": location}, b"")


@contextmanager
def public_web(monkeypatch: pytest.MonkeyPatch) -> Iterator[PublicWeb]:
    web = PublicWeb()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass

        def do_GET(self) -> None:
            key = f"{self.headers['Host']}{self.path}"
            web.served.append(key)
            status, headers, body = web.routes.get(key, (404, {}, b"not found"))
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    web.loopback_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()

    resolve = socket.getaddrinfo
    send = httpx.HTTPTransport.handle_request

    def fake_dns(host, port, *args, **kwargs):
        if host not in web.dns:
            return resolve(host, port, *args, **kwargs)
        web.resolved.append(host)
        answer = web.dns[host]
        return [(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
                for address in (answer() if callable(answer) else answer)]

    def carry_to_loopback(transport, request: httpx.Request) -> httpx.Response:
        web.connected.append((request.url.host, request.headers["host"]))
        # The loopback server speaks plain HTTP: an https address (an https-only fetcher) is carried to it too.
        loopback = request.url.copy_with(scheme="http", host="127.0.0.1", port=web.loopback_port)
        return send(transport, httpx.Request(request.method, loopback, headers=request.headers,
                                             stream=request.stream, extensions=request.extensions))

    from row_bot.integrations import safe

    monkeypatch.setattr(safe, "_UNREACHABLE", {})
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", carry_to_loopback)
    try:
        yield web
    finally:
        server.shutdown()
        server.server_close()
