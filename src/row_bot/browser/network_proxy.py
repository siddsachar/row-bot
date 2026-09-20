"""Loopback proxy that pins managed Browser sockets to policy-approved IPs.

Playwright request routing can reject an unsafe URL, but Chromium normally
resolves the hostname again when it opens the socket.  This proxy is the
transport boundary for the managed Browser: it rechecks policy, then connects
to an exact address returned by that check without another hostname lookup.
"""

from __future__ import annotations

import base64
from collections import deque
import hmac
import selectors
import secrets
import socket
import socketserver
import threading
import time
from typing import Callable
from urllib.parse import urlsplit

from row_bot.browser.network_security import BrowserNetworkSecurity, NetworkDecision


_MAX_HEADER_BYTES = 64 * 1024
_SOCKET_TIMEOUT_SECONDS = 30.0
_TICKET_TTL_SECONDS = 10.0
_MAX_TICKETS = 2048
Connector = Callable[[str, int, float], socket.socket]


def _connect_exact(address: str, port: int, timeout: float) -> socket.socket:
    return socket.create_connection((address, port), timeout=timeout)


def _authority_url(authority: str) -> str:
    return f"https://{authority}/"


def _target_port(url: str) -> int:
    parsed = urlsplit(url)
    return parsed.port or (443 if parsed.scheme.casefold() in {"https", "wss"} else 80)


def _origin_form(url: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path or "/"
    return path + (f"?{parsed.query}" if parsed.query else "")


def _ticket_key(url: str, method: str) -> tuple[str, str, str] | None:
    try:
        parsed = urlsplit(str(url))
        scheme = parsed.scheme.casefold()
        host = (parsed.hostname or "").rstrip(".").casefold()
        if scheme not in {"http", "https", "ws", "wss"} or not host:
            return None
        port = parsed.port or (443 if scheme in {"https", "wss"} else 80)
        rendered = f"[{host}]" if ":" in host else host
        if scheme in {"https", "wss"}:
            return ("connect", f"{rendered}:{port}", "GET")
        normalized_scheme = "http"
        default_port = 80
        origin = f"{normalized_scheme}://{rendered}"
        if port != default_port:
            origin += f":{port}"
        return (
            "request",
            origin + _origin_form(url),
            str(method or "GET").upper(),
        )
    except (TypeError, ValueError):
        return None


class _PinnedProxyServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, owner: PinnedNetworkProxy) -> None:
        self.owner = owner
        super().__init__(("127.0.0.1", 0), _PinnedProxyHandler)


class _PinnedProxyHandler(socketserver.BaseRequestHandler):
    server: _PinnedProxyServer

    def handle(self) -> None:
        client = self.request
        client.settimeout(_SOCKET_TIMEOUT_SECONDS)
        try:
            header, remainder = self._read_header(client)
            request_line, headers = self._parse_header(header)
            method, target, version = request_line
            if not self.server.owner.authorized(headers):
                self._reject(client, 407, "Proxy Authentication Required")
                return
            if method == "CONNECT":
                self._connect_tunnel(client, target, remainder)
                return
            self._forward_http(client, method, target, version, headers, remainder)
        except (ConnectionError, OSError, ValueError):
            try:
                self._reject(client, 502, "Bad Gateway")
            except OSError:
                pass

    @staticmethod
    def _read_header(client: socket.socket) -> tuple[bytes, bytes]:
        data = bytearray()
        while b"\r\n\r\n" not in data:
            chunk = client.recv(min(8192, _MAX_HEADER_BYTES + 1 - len(data)))
            if not chunk:
                raise ConnectionError("proxy request ended before headers")
            data.extend(chunk)
            if len(data) > _MAX_HEADER_BYTES:
                raise ValueError("proxy request headers are too large")
        marker = data.index(b"\r\n\r\n") + 4
        return bytes(data[:marker]), bytes(data[marker:])

    @staticmethod
    def _parse_header(
        header: bytes,
    ) -> tuple[tuple[str, str, str], list[tuple[str, str]]]:
        lines = header.decode("iso-8859-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3 or not parts[0] or not parts[1] or not parts[2].startswith("HTTP/1."):
            raise ValueError("invalid proxy request line")
        method = parts[0].upper()
        if not method.isalpha() or len(method) > 16:
            raise ValueError("invalid proxy method")
        headers: list[tuple[str, str]] = []
        for line in lines[1:]:
            if not line:
                continue
            name, separator, value = line.partition(":")
            if not separator or not name or any(character.isspace() for character in name):
                raise ValueError("invalid proxy header")
            headers.append((name, value.strip()))
        return (method, parts[1], parts[2]), headers

    def _connect_tunnel(
        self,
        client: socket.socket,
        authority: str,
        remainder: bytes,
    ) -> None:
        url = _authority_url(authority)
        decision = self.server.owner.authorize(url, method="GET", headers=[])
        if not decision.allowed:
            self._reject(client, 403, "Forbidden")
            return
        upstream = self.server.owner.connect(decision, _target_port(url))
        with upstream:
            upstream.settimeout(_SOCKET_TIMEOUT_SECONDS)
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if remainder:
                upstream.sendall(remainder)
            self._relay(client, upstream)

    def _forward_http(
        self,
        client: socket.socket,
        method: str,
        target: str,
        version: str,
        headers: list[tuple[str, str]],
        remainder: bytes,
    ) -> None:
        parsed = urlsplit(target)
        if parsed.scheme.casefold() not in {"http", "ws"} or not parsed.hostname:
            self._reject(client, 400, "Bad Request")
            return
        decision = self.server.owner.authorize(
            target,
            method=method,
            headers=headers,
        )
        if not decision.allowed:
            self._reject(client, 403, "Forbidden")
            return
        upstream = self.server.owner.connect(decision, _target_port(target))
        with upstream:
            upstream.settimeout(_SOCKET_TIMEOUT_SECONDS)
            upgrade = any(
                name.casefold() == "upgrade" and value.casefold() == "websocket"
                for name, value in headers
            )
            forwarded = [f"{method} {_origin_form(target)} {version}\r\n".encode("ascii")]
            for name, value in headers:
                lowered = name.casefold()
                if lowered in {"proxy-authorization", "proxy-connection", "connection"}:
                    continue
                forwarded.append(f"{name}: {value}\r\n".encode("iso-8859-1"))
            forwarded.append(
                b"Connection: Upgrade\r\n" if upgrade else b"Connection: close\r\n"
            )
            forwarded.append(b"\r\n")
            upstream.sendall(b"".join(forwarded))
            if remainder:
                upstream.sendall(remainder)
            self._relay(client, upstream)

    @staticmethod
    def _relay(left: socket.socket, right: socket.socket) -> None:
        selector = selectors.DefaultSelector()
        try:
            selector.register(left, selectors.EVENT_READ, right)
            selector.register(right, selectors.EVENT_READ, left)
            while selector.get_map():
                ready = selector.select(timeout=_SOCKET_TIMEOUT_SECONDS)
                if not ready:
                    return
                for key, _events in ready:
                    source = key.fileobj
                    destination = key.data
                    data = source.recv(64 * 1024)
                    if not data:
                        return
                    destination.sendall(data)
        finally:
            selector.close()

    @staticmethod
    def _reject(client: socket.socket, status: int, reason: str) -> None:
        body = reason.encode("ascii")
        challenge = (
            b'Proxy-Authenticate: Basic realm="Row-Bot"\r\n'
            if status == 407
            else b""
        )
        client.sendall(
            f"HTTP/1.1 {status} {reason}\r\n".encode("ascii")
            + challenge
            + b"Connection: close\r\nContent-Type: text/plain\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
            + body
        )


class PinnedNetworkProxy:
    """Authenticated per-browser proxy with exact-address connections."""

    def __init__(
        self,
        policy: BrowserNetworkSecurity,
        *,
        browser_session_id: str,
        connector: Connector | None = None,
    ) -> None:
        self._policy = policy
        self._browser_session_id = str(browser_session_id)
        self._connector = connector or _connect_exact
        self._username = "row-bot"
        self._password = secrets.token_urlsafe(32)
        token = base64.b64encode(
            f"{self._username}:{self._password}".encode("utf-8")
        ).decode("ascii")
        self._authorization = f"Basic {token}"
        self._server: _PinnedProxyServer | None = None
        self._thread: threading.Thread | None = None
        self._ticket_lock = threading.Lock()
        self._tickets: dict[
            tuple[str, str, str],
            deque[tuple[str, str, float]],
        ] = {}

    @property
    def playwright_config(self) -> dict[str, str]:
        server = self._server
        if server is None:
            raise RuntimeError("network proxy is not running")
        host, port = server.server_address
        return {
            "server": f"http://{host}:{port}",
            "username": self._username,
            "password": self._password,
        }

    @property
    def authorization_header(self) -> str:
        """Expose only to deterministic proxy tests, never logs or UI."""

        return self._authorization

    def start(self) -> None:
        if self._server is not None:
            return
        server = _PinnedProxyServer(self)
        thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
            name="row-bot-browser-proxy",
        )
        self._server = server
        self._thread = thread
        thread.start()

    def close(self) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=5)

    def authorized(self, headers: list[tuple[str, str]]) -> bool:
        supplied = next(
            (value for name, value in headers if name.casefold() == "proxy-authorization"),
            "",
        )
        return hmac.compare_digest(supplied, self._authorization)

    def authorize(
        self,
        url: str,
        *,
        method: str,
        headers: list[tuple[str, str]],
    ) -> NetworkDecision:
        approved = self._consume_ticket(url, method)
        if approved is None and not self._user_download(method, headers):
            return NetworkDecision(
                False,
                "transport_not_approved",
                "The managed engine did not approve this exact transport request.",
            )
        approved_url, approved_method = approved or (url, method)
        return self._policy.authorize(
            approved_url,
            browser_session_id=self._browser_session_id,
            method=approved_method,
        )

    @staticmethod
    def _user_download(method: str, headers: list[tuple[str, str]]) -> bool:
        """Recognize Chromium's user-activated download request.

        Playwright deliberately does not route requests that become downloads,
        so those requests cannot carry a route ticket. The browser still marks
        a real activated download distinctly; all three Fetch Metadata values
        are required and policy is rechecked before an exact-address socket.
        """

        values = {name.casefold(): value.casefold() for name, value in headers}
        return (
            str(method).upper() == "GET"
            and values.get("sec-fetch-mode") == "navigate"
            and values.get("sec-fetch-dest") == "empty"
            and values.get("sec-fetch-site") == "same-origin"
        )

    def approve_request(self, url: str, method: str) -> None:
        """Issue one short-lived transport ticket from the engine route hook."""

        key = _ticket_key(url, method)
        if key is None:
            return
        keys = [key]
        parsed = urlsplit(str(url))
        if parsed.scheme.casefold() == "ws" and parsed.hostname:
            port = parsed.port or 80
            rendered = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
            keys.append(("connect", f"{rendered.casefold()}:{port}", "GET"))
        now = time.monotonic()
        with self._ticket_lock:
            self._expire_tickets(now)
            if sum(len(queue) for queue in self._tickets.values()) + len(keys) > _MAX_TICKETS:
                return
            for selected in keys:
                self._tickets.setdefault(selected, deque()).append(
                    (str(url), str(method or "GET").upper(), now + _TICKET_TTL_SECONDS)
                )

    def _consume_ticket(self, url: str, method: str) -> tuple[str, str] | None:
        key = _ticket_key(url, method)
        if key is None:
            return None
        now = time.monotonic()
        with self._ticket_lock:
            self._expire_tickets(now)
            queue = self._tickets.get(key)
            if not queue:
                return None
            approved_url, approved_method, _expires_at = queue.popleft()
            if not queue:
                self._tickets.pop(key, None)
            return approved_url, approved_method

    def _expire_tickets(self, now: float) -> None:
        for key, queue in tuple(self._tickets.items()):
            while queue and queue[0][2] <= now:
                queue.popleft()
            if not queue:
                self._tickets.pop(key, None)

    def connect(self, decision: NetworkDecision, port: int) -> socket.socket:
        if not decision.allowed or not decision.destinations:
            raise PermissionError("network destination is not authorized")
        last_error: OSError | None = None
        for address in sorted(decision.destinations):
            try:
                return self._connector(address, port, _SOCKET_TIMEOUT_SECONDS)
            except OSError as exc:
                last_error = exc
        raise ConnectionError("no authorized destination accepted the connection") from last_error

    def __enter__(self) -> PinnedNetworkProxy:
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
