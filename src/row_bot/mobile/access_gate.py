"""Pure ASGI remote access gate for Row-Bot mobile companion sessions."""

from __future__ import annotations

from ipaddress import ip_address
import json
from typing import Awaitable, Callable
from urllib.parse import quote

from row_bot.mobile.auth import validate_device_token
from row_bot.mobile.cookies import extract_cookie_from_header
from row_bot.mobile.store import MobileAuthStore

ASGIApp = Callable[[dict, Callable, Callable], Awaitable[None]]

FORWARDED_HEADER_NAMES = {
    b"forwarded",
    b"x-forwarded",
    b"x-forwarded-for",
    b"x-forwarded-host",
    b"x-forwarded-port",
    b"x-forwarded-proto",
    b"x-forwarded-server",
    b"x-original-forwarded-for",
    b"x-real-ip",
    b"x-client-ip",
    b"x-cluster-client-ip",
    b"cf-connecting-ip",
    b"true-client-ip",
    b"fly-client-ip",
}

UNAUTHENTICATED_HTTP_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/mobile/pair"),
        ("GET", "/mobile/manifest.webmanifest"),
        ("GET", "/mobile/offline"),
        ("GET", "/mobile/service-worker.js"),
        ("GET", "/static/row_bot_glyph_256.png"),
        ("POST", "/api/mobile/pair/start"),
        ("POST", "/api/mobile/pair/confirm"),
        ("GET", "/api/mobile/session"),
        ("GET", "/api/launcher-ping"),
        ("GET", "/api/startup-state"),
    }
)


def _headers(scope: dict) -> dict[bytes, bytes]:
    return {bytes(name).lower(): bytes(value) for name, value in scope.get("headers", [])}


def has_forwarded_headers(scope: dict) -> bool:
    headers = _headers(scope)
    return any(name in headers for name in FORWARDED_HEADER_NAMES)


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def is_true_local_scope(scope: dict) -> bool:
    """Return True for direct loopback traffic without forwarding headers."""
    client = scope.get("client") or ("", 0)
    client_host = str(client[0] or "")
    return _is_loopback_host(client_host) and not has_forwarded_headers(scope)


def _scope_scheme(scope: dict) -> str:
    headers = _headers(scope)
    forwarded_proto = headers.get(b"x-forwarded-proto", b"").decode("latin-1", errors="ignore").split(",", 1)[0].strip()
    if forwarded_proto:
        return forwarded_proto
    scheme = str(scope.get("scheme") or "http")
    if scheme == "wss":
        return "https"
    if scheme == "ws":
        return "http"
    return scheme


def _path(scope: dict) -> str:
    return str(scope.get("path") or "/") or "/"


def _method(scope: dict) -> str:
    return str(scope.get("method") or "GET").upper()


def is_unauthenticated_route_allowed(scope: dict) -> bool:
    return (_method(scope), _path(scope)) in UNAUTHENTICATED_HTTP_ROUTES


def _cookie_header(scope: dict) -> str:
    return _headers(scope).get(b"cookie", b"").decode("latin-1", errors="ignore")


def authenticated_mobile_device(scope: dict, store: MobileAuthStore):
    token = extract_cookie_from_header(_cookie_header(scope), scheme=_scope_scheme(scope))
    if not token:
        return None
    return validate_device_token(store, token)


async def _send_response(
    send: Callable,
    status: int,
    body: bytes,
    *,
    content_type: str = "application/json",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    response_headers = [
        (b"content-type", content_type.encode("latin-1")),
        (b"cache-control", b"no-store"),
    ]
    response_headers.extend(headers or [])
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": response_headers,
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _redirect_to_pair(scope: dict, send: Callable) -> None:
    query = scope.get("query_string") or b""
    target = _path(scope)
    if query:
        target = f"{target}?{query.decode('latin-1', errors='ignore')}"
    location = f"/mobile/pair?next={quote(target, safe='')}"
    await _send_response(
        send,
        303,
        b"",
        content_type="text/plain; charset=utf-8",
        headers=[(b"location", location.encode("latin-1"))],
    )


async def _unauthorized_http(scope: dict, send: Callable) -> None:
    path = _path(scope)
    method = _method(scope)
    accept = _headers(scope).get(b"accept", b"").decode("latin-1", errors="ignore").lower()
    if method == "GET" and path == "/" and ("text/html" in accept or "*/*" in accept or not accept):
        await _redirect_to_pair(scope, send)
        return
    body = json.dumps({"ok": False, "error": "mobile_auth_required"}).encode("utf-8")
    await _send_response(send, 401, body)


class MobileAccessGate:
    """Gate non-local HTTP and WebSocket traffic behind mobile session auth."""

    def __init__(self, app: ASGIApp, *, store: MobileAuthStore | None = None) -> None:
        self.app = app
        self.store = store or MobileAuthStore()

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        scope_type = scope.get("type")
        if scope_type not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        if is_true_local_scope(scope):
            await self.app(scope, receive, send)
            return
        if scope_type == "http" and is_unauthenticated_route_allowed(scope):
            await self.app(scope, receive, send)
            return
        if authenticated_mobile_device(scope, self.store) is not None:
            await self.app(scope, receive, send)
            return
        if scope_type == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await _unauthorized_http(scope, send)
